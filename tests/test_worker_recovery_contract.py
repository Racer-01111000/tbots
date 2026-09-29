from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fitness_v2 import FitnessV2Error
from worker_recovery_contract import (
    RECOVERY_CONTRACT, RECOVERY_CONTRACT_ID, validate_recovery_contract,
)


class RecoveryContractTests(unittest.TestCase):
    def test_content_address_round_trips_against_the_persisted_envelope(self):
        envelope = json.loads((ROOT / "evolution/protocol" / (RECOVERY_CONTRACT_ID + ".json")).read_text())
        self.assertEqual(validate_recovery_contract(envelope), RECOVERY_CONTRACT_ID)

    def test_tampering_is_detected(self):
        envelope = {"manifest_id": RECOVERY_CONTRACT_ID, "content": copy.deepcopy(RECOVERY_CONTRACT)}
        envelope["content"]["audit_window_ends_utc"] = "2099-01-01T00:00:00Z"
        with self.assertRaises(FitnessV2Error):
            validate_recovery_contract(envelope)

    def test_values_match_what_status_json_has_shown_since_the_original_freeze(self):
        # These three fields have never changed across any with_updates()
        # call in fitness_v2_worker.py -- verified by reading every call
        # site during the 2026-09-29 migration inventory and again while
        # building this recovery contract.
        self.assertEqual(RECOVERY_CONTRACT["audit_window_ends_utc"], "2026-09-26T12:29:05Z")
        self.assertEqual(RECOVERY_CONTRACT["head_commit_parent"], "bb7cbf9e1e8969426a6f78bdc1022ada4eabea12")
        self.assertEqual(RECOVERY_CONTRACT["freeze_commit"], "1c3b6b013c4a47dbaf74a8a9ef5cda09f43afcd2")


if __name__ == "__main__":
    unittest.main()
