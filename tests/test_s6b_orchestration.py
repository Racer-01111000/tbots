"""Synthetic-only S6B orchestration, persistence, and fail-closed tests."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import s6a_final as p
import s6a_runtime as r
import s6b_runner as runner
from lib.ids import canonical_json
from s5a_config import EPISODE_PROTOCOL


def synthetic_availability() -> dict:
    episodes = [{
        "episode_index": row["episode_index"],
        "first_session": row["start_date"],
        "available_price_bars": {symbol: 253 for symbol in p.UNIVERSE},
    } for row in EPISODE_PROTOCOL["episodes"]]
    return {
        "schema_version": 1,
        "dataset_revision": p.DATASET,
        "development_start": p.HISTORY["development"]["start"],
        "development_end": p.HISTORY["development"]["end"],
        "source_bundle_latest": p.HISTORY["development"]["end"],
        "episode_count": len(episodes),
        "episodes": episodes,
        "minimum_price_bars_by_asset": {symbol: 253 for symbol in p.UNIVERSE},
        "minimum_price_bars": 253,
        "latest_observation_used_for_feasibility": episodes[-1]["first_session"],
    }


class SyntheticEvaluator:
    """Fast deterministic fixture that delegates aggregate math to frozen S6A."""

    def __init__(self):
        self.calls = []

    def __call__(self, code: str, genome: dict,
                 admission: r.CandidateAdmission) -> dict:
        self.calls.append((code, admission.genome_id))
        raw = int(admission.genome_id[-8:], 16)
        base = ((raw % 2001) - 1000) / 100000.0
        episodes = []
        for index, episode in enumerate(EPISODE_PROTOCOL["episodes"]):
            total_return = base + (index - 5.5) / 10000.0
            episodes.append({
                "episode_index": episode["episode_index"],
                "start_date": episode["start_date"],
                "end_date": episode["end_date"],
                "total_return": total_return,
                "sharpe": total_return * 10,
                "max_drawdown": -abs(total_return) / 2,
                "halted": False,
                "turnover": abs(total_return),
                "transaction_cost_rate": abs(total_return) / 100,
            })
        aggregate = r.compute_fitness(
            episodes, len(episodes), admission=admission,
        )
        return {
            "episode_metrics": episodes,
            "aggregate": aggregate,
            "metrics_hash": hashlib.sha256(canonical_json({
                "episodes": episodes, "aggregate": aggregate,
            }).encode()).hexdigest(),
        }


class S6BCompleteSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.availability = synthetic_availability()
        cls.evaluator = SyntheticEvaluator()
        cls.verifier = SyntheticEvaluator()
        cls.outcome = runner.run_and_reproduce_lineage(
            "E", cls.root, cls.availability, cls.evaluator, cls.verifier,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def generation(self, kind: str, generation: int) -> dict:
        path = (
            self.root / kind / f"E_{p.RUN_IDS['E']}" /
            "generations" / f"gen_{generation:02d}.json"
        )
        return json.loads(path.read_text())

    def feasibility(self, kind: str, generation: int) -> dict:
        path = (
            self.root / kind / f"E_{p.RUN_IDS['E']}" /
            "feasibility" / f"gen_{generation:02d}.json"
        )
        return json.loads(path.read_text())

    def test_complete_gen0_through_gen12_and_exact_population_size(self):
        for kind in ("primary", "reproduction"):
            generations = [self.generation(kind, generation) for generation in range(13)]
            self.assertEqual([row["generation"] for row in generations], list(range(13)))
            self.assertTrue(all(len(row["membership"]) == 64 for row in generations))
            self.assertTrue(all(
                member["admission"]["code"] == "E"
                for row in generations for member in row["membership"]
            ))
            self.assertTrue(all(
                not member["verified"]
                for row in generations[:-1] for member in row["membership"]
            ))
            self.assertTrue(all(
                member["verified"] for member in generations[-1]["membership"]
            ))
        self.assertEqual(len(self.evaluator.calls), 2 * 13 * 64)
        self.assertEqual(len(self.verifier.calls), 2 * 64)

    def test_deterministic_feasibility_resampling_and_generation_identities(self):
        primary_audits = [self.feasibility("primary", generation) for generation in range(13)]
        reproduced_audits = [
            self.feasibility("reproduction", generation) for generation in range(13)
        ]
        self.assertEqual(primary_audits, reproduced_audits)
        self.assertGreater(sum(row["rejected_total"] for row in primary_audits), 0)
        primary_ids = [
            self.generation("primary", generation)["generation_identity"]
            for generation in range(13)
        ]
        reproduced_ids = [
            self.generation("reproduction", generation)["generation_identity"]
            for generation in range(13)
        ]
        self.assertEqual(primary_ids, reproduced_ids)
        self.assertEqual(len(set(primary_ids)), 13)

    def test_same_lineage_parent_provenance(self):
        previous = {
            row["genome_id"] for row in self.generation("primary", 0)["membership"]
        }
        for generation in range(1, 13):
            current = self.generation("primary", generation)["membership"]
            for row in current:
                if row["role"] == "immigrant":
                    self.assertIsNone(row["parent_genome_id"])
                else:
                    self.assertIn(row["parent_genome_id"], previous)
                    self.assertEqual(row["lineage"], "E")
            previous = {row["genome_id"] for row in current}

    def test_top_eight_is_unique_verified_gen12_advancement(self):
        frozen = self.outcome["primary"]["development_top_eight"]
        self.assertEqual(len(frozen), 8)
        self.assertEqual(len({row["genome_id"] for row in frozen}), 8)
        self.assertTrue(all(row["generation"] == 12 and row["verified"] for row in frozen))

    def test_reproduction_is_byte_and_semantically_equal(self):
        self.assertEqual(self.outcome["status"], "agreed")
        self.assertEqual(
            self.outcome["primary"]["deterministic_digest"],
            self.outcome["reproduction"]["deterministic_digest"],
        )
        self.assertEqual(
            self.outcome["primary"]["tree_digest"],
            self.outcome["reproduction"]["tree_digest"],
        )

    def test_output_overwrite_protection(self):
        with self.assertRaises(runner.PersistenceCollision):
            runner.run_lineage(
                "E", self.root, self.availability,
                SyntheticEvaluator(), SyntheticEvaluator(),
            )


class S6BFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.availability = synthetic_availability()

    def feasible_genome(self, code: str = "E") -> dict:
        for seed in range(1000):
            genome = r.founder(code, r.derive_seed(code, "s6b-test", seed))
            try:
                r.require_development_feasible(
                    code, genome, availability=self.availability,
                )
                return genome
            except r.FeasibilityError:
                continue
        self.fail("could not construct a feasible fixture")

    def result(self, code: str = "E", genome: dict | None = None) -> tuple:
        genome = genome or self.feasible_genome(code)
        admission = runner.admit_candidate(code, genome, self.availability)
        return admission, SyntheticEvaluator()(code, genome, admission)

    def test_frozen_s6a_identity_verification(self):
        identities = runner.assert_frozen_identities()
        self.assertEqual(identities["evolution"], runner.EXPECTED_EVOLUTION)
        self.assertEqual(identities["plan"], runner.EXPECTED_PLAN)
        self.assertEqual(identities["completion_lock"], runner.EXPECTED_COMPLETION_LOCK)
        self.assertEqual(identities["run_ids"], runner.EXPECTED_RUN_IDS)

    def test_infeasible_candidate_never_reaches_evaluator(self):
        genome = r.founder("E", r.derive_seed("E", "infeasible-s6b"))
        genome.update({
            "fast_trend_sessions": 90,
            "slow_trend_sessions": 300,
            "regime_volatility_window": 90,
        })
        r.validate_genome("E", genome)
        evaluator = SyntheticEvaluator()
        with self.assertRaises(r.FeasibilityError):
            runner.evaluate_candidate("E", genome, self.availability, evaluator)
        self.assertEqual(evaluator.calls, [])

    def test_invalid_admission_token_never_reaches_evaluator(self):
        genome = self.feasible_genome()
        admission = runner.admit_candidate("E", genome, self.availability)
        invalid = replace(admission, code="B")
        evaluator = SyntheticEvaluator()
        with self.assertRaises(r.FeasibilityError):
            runner.evaluate_candidate(
                "E", genome, self.availability, evaluator, admission=invalid,
            )
        self.assertEqual(evaluator.calls, [])

    def test_cross_lineage_population_is_rejected(self):
        members = r.build_gen0("E", availability=self.availability)
        members[0] = {**members[0], "lineage": "F"}
        with self.assertRaises(r.IsolationError):
            runner.validate_population("E", members, 0, self.availability)

    def test_cross_lineage_parent_is_rejected(self):
        current = r.build_gen0("E", availability=self.availability)
        evaluated = [{**row, "fitness": index} for index, row in enumerate(current)]
        next_generation = r.build_generation(
            "E", evaluated, 1, availability=self.availability,
        )
        previous = {row["genome_id"] for row in current}
        victim = next(row for row in next_generation if row["role"] == "child")
        victim["parent_genome_id"] = "gen_cross_lineage"
        with self.assertRaises(r.IsolationError):
            runner.validate_population(
                "E", next_generation, 1, self.availability, previous,
            )

    def test_trader_a_is_rejected(self):
        members = r.build_gen0("E", availability=self.availability)
        trader_a = members[0]["genome_id"]
        with mock.patch.object(p, "TRADER_A", trader_a):
            with self.assertRaises(r.IsolationError):
                runner.validate_population("E", members, 0, self.availability)

    def test_independent_verifier_disagreement_fails_closed(self):
        admission, primary = self.result()
        verified = copy.deepcopy(primary)
        verified["aggregate"]["fitness"] += 1
        verified["metrics_hash"] = hashlib.sha256(canonical_json({
            "episodes": verified["episode_metrics"],
            "aggregate": verified["aggregate"],
        }).encode()).hexdigest()
        with self.assertRaises(runner.EvaluatorVerifierDisagreement):
            runner.assert_evaluator_verifier_agreement(primary, verified)
        self.assertIsInstance(admission, r.CandidateAdmission)

    def test_maximum_two_lineage_concurrency_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(runner.S6BError):
                runner.run_lineages(
                    ["B", "C", "D"], Path(temp), self.availability,
                    lambda unused: SyntheticEvaluator(),
                    lambda unused: SyntheticEvaluator(),
                    max_concurrency=3,
                )

    def test_mock_scheduler_never_exceeds_two_active_lineages(self):
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def fake_run(code, *args, **kwargs):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.02)
            with lock:
                state["active"] -= 1
            return {"lineage": code}

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(runner, "run_lineage", side_effect=fake_run):
                result = runner.run_lineages(
                    list("BCDEFG"), Path(temp), self.availability,
                    lambda unused: SyntheticEvaluator(),
                    lambda unused: SyntheticEvaluator(),
                    max_concurrency=2,
                )
        self.assertEqual(state["maximum"], 2)
        self.assertEqual(list(result), list("BCDEFG"))

    def test_2019_plus_development_access_is_rejected(self):
        bad = copy.deepcopy(self.availability)
        bad["source_bundle_latest"] = "2019-01-02"
        with self.assertRaises(r.BoundaryError):
            runner.validate_development_availability(bad)
        bad = copy.deepcopy(self.availability)
        bad["episodes"][0]["first_session"] = "2019-01-02"
        with self.assertRaises(r.BoundaryError):
            runner.validate_development_availability(bad)

    def test_2026_plus_access_is_rejected(self):
        bad = copy.deepcopy(self.availability)
        bad["source_bundle_latest"] = "2026-01-01"
        bad["latest_observation_used_for_feasibility"] = "2026-01-01"
        with self.assertRaises(r.BoundaryError):
            runner.validate_development_availability(bad)
        with self.assertRaises(r.BoundaryError):
            r.authorize_lane("2026")

    def test_real_population_adapter_remains_locked(self):
        with self.assertRaises(runner.PopulationExecutionLocked):
            runner.historical_evaluator(None, self.availability)

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(runner.PopulationExecutionLocked):
                runner.run_lineage(
                    "E", Path(temp), self.availability,
                    SyntheticEvaluator(), SyntheticEvaluator(),
                    execution_class="population",
                )
            self.assertEqual(list(Path(temp).iterdir()), [])
if __name__ == "__main__":

    unittest.main()
