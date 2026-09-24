"""Bounded DEVELOPMENT-lane revalidation: champion + D_primary_rank1 + the
3 canonical synthetic controls, across the 12 frozen DEV episodes, run
against a given scripts/ root (pre-repair or post-repair). No new genomes,
no candidate search, no QUALIFICATION/CHAMPIONSHIP/FINAL_RESERVE lane.

Usage: python3 dev_revalidation.py <repo_root> <out_json_path>
"""
import json
import random
import statistics
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
out_path = Path(sys.argv[2]).resolve()

sys.path[:0] = [str(repo_root / "scripts"), str(repo_root / "scripts" / "lib")]

import control_agent
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

CHAMPION_FILE = repo_root / "evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json"
D1_FILE = repo_root / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"

RANDOM_CONTROL_SEED = 20260921
CONTROL_ENVELOPE = {"max_positions": 3, "max_asset_weight": 0.35, "target_max_exposure": 0.80,
                    "rebalance_every_n_sessions": 21}


def dollars_to_cents(v):
    return round(float(v) * 100)


def episode_sortino(daily_returns):
    if len(daily_returns) < 2:
        return 0.0
    downside = [min(0.0, x) for x in daily_returns]
    downside_dev = (sum(d * d for d in downside) / len(daily_returns)) ** 0.5
    if downside_dev == 0:
        return 0.0
    return statistics.mean(daily_returns) / downside_dev * (252 ** 0.5)


def simulate_generic_episode(bundle, decide_fn, episode_index, *, max_asset_weight, max_total_exposure):
    episode = EPISODE_PROTOCOL["episodes"][episode_index]
    engine = ReplayEngine(p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
                          masked_time=True, random_seed=0, retention_end_date=LANE_END,
                          _verified_bundle=bundle)
    view = AgentView(engine)
    universe = UNIVERSE
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    step = 0
    while True:
        full = engine.observe()
        obs = view.observe()
        assets = obs["assets"]

        if hasattr(execution, "dividend_amount_dollars"):
            for s in universe:
                a = assets[s]
                if a["available"]:
                    div = execution.dividend_amount_dollars(a["corporate_action"])
                    if div:
                        portfolio.credit_dividend(s, div)

        if pending is not None:
            for order in pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
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
            risk.validate(target_weights, universe, max_asset_weight=max_asset_weight,
                          max_total_exposure=max_total_exposure,
                          drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown)
            current_shares = {s: portfolio.shares_of(s) for s in universe if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, universe, equity_cents, mark_prices, current_shares)

        equity_curve.append(equity_cents)
        step += 1
        if not engine.advance():
            break

    daily_returns = [equity_curve[i] / equity_curve[i - 1] - 1.0 if equity_curve[i - 1] else 0.0
                     for i in range(1, len(equity_curve))]
    final_equity_cents = equity_curve[-1]
    max_drawdown = min(equity / max(equity_curve[:i + 1]) - 1.0 for i, equity in enumerate(equity_curve))
    return {
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": s6b.episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "transaction_cost_cents": portfolio.total_commission_cents,
        "dividend_cents": getattr(portfolio, "total_dividend_cents", 0),
    }


def no_trade_decide_fn(view, step):
    return None


def buy_and_hold_spy_decide_fn(view, step):
    return {"SPY": 1.0} if step == 0 else None


def make_random_signal_decide_fn(episode_index):
    rng = random.Random(f"{RANDOM_CONTROL_SEED}-episode-{episode_index}")
    k = CONTROL_ENVELOPE["max_positions"]
    per_symbol_weight = CONTROL_ENVELOPE["target_max_exposure"] / k

    def decide_fn(view, step):
        if step % CONTROL_ENVELOPE["rebalance_every_n_sessions"] != 0:
            return None
        selected = rng.sample(UNIVERSE, k)
        return {s: per_symbol_weight for s in selected}
    return decide_fn


def make_genome_decide_fn(genome):
    def decide_fn(view, step):
        if step % genome["rebalance_every_n_sessions"] != 0:
            return None
        return control_agent.decide(view, genome)["weights"]
    return decide_fn


def evaluate_generic(bundle, decide_fn_factory, *, max_asset_weight, max_total_exposure):
    n = len(EPISODE_PROTOCOL["episodes"])
    return [simulate_generic_episode(bundle, decide_fn_factory(i), i,
                                     max_asset_weight=max_asset_weight, max_total_exposure=max_total_exposure)
            for i in range(n)]


def main():
    bundle = r.load_historical_bundle("development")
    availability = r.development_history_availability(bundle)

    subjects = {}

    champion = json.loads(CHAMPION_FILE.read_text())["content"]["champion"]["genome"]
    subjects["S5D_CHAMPION"] = evaluate_generic(
        bundle, lambda idx, g=champion: make_genome_decide_fn(g),
        max_asset_weight=champion["max_asset_weight"], max_total_exposure=champion["target_max_exposure"],
    )

    d1_entry = json.loads(D1_FILE.read_text())["frozen"][0]
    assert d1_entry["development_rank"] == 1 and d1_entry["lineage"] == "D"
    d1_genome = d1_entry["genome"]
    admission = r.require_development_feasible("D", d1_genome, availability=availability)
    official = s6b.evaluate_genome(bundle, "D", d1_genome, admission=admission)
    subjects["D_primary_rank1"] = [
        {**e, "dividend_cents": e.get("dividend_cents", 0)} for e in official["episode_metrics"]
    ]

    subjects["NO_TRADE_CONTROL"] = evaluate_generic(
        bundle, lambda idx: no_trade_decide_fn,
        max_asset_weight=CONTROL_ENVELOPE["max_asset_weight"], max_total_exposure=CONTROL_ENVELOPE["target_max_exposure"],
    )
    subjects["BUY_AND_HOLD_SPY_CONTROL"] = evaluate_generic(
        bundle, lambda idx: buy_and_hold_spy_decide_fn,
        max_asset_weight=1.0, max_total_exposure=1.0,
    )
    subjects["RANDOM_SIGNAL_CONTROL"] = evaluate_generic(
        bundle, lambda idx: make_random_signal_decide_fn(idx),
        max_asset_weight=CONTROL_ENVELOPE["max_asset_weight"], max_total_exposure=CONTROL_ENVELOPE["target_max_exposure"],
    )

    out_path.write_text(json.dumps({"dataset_revision": bundle.dataset_revision, "subjects": subjects}, indent=2, sort_keys=True))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
