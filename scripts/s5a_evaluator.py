"""Deterministic DEVELOPMENT-only evaluation for S5A genomes."""
import hashlib
import math
import statistics

import control_agent
import execution
import risk
import verifier
from lib.ids import canonical_json
from lib.replay import AgentView, ReplayEngine
from s5a_config import (
    DATASET_REVISION, EPISODE_PROTOCOL, FITNESS_PROTOCOL, ROOT, validate_genome,
)

STARTING_CASH_CENTS = EPISODE_PROTOCOL["starting_cash_cents"]
LANE_END = EPISODE_PROTOCOL["lane_end"]


class IndependentVerifierFailure(RuntimeError):
    def __init__(self, episode_index: int, step: int, true_ts: str, disagreements: list[str]):
        self.outcome = {
            "episode_index": episode_index,
            "step": step,
            "true_ts": true_ts,
            "status": "disagreed",
            "disagreements": disagreements,
        }
        super().__init__(
            f"independent verifier disagreement in episode {episode_index} step {step}"
        )


def dollars_to_cents(value: str) -> int:
    return round(float(value) * 100)


def episode_sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    stdev = statistics.stdev(daily_returns)
    if stdev == 0:
        return 0.0
    return statistics.mean(daily_returns) / stdev * math.sqrt(
        FITNESS_PROTOCOL["daily_return_annualization"]
    )


def simulate_episode(bundle, genome: dict, episode_index: int, *, verify: bool = False) -> dict:
    """Run one fresh-capital episode; no state is accepted from another episode."""
    validate_genome(genome)
    if not isinstance(episode_index, int) or isinstance(episode_index, bool):
        raise TypeError("episode_index must identify the frozen DEVELOPMENT manifest")
    episodes = EPISODE_PROTOCOL["episodes"]
    if not 0 <= episode_index < len(episodes):
        raise ValueError("episode_index is outside the frozen DEVELOPMENT manifest")
    episode = episodes[episode_index]
    engine = ReplayEngine(
        ROOT,
        DATASET_REVISION,
        episode["start_date"],
        episode["end_date"],
        masked_time=True,
        random_seed=0,
        retention_end_date=LANE_END,
        _verified_bundle=bundle,
    )
    view = AgentView(engine)
    universe = genome["universe"]
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    verifier_checks = 0
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
                fill = portfolio.apply_fill(
                    order["symbol"], order["side"], order["shares"], open_cents
                )
                total_slippage_cents += fill["slippage_cents"]
            pending = None

        mark_prices = {
            symbol: dollars_to_cents(assets[symbol]["close"])
            for symbol in universe if assets[symbol]["available"]
        }
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if drawdown <= -genome["drawdown_halt_pct"] - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted and step % genome["rebalance_every_n_sessions"] == 0:
            decision = control_agent.decide(view, genome)
            if verify:
                verifier_evaluations = verifier.evaluate_universe_v(view, genome)
                verifier_selected = verifier.rank_and_select_v(
                    verifier_evaluations, genome["max_positions"]
                )
                verifier_weights = verifier.size_positions_v(
                    verifier_selected, verifier_evaluations, genome
                )
                disagreements = verifier.compare_decision(
                    decision, verifier_evaluations, verifier_selected, verifier_weights
                )
                verifier_checks += 1
                if disagreements:
                    raise IndependentVerifierFailure(
                        episode["episode_index"], step, full["_true_timestamp"], disagreements
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
                symbol: portfolio.shares_of(symbol)
                for symbol in universe if portfolio.shares_of(symbol) > 0
            }
            pending = execution.compute_orders(
                target_weights, universe, equity_cents, sizing_prices, current_shares
            )
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
        "sharpe": episode_sharpe(daily_returns),
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
        "verifier_checks": verifier_checks,
    }


def _performance_concentration(returns: list[float]) -> float:
    absolute = [abs(value) for value in returns]
    total = sum(absolute)
    if total == 0 or len(absolute) < 2:
        return 0.0
    hhi = sum((value / total) ** 2 for value in absolute)
    floor = 1.0 / len(absolute)
    return max(0.0, min(1.0, (hhi - floor) / (1.0 - floor)))


def compute_fitness(episode_metrics: list[dict]) -> dict:
    if len(episode_metrics) != len(EPISODE_PROTOCOL["episodes"]):
        raise ValueError("fitness requires the complete frozen episode manifest")
    returns = [row["total_return"] for row in episode_metrics]
    sharpes = [row["sharpe"] for row in episode_metrics]
    turnover = [row["turnover"] for row in episode_metrics]
    costs = [row["transaction_cost_rate"] for row in episode_metrics]
    median_return = statistics.median(returns)
    median_sharpe = statistics.median(sharpes)
    dispersion = statistics.median(abs(value - median_return) for value in returns)
    positive_fraction = sum(value > 0 for value in returns) / len(returns)
    consistency = 0.5 * positive_fraction + 0.5 * (1 - min(1.0, dispersion / 0.10))
    worst_drawdown = min(row["max_drawdown"] for row in episode_metrics)
    halt_count = sum(bool(row["halted"]) for row in episode_metrics)
    halt_rate = halt_count / len(episode_metrics)
    median_turnover = statistics.median(turnover)
    median_cost_rate = statistics.median(costs)
    concentration = _performance_concentration(returns)
    weights = FITNESS_PROTOCOL["weights"]
    fitness = (
        weights["median_episode_return"] * median_return
        + weights["median_episode_sharpe"] * median_sharpe
        + weights["absolute_worst_drawdown"] * abs(worst_drawdown)
        + weights["consistency_score"] * consistency
        + weights["halt_rate"] * halt_rate
        + weights["median_episode_turnover"] * median_turnover
        + weights["median_transaction_cost_rate"] * median_cost_rate
        + weights["performance_concentration"] * concentration
    )
    places = FITNESS_PROTOCOL["fitness_round_decimal_places"]
    return {
        "fitness": round(fitness, places),
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
        "total_commission_cents": sum(row["commission_cents"] for row in episode_metrics),
        "total_slippage_cents": sum(row["slippage_cents"] for row in episode_metrics),
    }


def evaluate_genome(bundle, genome: dict, *, verify: bool = False) -> dict:
    validate_genome(genome)
    episodes = [
        simulate_episode(bundle, genome, index, verify=verify)
        for index in range(len(EPISODE_PROTOCOL["episodes"]))
    ]
    aggregate = compute_fitness(episodes)
    return {
        "episode_metrics": episodes,
        "aggregate": aggregate,
        "metrics_hash": hashlib.sha256(
            canonical_json({"episodes": episodes, "aggregate": aggregate}).encode()
        ).hexdigest(),
    }
