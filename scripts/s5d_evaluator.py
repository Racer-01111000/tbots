"""Frozen S5D evaluator. Importing never executes FINAL RESERVE."""
from dataclasses import dataclass, field
import hashlib
import math
import statistics

import control_agent
import execution
import risk
import verifier
from lib.ids import canonical_json
from lib.replay import AgentView, ReplayEngine
from s5a_evaluator import IndependentVerifierFailure, dollars_to_cents, episode_sharpe
from s5d_champion import FrozenChampion
from s5d_config import (
    ACCEPTED_CHAMPION_ID,
    DATASET_REVISION,
    EPISODE_PROTOCOL,
    FINAL_RESERVE_END,
    OUTCOME_PROTOCOL,
    ROOT,
    STARTING_CASH_CENTS,
    assert_frozen_execution_semantics,
)


class ContaminatedFinalReserveInput(ValueError):
    pass


@dataclass
class EpisodeState:
    portfolio: execution.Portfolio = field(
        default_factory=lambda: execution.Portfolio(STARTING_CASH_CENTS)
    )
    pending: list[dict] | None = None
    equity_curve: list[int] = field(default_factory=list)
    verifier_checks: int = 0
    order_count: int = 0
    total_slippage_cents: int = 0
    step: int = 0


def fresh_episode_state() -> EpisodeState:
    state = EpisodeState()
    if (
        state.portfolio.cash_cents != STARTING_CASH_CENTS
        or state.portfolio.positions
        or state.portfolio.peak_equity_cents != STARTING_CASH_CENTS
        or state.portfolio.halted
        or state.pending is not None
    ):
        raise RuntimeError("final reserve episode did not begin from frozen fresh state")
    return state


def initial_state_snapshot(state: EpisodeState) -> dict:
    return {
        "cash_cents": state.portfolio.cash_cents,
        "positions": dict(state.portfolio.positions),
        "peak_equity_cents": state.portfolio.peak_equity_cents,
        "drawdown": 0.0,
        "halted": state.portfolio.halted,
        "pending_orders": 0 if state.pending is None else len(state.pending),
    }


def episode_sortino(daily_returns: list[float]) -> float:
    downside = [value for value in daily_returns if value < 0]
    if len(downside) < 2:
        return 0.0
    stdev = statistics.stdev(downside)
    if stdev == 0:
        return 0.0
    return statistics.mean(daily_returns) / stdev * math.sqrt(
        OUTCOME_PROTOCOL["daily_return_annualization"]
    )


def simulate_final_reserve(
    bundle, champion: FrozenChampion, *, verify: bool = False
) -> dict:
    if champion.genome_id != ACCEPTED_CHAMPION_ID:
        raise ValueError("genome is not the accepted frozen champion")
    genome = champion.genome
    assert_frozen_execution_semantics(genome)
    episode = EPISODE_PROTOCOL["episodes"][0]
    engine = ReplayEngine(
        ROOT,
        DATASET_REVISION,
        episode["start_date"],
        episode["end_date"],
        masked_time=True,
        random_seed=0,
        retention_end_date=FINAL_RESERVE_END,
        _verified_bundle=bundle,
    )
    view = AgentView(engine)
    universe = genome["universe"]
    state = fresh_episode_state()
    initial = initial_state_snapshot(state)

    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        if state.pending is not None:
            for order in state.pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                fill = state.portfolio.apply_fill(
                    order["symbol"], order["side"], order["shares"], open_cents
                )
                state.total_slippage_cents += fill["slippage_cents"]
            state.pending = None

        mark_prices = {
            symbol: dollars_to_cents(assets[symbol]["close"])
            for symbol in universe if assets[symbol]["available"]
        }
        equity_cents = state.portfolio.equity_cents(mark_prices)
        drawdown = state.portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if (
            drawdown <= -genome["drawdown_halt_pct"] - 1e-9
            and not state.portfolio.halted
        ):
            state.portfolio.halted = True
            target_weights = {}
        elif (
            not state.portfolio.halted
            and state.step % genome["rebalance_every_n_sessions"] == 0
        ):
            decision = control_agent.decide(view, genome)
            if verify:
                evaluations = verifier.evaluate_universe_v(view, genome)
                selected = verifier.rank_and_select_v(
                    evaluations, genome["max_positions"]
                )
                weights = verifier.size_positions_v(selected, evaluations, genome)
                disagreements = verifier.compare_decision(
                    decision, evaluations, selected, weights
                )
                state.verifier_checks += 1
                if disagreements:
                    raise IndependentVerifierFailure(
                        0, state.step, full["_true_timestamp"], disagreements
                    )
            target_weights = decision["weights"]

        if target_weights is not None:
            risk.validate(
                target_weights,
                universe,
                max_asset_weight=genome["max_asset_weight"],
                max_total_exposure=genome["target_max_exposure"],
                drawdown_halt_pct=genome["drawdown_halt_pct"],
                current_drawdown=drawdown,
            )
            sizing_prices = {
                symbol: dollars_to_cents(assets[symbol]["close"])
                for symbol in universe if assets[symbol]["available"]
            }
            current_shares = {
                symbol: state.portfolio.shares_of(symbol)
                for symbol in universe if state.portfolio.shares_of(symbol) > 0
            }
            state.pending = execution.compute_orders(
                target_weights,
                universe,
                equity_cents,
                sizing_prices,
                current_shares,
            )
            state.order_count += len(state.pending)

        state.equity_curve.append(equity_cents)
        state.step += 1
        if not engine.advance():
            break

    daily_returns = [
        state.equity_curve[index] / state.equity_curve[index - 1] - 1.0
        if state.equity_curve[index - 1]
        else 0.0
        for index in range(1, len(state.equity_curve))
    ]
    final_equity = state.equity_curve[-1]
    max_drawdown = min(
        equity / max(state.equity_curve[: index + 1]) - 1.0
        for index, equity in enumerate(state.equity_curve)
    )
    transaction_cost = (
        state.portfolio.total_commission_cents + state.total_slippage_cents
    )
    return {
        "episode_index": 0,
        "start_date": episode["start_date"],
        "end_date": episode["end_date"],
        "initial_state": initial,
        "initial_state_hash": hashlib.sha256(
            canonical_json(initial).encode()
        ).hexdigest(),
        "starting_cash_cents": STARTING_CASH_CENTS,
        "final_equity_cents": final_equity,
        "total_return": final_equity / STARTING_CASH_CENTS - 1.0,
        "sharpe": episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": state.portfolio.halted,
        "turnover": (
            state.portfolio.total_traded_notional_cents / STARTING_CASH_CENTS
        ),
        "commission_cents": state.portfolio.total_commission_cents,
        "slippage_cents": state.total_slippage_cents,
        "transaction_cost_cents": transaction_cost,
        "transaction_cost_rate": transaction_cost / STARTING_CASH_CENTS,
        "order_count": state.order_count,
        "fill_count": state.portfolio.fill_count,
        "step_count": state.step,
        "verifier_checks": state.verifier_checks,
    }


def summarize_final_reserve(
    episode_metric: dict,
    *,
    historical_s5a_performance=None,
    historical_s5b_performance=None,
    historical_s5c_performance=None,
    evolutionary_feedback=None,
    competing_genomes=None,
    acceptance_threshold=None,
) -> dict:
    forbidden = {
        "historical S5A performance": historical_s5a_performance,
        "historical S5B performance": historical_s5b_performance,
        "historical S5C performance": historical_s5c_performance,
        "evolutionary feedback": evolutionary_feedback,
        "competing genomes": competing_genomes,
        "acceptance threshold": acceptance_threshold,
    }
    for name, value in forbidden.items():
        if value is not None:
            raise ContaminatedFinalReserveInput(
                f"{name} cannot enter final reserve reporting"
            )
    if (
        episode_metric.get("episode_index") != 0
        or episode_metric.get("start_date") != EPISODE_PROTOCOL["episodes"][0]["start_date"]
        or episode_metric.get("end_date") != EPISODE_PROTOCOL["episodes"][0]["end_date"]
    ):
        raise ValueError("final reserve reporting requires the one frozen episode")
    fields = (
        "total_return",
        "sharpe",
        "sortino",
        "max_drawdown",
        "halted",
        "turnover",
        "commission_cents",
        "slippage_cents",
        "transaction_cost_cents",
        "transaction_cost_rate",
        "final_equity_cents",
    )
    outcome = {name: episode_metric[name] for name in fields}
    outcome["performance_concentration"] = 0.0
    outcome["acceptance_threshold"] = None
    outcome["selection_or_replacement"] = None
    return outcome


def evaluate_champion(
    bundle, champion: FrozenChampion, *, verify: bool = False
) -> dict:
    episode = simulate_final_reserve(bundle, champion, verify=verify)
    outcome = summarize_final_reserve(episode)
    payload = {"episode": episode, "outcome": outcome}
    return {
        "agent_id": champion.agent_id,
        "genome_id": champion.genome_id,
        "episode_metric": episode,
        "outcome": outcome,
        "metrics_hash": hashlib.sha256(
            canonical_json(payload).encode()
        ).hexdigest(),
    }


def final_reserve_as_evolutionary_fitness(_result):
    raise ContaminatedFinalReserveInput(
        "final reserve results cannot become evolutionary fitness"
    )
