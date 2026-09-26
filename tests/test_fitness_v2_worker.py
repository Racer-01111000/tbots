from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import fitness_v2_worker as worker
from worker_checkpoint import ArtifactCollision, prepare_transaction, recover_pending_transaction
from worker_lock import worker_lock
from worker_status import build_status

BRANCH = "main"


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


def _base_status(**overrides) -> dict:
    fields = {
        "schema": "tbots-status-v1", "updated_utc": "2026-01-01T00:00:00Z",
        "head_commit_parent": "0" * 40, "state": "audit_window", "phase": "freeze_audit_window",
        "protocol_manifest": "x", "freeze_commit": "0" * 40,
        "audit_window_ends_utc": "2099-01-01T00:00:00Z",  # far future: gate stays "idle"
        "world_bank_id": None, "synthetic_world_count": 0, "campaign": None,
        "campaign_seed": None, "generation": None, "campaigns_complete": 0,
        "rank1_qualifiers": 0, "research_champion": None, "negative_result": False,
        "last_progress_utc": "2026-01-01T00:00:00Z", "stop_code": None, "stop_reason": None,
        "recoveries": 0, "last_generation_seconds": None,
        "disk_used_bytes": 0, "disk_free_bytes": 999_999_999_999,
    }
    fields.update(overrides)
    return build_status(**fields)


class WorkerHarness(unittest.TestCase):
    """Points every fitness_v2_worker module-level path constant at a
    scratch git repo pair for the duration of one test, then restores them --
    never touches /opt/evolutionary-markets or real DEVELOPMENT data."""

    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_ctx.name)
        self.origin, self.work = _init_repo_pair(self.tmp)
        self._originals = {
            name: getattr(worker, name)
            for name in ("REPO_ROOT", "STATUS_PATH", "CHECKPOINT_DIR", "LOCK_PATH", "BRANCH", "disk_gate")
        }
        worker.REPO_ROOT = self.work
        worker.STATUS_PATH = self.work / "STATUS.json"
        worker.CHECKPOINT_DIR = self.work / "evolution" / "state"
        worker.LOCK_PATH = self.work / ".tbots.lock"
        worker.BRANCH = BRANCH
        # /tmp on this machine can have less than the real 10GB floor free,
        # which is a fact about the test sandbox's filesystem, not about the
        # gating logic these tests actually check -- stub disk_gate "ok" by
        # default; DiskGateTakesPrecedence overrides it back to exercise the
        # real stop path deliberately.
        worker.disk_gate = lambda path: {
            "outcome": "ok", "stop_code": None, "detail": None,
            "disk_used_bytes": 0, "disk_free_bytes": 999_999_999_999,
        }

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(worker, name, value)
        self._tmp_ctx.cleanup()

    def _write_status(self, status: dict) -> None:
        worker.STATUS_PATH.write_text(json.dumps(status, indent=2) + "\n")
        _git(self.work, "add", "STATUS.json")
        _git(self.work, "commit", "-m", "status")
        _git(self.work, "push", "origin", BRANCH)


class DiskGateTakesPrecedence(WorkerHarness):
    def test_disk_stop_raised_before_any_audit_gate_check(self):
        self._write_status(_base_status())
        original_disk_gate = worker.disk_gate
        worker.disk_gate = lambda path: {
            "outcome": "stopped", "stop_code": "R", "detail": "disk full",
            "disk_used_bytes": 0, "disk_free_bytes": 0,
        }
        try:
            with self.assertRaises(worker.WorkerStop) as ctx:
                worker.run_one_step()
            self.assertEqual(ctx.exception.stop_code, "R")
        finally:
            worker.disk_gate = original_disk_gate


class AuditGateDispatch(WorkerHarness):
    def test_idle_window_does_no_work_and_touches_no_git_history(self):
        self._write_status(_base_status())  # audit_window_ends_utc far in the future
        before = _git(self.work, "rev-parse", "HEAD")
        result = worker.run_one_step()
        self.assertEqual(result["action"], "waiting_on_audit_window")
        self.assertEqual(_git(self.work, "rev-parse", "HEAD"), before)

    def test_hold_file_on_remote_raises_stop_h(self):
        # Audit window already elapsed, but a FREEZE_AUDIT_HOLD.md sits on
        # the remote branch -- must stop with code H, never proceed.
        (self.work / "FREEZE_AUDIT_HOLD.md").write_text("hold\n")
        _git(self.work, "add", "FREEZE_AUDIT_HOLD.md")
        _git(self.work, "commit", "-m", "hold")
        _git(self.work, "push", "origin", BRANCH)
        self._write_status(_base_status(audit_window_ends_utc="2020-01-01T00:00:00Z"))
        with self.assertRaises(worker.WorkerStop) as ctx:
            worker.run_one_step()
        self.assertEqual(ctx.exception.stop_code, "H")

    def test_ready_gate_dispatches_to_world_bank_generation_when_none_exists(self):
        self._write_status(_base_status(audit_window_ends_utc="2020-01-01T00:00:00Z"))
        calls = []
        original = worker._generate_world_bank
        worker._generate_world_bank = lambda status: calls.append("world_bank") or {"action": "stubbed"}
        try:
            result = worker.run_one_step()
            self.assertEqual(calls, ["world_bank"])
            self.assertEqual(result["action"], "stubbed")
        finally:
            worker._generate_world_bank = original

    def test_ready_gate_dispatches_to_next_campaign_when_world_bank_exists(self):
        self._write_status(_base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
        ))
        calls = []
        original = worker._run_next_campaign
        worker._run_next_campaign = lambda status: calls.append("campaign") or {"action": "stubbed"}
        try:
            worker.run_one_step()
            self.assertEqual(calls, ["campaign"])
        finally:
            worker._run_next_campaign = original

    def test_ready_gate_dispatches_to_finalize_when_all_campaigns_complete(self):
        self._write_status(_base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
            campaigns_complete=5,
        ))
        calls = []
        original = worker._finalize_or_expand
        worker._finalize_or_expand = lambda status: calls.append("finalize") or {"action": "stubbed"}
        try:
            worker.run_one_step()
            self.assertEqual(calls, ["finalize"])
        finally:
            worker._finalize_or_expand = original


class MainEntryPoint(WorkerHarness):
    def test_lock_held_elsewhere_is_a_clean_skip_not_a_crash(self):
        self._write_status(_base_status())
        with worker_lock(worker.LOCK_PATH):
            exit_code = worker.main()
        self.assertEqual(exit_code, 0)

    def test_workerstop_is_recorded_into_status_json_and_exits_nonzero(self):
        (self.work / "FREEZE_AUDIT_HOLD.md").write_text("hold\n")
        _git(self.work, "add", "FREEZE_AUDIT_HOLD.md")
        _git(self.work, "commit", "-m", "hold")
        _git(self.work, "push", "origin", BRANCH)
        self._write_status(_base_status(audit_window_ends_utc="2020-01-01T00:00:00Z"))

        exit_code = worker.main()
        self.assertEqual(exit_code, 1)
        recorded = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(recorded["state"], "stopped")
        self.assertEqual(recorded["stop_code"], "H")

    def test_lock_is_released_after_a_stop_so_a_later_invocation_can_run(self):
        self._write_status(_base_status())  # idle window: run_one_step returns cleanly
        first = worker.main()
        second = worker.main()
        self.assertEqual((first, second), (0, 0))


class CrashRecovery(WorkerHarness):
    """worker_checkpoint.py's own test suite already proves prepare_transaction/
    recover_pending_transaction's generic crash-safety. What these tests add:
    that fitness_v2_worker's OWN entry point (run_one_step -- called at the
    top of every invocation, before any new work) actually benefits from
    that guarantee for the artifact shapes and paths this worker really
    uses, and that a fully-deterministic redo (the safe fallback this
    worker's current design relies on for mid-campaign crashes -- see the
    "not yet implemented" note in fitness_v2_worker.py's commit -- restarts a
    campaign from Gen0 rather than resuming generation N) never corrupts or
    duplicates an already-committed artifact."""

    def test_pending_transaction_from_a_simulated_crash_is_recovered_before_new_work(self):
        # Simulate: prepare_transaction wrote the manifest (durable proof of
        # intent) but the process died before recover_pending_transaction's
        # hardlink-commit step ran -- exactly worker_checkpoint's own crash
        # window. The NEXT invocation's run_one_step() must resolve this
        # before doing anything else.
        artifacts = {"evolution/state/world_bank.json": {"historical": ["H1", "H2", "H3"]}}
        prepare_transaction(worker.REPO_ROOT, "crash_test", artifacts, allowed_prefixes={"evolution"})
        final_path = worker.REPO_ROOT / "evolution" / "state" / "world_bank.json"
        self.assertFalse(final_path.exists())  # not yet committed -- this IS the crash window

        self._write_status(_base_status())  # idle window: run_one_step does no new work either way
        result = worker.run_one_step()

        self.assertEqual(result["action"], "waiting_on_audit_window")
        self.assertTrue(final_path.exists())
        self.assertEqual(json.loads(final_path.read_text()), artifacts["evolution/state/world_bank.json"])
        # The pending-transaction bookkeeping itself is gone -- recovered, not left dangling.
        self.assertFalse((worker.REPO_ROOT / ".worker_checkpoint_transaction.json").exists())

    def test_deterministic_redo_of_an_already_committed_generation_is_a_safe_no_op(self):
        # Models the current worker's mid-campaign crash fallback: if the
        # process dies partway through a campaign, the next invocation
        # restarts that campaign from Gen0 (same campaign_seed => byte-
        # identical generations) rather than resuming generation N. Proves
        # that redoing generation 0's checkpoint with IDENTICAL content
        # after it was already committed is a safe no-op, never a collision
        # or a silent overwrite.
        artifacts = {"evolution/state/campaign_1_generation_0.json": {"generation": 0, "best": 0.5}}
        worker._checkpoint_and_push("gen0", artifacts, "generation 0")
        first_head = self._git_head()

        # "Restart from scratch": recompute and re-checkpoint the exact same
        # content, exactly as a deterministic campaign redo would.
        worker._checkpoint_and_push("gen0_redo", artifacts, "generation 0 (redo after crash)")

        self.assertEqual(self._git_head(), first_head)  # nothing new to commit -- true no-op
        recorded = json.loads(
            (worker.REPO_ROOT / "evolution" / "state" / "campaign_1_generation_0.json").read_text()
        )
        self.assertEqual(recorded, artifacts["evolution/state/campaign_1_generation_0.json"])

    def test_redo_with_genuinely_different_content_is_rejected_not_silently_overwritten(self):
        # If determinism were ever violated (a real bug, not a crash), the
        # checkpoint layer must fail closed rather than silently replace
        # already-committed evidence with a different result.
        first = {"evolution/state/campaign_1_generation_0.json": {"generation": 0, "best": 0.5}}
        worker._checkpoint_and_push("gen0", first, "generation 0")

        second = {"evolution/state/campaign_1_generation_0.json": {"generation": 0, "best": 0.999}}
        with self.assertRaises(ArtifactCollision):
            prepare_transaction(worker.REPO_ROOT, "gen0_conflict", second, allowed_prefixes={"evolution"})
            recover_pending_transaction(worker.REPO_ROOT)

    def _git_head(self) -> str:
        return _git(self.work, "rev-parse", "HEAD")


if __name__ == "__main__":
    unittest.main()
