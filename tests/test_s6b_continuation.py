"""Synthetic-only tests for the S6B deterministic continuation surface.

Every fixture here is a synthetic in-tempdir checkpoint built with the
SyntheticEvaluator fixture already used by test_s6b_orchestration -- no
historical organism, historical price data, or real S6 population is ever
touched. Nothing under /opt/evolutionary-markets/ is read or written.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import s6a_final as p
import s6a_runtime as r
import s6b_continuation as cont
import s6b_runner as runner
from lib.ids import canonical_json
from test_s6b_orchestration import SyntheticEvaluator, synthetic_availability  # noqa: E402 (sibling test module)


def build_full_lineage(root: Path, code: str, availability: dict, output_kind: str = "primary"):
    return runner.run_lineage(
        code, root, availability, SyntheticEvaluator(), SyntheticEvaluator(),
        output_kind=output_kind,
    )


def checkpoint_target(root: Path, output_kind: str, code: str) -> Path:
    return Path(root) / output_kind / f"{code}_{p.RUN_IDS[code]}"


def truncate_after(target: Path, keep_through: int) -> None:
    """Delete everything persisted for generations after ``keep_through``,
    plus any tail artifacts that only exist once generation 12 completed --
    simulating an interruption partway through a real run."""
    for sub in ("generations", "feasibility"):
        directory = target / sub
        for path in sorted(directory.glob("gen_*.json")):
            index = int(path.stem.split("_")[1])
            if index > keep_through:
                path.unlink()
    for name in ("completion.json", "gen12_verification.json", "development_top_eight.json"):
        candidate = target / name
        if candidate.is_file():
            candidate.unlink()


def tree_hashes(target: Path) -> dict:
    return {
        str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(target.rglob("*")) if path.is_file()
    }


def load_record(target: Path, generation: int) -> dict:
    path = target / "generations" / f"gen_{generation:02d}.json"
    return json.loads(path.read_text())


def save_record(target: Path, generation: int, record: dict) -> None:
    path = target / "generations" / f"gen_{generation:02d}.json"
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")


def resign(record: dict) -> dict:
    """Recompute generation_identity to match (possibly tampered) content,
    isolating one specific downstream check per test instead of tripping
    the blanket digest-mismatch check."""
    record = copy.deepcopy(record)
    payload = {
        "schema_version": record["schema_version"],
        "lineage": record["lineage"],
        "run_id": record["run_id"],
        "generation": record["generation"],
        "membership": record["membership"],
    }
    record["generation_identity"] = "s6b_generation_" + runner._digest(payload)
    return record


def bare(rows: list) -> list:
    return [
        {
            "role": row["role"], "lineage": row["lineage"], "genome": row["genome"],
            "genome_id": row["genome_id"], "parent_genome_id": row["parent_genome_id"],
        }
        for row in rows
    ]


class ContinuationFixtureBase(unittest.TestCase):
    """One full reference lineage built once per class; each test copies it."""
    code = "E"

    @classmethod
    def setUpClass(cls):
        cls.availability = synthetic_availability()
        cls.template_temp = tempfile.TemporaryDirectory()
        cls.template_root = Path(cls.template_temp.name)
        cls.template_result = build_full_lineage(cls.template_root, cls.code, cls.availability)
        cls.template_target = checkpoint_target(cls.template_root, "primary", cls.code)

    @classmethod
    def tearDownClass(cls):
        cls.template_temp.cleanup()

    def working_copy(self, keep_through: int | None = None) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copytree(self.template_root, root, dirs_exist_ok=True)
        target = checkpoint_target(root, "primary", self.code)
        if keep_through is not None:
            truncate_after(target, keep_through)
        return root


class S6BContinuationNoExistingRunTests(unittest.TestCase):
    def setUp(self):
        self.availability = synthetic_availability()

    def test_absent_directory_is_no_existing_run_not_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            state = cont.inspect_checkpoint(temp, "primary", "F", self.availability)
        self.assertEqual(state.status, cont.STATUS_NO_EXISTING_RUN)
        self.assertIsNone(state.highest_generation)
        self.assertIsNone(state.next_generation)

    def test_absence_does_not_authorize_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            state = cont.inspect_checkpoint(temp, "primary", "G", self.availability)
            with self.assertRaises(cont.NoExistingRun):
                cont.preview_next_generation(state, self.availability)
            self.assertEqual(list(Path(temp).iterdir()), [])


class S6BContinuationResumeTests(ContinuationFixtureBase):
    def test_valid_resume_reproduces_the_original_truncated_generation(self):
        original = bare(load_record(self.template_target, 4)["membership"])
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.status, cont.STATUS_INTERRUPTED)
        self.assertEqual(state.highest_generation, 3)
        self.assertEqual(state.next_generation, 4)
        preview = cont.preview_next_generation(state, self.availability)
        self.assertEqual(preview, original)
        self.assertFalse((target / "generations" / "gen_04.json").exists())

    def test_deterministic_next_generation_identity(self):
        root_a = self.working_copy(keep_through=6)
        root_b = self.working_copy(keep_through=6)
        state_a = cont.inspect_checkpoint(root_a, "primary", self.code, self.availability)
        state_b = cont.inspect_checkpoint(root_b, "primary", self.code, self.availability)
        preview_a = cont.preview_next_generation(state_a, self.availability)
        preview_b = cont.preview_next_generation(state_b, self.availability)
        self.assertEqual(canonical_json(preview_a), canonical_json(preview_b))
        direct = r.build_generation(
            self.code, state_a.last_generation_members, 7,
            availability=self.availability, audit=r.new_feasibility_audit(),
        )
        self.assertEqual(canonical_json(preview_a), canonical_json(bare(direct)))

    def test_gen0_only_checkpoint_resumes_to_gen1(self):
        root = self.working_copy(keep_through=0)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.highest_generation, 0)
        self.assertEqual(state.next_generation, 1)
        preview = cont.preview_next_generation(state, self.availability)
        self.assertEqual(len(preview), 64)

    def test_different_lineage_produces_a_different_next_generation(self):
        d_root_temp = tempfile.TemporaryDirectory()
        self.addCleanup(d_root_temp.cleanup)
        d_root = Path(d_root_temp.name)
        build_full_lineage(d_root, "D", self.availability)
        truncate_after(checkpoint_target(d_root, "primary", "D"), keep_through=2)

        e_root = self.working_copy(keep_through=2)
        state_e = cont.inspect_checkpoint(e_root, "primary", "E", self.availability)
        state_d = cont.inspect_checkpoint(d_root, "primary", "D", self.availability)
        preview_e = cont.preview_next_generation(state_e, self.availability)
        preview_d = cont.preview_next_generation(state_d, self.availability)
        self.assertNotEqual(
            {row["genome_id"] for row in preview_e},
            {row["genome_id"] for row in preview_d},
        )

    def test_preserves_checkpoint_bytes_exactly(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        before = tree_hashes(target)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        cont.preview_next_generation(state, self.availability)
        after = tree_hashes(target)
        self.assertEqual(before, after)

    def test_no_evaluator_or_decision_function_ever_runs(self):
        root = self.working_copy(keep_through=5)

        def poison(*_args, **_kwargs):
            raise AssertionError("a trading decision function executed during continuation prep")

        with mock.patch.object(r, "decide_E", side_effect=poison), \
             mock.patch.object(r, "compute_fitness", side_effect=poison):
            state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
            preview = cont.preview_next_generation(state, self.availability)
        self.assertEqual(len(preview), 64)
        for row in preview:
            self.assertEqual(set(row), {"role", "lineage", "genome", "genome_id", "parent_genome_id"})


class S6BContinuationCompletedRunProtectionTests(ContinuationFixtureBase):
    def test_complete_run_status_and_resume_rejection(self):
        root = self.working_copy()
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.status, cont.STATUS_COMPLETE)
        self.assertEqual(state.highest_generation, 12)
        self.assertIsNone(state.next_generation)
        with self.assertRaises(cont.CompleteRunProtected):
            cont.preview_next_generation(state, self.availability)

    def test_complete_run_still_collides_on_naive_rerun(self):
        root = self.working_copy()
        cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        with self.assertRaises(runner.PersistenceCollision):
            runner.run_lineage(
                self.code, root, self.availability,
                SyntheticEvaluator(), SyntheticEvaluator(),
            )

    def test_interrupted_checkpoint_still_collides_on_naive_rerun(self):
        root = self.working_copy(keep_through=4)
        cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        with self.assertRaises(runner.PersistenceCollision):
            runner.run_lineage(
                self.code, root, self.availability,
                SyntheticEvaluator(), SyntheticEvaluator(),
            )

    def test_generation_already_exists_is_refused_directly(self):
        root = self.working_copy(keep_through=6)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        tampered_state = cont.CheckpointState(
            **{**state.__dict__, "next_generation": 6},
        )
        with self.assertRaises(cont.GenerationAlreadyExists):
            cont.preview_next_generation(tampered_state, self.availability)


class S6BContinuationFailedRunTests(ContinuationFixtureBase):
    def test_failure_marker_blocks_continuation(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        (target / "failure.json").write_text(json.dumps({
            "status": "failed_closed", "lineage": self.code,
            "error_type": "SimulatedOutage", "error": "synthetic test failure marker",
        }))
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.status, cont.STATUS_FAILED)
        with self.assertRaises(cont.FailedRunNeedsReview):
            cont.preview_next_generation(state, self.availability)


class S6BContinuationFailClosedTests(ContinuationFixtureBase):
    def test_wrong_lineage_in_metadata_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        metadata_path = target / "run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["lineage"] = "D"
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(cont.WrongLineage):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_wrong_lineage_in_generation_record_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        record["lineage"] = "D"
        save_record(target, 2, resign(record))
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_wrong_run_identity_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        metadata_path = target / "run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["run_id"] = "s6a_e_0000000000000000000000000000000000000000000000000000000000000000"
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(cont.WrongRunIdentity):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_wrong_frozen_protocol_identity_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        metadata_path = target / "run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["frozen_identities"]["evolution"] = "s6a_evolution_" + "0" * 64
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(runner.IdentityMismatch):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_generation_gap_is_rejected(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        (target / "generations" / "gen_04.json").unlink()
        with self.assertRaises(cont.GenerationGap):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_partial_generation_membership_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        record["membership"] = record["membership"][:40]
        save_record(target, 2, record)
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_population_cardinality_overflow_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        extra = copy.deepcopy(record["membership"][0])
        record["membership"].append(extra)
        save_record(target, 2, record)
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_invalid_parent_provenance_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        victim = next(row for row in record["membership"] if row["role"] == "child")
        victim["parent_genome_id"] = "s6a_e_" + "f" * 64
        save_record(target, 2, resign(record))
        with self.assertRaises(r.IsolationError):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_inconsistent_stored_fitness_state_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        record["membership"][0]["aggregate"]["fitness"] += 1.0
        save_record(target, 2, resign(record))
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_corrupted_json_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        (target / "generations" / "gen_02.json").write_text("{not valid json")
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_generation_identity_tamper_without_resign_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        record = load_record(target, 2)
        record["membership"][0]["genome_id"] = record["membership"][0]["genome_id"][:-1] + (
            "0" if record["membership"][0]["genome_id"][-1] != "0" else "1"
        )
        save_record(target, 2, record)
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_completion_without_gen12_is_rejected(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        (target / "completion.json").write_text(json.dumps({
            "status": "completed", "lineage": self.code, "run_id": p.RUN_IDS[self.code],
            "generation_count": 13,
        }))
        with self.assertRaises(cont.CorruptedCheckpoint):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)


class S6BLegacyCompatibilityProofTests(unittest.TestCase):
    """Tests on the compatibility-proof mechanism itself, independent of any
    checkpoint fixture: it must prove exactly one documented field rename
    relates the two identities, and fail closed on anything else."""

    def setUp(self):
        self.current_content = r.completion_lock_content()

    def test_current_content_hashes_to_known_current_identity(self):
        self.assertEqual(
            cont._completion_lock_id(self.current_content), cont.KNOWN_CURRENT_COMPLETION_LOCK,
        )

    def test_legacy_transform_changes_only_the_one_documented_field(self):
        legacy = cont._legacy_shape(self.current_content)
        common_keys = set(self.current_content) - {cont._CURRENT_TELEMETRY_FIELD}
        self.assertEqual(common_keys, set(legacy) - {cont._LEGACY_TELEMETRY_FIELD})
        for key in common_keys:
            self.assertEqual(self.current_content[key], legacy[key])
        self.assertNotIn(cont._CURRENT_TELEMETRY_FIELD, legacy)
        self.assertEqual(legacy[cont._LEGACY_TELEMETRY_FIELD], 0)

    def test_legacy_transform_reproduces_known_legacy_identity(self):
        legacy = cont._legacy_shape(self.current_content)
        self.assertEqual(cont._completion_lock_id(legacy), cont.KNOWN_LEGACY_COMPLETION_LOCK)

    def test_verify_legacy_compatibility_proof_never_claims_equality(self):
        proof = cont.verify_legacy_compatibility()
        self.assertEqual(proof["legacy_completion_lock"], cont.KNOWN_LEGACY_COMPLETION_LOCK)
        self.assertEqual(proof["current_completion_lock"], cont.KNOWN_CURRENT_COMPLETION_LOCK)
        self.assertNotEqual(proof["legacy_completion_lock"], proof["current_completion_lock"])
        self.assertIn(cont._LEGACY_TELEMETRY_FIELD, proof["transform"])
        self.assertIn(cont._CURRENT_TELEMETRY_FIELD, proof["transform"])

    def test_nonzero_current_field_is_rejected(self):
        tampered = {**self.current_content, cont._CURRENT_TELEMETRY_FIELD: 1}
        with self.assertRaises(cont.ContinuationError):
            cont._legacy_shape(tampered)

    def test_nonzero_legacy_field_breaks_the_known_identity(self):
        corrupted = dict(self.current_content)
        del corrupted[cont._CURRENT_TELEMETRY_FIELD]
        corrupted[cont._LEGACY_TELEMETRY_FIELD] = 1
        self.assertNotEqual(cont._completion_lock_id(corrupted), cont.KNOWN_LEGACY_COMPLETION_LOCK)

    def test_second_renamed_field_breaks_the_known_identity(self):
        legacy = cont._legacy_shape(self.current_content)
        tampered = dict(legacy)
        tampered["phase"] = "RENAMED_PHASE"
        self.assertNotEqual(cont._completion_lock_id(tampered), cont.KNOWN_LEGACY_COMPLETION_LOCK)

    def test_changed_unrelated_value_breaks_the_known_identity(self):
        legacy = cont._legacy_shape(self.current_content)
        tampered = dict(legacy)
        tampered["plan_hash"] = "s6a_plan_" + "0" * 64
        self.assertNotEqual(cont._completion_lock_id(tampered), cont.KNOWN_LEGACY_COMPLETION_LOCK)

    def test_missing_field_breaks_the_known_identity(self):
        legacy = cont._legacy_shape(self.current_content)
        tampered = dict(legacy)
        del tampered["probe_manifest_hash"]
        self.assertNotEqual(cont._completion_lock_id(tampered), cont.KNOWN_LEGACY_COMPLETION_LOCK)

    def test_additional_field_breaks_the_known_identity(self):
        legacy = cont._legacy_shape(self.current_content)
        tampered = dict(legacy)
        tampered["extra_field"] = 0
        self.assertNotEqual(cont._completion_lock_id(tampered), cont.KNOWN_LEGACY_COMPLETION_LOCK)


class S6BLegacyProfileCheckpointTests(ContinuationFixtureBase):
    """Checkpoint-level classification: CURRENT_TBOTS vs LEGACY_NODE_74ACB366
    vs fail closed, exercised against synthetic fixtures whose stored
    frozen_identities are edited to simulate a legacy-produced checkpoint --
    never against real preserved evidence, and never mutating anything but
    the synthetic fixture's own run_metadata.json."""

    def stamp_completion_lock(self, target: Path, completion_lock: str,
                              extra: dict | None = None) -> None:
        metadata_path = target / "run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["frozen_identities"]["completion_lock"] = completion_lock
        if extra:
            metadata["frozen_identities"].update(extra)
        metadata_path.write_text(json.dumps(metadata))

    def test_current_tbots_envelope_is_classified_current(self):
        root = self.working_copy(keep_through=3)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.identity_profile, cont.PROFILE_CURRENT_TBOTS)

    def test_legacy_envelope_is_classified_legacy_only_through_compatibility_proof(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        self.stamp_completion_lock(target, cont.KNOWN_LEGACY_COMPLETION_LOCK)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.status, cont.STATUS_INTERRUPTED)
        self.assertEqual(state.identity_profile, cont.PROFILE_LEGACY_NODE)
        self.assertEqual(state.next_generation, 4)

    def test_unknown_completion_lock_hash_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        self.stamp_completion_lock(target, "s6a_completion_lock_" + "9" * 64)
        with self.assertRaises(runner.IdentityMismatch):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_legacy_completion_lock_with_unrelated_field_changed_is_rejected(self):
        root = self.working_copy(keep_through=3)
        target = checkpoint_target(root, "primary", self.code)
        self.stamp_completion_lock(
            target, cont.KNOWN_LEGACY_COMPLETION_LOCK,
            extra={"plan": "s6a_plan_" + "0" * 64},
        )
        with self.assertRaises(runner.IdentityMismatch):
            cont.inspect_checkpoint(root, "primary", self.code, self.availability)

    def test_legacy_checkpoint_bytes_remain_untouched(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        self.stamp_completion_lock(target, cont.KNOWN_LEGACY_COMPLETION_LOCK)
        before = tree_hashes(target)
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        cont.preview_next_generation(state, self.availability)
        after = tree_hashes(target)
        self.assertEqual(before, after)

    def test_no_evaluator_or_decision_function_runs_for_legacy_profile(self):
        root = self.working_copy(keep_through=5)
        target = checkpoint_target(root, "primary", self.code)
        self.stamp_completion_lock(target, cont.KNOWN_LEGACY_COMPLETION_LOCK)

        def poison(*_args, **_kwargs):
            raise AssertionError("a trading decision function executed during legacy inspection")

        with mock.patch.object(r, "decide_E", side_effect=poison), \
             mock.patch.object(r, "compute_fitness", side_effect=poison):
            state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
            preview = cont.preview_next_generation(state, self.availability)
        self.assertEqual(state.identity_profile, cont.PROFILE_LEGACY_NODE)
        self.assertEqual(len(preview), 64)

    def test_current_synthetic_continuation_behavior_is_still_deterministic(self):
        root_a = self.working_copy(keep_through=6)
        root_b = self.working_copy(keep_through=6)
        state_a = cont.inspect_checkpoint(root_a, "primary", self.code, self.availability)
        state_b = cont.inspect_checkpoint(root_b, "primary", self.code, self.availability)
        self.assertEqual(state_a.identity_profile, cont.PROFILE_CURRENT_TBOTS)
        preview_a = cont.preview_next_generation(state_a, self.availability)
        preview_b = cont.preview_next_generation(state_b, self.availability)
        self.assertEqual(canonical_json(preview_a), canonical_json(preview_b))


if __name__ == "__main__":
    unittest.main()
