"""Git commit/push cadence for the TBOTS Fitness V2 worker.

GO Addendum A: 'git commit+push once per generation plus per state change --
never per artifact.' Rick's blocking correction: 'push after every commit,
not at the end.' Every commit this module makes is followed, in the same
call, by a push and a verification that local HEAD == origin/<branch> --
there is no code path here that commits without also pushing and verifying.
"""
import subprocess


class GitCadenceError(Exception):
    pass


def _run_git(repo_root, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise GitCadenceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_and_push(repo_root, branch: str, paths: list, message: str) -> dict:
    """Stages exactly `paths` (never `-A`, never `.`), commits, pushes, and
    verifies. Returns {"committed": bool, "commit_sha": str|None}. If
    nothing is staged (paths already match HEAD) this commits nothing but
    still verifies local == remote -- callers must not skip verification
    just because the call was a no-op."""
    if not paths:
        raise GitCadenceError("commit_and_push requires at least one explicit path")
    _run_git(repo_root, "add", "--", *[str(p) for p in paths])
    status = _run_git(repo_root, "status", "--porcelain", "--", *[str(p) for p in paths])
    if not status:
        return {
            "committed": False, "commit_sha": None,
            "verified_head": verify_local_matches_remote(repo_root, branch),
        }

    _run_git(repo_root, "commit", "-m", message)
    commit_sha = _run_git(repo_root, "rev-parse", "HEAD")
    _run_git(repo_root, "push", "origin", f"HEAD:{branch}")
    verified_head = verify_local_matches_remote(repo_root, branch)
    return {"committed": True, "commit_sha": commit_sha, "verified_head": verified_head}


def verify_local_matches_remote(repo_root, branch: str) -> str:
    _run_git(repo_root, "fetch", "origin", branch)
    local_head = _run_git(repo_root, "rev-parse", "HEAD")
    remote_head = _run_git(repo_root, "rev-parse", f"origin/{branch}")
    if local_head != remote_head:
        raise GitCadenceError(
            f"push did not land: local HEAD {local_head} != origin/{branch} {remote_head}"
        )
    return local_head
