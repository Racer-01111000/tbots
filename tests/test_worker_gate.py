import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_gate import GateError, check_audit_gate, parse_utc, window_elapsed

BRANCH = "main"


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo_pair(tmp: Path):
    """Bare 'origin' + a real clone at tmp/work, both on branch `main`,
    with one shared initial commit."""
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


class ParseAndElapsed(unittest.TestCase):
    def test_parse_utc_round_trips(self):
        dt = parse_utc("2026-09-26T12:29:05Z")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_window_elapsed_true_when_now_after_end(self):
        self.assertTrue(window_elapsed(
            datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc), "2026-09-26T12:29:05Z",
        ))

    def test_window_elapsed_false_when_now_before_end(self):
        self.assertFalse(window_elapsed(
            datetime(2026, 9, 26, 6, 0, 0, tzinfo=timezone.utc), "2026-09-26T12:29:05Z",
        ))

    def test_window_elapsed_true_at_exact_boundary(self):
        self.assertTrue(window_elapsed(
            datetime(2026, 9, 26, 12, 29, 5, tzinfo=timezone.utc), "2026-09-26T12:29:05Z",
        ))


class WindowStillOpen(unittest.TestCase):
    def test_returns_idle_without_touching_git_at_all(self):
        result = check_audit_gate(
            "/path/does/not/exist", BRANCH, "2026-09-26T12:29:05Z",
            now_utc=datetime(2026, 9, 26, 6, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["outcome"], "idle")
        self.assertIsNone(result["stop_code"])


class WindowElapsedCleanState(unittest.TestCase):
    def test_ready_when_local_already_matches_remote_and_no_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            result = check_audit_gate(
                work, BRANCH, "2026-09-26T12:29:05Z",
                now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["outcome"], "ready")
            self.assertIsNone(result["stop_code"])


class WindowElapsedFastForward(unittest.TestCase):
    def test_fast_forwards_local_head_when_strictly_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work = _init_repo_pair(tmp)
            other = tmp / "other_clone"
            _git(tmp, "clone", str(origin), str(other))
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            (other / "advance.txt").write_text("advance\n")
            _git(other, "add", "advance.txt")
            _git(other, "commit", "-m", "advance")
            _git(other, "push", "origin", BRANCH)
            remote_head = _git(other, "rev-parse", BRANCH)

            result = check_audit_gate(
                work, BRANCH, "2026-09-26T12:29:05Z",
                now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["outcome"], "ready")
            self.assertEqual(_git(work, "rev-parse", "HEAD"), remote_head)


class WindowElapsedNonFastForward(unittest.TestCase):
    def test_stops_with_code_c_on_divergent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work = _init_repo_pair(tmp)

            (work / "local_only.txt").write_text("local\n")
            _git(work, "add", "local_only.txt")
            _git(work, "commit", "-m", "local divergent commit")

            other = tmp / "other_clone"
            _git(tmp, "clone", str(origin), str(other))
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            (other / "remote_only.txt").write_text("remote\n")
            _git(other, "add", "remote_only.txt")
            _git(other, "commit", "-m", "remote divergent commit")
            _git(other, "push", "origin", BRANCH)

            result = check_audit_gate(
                work, BRANCH, "2026-09-26T12:29:05Z",
                now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["outcome"], "stopped")
            self.assertEqual(result["stop_code"], "C")


class WindowElapsedLocalAheadUnpushed(unittest.TestCase):
    def test_stops_with_code_c_but_a_distinct_unpushed_commit_message(self):
        """Remote has not moved at all -- local is simply ahead with a
        commit that never reached origin (e.g. a crash between git commit
        and git push). This must NOT be reported as a rewritten/diverged
        branch: the message is the whole point of this test, not just the
        stop_code (which is intentionally unchanged -- see worker_gate.py's
        own comment)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)

            (work / "unpushed.txt").write_text("local, never pushed\n")
            _git(work, "add", "unpushed.txt")
            _git(work, "commit", "-m", "committed locally, crashed before push")

            result = check_audit_gate(
                work, BRANCH, "2026-09-26T12:29:05Z",
                now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["outcome"], "stopped")
            self.assertEqual(result["stop_code"], "C")
            self.assertIn("unpushed commit", result["detail"])
            self.assertIn("remote has not diverged", result["detail"])
            self.assertIn("Not a rewritten branch", result["detail"])
            self.assertNotIn("non-fast-forward / rewritten branch", result["detail"])


class WindowElapsedHoldFile(unittest.TestCase):
    def test_stops_with_code_h_when_hold_file_present_on_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work = _init_repo_pair(tmp)
            other = tmp / "other_clone"
            _git(tmp, "clone", str(origin), str(other))
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            (other / "FREEZE_AUDIT_HOLD.md").write_text("hold\n")
            _git(other, "add", "FREEZE_AUDIT_HOLD.md")
            _git(other, "commit", "-m", "hold")
            _git(other, "push", "origin", BRANCH)

            result = check_audit_gate(
                work, BRANCH, "2026-09-26T12:29:05Z",
                now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["outcome"], "stopped")
            self.assertEqual(result["stop_code"], "H")
            # the fast-forward to pick up the hold file itself is still permitted
            self.assertEqual(
                _git(work, "rev-parse", "HEAD"), _git(work, "rev-parse", f"origin/{BRANCH}"),
            )


class GitErrorPropagation(unittest.TestCase):
    def test_raises_gate_error_for_a_bad_repo_once_window_has_elapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GateError):
                check_audit_gate(
                    tmp, BRANCH, "2026-09-26T12:29:05Z",
                    now_utc=datetime(2026, 9, 26, 13, 0, 0, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
