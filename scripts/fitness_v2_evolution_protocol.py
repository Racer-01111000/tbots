"""Fitness V2 baseline-evolution protocol: population/generation counts and
the campaign-scoped deterministic seed derivation (Sable handoff §42, §48-49
via the 2026-09-26 handoff; Rick's parameter freeze for the five campaign
seeds themselves).

Population size, generation count, survivor/children/immigrant counts, and
mutated-gene-count exist only as prose in the handoff document. This module
pins them as a frozen, content-addressed, machine-validated manifest --
mirroring fitness_v2_warmup.py's and fitness_v2_formula_definitions.py's
exact pattern -- so a future caller cannot silently drift from handoff §42
by editing a bare inline constant.

This module does not invent a new evolutionary algorithm: population size
(50), generation range (Gen0-Gen10), survivor/children/immigrant counts, and
mutated-gene-count (1-3) all come from the handoff verbatim. It reuses,
never re-derives, S5A's genome schema/mutation/validation
(s5a_config.random_genome/mutate_genome/validate_genome/population_diversity/
genome_distance) -- those operate on the frozen MUTABLE grid via a caller-
supplied random.Random and are not tied to S5A's own EVOLUTION_SEED.

derive_seed() below is a genuinely new quantity: S5A's derive_seed() is
keyed off one module-level EVOLUTION_SEED constant for one run, but Fitness
V2 has five independent frozen campaign seeds, so a Fitness-V2 derive_seed
must take the active campaign seed as an explicit input rather than reading
a fixed module constant. It mirrors S5A's exact formula (sha256 of the
canonical-JSON input list, first 8 bytes big-endian, masked to 63 bits) --
same construction, campaign-scoped input -- per the handoff's §30 delegated-
authority rule for a genuinely new quantity not exactly defined by S5A.
"""
from __future__ import annotations

import hashlib

from fitness_v2 import FitnessV2Error, content_identity, canonical_json

EVOLUTION_PROTOCOL = {
    "schema_version": 1,
    "authority": "Sable handoff, 2026-09-26, section 42 (baseline evolution protocol)",
    "population_size": 50,
    "final_generation": 10,
    "generation_zero": {
        "control_anchor": 1,
        "random_immigrants": 49,
    },
    "subsequent_generation": {
        "control_anchor": 1,
        "elites": 9,
        "mutated_children": 30,
        "random_immigrants": 10,
    },
    "child_mutated_gene_count": {"min": 1, "max": 3},
    "crossover": "disabled",
    "duplicate_retry_limit_per_slot": 10000,
    "ranking_tie_break": ["descending_fitness_v2_score", "lexicographic_genome_id"],
    "final_generation_ranking_source": (
        "Only the final generation's (Gen10's) evaluated population is ranked "
        "to produce the campaign's rank-1 nominee -- not the best genome ever "
        "seen across the campaign. This mirrors s5a_evolution.run_evolution's "
        "own pattern exactly (final_ranked = ranked population at "
        "generation == FINAL_GENERATION, frozen via _freeze_top10): S5A "
        "already defines this convention for the same quantity ('who is the "
        "campaign's winner after N generations'), so it is reused, not "
        "invented, per the handoff's §30 rule."
    ),
}
EVOLUTION_PROTOCOL_ID = content_identity(
    "fitness_v2_evolution_protocol_", EVOLUTION_PROTOCOL
)

POPULATION_SIZE = EVOLUTION_PROTOCOL["population_size"]
FINAL_GENERATION = EVOLUTION_PROTOCOL["final_generation"]


class EvolutionProtocolError(FitnessV2Error):
    pass


def validate_evolution_protocol(envelope: dict) -> str:
    if envelope != {"manifest_id": EVOLUTION_PROTOCOL_ID, "content": EVOLUTION_PROTOCOL}:
        raise EvolutionProtocolError("Fitness V2 evolution-protocol manifest mismatch")
    return EVOLUTION_PROTOCOL_ID


def derive_seed(campaign_seed: int, *parts) -> int:
    """Deterministic sub-seed for one campaign. Mirrors s5a_config.derive_seed's
    exact construction (sha256 of canonical-JSON [seed, *parts], first 8 bytes
    big-endian, masked to 63 bits) with the campaign's own frozen EVOLUTION_SEED
    as the input instead of a fixed module constant -- see module docstring."""
    if isinstance(campaign_seed, bool) or not isinstance(campaign_seed, int):
        raise EvolutionProtocolError("campaign_seed must be an integer")
    payload = canonical_json([campaign_seed, *parts]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)


def rotation_slots(generation: int) -> tuple[int, int]:
    """Handoff §15's frozen rotation: which two of each synthetic family's
    three training worlds (1-indexed slots 1-3; slot 4 is always withheld)
    are shown to every genome this generation."""
    table = {0: (1, 2), 1: (2, 3), 2: (3, 1)}
    return table[generation % 3]
