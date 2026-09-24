"""Single-episode holdout evaluator for the FINAL_RESERVE window
(2026-01-01 .. 2026-08-25), built for GO — TBOTS CONTROL INTERPRETATION +
HOLDOUT VALIDATION (2026-09-21).

This is a parallel of experiments/controls_comparison_20260921T064013Z/
harness.py's _simulate_generic_episode / _simulate_bcd_with_curve, generalized
to take one explicit (start_date, end_date) pair instead of iterating
EPISODE_PROTOCOL's 12 frozen DEVELOPMENT episodes. Same primitives, same
call order, same execution assumptions (execution.Portfolio, risk.validate,
execution.compute_orders, T+1 open fill, same-session-close mark, 12%
drawdown halt) -- nothing here is a new evaluator, it is the same one
pointed at a different, explicitly-authorized date range.

No bundle abstraction is used: s6a_runtime.load_historical_bundle only
accepts "development" (<=2018-12-31) or "qualification" (<=2022-12-31) and
explicitly raises BoundaryError for anything else ("unknown/2026+ lane
rejected", "comparative requires separate future GO"). There is no
accepted bundle covering 2026. Per this GO's explicit authorization,
ReplayEngine is constructed directly with _verified_bundle omitted, which
makes it load_and_verify_dataset() the full on-disk, sha256-verified
normalized dataset itself and then windows to exactly the requested
start_date/end_date -- the same code path every other lane uses
internally, just not gated through s5_boundary.py's sealed-lane manifest
system (that system is specific to the S5A-D evaluators; s5_boundary.py
is imported only by s5a_build_development_bundle.py,
s5b_build_qualification_bundle.py, and s5a_evolution.py -- confirmed by
grep -- never by s6b_evaluator.py or this file's ancestor).
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import execution
import risk
import s6a_final as p
import s6a_runtime as r
import s6b_evaluator as s6b
from lib.replay import AgentView, ReplayEngine

STARTING_CASH_CENTS = p.EXECUTION["starting_cash_cents"]
UNIVERSE = p.UNIVERSE
DRAWDOWN_HALT = p.DRAWDOWN_HALT
ANNUALIZATION = s6b.ANNUALIZATION

HOLDOUT_START = "2026-01-01"
HOLDOUT_END = "2026-08-25"  # DATASET_END per scripts/s5_boundary.py line 25


def dollars_to_cents(value: str) -> int:
    return round(float(value) * 100)


def episode_sortino(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    downside = [min(0.0, x) for x in daily_returns]
    downside_dev = math.sqrt(sum(d * d for d in downside) / len(daily_returns))
    if downside_dev == 0:
        return 0.0
    return statistics.mean(daily_returns) / downside_dev * math.sqrt(ANNUALIZATION)


def _make_engine():
    return ReplayEngine(
        ROOT, p.DATASET, HOLDOUT_START, HOLDOUT_END,
        masked_time=True, random_seed=0, retention_end_date=HOLDOUT_END,
    )


def _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step, extra):
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
    out = {
        "start_date": HOLDOUT_START, "end_date": HOLDOUT_END,
        "starting_cash_cents": STARTING_CASH_CENTS,
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": s6b.episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "commission_cents": portfolio.total_commission_cents,
        "slippage_cents": total_slippage_cents,
        "transaction_cost_cents": transaction_cost_cents,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
        "ever_held_position": portfolio.fill_count > 0,
        "daily_returns": daily_returns,
    }
    out.update(extra)
    return out


def simulate_generic_holdout(decide_fn, *, max_asset_weight: float, max_total_exposure: float) -> dict:
    """decide_fn(view, step) -> {symbol: weight} | None. Used for the
    champion (control_agent-style trend decision) and all four controls."""
    engine = _make_engine()
    view = AgentView(engine)
    universe = UNIVERSE
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    order_count = 0
    total_slippage_cents = 0
    step = 0

    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        if pending is not None:
            for order in pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                total_slippage_cents += fill["slippage_cents"]
            pending = None

        mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in universe}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if drawdown <= -DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted:
            target_weights = decide_fn(view, step)

        if target_weights is not None:
            target_weights = {s: w for s, w in target_weights.items() if s in universe}
            risk.validate(
                target_weights, universe, max_asset_weight=max_asset_weight,
                max_total_exposure=max_total_exposure,
                drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown,
            )
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, universe, equity_cents, mark_prices, current_shares)
            order_count += len(pending)

        equity_curve.append(equity_cents)
        step += 1
        if not engine.advance():
            break

    return _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step, {})


def simulate_D_holdout(genome: dict) -> dict:
    """Literal parallel of controls_comparison harness.py's
    _simulate_bcd_with_curve, D-only branch, generalized to the holdout
    window. Calls s6a_runtime.decide_D directly -- the same frozen
    decision function used for every D-lineage genome in the DEVELOPMENT
    comparison, unmodified."""
    code = "D"
    engine = _make_engine()
    view = AgentView(engine)
    universe = UNIVERSE
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    order_count = 0
    total_slippage_cents = 0
    step = 0

    needed = r.warmup(code, genome)
    h = {s: [float(row["adjusted_close"]) for row in view.history(s, needed)] for s in universe} \
        if needed > 0 else {s: [] for s in universe}

    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        if step > 0:
            for s in universe:
                h[s].append(float(assets[s]["adjusted_close"]))

        if pending is not None:
            for order in pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                total_slippage_cents += fill["slippage_cents"]
            pending = None

        mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in universe}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if drawdown <= -p.DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted:
            target_weights = r.decide_D(genome, h, step)

        if target_weights is not None:
            target_weights = {s: w for s, w in target_weights.items() if s in universe}
            max_asset_weight, max_total_exposure = s6b.risk_bounds(code, genome)
            risk.validate(
                target_weights, universe, max_asset_weight=max_asset_weight,
                max_total_exposure=max_total_exposure,
                drawdown_halt_pct=p.DRAWDOWN_HALT, current_drawdown=drawdown,
            )
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, universe, equity_cents, mark_prices, current_shares)
            order_count += len(pending)

        equity_curve.append(equity_cents)
        step += 1
        if not engine.advance():
            break

    return _finalize(equity_curve, portfolio, order_count, total_slippage_cents, step, {})
