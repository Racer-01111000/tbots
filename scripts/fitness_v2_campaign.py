"""Fitness V2 genome/generation loop and campaign orchestrator.

Reuses, never re-derives: s5a_config's genome primitives (random_genome,
mutate_genome, validate_genome -- these take a caller-supplied random.Random
and depend only on the frozen MUTABLE grid, never on S5A's own run-specific
EVOLUTION_SEED), genome_control.CONTROL_GENOME, fitness_v2_evolution_protocol's
frozen population/generation counts and campaign-scoped derive_seed,
fitness_v2_simulator's world simulation and passive comparator, and
fitness_v2's contract algebra (per_world_score via evaluate_candidate,
nominate_campaign, decide_admission).

Final-generation-ranking convention (handoff §30 delegated-reuse rule): only
the final generation's (Gen10's) evaluated population is ranked to produce a
campaign's rank-1 nominee -- mirrors s5a_evolution.run_evolution's own
pattern exactly (final_ranked = ranked population at generation ==
FINAL_GENERATION, frozen via _freeze_top10). Not invented here; already
recorded in fitness_v2_evolution_protocol.EVOLUTION_PROTOCOL.

OC1 reference passive Sharpes (handoff §37, frozen by Rick's 2026-09-26
ruling -- see fitness_v2_oc1_reference.py and
FITNESS_V2_OC1_REFERENCE_FREEZE_20260926.md): computed once, from the
standalone 80% PASSIVE_ENVELOPE run on the three real historical worlds --
a fixed reference baseline reused by every candidate's OC1 check, exactly
mirroring how the 32-descriptor reference is also computed once from the
three historical worlds (development_reference_calibration). A candidate's
own passive Sharpe (for family_signal_deltas) still uses the CANDIDATE's own
exposure cap (handoff §37, "exposure-matched primary passive comparator");
only the OC1 baseline uses the fixed envelope -- the two must never be
collapsed into one concept.
"""
from __future__ import annotations

import random
import statistics
from collections.abc import Mapping, Sequence

from fitness_v2 import FitnessV2Error, decide_admission, evaluate_candidate, nominate_campaign
from fitness_v2_evolution_protocol import EVOLUTION_PROTOCOL, derive_seed, rotation_slots
from fitness_v2_formula_definitions import family_oc1
from fitness_v2_oc1_reference import PASSIVE_ENVELOPE
from fitness_v2_simulator import simulate_passive_comparator, simulate_world, world_metrics
from genome_control import CONTROL_GENOME
from lib.ids import genome_id
from s5a_config import mutate_genome, random_genome, validate_genome

POPULATION_SIZE = EVOLUTION_PROTOCOL["population_size"]
FINAL_GENERATION = EVOLUTION_PROTOCOL["final_generation"]
WITHHELD_SLOT = 4


class CampaignError(FitnessV2Error):
    pass


# ---------------------------------------------------------------------------
# Genome population: creation, mutation, uniqueness
# ---------------------------------------------------------------------------

def _unique_immigrant(campaign_seed: int, generation: int, slot: int, used: set[str], limit: int) -> dict:
    for attempt in range(limit):
        seed = derive_seed(campaign_seed, "immigrant", generation, slot, attempt)
        genome = random_genome(random.Random(seed))
        gid = genome_id(genome)
        if gid not in used:
            used.add(gid)
            return {"genome_id": gid, "genome": genome, "role": "immigrant", "is_control": False}
    raise CampaignError("duplicate retry limit exhausted for immigrant")


def _unique_child(campaign_seed: int, generation: int, slot: int, survivors: list[dict],
                   used: set[str], limit: int) -> dict:
    for attempt in range(limit):
        seed = derive_seed(campaign_seed, "child", generation, slot, attempt)
        rng = random.Random(seed)
        parent = survivors[rng.randrange(len(survivors))]
        genome, mutation, magnitude = mutate_genome(parent["genome"], rng)
        gid = genome_id(genome)
        if gid not in used:
            used.add(gid)
            return {
                "genome_id": gid, "genome": genome, "role": "child", "is_control": False,
                "parent_genome_id": parent["genome_id"], "mutation": mutation, "magnitude": magnitude,
            }
    raise CampaignError("duplicate retry limit exhausted for child")


def initial_population(campaign_seed: int) -> list[dict]:
    """Gen0: one immutable control anchor plus 49 random immigrants (handoff
    §42). The control anchor's genome_id must be identical across every
    campaign -- it is not campaign-seeded, it is the one frozen S4 genome."""
    anchor = {
        "genome_id": genome_id(CONTROL_GENOME), "genome": dict(CONTROL_GENOME),
        "role": "control_anchor", "is_control": True,
    }
    used = {anchor["genome_id"]}
    limit = EVOLUTION_PROTOCOL["duplicate_retry_limit_per_slot"]
    population = [anchor]
    for slot in range(1, EVOLUTION_PROTOCOL["generation_zero"]["random_immigrants"] + 1):
        population.append(_unique_immigrant(campaign_seed, 0, slot, used, limit))
    if len(population) != POPULATION_SIZE:
        raise CampaignError("Gen0 population size drifted from the frozen count")
    return population


def next_population(campaign_seed: int, generation: int, survivors: list[dict], used: set[str]) -> list[dict]:
    rules = EVOLUTION_PROTOCOL["subsequent_generation"]
    if len(survivors) != rules["control_anchor"] + rules["elites"]:
        raise CampaignError("survivor count does not match the frozen elite count")
    limit = EVOLUTION_PROTOCOL["duplicate_retry_limit_per_slot"]
    population = list(survivors)
    for slot in range(rules["mutated_children"]):
        population.append(_unique_child(campaign_seed, generation, slot, survivors, used, limit))
    for slot in range(rules["random_immigrants"]):
        population.append(_unique_immigrant(campaign_seed, generation, slot, used, limit))
    if len(population) != POPULATION_SIZE:
        raise CampaignError("population size drifted from the frozen count")
    gids = [member["genome_id"] for member in population]
    if len(set(gids)) != POPULATION_SIZE:
        raise CampaignError("population contains a duplicate genome slot")
    return population


def rank_population(evaluated: list[dict]) -> list[dict]:
    """Ranking tie-break (handoff §42): descending Fitness V2 score, then
    lexicographic genome_id -- exact mirror of s5a_evolution._rank_population,
    reparented to cross_family_fitness instead of S5A's own aggregate.fitness."""
    return sorted(
        evaluated,
        key=lambda member: (-member["evaluation"]["cross_family_fitness"], member["genome_id"]),
    )


def select_survivors(ranked: list[dict]) -> list[dict]:
    """Control anchor always survives (handoff §42); the next 9 by rank fill
    the elite slots -- exact mirror of s5a_evolution._select_survivors."""
    anchor = next(member for member in ranked if member["is_control"])
    non_control = [member for member in ranked if not member["is_control"]]
    elites = EVOLUTION_PROTOCOL["subsequent_generation"]["elites"]
    survivors = [anchor, *non_control[:elites]]
    return sorted(survivors, key=lambda member: (-member["evaluation"]["cross_family_fitness"], member["genome_id"]))


# ---------------------------------------------------------------------------
# Genome evaluation: simulate against a world set, wire OC1
# ---------------------------------------------------------------------------

def reference_passive_sharpes(historical_worlds: Sequence[Mapping[str, object]]) -> list[float]:
    """See module docstring: the standalone 80% PASSIVE_ENVELOPE's passive
    comparator on the three real historical worlds, computed once and reused
    by every candidate's OC1 check -- never CONTROL_GENOME, never a
    candidate's own exposure. Simulated over the FULL world (warm-up prefix
    included) via its own scored_start_index -- never scored_only(), which
    would delete the warm-up prefix simulate_passive_comparator's own
    indicator-free mechanics don't need but which keeps this call symmetric
    with evaluate_genome_against_worlds and the frozen world shape."""
    return [
        simulate_passive_comparator(world, PASSIVE_ENVELOPE, scored_start_index=world["scored_start_index"])["sharpe"]
        for world in historical_worlds
    ]


def evaluate_genome_against_worlds(
    genome: Mapping[str, object], worlds: Sequence[Mapping[str, object]],
    reference_sharpes_by_family: Mapping[str, Sequence[float]],
) -> dict:
    """worlds: [{"family", "world_id", "world", "skip_mask"(optional)}, ...].
    Returns the evaluate_candidate() result for this genome against exactly
    this world set (a generation's rotated training set, or the full
    19-world admission set)."""
    validate_genome(genome)
    by_family: dict[str, list[dict]] = {}
    for entry in worlds:
        family = entry["family"]
        # The FULL world (warm-up prefix included), never scored_only() --
        # simulate_world/simulate_passive_comparator need the prefix for
        # indicator lookback via their own scored_start_index; scored_only()
        # is for descriptor/admission computation only (fitness_v2_world_bank).
        world = entry["world"]
        start = world["scored_start_index"]
        simulation = simulate_world(world, genome, scored_start_index=start, skip_mask=entry.get("skip_mask"))
        passive = simulate_passive_comparator(world, genome, scored_start_index=start)
        by_family.setdefault(family, []).append({
            "family": family, "world_id": entry["world_id"],
            "metrics": world_metrics(simulation),
            "exposure_matched_passive_sharpe": passive["sharpe"],
        })

    rows: list[dict] = []
    for family, family_rows in by_family.items():
        reference = reference_sharpes_by_family.get(family)
        anomalous = False
        if reference:
            world_sharpes = [row["exposure_matched_passive_sharpe"] for row in family_rows]
            oc1 = family_oc1(world_sharpes, list(reference))
            anomalous = oc1["passive_comparator_anomalous"]
        for row in family_rows:
            rows.append({**row, "passive_comparator_anomalous": anomalous})

    return evaluate_candidate(rows)


# ---------------------------------------------------------------------------
# One generation: evaluate, rank, select, breed
# ---------------------------------------------------------------------------

def training_world_set(generation: int, historical_worlds, synthetic_worlds_by_family) -> list[dict]:
    """Handoff §15's frozen rotation: all 3 historical worlds plus exactly 2
    of each synthetic family's first 3 (non-withheld) worlds, chosen by
    `generation mod 3`. Slot 4 (index 3) is never in a training set."""
    entries = [
        {"family": "historical", "world_id": f"historical_{i}", "world": world}
        for i, world in enumerate(historical_worlds)
    ]
    slot_a, slot_b = rotation_slots(generation)
    for family, worlds in synthetic_worlds_by_family.items():
        for slot in (slot_a, slot_b):
            world = worlds[slot - 1]
            entry = {"family": family, "world_id": f"{family}_{slot}", "world": world}
            entries.append(entry)
    return entries


def run_generation(
    campaign_seed: int, generation: int, population: list[dict], historical_worlds,
    synthetic_worlds_by_family, reference_sharpes_by_family, used_genome_ids: set[str],
) -> dict:
    """Evaluates every member, ranks, selects survivors, and (if not the
    final generation) breeds the next population. Returns everything a
    caller needs to checkpoint this generation transactionally."""
    worlds = training_world_set(generation, historical_worlds, synthetic_worlds_by_family)
    evaluated = []
    for member in population:
        evaluation = evaluate_genome_against_worlds(member["genome"], worlds, reference_sharpes_by_family)
        evaluated.append({**member, "evaluation": evaluation})

    ranked = rank_population(evaluated)
    survivors = select_survivors(ranked)
    result = {
        "generation": generation,
        "ranked_genome_ids": [m["genome_id"] for m in ranked],
        "best_cross_family_fitness": ranked[0]["evaluation"]["cross_family_fitness"],
        "median_cross_family_fitness": statistics.median(
            m["evaluation"]["cross_family_fitness"] for m in ranked
        ),
        "control_anchor_fitness": next(
            m["evaluation"]["cross_family_fitness"] for m in ranked if m["is_control"]
        ),
    }
    if generation < FINAL_GENERATION:
        result["next_population"] = next_population(campaign_seed, generation + 1, survivors, used_genome_ids)
    else:
        result["final_ranked"] = ranked
    return result


# ---------------------------------------------------------------------------
# One campaign: Gen0 -> Gen10, rank-1 nomination, withheld-world admission
# ---------------------------------------------------------------------------

def run_campaign(
    campaign_seed: int, historical_worlds, synthetic_worlds_by_family,
    reference_sharpes_by_family, *, on_generation=None,
) -> dict:
    """Runs one full campaign (Gen0 through the frozen FINAL_GENERATION),
    nominates the campaign's rank-1 genome from ONLY the final generation's
    ranking (see module docstring), then evaluates that one genome -- no
    sibling substitution (handoff §46) -- against the full 19-world
    admission set (3 historical + all 4 worlds of every synthetic family,
    including the withheld 4th). `on_generation(generation, result)` is an
    optional callback for transactional per-generation checkpointing."""
    used_genome_ids: set[str] = set()
    population = initial_population(campaign_seed)
    used_genome_ids.update(m["genome_id"] for m in population)
    final_ranked = None

    for generation in range(FINAL_GENERATION + 1):
        result = run_generation(
            campaign_seed, generation, population, historical_worlds,
            synthetic_worlds_by_family, reference_sharpes_by_family, used_genome_ids,
        )
        if on_generation is not None:
            on_generation(generation, result)
        if generation < FINAL_GENERATION:
            population = result["next_population"]
        else:
            final_ranked = result["final_ranked"]

    nominee = nominate_campaign(final_ranked)

    admission_worlds = [
        {"family": "historical", "world_id": f"historical_{i}", "world": world}
        for i, world in enumerate(historical_worlds)
    ]
    for family, worlds in synthetic_worlds_by_family.items():
        for slot, world in enumerate(worlds, start=1):
            admission_worlds.append({"family": family, "world_id": f"{family}_{slot}", "world": world})

    winner_genome = next(m["genome"] for m in final_ranked if m["genome_id"] == nominee["genome_id"])
    full_evaluation = evaluate_genome_against_worlds(winner_genome, admission_worlds, reference_sharpes_by_family)
    full_nominee = {
        "genome_id": nominee["genome_id"],
        "evaluation": full_evaluation,
        "clears_all_gates": full_evaluation["eligible"] is True,
        "rank": 1,
        "sibling_substitution_permitted": False,
        "evolution_seed": campaign_seed,
    }
    return {
        "campaign_seed": campaign_seed,
        "generation_zero_nominee_evaluation": nominee["evaluation"],
        "final_admission_nominee": full_nominee,
    }


def run_all_campaigns(
    campaign_seeds: Sequence[int], historical_worlds, synthetic_worlds_by_family,
    reference_sharpes_by_family, *, on_generation=None,
) -> dict:
    """Handoff §40-41/§47: exactly N=5 independent campaigns (frozen seeds),
    3-of-5 majority, deterministic lowest-EVOLUTION_SEED champion selection."""
    nominees = []
    campaign_results = []
    for seed in campaign_seeds:
        result = run_campaign(
            seed, historical_worlds, synthetic_worlds_by_family,
            reference_sharpes_by_family, on_generation=on_generation,
        )
        campaign_results.append(result)
        nominees.append(result["final_admission_nominee"])
    admission = decide_admission(nominees, predeclared_n=len(campaign_seeds))
    return {"campaigns": campaign_results, "admission": admission}
