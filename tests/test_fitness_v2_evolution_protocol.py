from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from fitness_v2_evolution_protocol import (
    EVOLUTION_PROTOCOL,
    EVOLUTION_PROTOCOL_ID,
    EvolutionProtocolError,
    derive_seed,
    rotation_slots,
    validate_evolution_protocol,
)


class FrozenCounts(unittest.TestCase):
    def test_population_and_generations_match_handoff_42(self):
        self.assertEqual(EVOLUTION_PROTOCOL["population_size"], 50)
        self.assertEqual(EVOLUTION_PROTOCOL["final_generation"], 10)

    def test_generation_zero_sums_to_population(self):
        g0 = EVOLUTION_PROTOCOL["generation_zero"]
        self.assertEqual(g0["control_anchor"] + g0["random_immigrants"], 50)

    def test_subsequent_generation_sums_to_population(self):
        g = EVOLUTION_PROTOCOL["subsequent_generation"]
        total = g["control_anchor"] + g["elites"] + g["mutated_children"] + g["random_immigrants"]
        self.assertEqual(total, 50)

    def test_survivor_count_is_control_plus_elites(self):
        g = EVOLUTION_PROTOCOL["subsequent_generation"]
        self.assertEqual(g["control_anchor"] + g["elites"], 10)

    def test_mutated_gene_count_is_1_to_3(self):
        self.assertEqual(EVOLUTION_PROTOCOL["child_mutated_gene_count"], {"min": 1, "max": 3})

    def test_crossover_disabled(self):
        self.assertEqual(EVOLUTION_PROTOCOL["crossover"], "disabled")


class ManifestIdentity(unittest.TestCase):
    def test_manifest_id_matches_content(self):
        self.assertTrue(EVOLUTION_PROTOCOL_ID.startswith("fitness_v2_evolution_protocol_"))
        validate_evolution_protocol({"manifest_id": EVOLUTION_PROTOCOL_ID, "content": EVOLUTION_PROTOCOL})

    def test_tamper_in_population_size_detected(self):
        tampered = copy.deepcopy(EVOLUTION_PROTOCOL)
        tampered["population_size"] = 51
        with self.assertRaises(EvolutionProtocolError):
            validate_evolution_protocol({"manifest_id": EVOLUTION_PROTOCOL_ID, "content": tampered})

    def test_tamper_in_final_generation_detected(self):
        tampered = copy.deepcopy(EVOLUTION_PROTOCOL)
        tampered["final_generation"] = 20
        with self.assertRaises(EvolutionProtocolError):
            validate_evolution_protocol({"manifest_id": EVOLUTION_PROTOCOL_ID, "content": tampered})

    def test_wrong_declared_id_detected(self):
        with self.assertRaises(EvolutionProtocolError):
            validate_evolution_protocol({"manifest_id": "wrong", "content": EVOLUTION_PROTOCOL})


class DeriveSeed(unittest.TestCase):
    CAMPAIGN_SEEDS = [2066557696, 604610261, 3608585586, 3251376561, 1894202052]

    def test_deterministic(self):
        a = derive_seed(2066557696, "child", 3, 5, 0)
        b = derive_seed(2066557696, "child", 3, 5, 0)
        self.assertEqual(a, b)

    def test_different_campaign_seeds_diverge(self):
        outputs = {derive_seed(seed, "immigrant", 0, 1, 0) for seed in self.CAMPAIGN_SEEDS}
        self.assertEqual(len(outputs), len(self.CAMPAIGN_SEEDS))

    def test_different_parts_diverge(self):
        a = derive_seed(2066557696, "child", 3, 5, 0)
        b = derive_seed(2066557696, "child", 3, 5, 1)
        self.assertNotEqual(a, b)

    def test_result_is_nonnegative_63_bit(self):
        value = derive_seed(2066557696, "control_anchor")
        self.assertGreaterEqual(value, 0)
        self.assertLess(value, 1 << 63)

    def test_rejects_non_integer_campaign_seed(self):
        with self.assertRaises(EvolutionProtocolError):
            derive_seed(True, "x")
        with self.assertRaises(EvolutionProtocolError):
            derive_seed(1.5, "x")


class Rotation(unittest.TestCase):
    def test_frozen_table(self):
        self.assertEqual(rotation_slots(0), (1, 2))
        self.assertEqual(rotation_slots(1), (2, 3))
        self.assertEqual(rotation_slots(2), (3, 1))

    def test_wraps_every_three_generations(self):
        for generation in range(11):
            self.assertEqual(rotation_slots(generation), rotation_slots(generation + 3))


if __name__ == "__main__":
    unittest.main()
