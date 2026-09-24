"""Controls-comparison evaluation harness.

Read-only against NODE; writes stay confined to this experiments directory.
Reuses the frozen S6A/S6B primitives verbatim for B/C/D genomes
(s6b_evaluator.evaluate_genome / simulate_episode -- untouched, no DB) and
for the S5D champion (control_agent.decide, genome-parameterized, same
"trend" schema as genome_control.CONTROL_GENOME).

For the champion and the three synthetic controls, s6b_evaluator has no
entrypoint (its DECIDE_ARITY only covers lineages B-G) and the DB-backed
run_control_episode.py is off-limits here (this GO writes only inside
experiments/). _simulate_generic_episode below is a parallel, DB-free
per-step loop built from the *same* primitives in the *same* order as
s6b_evaluator._simulate_admitted_episode (execution.Portfolio, risk.validate,
execution.compute_orders, the same T+1-fill / same-session-close-mark /
12%-drawdown-halt sequence) so every agent in this comparison sees identical
execution assumptions. It also captures the daily-return series (which
s6b_evaluator discards after computing sharpe) so Sortino can be computed --
a metric Rick asked for that the frozen evaluator does not itself expose.
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
from s5a_config import EPISODE_PROTOCOL

STARTING_CASH_CENTS = p.EXECUTION["starting_cash_cents"]
LANE_END = p.HISTORY["development"]["end"]
UNIVERSE = p.UNIVERSE
DRAWDOWN_HALT = p.DRAWDOWN_HALT
ANNUALIZATION = s6b.ANNUALIZATION


def dollars_to_cents(value: str) -> int:
    return round(float(value) * 100)


def episode_sortino(daily_returns: list[float]) -> float:
    """Population semi-deviation Sortino, rf=0, annualized by sqrt(252) --
    mirrors s6b_evaluator.episode_sharpe's conventions (0.0 sentinel when
    the denominator is degenerate, same annualization constant)."""
    if len(daily_returns) < 2:
        return 0.0
    downside = [min(0.0, x) for x in daily_returns]
    downside_dev = math.sqrt(sum(d * d for d in downside) / len(daily_returns))
    if downside_dev == 0:
        return 0.0
    return statistics.mean(daily_returns) / downside_dev * math.sqrt(ANNUALIZATION)


def _simulate_generic_episode(bundle, decide_fn, episode_index: int, *,
                              max_asset_weight: float, max_total_exposure: float) -> dict:
    """decide_fn(view, step) -> {symbol: weight} or None (no rebalance this
    step). Byte-for-byte same sequencing as
    s6b_evaluator._simulate_admitted_episode's trading loop; the only
    difference is the decision dispatch (one pluggable fn instead of a
    B/C/D/E/F/G code switch) and that this also returns the daily-return
    series."""
    episodes = EPISODE_PROTOCOL["episodes"]
    episode = episodes[episode_index]
    engine = ReplayEngine(
        p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
        masked_time=True, random_seed=0, retention_end_date=LANE_END,
        _verified_bundle=bundle,
    )
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
    return {
        "episode_index": episode["episode_index"],
        "start_date": episode["start_date"],
        "end_date": episode["end_date"],
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


def evaluate_generic_agent(bundle, decide_fn_factory, *, max_asset_weight: float, max_total_exposure: float) -> dict:
    """decide_fn_factory(episode_index) -> decide_fn(view, step). A factory
    (not a single fn) so a control that needs fresh per-episode state (the
    seeded random control) gets a clean, independently-seeded RNG each
    episode while stateless agents (champion, no-trade, buy-and-hold) can
    just return the same fn every time."""
    episode_count = len(EPISODE_PROTOCOL["episodes"])
    episodes = [
        _simulate_generic_episode(
            bundle, decide_fn_factory(index), index,
            max_asset_weight=max_asset_weight, max_total_exposure=max_total_exposure,
        )
        for index in range(episode_count)
    ]
    return {"episode_metrics": episodes}


def evaluate_bcd_agent_with_curve(bundle, code: str, genome: dict, admission) -> dict:
    """B/C/D genomes: get the OFFICIAL numbers from the untouched frozen
    evaluator (s6b_evaluator.evaluate_genome), then separately compute
    Sortino from a parallel same-primitives simulation (s6b_evaluator does
    not expose the daily-return series). The parallel run's total_return/
    sharpe/max_drawdown/turnover/transaction_cost_rate/halted are asserted
    byte-identical to the official run for every episode before anything is
    trusted -- if they ever disagree this raises, rather than silently
    reporting a number that might not match the frozen path."""
    official = s6b.evaluate_genome(bundle, code, genome, admission=admission)
    parallel_episodes = _simulate_bcd_with_curve(bundle, code, genome)

    for off, par in zip(official["episode_metrics"], parallel_episodes):
        mismatches = []
        for key in ("total_return", "sharpe", "max_drawdown", "turnover",
                    "transaction_cost_rate", "halted", "order_count", "fill_count"):
            a, b = off[key], par[key]
            if isinstance(a, float):
                if abs(a - b) > 1e-9:
                    mismatches.append((key, a, b))
            elif a != b:
                mismatches.append((key, a, b))
        if mismatches:
            raise AssertionError(
                f"parallel-curve simulation diverged from official evaluator for "
                f"{code} episode {off['episode_index']}: {mismatches}"
            )

    merged_episodes = []
    for off, par in zip(official["episode_metrics"], parallel_episodes):
        merged = dict(off)
        merged["sortino"] = par["sortino"]
        merged["ever_held_position"] = par["ever_held_position"]
        merged["daily_returns"] = par["daily_returns"]
        merged_episodes.append(merged)

    return {"episode_metrics": merged_episodes, "aggregate": official["aggregate"],
            "metrics_hash": official["metrics_hash"]}


def _simulate_bcd_with_curve(bundle, code: str, genome: dict) -> list[dict]:
    """Exact re-derivation of s6b_evaluator._simulate_admitted_episode's
    B/C/D branch (same h-dict bookkeeping, same r.decide_B/C/D calls, same
    execution/risk sequencing) -- the only addition is capturing daily
    returns for Sortino. Kept as a literal parallel of the original rather
    than a generalized decide_fn to avoid any risk of behavioral drift in
    the B-specific holding_days bookkeeping."""
    episodes_out = []
    for episode_index in range(len(EPISODE_PROTOCOL["episodes"])):
        episode = EPISODE_PROTOCOL["episodes"][episode_index]
        engine = ReplayEngine(
            p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
            masked_time=True, random_seed=0, retention_end_date=LANE_END,
            _verified_bundle=bundle,
        )
        view = AgentView(engine)
        universe = p.UNIVERSE
        portfolio = execution.Portfolio(STARTING_CASH_CENTS)
        pending = None
        equity_curve = []
        order_count = 0
        total_slippage_cents = 0
        holding_days: dict[str, int] = {}
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

            if code == "B":
                prev_held = set(holding_days)
                current_held = set(portfolio.positions)
                for s in current_held - prev_held:
                    holding_days[s] = 0
                for s in current_held & prev_held:
                    holding_days[s] += 1
                for s in prev_held - current_held:
                    del holding_days[s]

            mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in universe}
            equity_cents = portfolio.equity_cents(mark_prices)
            drawdown = portfolio.update_peak_and_drawdown(equity_cents)

            target_weights = None
            if drawdown <= -p.DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
                portfolio.halted = True
                target_weights = {}
            elif not portfolio.halted:
                if code == "B":
                    target_weights = r.decide_B(genome, h, set(portfolio.positions), holding_days)
                elif code == "C":
                    target_weights = r.decide_C(genome, h, set(portfolio.positions))
                elif code == "D":
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
        episodes_out.append({
            "episode_index": episode["episode_index"],
            "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
            "sharpe": s6b.episode_sharpe(daily_returns),
            "sortino": episode_sortino(daily_returns),
            "max_drawdown": max_drawdown,
            "halted": portfolio.halted,
            "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
            "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
            "order_count": order_count,
            "fill_count": portfolio.fill_count,
            "ever_held_position": portfolio.fill_count > 0,
            "daily_returns": daily_returns,
        })
    return episodes_out
