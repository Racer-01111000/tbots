from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from fitness_v2_oc1_reference import (
    PASSIVE_ENVELOPE,
    PASSIVE_ENVELOPE_ID,
    OC1ReferenceError,
    validate_passive_envelope,
)


class FrozenEnvelope(unittest.TestCase):
    def test_matches_the_pre_fitness_v2_passive_envelope_control_construction(self):
        # experiments/d_exposure_isolation_20260923T052542Z/PREDECLARATION.md:
        # PASSIVE_ENVELOPE_CONTROL is 80% total exposure, max_asset_weight 0.35.
        self.assertEqual(PASSIVE_ENVELOPE["target_max_exposure"], 0.80)
        self.assertEqual(PASSIVE_ENVELOPE["max_asset_weight"], 0.35)

    def test_manifest_is_exactly_two_fields(self):
        # simulate_passive_comparator only ever reads target_max_exposure;
        # max_asset_weight is carried for documentation/provenance parity
        # with the historical PASSIVE_ENVELOPE_CONTROL, not consulted.
        self.assertEqual(set(PASSIVE_ENVELOPE), {"target_max_exposure", "max_asset_weight"})


class ContentIdentity(unittest.TestCase):
    def test_validate_accepts_the_exact_frozen_envelope(self):
        validate_passive_envelope({"manifest_id": PASSIVE_ENVELOPE_ID, "content": PASSIVE_ENVELOPE})

    def test_validate_rejects_a_drifted_envelope(self):
        drifted = {**PASSIVE_ENVELOPE, "target_max_exposure": 0.5}
        with self.assertRaises(OC1ReferenceError):
            validate_passive_envelope({"manifest_id": PASSIVE_ENVELOPE_ID, "content": drifted})

    def test_validate_rejects_a_stale_manifest_id(self):
        with self.assertRaises(OC1ReferenceError):
            validate_passive_envelope({"manifest_id": "stale", "content": PASSIVE_ENVELOPE})


if __name__ == "__main__":
    unittest.main()
