"""Frozen S5C evaluator. Importing this module never starts championship."""
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
from s5a_evaluator import (
    IndependentVerifierFailure,
    _performance_concentration,
    dollars_to_cents,
    episode_sharpe,
)
from s5c_config import (
    CHAMPIONSHIP_END,
    DATASET_REVISION,
    EPISODE_PROTOCOL,
    FINALIST_IDS,
    ROOT,
    SCORE_PROTOCOL,
    STARTING_CASH_CENTS,
    assert_frozen_execution_semantics,
)
from s5c_finalists import ChampionshipFinalist


class ContaminatedChampionshipInput(ValueError):
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
        raise RuntimeError("championship episode did not begin from frozen fresh state")
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
        SCORE_PROTOCOL["daily_return_annualization"]
    )


def simulate_episode(
    bundle, finalist: ChampionshipFinalist, episode_index: int, *, verify: bool = False
) -> dict:
    genome = finalist.genome
    assert_frozen_execution_semantics(genome)
    if finalist.genome_id not in FINALIST_IDS:
        raise ValueError("genome is not an accepted frozen championship finalist")
    if not isinstance(episode_index, int) or isinstance(episode_index, bool):
        raise TypeError("episode_index must identify the frozen championship manifest")
    episodes = EPISODE_PROTOCOL["episodes"]
    if not 0 <= episode_index < len(episodes):
        raise ValueError("episode_index is outside the frozen championship manifest")
    episode = episodes[episode_index]
    engine = ReplayEngine(
        ROOT,
        DATASET_REVISION,
        episode["start_date"],
        episode["end_date"],
        masked_time=True,
        random_seed=0,
        retention_end_date=CHAMPIONSHIP_END,
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
                        episode["episode_index"],
                        state.step,
                        full["_true_timestamp"],
                        disagreements,
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
        state.equity_curve[i] / state.equity_curve[i - 1] - 1.0
        if state.equity_curve[i - 1]
        else 0.0
        for i in range(1, len(state.equity_curve))
    ]
    final_equity = state.equity_curve[-1]
    max_drawdown = min(
        equity / max(state.equity_curve[: index + 1]) - 1.0
        for index, equity in enumerate(state.equity_curve)
    )
    transaction_cost = (
        state.portfolio.total_commission_cents + state.total_slippage_cents
    )
    initial_hash = hashlib.sha256(canonical_json(initial).encode()).hexdigest()
    return {
        "episode_index": episode["episode_index"],
        "start_date": episode["start_date"],
        "end_date": episode["end_date"],
        "initial_state": initial,
        "initial_state_hash": initial_hash,
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


def compute_championship_score(
    episode_metrics: list[dict],
    *,
    historical_s5a_performance=None,
    historical_s5b_performance=None,
    evolutionary_feedback=None,
    final_reserve_information=None,
) -> dict:
    contaminated = {
        "historical S5A performance": historical_s5a_performance,
        "historical S5B performance": historical_s5b_performance,
        "evolutionary feedback": evolutionary_feedback,
        "FINAL RESERVE information": final_reserve_information,
    }
    for name, value in contaminated.items():
        if value is not None:
            raise ContaminatedChampionshipInput(
                f"{name} cannot enter championship scoring"
            )
    expected = EPISODE_PROTOCOL["episodes"]
    if (
        len(episode_metrics) != len(expected)
        or [row.get("episode_index") for row in episode_metrics]
        != list(range(len(expected)))
    ):
        raise ValueError(
            "championship score requires all three frozen episodes in order"
        )
    returns = [row["total_return"] for row in episode_metrics]
    sharpes = [row["sharpe"] for row in episode_metrics]
    turnover = [row["turnover"] for row in episode_metrics]
    costs = [row["transaction_cost_rate"] for row in episode_metrics]
    median_return = statistics.median(returns)
    median_sharpe = statistics.median(sharpes)
    dispersion = statistics.median(
        abs(value - median_return) for value in returns
    )
    positive_fraction = sum(value > 0 for value in returns) / len(returns)
    consistency = 0.5 * positive_fraction + 0.5 * (
        1 - min(1.0, dispersion / 0.10)
    )
    worst_drawdown = min(row["max_drawdown"] for row in episode_metrics)
    halt_count = sum(bool(row["halted"]) for row in episode_metrics)
    halt_rate = halt_count / len(episode_metrics)
    median_turnover = statistics.median(turnover)
    median_cost_rate = statistics.median(costs)
    concentration = _performance_concentration(returns)
    weights = SCORE_PROTOCOL["weights"]
    score = (
        weights["median_episode_return"] * median_return
        + weights["median_episode_sharpe"] * median_sharpe
        + weights["absolute_worst_drawdown"] * abs(worst_drawdown)
        + weights["consistency_score"] * consistency
        + weights["halt_rate"] * halt_rate
        + weights["median_episode_turnover"] * median_turnover
        + weights["median_transaction_cost_rate"] * median_cost_rate
        + weights["performance_concentration"] * concentration
    )
    places = SCORE_PROTOCOL["score_round_decimal_places"]
    return {
        "championship_score": round(score, places),
        "median_episode_return": round(median_return, places),
        "median_episode_sharpe": round(median_sharpe, places),
        "worst_drawdown": round(worst_drawdown, places),
        "return_dispersion": round(dispersion, places),
        "positive_episode_fraction": round(positive_fraction, places),
        "consistency_score": round(consistency, places),
        "drawdown_halt_count": halt_count,
        "halt_rate": round(halt_rate, places),
        "median_episode_turnover": round(median_turnover, places),
        "median_transaction_cost_rate": round(median_cost_rate, places),
        "performance_concentration": round(concentration, places),
        "total_commission_cents": sum(
            row["commission_cents"] for row in episode_metrics
        ),
        "total_slippage_cents": sum(
            row["slippage_cents"] for row in episode_metrics
        ),
    }


def evaluate_finalist(
    bundle, finalist: ChampionshipFinalist, *, verify: bool = False
) -> dict:
    episodes = [
        simulate_episode(bundle, finalist, index, verify=verify)
        for index in range(len(EPISODE_PROTOCOL["episodes"]))
    ]
    aggregate = compute_championship_score(episodes)
    payload = {"episodes": episodes, "aggregate": aggregate}
    return {
        "selection_order": finalist.selection_order,
        "agent_id": finalist.agent_id,
        "genome_id": finalist.genome_id,
        "episode_metrics": episodes,
        "aggregate": aggregate,
        "metrics_hash": hashlib.sha256(
            canonical_json(payload).encode()
        ).hexdigest(),
    }


def rank_completed_results(results: list[dict]) -> list[dict]:
    if (
        len(results) != 3
        or len({row.get("genome_id") for row in results}) != 3
        or set(row.get("genome_id") for row in results) != set(FINALIST_IDS)
    ):
        raise ValueError("ranking requires all three distinct frozen finalists")
    for row in results:
        if (
            len(row.get("episode_metrics", [])) != 3
            or [episode.get("episode_index") for episode in row["episode_metrics"]]
            != [0, 1, 2]
            or "championship_score" not in row.get("aggregate", {})
        ):
            raise ValueError(
                "ranking cannot begin before every championship examination is complete"
            )
    ordered = sorted(
        results,
        key=lambda row: (
            -row["aggregate"]["championship_score"],
            row["genome_id"],
        ),
    )
    return [
        dict(row, championship_rank=index)
        for index, row in enumerate(ordered, 1)
    ]


def select_winner(
    results: list[dict],
    *,
    discretionary_override=None,
    final_reserve_information=None,
    post_hoc_weights=None,
    dropped_episode=None,
) -> tuple[dict, list[dict]]:
    forbidden = (
        discretionary_override,
        final_reserve_information,
        post_hoc_weights,
        dropped_episode,
    )
    if any(value is not None for value in forbidden):
        raise ContaminatedChampionshipInput(
            "winner selection accepts no override, reserve information, "
            "post-hoc weights, or dropped episode"
        )
    ranked = rank_completed_results(results)
    return ranked[0], ranked


def championship_as_evolutionary_fitness(_result):
    raise ContaminatedChampionshipInput(
        "championship results cannot become evolutionary fitness"
    )
