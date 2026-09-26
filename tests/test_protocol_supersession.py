"""Consistency checks for evolution/protocol/SUPERSEDED.json (GO Addendum C
section 1). Reads real repo files -- STATUS.json and the protocol
directory's file listing -- but never any market data. The point of this
suite is exactly to catch a stray third "ready" manifest sitting on disk
ambiguously, so it must look at what is actually there, not a fixture.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = ROOT / "evolution" / "protocol"
SUPERSEDED_PATH = PROTOCOL_DIR / "SUPERSEDED.json"
STATUS_PATH = ROOT / "STATUS.json"
COMPLETE_PROTOCOL_RE = re.compile(r"^(fitness_v2_complete_protocol_[0-9a-f]+)\.json$")


def _load_superseded() -> dict:
    return json.loads(SUPERSEDED_PATH.read_text())


def _load_status() -> dict:
    return json.loads(STATUS_PATH.read_text())


def _complete_protocol_manifest_ids() -> list[str]:
    ids = []
    for path in PROTOCOL_DIR.glob("fitness_v2_complete_protocol_*.json"):
        match = COMPLETE_PROTOCOL_RE.match(path.name)
        if match:
            ids.append(match.group(1))
    return sorted(ids)


class SupersededFileSchema(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(SUPERSEDED_PATH.is_file())

    def test_schema_field_is_exact(self):
        content = _load_superseded()
        self.assertEqual(content.get("schema"), "tbots-protocol-supersession-v1")

    def test_top_level_keys_are_exactly_schema_updated_superseded(self):
        content = _load_superseded()
        self.assertEqual(set(content), {"schema", "updated_utc", "superseded"})

    def test_superseded_is_a_list(self):
        content = _load_superseded()
        self.assertIsInstance(content["superseded"], list)

    def test_every_entry_has_the_required_fields(self):
        required = {
            "manifest_id", "superseded_by", "superseded_at_commit",
            "reason", "production_used",
        }
        for entry in _load_superseded()["superseded"]:
            self.assertEqual(set(entry), required)

    def test_production_used_is_a_bool_in_every_entry(self):
        for entry in _load_superseded()["superseded"]:
            self.assertIsInstance(entry["production_used"], bool)

    def test_no_entry_supersedes_itself(self):
        for entry in _load_superseded()["superseded"]:
            self.assertNotEqual(entry["manifest_id"], entry["superseded_by"])

    def test_manifest_ids_are_unique_across_entries(self):
        ids = [entry["manifest_id"] for entry in _load_superseded()["superseded"]]
        self.assertEqual(len(ids), len(set(ids)), "an entry was recorded more than once")

    def test_commit_shas_look_like_real_git_commits(self):
        sha_re = re.compile(r"^[0-9a-f]{40}$")
        for entry in _load_superseded()["superseded"]:
            self.assertRegex(entry["superseded_at_commit"], sha_re)


class NoAmbiguousReadyManifest(unittest.TestCase):
    """The actual point of this file: a stray third 'ready' manifest must
    fail the suite loudly instead of sitting there ambiguously."""

    def test_every_complete_protocol_manifest_on_disk_is_accounted_for(self):
        on_disk = set(_complete_protocol_manifest_ids())
        superseded_ids = {entry["manifest_id"] for entry in _load_superseded()["superseded"]}
        active_id = _load_status()["protocol_manifest"]

        accounted_for = superseded_ids | {active_id}
        unaccounted = on_disk - accounted_for
        self.assertEqual(
            unaccounted, set(),
            f"manifest(s) on disk are neither superseded nor the active STATUS.json "
            f"manifest -- ambiguous: {sorted(unaccounted)}",
        )

    def test_exactly_one_on_disk_manifest_is_not_superseded(self):
        on_disk = set(_complete_protocol_manifest_ids())
        superseded_ids = {entry["manifest_id"] for entry in _load_superseded()["superseded"]}
        active_candidates = on_disk - superseded_ids
        self.assertEqual(len(active_candidates), 1, active_candidates)

    def test_the_one_non_superseded_manifest_matches_status_json(self):
        on_disk = set(_complete_protocol_manifest_ids())
        superseded_ids = {entry["manifest_id"] for entry in _load_superseded()["superseded"]}
        active_candidates = on_disk - superseded_ids
        self.assertEqual(active_candidates, {_load_status()["protocol_manifest"]})

    def test_status_json_manifest_actually_exists_on_disk(self):
        active_id = _load_status()["protocol_manifest"]
        self.assertIn(active_id, _complete_protocol_manifest_ids())

    def test_superseded_by_targets_are_each_either_active_or_on_disk(self):
        on_disk = set(_complete_protocol_manifest_ids())
        for entry in _load_superseded()["superseded"]:
            self.assertIn(entry["superseded_by"], on_disk)


if __name__ == "__main__":
    unittest.main()
