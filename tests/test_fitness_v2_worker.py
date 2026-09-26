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


if __name__ == "__main__":
    unittest.main()
