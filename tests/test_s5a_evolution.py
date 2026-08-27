import json
import math
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from genome_control import CONTROL_GENOME
from lib.db import init_db
from lib.ids import canonical_json, genome_id
from s5a_build_development_bundle import derive_development_bundle, write_development_bundle
from s5a_development_bundle import _load_bundle_directory
import s5a_config as config
import s5a_evaluator as evaluator
import s5a_evolution as evolution


class S5AProtocolTestCase(unittest.TestCase):
    def test_protocol_is_content_addressed_and_development_only(self):
        self.assertEqual(config.POPULATION_SIZE, 50)
        self.assertEqual(config.FINAL_GENERATION, 10)
        self.assertEqual(config.EPISODE_PROTOCOL["lane_start"], "2007-02-07")
        self.assertEqual(config.EPISODE_PROTOCOL["lane_end"], "2018-12-31")
        self.assertEqual(
            config.EPISODE_PROTOCOL["lane_manifest_hash"],
            config.DEVELOPMENT_LANE_HASH,
        )
        self.assertEqual(config.EPISODE_PROTOCOL["dataset_revision"], config.DATASET_REVISION)
        for manifest_hash, path in config.PROTOCOL_PATHS.items():
            envelope = json.loads(path.read_text())
            self.assertEqual(envelope["manifest_hash"], manifest_hash)
            self.assertIn(manifest_hash, path.name)

    def test_episode_manifest_is_contiguous_and_resets_all_state(self):
        episodes = config.EPISODE_PROTOCOL["episodes"]
        self.assertEqual(len(episodes), 12)
        self.assertEqual([row["episode_index"] for row in episodes], list(range(12)))
        self.assertEqual(episodes[0]["start_date"], "2007-02-07")
        self.assertEqual(episodes[-1]["end_date"], "2018-12-31")
        self.assertEqual(
            set(config.EPISODE_PROTOCOL["fresh_state_each_episode"]),
            {"cash", "portfolio", "peak_equity", "drawdown", "halt"},
        )
        from datetime import date, timedelta

        for before, after in zip(episodes, episodes[1:]):
            expected = date.fromisoformat(before["end_date"]) + timedelta(days=1)
            self.assertEqual(after["start_date"], expected.isoformat())

    def test_population_formula_and_control_anchor_are_frozen(self):
        rules = config.POPULATION_PROTOCOL
        self.assertEqual(rules["generation_zero"], {"control_anchors": 1, "random_immigrants": 49})
        self.assertEqual(
            rules["subsequent_generation"],
            {"elites": 10, "mutated_children": 30, "random_immigrants": 10},
        )
        self.assertEqual(genome_id(CONTROL_GENOME), config.CONTROL_GENOME_ID)
        config.validate_genome(CONTROL_GENOME)

    def test_qualification_and_locked_dates_are_absent_from_protocol(self):
        serialized = canonical_json({
            "episode": config.EPISODE_PROTOCOL,
            "mutation": config.MUTATION_PROTOCOL,
            "population": config.POPULATION_PROTOCOL,
        })
        self.assertNotIn("QUALIFICATION", serialized)
        self.assertNotIn("2019-01-01", serialized)
        self.assertNotIn("2023-01-01", serialized)
        self.assertNotIn("2026-01-01", serialized)


class S5AGenomeTestCase(unittest.TestCase):
    def test_random_and_mutated_genomes_are_deterministic_and_bounded(self):
        for seed in range(100):
            a = config.random_genome(random.Random(seed))
            b = config.random_genome(random.Random(seed))
            self.assertEqual(a, b)
            config.validate_genome(a)
            child_a, mutation_a, magnitude_a = config.mutate_genome(a, random.Random(seed + 10_000))
            child_b, mutation_b, magnitude_b = config.mutate_genome(a, random.Random(seed + 10_000))
            self.assertEqual((child_a, mutation_a, magnitude_a), (child_b, mutation_b, magnitude_b))
            self.assertNotEqual(child_a, a)
            config.validate_genome(child_a)
            self.assertGreater(magnitude_a, 0)
            self.assertLessEqual(len(mutation_a), 3)

    def test_immutable_or_misordered_genome_fails_closed(self):
        short = dict(CONTROL_GENOME, shorting="allowed")
        with self.assertRaises(config.ProtocolError):
            config.validate_genome(short)
        bad_order = dict(CONTROL_GENOME, momentum_lookbacks=[126, 63, 252])
        with self.assertRaises(config.ProtocolError):
            config.validate_genome(bad_order)

    def test_population_distance_and_diversity_are_deterministic(self):
        genomes = [config.random_genome(random.Random(config.derive_seed("test", i))) for i in range(20)]
        self.assertEqual(config.population_diversity(genomes), config.population_diversity(genomes))
        self.assertGreater(config.population_diversity(genomes), 0)
        self.assertEqual(config.genome_distance(genomes[0], genomes[0]), 0)


class S5AFitnessTestCase(unittest.TestCase):
    def _episode(self, index, total_return, sharpe, drawdown, halted, turnover, cost):
        return {
            "episode_index": index,
            "total_return": total_return,
            "sharpe": sharpe,
            "max_drawdown": drawdown,
            "halted": halted,
            "turnover": turnover,
            "commission_cents": int(cost * evaluator.STARTING_CASH_CENTS / 2),
            "slippage_cents": int(cost * evaluator.STARTING_CASH_CENTS / 2),
            "transaction_cost_rate": cost,
        }

    def test_complete_frozen_formula_is_applied_exactly(self):
        rows = [
            self._episode(i, 0.01 * (i - 4), 0.1 * (i - 3), -0.01 * (i + 1), i in {2, 9}, 1 + i / 10, 0.001 + i / 100_000)
            for i in range(12)
        ]
        result = evaluator.compute_fitness(rows)
        weights = config.FITNESS_PROTOCOL["weights"]
        expected = (
            weights["median_episode_return"] * result["median_episode_return"]
            + weights["median_episode_sharpe"] * result["median_episode_sharpe"]
            + weights["absolute_worst_drawdown"] * abs(result["worst_drawdown"])
            + weights["consistency_score"] * result["consistency_score"]
            + weights["halt_rate"] * result["halt_rate"]
            + weights["median_episode_turnover"] * result["median_episode_turnover"]
            + weights["median_transaction_cost_rate"] * result["median_transaction_cost_rate"]
            + weights["performance_concentration"] * result["performance_concentration"]
        )
        self.assertAlmostEqual(result["fitness"], expected, places=10)
        self.assertEqual(result["drawdown_halt_count"], 2)

    def test_incomplete_episode_set_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluator.compute_fitness([])


class S5APersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "s5a.db"
        self.conn = init_db(db_path=self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.run_id = "evo_test"
        self.conn.execute(
            "INSERT INTO evolution_runs (run_id, code_revision, code_dirty, dataset_revision, "
            "lane_manifest_hash, development_bundle_revision, bundle_manifest_hash, "
            "evolution_seed, population_size, final_generation, "
            "episode_manifest_hash, fitness_formula_hash, mutation_bounds_hash, "
            "population_rules_hash, status, created_at) "
            "VALUES (?, 'revision', 0, ?, ?, ?, 'manifest', ?, ?, ?, ?, ?, ?, ?, 'running', 'now')",
            (
                self.run_id,
                config.DATASET_REVISION,
                config.DEVELOPMENT_LANE_HASH,
                config.AUTHORIZED_DEVELOPMENT_BUNDLE_REVISION,
                config.EVOLUTION_SEED,
                config.POPULATION_SIZE,
                config.FINAL_GENERATION,
                config.EPISODE_HASH,
                config.FITNESS_HASH,
                config.MUTATION_HASH,
                config.POPULATION_HASH,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_generation_zero_and_one_are_exactly_50_unique(self):
        population, used = evolution.initial_population(self.conn, self.run_id)
        self.assertEqual(len(population), 50)
        self.assertEqual(len({member["genome_id"] for member in population}), 50)
        self.assertEqual(sum(member["is_control"] for member in population), 1)
        for index, member in enumerate(population):
            member["fitness"] = float(index)
        survivors = evolution._select_survivors(evolution._rank_population(population))
        next_population = evolution.next_population(self.conn, self.run_id, 1, survivors, used)
        self.assertEqual(len(next_population), 50)
        self.assertEqual(len({member["genome_id"] for member in next_population}), 50)
        roles = [member.get("membership_role", member["role"]) for member in next_population]
        self.assertEqual(roles.count("child"), 30)
        self.assertEqual(roles.count("immigrant"), 10)
        self.assertEqual(roles.count("control_anchor"), 1)
        self.assertEqual(roles.count("elite"), 9)

    def test_retired_organism_stays_auditable_and_cannot_be_active(self):
        population, _ = evolution.initial_population(self.conn, self.run_id)
        victim = population[-1]
        evolution._retire(self.conn, [victim])
        row = self.conn.execute(
            "SELECT state, retired_at FROM evolution_organisms WHERE agent_id = ?",
            (victim["agent_id"],),
        ).fetchone()
        self.assertEqual(row["state"], "retired")
        self.assertIsNotNone(row["retired_at"])
        self.assertEqual(
            self.conn.execute("SELECT status FROM agents WHERE agent_id = ?", (victim["agent_id"],)).fetchone()[0],
            "graveyard",
        )

    def test_schema_rejects_duplicate_population_genome_slots(self):
        population, _ = evolution.initial_population(self.conn, self.run_id)
        evolution._insert_population(self.conn, self.run_id, 0, population)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO evolution_population "
                "(run_id, generation, slot_index, agent_id, genome_id, membership_role) "
                "VALUES (?, 0, 50, ?, ?, 'immigrant')",
                (self.run_id, population[1]["agent_id"], population[1]["genome_id"]),
            )


class S5AIsolationIntegrationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        derived = derive_development_bundle(config.ROOT, "TEST_CONSTRUCTION_REVISION")
        path = write_development_bundle(derived, Path(cls.tmp.name))
        cls.bundle = _load_bundle_directory(
            path, derived["derived_bundle_revision"]
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_verified_bundle_retains_no_locked_lane_rows(self):
        self.assertLessEqual(self.bundle.calendar[-1], "2018-12-31")
        for rows in self.bundle.per_symbol_rows.values():
            self.assertTrue(all(row["timestamp"] <= "2018-12-31" for row in rows))

    def test_episode_state_is_fresh_and_repeatable(self):
        episode = config.EPISODE_PROTOCOL["episodes"][0]
        first = evaluator.simulate_episode(self.bundle, CONTROL_GENOME, 0)
        second = evaluator.simulate_episode(self.bundle, CONTROL_GENOME, 0)
        self.assertEqual(first, second)
        self.assertEqual(first["starting_cash_cents"], 100_000_000)

    def test_independent_verifier_disagreement_fails_closed(self):
        with mock.patch.object(evaluator.verifier, "compare_decision", return_value=["injected"]):
            with self.assertRaises(evaluator.IndependentVerifierFailure):
                evaluator.simulate_episode(self.bundle, CONTROL_GENOME, 0, verify=True)


class S5ARunGuardTestCase(unittest.TestCase):
    def test_dirty_revision_refuses_before_database_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "must_not_exist.db"
            with mock.patch.object(evolution, "get_code_revision", return_value=("rev", True)):
                with self.assertRaisesRegex(RuntimeError, "clean committed"):
                    evolution.run_evolution(db_path=db_path, write_report=False)
            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
