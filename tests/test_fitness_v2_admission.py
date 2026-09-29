from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fitness_v2 import FitnessV2Error
from fitness_v2_admission import market_distance_passes, unchanged_h1_warmup_coverage


class MarketDistanceAmendmentTests(unittest.TestCase):
    def test_execution_distance_only_exemption(self):
        self.assertTrue(market_distance_passes("execution", [0.0], [[0.0]]))

    def test_shock_distance_threshold_exemption(self):
        # Shock is exempt from the >=0.75 threshold only (Rick, 2026-09-29) --
        # unlike Execution, it still requires valid descriptors (see
        # test_invalid_descriptors_fail_closed_for_market_families).
        self.assertTrue(market_distance_passes("shock", [0.0], [[0.0]]))
        self.assertTrue(market_distance_passes("shock", [0.749999], [[0.0]]))
        self.assertTrue(market_distance_passes("shock", [0.75], [[0.0]]))
        self.assertTrue(market_distance_passes("shock", [1.0], [[0.0], [1.0]]))

    def test_other_families_keep_exact_threshold(self):
        for family in ("distributional", "sequence"):
            with self.subTest(family=family):
                self.assertFalse(market_distance_passes(family, [0.0], [[0.0]]))
                self.assertFalse(market_distance_passes(family, [0.749999], [[0.0]]))
                self.assertTrue(market_distance_passes(family, [0.75], [[0.0]]))

    def test_all_references_remain_binding(self):
        for family in ("distributional", "sequence"):
            self.assertFalse(market_distance_passes(family, [1.0], [[0.0], [1.0]]))

    def test_exemption_does_not_accept_unknown_family_or_historical_replacement(self):
        for family in ("Execution", "execution_stress", "historical", "", None):
            with self.assertRaises(FitnessV2Error):
                market_distance_passes(family, [0.0], [[0.0]])

    def test_anchor_inputs_are_immutable(self):
        anchors = [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]
        before = copy.deepcopy(anchors)
        for family in ("execution", "distributional", "sequence", "shock"):
            market_distance_passes(family, [0.0, 1.0], anchors)
        self.assertEqual(anchors, before)

    def test_invalid_descriptors_fail_closed_for_market_families(self):
        for family in ("distributional", "sequence", "shock"):
            for candidate, references in (([], [[0]]), ([0], []), ([float("nan")], [[0]]),
                                          ([0], [[0, 1]]), ([True], [[0]])):
                with self.assertRaises(FitnessV2Error):
                    market_distance_passes(family, candidate, references)

    def test_existing_source_proves_h1_warmup_shortfall_without_generation(self):
        proof = unchanged_h1_warmup_coverage()
        self.assertEqual(proof["required_synchronized_prior_bars"], 378)
        self.assertEqual(proof["per_asset_prior_bars_including_predevelopment"]["DBC"], 252)
        self.assertEqual(proof["common_prior_bars_including_predevelopment"], 252)
        self.assertLess(proof["common_prior_bars_from_2007_01_01"], 378)
        self.assertEqual(proof["worlds_generated"], 0)
        self.assertEqual(proof["genomes_generated"], 0)
        self.assertEqual(proof["isolation"]["observations_after_2018_12_31_available_to_evaluator"], 0)


if __name__ == "__main__":
    unittest.main()
