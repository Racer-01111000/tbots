from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import statistics
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fitness_v2 import FitnessV2Error, content_identity
import fitness_v2_formula_definitions as f


class ExactFormulaTests(unittest.TestCase):
    def test_increasing_decreasing_loglinear_and_constant(self):
        for sign in (-1, 1):
            self.assertAlmostEqual(f.trend_strength([math.exp(sign*i/100) for i in range(63)]), 1.0)
        self.assertEqual(f.trend_strength([17.0]*63), 0.0)

    def test_no_incomplete_contributions(self):
        self.assertIsNone(f.trend_strength([1.0]*62))
        self.assertEqual(len(f.trend_window_strengths([1.0]*64)), 2)

    def test_invalid_prices_even_in_incomplete_history_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(FitnessV2Error):
                f.trend_strength([1.0]*62+[value])
            with self.assertRaises(FitnessV2Error):
                f.trend_strength([value])

    def test_independent_window_formula_and_exact_per_asset_median(self):
        # Unit input only, never a production world or candidate.
        prices = [math.exp(math.sin(i/9)+i/300) for i in range(90)]
        before = list(prices)
        expected = []
        for start in range(28):
            y = [math.log(p) for p in prices[start:start+63]]
            sx, sy = sum(range(63)), sum(y)
            numerator = 63*sum(i*v for i, v in enumerate(y))-sx*sy
            denominator = math.sqrt((63*sum(i*i for i in range(63))-sx*sx)*(63*sum(v*v for v in y)-sy*sy))
            expected.append(abs(numerator/denominator))
        self.assertAlmostEqual(f.trend_strength(prices), statistics.median(expected), places=12)
        self.assertEqual(prices, before)
        self.assertEqual(f.trend_strength([3.0]*90), 0.0)

    def test_all_signed_zero_mad_sentinels_and_booleans(self):
        result = f.family_oc1([-1.0, 0.0, 1.0], [0.0]*5)
        self.assertEqual(result["world_z"], [-4.0, 0.0, 4.0])
        self.assertEqual(result["zero_mad_world_anomalous"], [True, False, True])
        self.assertEqual(result["family_median_z"], 0.0)
        self.assertFalse(result["passive_comparator_anomalous"])

    def test_ordinary_family_median_and_one_sided_threshold(self):
        self.assertTrue(f.family_oc1([1,1,1,-1], [0]*5)["passive_comparator_anomalous"])
        self.assertFalse(f.family_oc1([-1,-1,-1,1], [0]*5)["passive_comparator_anomalous"])
        result = f.family_oc1([-1,0,1,1], [0]*5)
        self.assertEqual(result["family_median_z"], 2.0)
        self.assertFalse(result["passive_comparator_anomalous"])
        for z, expected in ((3.0, False), (3.01, True), (-4.0, False)):
            result = f.family_oc1([z*1.4826]*4, [-1,0,1])
            self.assertAlmostEqual(result["family_median_z"], z)
            self.assertEqual(result["passive_comparator_anomalous"], expected)

    def test_nonzero_mad_formula(self):
        self.assertAlmostEqual(f.passive_robust_z(7, 1, 2), 6/(1.4826*2))
        with self.assertRaises(FitnessV2Error):
            f.passive_robust_z(1,0,-1)

    def test_source_boundary_excludes_anchor_only_earlier_history(self):
        f.validate_development_source(["2007-02-07","2018-12-31"])
        for earlier in ("2006-02-06", "2007-01-01", "2007-02-06"):
            with self.assertRaises(FitnessV2Error):
                f.validate_development_source([earlier,"2007-02-07"])
        with self.assertRaises(FitnessV2Error):
            f.validate_development_source(["2007-02-07","2019-01-01"])

    def test_content_identity_tampering_and_no_readiness_claim(self):
        path = ROOT / "evolution/protocol" / (f.DEFINITIONS_ID+".json")
        envelope = json.loads(path.read_text())
        self.assertEqual(f.validate_definitions(envelope), content_identity("fitness_v2_formula_definitions_",envelope["content"]))
        self.assertFalse(envelope["content"]["resolution_status"]["complete_protocol_ready"])
        changed = copy.deepcopy(envelope)
        changed["content"]["source_boundary"]["generation_and_calibration"][0] = "2007-01-01"
        with self.assertRaises(FitnessV2Error):
            f.validate_definitions(changed)


class ScientificConservationProofTests(unittest.TestCase):
    def test_variance_convention_can_change_distance_admission(self):
        # Descriptor-level algebra only: 9 equal volatility components, other
        # 23 equal. World/anchor each have 1000 returns, reference windows251.
        reference = [.01,.02,.03,.04,.05]
        anchor = .03
        candidate = .03+.01*1.414
        mad = statistics.median(abs(x-statistics.median(reference)) for x in reference)
        sample_delta = (candidate-anchor)/mad
        population_delta = (candidate-anchor)*math.sqrt(999/1000)/(mad*math.sqrt(250/251))
        sample_distance = math.sqrt(9*sample_delta**2/32)
        population_distance = math.sqrt(9*population_delta**2/32)
        self.assertAlmostEqual(sample_distance, .7498867414483336)
        self.assertAlmostEqual(population_distance, .7510092316706231)
        self.assertLess(sample_distance, .75)
        self.assertGreater(population_distance, .75)


if __name__ == "__main__":
    unittest.main()
