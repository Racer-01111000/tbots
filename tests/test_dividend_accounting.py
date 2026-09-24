"""Focused correctness tests for the S4.8 dividend-accounting repair
(execution.dividend_amount_dollars / Portfolio.credit_dividend), plus two
integration-level tests (8, 9) that drive a real, synthetic, verified
dataset through ReplayEngine to prove the repair reaches daily equity
without double-counting and never touches indicator-visible data.

Per Rick's GO "TBOTS RESEARCH EVIDENCE PRESERVATION + DIVIDEND ACCOUNTING
REPAIR / REVALIDATION," Phase B, B4.
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import build_dataset_revision as bdr
import execution
from replay import AgentView, ReplayEngine

WEEKDAYS = [
    (2021, 1, 4), (2021, 1, 5), (2021, 1, 6), (2021, 1, 7), (2021, 1, 8),
    (2021, 1, 11), (2021, 1, 12),
]
ISO = lambda t: f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"


def _epoch(y, m, d, hour=16):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def _fixture_raw_for(ticker, dates, base_price=100.0, dividend_on=None, dividend_amount=0.5):
    """Same shape as tests/test_replay.py's helper: a minimal Yahoo
    chart-API-like response with a controlled events.dividends entry."""
    days = [_epoch(*d) for d in dates]
    n = len(days)
    quote = {
        "open": [base_price for _ in range(n)],
        "high": [base_price + 0.5 for _ in range(n)],
        "low": [base_price - 0.5 for _ in range(n)],
        "close": [base_price for _ in range(n)],
        "volume": [1000 for _ in range(n)],
    }
    adjclose = [v - 1.0 for v in quote["close"]]
    events = {}
    if dividend_on is not None:
        div_epoch = _epoch(*dividend_on)
        events["dividends"] = {str(div_epoch): {"amount": dividend_amount, "date": div_epoch}}
    raw = {
        "chart": {
            "result": [{
                "meta": {"symbol": ticker},
                "timestamp": days,
                "indicators": {"quote": [quote], "adjclose": [{"adjclose": adjclose}]},
                "events": events,
            }],
            "error": None,
        }
    }
    return json.dumps(raw).encode("utf-8")


class PortfolioCreditDividendTestCase(unittest.TestCase):
    """Items 1, 2, 4, 5, 6, 7: pure Portfolio-level entitlement/amount tests."""

    def test_1_eligible_long_position_receives_dividend_cash(self):
        p = execution.Portfolio(1_000_000_00)
        p.apply_fill("A", "buy", 100, 10_000)
        before = p.cash_cents
        credited = p.credit_dividend("A", 0.5)
        self.assertEqual(credited, 5_000)  # 100 shares x $0.50 = $50.00 = 5000 cents
        self.assertEqual(p.cash_cents, before + 5_000)
        self.assertEqual(p.total_dividend_cents, 5_000)

    def test_2_no_position_receives_no_dividend(self):
        p = execution.Portfolio(1_000_000_00)
        before = p.cash_cents
        credited = p.credit_dividend("A", 0.5)
        self.assertEqual(credited, 0)
        self.assertEqual(p.cash_cents, before)
        self.assertEqual(p.total_dividend_cents, 0)

    def test_3_post_event_acquisition_receives_no_retroactive_dividend(self):
        """Mirrors the real call-site ordering: credit_dividend is invoked
        for the day's holdings BEFORE that day's fill is applied. A buy
        that fills ON the ex-date must not have been eligible for it."""
        p = execution.Portfolio(1_000_000_00)
        # step T: nothing held yet -- credit first (per the real ordering),
        # THEN apply today's fill (a buy that only takes effect after).
        credited_at_fill_time = p.credit_dividend("A", 0.5)
        p.apply_fill("A", "buy", 100, 10_000)
        self.assertEqual(credited_at_fill_time, 0)
        self.assertEqual(p.total_dividend_cents, 0)
        self.assertEqual(p.shares_of("A"), 100)  # the fill itself still succeeded

    def test_4_dividend_credited_exactly_once(self):
        p = execution.Portfolio(1_000_000_00)
        p.apply_fill("A", "buy", 100, 10_000)
        corporate_actions = [None, None, json.dumps({"dividend_amount": 0.5}), None, None]
        total_credits = 0
        for ca in corporate_actions:
            div = execution.dividend_amount_dollars(ca)
            if div:
                p.credit_dividend("A", div)
                total_credits += 1
        self.assertEqual(total_credits, 1)
        self.assertEqual(p.total_dividend_cents, 5_000)

    def test_5_amount_scales_correctly_with_share_quantity(self):
        p100 = execution.Portfolio(1_000_000_00)
        p100.apply_fill("A", "buy", 100, 10_000)
        c100 = p100.credit_dividend("A", 0.5)

        p250 = execution.Portfolio(1_000_000_00)
        p250.apply_fill("A", "buy", 250, 10_000)
        c250 = p250.credit_dividend("A", 0.5)

        self.assertEqual(c100, 5_000)
        self.assertEqual(c250, 12_500)
        self.assertAlmostEqual(c250 / c100, 2.5, places=9)

    def test_6_multiple_dividend_events_accumulate_correctly(self):
        p = execution.Portfolio(1_000_000_00)
        p.apply_fill("A", "buy", 100, 10_000)
        cash_after_fill = p.cash_cents
        first = p.credit_dividend("A", 0.5)
        second = p.credit_dividend("A", 0.30)
        self.assertEqual(first, 5_000)
        self.assertEqual(second, 3_000)
        self.assertEqual(p.total_dividend_cents, 8_000)
        self.assertEqual(p.cash_cents, cash_after_fill + 8_000)

    def test_7_unrelated_assets_are_unaffected(self):
        p = execution.Portfolio(1_000_000_00)
        p.apply_fill("A", "buy", 100, 10_000)  # B never bought
        cash_before_credit = p.cash_cents
        credited_a = p.credit_dividend("A", 0.5)
        credited_b = p.credit_dividend("B", 0.5)
        self.assertEqual(credited_a, 5_000)
        self.assertEqual(credited_b, 0)
        self.assertEqual(p.cash_cents, cash_before_credit + 5_000)

    def test_dividend_amount_dollars_parses_present_absent_and_split_only(self):
        self.assertIsNone(execution.dividend_amount_dollars(None))
        self.assertIsNone(execution.dividend_amount_dollars(""))
        self.assertIsNone(execution.dividend_amount_dollars(json.dumps({"split": {"numerator": 2}})))
        self.assertEqual(execution.dividend_amount_dollars(json.dumps({"dividend_amount": 1.58})), 1.58)


class DividendIntegrationTestCase(unittest.TestCase):
    """Items 8, 9, 10: drive a real, hash-verified synthetic dataset through
    ReplayEngine with a minimal buy-and-hold loop that reproduces the real
    orchestration's step ordering (credit today's dividend, THEN apply
    today's fill, THEN mark equity on RAW close)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        raw_dir = self.root / "data" / "raw"
        raw_dir.mkdir(parents=True)

        self.dividend_date = WEEKDAYS[3]
        specs = [("AAA", WEEKDAYS, {"dividend_on": self.dividend_date, "dividend_amount": 0.5})]
        manifest_entries = {}
        for ticker, dates, kwargs in specs:
            data = _fixture_raw_for(ticker, dates, **kwargs)
            (raw_dir / f"{ticker}.json").write_bytes(data)
            manifest_entries[ticker] = {"ticker": ticker, "fetched_at": "2026-01-01T00:00:00+00:00"}
        (raw_dir / "manifest.json").write_text(json.dumps({"source": "test", "entries": manifest_entries}))

        norm_dir = self.root / "data" / "normalized"
        orig = (bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE)
        bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = self.root, raw_dir, norm_dir, ["AAA"]
        try:
            result = bdr.build()
        finally:
            bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = orig
        self.dataset_revision = result["dataset_revision"]
        self.d = [ISO(t) for t in WEEKDAYS]

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_buy_and_hold(self, *, credit_dividends: bool) -> dict:
        """100 shares of AAA bought at step 0's open, held for the whole
        window, no other trades -- the minimal loop needed to observe the
        dividend's effect on daily equity in isolation. Step ordering
        mirrors run_control_episode.py's real state machine exactly (credit
        -> fill -> mark on RAW close)."""
        engine = ReplayEngine(self.root, self.dataset_revision, self.d[0], self.d[-1])
        view = AgentView(engine)
        portfolio = execution.Portfolio(1_000_000_00)
        pending_buy = True
        equity_curve = []
        adjusted_close_seen = []
        step = 0
        while True:
            obs = view.observe()
            a = obs["assets"]["AAA"]

            if credit_dividends and a["available"]:
                div = execution.dividend_amount_dollars(a["corporate_action"])
                if div:
                    portfolio.credit_dividend("AAA", div)

            if pending_buy:
                open_cents = round(float(a["open"]) * 100)
                portfolio.apply_fill("AAA", "buy", 100, open_cents)
                pending_buy = False

            mark_cents = round(float(a["close"]) * 100)
            equity_curve.append(portfolio.equity_cents({"AAA": mark_cents}))
            adjusted_close_seen.append(a["adjusted_close"])

            step += 1
            if not engine.advance():
                break
        return {
            "final_equity_cents": equity_curve[-1],
            "total_dividend_cents": portfolio.total_dividend_cents,
            "cash_cents": portfolio.cash_cents,
            "adjusted_close_seen": adjusted_close_seen,
        }

    def test_8_daily_equity_reflects_dividend_cash_without_adjusted_price_double_counting(self):
        with_repair = self._run_buy_and_hold(credit_dividends=True)
        without_repair = self._run_buy_and_hold(credit_dividends=False)

        self.assertGreater(with_repair["total_dividend_cents"], 0)
        # marking uses RAW close in both cases (unchanged by the repair);
        # the ONLY difference the repair introduces is the credited cash,
        # so the equity delta must equal the dividend credited exactly --
        # not double (which double-counting would produce) and not zero
        # (which "repair did nothing" would produce).
        self.assertEqual(
            with_repair["final_equity_cents"] - without_repair["final_equity_cents"],
            with_repair["total_dividend_cents"],
        )

    def test_9_indicators_signals_unchanged_by_accounting_repair(self):
        with_repair = self._run_buy_and_hold(credit_dividends=True)
        without_repair = self._run_buy_and_hold(credit_dividends=False)
        self.assertEqual(with_repair["adjusted_close_seen"], without_repair["adjusted_close_seen"])

    def test_9b_credit_dividend_observation_leaves_market_view_untouched(self):
        engine = ReplayEngine(self.root, self.dataset_revision, self.d[0], self.d[-1])
        view = AgentView(engine)
        portfolio = execution.Portfolio(1_000_000_00)
        portfolio.apply_fill("AAA", "buy", 100, 10_000)
        before = copy.deepcopy(view.observe())
        portfolio.credit_dividend("AAA", 0.5)
        after = view.observe()
        self.assertEqual(before, after)

    def test_10_deterministic_repeated_execution_gives_identical_output(self):
        first = self._run_buy_and_hold(credit_dividends=True)
        second = self._run_buy_and_hold(credit_dividends=True)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
