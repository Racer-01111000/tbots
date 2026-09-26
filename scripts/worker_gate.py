"""Freeze-audit gate: window / hold-file / fast-forward checks (GO Addendum A §2).

Must run before any world/genome/baseline generation. Three possible
outcomes:
  - window still open              -> ("idle", None)
  - window elapsed, HOLD present   -> ("stopped", "H")
  - window elapsed, non-fast-forward remote (rewritten branch) -> ("stopped", "C")
  - window elapsed, clean pull, no hold -> ("ready", None)

This module only ever fast-forwards local HEAD to a strict ancestor-compatible
remote tip. It never force-resets, never rewrites history, and a
non-fast-forward situation is reported, not repaired -- repairing it is
Rick's call (stop condition C in the original GO).
"""
import subprocess
from datetime import datetime, timezone

HOLD_FILENAME = "FREEZE_AUDIT_HOLD.md"


class GateError(Exception):
    pass


def _run_git(repo_root, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def window_elapsed(now_utc: datetime, audit_window_ends_utc: str) -> bool:
    return now_utc >= parse_utc(audit_window_ends_utc)


def check_audit_gate(repo_root, branch: str, audit_window_ends_utc: str, *, now_utc=None) -> dict:
    """Returns {"outcome": "idle"|"stopped"|"ready", "stop_code": str|None, "detail": str}."""
    now_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if not window_elapsed(now_utc, audit_window_ends_utc):
        return {
            "outcome": "idle", "stop_code": None,
            "detail": f"audit window open until {audit_window_ends_utc}",
        }

    _run_git(repo_root, "fetch", "origin", branch)
    remote_ref = f"origin/{branch}"
    local_head = _run_git(repo_root, "rev-parse", "HEAD")
    remote_head = _run_git(repo_root, "rev-parse", remote_ref)

    if local_head != remote_head:
        merge_base = _run_git(repo_root, "merge-base", "HEAD", remote_ref)
        if merge_base != local_head:
            return {
                "outcome": "stopped", "stop_code": "C",
                "detail": (
                    f"local HEAD {local_head} is not an ancestor of {remote_ref} "
                    f"{remote_head} (non-fast-forward / rewritten branch)"
                ),
            }
        _run_git(repo_root, "merge", "--ff-only", remote_ref)
        local_head = _run_git(repo_root, "rev-parse", "HEAD")

    try:
        _run_git(repo_root, "show", f"{remote_ref}:{HOLD_FILENAME}")
        hold_present = True
    except GateError:
        hold_present = False

    if hold_present:
        return {
            "outcome": "stopped", "stop_code": "H",
            "detail": f"{HOLD_FILENAME} present on {branch}",
        }

    return {
        "outcome": "ready", "stop_code": None,
        "detail": f"audit window elapsed, no hold file, HEAD={local_head}",
    }
