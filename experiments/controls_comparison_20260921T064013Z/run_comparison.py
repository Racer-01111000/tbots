#!/usr/bin/env python3
"""Orchestrates the full controls comparison: loads the champion + 40
top-eight genomes + 3 synthetic controls, evaluates all 44 on the 12 frozen
DEVELOPMENT episodes, writes results.json. Read-only against NODE-derived
data already committed in tbots; writes only inside this directory."""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import control_agent
import s6a_final as p
import s6a_runtime as r
import harness
import synthetic_controls as sc

TOP_EIGHT_FILES = {
    "B_primary": ROOT / "evolution/s6b_runs/primary/B_s6a_b_11cf933f3df42f14f360338c1a834fda684eb2d58705abfd90b73ca8afe75d79/development_top_eight.json",
    "B_reproduction": ROOT / "evolution/s6b_runs/reproduction/B_s6a_b_11cf933f3df42f14f360338c1a834fda684eb2d58705abfd90b73ca8afe75d79/development_top_eight.json",
    "C_primary": ROOT / "evolution/s6b_runs/primary/C_s6a_c_99e51f2f688620f2bf46c8cc1d06cfefc5ea608d19aa378ebcb65d9ef8ac9c5a/development_top_eight.json",
    "D_primary": ROOT / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json",
    "D_reproduction": ROOT / "evolution/s6b_runs/reproduction/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json",
}
CHAMPION_FILE = ROOT / "evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json"


def load_top_eight(path: Path, lineage: str, group_label: str) -> list[dict]:
    data = json.loads(path.read_text())
    out = []
    for entry in data["frozen"]:
        assert entry["lineage"] == lineage, f"{path}: lineage mismatch"
        out.append({
            "agent_id": f"{group_label}_rank{entry['development_rank']}",
            "kind": "evolved",
            "lineage": lineage,
            "group": group_label,
            "genome_id": entry["genome_id"],
            "genome": entry["genome"],
            "development_rank": entry["development_rank"],
            "original_fitness": entry["fitness"],
        })
    return out


def load_champion() -> dict:
    data = json.loads(CHAMPION_FILE.read_text())
    champ = data["content"]["champion"]
    return {
        "agent_id": "S5D_CHAMPION",
        "kind": "champion",
        "lineage": "trend",
        "group": "champion",
        "genome_id": champ["genome_id"],
        "genome": champ["genome"],
    }


def per_agent_summary(episode_metrics: list[dict]) -> dict:
    returns = [e["total_return"] for e in episode_metrics]
    drawdowns = [e["max_drawdown"] for e in episode_metrics]
    sharpes = [e["sharpe"] for e in episode_metrics]
    sortinos = [e["sortino"] for e in episode_metrics]
    turnovers = [e["turnover"] for e in episode_metrics]
    cost_rates = [e["transaction_cost_rate"] for e in episode_metrics]
    cost_cents_total = sum(e["transaction_cost_cents"] for e in episode_metrics) if "transaction_cost_cents" in episode_metrics[0] else None
    halted = [bool(e["halted"]) for e in episode_metrics]
    ever_held = [bool(e["ever_held_position"]) for e in episode_metrics]
    return {
        "total_return_median": statistics.median(returns),
        "total_return_worst": min(returns),
        "total_return_best": max(returns),
        "max_drawdown_worst": min(drawdowns),
        "sharpe_median": statistics.median(sharpes),
        "sortino_median": statistics.median(sortinos),
        "turnover_median": statistics.median(turnovers),
        "transaction_cost_rate_median": statistics.median(cost_rates),
        "transaction_cost_cents_total": cost_cents_total,
        "pct_episodes_halted": 100.0 * sum(halted) / len(halted),
        "pct_episodes_fully_invested": 100.0 * sum(ever_held) / len(ever_held),
        "pct_episodes_cash_preserving": 100.0 * (len(ever_held) - sum(ever_held)) / len(ever_held),
    }


def compare_to_control(real_episodes: list[dict], control_episodes: list[dict]) -> dict:
    n = len(real_episodes)
    wins = {"total_return": 0, "max_drawdown": 0, "sharpe": 0, "sortino": 0}
    for re_, ce in zip(real_episodes, control_episodes):
        if re_["total_return"] > ce["total_return"]:
            wins["total_return"] += 1
        if re_["max_drawdown"] > ce["max_drawdown"]:  # closer to 0 (less negative) = better
            wins["max_drawdown"] += 1
        if re_["sharpe"] > ce["sharpe"]:
            wins["sharpe"] += 1
        if re_["sortino"] > ce["sortino"]:
            wins["sortino"] += 1
    return {k: v / n for k, v in wins.items()}


def main():
    t0 = time.time()
    print("Loading verified DEVELOPMENT bundle...", flush=True)
    bundle = r.load_historical_bundle("development")
    availability = r.development_history_availability(bundle)
    print(f"Bundle loaded: dataset_revision={bundle.dataset_revision}", flush=True)

    agents = []
    for group, path in TOP_EIGHT_FILES.items():
        lineage = group[0]
        agents.extend(load_top_eight(path, lineage, group))
    champion = load_champion()
    agents.append(champion)
    print(f"Loaded {len(agents)} real genomes (incl. champion)", flush=True)

    results = {}

    for agent in agents:
        aid = agent["agent_id"]
        print(f"Evaluating {aid} ({agent['kind']}, lineage={agent['lineage']})...", flush=True)
        if agent["kind"] == "evolved":
            code = agent["lineage"]
            admission = r.require_development_feasible(code, agent["genome"], availability=availability)
            out = harness.evaluate_bcd_agent_with_curve(bundle, code, agent["genome"], admission)
        else:
            genome = agent["genome"]

            def decide_fn_factory(_idx, _genome=genome):
                def fn(view, step):
                    if step % _genome["rebalance_every_n_sessions"] != 0:
                        return None
                    return control_agent.decide(view, _genome)["weights"]
                return fn

            out = harness.evaluate_generic_agent(
                bundle, decide_fn_factory,
                max_asset_weight=genome["max_asset_weight"],
                max_total_exposure=genome["target_max_exposure"],
            )
        results[aid] = {"agent_meta": {k: v for k, v in agent.items() if k != "genome"} | {"genome": agent["genome"]},
                        "episode_metrics": out["episode_metrics"]}

    # Three synthetic controls
    print("Evaluating NO_TRADE_CONTROL...", flush=True)
    out = harness.evaluate_generic_agent(
        bundle, lambda idx: sc.no_trade_decide_fn, **sc.NO_TRADE_RISK,
    )
    results["NO_TRADE_CONTROL"] = {"agent_meta": {"agent_id": "NO_TRADE_CONTROL", "kind": "control"},
                                   "episode_metrics": out["episode_metrics"]}

    print("Evaluating BUY_AND_HOLD_SPY_CONTROL...", flush=True)
    out = harness.evaluate_generic_agent(
        bundle, lambda idx: sc.buy_and_hold_spy_decide_fn, **sc.BUY_AND_HOLD_SPY_RISK,
    )
    results["BUY_AND_HOLD_SPY_CONTROL"] = {"agent_meta": {"agent_id": "BUY_AND_HOLD_SPY_CONTROL", "kind": "control"},
                                           "episode_metrics": out["episode_metrics"]}

    print("Evaluating RANDOM_SIGNAL_CONTROL...", flush=True)
    out = harness.evaluate_generic_agent(
        bundle, sc.make_random_signal_decide_fn, **sc.RANDOM_SIGNAL_RISK,
    )
    results["RANDOM_SIGNAL_CONTROL"] = {"agent_meta": {"agent_id": "RANDOM_SIGNAL_CONTROL", "kind": "control",
                                                       "seed": sc.RANDOM_CONTROL_SEED},
                                        "episode_metrics": out["episode_metrics"]}

    # Summaries + comparison matrix
    summaries = {aid: per_agent_summary(r_["episode_metrics"]) for aid, r_ in results.items()}
    control_ids = ["NO_TRADE_CONTROL", "BUY_AND_HOLD_SPY_CONTROL", "RANDOM_SIGNAL_CONTROL"]
    comparison = {}
    for aid, r_ in results.items():
        if aid in control_ids:
            continue
        comparison[aid] = {}
        for cid in control_ids:
            comparison[aid][cid] = compare_to_control(r_["episode_metrics"], results[cid]["episode_metrics"])

    flags = {}
    for aid, cmp_ in comparison.items():
        beats_all_all = all(
            frac > 0.5 for cid in control_ids for frac in cmp_[cid].values()
        )
        beats_no_trade_return = cmp_["NO_TRADE_CONTROL"]["total_return"] > 0.5
        flags[aid] = {"beats_all_controls_all_metrics_gt50pct": beats_all_all,
                     "beats_no_trade_after_costs_gt50pct": beats_no_trade_return}

    out_path = HERE / "results.json"
    with open(out_path, "w") as f:
        json.dump({
            "dataset_revision": bundle.dataset_revision,
            "random_control_seed": sc.RANDOM_CONTROL_SEED,
            "agent_count": len(results),
            "results": results,
            "summaries": summaries,
            "comparison_matrix": comparison,
            "aggregate_flags": flags,
        }, f, indent=2, default=str)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
