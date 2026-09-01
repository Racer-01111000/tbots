import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import build_dataset_revision as bdr
import genome_control
import run_control_episode as rce
from db import init_db  # scripts/lib/db.py; SCHEMA_PATH there resolves to the real repo schema (pure DDL, no dataset-specific content, safe to reuse for a temp test db)

TEST_GENOME = {
    "strategy_family": "trend", "universe": ["CRASH", "STABLE"],
    "momentum_lookbacks": [63, 126, 252], "trend_filter_window": 200, "volatility_window": 63,
    "max_positions": 2, "target_max_exposure": 0.80, "max_asset_weight": 0.60,
    "direction": "long_only", "leverage": "none", "shorting": "none",
    "rebalance_every_n_sessions": 21, "drawdown_halt_pct": 0.12,
}


def _weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _epoch(dt):
    return int(dt.replace(hour=16, tzinfo=timezone.utc).timestamp())


class ControlIntegrationBase(unittest.TestCase):
    N = 380

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        raw_dir = self.root / "data" / "raw"
        raw_dir.mkdir(parents=True)
        self.dates = _weekdays(datetime(2017, 1, 2), self.N)
        days = [_epoch(d) for d in self.dates]

        # CRASH: steady uptrend for 300 sessions (becomes eligible, gets
        # selected), then an 8-session ~28% decline, then flat -- enough
        # to breach -12% portfolio drawdown mid-holding, well before the
        # next scheduled rebalance could react on its own. Small daily
        # noise is real -- a perfectly deterministic ratio every single
        # day produces exactly-zero variance, which is a degenerate input
        # neither implementation should be exercised against (real market
        # data never does this).
        import random
        rnd = random.Random(20170102)
        crash_prices = []
        p = 100.0
        for i in range(self.N):
            if i < 300:
                p *= 1.0007
            elif i < 310:
                p *= 0.94  # ~46% decline over 10 sessions
            crash_prices.append(p * (1 + rnd.uniform(-0.003, 0.003)))

        # STABLE: mild, boring uptrend the whole time -- always eligible,
        # never crashes, so it stays in the picture as a comparison.
        rnd2 = random.Random(99)
        stable_prices = [100.0 * (1.0002 ** i) * (1 + rnd2.uniform(-0.002, 0.002)) for i in range(self.N)]

        for ticker, prices in [("CRASH", crash_prices), ("STABLE", stable_prices)]:
            quote = {"open": prices, "high": [x * 1.002 for x in prices], "low": [x * 0.998 for x in prices],
                     "close": prices, "volume": [1000] * len(prices)}
            raw = {"chart": {"result": [{"meta": {"symbol": ticker}, "timestamp": days,
                   "indicators": {"quote": [quote], "adjclose": [{"adjclose": list(prices)}]},
                   "events": {}}], "error": None}}
            (raw_dir / f"{ticker}.json").write_bytes(json.dumps(raw).encode("utf-8"))

        (raw_dir / "manifest.json").write_text(json.dumps({"source": "test", "entries": {
            "CRASH": {"ticker": "CRASH", "fetched_at": "2026-01-01T00:00:00+00:00"},
            "STABLE": {"ticker": "STABLE", "fetched_at": "2026-01-01T00:00:00+00:00"},
        }}))

        orig = (bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE)
        bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = (
            self.root, raw_dir, self.root / "data" / "normalized", ["CRASH", "STABLE"]
        )
        try:
            result = bdr.build()
        finally:
            bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = orig
        self.dataset_revision = result["dataset_revision"]
        self.d = [dt.strftime("%Y-%m-%d") for dt in self.dates]

        self._orig_genome = genome_control.CONTROL_GENOME
        genome_control.CONTROL_GENOME = TEST_GENOME
        rce.CONTROL_GENOME = TEST_GENOME
        self.db_path = self.root / "test.db"
        init_db(db_path=self.db_path).close()

    def tearDown(self):
        genome_control.CONTROL_GENOME = self._orig_genome
        rce.CONTROL_GENOME = self._orig_genome
        self.tmpdir.cleanup()

    def run_episode(self, **kw):
        return rce.run_control_episode(self.root, self.dataset_revision, self.d[0], self.d[-1],
                                        db_path=self.db_path, **kw)


class DrawdownHaltIntegrationTestCase(ControlIntegrationBase):
    def test_halt_triggers_and_liquidates_and_stays_halted(self):
        result = self.run_episode()
        self.assertTrue(result["halted"])
        self.assertLessEqual(result["max_drawdown"], -TEST_GENOME["drawdown_halt_pct"])

        halt_events = [r for r in result["rebalance_log"] if r["kind"] == "halt_liquidation"]
        self.assertEqual(len(halt_events), 1)  # triggers exactly once, never re-triggers
        self.assertEqual(halt_events[0]["weights"], {})

        halt_step = halt_events[0]["step"]
        # every equity-curve row after the halt step shows halted=True and
        # zero holdings (fully liquidated, no new positions re-opened)
        for row in result["equity_curve"]:
            if row["step"] > halt_step + 1:  # +1: the liquidation itself fills one step later
                self.assertTrue(row["halted"])
        final_positions = result["equity_curve"][-1]["positions"]
        self.assertEqual(final_positions, {})  # nothing re-opened after halt

    def test_no_rebalance_orders_after_halt_even_on_schedule(self):
        result = self.run_episode()
        halt_step = next(r["step"] for r in result["rebalance_log"] if r["kind"] == "halt_liquidation")
        post_halt_rebalances = [r for r in result["rebalance_log"]
                                 if r["step"] > halt_step and r["kind"] == "rebalance"]
        for r in post_halt_rebalances:
            self.assertEqual(r["weights"], {})  # scheduled rebalances after halt propose nothing


class EquityCurveArtifactTestCase(ControlIntegrationBase):
    def test_equity_curve_csv_round_trips_through_csv_reader(self):
        """Regression: positions_json (e.g. {"EEM": 10701, "GLD": 5607})
        contains embedded commas. An earlier version hand-wrote CSV rows
        with an f-string, which silently corrupted the file -- csv.reader
        would split the JSON's internal comma into extra columns. Must
        round-trip through a real CSV parser, not just "the file exists"."""
        result = self.run_episode()
        self.assertIsNotNone(result["equity_curve_path"])
        import csv as csv_module
        with open(result["equity_curve_path"], newline="") as f:
            rows = list(csv_module.DictReader(f))
        self.assertEqual(len(rows), result["step_count"])
        for row in rows:
            self.assertEqual(set(row.keys()), {"step", "true_ts", "masked_day", "cash_cents",
                                                 "equity_cents", "drawdown", "halted", "positions_json"})
            self.assertIsNone(row.get(None))  # no overflow column from a comma-splitting bug
            parsed_positions = json.loads(row["positions_json"])
            self.assertIsInstance(parsed_positions, dict)


class DeterministicRerunTestCase(ControlIntegrationBase):
    def test_full_rerun_identical_in_every_required_field(self):
        r1 = self.run_episode(verify_every_rebalance=True)
        r2 = self.run_episode(verify_every_rebalance=True)

        strip_order_id = lambda fills: [{k: v for k, v in f.items() if k != "order_db_id"} for f in fills]

        self.assertEqual(r1["step_count"], r2["step_count"])
        self.assertEqual(r1["rebalance_log"], r2["rebalance_log"])
        self.assertEqual(strip_order_id(r1["fill_log"]), strip_order_id(r2["fill_log"]))
        self.assertEqual(r1["final_equity_cents"], r2["final_equity_cents"])
        self.assertEqual(r1["max_drawdown"], r2["max_drawdown"])
        self.assertEqual(r1["genome_id"], r2["genome_id"])
        self.assertEqual(r1["verifier_disagreements"], [])
        self.assertEqual(r2["verifier_disagreements"], [])
        self.assertEqual(r1["equity_curve"], r2["equity_curve"])

    def test_completed_experiment_persists_terminal_result(self):
        result = self.run_episode(write_equity_curve=False)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, final_result_json, completed_at FROM experiments WHERE experiment_id = ?",
            (result["experiment_id"],),
        ).fetchone()
        episode_status = conn.execute(
            "SELECT status FROM episodes WHERE episode_id = ?", (result["episode_id"],)
        ).fetchone()["status"]
        conn.close()
        self.assertEqual(episode_status, "COMPLETED")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(json.loads(row["final_result_json"])["verifier"]["status"], "agreed")
        self.assertIsNotNone(row["completed_at"])


class FailurePersistenceTestCase(ControlIntegrationBase):
    def test_runtime_failure_persists_failed_experiment_and_episode(self):
        with mock.patch.object(rce.control_agent, "decide", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.run_episode(write_equity_curve=False)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        experiment = conn.execute(
            "SELECT status, final_result_json, completed_at FROM experiments"
        ).fetchone()
        episode = conn.execute("SELECT status FROM episodes").fetchone()
        conn.close()
        self.assertEqual(experiment["status"], "failed")
        self.assertEqual(json.loads(experiment["final_result_json"])["error_type"], "RuntimeError")
        self.assertIsNotNone(experiment["completed_at"])
        self.assertEqual(episode["status"], "FAILED")

    def test_injected_verifier_disagreement_fails_closed_and_records_provenance(self):
        with mock.patch.object(rce, "get_code_revision",
                               return_value=("runtime-deadbeef", True)):
            with mock.patch.object(rce.verifier, "compare_decision",
                                   return_value=["injected verifier mismatch"]):
                with self.assertRaises(rce.VerifierDisagreement):
                    self.run_episode(write_equity_curve=False)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        experiment = conn.execute("SELECT * FROM experiments").fetchone()
        decision = conn.execute("SELECT payload_json FROM decisions").fetchone()
        conn.close()
        final_result = json.loads(experiment["final_result_json"])
        decision_payload = json.loads(decision["payload_json"])
        self.assertEqual(experiment["status"], "failed")
        self.assertEqual(experiment["code_revision"], "runtime-deadbeef")
        self.assertEqual(experiment["code_dirty"], 1)
        self.assertEqual(final_result["verifier"]["status"], "disagreed")
        self.assertIn("injected verifier mismatch",
                      final_result["verifier"]["outcomes"][0]["disagreements"])
        self.assertEqual(decision_payload["kind"], "rebalance_blocked")
        self.assertEqual(decision_payload["verifier"]["status"], "disagreed")


class SlippagePersistenceAuditTestCase(ControlIntegrationBase):
    def test_buy_and_sell_fill_slippage_is_persisted(self):
        result = self.run_episode(write_equity_curve=False)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            "SELECT f.order_id, o.side, f.slippage_cents FROM fills f "
            "JOIN orders o ON o.order_id = f.order_id"
        ).fetchall()
        conn.close()
        expected = {fill["order_db_id"]: fill["slippage_cents"] for fill in result["fill_log"]}
        self.assertEqual({side for _, side, _ in stored}, {"buy", "sell"})
        for order_id, _, slippage_cents in stored:
            self.assertEqual(slippage_cents, expected[order_id])
            self.assertGreater(slippage_cents, 0)


if __name__ == "__main__":
    unittest.main()
