#!/usr/bin/env python3
"""GO -- PASSIVE_ENVELOPE DEVELOPMENT-LANE GAP CLOSURE (2026-09-23).

Bounded question: does the existing PASSIVE_ENVELOPE_CONTROL (built for the
FINAL_RESERVE/CHAMPIONSHIP holdout GO, experiments/
controls_interpretation_holdout_20260921T140000Z/holdout_controls.py) also
outperform the evolved population across the same 12 frozen DEVELOPMENT
episodes (2007-02-07 .. 2018-12-31) already used for the 41 real agents in
experiments/controls_comparison_20260921T064013Z/results.json?

This script:
  1. Imports PASSIVE_ENVELOPE_CONTROL's decide function and risk envelope
     UNCHANGED from holdout_controls.py (read-only import, that file is
     never modified or re-derived).
  2. Imports harness.evaluate_generic_agent / _simulate_generic_episode
     UNCHANGED from controls_comparison_20260921T064013Z/harness.py -- the
     same frozen DB-free per-step loop (execution.Portfolio, risk.validate,
     execution.compute_orders, same T+1-fill / same-session-close-mark /
     12%-drawdown-halt sequence, same EPISODE_PROTOCOL 12-episode list) used
     for the original 41 real agents and the original 3 controls. Nothing
     in harness.py is modified.
  3. Additionally runs one parallel, read-only-in-spirit loop (same pattern
     the repo already uses in harness.py's _simulate_bcd_with_curve, which
     exists solely to expose Sortino/realized-position data the frozen
     evaluator doesn't return) to capture per-step invested weight
     (1 - cash_cents/equity_cents) for a "realized exposure" figure that
     evaluate_generic_agent's return dict does not expose. This parallel
     run is asserted byte/near-identical (total_return, sharpe, max_drawdown,
     turnover, halted, order_count, fill_count) against the OFFICIAL
     evaluate_generic_agent output before being trusted, exactly mirroring
     the existing evaluate_bcd_agent_with_curve cross-check pattern.
  4. Loads the ALREADY-RECORDED 41-agent DEV results from
     controls_comparison_20260921T064013Z/results.json (not rerun).
  5. Builds the comparison: fraction of (agent, episode) pairs where
     PASSIVE_ENVELOPE beats the real agent, per metric; champion-specific
     and D1/D2/D3-specific breakdowns.

Writes only into this directory. No modification of any existing file, no
new controls, no genome mutation, no NODE access, no commit/push.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPARISON_DIR = ROOT / "experiments" / "controls_comparison_20260921T064013Z"
HOLDOUT_DIR = ROOT / "experiments" / "controls_interpretation_holdout_20260921T140000Z"

sys.path[:0] = [str(COMPARISON_DIR), str(HOLDOUT_DIR), str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import execution  # noqa: E402  (frozen, unmodified)
import risk  # noqa: E402  (frozen, unmodified)
import s6a_final as p  # noqa: E402  (frozen, unmodified)
import s6a_runtime as r  # noqa: E402  (frozen, unmodified)
import s6b_evaluator as s6b  # noqa: E402  (frozen, unmodified)
from lib.replay import AgentView, ReplayEngine  # noqa: E402  (frozen, unmodified)
from s5a_config import EPISODE_PROTOCOL  # noqa: E402  (frozen, unmodified)

import harness  # noqa: E402  (controls_comparison dir, unmodified)
import holdout_controls as hc  # noqa: E402  (holdout dir, unmodified)

STARTING_CASH_CENTS = p.EXECUTION["starting_cash_cents"]
LANE_END = p.HISTORY["development"]["end"]
UNIVERSE = p.UNIVERSE
DRAWDOWN_HALT = p.DRAWDOWN_HALT

RESULTS_JSON = COMPARISON_DIR / "results.json"
CONTROL_IDS = {"NO_TRADE_CONTROL", "BUY_AND_HOLD_SPY_CONTROL", "RANDOM_SIGNAL_CONTROL"}
HEADLINE_IDS = ["S5D_CHAMPION", "D_primary_rank1", "D_primary_rank2", "D_primary_rank3"]


def dollars_to_cents(value: str) -> int:
    return round(float(value) * 100)


def _simulate_passive_with_exposure(bundle, episode_index: int) -> dict:
    """Literal parallel of harness._simulate_generic_episode for
    PASSIVE_ENVELOPE_CONTROL specifically, with one addition: per-step
    invested-weight capture (1 - cash_cents / equity_cents) so a realized
    exposure figure can be reported. Same primitives, same call order, same
    execution assumptions as the frozen harness -- cross-checked below
    against the OFFICIAL evaluate_generic_agent output before being
    trusted."""
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
    invested_weights = []
    order_count = 0
    total_slippage_cents = 0
    step = 0
    decide_fn = hc.passive_envelope_decide_fn
    max_asset_weight = hc.PASSIVE_ENVELOPE_RISK["max_asset_weight"]
    max_total_exposure = hc.PASSIVE_ENVELOPE_RISK["max_total_exposure"]

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

        invested_weight = 1.0 - (portfolio.cash_cents / equity_cents) if equity_cents else 0.0
        invested_weights.append(invested_weight)
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
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": s6b.episode_sharpe(daily_returns),
        "sortino": harness.episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
        "realized_exposure_mean": statistics.mean(invested_weights),
        "realized_exposure_end": invested_weights[-1],
        "realized_exposure_max": max(invested_weights),
    }


def compare_agent_pair(control_episodes: list[dict], real_episodes: list[dict]) -> dict:
    """Fraction of episodes (of the 12 DEV episodes, a single correlated
    2007-2018 historical path sliced into contiguous windows -- NOT 12
    statistically independent samples) where PASSIVE_ENVELOPE_CONTROL beats
    the given real agent on each metric."""
    n = len(real_episodes)
    wins = {"total_return": 0, "max_drawdown": 0, "sharpe": 0, "sortino": 0}
    for ce, re_ in zip(control_episodes, real_episodes):
        if ce["total_return"] > re_["total_return"]:
            wins["total_return"] += 1
        if ce["max_drawdown"] > re_["max_drawdown"]:  # closer to 0 (less negative) = better
            wins["max_drawdown"] += 1
        if ce["sharpe"] > re_["sharpe"]:
            wins["sharpe"] += 1
        if ce["sortino"] > re_["sortino"]:
            wins["sortino"] += 1
    return {k: v / n for k, v in wins.items()}


def main():
    t0 = time.time()

    print("Loading verified DEVELOPMENT bundle...", flush=True)
    bundle = r.load_historical_bundle("development")
    print(f"Bundle loaded: dataset_revision={bundle.dataset_revision}", flush=True)

    # --- Step A: OFFICIAL numbers via the frozen, unmodified harness -----
    print("Evaluating PASSIVE_ENVELOPE_CONTROL via frozen harness.evaluate_generic_agent (official, unmodified)...", flush=True)
    official = harness.evaluate_generic_agent(
        bundle, lambda idx: hc.passive_envelope_decide_fn, **hc.PASSIVE_ENVELOPE_RISK,
    )
    official_episodes = official["episode_metrics"]

    # --- Step B: parallel run for realized-exposure capture --------------
    print("Running parallel exposure-capturing simulation (cross-checked against official)...", flush=True)
    parallel_episodes = [
        _simulate_passive_with_exposure(bundle, idx) for idx in range(len(EPISODE_PROTOCOL["episodes"]))
    ]

    mismatches_all = []
    for off, par in zip(official_episodes, parallel_episodes):
        mism = []
        for key in ("total_return", "sharpe", "max_drawdown", "turnover", "transaction_cost_rate",
                    "halted", "order_count", "fill_count"):
            a, b = off[key], par[key]
            if isinstance(a, float):
                if abs(a - b) > 1e-9:
                    mism.append((key, a, b))
            elif a != b:
                mism.append((key, a, b))
        if mism:
            mismatches_all.append((off["episode_index"], mism))
    if mismatches_all:
        raise AssertionError(f"parallel exposure-capture simulation diverged from official evaluator: {mismatches_all}")
    print("Parallel run matches official evaluator exactly on all cross-checked fields (12/12 episodes).", flush=True)

    merged_episodes = []
    for off, par in zip(official_episodes, parallel_episodes):
        merged = dict(off)
        merged["realized_exposure_mean"] = par["realized_exposure_mean"]
        merged["realized_exposure_end"] = par["realized_exposure_end"]
        merged["realized_exposure_max"] = par["realized_exposure_max"]
        merged_episodes.append(merged)

    # --- Step C: determinism rerun (full official + parallel, from scratch)
    print("Determinism rerun: full 12-episode PASSIVE_ENVELOPE evaluation, from scratch...", flush=True)
    bundle2 = r.load_historical_bundle("development")
    rerun_official = harness.evaluate_generic_agent(
        bundle2, lambda idx: hc.passive_envelope_decide_fn, **hc.PASSIVE_ENVELOPE_RISK,
    )["episode_metrics"]
    determinism_mismatches = []
    for a, b in zip(official_episodes, rerun_official):
        for key in a:
            if key == "daily_returns":
                if a[key] != b[key]:
                    determinism_mismatches.append(("daily_returns", a["episode_index"]))
                continue
            av, bv = a[key], b[key]
            if isinstance(av, float):
                if av != bv:
                    determinism_mismatches.append((key, a["episode_index"], av, bv))
            elif av != bv:
                determinism_mismatches.append((key, a["episode_index"], av, bv))
    determinism_byte_identical = len(determinism_mismatches) == 0
    print(f"Determinism rerun byte-identical: {determinism_byte_identical}", flush=True)

    # --- Step D: load already-recorded 41-agent DEV results (not rerun) --
    print(f"Loading already-recorded 41-agent DEV results from {RESULTS_JSON}...", flush=True)
    recorded = json.loads(RESULTS_JSON.read_text())
    real_agent_ids = [aid for aid in recorded["results"] if aid not in CONTROL_IDS]
    assert len(real_agent_ids) == 41, f"expected 41 real agents, found {len(real_agent_ids)}"

    # --- Step E: comparison matrix ---------------------------------------
    per_agent_comparison = {}
    for aid in real_agent_ids:
        real_episodes = recorded["results"][aid]["episode_metrics"]
        per_agent_comparison[aid] = compare_agent_pair(merged_episodes, real_episodes)

    pooled = {m: statistics.mean(per_agent_comparison[aid][m] for aid in real_agent_ids)
              for m in ("total_return", "max_drawdown", "sharpe", "sortino")}

    headline = {aid: per_agent_comparison.get(aid) for aid in HEADLINE_IDS}

    # --- Step F: summary stats over the 12 episodes -----------------------
    summary = {
        "total_return_median": statistics.median(e["total_return"] for e in merged_episodes),
        "total_return_worst": min(e["total_return"] for e in merged_episodes),
        "total_return_best": max(e["total_return"] for e in merged_episodes),
        "max_drawdown_worst": min(e["max_drawdown"] for e in merged_episodes),
        "sharpe_median": statistics.median(e["sharpe"] for e in merged_episodes),
        "sortino_median": statistics.median(e["sortino"] for e in merged_episodes),
        "turnover_median": statistics.median(e["turnover"] for e in merged_episodes),
        "realized_exposure_mean_median": statistics.median(e["realized_exposure_mean"] for e in merged_episodes),
        "realized_exposure_end_median": statistics.median(e["realized_exposure_end"] for e in merged_episodes),
        "pct_episodes_halted": 100.0 * sum(bool(e["halted"]) for e in merged_episodes) / len(merged_episodes),
    }

    out = {
        "dataset_revision": bundle.dataset_revision,
        "note_on_episode_independence": (
            "The 12 DEVELOPMENT episodes are contiguous, non-overlapping, "
            "back-to-back annual slices of a single 2007-2018 historical "
            "path -- NOT 12 statistically independent samples. Any "
            "episode-count-based fraction below (e.g. 'beats agent X in "
            "N/12 episodes') describes one correlated historical "
            "realization examined at 12 checkpoints, not 12 independent "
            "trials. This applies to every fraction in this file."
        ),
        "known_limitation_dividend_accounting": (
            "The frozen execution.py simulator marks daily equity using "
            "each session's RAW (unadjusted) close and credits no dividend "
            "cash on ex-dividend dates anywhere in Portfolio. This defect "
            "is preserved UNCHANGED in this experiment for comparability "
            "with all prior DEV/CHAMPIONSHIP/FINAL_RESERVE results, per "
            "explicit GO instruction. It silently understates total return "
            "for any agent (including PASSIVE_ENVELOPE_CONTROL itself, "
            "which holds all 8 universe assets including dividend payers "
            "for the full episode in every episode) holding a "
            "dividend-paying position through an ex-date. Not fixed, not "
            "quantified in aggregate here."
        ),
        "passive_envelope_episode_metrics": merged_episodes,
        "passive_envelope_summary": summary,
        "determinism_rerun": {
            "byte_identical": determinism_byte_identical,
            "mismatches": determinism_mismatches,
        },
        "comparison_vs_41_real_agents": {
            "per_agent": per_agent_comparison,
            "pooled_492_pairs_mean_of_41_per_agent_fractions": pooled,
            "headline": headline,
        },
        "source_results_json": str(RESULTS_JSON),
    }

    out_path = HERE / "passive_envelope_dev_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, sha256={out_hash})", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
