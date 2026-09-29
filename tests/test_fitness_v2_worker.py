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


class RealParameterFreezeArtifact(unittest.TestCase):
    """Exercises load_parameter_freeze() against the REAL, committed
    evolution/protocol/fitness_v2_parameter_freeze_*.json -- not a fixture.
    Every other loader in this codebase (evolution_protocol, formula_
    definitions) wraps its frozen content as {"manifest_id":...,
    "content": {...}}; the parameter-freeze file is flat (manifest_id sits
    alongside the actual fields) and validate_parameter_freeze (fitness_v2.py)
    is written for that flat shape. load_parameter_freeze() previously
    assumed the wrapped shape and did `return envelope["content"]`, which
    raised KeyError against the real file -- caught only by the first real
    NODE invocation (2026-09-27), since every _generate_world_bank-adjacent
    test uses fixture data and never called this loader for real. This test
    exists so that class of drift is caught locally next time, not live."""

    def test_returns_the_flat_manifest_synthetic_families_are_read_from(self):
        freeze = worker.load_parameter_freeze()
        self.assertIn("synthetic_families", freeze)
        for family in ("distributional", "execution", "sequence", "shock"):
            self.assertIn(family, freeze["synthetic_families"])
            self.assertIn("seeds", freeze["synthetic_families"][family])


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
            for name in (
                "REPO_ROOT", "STATUS_PATH", "CHECKPOINT_DIR", "LOCK_PATH", "BRANCH",
                "disk_gate", "_active_protocol_manifest_id",
            )
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
        # Real _active_protocol_manifest_id() reads the real, committed
        # evolution/protocol/fitness_v2_complete_protocol_*.json, which does
        # not exist in this test's scratch repo -- stub it to match
        # _base_status()'s own "x" placeholder, exactly like disk_gate above.
        # StaleReconstructionTests below overrides this per-test to exercise
        # a genuine mismatch.
        worker._active_protocol_manifest_id = lambda: "x"

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(worker, name, value)
        self._tmp_ctx.cleanup()

    def _write_status(self, status: dict) -> None:
        worker.STATUS_PATH.write_text(json.dumps(status, indent=2) + "\n")
        _git(self.work, "add", "STATUS.json")
        _git(self.work, "commit", "-m", "status")
        _git(self.work, "push", "origin", BRANCH)

    def _write_world_bank(self, world_bank_id: str, synthetic_counts: dict | None = None) -> None:
        """A minimal-but-durably-valid world-bank checkpoint file, written
        directly to CHECKPOINT_DIR (no git commit needed -- reconcile_status
        only reads the local filesystem, matching production: a worker
        never needs its OWN just-committed checkpoint to still be reachable
        over git to resume from it locally). Defaults to 4/family = 16, the
        real initial-batch count, so _expansion_batch_id succeeds."""
        counts = synthetic_counts or {f: 4 for f in ("distributional", "execution", "sequence", "shock")}
        bank = {
            "historical": [{}, {}, {}],
            "synthetic": {family: [{}] * n for family, n in counts.items()},
            "synthetic_state": {
                family: {"consumed_indices": [], "used_seeds": [], "attempts": []}
                for family in counts
            },
        }
        worker.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        (worker.CHECKPOINT_DIR / f"{world_bank_id}.json").write_text(json.dumps(bank))

    def _write_nominee(self, batch_id: int, seed: int, *, clears_all_gates: bool = True) -> None:
        worker.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        path = worker.CHECKPOINT_DIR / f"batch{batch_id}_campaign_{seed}_nominee.json"
        path.write_text(json.dumps({
            "genome_id": f"gen_{seed}", "evolution_seed": seed,
            "clears_all_gates": clears_all_gates, "cross_family_fitness": 0.0,
        }))


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
        self._write_world_bank("world_bank_v1")
        self._write_status(_base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
            synthetic_world_count=16,
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
        self._write_world_bank("world_bank_v1")
        for seed in worker.CAMPAIGN_SEEDS:
            self._write_nominee(0, seed)
        self._write_status(_base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
            synthetic_world_count=16, campaigns_complete=5, rank1_qualifiers=5,
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


class MidCampaignResume(WorkerHarness):
    """Gap 1 (handoff §58, "Never reconstruct completed generations just
    because a process died. Resume from the latest durable transactional
    checkpoint."): proves the worker now resumes at generation N+1 rather
    than the deterministic-Gen0-redo fallback CrashRecovery above
    documents -- and that the resumed campaign is byte-identical to an
    uninterrupted reference run of the same seed and worlds."""

    SEED = 2066557696

    def _seed_world_bank(self):
        from test_fitness_v2_campaign import _tiny_world_bank
        historical, synthetic = _tiny_world_bank(scored_length=40)
        bank = {
            "historical": historical,
            "synthetic": synthetic,
            "synthetic_state": {
                family: {"consumed_indices": [], "used_seeds": []} for family in synthetic
            },
        }
        worker.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        (worker.CHECKPOINT_DIR / "world_bank_v1.json").write_text(json.dumps(bank))
        _git(self.work, "add", "evolution/state/world_bank_v1.json")
        _git(self.work, "commit", "-m", "world bank")
        _git(self.work, "push", "origin", BRANCH)
        return historical, synthetic

    def test_resume_after_simulated_crash_never_repeats_a_completed_generation(self):
        historical, synthetic = self._seed_world_bank()
        self._write_status(_base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z",
            world_bank_id="world_bank_v1", synthetic_world_count=16, campaigns_complete=0,
        ))

        calls: list[int] = []
        original_run_generation = worker.run_generation

        def crash_before_generation_3(*args, **kwargs):
            generation = args[1]
            calls.append(generation)
            if generation == 3:
                raise RuntimeError("simulated crash before generation 3")
            return original_run_generation(*args, **kwargs)

        worker.run_generation = crash_before_generation_3
        try:
            with self.assertRaises(RuntimeError):
                worker.run_one_step()
        finally:
            worker.run_generation = original_run_generation

        self.assertEqual(calls, [0, 1, 2, 3])  # 0-2 completed & checkpointed; 3 attempted, then "died"
        pre_crash_status = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(pre_crash_status["generation"], 2)
        self.assertEqual(pre_crash_status["campaign_seed"], self.SEED)
        gen2_checkpoint = json.loads(
            worker._generation_checkpoint_path(0, self.SEED, 2).read_text()
        )
        gen2_head_before_resume = self._git_head()

        calls.clear()
        worker.run_generation = lambda *a, **kw: (calls.append(a[1]) or original_run_generation(*a, **kw))
        try:
            result = worker.run_one_step()
        finally:
            worker.run_generation = original_run_generation

        self.assertEqual(calls, [3, 4, 5, 6, 7, 8, 9, 10])  # never re-ran 0-2
        self.assertEqual(result["action"], "completed_campaign")

        # Completed generations survived interruption unchanged -- no
        # duplicate/rewritten evidence for a generation already durable
        # before the crash.
        self.assertEqual(
            json.loads(worker._generation_checkpoint_path(0, self.SEED, 2).read_text()),
            gen2_checkpoint,
        )
        self._git(self.work, "log", "--oneline", gen2_head_before_resume, "-1")  # still reachable

        status = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(status["campaigns_complete"], 1)

        # Population identity + RNG/seed-state fidelity: an uninterrupted
        # reference run of the identical seed/worlds must land on the exact
        # same nominee and fitness -- the resume path is not merely
        # "different but plausible", it reproduces the one true continuation.
        from fitness_v2_campaign import run_campaign
        reference_sharpes = {"execution": worker.reference_passive_sharpes(historical)}
        reference = run_campaign(self.SEED, historical, synthetic, reference_sharpes)
        resumed_nominee = json.loads(worker._nominee_checkpoint_path(0, self.SEED).read_text())
        self.assertEqual(
            resumed_nominee["genome_id"],
            reference["final_admission_nominee"]["genome_id"],
        )
        self.assertEqual(
            resumed_nominee["cross_family_fitness"],
            reference["final_admission_nominee"]["evaluation"]["cross_family_fitness"],
        )

    def _git(self, cwd, *args):
        return _git(cwd, *args)

    def _git_head(self) -> str:
        return _git(self.work, "rev-parse", "HEAD")


class WorldBankGeneration(WorkerHarness):
    """2026-09-27 incident: _generate_world_bank seeded admitted_reference_
    vectors at [] and only grew it on a *successful* admission. Since
    "distributional" (SYNTHETIC_FAMILIES[0]) is not distance-exempt, every
    one of its candidates hit market_distance_passes' own fail-closed "not
    references" guard (fitness_v2_admission.py, unchanged by this repair --
    still proven by test_fitness_v2_admission.py::test_invalid_descriptors_
    fail_closed_for_market_families's ([0], []) case), which raises rather
    than admits. That guard is correct on its own terms; the bug was calling
    it from a state (zero references) that can only ever raise, with no way
    out, since only a non-raising admission grows the list. This is an
    unrecoverable deadlock, not a slow-but-valid search -- proven live: NODE
    invocation 08:12 2026-09-27 burned ~7h54m of CPU this way before Rick's
    GO to stop it, and produced no world bank (world_bank_id stayed null the
    entire time; see FITNESS_V2_STALLED_WORKER_REPAIR_20260927.md).

    Handoff §24 requires diversity against historical anchors too
    ("sufficiently distant from historical anchors and earlier admitted
    synthetic market worlds"), so the fix seeds admitted_vectors with the 3
    historical worlds' own descriptor vectors before the family loop starts
    -- the same reconstruction _expand_and_continue already does from a
    persisted bank's synthetic worlds, just extended to include historical.

    These tests run the REAL build_synthetic_family / market_distance_passes
    / world_distance_admission / draw_next end to end unmocked -- only the
    expensive, real-data-dependent loaders and the ~10-min-per-vector
    descriptor math are faked (at 1 float component instead of 32), so
    termination and genuine threshold enforcement are proven, not assumed."""

    def setUp(self):
        super().setUp()
        import fitness_v2_world_bank as wb
        from fitness_v2_protocol import COMPONENT_NAMES
        self._wb = wb
        self._originals2 = {
            name: getattr(worker, name)
            for name in (
                "load_complete_protocol", "load_parameter_freeze", "load_real_development_pool",
                "historical_world", "_world_bank_build_context", "world_descriptor_vector", "scored_only",
            )
        }
        self._wb_originals = {name: getattr(wb, name) for name in ("world_descriptor_vector", "scored_only")}

        worker.load_complete_protocol = lambda: {}
        worker.load_real_development_pool = lambda: {}
        worker.load_parameter_freeze = lambda: {
            "synthetic_families": {
                # Seed 0.0 is deliberately identical to H1's fake anchor
                # position below -- it must be REJECTED by the real 0.75
                # threshold once seeded, proving the historical vectors are
                # genuinely enforced, not just present. The remaining seeds
                # (100/200/300) are far enough apart to admit in one pass.
                "distributional": {"seeds": [0.0, 100.0, 200.0, 300.0]},
                "execution": {"seeds": [1000.0, 1001.0, 1002.0, 1003.0]},
                "sequence": {"seeds": [2000.0, 2001.0, 2002.0, 2003.0]},
                "shock": {"seeds": [3000.0, 3001.0, 3002.0, 3003.0]},
            }
        }
        worker.historical_world = lambda pool, shape, protocol: {
            "x": {"H1": 0.0, "H2": 10.0, "H3": 20.0}[shape]
        }
        # normalized_descriptor_vector (fitness_v2_protocol.py) always
        # iterates the real, fixed 32-name COMPONENT_NAMES -- not whatever
        # keys a vector happens to have -- so median/mad and every fake
        # descriptor vector must carry all 32, not just one "x" key. Every
        # component is set to the SAME scalar (world["x"]), which collapses
        # the real RMS-normalized-Euclidean-distance formula to plain
        # |a - b|: exactly the simple 1-D distances this test reasons about.
        worker._world_bank_build_context = lambda protocol, pool, historical: (
            0.02,
            {"median": {name: 0.0 for name in COMPONENT_NAMES}, "mad": {name: 1.0 for name in COMPONENT_NAMES}},
            lambda family: (lambda seed: {"x": seed}),
        )
        fake_descriptor = lambda world, tau: {name: world["x"] for name in COMPONENT_NAMES}
        fake_scored = lambda world: world
        worker.world_descriptor_vector = fake_descriptor
        worker.scored_only = fake_scored
        # build_synthetic_family runs unmocked and calls fitness_v2_world_
        # bank's OWN bindings of these two names (imported there separately
        # from fitness_v2_protocol) -- patching only worker's copies would
        # leave the real, ~10-min-per-vector math running inside it.
        wb.world_descriptor_vector = fake_descriptor
        wb.scored_only = fake_scored

    def tearDown(self):
        for name, value in self._originals2.items():
            setattr(worker, name, value)
        for name, value in self._wb_originals.items():
            setattr(self._wb, name, value)
        super().tearDown()

    def test_first_distributional_candidate_is_seeded_with_historical_vectors_not_empty(self):
        self._write_status(_base_status(audit_window_ends_utc="2020-01-01T00:00:00Z"))

        result = worker.run_one_step()

        self.assertEqual(result["action"], "generated_world_bank")
        bank = json.loads((worker.CHECKPOINT_DIR / "world_bank_v1.json").read_text())

        # The deadlock this reproduces: with the old admitted_vectors = [],
        # this family's search could never terminate. Proof it now does:
        # exactly slot_count=4 worlds were admitted, not zero and not stuck.
        self.assertEqual(len(bank["synthetic"]["distributional"]), 4)

        # Anti-weakening proof: seed 0.0 collides exactly with H1's seeded
        # vector (distance 0 < 0.75) and must be rejected by the real
        # threshold check -- not waved through because it happens to be the
        # very first candidate ever attempted.
        attempts = bank["synthetic_state"]["distributional"]["attempts"]
        first = attempts[0]
        self.assertEqual(first["seed"], 0.0)
        self.assertFalse(first["accepted"])
        self.assertEqual(first["reason"], "failed_market_distance_admission")

        # And the other three initial seeds (100/200/300), each far from
        # every seeded historical vector, were admitted without needing the
        # replacement stream.
        accepted_seeds = [a["seed"] for a in attempts if a["accepted"]]
        self.assertEqual(accepted_seeds[:3], [100.0, 200.0, 300.0])

        status = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(status["world_bank_id"], "world_bank_v1")
        self.assertEqual(status["synthetic_world_count"], 16)

    def test_admitted_vectors_start_from_all_three_historical_worlds_before_any_family_runs(self):
        # Direct proof of the fix's own precondition, independent of the
        # end-to-end admission outcome above: the very first
        # build_synthetic_family call for the first family must already see
        # 3 references (H1/H2/H3), never 0.
        self._write_status(_base_status(audit_window_ends_utc="2020-01-01T00:00:00Z"))
        original = self._wb.build_synthetic_family
        seen_lengths = []

        def spy(*args, **kwargs):
            seen_lengths.append(len(kwargs["admitted_reference_vectors"]))
            return original(*args, **kwargs)

        worker.build_synthetic_family = spy
        try:
            worker.run_one_step()
        finally:
            worker.build_synthetic_family = original
        self.assertEqual(seen_lengths[0], 3)


class WorldBankExpansion(WorkerHarness):
    """Gap 2 (handoff §50) + Rick's 2026-09-26 multi-batch freeze
    (FITNESS_V2_EXPANSION_CAMPAIGN_CYCLE_FREEZE_20260926.md): wires
    fitness_v2_world_bank.expand_family into the worker's finalize/expand
    step. expand_family's own mechanics (ceiling, idempotent stream
    continuation, real diversity-distance admission) are already proven in
    tests/test_fitness_v2_world_bank.py::FamilyExpansion, at real world
    scale -- these tests stub expand_family/world_descriptor_vector/
    scored_only/_world_bank_build_context to cheap fakes so they can prove
    what's actually new here fast and deterministically: _expand_and_
    continue's OWN orchestration -- reconstructing the cross-family
    admitted-vector accumulation state from a persisted bank, calling
    expand_family once per family in order, advancing world_bank_id/
    synthetic_world_count, resetting campaign progress and continuing
    straight into the next batch (never holding) unless the frozen 32-world
    ceiling is reached, which alone is the terminal HOLD."""

    def setUp(self):
        super().setUp()
        self._originals2 = {
            name: getattr(worker, name)
            for name in (
                "load_complete_protocol", "load_real_development_pool",
                "_world_bank_build_context", "world_descriptor_vector", "scored_only", "expand_family",
            )
        }
        worker.load_complete_protocol = lambda: {}
        worker.load_real_development_pool = lambda: {}
        worker._world_bank_build_context = lambda protocol, pool, historical: (
            0.02, {}, lambda family: (lambda seed: f"{family}_candidate_{seed}")
        )
        # scored_only/world_descriptor_vector run once per ALREADY-admitted
        # world (to reconstruct admitted_reference_vectors) -- these fakes
        # just need to be cheap and to round-trip a world identity through,
        # not compute anything real (real descriptor math is proven
        # elsewhere, at real world scale).
        worker.scored_only = lambda world: world
        worker.world_descriptor_vector = lambda world, tau: {"descriptor_of": world}

        self.expand_calls: list[tuple] = []

        def fake_expand_family(family, prior_state, build_candidate, reference, admitted_reference_vectors, tau):
            from fitness_v2_world_bank import ExpansionHeld
            self.expand_calls.append((family, len(admitted_reference_vectors)))
            existing = prior_state["worlds"]
            if len(existing) >= 8:  # MAX_WORLDS_PER_FAMILY
                raise ExpansionHeld(f"{family} at ceiling")
            new_world = f"{family}_world_{len(existing)}"
            admitted_reference_vectors.append({"descriptor_of": new_world})
            return {
                "worlds": [*existing, new_world],
                "consumed_indices": [*prior_state["consumed_indices"], len(existing) + 1],
                "used_seeds": [*prior_state["used_seeds"], 900 + len(existing)],
                "attempts": [*prior_state["attempts"], {"accepted": True, "world": new_world}],
            }

        worker.expand_family = fake_expand_family

    def tearDown(self):
        for name, value in self._originals2.items():
            setattr(worker, name, value)
        super().tearDown()

    def _seed_bank(self, worlds_per_family=4):
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        synthetic = {f: [f"{f}_world_{i}" for i in range(worlds_per_family)] for f in SYNTHETIC_FAMILIES}
        synthetic_state = {
            f: {
                "consumed_indices": list(range(1, worlds_per_family + 1)),
                "used_seeds": list(range(100, 100 + worlds_per_family)), "attempts": [],
            }
            for f in SYNTHETIC_FAMILIES
        }
        bank = {"historical": ["H1", "H2", "H3"], "synthetic": synthetic, "synthetic_state": synthetic_state}
        worker.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        (worker.CHECKPOINT_DIR / "world_bank_v1.json").write_text(json.dumps(bank))
        _git(self.work, "add", "evolution/state/world_bank_v1.json")
        _git(self.work, "commit", "-m", "world bank")
        _git(self.work, "push", "origin", BRANCH)
        return synthetic

    def _status_after_batch(self, **overrides):
        return _base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
            synthetic_world_count=16, campaigns_complete=5, **overrides,
        )

    def test_expansion_calls_expand_family_once_per_family_with_accumulating_vectors(self):
        synthetic = self._seed_bank()
        status = self._status_after_batch()
        self._write_status(status)

        result = worker._expand_and_continue(status, admission={"admitted": False})

        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        self.assertEqual([c[0] for c in self.expand_calls], list(SYNTHETIC_FAMILIES))
        # admitted_reference_vectors starts with the 3 historical worlds'
        # descriptors plus all 16 pre-existing synthetic worlds' descriptors
        # (reconstructed from the persisted bank) and grows by one for every
        # family expanded so far this cycle -- proves the cross-family
        # accumulation is threaded through in order, not reset per family or
        # left incomplete, AND that historical anchors are seeded in too
        # (the expansion-path counterpart of WorldBankGeneration's fix above;
        # real distance-admission proof against real historical/synthetic
        # collisions lives in WorldBankExpansionDiversity below).
        self.assertEqual([c[1] for c in self.expand_calls], [19, 20, 21, 22])

        self.assertEqual(result["action"], "expanded_world_bank")
        self.assertEqual(result["synthetic_world_count"], 20)
        written = json.loads(worker.STATUS_PATH.read_text())
        # Rick's multi-batch freeze: expansion RESETS and CONTINUES straight
        # into the next batch -- it never holds (that's reserved for the
        # 32-world ceiling alone).
        self.assertEqual(written["state"], "running")
        self.assertEqual(written["phase"], "campaign")
        self.assertIsNone(written["stop_code"])
        self.assertEqual(written["synthetic_world_count"], 20)
        self.assertNotEqual(written["world_bank_id"], "world_bank_v1")
        self.assertEqual(written["campaigns_complete"], 0)
        self.assertIsNone(written["campaign"])
        self.assertIsNone(written["campaign_seed"])
        self.assertIsNone(written["generation"])
        self.assertEqual(written["rank1_qualifiers"], 0)

        new_bank = json.loads((worker.CHECKPOINT_DIR / f"{written['world_bank_id']}.json").read_text())
        for family in synthetic:
            self.assertEqual(len(new_bank["synthetic"][family]), 5)
            # The original four worlds per family are preserved unchanged.
            self.assertEqual(new_bank["synthetic"][family][:4], synthetic[family])

        # A worker invocation after a reset campaigns_complete=0 must
        # dispatch straight back into _run_next_campaign for the new batch,
        # never idle -- unlike the terminal-ceiling case below.
        calls = []
        original_run_next_campaign = worker._run_next_campaign
        worker._run_next_campaign = lambda status: calls.append(status["synthetic_world_count"]) or {"action": "stubbed"}
        try:
            again = worker.run_one_step()
        finally:
            worker._run_next_campaign = original_run_next_campaign
        self.assertEqual(calls, [20])
        self.assertEqual(again["action"], "stubbed")

    def test_a_qualifying_champion_does_not_stop_expansion(self):
        # Rick's multi-batch freeze: "A qualifying research champion in an
        # earlier batch does not terminate the expansion program."
        self._seed_bank()
        status = self._status_after_batch()
        self._write_status(status)

        result = worker._expand_and_continue(
            status, admission={"admitted": True, "selected_genome_id": "gen_champion"},
        )
        self.assertEqual(result["action"], "expanded_world_bank")
        written = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(written["state"], "running")  # not "hold" -- the champion changes nothing here
        self.assertEqual(written["synthetic_world_count"], 20)

    def test_expansion_holds_at_the_frozen_32_world_ceiling(self):
        self._seed_bank()
        status = self._status_after_batch()
        self._write_status(status)

        result = None
        for _ in range(4):  # 16 -> 20 -> 24 -> 28 -> 32
            result = worker._expand_and_continue(status, admission={"admitted": False})
            status = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(result["action"], "expanded_world_bank")
        self.assertEqual(status["synthetic_world_count"], 32)
        self.assertEqual(status["state"], "running")  # batch 4 still runs against the 32-world bank

        held = worker._expand_and_continue(status, admission={"admitted": False})
        self.assertEqual(held["action"], "expansion_held")
        final_status = json.loads(worker.STATUS_PATH.read_text())
        self.assertEqual(final_status["state"], "hold")
        self.assertEqual(final_status["phase"], "terminal_hold")
        self.assertEqual(final_status["synthetic_world_count"], 32)  # unchanged -- no partial expansion

        # And the hold guard idles here too, on the ceiling path specifically.
        again = worker.run_one_step()
        self.assertEqual(again["action"], "holding")


class WorldBankExpansionDiversity(WorkerHarness):
    """Gap recorded alongside the admission-deadlock repair (commit
    364f6340, 2026-09-27): _expand_and_continue's admitted_vectors started
    from prior synthetic worlds only, never including H1/H2/H3. Handoff §24
    requires diversity against BOTH "historical anchors AND earlier admitted
    synthetic market worlds" -- the same rule _generate_world_bank was fixed
    to honor (see WorldBankGeneration above). Unlike the initial-generation
    bug this was never a deadlock (synthetic[] is never empty by the time
    expansion runs), but a silent under-enforcement: an expansion candidate
    identical to a historical anchor could be admitted undetected. Fixed the
    same way -- fold the historical worlds' own descriptor vectors in ahead
    of the prior synthetic ones.

    Runs the REAL expand_family / build_synthetic_family / market_distance_
    passes / world_distance_admission / draw_next end to end unmocked --
    only _world_bank_build_context's candidate builder and the descriptor
    math are faked (1 float component instead of 32), exactly as
    WorldBankGeneration does above for the initial-generation path. The
    FamilyExpansion tests in test_fitness_v2_world_bank.py only exercise
    expand_family directly, and only with "execution" (distance-exempt) --
    they never prove _expand_and_continue's own admitted_vectors
    reconstruction against a real, non-exempt distance check. Replacement
    seeds are computed with the real world_seed_stream.derive_seed rather
    than hardcoded, so these tests stay correct if the stream formula ever
    changes."""

    def setUp(self):
        super().setUp()
        import fitness_v2_world_bank as wb
        from fitness_v2_protocol import COMPONENT_NAMES
        self._wb = wb
        self._component_names = COMPONENT_NAMES
        self._originals2 = {
            name: getattr(worker, name)
            for name in (
                "load_complete_protocol", "load_real_development_pool",
                "_world_bank_build_context", "world_descriptor_vector", "scored_only",
            )
        }
        self._wb_originals = {name: getattr(wb, name) for name in ("world_descriptor_vector", "scored_only")}

        worker.load_complete_protocol = lambda: {}
        worker.load_real_development_pool = lambda: {}
        worker._world_bank_build_context = lambda protocol, pool, historical: (
            0.02,
            {"median": {name: 0.0 for name in COMPONENT_NAMES}, "mad": {name: 1.0 for name in COMPONENT_NAMES}},
            lambda family: (lambda seed: {"x": seed}),
        )
        fake_descriptor = lambda world, tau: {name: world["x"] for name in COMPONENT_NAMES}
        fake_scored = lambda world: world
        worker.world_descriptor_vector = fake_descriptor
        worker.scored_only = fake_scored
        wb.world_descriptor_vector = fake_descriptor
        wb.scored_only = fake_scored

    def tearDown(self):
        for name, value in self._originals2.items():
            setattr(worker, name, value)
        for name, value in self._wb_originals.items():
            setattr(self._wb, name, value)
        super().tearDown()

    def _seed_bank(self, historical_x, synthetic_x, used_seeds_by_family):
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        historical = [{"x": x} for x in historical_x]
        synthetic = {f: [{"x": x} for x in synthetic_x[f]] for f in SYNTHETIC_FAMILIES}
        synthetic_state = {
            f: {
                "consumed_indices": list(range(1, len(synthetic_x[f]) + 1)),
                "used_seeds": list(used_seeds_by_family[f]),
                "attempts": [],
            }
            for f in SYNTHETIC_FAMILIES
        }
        bank = {"historical": historical, "synthetic": synthetic, "synthetic_state": synthetic_state}
        worker.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        (worker.CHECKPOINT_DIR / "world_bank_v1.json").write_text(json.dumps(bank))
        _git(self.work, "add", "evolution/state/world_bank_v1.json")
        _git(self.work, "commit", "-m", "world bank")
        _git(self.work, "push", "origin", BRANCH)
        return bank

    def _status_after_batch(self, **overrides):
        return _base_status(
            audit_window_ends_utc="2020-01-01T00:00:00Z", world_bank_id="world_bank_v1",
            synthetic_world_count=16, campaigns_complete=5, **overrides,
        )

    def test_admitted_vectors_for_first_expanding_family_start_with_historical_then_prior_synthetic(self):
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        historical_x = [0.0, 10.0, 20.0]
        synthetic_x = {f: [100.0, 200.0, 300.0, 400.0] for f in SYNTHETIC_FAMILIES}
        used_seeds = {f: [11, 22, 33, 44] for f in SYNTHETIC_FAMILIES}
        self._seed_bank(historical_x, synthetic_x, used_seeds)
        status = self._status_after_batch()
        self._write_status(status)

        original = self._wb.build_synthetic_family
        seen = []

        def spy(*args, **kwargs):
            seen.append(list(kwargs["admitted_reference_vectors"]))
            return original(*args, **kwargs)

        self._wb.build_synthetic_family = spy
        try:
            worker._expand_and_continue(status, admission={"admitted": False})
        finally:
            self._wb.build_synthetic_family = original

        first_family_vectors = seen[0]
        # 3 historical + 16 prior synthetic (4 per family) -- not 16 alone,
        # which is exactly the gap this repairs.
        self.assertEqual(len(first_family_vectors), 19)
        component = self._component_names[0]
        self.assertEqual([v[component] for v in first_family_vectors[:3]], historical_x)

    def test_candidate_identical_to_historical_anchor_is_rejected_then_replacement_admits(self):
        from world_seed_stream import derive_seed
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        used = [11, 22, 33, 44]
        s1 = derive_seed("distributional", 5, used_seeds=used)["seed"]
        s2 = derive_seed("distributional", 6, used_seeds=[*used, s1])["seed"]

        historical_x = [float(s1), 10.0, 20.0]  # H1 sits exactly where the next draw lands
        synthetic_x = {f: [100.0, 200.0, 300.0, 400.0] for f in SYNTHETIC_FAMILIES}
        used_seeds = {f: list(used) for f in SYNTHETIC_FAMILIES}
        self._seed_bank(historical_x, synthetic_x, used_seeds)
        status = self._status_after_batch()
        self._write_status(status)

        worker._expand_and_continue(status, admission={"admitted": False})

        written = json.loads(worker.STATUS_PATH.read_text())
        bank = json.loads((worker.CHECKPOINT_DIR / f"{written['world_bank_id']}.json").read_text())
        attempts = bank["synthetic_state"]["distributional"]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["seed"], s1)
        self.assertFalse(attempts[0]["accepted"])
        self.assertEqual(attempts[0]["reason"], "failed_market_distance_admission")
        self.assertEqual(attempts[1]["seed"], s2)
        self.assertTrue(attempts[1]["accepted"])

        self.assertEqual(bank["synthetic"]["distributional"][-1]["x"], s2)
        self.assertEqual(
            sorted(bank["synthetic_state"]["distributional"]["consumed_indices"]), [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(bank["synthetic_state"]["distributional"]["used_seeds"], [*used, s1, s2])

    def test_candidate_identical_to_prior_synthetic_world_is_rejected_then_replacement_admits(self):
        from world_seed_stream import derive_seed
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        used = [11, 22, 33, 44]
        s1 = derive_seed("distributional", 5, used_seeds=used)["seed"]
        s2 = derive_seed("distributional", 6, used_seeds=[*used, s1])["seed"]

        historical_x = [0.0, 10.0, 20.0]  # far away -- not the collision under test here
        synthetic_x = {f: [100.0, 200.0, 300.0, 400.0] for f in SYNTHETIC_FAMILIES}
        synthetic_x["distributional"][0] = float(s1)  # a prior admitted world sits exactly there
        used_seeds = {f: list(used) for f in SYNTHETIC_FAMILIES}
        self._seed_bank(historical_x, synthetic_x, used_seeds)
        status = self._status_after_batch()
        self._write_status(status)

        worker._expand_and_continue(status, admission={"admitted": False})

        written = json.loads(worker.STATUS_PATH.read_text())
        bank = json.loads((worker.CHECKPOINT_DIR / f"{written['world_bank_id']}.json").read_text())
        attempts = bank["synthetic_state"]["distributional"]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["seed"], s1)
        self.assertFalse(attempts[0]["accepted"])
        self.assertEqual(attempts[0]["reason"], "failed_market_distance_admission")
        self.assertEqual(attempts[1]["seed"], s2)
        self.assertTrue(attempts[1]["accepted"])
        self.assertEqual(bank["synthetic"]["distributional"][-1]["x"], s2)

    def test_valid_far_candidate_admits_immediately_across_every_family(self):
        from fitness_v2_world_bank import SYNTHETIC_FAMILIES
        historical_x = [0.0, 10.0, 20.0]
        synthetic_x = {f: [100.0, 200.0, 300.0, 400.0] for f in SYNTHETIC_FAMILIES}
        used_seeds = {f: [11, 22, 33, 44] for f in SYNTHETIC_FAMILIES}
        self._seed_bank(historical_x, synthetic_x, used_seeds)
        status = self._status_after_batch()
        self._write_status(status)

        result = worker._expand_and_continue(status, admission={"admitted": False})

        self.assertEqual(result["action"], "expanded_world_bank")
        written = json.loads(worker.STATUS_PATH.read_text())
        bank = json.loads((worker.CHECKPOINT_DIR / f"{written['world_bank_id']}.json").read_text())
        for family in SYNTHETIC_FAMILIES:
            attempts = bank["synthetic_state"][family]["attempts"]
            # Every family's existing/historical worlds are far from the
            # freshly drawn seed -- the real distance gate admits on the
            # very first draw, no replacement needed.
            self.assertEqual(len(attempts), 1)
            self.assertTrue(attempts[0]["accepted"])
            self.assertEqual(len(bank["synthetic"][family]), 5)


class CheckpointNamespaceAcrossBatches(WorkerHarness):
    """Rick's 2026-09-26 freeze: "Every durable runtime identity must
    include enough information to make expansion batches collision-proof
    ... protocol_id -> world_bank_id -> expansion_batch_id -> campaign_seed/
    campaign_index -> generation. A checkpoint from one world-bank/batch
    combination must never satisfy or overwrite a checkpoint belonging to
    another." CAMPAIGN_SEEDS are deliberately reused every batch (same
    freeze), so this is exactly the collision worker_checkpoint's
    ArtifactCollision guard would otherwise be relied on to catch --
    proving the namespace means it never has to."""

    def test_batch_id_is_derived_from_synthetic_world_count(self):
        self.assertEqual(worker._expansion_batch_id(16), 0)
        self.assertEqual(worker._expansion_batch_id(20), 1)
        self.assertEqual(worker._expansion_batch_id(24), 2)
        self.assertEqual(worker._expansion_batch_id(28), 3)
        self.assertEqual(worker._expansion_batch_id(32), 4)
        with self.assertRaises(ValueError):
            worker._expansion_batch_id(17)

    def test_same_seed_and_generation_in_different_batches_never_collide(self):
        seed = 2066557696
        generation = 0
        batch0_path = worker._generation_checkpoint_path(0, seed, generation)
        batch1_path = worker._generation_checkpoint_path(1, seed, generation)
        self.assertNotEqual(batch0_path, batch1_path)

        worker._checkpoint_and_push(
            "batch0_artifact", {str(batch0_path.relative_to(worker.REPO_ROOT)): {"generation": 0, "batch": 0}},
            "batch 0 generation 0",
        )
        # Same (seed, generation) pair, materially DIFFERENT content (as a
        # real second batch against an enlarged world bank would produce) --
        # must not raise ArtifactCollision, and must not touch batch 0's file.
        worker._checkpoint_and_push(
            "batch1_artifact", {str(batch1_path.relative_to(worker.REPO_ROOT)): {"generation": 0, "batch": 1}},
            "batch 1 generation 0",
        )

        self.assertEqual(json.loads(batch0_path.read_text()), {"generation": 0, "batch": 0})
        self.assertEqual(json.loads(batch1_path.read_text()), {"generation": 0, "batch": 1})

    def test_nominee_and_admission_paths_also_namespaced_by_batch(self):
        seed = 2066557696
        self.assertNotEqual(
            worker._nominee_checkpoint_path(0, seed), worker._nominee_checkpoint_path(1, seed),
        )
        self.assertNotEqual(
            worker._admission_decision_path(0), worker._admission_decision_path(1),
        )

    def test_campaign_seed_selection_does_not_depend_on_batch_id(self):
        # Rick's freeze: "reuse exactly the existing five frozen Fitness V2
        # campaign seeds ... Do not generate replacement/new campaign seeds
        # for later expansion batches." _run_next_campaign selects
        # CAMPAIGN_SEEDS[campaigns_complete] -- inspect the source to prove
        # batch_id/synthetic_world_count never enters that expression, only
        # the checkpoint paths built from it.
        import inspect
        source = inspect.getsource(worker._run_next_campaign)
        seed_line = next(line for line in source.splitlines() if "seed = CAMPAIGN_SEEDS[" in line)
        self.assertEqual(seed_line.strip(), "seed = CAMPAIGN_SEEDS[status[\"campaigns_complete\"]]")
        self.assertEqual(worker.CAMPAIGN_SEEDS, [
            2066557696, 604610261, 3608585586, 3251376561, 1894202052,
        ])


if __name__ == "__main__":
    unittest.main()
