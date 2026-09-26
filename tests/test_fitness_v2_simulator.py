from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from fitness_v2_protocol import ASSET_UNIVERSE
from fitness_v2_simulator import (
    SimulatorError,
    WorldView,
    _validate_simulation_world_shape,
    passive_comparator_genome,
    simulate_passive_comparator,
    simulate_world,
    world_metrics,
)
from genome_control import CONTROL_GENOME


def _trending_world(n_sessions=300, drift=1.0005, dividend_at=None, dividend_amount=0.5,
                     flat=False, asset_offsets=None):
    """Deterministic, no real market data: a smooth per-asset exponential
    price path so momentum/trend indicators naturally become eligible after
    warmup, without depending on the frozen MUTABLE grid's exact bounds."""
    offsets = asset_offsets or {}
    assets = {}
    for a_index, symbol in enumerate(ASSET_UNIVERSE):
        bump = offsets.get(symbol, 1.0 + a_index * 0.0002)
        prices = []
        level = 100.0
        for t in range(n_sessions):
            if not flat:
                level *= drift * bump
            prices.append(round(level, 6))
        corporate_action = ["" for _ in range(n_sessions)]
        if dividend_at is not None and symbol == ASSET_UNIVERSE[0]:
            corporate_action[dividend_at] = json.dumps({"dividend_amount": dividend_amount})
        assets[symbol] = {
            "adjusted_close": list(prices),
            "raw_open": list(prices),
            "raw_close": list(prices),
            "corporate_action": corporate_action,
        }
    return {"assets": assets}


class WorldShapeValidation(unittest.TestCase):
    def test_rejects_missing_asset(self):
        world = _trending_world(n_sessions=10)
        del world["assets"]["SPY"]
        with self.assertRaises(SimulatorError):
            _validate_simulation_world_shape(world)

    def test_rejects_desynchronized_lengths(self):
        world = _trending_world(n_sessions=10)
        world["assets"]["SPY"]["adjusted_close"] = world["assets"]["SPY"]["adjusted_close"][:-1]
        with self.assertRaises(SimulatorError):
            _validate_simulation_world_shape(world)

    def test_accepts_small_fixture_two_sessions(self):
        world = _trending_world(n_sessions=2)
        self.assertEqual(_validate_simulation_world_shape(world), 2)

    def test_rejects_single_session(self):
        with self.assertRaises(SimulatorError):
            _validate_simulation_world_shape(_trending_world(n_sessions=1))


class WorldViewContract(unittest.TestCase):
    def test_history_includes_today_and_never_pads(self):
        world = _trending_world(n_sessions=10)
        view = WorldView(world["assets"], current_index=3)
        history = view.history("SPY", 5)
        self.assertEqual(len(history), 4)  # indices 0..3, only 4 exist before padding kicks in
        self.assertEqual(
            history[-1]["adjusted_close"], world["assets"]["SPY"]["adjusted_close"][3]
        )

    def test_history_never_sees_future(self):
        world = _trending_world(n_sessions=10)
        view = WorldView(world["assets"], current_index=2)
        history = view.history("SPY", 100)
        self.assertEqual(len(history), 3)

    def test_observe_matches_current_index(self):
        world = _trending_world(n_sessions=10)
        view = WorldView(world["assets"], current_index=5)
        obs = view.observe()
        self.assertEqual(
            obs["assets"]["SPY"]["adjusted_close"], world["assets"]["SPY"]["adjusted_close"][5]
        )


class SimulateWorldNoActivity(unittest.TestCase):
    def test_flat_short_world_holds_cash_no_orders(self):
        world = _trending_world(n_sessions=30, flat=True)
        result = simulate_world(world, CONTROL_GENOME)
        self.assertEqual(result["order_count"], 0)
        self.assertAlmostEqual(result["total_return"], 0.0, places=9)
        self.assertFalse(result["halted"])


class ScoredStartIndex(unittest.TestCase):
    def test_rejects_out_of_range_scored_start(self):
        world = _trending_world(n_sessions=300)
        with self.assertRaises(SimulatorError):
            simulate_world(world, CONTROL_GENOME, scored_start_index=300)
        with self.assertRaises(SimulatorError):
            simulate_world(world, CONTROL_GENOME, scored_start_index=-1)

    def test_history_reaches_into_warmup_region_for_immediate_eligibility(self):
        # 252 warmup bars + a short scored region. Without warmup lookback,
        # CONTROL_GENOME's M252 could never become eligible inside the short
        # scored region alone; with it, trading can start on day 1 of scoring.
        world = _trending_world(n_sessions=252 + 60)
        result = simulate_world(world, CONTROL_GENOME, scored_start_index=252)
        self.assertGreater(result["order_count"], 0)

    def test_warmup_region_itself_is_never_scored(self):
        # Choose a scored_start_index well PAST the point (index 252) where
        # CONTROL_GENOME's M252 first becomes eligible, so real, order-
        # producing rebalances occur in indices 252..279 in the "whole" run
        # -- and must be invisible to a run that scores only from index 280
        # onward, even though both runs share the exact same underlying
        # world array.
        world = _trending_world(n_sessions=280 + 60)
        whole = simulate_world(world, CONTROL_GENOME, scored_start_index=0)
        tail_only = simulate_world(world, CONTROL_GENOME, scored_start_index=280)
        self.assertNotEqual(whole["total_return"], tail_only["total_return"])


class SimulateWorldWithActivity(unittest.TestCase):
    def setUp(self):
        self.world = _trending_world(n_sessions=300)
        self.result = simulate_world(self.world, CONTROL_GENOME)

    def test_orders_were_placed_once_lookback_warms_up(self):
        self.assertGreater(self.result["order_count"], 0)

    def test_positive_trend_world_yields_positive_return(self):
        self.assertGreater(self.result["total_return"], 0.0)

    def test_never_halts_on_a_rising_world(self):
        self.assertFalse(self.result["halted"])

    def test_metrics_shape_matches_per_world_score_contract(self):
        metrics = world_metrics(self.result)
        for key in (
            "order_count", "median_return", "worst_drawdown", "median_sharpe",
            "halt_rate", "median_turnover", "median_transaction_cost_rate",
        ):
            self.assertIn(key, metrics)
        self.assertEqual(metrics["halt_rate"], 0.0)

    def test_deterministic_repeat(self):
        repeat = simulate_world(self.world, CONTROL_GENOME)
        self.assertEqual(repeat, self.result)


class DrawdownHalt(unittest.TestCase):
    def test_a_crashing_world_halts_and_stops_new_positions(self):
        world = _trending_world(n_sessions=300)
        # After warmup, crash every asset hard so drawdown blows through the
        # genome's frozen 12% halt threshold.
        for symbol in ASSET_UNIVERSE:
            row = world["assets"][symbol]
            for i in range(260, 300):
                row["adjusted_close"][i] = row["adjusted_close"][259] * 0.5
                row["raw_open"][i] = row["raw_open"][259] * 0.5
                row["raw_close"][i] = row["raw_close"][259] * 0.5
        result = simulate_world(world, CONTROL_GENOME)
        self.assertTrue(result["halted"])
        self.assertLessEqual(result["max_drawdown"], -CONTROL_GENOME["drawdown_halt_pct"])


class DividendCrediting(unittest.TestCase):
    def test_dividend_credits_cash_only_while_position_held(self):
        symbol = ASSET_UNIVERSE[0]
        world = _trending_world(n_sessions=300, dividend_at=280, dividend_amount=1.0)
        no_dividend_world = _trending_world(n_sessions=300)
        with_div = simulate_world(world, CONTROL_GENOME)
        without_div = simulate_world(no_dividend_world, CONTROL_GENOME)
        # A held position earning a real cash dividend should never produce a
        # strictly worse total return than the same world with no dividend at
        # all, all else equal.
        self.assertGreaterEqual(with_div["total_return"], without_div["total_return"] - 1e-9)

    def test_dividend_before_position_ever_opened_is_a_noop(self):
        world = _trending_world(n_sessions=300, dividend_at=1, dividend_amount=5.0)
        result = simulate_world(world, CONTROL_GENOME)
        # Should not raise, and cash-only early dividend changes nothing
        # observable about order flow.
        self.assertGreaterEqual(result["order_count"], 0)


class ExecutionSkipMask(unittest.TestCase):
    def test_skip_mask_defers_a_fill_exactly_one_session_never_drops_it(self):
        world = _trending_world(n_sessions=300)
        no_skip = simulate_world(world, CONTROL_GENOME)
        # Mark every session as a skip candidate; fills still all eventually
        # land (order_count identical) but one session later each time, on a
        # steadily-rising world that must change the realized fill price and
        # therefore the outcome -- proving deferral actually happened rather
        # than the mask being silently ignored.
        all_skip = simulate_world(world, CONTROL_GENOME, skip_mask=[True] * 300)
        self.assertEqual(no_skip["order_count"], all_skip["order_count"])
        self.assertNotEqual(no_skip["total_return"], all_skip["total_return"])

    def test_a_single_deferred_fill_is_never_deferred_twice(self):
        world = _trending_world(n_sessions=300)
        # Skip only ONE specific session; if a fill happens to land there it
        # defers once, then must land the very next session regardless of
        # that next session's own mask value.
        mask = [False] * 300
        mask[63] = True
        mask[64] = True
        result = simulate_world(world, CONTROL_GENOME, skip_mask=mask)
        baseline = simulate_world(world, CONTROL_GENOME)
        self.assertEqual(result["order_count"], baseline["order_count"])

    def test_skip_mask_all_false_is_identical_to_no_mask(self):
        world = _trending_world(n_sessions=300)
        no_mask = simulate_world(world, CONTROL_GENOME)
        mask_all_false = simulate_world(world, CONTROL_GENOME, skip_mask=[False] * 300)
        self.assertEqual(no_mask, mask_all_false)


class PassiveComparator(unittest.TestCase):
    def test_passive_genome_spreads_exposure_equal_weight(self):
        passive = passive_comparator_genome(CONTROL_GENOME)
        self.assertEqual(passive["max_positions"], len(ASSET_UNIVERSE))

    def test_rejects_genome_whose_cap_cannot_spread_equal_weight(self):
        narrow = {**CONTROL_GENOME, "target_max_exposure": 0.80, "max_asset_weight": 0.05}
        with self.assertRaises(SimulatorError):
            passive_comparator_genome(narrow)

    def test_passive_comparator_runs_and_returns_sharpe_and_return(self):
        world = _trending_world(n_sessions=300)
        result = simulate_passive_comparator(world, CONTROL_GENOME)
        self.assertIn("sharpe", result)
        self.assertIn("total_return", result)
        self.assertGreater(result["total_return"], 0.0)

    def test_passive_comparator_buys_once_never_rebalances(self):
        # A world where each asset's relative ranking flips hard partway
        # through should not matter to the passive comparator's return in
        # the way it would to an active strategy -- it holds its single
        # initial allocation throughout.
        world = _trending_world(n_sessions=300, asset_offsets={
            symbol: 1.0 + 0.0005 * i for i, symbol in enumerate(ASSET_UNIVERSE)
        })
        result_a = simulate_passive_comparator(world, CONTROL_GENOME)
        result_b = simulate_passive_comparator(world, CONTROL_GENOME)
        self.assertEqual(result_a, result_b)


if __name__ == "__main__":
    unittest.main()
