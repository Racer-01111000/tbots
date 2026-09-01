import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib.ids import canonical_json
import s5c_freeze_advancement as s5c


def row(rank, genome, score, behavior):
    episodes = [
        {"episode_index": index, "execution_result": behavior, "year": 2019 + index}
        for index in range(4)
    ]
    aggregate = {"qualification_score": score, "behavior": behavior}
    metrics_hash = hashlib.sha256(canonical_json({
        "episodes": episodes,
        "aggregate": aggregate,
    }).encode()).hexdigest()
    return {
        "qualification_rank": rank,
        "development_rank": rank,
        "genome_id": genome,
        "episode_metrics": episodes,
        "aggregate": aggregate,
        "metrics_hash": metrics_hash,
    }


class S5CAdvancementUnitTest(unittest.TestCase):
    def test_mechanical_selection_skips_complete_behavior_duplicates(self):
        rows = [
            row(1, "gen_01", 10.0, "A"),
            row(2, "gen_02", 10.0, "A"),
            row(3, "gen_03", 9.0, "B"),
            row(4, "gen_04", 9.0, "B"),
            row(5, "gen_05", 8.0, "C"),
        ] + [
            row(rank, f"gen_{rank:02d}", 7.0 - rank / 100, "D")
            for rank in range(6, 11)
        ]
        selected, trace = s5c.mechanical_select(rows)
        self.assertEqual(
            [item["genome_id"] for item in selected],
            ["gen_01", "gen_03", "gen_05"],
        )
        self.assertEqual(trace[1]["action"], "skipped_identical_behavior")
        self.assertEqual(trace[3]["action"], "skipped_identical_behavior")

    def test_identity_is_not_qualification_score_only(self):
        rows = [
            row(1, "gen_01", 10.0, "A"),
            row(2, "gen_02", 10.0, "B"),
            row(3, "gen_03", 9.0, "C"),
        ] + [
            row(rank, f"gen_{rank:02d}", 8.0 - rank / 100, f"X{rank}")
            for rank in range(4, 11)
        ]
        selected, _ = s5c.mechanical_select(rows)
        self.assertEqual(
            [item["genome_id"] for item in selected],
            ["gen_01", "gen_02", "gen_03"],
        )

    def test_forged_behavior_representation_is_rejected(self):
        forged = row(1, "gen_01", 1.0, "A")
        forged["episode_metrics"][0]["execution_result"] = "FORGED"
        with self.assertRaises(s5c.S5CAdvancementError):
            s5c.behavior_identity(forged)

    def test_genome_inputs_are_not_mutated(self):
        rows = [
            row(rank, f"gen_{rank:02d}", 11.0 - rank, f"B{rank}")
            for rank in range(1, 11)
        ]
        before = copy.deepcopy(rows)
        s5c.mechanical_select(rows)
        self.assertEqual(rows, before)

    def test_protocol_keeps_sealed_lanes_locked_and_has_no_data_path(self):
        protocol = s5c.ADVANCEMENT_PROTOCOL
        self.assertEqual(protocol["sealed_lanes"]["championship"]["status"], "LOCKED")
        self.assertEqual(protocol["sealed_lanes"]["final_reserve"]["status"], "LOCKED")
        self.assertFalse(protocol["championship_or_final_reserve_information_allowed"])
        source = Path(s5c.__file__).read_text()
        self.assertNotIn("data/normalized", source)
        self.assertNotIn("data/qualification_bundles", source)
        self.assertNotIn("data/championship", source)
        self.assertNotIn("data/sealed", source)


class S5CAdvancementArtifactTest(unittest.TestCase):
    def test_frozen_artifacts_are_content_addressed_and_expected(self):
        if not s5c.PROTOCOL_PATH.exists():
            self.skipTest("S5C artifact not frozen in this checkout")
        protocol_envelope = json.loads(s5c.PROTOCOL_PATH.read_text())
        self.assertEqual(protocol_envelope["content"], s5c.ADVANCEMENT_PROTOCOL)
        self.assertEqual(protocol_envelope["manifest_hash"], s5c.PROTOCOL_HASH)
        manifests = list(s5c.PROTOCOL_DIR.glob(
            "advancement_manifest_s5c_advancement_manifest_*.json"
        ))
        self.assertEqual(len(manifests), 1)
        artifact = json.loads(manifests[0].read_text())
        content = artifact["content"]
        self.assertEqual(
            artifact["manifest_hash"],
            s5c.content_hash("s5c_advancement_manifest_", content),
        )
        self.assertEqual(
            [row["genome_id"] for row in content["selected_finalists"]],
            list(s5c.EXPECTED_REPRESENTATIVES),
        )
        self.assertEqual(content["championship_observations_accessed"], 0)
        self.assertEqual(content["final_reserve_observations_accessed"], 0)
        self.assertEqual(content["sealed_observation_paths_read"], [])


if __name__ == "__main__":
    unittest.main()
