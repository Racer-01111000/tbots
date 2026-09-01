import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib import models
from scripts.lib.db import SCHEMA_PATH, connect, init_db
from scripts.lib.ids import genome_id

EXPECTED_TABLES = {
    "genomes", "agents", "experiments", "episodes", "decisions", "orders", "fills",
}

SAMPLE_GENOME = {
    "strategy_family": "trend",
    "lookbacks": [63, 126, 252],
    "trend_filter": 200,
    "max_positions": 3,
    "target_exposure": 0.80,
    "max_asset_weight": 0.35,
    "rebalance_days": 21,
    "volatility_window": 63,
}


class SchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.conn = init_db(db_path=self.db_path, schema_path=SCHEMA_PATH)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_all_tables_present(self):
        tables = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue(EXPECTED_TABLES.issubset(tables))

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO agents (agent_id, genome_id, generation, created_at) "
                "VALUES ('agt_bogus', 'gen_does_not_exist', 0, '2026-01-01T00:00:00+00:00')"
            )

    def test_genome_id_is_content_addressed_and_order_independent(self):
        gid1 = genome_id(SAMPLE_GENOME)
        reordered = dict(reversed(list(SAMPLE_GENOME.items())))
        gid2 = genome_id(reordered)
        self.assertEqual(gid1, gid2)

        different = dict(SAMPLE_GENOME, max_positions=4)
        gid3 = genome_id(different)
        self.assertNotEqual(gid1, gid3)

    def test_create_genome_is_idempotent(self):
        gid_a = models.create_genome(self.conn, SAMPLE_GENOME)
        gid_b = models.create_genome(self.conn, SAMPLE_GENOME)
        self.conn.commit()
        self.assertEqual(gid_a, gid_b)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM genomes WHERE genome_id = ?", (gid_a,)
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_full_round_trip_through_the_experiment_model(self):
        conn = self.conn
        gid = models.create_genome(conn, SAMPLE_GENOME)
        agent_id = models.create_agent(conn, gid, generation=0)
        child_agent_id = models.create_agent(conn, gid, generation=1, parent_agent_id=agent_id)

        experiment_id = models.create_experiment(
            conn,
            code_revision="deadbeef",
            code_dirty=False,
            dataset_revision="sha256:testdataset",
            random_seed=42,
            agent_id=agent_id,
            genome_id_=gid,
            start_state={"cash_cents": 10_000_000},
            replay_window_start="2020-01-01",
            replay_window_end="2020-12-31",
            execution_assumptions={"commission_bps": 5, "slippage_bps": 2},
        )
        episode_id = models.create_episode(
            conn, experiment_id, agent_id, dataset_revision="sha256:testdataset",
            start_ts="2020-01-02T16:00:00+00:00", end_ts="2020-01-03T16:00:00+00:00", label="DAY 001"
        )
        decision_id = models.create_decision(
            conn, episode_id, agent_id, simulated_ts="2020-01-02T16:00:00+00:00",
            payload={"eligible": ["SPY", "TLT"], "target_weights": {"SPY": 0.5, "TLT": 0.3}},
        )
        order_id = models.create_order(
            conn, decision_id, episode_id, symbol="SPY", side="buy", quantity=10,
            order_type="market", submitted_ts="2020-01-02T16:00:00+00:00",
        )
        fill_id = models.create_fill(
            conn, order_id, fill_ts="2020-01-02T16:00:00+00:00",
            fill_price_cents=32450, fill_quantity=10, commission_cents=100,
        )
        conn.commit()

        row = conn.execute(
            "SELECT f.fill_id, o.symbol, d.episode_id, e.experiment_id, x.dataset_revision, "
            "a.parent_agent_id, a.generation, g.genome_id "
            "FROM fills f "
            "JOIN orders o ON o.order_id = f.order_id "
            "JOIN decisions d ON d.decision_id = o.decision_id "
            "JOIN episodes e ON e.episode_id = d.episode_id "
            "JOIN experiments x ON x.experiment_id = e.experiment_id "
            "JOIN agents a ON a.agent_id = x.agent_id "
            "JOIN genomes g ON g.genome_id = x.genome_id "
            "WHERE f.fill_id = ?",
            (fill_id,),
        ).fetchone()

        self.assertEqual(row["symbol"], "SPY")
        self.assertEqual(row["episode_id"], episode_id)
        self.assertEqual(row["experiment_id"], experiment_id)
        self.assertEqual(row["dataset_revision"], "sha256:testdataset")
        self.assertEqual(row["genome_id"], gid)

        child = conn.execute(
            "SELECT parent_agent_id, generation FROM agents WHERE agent_id = ?",
            (child_agent_id,),
        ).fetchone()
        self.assertEqual(child["parent_agent_id"], agent_id)
        self.assertEqual(child["generation"], 1)

    def _experiment_with_episode(self):
        gid = models.create_genome(self.conn, SAMPLE_GENOME)
        agent_id = models.create_agent(self.conn, gid, generation=0)
        experiment_id = models.create_experiment(
            self.conn, code_revision="runtime-revision", code_dirty=True,
            dataset_revision="sha256:testdataset", random_seed=0, agent_id=agent_id,
            genome_id_=gid, start_state={"cash_cents": 1_000_000},
            replay_window_start="2020-01-01", replay_window_end="2020-01-31",
            execution_assumptions={"commission_bps": 5, "slippage_bps": 5},
        )
        episode_id = models.create_episode(
            self.conn, experiment_id, agent_id, dataset_revision="sha256:testdataset",
            start_ts="2020-01-01", end_ts="2020-01-31",
        )
        self.conn.commit()
        return experiment_id, episode_id

    def test_atomic_pending_running_completed_lifecycle(self):
        experiment_id, episode_id = self._experiment_with_episode()
        models.mark_experiment_running(self.conn, experiment_id)
        models.update_episode_progress(
            self.conn, episode_id, current_ts="2020-01-31", status="COMPLETED"
        )
        models.complete_experiment(self.conn, experiment_id, {"status": "completed", "value": 7})
        self.conn.commit()

        row = self.conn.execute(
            "SELECT status, final_result_json, completed_at FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(json.loads(row["final_result_json"]), {"status": "completed", "value": 7})
        self.assertIsNotNone(row["completed_at"])
        with self.assertRaises(models.InvalidExperimentTransition):
            models.fail_experiment(self.conn, experiment_id, {"status": "failed"})

    def test_incomplete_episode_cannot_complete_experiment(self):
        experiment_id, _ = self._experiment_with_episode()
        models.mark_experiment_running(self.conn, experiment_id)
        with self.assertRaises(models.InvalidExperimentTransition):
            models.complete_experiment(self.conn, experiment_id, {"status": "completed"})
        row = self.conn.execute(
            "SELECT status, final_result_json, completed_at FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["final_result_json"])
        self.assertIsNone(row["completed_at"])

    def test_pending_or_running_experiment_can_fail_with_result(self):
        experiment_id, episode_id = self._experiment_with_episode()
        models.mark_experiment_running(self.conn, experiment_id)
        models.fail_incomplete_episode(self.conn, episode_id)
        models.fail_experiment(self.conn, experiment_id, {"status": "failed", "reason": "injected"})
        self.conn.commit()
        row = self.conn.execute(
            "SELECT status, final_result_json, completed_at FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(json.loads(row["final_result_json"])["reason"], "injected")
        self.assertIsNotNone(row["completed_at"])


if __name__ == "__main__":
    unittest.main()
