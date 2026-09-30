"""Startup checkpoint-durability check (Rick, 2026-09-30: "GO -- TBOTS
CHECKPOINT DURABILITY REPAIR AND REVALIDATION").

Closes a crash window proven during the prior Cloud V1 acceptance GO: a
checkpoint file can be durably written to evolution/state/ (via
worker_checkpoint.commit_checkpoint's stage-then-hardlink transaction), and
the process can then crash before the corresponding git commit ever runs.
recover_pending_transaction only recovers PRE-finalization state (a staged
transaction not yet hard-linked); it does nothing for an already-finalized
file that simply has not been committed yet. Nothing else in the existing
control flow inspects git's working-tree status: reconcile_status trusts
durable evolution/state/ content regardless of git status and advances
past the orphan, and check_audit_gate only ever compares committed HEAD
against origin -- never the working tree. Proven empirically (not
theorized) during the acceptance GO: after the crash, restart correctly
advanced to the next generation with no duplication or corruption, but the
orphaned file was left permanently untracked and unpushed, forever.

This module runs in run_one_step(), immediately after
recover_pending_transaction + disk_gate and BEFORE reconcile_status, so
STATUS.json can never advance to describe a checkpoint that is not yet
durable in git. It owns the working-tree dimension only.
check_audit_gate continues to own the commit-graph dimension (a locally
committed-but-unpushed generation -- GO Addendum A's stop_code "C") --
the two are deliberately not merged; see that module's own docstring.

Design choice (stated explicitly per the repair GO's own instruction):
after successfully persisting an orphan, run_one_step() returns a report
and the invocation ends -- it does not fall through to reconcile_status/
_advance in the same call. Trivially idempotent (the next invocation finds
a clean tree and proceeds normally), and avoids ever evaluating a new
generation in the same invocation that just resolved a durability
question.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from worker_checkpoint import STAGE_PREFIX, TRANSACTION_FILE
from worker_git import GitCadenceError, commit_and_push
from worker_state_reconstruction import StateReconstructionError, derive_durable_progress

_GENERATION_RE = re.compile(r"batch\d+_campaign_\d+_generation_(\d+)\.json$")
_NOMINEE_RE = re.compile(r"batch\d+_campaign_(\d+)_nominee\.json$")


class UnresolvedCheckpointDurability(Exception):
    """An orphan checkpoint was found but cannot be safely auto-persisted.
    The caller must convert this into a fail-closed WorkerStop (stop_code
    "O") -- never retried automatically, never silently skipped."""


def _run_git(repo_root, *args) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise GitCadenceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def find_evolution_git_anomalies(repo_root) -> list[tuple[str, str]]:
    """[(status_code, relative_path), ...] for anything under evolution/
    that is untracked ("??") or locally modified ("M") relative to HEAD.

    Deliberately scoped to evolution/ only:
    - STATUS.json lives at REPO_ROOT, not evolution/, and is *always*
      locally modified by design (see worker_state_reconstruction's own
      module docstring) -- an unscoped `git status` would fail closed on
      every single boot, which is not this check's job.
    - evolution/s6b_runs/ is .gitignore'd legacy-run scratch space,
      unrelated to Fitness V2 checkpoints; excluded explicitly in case a
      future .gitignore edit ever changes that.
    """
    # --untracked-files=all: without it, git collapses an entirely-new,
    # never-before-tracked directory into a single "?? evolution/" line
    # instead of listing the files inside it -- fine for a human glance,
    # but this check needs one path per file to validate and commit each
    # individually.
    output = _run_git(repo_root, "status", "--untracked-files=all", "--porcelain", "--", "evolution/")
    anomalies = []
    for line in output.splitlines():
        if not line:
            continue
        code, rel = line[:2].strip(), line[3:]
        if rel.startswith("evolution/s6b_runs/"):
            continue
        anomalies.append((code, rel))
    return anomalies


def find_leftover_transaction_debris(repo_root) -> list[str]:
    """Names of any worker_checkpoint transaction manifest / stage
    directory still present at REPO_ROOT. run_one_step always calls
    recover_pending_transaction immediately before this check, and that
    function either finishes a pending transaction or fully cleans up
    inert pre-manifest debris -- so anything still here afterward is a
    genuine anomaly this module has no basis to resolve on its own."""
    root = Path(repo_root)
    found = []
    if (root / TRANSACTION_FILE).exists():
        found.append(TRANSACTION_FILE)
    found.extend(sorted(p.name for p in root.glob(f"{STAGE_PREFIX}*")))
    return found


def _orphan_actually_incorporated(rel_path: str, durable: dict, campaign_seeds) -> bool:
    """derive_campaign_progress falls back to the latest earlier-VALID
    generation when the newest on-disk file is malformed, rather than
    raising, whenever an earlier valid fallback exists (see its own
    docstring: fail-closed only applies when there is NO earlier valid
    generation). That is correct for reconcile_status's purposes, but it
    means a malformed orphan can silently pass derive_durable_progress
    without ever being incorporated. Filename or parse success alone is
    never sufficient (per the repair GO's own instruction) -- this
    confirms the specific orphan's claimed position is actually reflected
    in the derived state, not quietly skipped."""
    name = Path(rel_path).name
    gen_match = _GENERATION_RE.search(name)
    if gen_match:
        claimed_generation = int(gen_match.group(1))
        return durable["generation"] is not None and durable["generation"] >= claimed_generation

    nominee_match = _NOMINEE_RE.search(name)
    if nominee_match:
        seed = int(nominee_match.group(1))
        if seed not in campaign_seeds:
            return False
        index = campaign_seeds.index(seed)
        return durable["campaigns_complete"] >= index + 1

    # world_bank_path() names the file after whatever world_bank_id string
    # the worker chose (not always literally "world_bank_v1") -- match
    # generically: did derive_world_bank_progress's own winning-candidate
    # selection pick exactly this file?
    if durable["world_bank_id"] is not None and Path(name).stem == durable["world_bank_id"]:
        return True

    # admission_decision.json and any other/unrecognized evolution/state
    # file type: this module does not (yet) have a validated derivation to
    # check it against, so it is never auto-persisted -- fail closed by
    # default rather than trust an unrecognized shape.
    return False


def check_startup_durability(
    *, repo_root, checkpoint_dir, branch, campaign_seeds, final_generation,
    expansion_batch_id, generation_checkpoint_path, nominee_checkpoint_path,
    active_protocol_manifest_id, status_path,
) -> dict:
    """Returns {"outcome": "clean"} when there is nothing to do, or
    {"outcome": "repaired", "committed_paths": [...], "commit_sha": ...,
    "durable_progress": {...}} after successfully committing, pushing, and
    remote-verifying every orphan. Raises UnresolvedCheckpointDurability
    -- message always identifies the specific file(s) and a bounded
    recovery procedure -- when an orphan is invalid, ambiguous, or cannot
    be safely persisted.
    """
    debris = find_leftover_transaction_debris(repo_root)
    if debris:
        raise UnresolvedCheckpointDurability(
            f"checkpoint transaction debris survived recover_pending_transaction: "
            f"{debris} at {repo_root} -- this should never happen (that function "
            "either finishes a pending transaction or fully cleans up inert "
            "pre-manifest debris). Bounded recovery: inspect the listed file(s) by "
            "hand. If a transaction manifest is present and its staged artifacts "
            "hash-verify, re-running recover_pending_transaction(repo_root) directly "
            "should finish it; otherwise this needs a human decision, not an "
            "automatic one -- do not delete or regenerate anything here."
        )

    anomalies = find_evolution_git_anomalies(repo_root)
    if not anomalies:
        return {"outcome": "clean"}

    anomaly_paths = sorted(rel for _, rel in anomalies)

    # Validate the augmented (orphan-including) checkpoint_dir through the
    # SAME validated precedence logic reconcile_status already relies on
    # (contiguity, malformation, ambiguous-tie fail-closed rules) -- never
    # a second, hand-rolled definition of "valid" that could drift from
    # the tested one. Filename or successful JSON parsing alone is never
    # sufficient; this actually walks the chain.
    try:
        durable = derive_durable_progress(
            checkpoint_dir=checkpoint_dir, campaign_seeds=campaign_seeds,
            final_generation=final_generation, expansion_batch_id=expansion_batch_id,
            generation_checkpoint_path=generation_checkpoint_path,
            nominee_checkpoint_path=nominee_checkpoint_path,
        )
    except StateReconstructionError as exc:
        raise UnresolvedCheckpointDurability(
            f"orphan checkpoint(s) {anomaly_paths} are present on disk but not "
            f"committed, and fail validation against the durable-evidence chain: "
            f"{exc}. Bounded recovery: do not commit these files as-is. Compare each "
            "against the last known-good committed generation/nominee checkpoint by "
            "hand; if genuinely corrupt or superseded, a human must decide whether to "
            "discard it -- this check never deletes, overwrites, or regenerates "
            "checkpoint content itself."
        ) from exc

    not_incorporated = [
        rel for rel in anomaly_paths
        if not _orphan_actually_incorporated(rel, durable, campaign_seeds)
    ]
    if not_incorporated:
        raise UnresolvedCheckpointDurability(
            f"orphan checkpoint(s) {not_incorporated} did not raise a chain error, but "
            f"are not actually reflected in the derived durable state (world_bank_id="
            f"{durable['world_bank_id']!r}, generation={durable['generation']!r}, "
            f"campaigns_complete={durable['campaigns_complete']!r}) -- this can happen "
            "when a malformed file has an earlier valid generation to silently fall "
            "back to, or when the file type is not one this check validates. Bounded "
            "recovery: inspect the listed file(s) by hand against the last known-good "
            "committed checkpoint; never commit an orphan this check cannot positively "
            "confirm is incorporated."
        )

    # An orphan that derives cleanly may still have been computed under a
    # protocol manifest that has since been superseded. reconcile_status
    # applies exactly this standard to a present STATUS.json; applied here
    # too, since this check runs first and must not let that slip through.
    status_path = Path(status_path)
    if status_path.exists():
        try:
            existing_manifest = json.loads(status_path.read_text()).get("protocol_manifest")
        except (OSError, json.JSONDecodeError):
            existing_manifest = None
        manifest_id = active_protocol_manifest_id()
        if existing_manifest is not None and existing_manifest != manifest_id:
            raise UnresolvedCheckpointDurability(
                f"orphan checkpoint(s) {anomaly_paths} pass the durable-evidence "
                f"chain, but STATUS.json records protocol_manifest="
                f"{existing_manifest!r}, not the active {manifest_id!r}. Refusing to "
                "commit output that may have been computed under superseded rules. "
                "Bounded recovery: confirm by hand which protocol manifest actually "
                "governed this run before deciding whether the orphan is still valid."
            )

    paths = [Path(repo_root) / rel for rel in anomaly_paths]
    try:
        result = commit_and_push(
            repo_root, branch, paths,
            "Recover durable checkpoint(s) orphaned by a prior crash between local "
            "persistence and git commit "
            "[checkpoint-durability startup check, not a new unit of work]",
        )
    except GitCadenceError as exc:
        raise UnresolvedCheckpointDurability(
            f"orphan checkpoint(s) {anomaly_paths} are valid but could not be "
            f"committed/pushed/remote-verified: {exc}. Bounded recovery: resolve the "
            "underlying git failure by hand (e.g. a real remote divergence needs a "
            "human decision), then let the next invocation retry -- this check never "
            "force-pushes and never retries automatically."
        ) from exc

    return {
        "outcome": "repaired", "committed_paths": anomaly_paths,
        "durable_progress": durable, **result,
    }
