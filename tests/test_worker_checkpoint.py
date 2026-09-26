import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_checkpoint import (
    ArtifactCollision,
    CheckpointError,
    STAGE_PREFIX,
    TRANSACTION_FILE,
    cleanup_orphan_stages,
    commit_checkpoint,
    prepare_transaction,
    recover_pending_transaction,
)

PREFIXES = {"generations", "feasibility"}


class CommitCheckpoint(unittest.TestCase):
    def test_writes_every_artifact_with_correct_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            artifacts = {
                "generations/gen_00.json": {"generation": 0, "value": 1},
                "feasibility/gen_00.json": {"audit": True},
            }
            result = commit_checkpoint(target, "gen_00", artifacts, PREFIXES)
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(
                json.loads((target / "generations/gen_00.json").read_text()),
                {"generation": 0, "value": 1},
            )
            self.assertFalse((target / TRANSACTION_FILE).exists())

    def test_never_overwrites_a_final_artifact_with_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            commit_checkpoint(target, "gen_00", {"generations/gen_00.json": {"v": 1}}, PREFIXES)
            with self.assertRaises(ArtifactCollision):
                commit_checkpoint(target, "gen_00_retry", {"generations/gen_00.json": {"v": 2}}, PREFIXES)
            # the original survives untouched
            self.assertEqual(
                json.loads((target / "generations/gen_00.json").read_text()), {"v": 1},
            )

    def test_committing_identical_content_twice_is_a_harmless_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            commit_checkpoint(target, "gen_00", {"generations/gen_00.json": {"v": 1}}, PREFIXES)
            result = commit_checkpoint(target, "gen_00_again", {"generations/gen_00.json": {"v": 1}}, PREFIXES)
            self.assertEqual(result["status"], "recovered")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            with self.assertRaises(CheckpointError):
                commit_checkpoint(target, "gen_00", {"../escape.json": {}}, PREFIXES)

    def test_rejects_path_outside_allowed_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            with self.assertRaises(CheckpointError):
                commit_checkpoint(target, "gen_00", {"not_allowed/x.json": {}}, PREFIXES)


class CrashAndResume(unittest.TestCase):
    """Simulates the exact failure points GO Addendum A §6's acceptance proof
    exercises for real: a kill -9 (or reboot) right after the transaction
    manifest is fsync'd but before any final file is hard-linked."""

    def test_resumes_a_transaction_whose_manifest_was_fsynced_but_never_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            artifacts = {"generations/gen_00.json": {"generation": 0}}
            prepare_transaction(target, "gen_00", artifacts, PREFIXES)  # simulated crash here
            self.assertTrue((target / TRANSACTION_FILE).exists())
            self.assertFalse((target / "generations/gen_00.json").exists())

            recovered = recover_pending_transaction(target)  # simulated restart
            self.assertEqual(recovered["status"], "recovered")
            self.assertTrue((target / "generations/gen_00.json").exists())
            self.assertFalse((target / TRANSACTION_FILE).exists())

    def test_recovering_twice_in_a_row_is_a_no_op_the_second_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            commit_checkpoint(target, "gen_00", {"generations/gen_00.json": {"v": 1}}, PREFIXES)
            self.assertIsNone(recover_pending_transaction(target))

    def test_a_new_commit_after_restart_first_finishes_the_stale_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            prepare_transaction(
                target, "gen_00", {"generations/gen_00.json": {"generation": 0}}, PREFIXES,
            )  # crash before recover
            # worker restarts and immediately tries the NEXT unit of work
            commit_checkpoint(
                target, "gen_01", {"generations/gen_01.json": {"generation": 1}}, PREFIXES,
            )
            self.assertTrue((target / "generations/gen_00.json").exists())
            self.assertTrue((target / "generations/gen_01.json").exists())

    def test_orphan_pre_manifest_stage_is_cleaned_up_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            target.mkdir(parents=True)
            stray = target / f"{STAGE_PREFIX}half_written"
            stray.mkdir()
            (stray / "junk.json").write_bytes(b"{incomplete")
            self.assertIsNone(cleanup_orphan_stages(target))
            self.assertFalse(stray.exists())

    def test_prepare_refuses_a_second_transaction_while_one_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "campaign_1"
            prepare_transaction(target, "gen_00", {"generations/gen_00.json": {"v": 1}}, PREFIXES)
            with self.assertRaises(CheckpointError):
                prepare_transaction(target, "gen_00b", {"generations/gen_00b.json": {"v": 2}}, PREFIXES)


if __name__ == "__main__":
    unittest.main()
