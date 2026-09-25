from __future__ import annotations

import math
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fitness_v2 as fv2


def metrics(*, ret=0.05, sharpe=0.5, drawdown=-0.01, orders=2,
            turnover=0.03, halt=0.0, cost=0.0):
    return {
        "median_return": ret,
        "median_sharpe": sharpe,
        "worst_drawdown": drawdown,
        "halt_rate": halt,
        "median_turnover": turnover,
        "median_transaction_cost_rate": cost,
        "order_count": orders,
    }


def candidate_rows(*, sharpe=0.5, passive=0.2, ret=0.05, turnover=0.03,
                   anomalous=()):
    rows = []
    for index in range(3):
        rows.append({
            "world_id": f"H{index + 1}",
            "family": "historical",
            "metrics": metrics(ret=ret, sharpe=sharpe, turnover=turnover),
            "exposure_matched_passive_sharpe": passive,
            "passive_comparator_anomalous": "historical" in anomalous,
        })
    for family in fv2.SYNTHETIC_FAMILIES:
        rows.append({
            "world_id": family,
            "family": family,
            "metrics": metrics(ret=ret, sharpe=sharpe, turnover=turnover),
            "exposure_matched_passive_sharpe": passive,
            "passive_comparator_anomalous": family in anomalous,
        })
    return rows


class ExplodingSharpe(dict):
    def __getitem__(self, key):
        if key == "median_sharpe":
            raise AssertionError("Sharpe was evaluated after inactivity short-circuit")
        return super().__getitem__(key)


class PerWorldContractTests(unittest.TestCase):
    def test_no_trade_short_circuits_before_sharpe(self):
        value = fv2.per_world_score(ExplodingSharpe(order_count=0))
        self.assertEqual(value["score"], 0.0)
        self.assertTrue(value["short_circuited_inactive"])

    def test_negative_return_is_not_clipped_away(self):
        row = metrics(ret=-0.05, sharpe=0.0, drawdown=-0.06, turnover=0.0)
        value = fv2.per_world_score(row)
        self.assertEqual(value["scaling_factor"], 1.0)
        self.assertAlmostEqual(value["return_term"], -0.15)
        row["median_return"] = -0.002
        row["worst_drawdown"] = -0.001
        self.assertAlmostEqual(fv2.per_world_score(row)["return_term"], -0.006)

    def test_k3_return_drawdown_scaling(self):
        value = fv2.per_world_score(metrics(ret=0.25, sharpe=0, drawdown=-0.11, turnover=0))
        self.assertAlmostEqual(value["scaling_factor"], 0.25 / 0.331, places=12)
        full = fv2.per_world_score(metrics(ret=0.20, sharpe=0, drawdown=-0.03, turnover=0))
        self.assertEqual(full["scaling_factor"], 1.0)

    def test_family_arithmetic_and_weights(self):
        self.assertAlmostEqual(fv2.family_score([0.05, -0.02, 0.08]), -0.001666666666666667)
        self.assertAlmostEqual(sum(fv2.FAMILY_WEIGHTS.values()), 1.0)

    def test_nonfinite_and_invalid_ranges_fail_closed(self):
        with self.assertRaises(fv2.FitnessV2Error):
            fv2.per_world_score(metrics(ret=math.nan))
        with self.assertRaises(fv2.FitnessV2Error):
            fv2.per_world_score(metrics(drawdown=0.01))


class BoundaryTests(unittest.TestCase):
    def _manifest(self):
        calibration = {
            "lane": "DEVELOPMENT",
            "start": "2007-01-01",
            "end": "2018-12-31",
        }
        worlds = [
            {
                "world_id": f"H{index + 1}",
                "family": "historical",
                "provenance_class": "REAL_DEVELOPMENT",
                "calibration_source": calibration,
            }
            for index in range(3)
        ]
        for family in fv2.SYNTHETIC_FAMILIES:
            for seed in (1, 2, 3):
                worlds.append({
                    "world_id": f"{family}-{seed}",
                    "family": family,
                    "provenance_class": "SYNTHETIC_DEVELOPMENT",
                    "calibration_source": calibration,
                    "technique": f"fixed-{family}",
                    "deterministic_seed": seed,
                })
        manifest = {
            "schema_version": 1,
            "calibration_source": calibration,
            "worlds": worlds,
        }
        manifest["manifest_id"] = fv2.content_identity("fitness_v2_world_bank_", manifest)
        return manifest

    def test_exact_content_addressed_minimum_bank_is_accepted(self):
        manifest = self._manifest()
        self.assertEqual(fv2.validate_world_manifest(manifest), manifest["manifest_id"])

    def test_protected_period_calibration_is_rejected(self):
        manifest = self._manifest()
        manifest["calibration_source"] = {
            "lane": "DEVELOPMENT", "start": "2007-01-01", "end": "2026-08-25"
        }
        with self.assertRaisesRegex(fv2.FitnessV2Error, "exactly DEVELOPMENT"):
            fv2.validate_world_manifest(manifest)

    def test_design_range_cannot_masquerade_as_frozen_bank(self):
        with self.assertRaisesRegex(fv2.FitnessV2Error, "exact non-empty"):
            fv2.validate_world_manifest({
                "schema_version": 1,
                "calibration_source": {
                    "lane": "DEVELOPMENT", "start": "2007-01-01", "end": "2018-12-31"
                },
                "synthetic_world_count": "12-16",
            })

    def test_minimum_three_worlds_and_two_seeds_per_technique_are_enforced(self):
        manifest = self._manifest()
        manifest["worlds"] = [
            row for row in manifest["worlds"]
            if not (row["family"] == "shock" and row.get("deterministic_seed") == 3)
        ]
        unsigned = dict(manifest)
        unsigned.pop("manifest_id")
        manifest["manifest_id"] = fv2.content_identity("fitness_v2_world_bank_", unsigned)
        with self.assertRaisesRegex(fv2.FitnessV2Error, "at least three"):
            fv2.validate_world_manifest(manifest)

    def test_exact_rick_parameter_freeze_is_content_addressed_and_accepted(self):
        path = ROOT / fv2.PARAMETER_FREEZE_PATH
        with path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(fv2.validate_parameter_freeze(manifest), fv2.PARAMETER_FREEZE_ID)

    def test_parameter_freeze_tampering_fails_closed(self):
        path = ROOT / fv2.PARAMETER_FREEZE_PATH
        with path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["campaign_batch"]["evolution_seeds"][0] += 1
        with self.assertRaisesRegex(fv2.FitnessV2Error, "identity mismatch"):
            fv2.validate_parameter_freeze(manifest)


class SurvivalGateTests(unittest.TestCase):
    def test_legitimate_defensive_inactivity_in_one_world_is_not_structural(self):
        rows = candidate_rows()
        rows[-1]["metrics"] = metrics(orders=0, turnover=0)
        result = fv2.evaluate_candidate(rows)
        self.assertFalse(result["structurally_inactive"])

    def test_token_trades_do_not_evade_structural_inactivity(self):
        rows = candidate_rows(turnover=5e-7)
        result = fv2.evaluate_candidate(rows)
        self.assertEqual(result["activity_rate"], 0.0)
        self.assertTrue(result["structurally_inactive"])
        self.assertFalse(result["eligible"])

    def test_genuinely_modest_trade_counts_as_active(self):
        result = fv2.evaluate_candidate(candidate_rows(turnover=0.03))
        self.assertEqual(result["activity_rate"], 1.0)
        self.assertFalse(result["structurally_inactive"])

    def test_inactive_in_all_historical_worlds_is_rejected(self):
        rows = candidate_rows()
        for row in rows[:3]:
            row["metrics"] = metrics(orders=0, turnover=0)
        result = fv2.evaluate_candidate(rows)
        self.assertTrue(result["structurally_inactive"])

    def test_historical_and_synthetic_floors_are_nonnegotiable(self):
        rows = candidate_rows()
        rows[0]["metrics"] = metrics(ret=-0.10, sharpe=0, drawdown=-0.10)
        result = fv2.evaluate_candidate(rows)
        self.assertFalse(result["historical_floor"])
        self.assertFalse(result["eligible"])

    def test_oc2_requires_all_three_distinct_margins(self):
        rows = candidate_rows(sharpe=0.5, passive=0.53, ret=0.05)
        result = fv2.evaluate_candidate(rows)
        self.assertTrue(result["passive_parity_or_worse"])
        self.assertTrue(result["oc2_eligible"])
        self.assertTrue(result["eligible"])

        low_historical = candidate_rows(sharpe=0.5, passive=0.53, ret=0.05)
        low_historical[0]["metrics"] = metrics(ret=0.005, sharpe=0.5)
        self.assertFalse(fv2.evaluate_candidate(low_historical)["oc2_eligible"])

        low_activity = candidate_rows(sharpe=0.5, passive=0.53, ret=0.05)
        for row in low_activity[-2:]:
            row["metrics"] = metrics(orders=0, turnover=0)
        low_activity_result = fv2.evaluate_candidate(low_activity)
        self.assertGreaterEqual(low_activity_result["activity_rate"], 0.50)
        self.assertLess(low_activity_result["activity_rate"], 0.75)
        self.assertFalse(low_activity_result["oc2_eligible"])

    def test_two_negative_family_drivers_cannot_use_oc1(self):
        rows = candidate_rows(sharpe=0.5, passive=0.5)
        for row in rows:
            if row["family"] in {"sequence", "shock"}:
                row["exposure_matched_passive_sharpe"] = 1.5
                row["passive_comparator_anomalous"] = True
        result = fv2.evaluate_candidate(rows)
        self.assertTrue(result["passive_parity_or_worse"])
        self.assertFalse(result["oc1_eligible"])


class AdmissionTests(unittest.TestCase):
    def test_rejected_rank_one_is_not_replaced_by_sibling(self):
        rejected = {"cross_family_fitness": 2.0, "eligible": False}
        acceptable = {"cross_family_fitness": 1.0, "eligible": True}
        nominee = fv2.nominate_campaign([
            {"genome_id": "rank1", "evaluation": rejected},
            {"genome_id": "rank2", "evaluation": acceptable},
        ])
        self.assertEqual(nominee["genome_id"], "rank1")
        self.assertFalse(nominee["clears_all_gates"])
        self.assertFalse(nominee["sibling_substitution_permitted"])

    def test_correct_majority_table_n1_through_n10(self):
        for n in range(1, 11):
            required = n // 2 + 1
            nominees = [
                {
                    "genome_id": f"g{i}",
                    "evolution_seed": i,
                    "clears_all_gates": i < required,
                }
                for i in range(n)
            ]
            decision = fv2.decide_admission(nominees, predeclared_n=n)
            self.assertEqual(decision["required_count"], required)
            self.assertTrue(decision["admitted"])
            if required > 1:
                nominees[required - 1]["clears_all_gates"] = False
                self.assertFalse(fv2.decide_admission(nominees, predeclared_n=n)["admitted"])

    def test_five_campaign_rule_and_lowest_seed_selection(self):
        nominees = [
            {"genome_id": f"g{seed}", "evolution_seed": seed, "clears_all_gates": seed in {9, 3, 5}}
            for seed in (9, 3, 5, 7, 11)
        ]
        decision = fv2.decide_admission(nominees, predeclared_n=5)
        self.assertEqual(decision["ratio"], "3/5")
        self.assertTrue(decision["admitted"])
        self.assertEqual(decision["selected_genome_id"], "g3")

    def test_campaign_count_cannot_expand_after_results(self):
        nominees = [
            {"genome_id": "g1", "evolution_seed": 1, "clears_all_gates": False},
            {"genome_id": "g2", "evolution_seed": 2, "clears_all_gates": False},
            {"genome_id": "g3", "evolution_seed": 3, "clears_all_gates": False},
            {"genome_id": "late", "evolution_seed": 4, "clears_all_gates": True},
        ]
        with self.assertRaisesRegex(fv2.FitnessV2Error, "predeclared N"):
            fv2.decide_admission(nominees, predeclared_n=3)


if __name__ == "__main__":
    unittest.main()
