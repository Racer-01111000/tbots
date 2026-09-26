from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fitness_v2_campaign import (
    CampaignError,
    evaluate_genome_against_worlds,
    initial_population,
    next_population,
    rank_population,
    reference_passive_sharpes,
    run_all_campaigns,
    run_campaign,
    run_generation,
    select_survivors,
    training_world_set,
)
from fitness_v2_evolution_protocol import EVOLUTION_PROTOCOL
from fitness_v2_world_bank import (
    distributional_world,
    execution_world,
    historical_world,
    sequence_world,
    shock_world,
)
from genome_control import CONTROL_GENOME
from lib.ids import genome_id
from s5a_config import validate_genome
from test_fitness_v2_world_bank import _development_pool_for_reconstruction, _fake_pool, _fake_protocol_for

POPULATION_SIZE = EVOLUTION_PROTOCOL["population_size"]
FINAL_GENERATION = EVOLUTION_PROTOCOL["final_generation"]


def _tiny_world_bank(scored_length=60, pool_n=1500, start_offset=500):
    """Real frozen construction, deliberately small scored regions (speed) --
    warm-up is always the full frozen 378 bars regardless (every genome's
    lookback, even the grid maximum, must be satisfiable from session 0 of
    scoring), so every genome sees real trading activity, not just an
    empty-decision warm-up. evaluate_candidate requires exactly three
    historical worlds (H1/H2/H3); three non-overlapping slices of the same
    pool stand in for them here -- this test is about the generation/
    campaign loop's own mechanics, not about real H-shape dates."""
    pool = _fake_pool(n=pool_n)
    gap = scored_length + 20
    historical = [
        historical_world(pool, f"H{i+1}", _fake_protocol_for(
            pool, f"H{i+1}", start_offset=start_offset + i * gap, n_sessions=scored_length
        ))
        for i in range(3)
    ]
    anchor = historical[0]
    recon_pool = _development_pool_for_reconstruction(
        pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"]
    )
    target_length = 378 + scored_length
    donor_pool = [(i % 8, sign * 0.02) for i, sign in enumerate([1, -1, 1, -1, 1, -1, 1, -1])]
    synthetic = {
        "distributional": [
            distributional_world(recon_pool, anchor, seed=1000 + i, target_length=target_length)
            for i in range(4)
        ],
        "execution": [execution_world(anchor) for _ in range(4)],
        "sequence": [
            sequence_world(recon_pool, anchor, seed=2000 + i, target_scored_length=scored_length)
            for i in range(4)
        ],
        "shock": [
            shock_world(anchor, seed=3000 + i, donor_pool=donor_pool, extrema=(-0.5, 0.5))
            for i in range(4)
        ],
    }
    return historical, synthetic


class GenomePopulation(unittest.TestCase):
    def test_gen0_has_control_anchor_plus_49_immigrants(self):
        population = initial_population(campaign_seed=1)
        self.assertEqual(len(population), POPULATION_SIZE)
        anchors = [m for m in population if m["is_control"]]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["genome_id"], genome_id(CONTROL_GENOME))
        for member in population:
            validate_genome(member["genome"])

    def test_gen0_is_deterministic_in_campaign_seed(self):
        a = initial_population(campaign_seed=42)
        b = initial_population(campaign_seed=42)
        self.assertEqual([m["genome_id"] for m in a], [m["genome_id"] for m in b])

    def test_different_campaign_seeds_diverge(self):
        a = initial_population(campaign_seed=1)
        b = initial_population(campaign_seed=2)
        self.assertNotEqual([m["genome_id"] for m in a], [m["genome_id"] for m in b])

    def test_gen0_has_no_duplicate_genomes(self):
        population = initial_population(campaign_seed=7)
        self.assertEqual(len({m["genome_id"] for m in population}), POPULATION_SIZE)

    def test_next_population_preserves_survivors_and_fills_slots(self):
        gen0 = initial_population(campaign_seed=3)
        survivors = gen0[:10]  # any 10 for this structural test
        used = {m["genome_id"] for m in gen0}
        population = next_population(3, 1, survivors, used)
        self.assertEqual(len(population), POPULATION_SIZE)
        survivor_ids = {m["genome_id"] for m in survivors}
        self.assertTrue(survivor_ids.issubset({m["genome_id"] for m in population}))
        self.assertEqual(len({m["genome_id"] for m in population}), POPULATION_SIZE)

    def test_next_population_rejects_wrong_survivor_count(self):
        gen0 = initial_population(campaign_seed=3)
        with self.assertRaises(CampaignError):
            next_population(3, 1, gen0[:5], {m["genome_id"] for m in gen0})


class RankingAndSurvivors(unittest.TestCase):
    def test_rank_orders_by_fitness_desc_then_genome_id(self):
        members = [
            {"genome_id": "gen_b", "evaluation": {"cross_family_fitness": 1.0}},
            {"genome_id": "gen_a", "evaluation": {"cross_family_fitness": 1.0}},
            {"genome_id": "gen_c", "evaluation": {"cross_family_fitness": 2.0}},
        ]
        ranked = rank_population(members)
        self.assertEqual([m["genome_id"] for m in ranked], ["gen_c", "gen_a", "gen_b"])

    def test_select_survivors_always_keeps_control_anchor(self):
        ranked = [
            {"genome_id": f"gen_{i}", "is_control": False, "evaluation": {"cross_family_fitness": 10 - i}}
            for i in range(20)
        ]
        ranked.append({"genome_id": "gen_control", "is_control": True, "evaluation": {"cross_family_fitness": -999}})
        ranked = rank_population(ranked)
        survivors = select_survivors(ranked)
        self.assertEqual(len(survivors), 10)
        self.assertTrue(any(m["is_control"] for m in survivors))


class ReferencePassiveSharpes(unittest.TestCase):
    def test_returns_one_sharpe_per_historical_world(self):
        historical, _ = _tiny_world_bank(scored_length=60)
        sharpes = reference_passive_sharpes(historical)
        self.assertEqual(len(sharpes), 3)
        self.assertTrue(all(isinstance(s, float) for s in sharpes))

    def test_uses_the_standalone_passive_envelope_not_control_genome(self):
        # Handoff §37 / FITNESS_V2_OC1_REFERENCE_FREEZE_20260926.md: OC1's
        # reference is the standalone 80% PASSIVE_ENVELOPE, never
        # CONTROL_GENOME -- numerically identical today only because
        # CONTROL_GENOME also happens to declare target_max_exposure=0.80.
        # Prove the DEPENDENCY, not just the coincidence: swap in a genome
        # object with a materially different exposure and confirm
        # reference_passive_sharpes is unaffected.
        import fitness_v2_campaign as campaign_module
        historical, _ = _tiny_world_bank(scored_length=60)
        before = reference_passive_sharpes(historical)

        original_control_genome = campaign_module.CONTROL_GENOME
        campaign_module.CONTROL_GENOME = {**original_control_genome, "target_max_exposure": 0.05}
        try:
            after = reference_passive_sharpes(historical)
        finally:
            campaign_module.CONTROL_GENOME = original_control_genome
        self.assertEqual(before, after)

    def test_calls_simulate_passive_comparator_with_the_frozen_envelope(self):
        import fitness_v2_campaign as campaign_module
        from fitness_v2_oc1_reference import PASSIVE_ENVELOPE
        historical, _ = _tiny_world_bank(scored_length=60)

        seen_genomes = []
        original = campaign_module.simulate_passive_comparator

        def spy(world, genome, **kwargs):
            seen_genomes.append(genome)
            return original(world, genome, **kwargs)

        campaign_module.simulate_passive_comparator = spy
        try:
            reference_passive_sharpes(historical)
        finally:
            campaign_module.simulate_passive_comparator = original
        self.assertEqual(len(seen_genomes), 3)
        self.assertTrue(all(g is PASSIVE_ENVELOPE for g in seen_genomes))

    def test_per_candidate_exposure_matched_sharpe_uses_the_candidates_own_genome(self):
        # §37's "do not collapse these into one concept": evaluate_genome_
        # against_worlds's own exposure_matched_passive_sharpe (the
        # family_signal_deltas input) must call simulate_passive_comparator
        # with the CANDIDATE's genome, never the OC1 PASSIVE_ENVELOPE.
        import fitness_v2_campaign as campaign_module
        from fitness_v2_oc1_reference import PASSIVE_ENVELOPE
        historical, synthetic = _tiny_world_bank(scored_length=60)
        worlds = training_world_set(0, historical, synthetic)
        reference = {"execution": reference_passive_sharpes(historical)}

        seen_genomes = []
        original = campaign_module.simulate_passive_comparator

        def spy(world, genome, **kwargs):
            seen_genomes.append(genome)
            return original(world, genome, **kwargs)

        campaign_module.simulate_passive_comparator = spy
        try:
            evaluate_genome_against_worlds(CONTROL_GENOME, worlds, reference)
        finally:
            campaign_module.simulate_passive_comparator = original
        self.assertTrue(seen_genomes)
        self.assertTrue(all(g is CONTROL_GENOME for g in seen_genomes))
        self.assertNotIn(PASSIVE_ENVELOPE, seen_genomes)


class TrainingWorldSetRotation(unittest.TestCase):
    def test_rotation_selects_two_of_three_non_withheld_slots(self):
        historical, synthetic = _tiny_world_bank(scored_length=40)
        worlds = training_world_set(0, historical, synthetic)
        distributional_ids = {e["world_id"] for e in worlds if e["family"] == "distributional"}
        self.assertEqual(distributional_ids, {"distributional_1", "distributional_2"})
        self.assertEqual(len(worlds), 3 + 2 * 4)

    def test_slot_4_never_appears_in_any_rotation(self):
        historical, synthetic = _tiny_world_bank(scored_length=40)
        for generation in range(11):
            worlds = training_world_set(generation, historical, synthetic)
            self.assertFalse(any(e["world_id"].endswith("_4") for e in worlds))


class GenomeEvaluation(unittest.TestCase):
    def test_evaluate_genome_produces_valid_evaluate_candidate_shape(self):
        historical, synthetic = _tiny_world_bank(scored_length=60)
        worlds = training_world_set(0, historical, synthetic)
        reference = {"execution": reference_passive_sharpes(historical) or [0.0]}
        result = evaluate_genome_against_worlds(CONTROL_GENOME, worlds, reference)
        for key in ("cross_family_fitness", "family_scores", "eligible", "activity_rate"):
            self.assertIn(key, result)


class OneGeneration(unittest.TestCase):
    def test_run_generation_produces_next_population_or_final_ranking(self):
        historical, synthetic = _tiny_world_bank(scored_length=50)
        population = initial_population(campaign_seed=9)
        reference = {}
        used = {m["genome_id"] for m in population}
        result = run_generation(9, 0, population, historical, synthetic, reference, used)
        self.assertIn("next_population", result)
        self.assertEqual(len(result["next_population"]), POPULATION_SIZE)
        self.assertEqual(result["generation"], 0)


class FullCampaign(unittest.TestCase):
    """Real frozen counts (population 50, Gen0-Gen10) end to end, kept fast
    only via tiny scored-region world lengths -- proves the whole loop
    terminates with no duplicate/skipped generation and a coherent nominee,
    not a claim about real fitness outcomes."""

    def test_full_campaign_runs_all_11_generations_without_duplication(self):
        historical, synthetic = _tiny_world_bank(scored_length=45)
        reference = {"execution": reference_passive_sharpes(historical)}
        seen_generations = []

        def on_generation(generation, result):
            seen_generations.append(generation)

        result = run_campaign(2066557696, historical, synthetic, reference, on_generation=on_generation)
        self.assertEqual(seen_generations, list(range(FINAL_GENERATION + 1)))
        self.assertIn("final_admission_nominee", result)
        nominee = result["final_admission_nominee"]
        self.assertEqual(nominee["evolution_seed"], 2066557696)
        self.assertIn(nominee["clears_all_gates"], (True, False))

    def test_deterministic_given_same_seed_and_worlds(self):
        historical, synthetic = _tiny_world_bank(scored_length=45)
        reference = {"execution": reference_passive_sharpes(historical)}
        a = run_campaign(555, historical, synthetic, reference)
        b = run_campaign(555, historical, synthetic, reference)
        self.assertEqual(
            a["final_admission_nominee"]["genome_id"], b["final_admission_nominee"]["genome_id"]
        )
        self.assertEqual(
            a["final_admission_nominee"]["evaluation"]["cross_family_fitness"],
            b["final_admission_nominee"]["evaluation"]["cross_family_fitness"],
        )


class AllCampaigns(unittest.TestCase):
    def test_five_frozen_seeds_produce_a_3_of_5_admission_decision(self):
        historical, synthetic = _tiny_world_bank(scored_length=40)
        reference = {"execution": reference_passive_sharpes(historical)}
        seeds = [2066557696, 604610261, 3608585586, 3251376561, 1894202052]
        result = run_all_campaigns(seeds, historical, synthetic, reference)
        self.assertEqual(len(result["campaigns"]), 5)
        self.assertEqual(result["admission"]["predeclared_n"], 5)
        self.assertEqual(result["admission"]["required_count"], 3)
        self.assertIn(result["admission"]["admitted"], (True, False))


if __name__ == "__main__":
    unittest.main()
