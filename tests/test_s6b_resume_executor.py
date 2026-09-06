"""Synthetic-only proof for the S6B deterministic resume executor.

These tests use temporary directories and SyntheticEvaluator only. They never
read or mutate the preserved historical S6B trees and never execute historical
market data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import s6a_final as p
import s6b_continuation as cont
import s6b_resume_executor as resume
import s6b_runner as runner
from test_s6b_continuation import truncate_after
from test_s6b_orchestration import SyntheticEvaluator, synthetic_availability


def target(root: Path, kind: str, code: str) -> Path:
    return root / kind / f"{code}_{p.RUN_IDS[code]}"


def build(root: Path, code: str, availability: dict, kind: str = "primary") -> None:
    runner.run_lineage(
        code, root, availability, SyntheticEvaluator(), SyntheticEvaluator(),
        output_kind=kind,
    )


def hashes(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*")) if item.is_file()
    }


class ResumeExecutorFixture(unittest.TestCase):
    code = "E"

    @classmethod
    def setUpClass(cls):
        cls.availability = synthetic_availability()
        cls.reference_temp = tempfile.TemporaryDirectory()
        cls.reference_root = Path(cls.reference_temp.name)
        build(cls.reference_root, cls.code, cls.availability)
        cls.reference_target = target(cls.reference_root, "primary", cls.code)
        cls.reference_hashes = hashes(cls.reference_target)

    @classmethod
    def tearDownClass(cls):
        cls.reference_temp.cleanup()

    def interrupted_copy(self, keep_through: int) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copytree(self.reference_root, root, dirs_exist_ok=True)
        truncate_after(target(root, "primary", self.code), keep_through)
        return temp, root


class ResumeExactnessTests(ResumeExecutorFixture):
    def test_resume_gen12_from_gen11_reproduces_uninterrupted_tree_byte_for_byte(self):
        _temp, root = self.interrupted_copy(11)
        result = resume.resume_to_completion(
            root, "primary", self.code, self.availability,
            SyntheticEvaluator(), SyntheticEvaluator(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["generations_committed_this_invocation"], [12])
        self.assertEqual(hashes(target(root, "primary", self.code)), self.reference_hashes)

    def test_resume_from_gen6_reproduces_uninterrupted_tree_byte_for_byte(self):
        _temp, root = self.interrupted_copy(6)
        result = resume.resume_to_completion(
            root, "primary", self.code, self.availability,
            SyntheticEvaluator(), SyntheticEvaluator(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["generations_committed_this_invocation"], list(range(7, 13)))
        self.assertEqual(hashes(target(root, "primary", self.code)), self.reference_hashes)

    def test_resume_one_generation_advances_exactly_one_generation(self):
        _temp, root = self.interrupted_copy(6)
        result = resume.resume_one_generation(
            root, "primary", self.code, self.availability,
            SyntheticEvaluator(), SyntheticEvaluator(),
        )
        self.assertEqual(result["generation"], 7)
        self.assertEqual(result["highest_generation"], 7)
        self.assertEqual(result["next_generation"], 8)
        self.assertFalse((target(root, "primary", self.code) / "generations" / "gen_08.json").exists())


class ResumeProtectionTests(ResumeExecutorFixture):
    def test_complete_run_is_never_overwritten(self):
        before = dict(self.reference_hashes)
        with self.assertRaises(cont.CompleteRunProtected):
            resume.resume_one_generation(
                self.reference_root, "primary", self.code, self.availability,
                SyntheticEvaluator(), SyntheticEvaluator(),
            )
        self.assertEqual(hashes(self.reference_target), before)

    def test_no_existing_run_is_never_created(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(cont.NoExistingRun):
                resume.resume_one_generation(
                    root, "primary", "F", self.availability,
                    SyntheticEvaluator(), SyntheticEvaluator(),
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_population_checkpoint_requires_explicit_execution_gate(self):
        _temp, root = self.interrupted_copy(6)
        run_target = target(root, "primary", self.code)
        metadata_path = run_target / "run_metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["execution_class"] = "population"
        metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        before = hashes(run_target)
        with self.assertRaises(resume.ResumeExecutionLocked):
            resume.resume_one_generation(
                root, "primary", self.code, self.availability,
                SyntheticEvaluator(), SyntheticEvaluator(),
            )
        self.assertEqual(hashes(run_target), before)

    def test_verifier_must_be_independent(self):
        _temp, root = self.interrupted_copy(11)
        evaluator = SyntheticEvaluator()
        with self.assertRaises(runner.S6BError):
            resume.resume_one_generation(
                root, "primary", self.code, self.availability,
                evaluator, evaluator,
            )


class ResumeCrashRecoveryTests(ResumeExecutorFixture):
    def test_crash_between_feasibility_and_generation_commit_recovers_same_transaction(self):
        _temp, root = self.interrupted_copy(3)
        run_target = target(root, "primary", self.code)
        real_link = os.link
        calls = {"count": 0}

        def crash_on_second_link(source, destination):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("synthetic crash after first committed artifact")
            return real_link(source, destination)

        with mock.patch.object(resume.os, "link", side_effect=crash_on_second_link):
            with self.assertRaises(RuntimeError):
                resume.resume_one_generation(
                    root, "primary", self.code, self.availability,
                    SyntheticEvaluator(), SyntheticEvaluator(),
                )

        self.assertTrue((run_target / resume.TRANSACTION_FILE).is_file())
        self.assertTrue((run_target / "feasibility" / "gen_04.json").is_file())
        self.assertFalse((run_target / "generations" / "gen_04.json").exists())

        recovered = resume.resume_one_generation(
            root, "primary", self.code, self.availability,
            SyntheticEvaluator(), SyntheticEvaluator(),
        )
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["generation"], 4)
        self.assertEqual(recovered["highest_generation"], 4)
        self.assertEqual(recovered["next_generation"], 5)
        self.assertFalse((run_target / resume.TRANSACTION_FILE).exists())
        self.assertEqual(
            hashlib.sha256((run_target / "generations" / "gen_04.json").read_bytes()).hexdigest(),
            self.reference_hashes["generations/gen_04.json"],
        )

    def test_orphan_precommit_stage_is_cleaned_without_advancing_checkpoint(self):
        _temp, root = self.interrupted_copy(5)
        run_target = target(root, "primary", self.code)
        stage = run_target / f"{resume.STAGE_PREFIX}06"
        stage.mkdir()
        (stage / "junk").write_text("not committed")
        recovered = resume.recover_pending_transaction(run_target)
        self.assertIsNone(recovered)
        self.assertFalse(stage.exists())
        state = cont.inspect_checkpoint(root, "primary", self.code, self.availability)
        self.assertEqual(state.highest_generation, 5)
        self.assertEqual(state.next_generation, 6)


if __name__ == "__main__":
    unittest.main()
