"""Deterministic runtime-state reconstruction from durable artifacts.

Rick, 2026-09-29 ("GO -- TBOTS CLOUD V1 STATE SURVIVABILITY FIX"): STATUS.json
is local-only (fitness_v2_worker.py never passes it to worker_git.commit_
and_push, at any phase -- every write_status() call site was read and none
does) and does not survive instance replacement. evolution/state/*.json
(world bank + generation/nominee/admission checkpoints, all git-committed
and push-verified) and the active complete-protocol manifest remain the
sole canonical record of completed work. This module reconstructs the
minimal STATUS.json-shaped runtime state needed to resume correctly from
that durable evidence alone, and refuses (raises StateReconstructionError;
the caller converts this into a fail-closed WorkerStop) rather than
guessing whenever durable evidence is internally contradictory, or
disagrees with a present STATUS.json in a direction that cannot be safely
resolved by trusting the more-advanced side.

Precedence, in order:
  1. A STATUS.json recording progress under a DIFFERENT protocol manifest
     than the one currently active is never trusted -- fail closed. Rules,
     seeds, or calibration may have changed underneath it.
  2. A STATUS.json pointing at a world_bank_id that does not durably exist
     as a valid checkpoint, or whose recorded synthetic_world_count
     disagrees with that checkpoint's own content, is never trusted --
     fail closed.
  3. Durable evolution/state/*.json artifacts are canonical for completed
     work. A STATUS.json that is BEHIND what they prove is repaired to
     match, never the reverse.
  4. A STATUS.json that claims progress (or an identity -- world_bank_id/
     campaign_seed) durable artifacts do not support at the same rank is
     never trusted -- fail closed. There is no legitimate code path in
     fitness_v2_worker.py that ever advances STATUS.json before its
     matching checkpoint is committed and push-verified, so this can only
     mean corruption, tampering, or a manual mistake -- not a case to
     resolve automatically.
  5. A STATUS.json that already agrees with durable evidence is returned
     unchanged (this is what makes reconstruction idempotent across
     repeated invocations with no intervening progress).

Only genuinely *reconstructs* progress fields (world_bank_id,
synthetic_world_count, campaigns_complete, campaign_seed, generation,
rank1_qualifiers) plus protocol_manifest -- these are the only fields
fitness_v2_worker.py's control flow (_advance, _resume_campaign_state)
ever branches on. Every other STATUS.json field (state, phase, stop_code,
audit_window_ends_utc, head_commit_parent, freeze_commit, ...) is either
carried forward from an existing STATUS.json, taken from the frozen
RECOVERY_CONTRACT (worker_recovery_contract.py) when there is no existing
STATUS.json to carry anything forward from, or freshly measured
(disk_used_bytes/disk_free_bytes) -- none of it is load-bearing for resume
correctness. It is observability only: run_one_step's own live gate checks
(disk_gate, check_audit_gate) re-derive real conditions from the actual
environment on every single invocation regardless of what STATUS.json
says, and the one state value that IS branched on directly ("hold") is
only ever produced by _expand_and_continue hitting the frozen 32-world
ceiling -- re-deriving it as "running" here just means one extra, fully
idempotent pass through _finalize_or_expand before it is correctly
re-set, never an unsafe skip.
"""
from __future__ import annotations

import json
from pathlib import Path

from worker_disk import disk_snapshot
from worker_recovery_contract import RECOVERY_CONTRACT
from worker_status import build_status, read_status, with_updates, write_status


class StateReconstructionError(Exception):
    pass


def _read_json_if_valid(path: Path):
    """None on any read/parse failure -- callers treat that as "not
    durably usable right now", never as a crash. A local read failure is
    not proof the durable git history is bad; refusing to trust it and
    falling back (or failing closed) is always the safe choice here."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _world_bank_synthetic_count(content) -> int | None:
    if not isinstance(content, dict):
        return None
    synthetic = content.get("synthetic")
    if not isinstance(synthetic, dict):
        return None
    try:
        return sum(len(worlds) for worlds in synthetic.values())
    except TypeError:
        return None


def derive_world_bank_progress(checkpoint_dir):
    """(world_bank_id, synthetic_world_count) for the highest-count valid
    world-bank checkpoint on disk, or (None, None) if none exists. Raises
    if two distinct files claim the same highest count -- genuinely
    ambiguous, never picked arbitrarily."""
    candidates = []
    for path in sorted(Path(checkpoint_dir).glob("world_bank_v1*.json")):
        count = _world_bank_synthetic_count(_read_json_if_valid(path))
        if count is not None:
            candidates.append((count, path.stem))
    if not candidates:
        return None, None
    top = max(count for count, _ in candidates)
    tied = sorted(world_bank_id for count, world_bank_id in candidates if count == top)
    if len(tied) > 1:
        raise StateReconstructionError(
            f"ambiguous durable world-bank state: {tied} all claim "
            f"synthetic_world_count={top}"
        )
    return tied[0], top


def derive_campaign_progress(
    *, checkpoint_dir, batch_id, campaign_seeds, final_generation,
    generation_checkpoint_path, nominee_checkpoint_path,
):
    """(campaigns_complete, campaign_seed, generation, rank1_qualifiers)
    for one world-bank batch, purely from checkpoint files present on disk.
    campaign_seed/generation are None once every campaign_seeds entry has a
    valid nominee checkpoint -- the whole batch is evaluated, nothing left
    to resume mid-campaign."""
    campaigns_complete = 0
    rank1_qualifiers = 0
    for seed in campaign_seeds:
        content = _read_json_if_valid(nominee_checkpoint_path(batch_id, seed))
        if content is None or "clears_all_gates" not in content:
            break
        campaigns_complete += 1
        if content["clears_all_gates"]:
            rank1_qualifiers += 1

    for seed in campaign_seeds[campaigns_complete:]:
        content = _read_json_if_valid(nominee_checkpoint_path(batch_id, seed))
        if content is not None and "clears_all_gates" in content:
            raise StateReconstructionError(
                f"non-contiguous nominee checkpoints in batch {batch_id}: seed {seed} "
                "has a valid checkpoint but an earlier campaign's does not"
            )

    if campaigns_complete == len(campaign_seeds):
        return campaigns_complete, None, None, rank1_qualifiers

    seed = campaign_seeds[campaigns_complete]
    generation = None
    for g in range(final_generation + 1):
        path = generation_checkpoint_path(batch_id, seed, g)
        if not path.exists():
            break
        content = _read_json_if_valid(path)
        valid = (
            content is not None and content.get("generation") == g
            and (("next_population" in content) if g < final_generation else ("final_ranked" in content))
        )
        if not valid:
            if generation is None:
                # A checkpoint file exists at this position but isn't usable,
                # and there is no earlier valid generation to fall back to --
                # this is indistinguishable from "generation g was really
                # completed and durably committed, and only this LOCAL copy
                # is corrupted" versus "the campaign never really got this
                # far". Never guess which; fail closed (HOLD).
                raise StateReconstructionError(
                    f"generation {g} checkpoint for batch {batch_id} seed {seed} exists "
                    "but is malformed/incomplete, and no earlier valid generation exists "
                    "to fall back to -- refusing to guess whether it was ever durably "
                    "completed"
                )
            break
        generation = g

    resume_floor = -1 if generation is None else generation
    for g in range(resume_floor + 1, final_generation + 1):
        content = _read_json_if_valid(generation_checkpoint_path(batch_id, seed, g))
        valid = (
            content is not None and content.get("generation") == g
            and (("next_population" in content) if g < final_generation else ("final_ranked" in content))
        )
        if valid:
            raise StateReconstructionError(
                f"generation {g} checkpoint is valid but generation {resume_floor} "
                f"(the latest provable resume point) is not -- non-contiguous durable "
                f"state for batch {batch_id} seed {seed}"
            )

    # Real STATUS.json semantics (both _expand_and_continue and
    # _generate_world_bank): campaign_seed is None until that campaign's
    # FIRST generation is actually checkpointed, even though
    # CAMPAIGN_SEEDS[campaigns_complete] is already well-defined -- match
    # that exactly, or a freshly-generated/expanded world bank with zero
    # generation checkpoints yet would reconstruct a non-null campaign_seed
    # a real, intact STATUS.json would never show at that point.
    if generation is None:
        seed = None

    return campaigns_complete, seed, generation, rank1_qualifiers


def derive_durable_progress(
    *, checkpoint_dir, campaign_seeds, final_generation,
    expansion_batch_id, generation_checkpoint_path, nominee_checkpoint_path,
):
    """The full durable-evidence-only progress dict. Never reads
    STATUS.json; purely a function of what's on disk in checkpoint_dir."""
    world_bank_id, synthetic_world_count = derive_world_bank_progress(checkpoint_dir)
    if world_bank_id is None:
        return {
            "world_bank_id": None, "synthetic_world_count": 0,
            "campaigns_complete": 0, "campaign_seed": None, "generation": None,
            "rank1_qualifiers": 0,
        }
    try:
        batch_id = expansion_batch_id(synthetic_world_count)
    except ValueError as exc:
        # A durably-committed world bank can never actually have an
        # off-boundary count in production (_generate_world_bank/
        # _expand_and_continue each commit the whole family set as one
        # atomic checkpoint -- there is no partially-written world bank to
        # discover), so this can only mean local corruption of the one file
        # that won the highest-count selection above. Fail closed rather
        # than crash with a raw ValueError.
        raise StateReconstructionError(
            f"world bank {world_bank_id!r} has synthetic_world_count="
            f"{synthetic_world_count}, not a valid expansion-batch boundary: {exc}"
        ) from exc
    campaigns_complete, campaign_seed, generation, rank1_qualifiers = derive_campaign_progress(
        checkpoint_dir=checkpoint_dir, batch_id=batch_id, campaign_seeds=campaign_seeds,
        final_generation=final_generation,
        generation_checkpoint_path=generation_checkpoint_path,
        nominee_checkpoint_path=nominee_checkpoint_path,
    )
    return {
        "world_bank_id": world_bank_id, "synthetic_world_count": synthetic_world_count,
        "campaigns_complete": campaigns_complete, "campaign_seed": campaign_seed,
        "generation": generation, "rank1_qualifiers": rank1_qualifiers,
    }


def _progress_rank(d: dict) -> tuple:
    return (
        d.get("synthetic_world_count") if d.get("synthetic_world_count") is not None else -1,
        d.get("campaigns_complete") or 0,
        d.get("generation") if d.get("generation") is not None else -1,
    )


def _build_reconstructed_status(*, durable, manifest_id, base, repo_root, now_utc):
    disk = disk_snapshot(repo_root)
    if base is not None:
        carry = base
    else:
        carry = {
            "schema": "tbots-status-v1",
            "audit_window_ends_utc": RECOVERY_CONTRACT["audit_window_ends_utc"],
            "head_commit_parent": RECOVERY_CONTRACT["head_commit_parent"],
            "freeze_commit": RECOVERY_CONTRACT["freeze_commit"],
            "research_champion": None, "negative_result": False,
            "recoveries": 0, "last_generation_seconds": None,
        }

    # phase is purely descriptive/observational (never branched on --
    # confirmed by reading every status.get("phase")/status["phase"] site in
    # fitness_v2_worker.py: there are none); best-effort only.
    if durable["world_bank_id"] is None:
        phase = "protocol_freeze"
    elif durable["generation"] is not None:
        phase = "campaign"
    elif durable["campaigns_complete"] > 0:
        # campaign_seed is None here only because every campaign in this
        # batch already has a valid nominee checkpoint (see
        # derive_campaign_progress) -- i.e. ready for _finalize_or_expand.
        phase = "champion_decision"
    else:
        phase = "world_bank"

    return build_status(
        schema="tbots-status-v1",
        updated_utc=now_utc,
        head_commit_parent=carry["head_commit_parent"],
        state="running",
        phase=phase,
        protocol_manifest=manifest_id,
        freeze_commit=carry["freeze_commit"],
        audit_window_ends_utc=carry["audit_window_ends_utc"],
        world_bank_id=durable["world_bank_id"],
        synthetic_world_count=durable["synthetic_world_count"],
        campaign=(None if durable["campaign_seed"] is None else durable["campaigns_complete"] + 1),
        campaign_seed=durable["campaign_seed"],
        generation=durable["generation"],
        campaigns_complete=durable["campaigns_complete"],
        rank1_qualifiers=durable["rank1_qualifiers"],
        research_champion=carry["research_champion"],
        negative_result=carry["negative_result"],
        last_progress_utc=now_utc,
        stop_code=None,
        stop_reason=None,
        recoveries=carry["recoveries"] + 1,
        last_generation_seconds=carry["last_generation_seconds"],
        disk_used_bytes=disk["disk_used_bytes"],
        disk_free_bytes=disk["disk_free_bytes"],
    )


def reconcile_status(
    *, repo_root, status_path, checkpoint_dir, campaign_seeds, final_generation,
    expansion_batch_id, generation_checkpoint_path, nominee_checkpoint_path,
    active_protocol_manifest_id, now_utc,
) -> dict:
    """The single entry point run_one_step() calls in place of the bare
    `read_status(STATUS_PATH) if STATUS_PATH.exists() else None`. Always
    returns a validated status dict, or raises StateReconstructionError."""
    status_path = Path(status_path)
    durable = derive_durable_progress(
        checkpoint_dir=checkpoint_dir, campaign_seeds=campaign_seeds,
        final_generation=final_generation, expansion_batch_id=expansion_batch_id,
        generation_checkpoint_path=generation_checkpoint_path,
        nominee_checkpoint_path=nominee_checkpoint_path,
    )
    manifest_id = active_protocol_manifest_id()

    local = None
    if status_path.exists():
        try:
            local = read_status(status_path)
        except Exception:
            local = None  # unreadable/malformed STATUS.json -- treated as missing

    if local is not None:
        if local.get("protocol_manifest") != manifest_id:
            raise StateReconstructionError(
                f"STATUS.json protocol_manifest {local.get('protocol_manifest')!r} != "
                f"active manifest {manifest_id!r} -- refusing to trust recorded progress "
                "under a superseded protocol"
            )

        claimed_id = local.get("world_bank_id")
        if claimed_id is not None and claimed_id != durable["world_bank_id"]:
            claimed_count = _world_bank_synthetic_count(
                _read_json_if_valid(Path(checkpoint_dir) / f"{claimed_id}.json")
            )
            if claimed_count is None or claimed_count != local.get("synthetic_world_count"):
                raise StateReconstructionError(
                    f"STATUS.json claims world_bank_id {claimed_id!r} with "
                    f"synthetic_world_count={local.get('synthetic_world_count')!r}, which does "
                    "not durably exist as a matching, valid checkpoint"
                )

        local_rank = _progress_rank(local)
        durable_rank = _progress_rank(durable)

        if local_rank == durable_rank:
            if (local.get("world_bank_id") == durable["world_bank_id"]
                    and local.get("campaign_seed") == durable["campaign_seed"]):
                return local  # already consistent -- idempotent, nothing to repair
            raise StateReconstructionError(
                "STATUS.json and durable evolution/state/ artifacts agree on progress "
                f"rank {local_rank} but disagree on identity: local world_bank_id="
                f"{local.get('world_bank_id')!r}/campaign_seed={local.get('campaign_seed')!r} "
                f"vs durable world_bank_id={durable['world_bank_id']!r}/"
                f"campaign_seed={durable['campaign_seed']!r}"
            )

        if local_rank > durable_rank:
            raise StateReconstructionError(
                f"STATUS.json claims progress rank {local_rank} beyond what durable "
                f"evolution/state/ artifacts support ({durable_rank})"
            )
        # local_rank < durable_rank: durable is unambiguously ahead -- it
        # wins; fall through to reconstruct and repair local.

    reconstructed = _build_reconstructed_status(
        durable=durable, manifest_id=manifest_id, base=local,
        repo_root=repo_root, now_utc=now_utc,
    )
    write_status(status_path, reconstructed)
    return reconstructed
