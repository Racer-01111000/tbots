"""Fitness V2 world simulator: adapts s5a_evaluator.simulate_episode's exact
mechanics to a pre-materialized Fitness V2 world (arrays indexed by session,
per fitness_v2_protocol.validate_world_shape) instead of a database-backed
ReplayEngine.

Reused verbatim, unchanged: control_agent.decide (-> indicators.py),
execution.Portfolio / compute_orders / dividend_amount_dollars, risk.validate.
These are genome/world-content-driven and were never tied to ReplayEngine's
date-based loading -- only to the AgentView.observe()/history() contract,
which WorldView below reproduces exactly (same field whitelist, same
bisect_right "include today" history() semantics, same "fewer rows than
requested, never padded/backfilled" behavior on short lookback).

Corporate-action format: a world's `corporate_action` array entry is the
exact same value the real dataset uses (a JSON string like
'{"dividend_amount": 1.23}', or ""/None) -- read directly by
execution.dividend_amount_dollars(), unchanged, for both historical and
synthetic worlds. fitness_v2_protocol.valid_gaps' truthy corporate_action
check already works unmodified against that representation. A synthetic
world built by reconstruct_distributional_path (which currently emits a
numeric "dividend_cents" field, not yet a corporate_action string) is
supported too: if a row carries "dividend_cents", that numeric per-share
dollar amount is used directly and corporate_action's own value is not
parsed -- this keeps both existing, already-tested representations valid
without a forced rewrite of already-frozen reconstruction code.

Execution family stress: the frozen skip mask (fitness_v2_protocol.
execution_skip_mask) marks candidate skip sessions; a marked, otherwise-
eligible fill is deferred exactly one session (frozen rule, no second skip);
this only ever changes WHEN a fill lands, never the world's own price path.

One world = one simulated episode end to end; there is no sub-episode
split, so per_world_score's "median_*" field names are populated directly
from this single run's own statistics (median of one observation is that
observation) -- this mirrors s5a_evaluator.compute_fitness's field naming
for interoperability with the already-frozen per_world_score formula,
without inventing a sub-episode structure Fitness V2 worlds don't have.
"""
from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence

import control_agent
import execution
import risk
from fitness_v2 import FitnessV2Error
from fitness_v2_protocol import ASSET_UNIVERSE

STARTING_CASH_CENTS = 100_000_000  # $1,000,000.00 -- matches S5A's own scale order
ANNUALIZATION = 252


class SimulatorError(FitnessV2Error):
    pass


def _validate_simulation_world_shape(world: Mapping[str, Mapping[str, Sequence]]) -> int:
    """Deliberately NOT fitness_v2_protocol.validate_world_shape: that
    function's >=253-session floor exists for descriptor computation (one
    complete 252-return window), a constraint specific to that consumer.
    Running a genome through a world end to end has no such floor -- every
    real Fitness V2 world (H1/H2/H3-shaped, 900+ sessions) clears it
    trivially, and reusing the descriptor-specific floor here would silently
    forbid legitimate small fixtures in this module's own tests."""
    assets = world.get("assets")
    if not isinstance(assets, Mapping) or set(assets) != set(ASSET_UNIVERSE):
        raise SimulatorError("world must cover exactly the eight-asset universe")
    lengths = set()
    for symbol in ASSET_UNIVERSE:
        row = assets[symbol]
        for field in ("adjusted_close", "raw_open", "raw_close", "corporate_action"):
            if field not in row:
                raise SimulatorError(f"asset {symbol} is missing required field {field}")
        lengths.add(len(row["adjusted_close"]))
    if len(lengths) != 1:
        raise SimulatorError("all eight assets must share one synchronized scored session index")
    n_sessions = lengths.pop()
    if n_sessions < 2:
        raise SimulatorError("a world requires at least two sessions to simulate a fill")
    return n_sessions


def _dollars_to_cents(value: float) -> int:
    return round(float(value) * 100)


def _row_dividend_dollars(assets: Mapping[str, Mapping[str, Sequence]], symbol: str, index: int):
    """Per-share cash dividend for this asset-session, or None. Prefers an
    explicit numeric per-share amount (synthetic-world reconstruction path);
    falls back to parsing corporate_action as the real dataset's JSON
    encoding (historical-world / execution.dividend_amount_dollars path)."""
    row_dividends = assets[symbol].get("dividend_cents")
    if row_dividends is not None:
        value = row_dividends[index]
        return float(value) if value else None
    corporate_action = assets[symbol]["corporate_action"][index]
    return execution.dividend_amount_dollars(corporate_action)


class WorldView:
    """The AgentView-equivalent contract (observe/history) over one
    pre-materialized Fitness V2 world, at a caller-advanced current index.
    No method here can see an index past `current_index` -- the same
    "sealed interface" guarantee AgentView gives against ReplayEngine."""

    def __init__(self, assets: Mapping[str, Mapping[str, Sequence]], current_index: int):
        self._assets = assets
        self._current_index = current_index

    def observe(self) -> dict:
        out = {}
        for symbol in ASSET_UNIVERSE:
            row = self._assets[symbol]
            out[symbol] = {
                "available": True,
                "adjusted_close": row["adjusted_close"][self._current_index],
            }
        return {"episode_day_index": self._current_index + 1, "assets": out}

    def history(self, symbol: str, bars: int) -> list[dict]:
        if not isinstance(bars, int) or isinstance(bars, bool):
            raise TypeError(f"bars must be an int, got {type(bars).__name__}")
        if bars <= 0:
            return []
        row = self._assets[symbol]
        cut = self._current_index + 1  # bisect_right semantics: today is included
        start = max(0, cut - bars)
        return [{"adjusted_close": row["adjusted_close"][i], "available": True}
                for i in range(start, cut)]


def _current_raw_prices(assets, field: str, index: int) -> dict:
    return {symbol: assets[symbol][field][index] for symbol in ASSET_UNIVERSE}


def simulate_world(
    world: Mapping[str, Mapping[str, Sequence]], genome: Mapping[str, object], *,
    skip_mask: Sequence[bool] | None = None,
) -> dict:
    """Runs one genome through one world end to end (index 0 through the
    world's last scored session), fresh capital, no state carried in or
    out. `skip_mask[i] is True` means: a fill that would otherwise land at
    the open of session i is deferred exactly one session instead (frozen
    Execution stress, §17) -- never applied to any other family."""
    n_sessions = _validate_simulation_world_shape(world)
    assets = world["assets"]
    universe = genome["universe"]
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending: list[dict] | None = None
    deferred: list[dict] | None = None
    equity_curve: list[int] = []
    order_count = 0
    total_slippage_cents = 0

    for index in range(n_sessions):
        raw_opens = _current_raw_prices(assets, "raw_open", index)
        raw_closes = _current_raw_prices(assets, "raw_close", index)

        for symbol in ASSET_UNIVERSE:
            dividend = _row_dividend_dollars(assets, symbol, index)
            if dividend:
                portfolio.credit_dividend(symbol, dividend)

        arriving = pending or []
        pending = None
        if skip_mask is not None and skip_mask[index] and arriving and deferred is None:
            deferred = arriving
            arriving = []
        elif deferred is not None:
            arriving = deferred + arriving
            deferred = None

        for order in arriving:
            open_cents = _dollars_to_cents(raw_opens[order["symbol"]])
            fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
            total_slippage_cents += fill["slippage_cents"]

        mark_prices = {s: _dollars_to_cents(raw_closes[s]) for s in universe}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        view = WorldView(assets, index)
        target_weights = None
        if drawdown <= -genome["drawdown_halt_pct"] - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted and index % genome["rebalance_every_n_sessions"] == 0:
            decision = control_agent.decide(view, genome)
            target_weights = decision["weights"]

        if target_weights is not None:
            risk.validate(
                target_weights, universe,
                max_asset_weight=genome["max_asset_weight"],
                max_total_exposure=genome["target_max_exposure"],
                drawdown_halt_pct=genome["drawdown_halt_pct"],
                current_drawdown=drawdown,
            )
            sizing_prices = {s: _dollars_to_cents(raw_closes[s]) for s in universe}
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            new_orders = execution.compute_orders(
                target_weights, universe, equity_cents, sizing_prices, current_shares
            )
            pending = new_orders
            order_count += len(new_orders)

        equity_curve.append(equity_cents)

    daily_returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0 if equity_curve[i - 1] else 0.0
        for i in range(1, len(equity_curve))
    ]
    final_equity_cents = equity_curve[-1]
    max_drawdown = min(
        equity / max(equity_curve[:index + 1]) - 1.0
        for index, equity in enumerate(equity_curve)
    )
    transaction_cost_cents = portfolio.total_commission_cents + total_slippage_cents
    sharpe = 0.0
    if len(daily_returns) >= 2:
        stdev = statistics.stdev(daily_returns)
        if stdev:
            sharpe = statistics.mean(daily_returns) / stdev * math.sqrt(ANNUALIZATION)
    return {
        "order_count": order_count,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "transaction_cost_cents": transaction_cost_cents,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
    }


def world_metrics(simulation: Mapping[str, object]) -> dict:
    """Adapts one simulate_world() result into the single-world `metrics`
    shape fitness_v2.per_world_score requires. One world is one episode, so
    each "median_*" field is simply that one run's own statistic."""
    return {
        "order_count": simulation["order_count"],
        "median_return": simulation["total_return"],
        "worst_drawdown": simulation["max_drawdown"],
        "median_sharpe": simulation["sharpe"],
        "halt_rate": 1.0 if simulation["halted"] else 0.0,
        "median_turnover": simulation["turnover"],
        "median_transaction_cost_rate": simulation["transaction_cost_rate"],
    }


def passive_comparator_genome(genome: Mapping[str, object]) -> dict:
    """Handoff §37 / parameter-freeze item 4: fixed eight-asset universe,
    equal weight, buy-once (no rebalancing thereafter), at the genome's own
    declared gross-exposure cap -- never post-hoc matched to realized
    exposure. Implemented as a genome whose rebalance interval exceeds the
    world length, so control_agent decides exactly once, at index 0."""
    equal_weight = genome["target_max_exposure"] / len(ASSET_UNIVERSE)
    if equal_weight > genome["max_asset_weight"] + 1e-9:
        raise SimulatorError(
            "genome's exposure cap cannot be spread equal-weight across the "
            "universe without exceeding its own per-asset cap"
        )
    return {
        **genome,
        "strategy_family": "passive_exposure_matched",
        "max_positions": len(ASSET_UNIVERSE),
        "rebalance_every_n_sessions": 10 ** 9,
    }


def simulate_passive_comparator(world: Mapping[str, Mapping[str, Sequence]], genome: Mapping[str, object]) -> dict:
    """Runs the exposure-matched passive comparator through the SAME world.
    Uses a fixed, deterministic equal-weight decision at index 0 rather than
    routing through control_agent/indicators (which select by momentum/
    trend, not "hold everything equal-weight") -- the passive comparator is
    not evaluated by the strategy's own agent logic, only by the same
    execution/accounting mechanics."""
    n_sessions = _validate_simulation_world_shape(world)
    assets = world["assets"]
    universe = ASSET_UNIVERSE
    target_weight = genome["target_max_exposure"] / len(universe)
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending: list[dict] | None = None
    equity_curve: list[int] = []

    for index in range(n_sessions):
        raw_opens = _current_raw_prices(assets, "raw_open", index)
        raw_closes = _current_raw_prices(assets, "raw_close", index)
        for symbol in universe:
            dividend = _row_dividend_dollars(assets, symbol, index)
            if dividend:
                portfolio.credit_dividend(symbol, dividend)
        if pending:
            for order in pending:
                open_cents = _dollars_to_cents(raw_opens[order["symbol"]])
                portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
            pending = None
        mark_prices = {s: _dollars_to_cents(raw_closes[s]) for s in universe}
        equity_cents = portfolio.equity_cents(mark_prices)
        portfolio.update_peak_and_drawdown(equity_cents)
        if index == 0:
            sizing_prices = {s: _dollars_to_cents(raw_closes[s]) for s in universe}
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(
                {s: target_weight for s in universe}, universe, equity_cents,
                sizing_prices, current_shares,
            )
        equity_curve.append(equity_cents)

    daily_returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0 if equity_curve[i - 1] else 0.0
        for i in range(1, len(equity_curve))
    ]
    sharpe = 0.0
    if len(daily_returns) >= 2:
        stdev = statistics.stdev(daily_returns)
        if stdev:
            sharpe = statistics.mean(daily_returns) / stdev * math.sqrt(ANNUALIZATION)
    return {"sharpe": sharpe, "total_return": equity_curve[-1] / STARTING_CASH_CENTS - 1.0}
