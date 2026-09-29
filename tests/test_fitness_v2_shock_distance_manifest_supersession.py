from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fitness_v2 import content_identity
from fitness_v2_protocol import validate_complete_protocol
from fitness_v2_world_bank import COMPLETE_PROTOCOL_PATH, load_complete_protocol

OLD_MANIFEST_PATH = (
    ROOT / "evolution" / "protocol" / "fitness_v2_complete_protocol_"
    "49c71da11d46f56838a560481350cb3436af3177bab6cb13254f9c84860c6e89.json"
)
NEW_MANIFEST_PATH = (
    ROOT / "evolution" / "protocol" / "fitness_v2_complete_protocol_"
    "a65b600736075a5766be1bd8f7b87a80ce0f74529ef4178acc71b30308f3c4ea.json"
)

# Fields intentionally changed by the 2026-09-29 Shock market-distance
# amendment; every other field must be byte-identical between manifests.
CHANGED_FIELDS = {"authority", "depends_on", "schema_version"}


class ShockDistanceManifestSupersessionTests(unittest.TestCase):
    def test_old_manifest_is_untouched_and_still_self_consistent(self):
        old = json.loads(OLD_MANIFEST_PATH.read_text())
        actual = content_identity("fitness_v2_complete_protocol_", old["content"])
        self.assertEqual(old["manifest_id"], actual)
        self.assertEqual(
            old["manifest_id"],
            "fitness_v2_complete_protocol_"
            "49c71da11d46f56838a560481350cb3436af3177bab6cb13254f9c84860c6e89",
        )

    def test_new_manifest_validates_and_is_genuinely_different(self):
        new = json.loads(NEW_MANIFEST_PATH.read_text())
        self.assertEqual(validate_complete_protocol(new), new["manifest_id"])
        old = json.loads(OLD_MANIFEST_PATH.read_text())
        self.assertNotEqual(new["manifest_id"], old["manifest_id"])

    def test_new_manifest_only_adds_the_shock_amendment_dependency(self):
        old_content = json.loads(OLD_MANIFEST_PATH.read_text())["content"]
        new_content = json.loads(NEW_MANIFEST_PATH.read_text())["content"]

        changed = {
            key for key in set(old_content) | set(new_content)
            if old_content.get(key) != new_content.get(key)
        }
        self.assertEqual(changed, CHANGED_FIELDS)

        self.assertEqual(new_content["schema_version"], old_content["schema_version"] + 1)

        old_depends, new_depends = old_content["depends_on"], new_content["depends_on"]
        self.assertEqual(set(new_depends) - set(old_depends), {"shock_distance_amendment"})
        for key in old_depends:
            self.assertEqual(new_depends[key], old_depends[key])

    def test_active_manifest_path_points_at_the_superseding_manifest(self):
        self.assertEqual(COMPLETE_PROTOCOL_PATH, NEW_MANIFEST_PATH)
        content = load_complete_protocol()
        self.assertIn("shock_distance_amendment", content["depends_on"])


if __name__ == "__main__":
    unittest.main()
