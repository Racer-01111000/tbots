from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from fitness_v2_worker import (
    CAMPAIGN_SEEDS, FINAL_GENERATION, _expansion_batch_id,
    _generation_checkpoint_path, _nominee_checkpoint_path, _resume_campaign_state,
)
from worker_state_reconstruction import (
    StateReconstructionError, derive_campaign_progress, derive_durable_progress,
    derive_world_bank_progress, reconcile_status,
)

ACTIVE_MANIFEST_ID = "fitness_v2_complete_protocol_active-test-manifest"
NOW = "2026-09-30T00:00:00Z"

# Real, already-accepted burn-in checkpoints on this NODE checkout -- the
# GO's own guidance: "Use the existing accepted world-bank/generation
# artifacts as realistic fixtures where practical."
REAL_WORLD_BANK = ROOT / "evolution" / "state" / "world_bank_v1.json"
REAL_GEN_PATHS = [
    ROOT / "evolution" / "state" / f"batch0_campaign_2066557696_generation_{g}.json"
    for g in (0, 1, 2)
]


def _paths(checkpoint_dir: Path):
    """Bind the four path-builder callables reconcile_status/derive_*
    expect to one concrete checkpoint_dir, mirroring exactly how
    fitness_v2_worker.py itself calls them (CHECKPOINT_DIR / "...")."""
    def generation_checkpoint_path(batch_id, seed, generation):
        return checkpoint_dir / f"batch{batch_id}_campaign_{seed}_generation_{generation}.json"

    def nominee_checkpoint_path(batch_id, seed):
        return checkpoint_dir / f"batch{batch_id}_campaign_{seed}_nominee.json"

    return generation_checkpoint_path, nominee_checkpoint_path


def _write_world_bank(checkpoint_dir: Path, world_bank_id: str, counts: dict) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    bank = {
        "historical": [{}, {}, {}],
        "synthetic": {family: [{}] * n for family, n in counts.items()},
        "synthetic_state": {
            family: {"consumed_indices": [], "used_seeds": [], "attempts": []} for family in counts
        },
    }
    (checkpoint_dir / f"{world_bank_id}.json").write_text(json.dumps(bank))


def _write_generation(checkpoint_dir, gen_path_fn, batch_id, seed, generation, *, final=False):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    artifact = {"generation": generation, "ranked_genome_ids": [f"gen_{seed}_{generation}"]}
    if final:
        artifact["final_ranked"] = [{"genome_id": f"gen_{seed}_{generation}", "genome": {}}]
    else:
        artifact["next_population"] = [{"genome_id": f"gen_{seed}_{generation}_child"}]
        artifact["used_genome_ids"] = sorted({f"gen_{seed}_{generation}", f"gen_{seed}_{generation}_child"})
    gen_path_fn(batch_id, seed, generation).write_text(json.dumps(artifact))


def _write_nominee(checkpoint_dir, nominee_path_fn, batch_id, seed, clears=True):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    nominee_path_fn(batch_id, seed).write_text(json.dumps({
        "genome_id": f"gen_{seed}", "evolution_seed": seed,
        "clears_all_gates": clears, "cross_family_fitness": 0.0,
    }))


DEFAULT_COUNTS = {"distributional": 4, "execution": 4, "sequence": 4, "shock": 4}


class WorldBankProgressDerivation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.checkpoint_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_world_bank_files_returns_none(self):
        self.assertEqual(derive_world_bank_progress(self.checkpoint_dir), (None, None))

    def test_single_world_bank_is_found(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        self.assertEqual(derive_world_bank_progress(self.checkpoint_dir), ("world_bank_v1", 16))

    def test_highest_count_expansion_wins_over_the_initial_bank(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        _write_world_bank(self.checkpoint_dir, "world_bank_v1_batch1",
                           {"distributional": 5, "execution": 5, "sequence": 5, "shock": 5})
        self.assertEqual(derive_world_bank_progress(self.checkpoint_dir), ("world_bank_v1_batch1", 20))

    def test_malformed_world_bank_file_is_skipped_not_crashed_on(self):
        (self.checkpoint_dir / "world_bank_v1.json").write_text("{not valid json")
        self.assertEqual(derive_world_bank_progress(self.checkpoint_dir), (None, None))

    def test_two_files_tied_for_highest_count_is_ambiguous_and_fails_closed(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        _write_world_bank(self.checkpoint_dir, "world_bank_v1_batch_dup", DEFAULT_COUNTS)
        with self.assertRaises(StateReconstructionError):
            derive_world_bank_progress(self.checkpoint_dir)


class CampaignProgressDerivation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.checkpoint_dir = Path(self._tmp.name)
        self.gen_path, self.nominee_path = _paths(self.checkpoint_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _derive(self):
        return derive_campaign_progress(
            checkpoint_dir=self.checkpoint_dir, batch_id=0, campaign_seeds=CAMPAIGN_SEEDS,
            final_generation=FINAL_GENERATION, generation_checkpoint_path=self.gen_path,
            nominee_checkpoint_path=self.nominee_path,
        )

    def test_nothing_checkpointed_is_a_fresh_batch(self):
        self.assertEqual(self._derive(), (0, None, None, 0))

    def test_generations_0_1_2_resume_point_is_generation_2_seed_unset_only_once_generation_exists(self):
        seed = CAMPAIGN_SEEDS[0]
        for g in (0, 1, 2):
            _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, g)
        campaigns_complete, campaign_seed, generation, rank1 = self._derive()
        self.assertEqual((campaigns_complete, campaign_seed, generation), (0, seed, 2))

    def test_all_five_nominees_present_closes_the_batch(self):
        for seed in CAMPAIGN_SEEDS:
            _write_nominee(self.checkpoint_dir, self.nominee_path, 0, seed, clears=(seed == CAMPAIGN_SEEDS[0]))
        self.assertEqual(self._derive(), (5, None, None, 1))

    def test_non_contiguous_nominees_fail_closed(self):
        _write_nominee(self.checkpoint_dir, self.nominee_path, 0, CAMPAIGN_SEEDS[0])
        _write_nominee(self.checkpoint_dir, self.nominee_path, 0, CAMPAIGN_SEEDS[2])  # gap at index 1
        with self.assertRaises(StateReconstructionError):
            self._derive()

    def test_non_contiguous_generations_fail_closed(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, 0)
        _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, 2)  # gap at generation 1
        with self.assertRaises(StateReconstructionError):
            self._derive()

    def test_malformed_latest_generation_falls_back_to_latest_valid_prior(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, 0)
        _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, 1)
        self.gen_path(0, seed, 2).write_text("{not valid json")
        campaigns_complete, campaign_seed, generation, _ = self._derive()
        self.assertEqual((campaign_seed, generation), (seed, 1))

    def test_malformed_generation_zero_with_no_valid_prior_is_unsafe_and_fails_closed(self):
        seed = CAMPAIGN_SEEDS[0]
        self.gen_path(0, seed, 0).write_text("{not valid json")
        with self.assertRaises(StateReconstructionError):
            self._derive()

    def test_incomplete_generation_missing_next_population_is_treated_as_malformed(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, 0)
        self.gen_path(0, seed, 1).write_text(json.dumps({"generation": 1}))  # no next_population
        _, campaign_seed, generation, _ = self._derive()
        self.assertEqual((campaign_seed, generation), (seed, 0))


class ReconcileStatusPrecedence(unittest.TestCase):
    """Synthetic fixtures, covering the GO's required precedence test
    cases directly. active_protocol_manifest_id is injected (matches how
    fitness_v2_worker.py itself calls reconcile_status), never patched
    globally."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        self.checkpoint_dir = self.repo_root / "evolution" / "state"
        self.status_path = self.repo_root / "STATUS.json"
        self.gen_path, self.nominee_path = _paths(self.checkpoint_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _reconcile(self):
        return reconcile_status(
            repo_root=self.repo_root, status_path=self.status_path,
            checkpoint_dir=self.checkpoint_dir, campaign_seeds=CAMPAIGN_SEEDS,
            final_generation=FINAL_GENERATION, expansion_batch_id=_expansion_batch_id,
            generation_checkpoint_path=self.gen_path, nominee_checkpoint_path=self.nominee_path,
            active_protocol_manifest_id=lambda: ACTIVE_MANIFEST_ID, now_utc=NOW,
        )

    def test_status_missing_world_bank_present_reconstructs_world_bank_state(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        status = self._reconcile()
        self.assertEqual(status["world_bank_id"], "world_bank_v1")
        self.assertEqual(status["synthetic_world_count"], 16)
        self.assertEqual(status["campaigns_complete"], 0)
        self.assertIsNone(status["campaign_seed"])
        self.assertIsNone(status["generation"])
        self.assertEqual(status["protocol_manifest"], ACTIVE_MANIFEST_ID)
        self.assertTrue(self.status_path.exists())

    def test_status_missing_world_bank_plus_generations_reconstructs_campaign_state(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        for g in (0, 1, 2):
            _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, g)
        status = self._reconcile()
        self.assertEqual(status["campaign_seed"], seed)
        self.assertEqual(status["generation"], 2)
        resume_from, population, used_ids, final_ranked = _resume_campaign_state(status, 0, seed)
        self.assertEqual(resume_from, 3)

    def test_stale_status_behind_durable_artifacts_durable_wins(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        for g in (0, 1, 2):
            _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, g)
        stale = _stale_status(world_bank_id="world_bank_v1", synthetic_world_count=16,
                               campaign_seed=seed, generation=0, campaigns_complete=0)
        self.status_path.write_text(json.dumps(stale))
        status = self._reconcile()
        self.assertEqual(status["generation"], 2)
        self.assertEqual(status["recoveries"], stale["recoveries"] + 1)

    def test_status_claiming_unsupported_progress_fails_closed(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        ahead = _stale_status(world_bank_id="world_bank_v1", synthetic_world_count=16,
                               campaign_seed=CAMPAIGN_SEEDS[0], generation=5, campaigns_complete=0)
        self.status_path.write_text(json.dumps(ahead))
        with self.assertRaises(StateReconstructionError):
            self._reconcile()

    def test_status_claiming_a_world_bank_id_with_no_durable_file_fails_closed(self):
        ghost = _stale_status(world_bank_id="world_bank_v1", synthetic_world_count=16,
                               campaign_seed=None, generation=None, campaigns_complete=0)
        self.status_path.write_text(json.dumps(ghost))
        with self.assertRaises(StateReconstructionError):
            self._reconcile()

    def test_protocol_manifest_mismatch_fails_closed(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        stale = _stale_status(world_bank_id="world_bank_v1", synthetic_world_count=16,
                               campaign_seed=None, generation=None, campaigns_complete=0,
                               protocol_manifest="a-superseded-manifest")
        self.status_path.write_text(json.dumps(stale))
        with self.assertRaises(StateReconstructionError):
            self._reconcile()

    def test_consistent_status_is_returned_unchanged_no_repair_write(self):
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        first = self._reconcile()
        mtime_before = self.status_path.stat().st_mtime_ns
        second = self._reconcile()
        self.assertEqual(first, second)
        self.assertEqual(self.status_path.stat().st_mtime_ns, mtime_before)  # no rewrite

    def test_reconciliation_is_idempotent_across_repeated_starts(self):
        seed = CAMPAIGN_SEEDS[0]
        _write_world_bank(self.checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
        for g in (0, 1, 2):
            _write_generation(self.checkpoint_dir, self.gen_path, 0, seed, g)
        # STATUS.json missing every time -- simulates repeated instance
        # replacement with no surviving local state at all.
        results = []
        for _ in range(3):
            self.status_path.unlink(missing_ok=True)
            results.append(self._reconcile())
        for r in results[1:]:
            self.assertEqual({k: v for k, v in r.items() if k not in ("updated_utc", "last_progress_utc")},
                              {k: v for k, v in results[0].items() if k not in ("updated_utc", "last_progress_utc")})


def _stale_status(*, world_bank_id, synthetic_world_count, campaign_seed, generation,
                   campaigns_complete, protocol_manifest=ACTIVE_MANIFEST_ID):
    return {
        "schema": "tbots-status-v1", "updated_utc": "2026-01-01T00:00:00Z",
        "head_commit_parent": "0" * 40, "state": "running", "phase": "campaign",
        "protocol_manifest": protocol_manifest, "freeze_commit": "0" * 40,
        "audit_window_ends_utc": "2020-01-01T00:00:00Z",
        "world_bank_id": world_bank_id, "synthetic_world_count": synthetic_world_count,
        "campaign": None, "campaign_seed": campaign_seed, "generation": generation,
        "campaigns_complete": campaigns_complete, "rank1_qualifiers": 0,
        "research_champion": None, "negative_result": False,
        "last_progress_utc": "2026-01-01T00:00:00Z", "stop_code": None, "stop_reason": None,
        "recoveries": 0, "last_generation_seconds": None,
        "disk_used_bytes": 0, "disk_free_bytes": 999_999_999_999,
    }


class RealAcceptedArtifactsReconstruction(unittest.TestCase):
    """Integration-level: the actual accepted evolution/state/world_bank_v1.json
    plus real generation 0/1/2 checkpoints from this NODE's own accepted
    2026-09-29 burn-in, exactly as the GO asked ("use the existing accepted
    world-bank/generation artifacts as realistic fixtures where
    practical")."""

    @classmethod
    def setUpClass(cls):
        if not REAL_WORLD_BANK.exists() or not all(p.exists() for p in REAL_GEN_PATHS):
            raise unittest.SkipTest("real accepted burn-in checkpoints not present on this checkout")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.checkpoint_dir = Path(cls._tmp.name)
        shutil.copy2(REAL_WORLD_BANK, cls.checkpoint_dir / REAL_WORLD_BANK.name)
        for p in REAL_GEN_PATHS:
            shutil.copy2(p, cls.checkpoint_dir / p.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _reconcile(self, status_path):
        return reconcile_status(
            repo_root=self.checkpoint_dir.parent, status_path=status_path,
            checkpoint_dir=self.checkpoint_dir, campaign_seeds=CAMPAIGN_SEEDS,
            final_generation=FINAL_GENERATION, expansion_batch_id=_expansion_batch_id,
            generation_checkpoint_path=lambda b, s, g: self.checkpoint_dir / f"batch{b}_campaign_{s}_generation_{g}.json",
            nominee_checkpoint_path=lambda b, s: self.checkpoint_dir / f"batch{b}_campaign_{s}_nominee.json",
            active_protocol_manifest_id=lambda: ACTIVE_MANIFEST_ID, now_utc=NOW,
        )

    def test_real_checkpoints_reconstruct_campaign_seed_and_generation_2(self):
        status_path = self.checkpoint_dir.parent / "STATUS_a.json"
        status = self._reconcile(status_path)
        self.assertEqual(status["world_bank_id"], "world_bank_v1")
        self.assertEqual(status["synthetic_world_count"], 16)
        self.assertEqual(status["campaign_seed"], 2066557696)
        self.assertEqual(status["generation"], 2)
        self.assertEqual(status["campaigns_complete"], 0)

    def test_real_checkpoints_resume_point_is_generation_3(self):
        status_path = self.checkpoint_dir.parent / "STATUS_b.json"
        status = self._reconcile(status_path)
        resume_from, population, used_ids, final_ranked = _resume_campaign_state(status, 0, 2066557696)
        self.assertEqual(resume_from, 3)
        real_gen2 = json.loads(REAL_GEN_PATHS[2].read_text())
        self.assertEqual(sorted(used_ids), sorted(real_gen2["used_genome_ids"]))
        self.assertEqual(population, real_gen2["next_population"])

    def test_real_checkpoints_reconstruction_never_rebuilds_the_world_bank(self):
        status_path = self.checkpoint_dir.parent / "STATUS_c.json"
        before = (self.checkpoint_dir / "world_bank_v1.json").stat().st_mtime_ns
        before_bytes = (self.checkpoint_dir / "world_bank_v1.json").read_bytes()
        self._reconcile(status_path)
        after = (self.checkpoint_dir / "world_bank_v1.json").stat().st_mtime_ns
        self.assertEqual(before, after)
        self.assertEqual(before_bytes, (self.checkpoint_dir / "world_bank_v1.json").read_bytes())

    def test_real_checkpoints_no_generation_duplicated_no_seed_reused_after_reconstruction(self):
        status_path = self.checkpoint_dir.parent / "STATUS_d.json"
        status = self._reconcile(status_path)
        # The three real, already-accepted generation checkpoints' used_genome_ids
        # accumulate strictly monotonically (this was independently proven live
        # during the accepted burn-in itself) -- reconstruction must not lose or
        # duplicate any of that lineage.
        ids_by_gen = [set(json.loads(p.read_text())["used_genome_ids"]) for p in REAL_GEN_PATHS]
        for earlier, later in zip(ids_by_gen, ids_by_gen[1:]):
            self.assertTrue(earlier <= later)
        resume_from, population, used_ids, _ = _resume_campaign_state(status, 0, 2066557696)
        # The reconstructed resume state's used_genome_ids must be exactly
        # generation 2's (the latest durable checkpoint) -- not generation 0's
        # or 1's, and not the union recomputed some other way.
        self.assertEqual(used_ids, ids_by_gen[2])
        self.assertNotEqual(resume_from, 0)
        self.assertNotEqual(resume_from, 1)


if __name__ == "__main__":
    unittest.main()
