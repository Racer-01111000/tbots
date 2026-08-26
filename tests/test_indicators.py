import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import build_dataset_revision as bdr
import indicators
import verifier
from replay import AgentView, ReplayEngine

GENOME = {
    "momentum_lookbacks": [63, 126, 252], "trend_filter_window": 200,
    "volatility_window": 63, "max_positions": 3, "universe": ["LIN", "SHORT"],
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


class IndicatorTestCase(unittest.TestCase):
    """LIN: price[i] = 100 + i, exactly 300 bars -- every indicator value
    is hand-computable. SHORT: only 30 bars, exercises insufficient-
    history behavior for every lookback."""

    N = 300

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        raw_dir = self.root / "data" / "raw"
        raw_dir.mkdir(parents=True)

        self.dates = _weekdays(datetime(2019, 1, 2), self.N)
        days = [_epoch(d) for d in self.dates]

        lin_prices = [100.0 + i for i in range(self.N)]
        for ticker, prices, ts in [("LIN", lin_prices, days), ("SHORT", lin_prices[:30], days[:30])]:
            quote = {"open": prices, "high": [p + 0.5 for p in prices], "low": [p - 0.5 for p in prices],
                     "close": prices, "volume": [1000] * len(prices)}
            raw = {"chart": {"result": [{"meta": {"symbol": ticker}, "timestamp": ts,
                   "indicators": {"quote": [quote], "adjclose": [{"adjclose": list(prices)}]},
                   "events": {}}], "error": None}}
            (raw_dir / f"{ticker}.json").write_bytes(json.dumps(raw).encode("utf-8"))

        (raw_dir / "manifest.json").write_text(json.dumps({"source": "test", "entries": {
            "LIN": {"ticker": "LIN", "fetched_at": "2026-01-01T00:00:00+00:00"},
            "SHORT": {"ticker": "SHORT", "fetched_at": "2026-01-01T00:00:00+00:00"},
        }}))

        orig = (bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE)
        bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = (
            self.root, raw_dir, self.root / "data" / "normalized", ["LIN", "SHORT"]
        )
        try:
            result = bdr.build()
        finally:
            bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = orig
        self.dataset_revision = result["dataset_revision"]
        self.d = [dt.strftime("%Y-%m-%d") for dt in self.dates]

    def tearDown(self):
        self.tmpdir.cleanup()

    def view_at(self, step_index):
        eng = ReplayEngine(self.root, self.dataset_revision, self.d[0], self.d[-1])
        for _ in range(step_index):
            eng.advance()
        return AgentView(eng), eng

    def test_momentum_63_126_252_hand_computed(self):
        # T = index 299 (last bar), price = 399 = 100 + 299
        view, _ = self.view_at(self.N - 1)
        m63 = indicators.momentum(view, "LIN", 63)
        m126 = indicators.momentum(view, "LIN", 126)
        m252 = indicators.momentum(view, "LIN", 252)
        self.assertAlmostEqual(m63, 399.0 / (100.0 + (299 - 63)) - 1.0, places=12)
        self.assertAlmostEqual(m126, 399.0 / (100.0 + (299 - 126)) - 1.0, places=12)
        self.assertAlmostEqual(m252, 399.0 / (100.0 + (299 - 252)) - 1.0, places=12)

    def test_moving_average_200_hand_computed(self):
        view, _ = self.view_at(self.N - 1)
        ma = indicators.moving_average(view, "LIN", 200)
        expected = sum(100.0 + i for i in range(100, 300)) / 200.0
        self.assertAlmostEqual(ma, expected, places=9)

    def test_insufficient_history_is_not_eligible_never_substituted(self):
        # at step 50, LIN doesn't have 63 bars yet either
        view, _ = self.view_at(50)
        ev = indicators.evaluate_symbol(view, "LIN", GENOME)
        self.assertFalse(ev["eligible"])
        self.assertEqual(ev["reason"], "insufficient_history_momentum")
        self.assertIsNone(ev["m63"])  # never substituted with a shorter window

    def test_short_series_never_becomes_eligible_within_its_30_bars(self):
        view, _ = self.view_at(29)
        ev = indicators.evaluate_symbol(view, "SHORT", GENOME)
        self.assertFalse(ev["eligible"])
        self.assertIn(ev["reason"], ("insufficient_history_momentum", "not_available"))

    def test_eligibility_true_once_full_history_and_uptrend(self):
        view, _ = self.view_at(self.N - 1)
        ev = indicators.evaluate_symbol(view, "LIN", GENOME)
        self.assertTrue(ev["eligible"])
        self.assertTrue(ev["trend_pass"])
        self.assertGreater(ev["m252"], 0)
        self.assertAlmostEqual(ev["ensemble_momentum"], (ev["m63"] + ev["m126"] + ev["m252"]) / 3.0, places=12)

    def test_ranking_deterministic_tie_break_by_symbol_ascending(self):
        evaluations = {
            "ZZZ": {"eligible": True, "ensemble_momentum": 0.10},
            "AAA": {"eligible": True, "ensemble_momentum": 0.10},
            "MMM": {"eligible": True, "ensemble_momentum": 0.20},
            "NOT": {"eligible": False, "ensemble_momentum": 0.99},
        }
        selected = indicators.rank_and_select(evaluations, max_positions=3)
        self.assertEqual(selected, ["MMM", "AAA", "ZZZ"])  # momentum desc, then symbol asc on the tie

    def test_ranking_respects_max_positions_cap(self):
        evaluations = {s: {"eligible": True, "ensemble_momentum": float(i)} for i, s in enumerate("ABCDE")}
        selected = indicators.rank_and_select(evaluations, max_positions=3)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected, ["E", "D", "C"])

    def test_volatility_matches_sample_stdev_of_simple_returns(self):
        view, _ = self.view_at(self.N - 1)
        vol = indicators.volatility(view, "LIN", 63)
        rows = view.history("LIN", 64)
        prices = [float(r["adjusted_close"]) for r in rows]
        rets = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]
        mean_r = sum(rets) / len(rets)
        var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        self.assertAlmostEqual(vol, var ** 0.5, places=12)

    def test_independent_verifier_agrees_on_every_metric(self):
        view, _ = self.view_at(self.N - 1)
        control_ev = indicators.evaluate_symbol(view, "LIN", GENOME)
        v_ev = verifier.evaluate_symbol_v(view, "LIN", GENOME)
        for field in ("m63", "m126", "m252", "ma200", "trend_pass", "ensemble_momentum",
                      "volatility", "eligible"):
            self.assertTrue(verifier._rel_close(control_ev[field], v_ev[field]),
                             f"{field}: control={control_ev[field]} verifier={v_ev[field]}")


if __name__ == "__main__":
    unittest.main()
