#!/usr/bin/env python3
"""Runs the predeclared holdout evaluation set (champion + top-3 D
candidates + 4 controls) on the single FINAL_RESERVE episode
(2026-01-01..2026-08-25), per GO — TBOTS CONTROL INTERPRETATION + HOLDOUT
VALIDATION. Writes only inside this directory. Agent set and controls were
fixed in the GO/report text before any holdout number was computed --
nothing here is selected or tuned after seeing results."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import control_agent
import holdout_controls as hc
import holdout_harness as hh

CHAMPION_FILE = ROOT / "evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json"
D_TOP_EIGHT_FILE = ROOT / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca/development_top_eight.json"

# Predeclared BEFORE running: champion (verbatim) + D_primary development-lane
# rank 1/2/3 (already-fixed ranking from the prior, preserved comparison --
# not reselected here).
PREDECLARED_D_RANKS = [1, 2, 3]


def load_champion() -> dict:
    data = json.loads(CHAMPION_FILE.read_text())
    champ = data["content"]["champion"]
    return {"agent_id": "S5D_CHAMPION", "kind": "champion", "genome_id": champ["genome_id"], "genome": champ["genome"]}


def load_predeclared_d(ranks: list[int]) -> list[dict]:
    data = json.loads(D_TOP_EIGHT_FILE.read_text())
    by_rank = {e["development_rank"]: e for e in data["frozen"]}
    out = []
    for rk in ranks:
        e = by_rank[rk]
        out.append({"agent_id": f"D_primary_rank{rk}", "kind": "evolved", "lineage": "D",
                     "genome_id": e["genome_id"], "genome": e["genome"], "development_rank": rk,
                     "development_fitness": e["fitness"]})
    return out


def champion_decide_fn_factory(genome):
    def fn(view, step):
        if step % genome["rebalance_every_n_sessions"] != 0:
            return None
        return control_agent.decide(view, genome)["weights"]
    return fn


def evaluate_agent(agent: dict) -> dict:
    kind = agent["kind"]
    if kind == "champion":
        genome = agent["genome"]
        result = hh.simulate_generic_holdout(
            champion_decide_fn_factory(genome),
            max_asset_weight=genome["max_asset_weight"], max_total_exposure=genome["target_max_exposure"],
        )
    elif kind == "evolved":  # D lineage
        result = hh.simulate_D_holdout(agent["genome"])
    else:
        raise ValueError(f"unknown kind {kind}")
    return result


def evaluate_control(control_id: str) -> dict:
    if control_id == "NO_TRADE_CONTROL":
        return hh.simulate_generic_holdout(hc.no_trade_decide_fn, **hc.NO_TRADE_RISK)
    if control_id == "BUY_AND_HOLD_SPY_CONTROL":
        return hh.simulate_generic_holdout(hc.buy_and_hold_spy_decide_fn, **hc.BUY_AND_HOLD_SPY_RISK)
    if control_id == "RANDOM_SIGNAL_CONTROL":
        # single holdout episode -> episode_index 0, same seed convention as DEVELOPMENT run
        return hh.simulate_generic_holdout(hc.make_random_signal_decide_fn(0), **hc.RANDOM_SIGNAL_RISK)
    if control_id == "PASSIVE_ENVELOPE_CONTROL":
        return hh.simulate_generic_holdout(hc.passive_envelope_decide_fn, **hc.PASSIVE_ENVELOPE_RISK)
    raise ValueError(control_id)


def main():
    t0 = time.time()
    agents = [load_champion()] + load_predeclared_d(PREDECLARED_D_RANKS)
    control_ids = ["NO_TRADE_CONTROL", "BUY_AND_HOLD_SPY_CONTROL", "RANDOM_SIGNAL_CONTROL", "PASSIVE_ENVELOPE_CONTROL"]

    results = {}
    for agent in agents:
        print(f"Evaluating {agent['agent_id']} on holdout...", flush=True)
        metrics = evaluate_agent(agent)
        results[agent["agent_id"]] = {
            "agent_meta": {k: v for k, v in agent.items() if k != "genome"} | {"genome": agent["genome"]},
            "holdout_metrics": metrics,
        }
    for cid in control_ids:
        print(f"Evaluating {cid} on holdout...", flush=True)
        metrics = evaluate_control(cid)
        results[cid] = {"agent_meta": {"agent_id": cid, "kind": "control"}, "holdout_metrics": metrics}

    # Determinism check: rerun the champion from scratch, compare byte-for-byte (via canonical JSON)
    print("Determinism rerun: S5D_CHAMPION...", flush=True)
    rerun = evaluate_agent(agents[0])
    first_json = json.dumps(results["S5D_CHAMPION"]["holdout_metrics"], sort_keys=True)
    rerun_json = json.dumps(rerun, sort_keys=True)
    determinism_ok = first_json == rerun_json

    out_path = HERE / "holdout_results.json"
    with open(out_path, "w") as f:
        json.dump({
            "dataset_revision": hh.p.DATASET,
            "holdout_window": {"start": hh.HOLDOUT_START, "end": hh.HOLDOUT_END},
            "predeclared_d_ranks": PREDECLARED_D_RANKS,
            "random_control_seed": hc.RANDOM_CONTROL_SEED,
            "results": results,
            "determinism_check": {"agent": "S5D_CHAMPION", "byte_identical_rerun": determinism_ok},
        }, f, indent=2, default=str)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)", flush=True)
    print(f"determinism_ok={determinism_ok}", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
