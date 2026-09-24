"""Single-episode evaluator for the CHAMPIONSHIP window (2023-01-01 ..
2025-12-31), built for GO -- TBOTS 2023-2025 CHAMPIONSHIP LANE HOLDOUT
(2026-09-21). Literal copy of
experiments/controls_interpretation_holdout_20260921T140000Z/holdout_harness.py
with only HOLDOUT_START/HOLDOUT_END changed -- same primitives, same call
order, same execution assumptions (execution.Portfolio, risk.validate,
execution.compute_orders, T+1 open fill, same-session-close mark, 12%
drawdown halt). Nothing here is a new evaluator.

IMPORTANT ASYMMETRY (see this GO's report): this window is the CHAMPIONSHIP
lane that directly selected the frozen champion at its S5C stage
(evolution/protocol/frozen_champion_....json cites
winner_rule_verified: "championship_rank_1" against this exact window). For
the champion this run is NOT an out-of-sample test -- it is a re-look at its
own selection data. For the D_primary candidates it is genuinely untouched
(S6A/B never ran a qualification or championship stage for lineages B-G).

s5_boundary.py's SEALED_INTERVALS["CHAMPIONSHIP"] = ("2023-01-01",
"2025-12-31") gates the S5A-D evaluators (s5a_build_development_bundle.py,
s5b_build_qualification_bundle.py, s5a_evolution.py import it; s6b_evaluator.py
and this file's ancestor do not). Per this GO's explicit authorization, the
same bypass already used for the 2026 FINAL_RESERVE holdout applies here:
ReplayEngine is constructed directly, outside the sealed-lane manifest
system, windowed to exactly the requested range.
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

HOLDOUT_START = "2023-01-01"
HOLDOUT_END = "2025-12-31"  # SEALED_INTERVALS["CHAMPIONSHIP"][1] per scripts/s5_boundary.py line 58


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
    """Literal parallel of the FINAL_RESERVE holdout harness's
    simulate_D_holdout. Calls s6a_runtime.decide_D directly -- the same
    frozen decision function used for every D-lineage genome in the
    DEVELOPMENT comparison and the 2026 holdout, unmodified."""
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
