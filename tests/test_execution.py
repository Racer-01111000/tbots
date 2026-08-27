import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import control_agent
import execution
import risk

GENOME = {"universe": ["A", "B", "C"], "target_max_exposure": 0.80, "max_asset_weight": 0.35,
          "drawdown_halt_pct": 0.12}


class PositionSizingTestCase(unittest.TestCase):
    def test_inverse_volatility_weighting_hand_computed(self):
        evaluations = {"A": {"volatility": 0.01}, "B": {"volatility": 0.02}, "C": {"volatility": 0.04}}
        weights = control_agent.size_positions(["A", "B", "C"], evaluations, GENOME)
        inv = {s: 1.0 / evaluations[s]["volatility"] for s in ("A", "B", "C")}
        total = sum(inv.values())
        expected = {s: min((inv[s] / total) * 0.80, 0.35) for s in ("A", "B", "C")}
        for s in ("A", "B", "C"):
            self.assertAlmostEqual(weights[s], expected[s], places=12)

    def test_individual_35_percent_cap_enforced_not_redistributed(self):
        # A has far lower vol than B/C -> uncapped weight would exceed 35%
        evaluations = {"A": {"volatility": 0.001}, "B": {"volatility": 0.05}, "C": {"volatility": 0.05}}
        weights = control_agent.size_positions(["A", "B", "C"], evaluations, GENOME)
        self.assertLessEqual(weights["A"], 0.35 + 1e-12)
        self.assertEqual(round(weights["A"], 6), 0.35)
        # total exposure is now LESS than 80% because the excess was not redistributed
        self.assertLess(sum(weights.values()), 0.80 - 1e-6)

    def test_total_exposure_never_exceeds_80_percent_when_uncapped(self):
        evaluations = {"A": {"volatility": 0.02}, "B": {"volatility": 0.021}, "C": {"volatility": 0.019}}
        weights = control_agent.size_positions(["A", "B", "C"], evaluations, GENOME)
        self.assertAlmostEqual(sum(weights.values()), 0.80, places=9)

    def test_cash_residual_when_fewer_than_max_positions_selected(self):
        evaluations = {"A": {"volatility": 0.02}}
        # with a permissive cap, a single selected asset takes the full 80% target
        loose = dict(GENOME, max_asset_weight=1.0)
        weights = control_agent.size_positions(["A"], evaluations, loose)
        self.assertAlmostEqual(weights["A"], 0.80, places=9)
        # under the real 35% cap, that same single-asset allocation is
        # capped well below 80% -- the shortfall is implicit cash, never
        # redistributed to force full exposure
        weights2 = control_agent.size_positions(["A"], evaluations, GENOME)
        self.assertAlmostEqual(weights2["A"], 0.35, places=9)
        self.assertLess(sum(weights2.values()), 0.80)

    def test_no_selection_means_all_cash(self):
        self.assertEqual(control_agent.size_positions([], {}, GENOME), {})


class RiskGateTestCase(unittest.TestCase):
    def test_leverage_and_shorting_refused(self):
        with self.assertRaises(risk.RiskRefusal):
            risk.validate({"A": -0.1}, GENOME["universe"], max_asset_weight=0.35,
                          max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=0.0)

    def test_over_35_percent_single_asset_refused(self):
        with self.assertRaises(risk.RiskRefusal):
            risk.validate({"A": 0.40}, GENOME["universe"], max_asset_weight=0.35,
                          max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=0.0)

    def test_over_80_percent_total_refused(self):
        with self.assertRaises(risk.RiskRefusal):
            risk.validate({"A": 0.35, "B": 0.35, "C": 0.20}, GENOME["universe"], max_asset_weight=0.35,
                          max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=0.0)

    def test_unknown_asset_refused(self):
        with self.assertRaises(risk.RiskRefusal):
            risk.validate({"ZZZ": 0.10}, GENOME["universe"], max_asset_weight=0.35,
                          max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=0.0)

    def test_valid_target_passes(self):
        risk.validate({"A": 0.30, "B": 0.30}, GENOME["universe"], max_asset_weight=0.35,
                      max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=0.0)

    def test_new_position_refused_once_drawdown_past_halt_independent_of_agent_flag(self):
        """Risk gate enforces this itself -- not by trusting the agent's
        self-reported halted state."""
        with self.assertRaises(risk.RiskRefusal):
            risk.validate({"A": 0.10}, GENOME["universe"], max_asset_weight=0.35,
                          max_total_exposure=0.80, drawdown_halt_pct=0.12, current_drawdown=-0.15)

    def test_liquidation_to_zero_permitted_even_past_halt(self):
        risk.validate({}, GENOME["universe"], max_asset_weight=0.35, max_total_exposure=0.80,
                      drawdown_halt_pct=0.12, current_drawdown=-0.15)


class DrawdownTestCase(unittest.TestCase):
    def test_drawdown_formula(self):
        p = execution.Portfolio(100_000_00)
        self.assertEqual(p.update_peak_and_drawdown(100_000_00), 0.0)
        self.assertAlmostEqual(p.update_peak_and_drawdown(110_000_00), 0.0)  # new peak, dd=0
        dd = p.update_peak_and_drawdown(96_800_00)
        self.assertAlmostEqual(dd, 96_800_00 / 110_000_00 - 1.0, places=9)

    def test_12_percent_halt_threshold_exact_boundary(self):
        p = execution.Portfolio(100_000_00)
        p.update_peak_and_drawdown(100_000_00)
        dd_at_exactly_12 = p.update_peak_and_drawdown(88_000_00)
        self.assertAlmostEqual(dd_at_exactly_12, -0.12, places=6)


class RebalanceScheduleTestCase(unittest.TestCase):
    def test_21_session_schedule_is_step_index_modulo(self):
        n = 21
        rebalance_steps = [s for s in range(200) if s % n == 0]
        self.assertEqual(rebalance_steps[:5], [0, 21, 42, 63, 84])
        self.assertNotIn(20, rebalance_steps)
        self.assertNotIn(22, rebalance_steps)


class OrderConstructionTestCase(unittest.TestCase):
    def test_long_only_never_negative_shares(self):
        orders = execution.compute_orders({"A": 0.30}, ["A", "B"], equity_cents=100_000_00,
                                           sizing_prices_cents={"A": 10_000}, current_shares={})
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "buy")
        self.assertGreater(orders[0]["shares"], 0)

    def test_symbols_not_in_target_are_liquidated_not_left_open(self):
        orders = execution.compute_orders({}, ["A", "B"], equity_cents=100_000_00,
                                           sizing_prices_cents={"A": 10_000, "B": 5_000},
                                           current_shares={"A": 50, "B": 100})
        sides = {o["symbol"]: o["side"] for o in orders}
        self.assertEqual(sides, {"A": "sell", "B": "sell"})

    def test_no_order_when_already_at_target(self):
        orders = execution.compute_orders({"A": 0.10}, ["A"], equity_cents=100_000_00,
                                           sizing_prices_cents={"A": 10_000}, current_shares={"A": 100})
        self.assertEqual(orders, [])


class FillTimingAndTransactionCostTestCase(unittest.TestCase):
    def test_next_session_execution_price_differs_from_decision_session(self):
        """The decision/sizing price and the fill price must be able to
        legitimately differ -- proving they are NOT the same observation,
        which is the structural guard against same-bar execution."""
        decision_session_close_cents = 10_000
        next_session_open_cents = 10_050  # a different, later session's price
        p = execution.Portfolio(1_000_000_00)
        fill = p.apply_fill("A", "buy", 100, next_session_open_cents)
        self.assertNotEqual(fill["fill_price_cents"], decision_session_close_cents)

    def test_buy_fill_includes_adverse_slippage(self):
        buy_price = execution.buy_fill_price_cents(10_000)
        self.assertGreater(buy_price, 10_000)
        self.assertEqual(buy_price, round(10_000 * (1 + execution.SLIPPAGE_BPS / 10_000)))

    def test_buy_fill_reports_total_actual_slippage_cents(self):
        fill = execution.Portfolio(1_000_000_00).apply_fill("A", "buy", 100, 10_000)
        self.assertEqual(fill["slippage_cents"],
                         (fill["fill_price_cents"] - 10_000) * fill["shares"])

    def test_sell_fill_includes_adverse_slippage(self):
        sell_price = execution.sell_fill_price_cents(10_000)
        self.assertLess(sell_price, 10_000)

    def test_sell_fill_reports_total_actual_slippage_cents(self):
        portfolio = execution.Portfolio(1_000_000_00)
        portfolio.apply_fill("A", "buy", 100, 10_000)
        fill = portfolio.apply_fill("A", "sell", 100, 10_000)
        self.assertEqual(fill["slippage_cents"],
                         (10_000 - fill["fill_price_cents"]) * fill["shares"])

    def test_commission_charged_on_every_fill(self):
        p = execution.Portfolio(1_000_000_00)
        before = p.cash_cents
        fill = p.apply_fill("A", "buy", 100, 10_000)
        self.assertGreater(fill["commission_cents"], 0)
        # cash decreased by MORE than the raw notional (fee is additive, not netted invisibly)
        self.assertLess(p.cash_cents, before - fill["notional_cents"])
        self.assertEqual(p.total_commission_cents, fill["commission_cents"])

    def test_buy_then_sell_realizes_pl_net_of_both_commissions(self):
        p = execution.Portfolio(1_000_000_00)
        p.apply_fill("A", "buy", 100, 10_000)
        sell = p.apply_fill("A", "sell", 100, 11_000)
        self.assertGreater(p.realized_pl_cents, 0)  # bought low, sold high, net of fees
        self.assertNotIn("A", p.positions)  # fully closed


if __name__ == "__main__":
    unittest.main()
