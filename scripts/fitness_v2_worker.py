"""Fitness V2 autonomous worker entry point.

Wires worker_lock/worker_disk/worker_gate/worker_checkpoint/worker_status/
worker_git around fitness_v2_world_bank and fitness_v2_campaign. This is the
only code path authorized to touch real DEVELOPMENT data and produce
result-bearing artifacts -- and even it refuses to unless the audit gate's
outcome is "ready" (worker_gate.check_audit_gate: window elapsed, remote
fetched and fast-forwarded, no FREEZE_AUDIT_HOLD.md).

Design: one invocation performs ONE unit of work -- generate the world bank;
or run one campaign to completion (all its generations, each checkpointed
individually); or decide admission once all campaigns are done; or one
expansion step -- and exits. This is what makes systemd-timer-driven,
crash-safe, HOST-independent operation possible: kill the process or reboot
the machine at any point, and the next invocation recovers the one pending
checkpoint transaction (if any, via worker_checkpoint's own crash-safety
guarantee) and resumes from the last durable STATUS.json/checkpoint state,
never re-running a completed generation. Every unit of work is committed
and pushed before the process returns (Addendum A §1: "push after every
commit, not at the end").

The world bank itself is persisted as one checkpoint artifact (all 19
worlds' full arrays) precisely so a later process invocation -- including
one recovering from a crash mid-campaign -- never has to regenerate or
re-derive it; it reloads the same, already-admitted worlds byte-for-byte.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fitness_v2 import decide_admission, validate_parameter_freeze
from fitness_v2_campaign import (
    evaluate_genome_against_worlds, initial_population, nominate_campaign,
    reference_passive_sharpes, run_generation,
)
from fitness_v2_evolution_protocol import FINAL_GENERATION
from fitness_v2_protocol import development_reference_calibration, world_descriptor_vector
from fitness_v2_world_bank import (
    SYNTHETIC_FAMILIES, ExpansionHeld, build_synthetic_family,
    distributional_world, execution_world, expand_family, historical_world,
    load_complete_protocol, load_real_development_pool, scored_only,
    sequence_world, shock_world,
)
from worker_checkpoint import commit_checkpoint, recover_pending_transaction
from worker_disk import disk_gate
from worker_gate import check_audit_gate
from worker_git import commit_and_push
from worker_lock import LockHeldElsewhere, worker_lock
from worker_status import read_status, with_updates, write_status

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = REPO_ROOT / "STATUS.json"
CHECKPOINT_DIR = REPO_ROOT / "evolution" / "state"
PARAMETER_FREEZE_PATH = (
    REPO_ROOT / "evolution" / "protocol"
    / "fitness_v2_parameter_freeze_47aba93e384f50836713b7c4e4723ddf858d6c8eda8da363c391d9bbb261cfac.json"
)
BRANCH = "feat/node-resident-fitness-v2-autonomous-evolution-20260925"
CAMPAIGN_SEEDS = [2066557696, 604610261, 3608585586, 3251376561, 1894202052]


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WorkerStop(Exception):
    def __init__(self, stop_code: str, reason: str):
        self.stop_code = stop_code
        self.reason = reason
        super().__init__(reason)


def load_parameter_freeze() -> dict:
    # Unlike fitness_v2_evolution_protocol/fitness_v2_formula_definitions's
    # {"manifest_id": ..., "content": {...}} envelope convention, the
    # parameter-freeze file is flat: manifest_id sits alongside the actual
    # fields (synthetic_families, campaign_batch, ...), and
    # validate_parameter_freeze (fitness_v2.py) content-addresses it that
    # way -- there is no separate "content" key to unwrap.
    manifest = json.loads(PARAMETER_FREEZE_PATH.read_text())
    validate_parameter_freeze(manifest)  # fails closed on any drift from Rick's freeze
    return manifest


def _checkpoint_and_push(label: str, artifacts: dict, message: str) -> None:
    """artifacts: {repo-relative-path-from-REPO_ROOT: json_serializable_value},
    e.g. {"evolution/state/world_bank.json": {...}}. commit_checkpoint's own
    `target` is REPO_ROOT (not CHECKPOINT_DIR) so that its allowed_prefixes
    check (each artifact key's first path segment) matches these full
    repo-relative keys, not paths re-relativized against CHECKPOINT_DIR."""
    commit_checkpoint(REPO_ROOT, label, artifacts, allowed_prefixes={"evolution"})
    paths = [REPO_ROOT / rel for rel in artifacts]
    commit_and_push(REPO_ROOT, BRANCH, list(dict.fromkeys(paths)), message)


def _world_bank_path(world_bank_id: str) -> Path:
    # Every world-bank generation (initial + each expansion, handoff §50)
    # is its own immutable checkpoint artifact, never overwritten in place --
    # worker_checkpoint refuses to silently replace an existing path's
    # content, by design. world_bank_id is what STATUS.json points at the
    # CURRENT one with.
    return CHECKPOINT_DIR / f"{world_bank_id}.json"


def _serialize_worlds(historical, synthetic) -> dict:
    return {
        "historical": historical,
        "synthetic": {family: result["worlds"] for family, result in synthetic.items()},
        "synthetic_state": {
            family: {
                "consumed_indices": result["consumed_indices"], "used_seeds": result["used_seeds"],
                "attempts": result["attempts"],
            }
            for family, result in synthetic.items()
        },
    }


def _load_world_bank(world_bank_id: str) -> dict:
    return json.loads(_world_bank_path(world_bank_id).read_text())


def run_one_step() -> dict:
    """Acquires no lock itself -- the caller (the systemd-invoked entry
    point, see fitness_v2_worker_main) is responsible for worker_lock, so a
    single acquisition spans exactly one invocation's disk/gate checks and
    unit of work. Returns a small report dict; raises WorkerStop for any
    condition requiring the worker to halt."""
    recover_pending_transaction(REPO_ROOT)

    disk = disk_gate(REPO_ROOT)
    if disk["outcome"] == "stopped":
        raise WorkerStop(disk["stop_code"], disk["detail"])

    status = read_status(STATUS_PATH) if STATUS_PATH.exists() else None
    audit_window_ends_utc = status["audit_window_ends_utc"] if status else None
    gate = check_audit_gate(REPO_ROOT, BRANCH, audit_window_ends_utc)
    if gate["outcome"] == "idle":
        return {"action": "waiting_on_audit_window", "detail": gate["detail"]}
    if gate["outcome"] == "stopped":
        raise WorkerStop(gate["stop_code"], gate["detail"])

    # A "hold" is a deliberate, recorded pause (the 32-world expansion
    # ceiling, or a scientific-protocol ambiguity fail-closed per AGENTS.md
    # "do not invent a rule") -- not an error. Idle here rather than
    # re-entering _finalize_or_expand, which would otherwise re-attempt
    # (and, for the ceiling case, re-raise) the same held step on every
    # timer firing. Only a fresh Rick GO that rewrites STATUS.json clears it.
    if status is not None and status.get("state") == "hold":
        return {"action": "holding", "detail": status.get("stop_reason")}

    return _advance(status)


def _advance(status: dict) -> dict:
    if status is None or status.get("world_bank_id") is None:
        return _generate_world_bank(status)
    if status.get("campaigns_complete", 0) < len(CAMPAIGN_SEEDS):
        return _run_next_campaign(status)
    return _finalize_or_expand(status)


# ---------------------------------------------------------------------------
# World-bank generation (real data; runs exactly once per experiment)
# ---------------------------------------------------------------------------

def _world_bank_build_context(protocol: dict, pool, historical: list[dict]):
    """Shared setup for both initial world-bank generation and later
    expansion (handoff §50) -- the candidate builder and calibration
    reference must be rebuilt identically both times (same frozen protocol,
    same 3 historical worlds, same DEVELOPMENT reconstruction pool), or an
    expansion world would not be reproducible under the same rules as the
    initial 16. Returns (tau, reference, builder)."""
    tau = protocol["gap_threshold_tau"]
    reference = development_reference_calibration([scored_only(w) for w in historical], tau)

    from fitness_v2_world_bank import _development_pool_for_reconstruction
    recon_pool = _development_pool_for_reconstruction(
        pool, protocol["development_boundary"]["start"], protocol["development_boundary"]["end"]
    )
    scored_length = protocol["h_shapes"]["dates"]["H1"]["n_sessions"]
    target_length = 378 + scored_length
    anchor = historical[0]

    def builder(family):
        def build(seed):
            if family == "distributional":
                return distributional_world(recon_pool, anchor, seed, target_length)
            if family == "execution":
                return execution_world(anchor)
            if family == "sequence":
                return sequence_world(recon_pool, anchor, seed, scored_length)
            if family == "shock":
                # Donor pool: pooled |gap| exceedances from the calibrated
                # DEVELOPMENT gap set, paired with an (unused) asset index --
                # apply_shock_event only consumes the signed gap value.
                donor_pool = [(0, g) for g in _pooled_gap_sample(recon_pool, tau)]
                extrema = tuple(protocol["gap_extrema"])
                return shock_world(anchor, seed, donor_pool, extrema)
            raise ValueError(family)
        return build

    return tau, reference, builder


def _generate_world_bank(status: dict | None) -> dict:
    protocol = load_complete_protocol()
    freeze = load_parameter_freeze()
    pool = load_real_development_pool()

    historical = [historical_world(pool, shape, protocol) for shape in ("H1", "H2", "H3")]
    tau, reference, builder = _world_bank_build_context(protocol, pool, historical)

    synthetic = {}
    admitted_vectors: list[list[float]] = []
    for family in SYNTHETIC_FAMILIES:
        result = build_synthetic_family(
            family, slot_count=4, initial_seeds=freeze["synthetic_families"][family]["seeds"],
            build_candidate=builder(family), reference=reference,
            admitted_reference_vectors=admitted_vectors, tau=tau,
        )
        synthetic[family] = result

    bank = _serialize_worlds(historical, synthetic)
    world_bank_id = "world_bank_v1"
    _checkpoint_and_push(
        "world_bank", {f"evolution/state/{world_bank_id}.json": bank},
        "World bank generated: 3 historical + 16 synthetic worlds (handoff §11)",
    )
    base = status or read_status(STATUS_PATH)
    updated = with_updates(
        base, updated_utc=_now_utc(), phase="world_bank", world_bank_id=world_bank_id,
        synthetic_world_count=16, last_progress_utc=_now_utc(),
    )
    write_status(STATUS_PATH, updated)
    return {"action": "generated_world_bank"}


def _pooled_gap_sample(recon_pool, tau, limit=64):
    """A bounded sample of real DEVELOPMENT gap magnitudes exceeding tau,
    for Shock's donor pool -- not a full recomputation of the frozen
    calibration's own pooled-gap statistics (already captured in tau/
    gap_extrema), just enough distinct real exceedances to draw from."""
    gaps = []
    for rows in recon_pool.values():
        for row in rows:
            gap = row["raw_open"] / row["prev_raw_close"] - 1.0
            if abs(gap) > tau:
                gaps.append(gap)
            if len(gaps) >= limit:
                return gaps
    return gaps or [tau * 1.5, -tau * 1.5]


# ---------------------------------------------------------------------------
# Campaign execution (runs each of the 5 frozen campaigns to completion)
# ---------------------------------------------------------------------------

def _worlds_from_bank(bank: dict) -> tuple[list[dict], dict]:
    return bank["historical"], bank["synthetic"]


def _expansion_batch_id(synthetic_world_count: int) -> int:
    """Handoff §50 / Rick's 2026-09-26 multi-batch freeze: batch 0 runs
    against the initial 16-world bank, batch 1 against 20, ... batch 4
    against the terminal 32. Derived from synthetic_world_count (already
    durably persisted in STATUS.json) rather than kept as a separate field,
    so there is exactly one source of truth for "which batch is this" --
    every checkpoint path below is namespaced by calling this, never by
    re-deriving the arithmetic inline."""
    if (synthetic_world_count - 16) % 4 != 0 or synthetic_world_count < 16:
        raise ValueError(f"synthetic_world_count {synthetic_world_count} is not a valid batch boundary")
    return (synthetic_world_count - 16) // 4


def _generation_checkpoint_path(batch_id: int, seed: int, generation: int) -> Path:
    # protocol_id -> world_bank_id -> expansion_batch_id -> campaign_seed ->
    # generation (Rick's namespace freeze): the protocol/world-bank identity
    # is already load-bearing via world_bank_id's own filename; batch_id
    # here is what makes an identical (seed, generation) pair collision-proof
    # across batches, since CAMPAIGN_SEEDS are deliberately reused every batch.
    return CHECKPOINT_DIR / f"batch{batch_id}_campaign_{seed}_generation_{generation}.json"


def _nominee_checkpoint_path(batch_id: int, seed: int) -> Path:
    return CHECKPOINT_DIR / f"batch{batch_id}_campaign_{seed}_nominee.json"


def _admission_decision_path(batch_id: int) -> Path:
    return CHECKPOINT_DIR / f"batch{batch_id}_admission_decision.json"


def _resume_campaign_state(status: dict, batch_id: int, seed: int):
    """Gap-1 crash/resume (handoff §58: "Never reconstruct completed
    generations just because a process died. Resume from the latest durable
    transactional checkpoint."). Returns (resume_from, population,
    used_genome_ids, final_ranked) -- either a fresh Gen0 start, or state
    reloaded from the last generation this campaign_seed durably
    checkpointed, so the loop below never repeats an already-committed
    generation."""
    if status.get("campaign_seed") == seed and status.get("generation") is not None:
        last_generation = status["generation"]
        checkpoint = json.loads(_generation_checkpoint_path(batch_id, seed, last_generation).read_text())
        if last_generation < FINAL_GENERATION:
            used_genome_ids = set(checkpoint["used_genome_ids"])
            return last_generation + 1, checkpoint["next_population"], used_genome_ids, None
        # Final generation already checkpointed (including its full ranked
        # population + evaluations) -- only nomination/admission remain;
        # the generation loop below runs zero times.
        return FINAL_GENERATION + 1, None, None, checkpoint["final_ranked"]

    population = initial_population(seed)
    used_genome_ids = {m["genome_id"] for m in population}
    return 0, population, used_genome_ids, None


def _run_next_campaign(status: dict) -> dict:
    batch_id = _expansion_batch_id(status["synthetic_world_count"])
    bank = _load_world_bank(status["world_bank_id"])
    historical, synthetic = _worlds_from_bank(bank)
    reference_sharpes = {"execution": reference_passive_sharpes(historical)}

    seed = CAMPAIGN_SEEDS[status["campaigns_complete"]]
    resume_from, population, used_genome_ids, final_ranked = _resume_campaign_state(status, batch_id, seed)

    for generation in range(resume_from, FINAL_GENERATION + 1):
        result = run_generation(
            seed, generation, population, historical, synthetic, reference_sharpes, used_genome_ids,
        )
        artifact = {
            "generation": generation,
            "ranked_genome_ids": result["ranked_genome_ids"],
            "best_cross_family_fitness": result["best_cross_family_fitness"],
            "median_cross_family_fitness": result["median_cross_family_fitness"],
            "control_anchor_fitness": result["control_anchor_fitness"],
        }
        # Enough state to resume at generation+1 (or, at the final
        # generation, to nominate/admit) without ever recomputing an
        # already-checkpointed generation -- see _resume_campaign_state.
        if generation < FINAL_GENERATION:
            artifact["next_population"] = result["next_population"]
            artifact["used_genome_ids"] = sorted(used_genome_ids)
        else:
            artifact["final_ranked"] = result["final_ranked"]
        generation_path = _generation_checkpoint_path(batch_id, seed, generation)
        _checkpoint_and_push(
            f"batch{batch_id}_campaign_{seed}_gen{generation}",
            {str(generation_path.relative_to(REPO_ROOT)): artifact},
            f"Batch {batch_id} campaign {seed} generation {generation}: evaluated, ranked, "
            f"{'final' if generation == FINAL_GENERATION else 'bred next population'}",
        )
        updated = with_updates(
            read_status(STATUS_PATH), updated_utc=_now_utc(), phase="campaign",
            campaign=CAMPAIGN_SEEDS.index(seed) + 1, campaign_seed=seed, generation=generation,
            last_progress_utc=_now_utc(),
        )
        write_status(STATUS_PATH, updated)
        if generation < FINAL_GENERATION:
            population = result["next_population"]
        else:
            final_ranked = result["final_ranked"]

    nominee = nominate_campaign(final_ranked)
    admission_worlds = [
        {"family": "historical", "world_id": f"historical_{i}", "world": w}
        for i, w in enumerate(historical)
    ]
    for family, worlds in synthetic.items():
        for slot, world in enumerate(worlds, start=1):
            admission_worlds.append({"family": family, "world_id": f"{family}_{slot}", "world": world})
    winner_genome = next(m["genome"] for m in final_ranked if m["genome_id"] == nominee["genome_id"])
    full_evaluation = evaluate_genome_against_worlds(winner_genome, admission_worlds, reference_sharpes)
    full_nominee = {
        "genome_id": nominee["genome_id"], "evolution_seed": seed,
        "clears_all_gates": full_evaluation["eligible"] is True,
        "cross_family_fitness": full_evaluation["cross_family_fitness"],
    }

    campaigns_complete = status["campaigns_complete"] + 1
    nominee_path = _nominee_checkpoint_path(batch_id, seed)
    _checkpoint_and_push(
        f"batch{batch_id}_campaign_{seed}_nominee",
        {str(nominee_path.relative_to(REPO_ROOT)): full_nominee},
        f"Batch {batch_id} campaign {seed}: rank-1 nominee evaluated against full admission set "
        f"(clears_all_gates={full_nominee['clears_all_gates']})",
    )
    updated = with_updates(
        read_status(STATUS_PATH), updated_utc=_now_utc(), phase="campaign",
        campaign=None, campaign_seed=None, generation=None,
        campaigns_complete=campaigns_complete,
        rank1_qualifiers=status["rank1_qualifiers"] + (1 if full_nominee["clears_all_gates"] else 0),
        last_progress_utc=_now_utc(),
    )
    write_status(STATUS_PATH, updated)
    return {"action": "completed_campaign", "campaign_seed": seed, "nominee": full_nominee}


# ---------------------------------------------------------------------------
# Final admission decision + world-bank expansion
# ---------------------------------------------------------------------------

def _finalize_or_expand(status: dict) -> dict:
    batch_id = _expansion_batch_id(status["synthetic_world_count"])
    nominees = []
    for seed in CAMPAIGN_SEEDS:
        record = json.loads(_nominee_checkpoint_path(batch_id, seed).read_text())
        nominees.append({
            "genome_id": record["genome_id"], "evolution_seed": record["evolution_seed"],
            "clears_all_gates": record["clears_all_gates"],
        })
    admission = decide_admission(nominees, predeclared_n=len(CAMPAIGN_SEEDS))

    admission_path = _admission_decision_path(batch_id)
    _checkpoint_and_push(
        f"batch{batch_id}_admission_decision",
        {str(admission_path.relative_to(REPO_ROOT)): admission},
        f"Batch {batch_id} 5-campaign admission decision ({status['synthetic_world_count']} synthetic "
        f"worlds): {admission['ratio']}, admitted={admission['admitted']} (handoff §47). "
        f"Preserved as batch {batch_id}'s own immutable result -- a later batch's outcome "
        "never discards or overwrites it (Rick's 2026-09-26 multi-batch freeze).",
    )
    updated = with_updates(
        read_status(STATUS_PATH), updated_utc=_now_utc(), phase="champion_decision",
        research_champion=admission.get("selected_genome_id"),
        negative_result=not admission["admitted"], last_progress_utc=_now_utc(),
    )
    write_status(STATUS_PATH, updated)
    return _expand_and_continue(updated, admission)


def _expand_and_continue(status: dict, admission: dict) -> dict:
    """Handoff §50 + Rick's 2026-09-26 multi-batch freeze
    (FITNESS_V2_EXPANSION_CAMPAIGN_CYCLE_FREEZE_20260926.md, resolving
    FITNESS_V2_EXPANSION_CAMPAIGN_CYCLE_STOP_20260926.md): after every
    completed 5-campaign batch, unconditionally -- "do not adapt world-bank
    difficulty based on candidate success/failure" -- add exactly one more
    world per family, continuing each family's own deterministic seed
    stream. A qualifying champion does NOT end the program early: every
    batch's admission decision (already checkpointed by _finalize_or_expand
    at its own immutable batch-namespaced path) stands regardless, and the
    worker resets campaign progress and proceeds straight into the next
    batch using the SAME frozen CAMPAIGN_SEEDS -- never a HOLD -- until the
    32-synthetic-world ceiling, which alone is the terminal HOLD."""
    bank = _load_world_bank(status["world_bank_id"])
    historical, synthetic = _worlds_from_bank(bank)
    protocol = load_complete_protocol()
    pool = load_real_development_pool()
    tau, reference, builder = _world_bank_build_context(protocol, pool, historical)

    admitted_vectors = [
        world_descriptor_vector(scored_only(world), tau)
        for family in SYNTHETIC_FAMILIES for world in synthetic[family]
    ]
    synthetic_state = bank["synthetic_state"]
    expanded = dict(synthetic)

    try:
        for family in SYNTHETIC_FAMILIES:
            state = synthetic_state[family]
            prior_state = {
                "worlds": expanded[family], "consumed_indices": state["consumed_indices"],
                "used_seeds": state["used_seeds"], "attempts": state.get("attempts", []),
            }
            result = expand_family(family, prior_state, builder(family), reference, admitted_vectors, tau)
            expanded[family] = result["worlds"]
            synthetic_state = {
                **synthetic_state,
                family: {
                    "consumed_indices": result["consumed_indices"],
                    "used_seeds": result["used_seeds"], "attempts": result["attempts"],
                },
            }
    except ExpansionHeld as held:
        updated = with_updates(
            status, updated_utc=_now_utc(), state="hold", phase="terminal_hold", stop_code=None,
            stop_reason=(
                "World bank reached the frozen 32-synthetic-world ceiling "
                f"(handoff §50): {held}. Terminal HOLD pending a new Rick GO."
            ),
            last_progress_utc=_now_utc(),
        )
        write_status(STATUS_PATH, updated)
        return {"action": "expansion_held", "detail": str(held), "admission": admission}

    new_bank = {"historical": historical, "synthetic": expanded, "synthetic_state": synthetic_state}
    new_count = sum(len(worlds) for worlds in expanded.values())
    new_batch_id = _expansion_batch_id(new_count)
    new_world_bank_id = f"world_bank_v1_batch{new_batch_id}"
    _checkpoint_and_push(
        f"world_bank_expansion_{new_count}",
        {f"evolution/state/{new_world_bank_id}.json": new_bank},
        f"World bank expanded to {new_count} synthetic worlds (handoff §50: "
        f"+1 per family after batch {_expansion_batch_id(status['synthetic_world_count'])}); "
        f"beginning batch {new_batch_id}",
    )
    updated = with_updates(
        status, updated_utc=_now_utc(), state="running", phase="campaign", stop_code=None,
        world_bank_id=new_world_bank_id, synthetic_world_count=new_count,
        campaign=None, campaign_seed=None, generation=None,
        campaigns_complete=0, rank1_qualifiers=0,
        last_progress_utc=_now_utc(),
    )
    write_status(STATUS_PATH, updated)
    return {"action": "expanded_world_bank", "synthetic_world_count": new_count, "admission": admission}


# ---------------------------------------------------------------------------
# Entry point: single-writer lock spans exactly one invocation
# ---------------------------------------------------------------------------

LOCK_PATH = REPO_ROOT / ".tbots.lock"


def main() -> int:
    """The systemd-invoked entry point. Never queues behind another writer
    (worker_lock is non-blocking): a second instance refuses to start rather
    than waiting, so an overrunning generation can never overlap the next
    timer firing. Returns a process exit code; never raises."""
    try:
        with worker_lock(LOCK_PATH):
            report = run_one_step()
    except LockHeldElsewhere:
        print(json.dumps({"action": "skipped_lock_held"}))
        return 0
    except WorkerStop as stop:
        status = read_status(STATUS_PATH) if STATUS_PATH.exists() else None
        if status is not None:
            updated = with_updates(
                status, updated_utc=_now_utc(), state="stopped",
                stop_code=stop.stop_code, stop_reason=stop.reason,
            )
            write_status(STATUS_PATH, updated)
        print(json.dumps({"action": "stopped", "stop_code": stop.stop_code, "reason": stop.reason}))
        return 1
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
