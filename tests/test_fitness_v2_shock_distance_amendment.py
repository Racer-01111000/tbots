from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fitness_v2 import FitnessV2Error
from fitness_v2_admission import market_distance_passes
from fitness_v2_shock_distance_amendment import (
    AMENDMENT_ID, SHOCK_DISTANCE_AMENDMENT, validate_amendment,
)
from fitness_v2_warmup import AMENDMENT_ID as WARMUP_AMENDMENT_ID


class ShockDistanceAmendmentTests(unittest.TestCase):
    def test_content_address_round_trips(self):
        envelope = json.loads((ROOT / "evolution/protocol" / (AMENDMENT_ID + ".json")).read_text())
        self.assertEqual(validate_amendment(envelope), AMENDMENT_ID)

    def test_tampering_is_detected(self):
        envelope = {"manifest_id": AMENDMENT_ID, "content": copy.deepcopy(SHOCK_DISTANCE_AMENDMENT)}
        envelope["content"]["rule"]["not_exempt"] = []
        with self.assertRaises(FitnessV2Error):
            validate_amendment(envelope)

    def test_extends_the_already_resolved_warmup_amendment(self):
        self.assertEqual(SHOCK_DISTANCE_AMENDMENT["extends_warmup_amendment"], WARMUP_AMENDMENT_ID)
        self.assertIsNone(SHOCK_DISTANCE_AMENDMENT["supersedes"])

    def test_rationale_records_the_pre_result_empirical_basis(self):
        rationale = SHOCK_DISTANCE_AMENDMENT["rationale"]
        self.assertIn("533+", rationale["empirical_evidence"])
        self.assertIn("0 admissions", rationale["empirical_evidence"])
        self.assertTrue(rationale["identified_pre_result"])

    def test_no_tuning_or_retroactive_acceptance(self):
        self.assertTrue(SHOCK_DISTANCE_AMENDMENT["no_threshold_tuning"])
        self.assertTrue(SHOCK_DISTANCE_AMENDMENT["no_retroactive_acceptance_of_prior_shock_attempts"])

    def test_declared_rule_matches_live_predicate_behavior(self):
        # The amendment's own declared rule must match what market_distance_passes
        # actually does -- distributional/sequence unaffected, shock exempt from
        # the threshold but still descriptor-validated.
        self.assertEqual(SHOCK_DISTANCE_AMENDMENT["rule"]["not_exempt"], ["distributional", "sequence"])
        for family in SHOCK_DISTANCE_AMENDMENT["rule"]["not_exempt"]:
            self.assertFalse(market_distance_passes(family, [0.0], [[0.0]]))
        self.assertTrue(market_distance_passes("shock", [0.0], [[0.0]]))
        with self.assertRaises(FitnessV2Error):
            market_distance_passes("shock", [], [[0.0]])


if __name__ == "__main__":
    unittest.main()
