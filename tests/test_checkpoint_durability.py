"""Tests for checkpoint_durability.py -- the startup durability check added
by the "TBOTS CHECKPOINT DURABILITY REPAIR AND REVALIDATION" GO (2026-09-30)
to close the crash window proven during Cloud V1 acceptance testing: a
checkpoint durably written to disk but never git-committed was silently
skipped by every existing gate (reconcile_status trusts on-disk content
regardless of git status; check_audit_gate only compares committed HEAD vs
origin, never the working tree)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from checkpoint_durability import (
    UnresolvedCheckpointDurability,
    check_startup_durability,
    find_evolution_git_anomalies,
    find_leftover_transaction_debris,
)
from fitness_v2_worker import CAMPAIGN_SEEDS, FINAL_GENERATION, _expansion_batch_id
from worker_checkpoint import STAGE_PREFIX, TRANSACTION_FILE
from worker_git import GitCadenceError

BRANCH = "main"
ACTIVE_MANIFEST_ID = "fitness_v2_complete_protocol_active-test-manifest"
DEFAULT_COUNTS = {"distributional": 4, "execution": 4, "sequence": 4, "shock": 4}


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo_pair(tmp: Path):
    """Bare 'origin' + a real clone at tmp/work, both on BRANCH, with the
    accepted-shaped world bank + generations 0-1 already committed+pushed
    -- mirrors the real accepted chain this check protects, on a local
    remote only (never GitHub)."""
    origin = tmp / "origin.git"
    work = tmp / "work"
    _git(tmp, "init", "--bare", "-b", BRANCH, str(origin))
    _git(tmp, "clone", str(origin), str(work))
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")

    checkpoint_dir = work / "evolution" / "state"
    gen_path, nominee_path = _paths(checkpoint_dir)
    _write_world_bank(checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)
    seed = CAMPAIGN_SEEDS[0]
    _write_generation(checkpoint_dir, gen_path, 0, seed, 0)
    _write_generation(checkpoint_dir, gen_path, 0, seed, 1)
    _git(work, "add", "evolution")
    _git(work, "commit", "-m", "seed: world bank + generations 0-1")
    _git(work, "push", "origin", BRANCH)
    return origin, work, checkpoint_dir, gen_path, nominee_path, seed


def _paths(checkpoint_dir: Path):
    def generation_checkpoint_path(batch_id, seed, generation):
        return checkpoint_dir / f"batch{batch_id}_campaign_{seed}_generation_{generation}.json"

    def nominee_checkpoint_path(batch_id, seed):
        return checkpoint_dir / f"batch{batch_id}_campaign_{seed}_nominee.json"

    return generation_checkpoint_path, nominee_checkpoint_path


def _write_world_bank(checkpoint_dir: Path, world_bank_id: str, counts: dict) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    bank = {
        "historical": [{}, {}, {}],
        "synthetic": {family: [{}] * n for family, n in counts.items()},
        "synthetic_state": {
            family: {"consumed_indices": [], "used_seeds": [], "attempts": []} for family in counts
        },
    }
    (checkpoint_dir / f"{world_bank_id}.json").write_text(json.dumps(bank))


def _write_nominee(checkpoint_dir, nominee_path_fn, batch_id, seed, clears=True):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    nominee_path_fn(batch_id, seed).write_text(json.dumps({
        "genome_id": f"gen_{seed}", "evolution_seed": seed,
        "clears_all_gates": clears, "cross_family_fitness": 0.0,
    }))


def _write_generation(checkpoint_dir, gen_path_fn, batch_id, seed, generation, *, final=False):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    artifact = {"generation": generation, "ranked_genome_ids": [f"gen_{seed}_{generation}"]}
    if final:
        artifact["final_ranked"] = [{"genome_id": f"gen_{seed}_{generation}", "genome": {}}]
    else:
        artifact["next_population"] = [{"genome_id": f"gen_{seed}_{generation}_child"}]
        artifact["used_genome_ids"] = sorted({f"gen_{seed}_{generation}", f"gen_{seed}_{generation}_child"})
    gen_path_fn(batch_id, seed, generation).write_text(json.dumps(artifact))


def _kwargs(checkpoint_dir, gen_path, nominee_path, repo_root, branch, status_path):
    return dict(
        repo_root=repo_root, checkpoint_dir=checkpoint_dir, branch=branch,
        campaign_seeds=CAMPAIGN_SEEDS, final_generation=FINAL_GENERATION,
        expansion_batch_id=_expansion_batch_id,
        generation_checkpoint_path=gen_path, nominee_checkpoint_path=nominee_path,
        active_protocol_manifest_id=lambda: ACTIVE_MANIFEST_ID,
        status_path=status_path,
    )


class DetectionScoping(unittest.TestCase):
    """The most important false-positive to rule out: STATUS.json is
    *always* locally modified by design (never git-committed at all, per
    worker_state_reconstruction's own module docstring) -- a detector that
    doesn't scope to evolution/ would HOLD on every single boot."""

    def test_dirty_status_json_alone_is_not_an_anomaly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            (work / "STATUS.json").write_text('{"generation": 1}\n')  # never committed, by design

            anomalies = find_evolution_git_anomalies(work)
            self.assertEqual(anomalies, [])

            result = check_startup_durability(
                **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
            )
            self.assertEqual(result["outcome"], "clean")

    def test_untracked_evolution_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)  # orphan: not yet committed

            anomalies = find_evolution_git_anomalies(work)
            self.assertEqual(len(anomalies), 1)
            self.assertEqual(anomalies[0][0], "??")
            self.assertTrue(anomalies[0][1].endswith("generation_2.json"))

    def test_s6b_runs_scratch_space_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            s6b = work / "evolution" / "s6b_runs" / "primary"
            s6b.mkdir(parents=True)
            (s6b / "scratch.json").write_text("{}")

            self.assertEqual(find_evolution_git_anomalies(work), [])


class ValidOrphanPersistence(unittest.TestCase):
    def test_valid_orphan_is_committed_pushed_and_remote_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)  # contiguous, valid orphan

            result = check_startup_durability(
                **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
            )

            self.assertEqual(result["outcome"], "repaired")
            self.assertTrue(result["committed"])
            self.assertEqual(result["durable_progress"]["generation"], 2)
            # remote-verified: local HEAD now equals the bare origin's HEAD
            local_head = _git(work, "rev-parse", "HEAD")
            bare_head = subprocess.run(
                ["git", "--git-dir", str(origin), "rev-parse", BRANCH],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(local_head, bare_head)
            self.assertEqual(find_evolution_git_anomalies(work), [])

    def test_second_call_after_repair_is_clean_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)
            kwargs = _kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")

            first = check_startup_durability(**kwargs)
            second = check_startup_durability(**kwargs)

            self.assertEqual(first["outcome"], "repaired")
            self.assertEqual(second["outcome"], "clean")

    def test_repeated_restart_across_three_invocations_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)
            kwargs = _kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")

            outcomes = [check_startup_durability(**kwargs)["outcome"] for _ in range(3)]
            self.assertEqual(outcomes, ["repaired", "clean", "clean"])
            # exactly one new commit landed, regardless of how many restarts probed it
            log = _git(work, "log", "--oneline")
            self.assertEqual(len(log.splitlines()), 2)  # seed commit (bank+gen0+gen1) + this one


class InvalidOrphanFailsClosed(unittest.TestCase):
    def test_non_contiguous_orphan_fails_closed_and_commits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            # generation 3 present, but generation 2 was never committed --
            # a gap, not a contiguous extension.
            _write_generation(checkpoint_dir, gen_path, 0, seed, 3)

            with self.assertRaises(UnresolvedCheckpointDurability) as ctx:
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )
            self.assertIn("generation_3.json", str(ctx.exception))
            self.assertIn("Bounded recovery", str(ctx.exception))
            # nothing committed -- the orphan is still sitting there untouched
            self.assertEqual(_git(work, "log", "--oneline").count("\n") + 1, 1)
            anomalies = find_evolution_git_anomalies(work)
            self.assertEqual(len(anomalies), 1)

    def test_malformed_orphan_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            gen_path(0, seed, 2).write_text('{"generation": 2}')  # missing next_population

            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )

    def test_invalid_orphan_still_fails_closed_on_a_repeated_attempt(self):
        """Never auto-resolved, never auto-discarded -- repeated restarts
        must keep failing closed identically until a human intervenes."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 3)  # non-contiguous
            kwargs = _kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")

            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(**kwargs)
            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(**kwargs)
            self.assertTrue(gen_path(0, seed, 3).exists())  # never deleted


class ProtocolManifestMismatchFailsClosed(unittest.TestCase):
    def test_orphan_under_a_superseded_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)
            (work / "STATUS.json").write_text(json.dumps({"protocol_manifest": "a-superseded-manifest"}))

            with self.assertRaises(UnresolvedCheckpointDurability) as ctx:
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )
            self.assertIn("superseded", str(ctx.exception))
            self.assertEqual(find_evolution_git_anomalies(work), [find_evolution_git_anomalies(work)[0]])


class TransactionDebrisFailsClosed(unittest.TestCase):
    def test_leftover_transaction_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            (work / TRANSACTION_FILE).write_text('{"schema_version": 1}')

            with self.assertRaises(UnresolvedCheckpointDurability) as ctx:
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )
            self.assertIn(TRANSACTION_FILE, str(ctx.exception))

    def test_leftover_stage_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            (work / f"{STAGE_PREFIX}orphan").mkdir()

            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )

    def test_debris_check_runs_before_git_scan_no_git_calls_needed(self):
        found = find_leftover_transaction_debris("/path/does/not/exist/at/all")
        self.assertEqual(found, [])  # a nonexistent repo_root just means "nothing found", never a crash


class PushFailureFailsClosedNoRetry(unittest.TestCase):
    def test_real_remote_divergence_on_push_fails_closed_orphan_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)

            # Someone else pushes first, from a second clone.
            other = tmp / "other_clone"
            _git(tmp, "clone", str(origin), str(other))
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            (other / "elsewhere.txt").write_text("pushed from elsewhere\n")
            _git(other, "add", "elsewhere.txt")
            _git(other, "commit", "-m", "pushed elsewhere first")
            _git(other, "push", "origin", BRANCH)

            _write_generation(checkpoint_dir, gen_path, 0, seed, 2)  # valid, but push will fail

            with self.assertRaises(UnresolvedCheckpointDurability) as ctx:
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )
            self.assertIn("could not be committed/pushed/remote-verified", str(ctx.exception))
            self.assertIn("never force-pushes", str(ctx.exception))
            # git committed locally (commit_and_push commits before it pushes)
            # but never force-pushed or retried -- confirm no force-push
            # artifact and that origin is still exactly what "other" pushed.
            bare_head = subprocess.run(
                ["git", "--git-dir", str(origin), "rev-parse", BRANCH],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(bare_head, _git(other, "rev-parse", "HEAD"))


class NomineeAndWorldBankOrphans(unittest.TestCase):
    """The two other checkpoint shapes _orphan_actually_incorporated
    recognizes, beyond a plain generation file -- previously untested."""

    def _complete_campaign(self, checkpoint_dir, gen_path, batch_id, seed):
        for g in range(FINAL_GENERATION):
            gen_path(batch_id, seed, g).write_text(json.dumps({
                "generation": g, "ranked_genome_ids": [f"g{g}"],
                "next_population": [{"genome_id": f"g{g}_child"}],
                "used_genome_ids": [f"g{g}", f"g{g}_child"],
            }))
        gen_path(batch_id, seed, FINAL_GENERATION).write_text(json.dumps({
            "generation": FINAL_GENERATION, "ranked_genome_ids": [f"g{FINAL_GENERATION}"],
            "final_ranked": [{"genome_id": f"g{FINAL_GENERATION}", "genome": {}}],
        }))

    def test_valid_nominee_orphan_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            self._complete_campaign(checkpoint_dir, gen_path, 0, seed)
            # commit the completed generation chain first, exactly as the
            # real worker would (one checkpoint per generation); only the
            # nominee itself is the orphan under test.
            _git(work, "add", "evolution")
            _git(work, "commit", "-m", "generations through final")
            _git(work, "push", "origin", BRANCH)
            _write_nominee(checkpoint_dir, nominee_path, 0, seed, clears=True)

            result = check_startup_durability(
                **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
            )
            self.assertEqual(result["outcome"], "repaired")
            self.assertEqual(result["durable_progress"]["campaigns_complete"], 1)

    def test_nominee_for_a_seed_not_in_campaign_seeds_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            self._complete_campaign(checkpoint_dir, gen_path, 0, seed)
            _git(work, "add", "evolution")
            _git(work, "commit", "-m", "generations through final")
            _git(work, "push", "origin", BRANCH)
            bogus_seed = 999999999
            _write_nominee(checkpoint_dir, nominee_path, 0, bogus_seed, clears=True)

            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )

    def test_valid_world_bank_orphan_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin = tmp / "origin.git"
            work = tmp / "work"
            _git(tmp, "init", "--bare", "-b", BRANCH, str(origin))
            _git(tmp, "clone", str(origin), str(work))
            _git(work, "config", "user.email", "test@example.com")
            _git(work, "config", "user.name", "Test")
            (work / "seed.txt").write_text("seed\n")
            _git(work, "add", "seed.txt")
            _git(work, "commit", "-m", "seed")
            _git(work, "push", "origin", BRANCH)

            checkpoint_dir = work / "evolution" / "state"
            gen_path, nominee_path = _paths(checkpoint_dir)
            _write_world_bank(checkpoint_dir, "world_bank_v1", DEFAULT_COUNTS)  # orphan: never committed

            result = check_startup_durability(
                **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
            )
            self.assertEqual(result["outcome"], "repaired")
            self.assertEqual(result["durable_progress"]["world_bank_id"], "world_bank_v1")

    def test_admission_decision_orphan_is_not_auto_validated_fails_closed(self):
        """This check does not (yet) have a validated derivation for
        admission_decision.json -- it must fail closed by default rather
        than trust an unrecognized shape, per the repair GO's own
        instruction that filename/parse success alone is insufficient."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            (checkpoint_dir / "batch0_admission_decision.json").write_text(
                json.dumps({"ratio": "1/5", "admitted": True})
            )

            with self.assertRaises(UnresolvedCheckpointDurability):
                check_startup_durability(
                    **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
                )


class CleanStart(unittest.TestCase):
    def test_fresh_checkout_with_nothing_but_accepted_history_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            result = check_startup_durability(
                **_kwargs(checkpoint_dir, gen_path, nominee_path, work, BRANCH, work / "STATUS.json")
            )
            self.assertEqual(result["outcome"], "clean")


class NoScientificEvaluationWhileUnresolved(unittest.TestCase):
    """Proves the ordering guarantee itself, not just its usual side
    effect: run_generation must never even be CALLED while an orphan is
    unresolved, whether or not it would have produced a new file."""

    def test_run_generation_is_never_invoked_when_durability_check_stops(self):
        import fitness_v2_worker as w

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work, checkpoint_dir, gen_path, nominee_path, seed = _init_repo_pair(tmp)
            _write_generation(checkpoint_dir, gen_path, 0, seed, 3)  # non-contiguous: invalid

            with mock.patch.object(w, "REPO_ROOT", work), \
                 mock.patch.object(w, "STATUS_PATH", work / "STATUS.json"), \
                 mock.patch.object(w, "CHECKPOINT_DIR", checkpoint_dir), \
                 mock.patch.object(w, "BRANCH", BRANCH), \
                 mock.patch.object(w, "disk_gate", return_value={"outcome": "ok", "stop_code": None}), \
                 mock.patch.object(w, "run_generation") as fake_run_generation:
                with self.assertRaises(w.WorkerStop) as ctx:
                    w.run_one_step()
                self.assertEqual(ctx.exception.stop_code, "O")
                fake_run_generation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
