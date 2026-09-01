import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import s5_boundary as s5


class LaneBoundaryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_manifest(self, content, *, preserve_claimed_hash=False):
        manifest_hash = s5.content_hash(content)
        envelope = {"content": content, "manifest_hash": manifest_hash}
        path = self.temp_root / f"test_{manifest_hash}.json"
        path.write_text(json.dumps(envelope))
        return path

    def test_lane_manifest_hashes_are_deterministic_and_content_addressed(self):
        for manifest_hash in (s5.DEVELOPMENT_HASH, s5.QUALIFICATION_HASH):
            first = s5.load_authorized_manifest(manifest_hash)
            second = s5.load_authorized_manifest(manifest_hash)
            envelope = json.loads(s5.AUTHORIZED_PATHS[manifest_hash].read_text())
            self.assertEqual(first, second)
            self.assertEqual(s5.content_hash(envelope["content"]), manifest_hash)
            self.assertIn(manifest_hash, s5.AUTHORIZED_PATHS[manifest_hash].name)

    def test_development_bounds_are_exact(self):
        plan = s5.build_control_evaluation_plan(s5.DEVELOPMENT_HASH)
        self.assertEqual(plan.manifest.lane, "DEVELOPMENT")
        self.assertEqual(plan.manifest.start_date, "2007-02-07")
        self.assertEqual(plan.manifest.end_date, "2018-12-31")
        self.assertEqual(plan.retention_end_date, "2018-12-31")
        self.assertTrue(plan.evolutionary_feedback_allowed)

    def test_qualification_bounds_are_exact_and_feedback_is_disabled(self):
        plan = s5.build_control_evaluation_plan(s5.QUALIFICATION_HASH)
        self.assertEqual(plan.manifest.lane, "QUALIFICATION")
        self.assertEqual(plan.manifest.start_date, "2019-01-01")
        self.assertEqual(plan.manifest.end_date, "2022-12-31")
        self.assertEqual(plan.retention_end_date, "2022-12-31")
        self.assertFalse(plan.evolutionary_feedback_allowed)

    def test_arbitrary_interval_is_rejected_even_if_it_matches_a_lane(self):
        with self.assertRaisesRegex(s5.LaneBoundaryError, "arbitrary"):
            s5.build_control_evaluation_plan(
                s5.DEVELOPMENT_HASH,
                requested_interval=("2007-02-07", "2018-12-31"),
            )

    def test_championship_manifest_is_rejected_as_sealed(self):
        content = dict(s5.AUTHORIZED_CONTENT[s5.DEVELOPMENT_HASH])
        content.update(lane="CHAMPIONSHIP", start_date="2023-01-01",
                       end_date="2025-12-31", sealed=True)
        with self.assertRaisesRegex(s5.SealedLaneError, "CHAMPIONSHIP"):
            s5.load_authorized_manifest(self._write_manifest(content))

    def test_final_reserve_manifest_is_rejected_as_sealed(self):
        content = dict(s5.AUTHORIZED_CONTENT[s5.DEVELOPMENT_HASH])
        content.update(lane="FINAL_RESERVE", start_date="2026-01-01",
                       end_date=s5.DATASET_END, sealed=True)
        with self.assertRaisesRegex(s5.SealedLaneError, "FINAL_RESERVE"):
            s5.load_authorized_manifest(self._write_manifest(content))

    def test_tainted_manifest_with_stale_hash_is_rejected(self):
        envelope = json.loads(s5.AUTHORIZED_PATHS[s5.DEVELOPMENT_HASH].read_text())
        envelope["content"]["end_date"] = "2019-01-02"
        path = self.temp_root / s5.AUTHORIZED_PATHS[s5.DEVELOPMENT_HASH].name
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(s5.ManifestIntegrityError, "hash mismatch"):
            s5.load_authorized_manifest(path)

    def test_tainted_self_consistent_manifest_is_not_authorized(self):
        content = dict(s5.AUTHORIZED_CONTENT[s5.DEVELOPMENT_HASH])
        content["start_date"] = "2008-01-01"
        with self.assertRaisesRegex(s5.ManifestIntegrityError, "not an authorized"):
            s5.load_authorized_manifest(self._write_manifest(content))

    def test_historical_result_identifiers_are_rejected(self):
        for identifier in (
            "exp_legacy", "epi_legacy", "result:legacy", "report-old-s4",
        ):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(s5.ContaminatedFitnessInput, "identifiers"):
                    s5.build_control_evaluation_plan(
                        s5.DEVELOPMENT_HASH,
                        prior_result_identifiers=[identifier],
                    )

    def test_historical_s4_performance_payload_is_rejected(self):
        tainted = {
            "performance": {"total_return": 9.9},
            "equity_curve": [1, 2, 3],
            "decisions": ["dec_old"],
            "orders": ["ord_old"],
            "fills": ["fil_old"],
            "final_metrics": {"sharpe": 99},
        }
        with self.assertRaisesRegex(s5.ContaminatedFitnessInput, "external performance"):
            s5.build_control_evaluation_plan(s5.DEVELOPMENT_HASH, fitness_input=tainted)

    def test_qualification_feedback_input_is_rejected(self):
        with self.assertRaisesRegex(s5.ContaminatedFitnessInput, "qualification results"):
            s5.build_control_evaluation_plan(
                s5.DEVELOPMENT_HASH,
                qualification_feedback={"winner": "agent-old"},
            )

    def test_control_genome_definition_is_the_only_anchor(self):
        plan = s5.build_control_evaluation_plan(s5.DEVELOPMENT_HASH)
        self.assertEqual(plan.genome_id, s5.CONTROL_GENOME_ID)
        self.assertEqual(plan.genome_source, "S4_CONTROL_GENOME_DEFINITION")
        self.assertTrue(plan.fresh_evaluation)

    def test_development_control_is_freshly_evaluated_at_manifest_bounds(self):
        fresh = {
            "final_equity_cents": 101,
            "total_return": 0.01,
            "max_drawdown": -0.02,
        }
        with mock.patch.object(s5, "run_control_episode", return_value=fresh) as runner:
            evaluation = s5.evaluate_control_lane(s5.DEVELOPMENT_HASH)
        runner.assert_called_once_with(
            s5.ROOT,
            s5.DATASET_REVISION,
            "2007-02-07",
            "2018-12-31",
            masked_time=True,
            write_equity_curve=False,
            verify_every_rebalance=True,
            data_access_end="2018-12-31",
        )
        self.assertEqual(evaluation.fresh_result, fresh)

    def test_fresh_development_metrics_can_be_whitelisted_as_fitness(self):
        plan = s5.build_control_evaluation_plan(s5.DEVELOPMENT_HASH)
        evaluation = s5.LaneEvaluationResult(
            plan,
            {
                "final_equity_cents": 101,
                "total_return": 0.01,
                "max_drawdown": -0.02,
                "experiment_id": "exp_fresh_output_not_used_as_fitness",
            },
        )
        self.assertEqual(
            s5.extract_evolutionary_fitness(evaluation),
            {"final_equity_cents": 101, "total_return": 0.01, "max_drawdown": -0.02},
        )

    def test_qualification_result_cannot_feed_evolutionary_fitness(self):
        plan = s5.build_control_evaluation_plan(s5.QUALIFICATION_HASH)
        evaluation = s5.LaneEvaluationResult(
            plan,
            {"final_equity_cents": 999, "total_return": 9.99, "max_drawdown": 0.0},
        )
        with self.assertRaisesRegex(s5.ContaminatedFitnessInput, "DEVELOPMENT"):
            s5.extract_evolutionary_fitness(evaluation)


if __name__ == "__main__":
    unittest.main()
