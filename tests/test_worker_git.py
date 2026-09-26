import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from worker_git import GitCadenceError, commit_and_push, verify_local_matches_remote

BRANCH = "main"


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _init_repo_pair(tmp: Path):
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
    return origin, work


class CommitAndPush(unittest.TestCase):
    def test_commits_and_pushes_a_new_file_and_lands_on_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work = _init_repo_pair(tmp)
            (work / "STATUS.json").write_text('{"a": 1}\n')

            result = commit_and_push(work, BRANCH, [work / "STATUS.json"], "generation 0 heartbeat")

            self.assertTrue(result["committed"])
            self.assertIsNotNone(result["commit_sha"])
            self.assertEqual(_git(work, "rev-parse", "HEAD"), result["commit_sha"])
            bare_head = subprocess.run(
                ["git", "--git-dir", str(origin), "rev-parse", BRANCH],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(bare_head, result["commit_sha"])

    def test_is_a_no_op_when_the_path_already_matches_head_but_still_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            result = commit_and_push(work, BRANCH, [work / "seed.txt"], "no-op attempt")
            self.assertFalse(result["committed"])
            self.assertIsNone(result["commit_sha"])

    def test_requires_at_least_one_explicit_path_never_dash_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            with self.assertRaises(GitCadenceError):
                commit_and_push(work, BRANCH, [], "bad call")

    def test_push_failure_on_unpulled_divergence_raises_git_cadence_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, work = _init_repo_pair(tmp)
            other = tmp / "other_clone"
            _git(tmp, "clone", str(origin), str(other))
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            (other / "first.txt").write_text("first\n")
            _git(other, "add", "first.txt")
            _git(other, "commit", "-m", "first pushed elsewhere")
            _git(other, "push", "origin", BRANCH)

            (work / "second.txt").write_text("second\n")
            with self.assertRaises(GitCadenceError):
                commit_and_push(work, BRANCH, [work / "second.txt"], "conflicting push")


class VerifyLocalMatchesRemote(unittest.TestCase):
    def test_passes_when_in_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            head = verify_local_matches_remote(work, BRANCH)
            self.assertEqual(head, _git(work, "rev-parse", "HEAD"))

    def test_raises_when_local_has_an_unpushed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _origin, work = _init_repo_pair(tmp)
            (work / "unpushed.txt").write_text("x\n")
            _git(work, "add", "unpushed.txt")
            _git(work, "commit", "-m", "not pushed")
            with self.assertRaises(GitCadenceError):
                verify_local_matches_remote(work, BRANCH)


if __name__ == "__main__":
    unittest.main()
