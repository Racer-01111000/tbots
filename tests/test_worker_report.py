import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_report import (
    ReportError,
    classify,
    collect_git_info,
    compose_body,
    compose_subject,
    decide_report,
    default_report_state,
    read_report_state,
    run_once,
    send_report,
    write_report_state,
)

BRANCH = "main"


def _status(**overrides):
    base = dict(
        schema="tbots-status-v1", updated_utc="2026-09-26T07:00:00Z",
        head_commit_parent="a" * 40, state="audit_window", phase="freeze_audit_window",
        protocol_manifest="fitness_v2_complete_protocol_x", freeze_commit="b" * 40,
        audit_window_ends_utc="2026-09-26T12:29:05Z", world_bank_id=None,
        synthetic_world_count=0, campaign=None, campaign_seed=None, generation=None,
        campaigns_complete=0, rank1_qualifiers=0, research_champion=None,
        negative_result=False, last_progress_utc="2026-09-26T06:55:00Z",
        stop_code=None, stop_reason="", recoveries=0, last_generation_seconds=None,
        disk_used_bytes=1000, disk_free_bytes=200 * 1024 ** 3,
    )
    base.update(overrides)
    return base


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo_pair(tmp: Path):
    origin = tmp / "origin.git"
    work = tmp / "work"
    _git(tmp, "init", "--bare", "-b", BRANCH, str(origin))
    _git(tmp, "clone", str(origin), str(work))
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "seed.txt").write_text("seed\n")
    _git(work, "add", "seed.txt")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "origin", BRANCH)
    return origin, work


class Classify(unittest.TestCase):
    def test_stopped_is_red(self):
        self.assertEqual(classify(_status(state="stopped", stop_code="H")), "red")

    def test_terminal_hold_is_done(self):
        self.assertEqual(classify(_status(state="terminal_hold")), "done")

    def test_anything_else_is_green(self):
        for state in ("audit_window", "idle", "world_bank", "campaign", "admission"):
            self.assertEqual(classify(_status(state=state)), "green")


class DecideReportGreen(unittest.TestCase):
    def test_always_sends_and_resets_counters(self):
        prior = {**default_report_state(), "last_state": "stopped", "last_stop_code": "H",
                 "red_report_count": 3}
        decision = decide_report(_status(state="campaign"), prior)
        self.assertTrue(decision["send"])
        self.assertEqual(decision["category"], "green")
        self.assertIsNone(decision["report_number"])
        self.assertEqual(decision["next_state"]["red_report_count"], 0)
        self.assertEqual(decision["next_state"]["last_stop_code"], None)


class DecideReportRed(unittest.TestCase):
    def test_new_stop_is_report_one(self):
        decision = decide_report(_status(state="stopped", stop_code="C"), default_report_state())
        self.assertEqual(decision["report_number"], 1)
        self.assertTrue(decision["send"])

    def test_repeat_stop_increments(self):
        prior = {**default_report_state(), "last_state": "stopped", "last_stop_code": "C",
                 "red_report_count": 1}
        decision = decide_report(_status(state="stopped", stop_code="C"), prior)
        self.assertEqual(decision["report_number"], 2)

    def test_third_repeat_increments_again(self):
        prior = {**default_report_state(), "last_state": "stopped", "last_stop_code": "C",
                 "red_report_count": 2}
        decision = decide_report(_status(state="stopped", stop_code="C"), prior)
        self.assertEqual(decision["report_number"], 3)

    def test_a_different_stop_code_while_stopped_is_a_new_report_one(self):
        prior = {**default_report_state(), "last_state": "stopped", "last_stop_code": "C",
                 "red_report_count": 5}
        decision = decide_report(_status(state="stopped", stop_code="H"), prior)
        self.assertEqual(decision["report_number"], 1)

    def test_next_state_clears_done_counter(self):
        prior = {**default_report_state(), "done_report_count": 2}
        decision = decide_report(_status(state="stopped", stop_code="A"), prior)
        self.assertEqual(decision["next_state"]["done_report_count"], 0)


class DecideReportDone(unittest.TestCase):
    def test_first_tick_is_report_one_and_sends(self):
        decision = decide_report(_status(state="terminal_hold"), default_report_state())
        self.assertEqual(decision["report_number"], 1)
        self.assertTrue(decision["send"])

    def test_second_tick_is_report_two_and_still_sends(self):
        prior = {**default_report_state(), "last_state": "terminal_hold", "done_report_count": 1}
        decision = decide_report(_status(state="terminal_hold"), prior)
        self.assertEqual(decision["report_number"], 2)
        self.assertTrue(decision["send"])

    def test_third_tick_does_not_send(self):
        prior = {**default_report_state(), "last_state": "terminal_hold", "done_report_count": 2}
        decision = decide_report(_status(state="terminal_hold"), prior)
        self.assertEqual(decision["report_number"], 3)
        self.assertFalse(decision["send"])

    def test_further_ticks_keep_not_sending(self):
        prior = {**default_report_state(), "last_state": "terminal_hold", "done_report_count": 2}
        for _ in range(5):
            decision = decide_report(_status(state="terminal_hold"), prior)
            self.assertFalse(decision["send"])
            prior = decision["next_state"]


class ComposeSubjectGreen(unittest.TestCase):
    def test_exact_format_with_all_fields_present(self):
        status = _status(
            state="campaign", phase="campaign_running", campaign=2, generation=5,
            synthetic_world_count=19, campaigns_complete=1,
        )
        subject = compose_subject(status, "green", None)
        self.assertEqual(
            subject,
            "[TBOTS GREEN] campaign_running C2/G5 — 19 worlds, 1/5 done",
        )

    def test_dashes_when_campaign_and_generation_are_none(self):
        subject = compose_subject(_status(state="audit_window"), "green", None)
        self.assertIn("C-/G-", subject)


class ComposeSubjectRed(unittest.TestCase):
    def test_report_one_has_no_suffix(self):
        status = _status(state="stopped", stop_code="H", stop_reason="hold file present")
        subject = compose_subject(status, "red", 1)
        self.assertEqual(subject, "[TBOTS RED] STOPPED H — hold file present")

    def test_report_two_has_suffix(self):
        status = _status(state="stopped", stop_code="H", stop_reason="hold file present")
        subject = compose_subject(status, "red", 2)
        self.assertTrue(subject.endswith("(report 2)"))

    def test_long_reason_is_truncated(self):
        long_reason = "x" * 200
        status = _status(state="stopped", stop_code="C", stop_reason=long_reason)
        subject = compose_subject(status, "red", 1)
        self.assertLess(len(subject), 120)
        self.assertIn("…", subject)


class ComposeSubjectDone(unittest.TestCase):
    def test_with_a_champion(self):
        status = _status(state="terminal_hold", research_champion="gen_abc123")
        subject = compose_subject(status, "done", 1)
        self.assertEqual(subject, "[TBOTS DONE] gen_abc123 — awaiting new GO")

    def test_without_a_champion_says_negative_result(self):
        status = _status(state="terminal_hold", research_champion=None, negative_result=True)
        subject = compose_subject(status, "done", 1)
        self.assertEqual(subject, "[TBOTS DONE] negative result — awaiting new GO")


class ComposeBody(unittest.TestCase):
    def _git_info(self):
        return {"local_head": "abc123", "remote_head": "abc123", "local_equals_remote": True}

    def test_red_state_puts_stop_code_and_reason_on_first_two_lines(self):
        status = _status(state="stopped", stop_code="R", stop_reason="disk low")
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        lines = body.splitlines()
        self.assertEqual(lines[0], "stop_code: R")
        self.assertEqual(lines[1], "stop_reason: disk low")

    def test_green_state_has_no_stop_lines(self):
        body = compose_body(_status(state="campaign"), self._git_info(),
                             datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertNotIn("stop_code:", body)
        self.assertNotIn("stop_reason:", body)

    def test_includes_saigon_local_time_seven_hours_ahead(self):
        status = _status(updated_utc="2026-09-26T07:00:00Z")
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("2026-09-26 14:00:00", body)

    def test_includes_disk_free_in_gb(self):
        status = _status(disk_free_bytes=21474836480)  # exactly 20 GiB
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("disk_free_gb: 20.0", body)

    def test_includes_local_equals_remote_and_head(self):
        body = compose_body(_status(), self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("local_head: abc123", body)
        self.assertIn("local_equals_remote: True", body)

    def test_ago_formatting_minutes_only(self):
        status = _status(last_progress_utc="2026-09-26T06:50:00Z")
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("(10m ago)", body)

    def test_ago_formatting_hours_and_minutes(self):
        status = _status(last_progress_utc="2026-09-26T04:30:00Z")
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("(2h30m ago)", body)

    def test_ago_formatting_handles_missing_timestamp(self):
        status = _status(last_progress_utc=None)
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("(unknown)", body)

    def test_ago_formatting_flags_future_timestamp(self):
        status = _status(last_progress_utc="2026-09-26T08:00:00Z")
        body = compose_body(status, self._git_info(), datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc))
        self.assertIn("clock skew", body)


class ReportStateIO(unittest.TestCase):
    def test_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = read_report_state(Path(tmp) / "missing.json")
            self.assertEqual(state, default_report_state())

    def test_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = {**default_report_state(), "red_report_count": 3}
            write_report_state(path, state)
            self.assertEqual(read_report_state(path), state)

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"schema": "wrong-v9"}))
            with self.assertRaises(ReportError):
                read_report_state(path)


class SendReport(unittest.TestCase):
    def test_message_has_correct_headers_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "captured.eml"
            capture_script = (
                "import sys, pathlib; "
                f"pathlib.Path({str(out)!r}).write_bytes(sys.stdin.buffer.read())"
            )
            send_report(
                subject="[TEST] hello", body="line one\nline two\n",
                to_addr="to@example.com", from_addr="from@example.com",
                sendmail_cmd=[sys.executable, "-c", capture_script],
            )
            message = out.read_text()
            self.assertIn("To: to@example.com", message)
            self.assertIn("From: from@example.com", message)
            self.assertIn("Subject: [TEST] hello", message)
            self.assertTrue(message.endswith("line one\nline two\n"))

    def test_raises_report_error_on_nonzero_exit(self):
        fail_script = "import sys; sys.stderr.write('boom'); sys.exit(1)"
        with self.assertRaises(ReportError) as ctx:
            send_report(
                subject="x", body="y",
                sendmail_cmd=[sys.executable, "-c", fail_script],
            )
        self.assertIn("boom", str(ctx.exception))


class CollectGitInfo(unittest.TestCase):
    def test_reports_local_equals_remote_when_in_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            info = collect_git_info(work, BRANCH)
            self.assertTrue(info["local_equals_remote"])
            self.assertEqual(info["local_head"], info["remote_head"])

    def test_reports_false_when_local_has_an_unpushed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            (work / "extra.txt").write_text("x\n")
            _git(work, "add", "extra.txt")
            _git(work, "commit", "-m", "unpushed")
            info = collect_git_info(work, BRANCH)
            self.assertFalse(info["local_equals_remote"])

    def test_degrades_gracefully_when_remote_is_unreachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            _git(work, "remote", "set-url", "origin", str(tmp / "does-not-exist.git"))
            info = collect_git_info(work, BRANCH)
            self.assertIsNone(info["local_equals_remote"])
            self.assertIsNotNone(info["local_head"])


class RunOnceIntegration(unittest.TestCase):
    def test_green_run_sends_and_persists_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            status_path = tmp / "STATUS.json"
            status_path.write_text(json.dumps(_status(state="campaign", campaign=1, generation=3)))
            state_path = tmp / "state.json"
            captured = tmp / "captured.eml"
            capture_script = (
                "import sys, pathlib; "
                f"pathlib.Path({str(captured)!r}).write_bytes(sys.stdin.buffer.read())"
            )

            result = run_once(
                status_path=status_path, state_path=state_path, branch=BRANCH,
                repo_root=work, sendmail_cmd=[sys.executable, "-c", capture_script],
            )

            self.assertTrue(result["sent"])
            self.assertEqual(result["category"], "green")
            self.assertTrue(captured.exists())
            saved_state = read_report_state(state_path)
            self.assertEqual(saved_state["last_state"], "campaign")
            self.assertIsNotNone(saved_state["last_sent_utc"])

    def test_done_exhaustion_does_not_invoke_mail_transport_a_third_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            status_path = tmp / "STATUS.json"
            status_path.write_text(json.dumps(_status(state="terminal_hold")))
            state_path = tmp / "state.json"
            write_report_state(state_path, {
                **default_report_state(), "last_state": "terminal_hold", "done_report_count": 2,
            })
            # a sendmail_cmd that would fail loudly if ever invoked
            never_run = [sys.executable, "-c", "import sys; sys.exit(1)"]

            result = run_once(
                status_path=status_path, state_path=state_path, branch=BRANCH,
                repo_root=work, sendmail_cmd=never_run,
            )
            self.assertFalse(result["sent"])

    def test_a_failed_send_does_not_advance_the_report_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            status_path = tmp / "STATUS.json"
            status_path.write_text(json.dumps(_status(state="stopped", stop_code="C")))
            state_path = tmp / "state.json"
            failing = [sys.executable, "-c", "import sys; sys.exit(1)"]

            with self.assertRaises(ReportError):
                run_once(
                    status_path=status_path, state_path=state_path, branch=BRANCH,
                    repo_root=work, sendmail_cmd=failing,
                )
            # state file was never written -- next attempt still sees report 1
            self.assertEqual(read_report_state(state_path), default_report_state())


if __name__ == "__main__":
    unittest.main()
