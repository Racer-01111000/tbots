#!/usr/bin/env python3
"""GO -- D1 QUALIFICATION-LANE GENERALIZATION TEST (2026-09-23).

Bounded question: does D_primary_rank1's DEV-lane risk-adjusted advantage
over PASSIVE_ENVELOPE_EXPOSURE_MATCHED (23% gross exposure, matched to D1's
own frozen gross_exposure_cap) survive on the 2019-2022 QUALIFICATION lane,
which was never used to select/tune/qualify/mutate/rank/promote D1?

METHODOLOGICAL NOTE (read before trusting "same evaluator" claims elsewhere):
The frozen production evaluator used throughout this experiment series
(s6b_evaluator.evaluate_genome / _simulate_admitted_episode, and the
DEVELOPMENT-only harness.py built on top of it) is HARD-BOUND to the
DEVELOPMENT lane in three independent places:
  1. s6b_evaluator.py imports `from s5a_config import EPISODE_PROTOCOL`
     at module scope -- this is DEVELOPMENT's 12-episode protocol, and
     s5a_config.py itself asserts `EPISODE_PROTOCOL["lane_manifest_hash"]
     == DEVELOPMENT_LANE_HASH` at import time. There is no parameter to
     swap this for QUALIFICATION's protocol.
  2. s6b_evaluator.require_admission_token() calls
     r.require_development_feasible(...) -- named, and behaviorally
     scoped, to DEVELOPMENT-lane price-bar availability only.
  3. s6a_runtime.load_historical_bundle("qualification") itself works
     fine (it is lane-generic), but nothing wires a QUALIFICATION-scoped
     evaluator to consume its output for B-G genomes.

So "use the same frozen DB-free evaluator ... only the evaluation interval
changes" cannot be satisfied by literally calling s6b_evaluator against a
qualification bundle -- it would raise a hash-mismatch/BoundaryError before
running anything. This is NOT a gap Rick's GO anticipated incorrectly; it is
a real architectural fact about this codebase, and the correct response
(consistent with how this same experiment series already handled an
analogous gap -- see controls_comparison_20260921T064013Z/harness.py's own
docstring: "s6b_evaluator has no entrypoint [for non-B-G agents] ... so
_simulate_generic_episode ... is a parallel, DB-free per-step loop built
from the *same* primitives in the *same* order") is to write a NEW,
self-contained script -- this file -- that:
  - imports execution.py, risk.py, s6a_runtime.py (for r.decide_D,
    r.warmup, r.validate_genome, risk_bounds), lib/replay.py (ReplayEngine,
    AgentView) UNMODIFIED, verbatim, by reference;
  - replicates s6b_evaluator._simulate_admitted_episode's per-step loop
    IDENTICALLY (same Portfolio/compute_orders/risk.validate/T+1-fill/
    12%-drawdown-halt sequence, same equity-curve/turnover/cost bookkeeping),
    for the D lineage's decide_D case only;
  - runs it against s6a_runtime.load_historical_bundle("qualification")
    (the existing, frozen, gate-checked loader -- untouched) and against
    s5b_config.EPISODE_PROTOCOL["episodes"] (the existing, PRE-EXISTING,
    frozen 4-calendar-year QUALIFICATION episode manifest -- not invented
    for this experiment, already hash-locked in the codebase and already
    exactly shaped as "one episode per calendar year 2019/2020/2021/2022",
    which is precisely what Rick's GO asked for).
Zero bytes of execution.py, risk.py, s6a_runtime.py, s6b_evaluator.py,
s5a_config.py, s5b_config.py, s5b_qualification_bundle.py, or any other
frozen file are modified by this script. No admission-token system is
bypassed for any DEVELOPMENT-lane purpose (this script never touches
DEVELOPMENT data or the DEVELOPMENT admission gate at all).

This script:
  1. Loads the QUALIFICATION bundle via the existing frozen loader and
     independently asserts s5b_qualification_bundle.assert_isolated(bundle)
     -- an existing, pre-built isolation check confirming zero championship/
     final-reserve/post-2022 exposure in the bundle actually used.
  2. Evaluates D_primary_rank1 (frozen genome, read from the same source
     file used throughout this series) across the 4 QUALIFICATION episodes,
     using r.decide_D unmodified.
  3. Evaluates PASSIVE_ENVELOPE_EXPOSURE_MATCHED (target_exposure=0.23,
     identical logic/parameters to experiments/d_exposure_isolation_
     20260923T052542Z/run_exposure_isolation.py) across the same 4 episodes.
  4. Reruns both from scratch and asserts byte-identical determinism.
  5. Writes per-episode, per-calendar-year, and aggregate comparisons.

Writes only into this directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D_EXPOSURE_DIR = ROOT / "experiments" / "d_exposure_isolation_20260923T052542Z"
COMPARISON_DIR = ROOT / "experiments" / "controls_comparison_20260921T064013Z"

sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import execution  # noqa: E402  (frozen, unmodified)
import risk  # noqa: E402  (frozen, unmodified)
import s5b_config as s5b  # noqa: E402  (frozen, unmodified)
import s5b_qualification_bundle as qb  # noqa: E402  (frozen, unmodified)
import s6a_final as p  # noqa: E402  (frozen, unmodified)
import s6a_runtime as r  # noqa: E402  (frozen, unmodified)
from lib.replay import AgentView, ReplayEngine  # noqa: E402  (frozen, unmodified)

STARTING_CASH_CENTS = p.EXECUTION["starting_cash_cents"]
assert STARTING_CASH_CENTS == s5b.STARTING_CASH_CENTS == 100_000_000
UNIVERSE = p.UNIVERSE
DRAWDOWN_HALT = p.DRAWDOWN_HALT
ANNUALIZATION = 252  # matches s6b_evaluator.ANNUALIZATION / harness.ANNUALIZATION

QUAL_EPISODES = s5b.EPISODE_PROTOCOL["episodes"]
QUAL_LANE_END = s5b.QUALIFICATION_END
assert [e["start_date"] for e in QUAL_EPISODES] == [
    "2019-01-01", "2020-01-01", "2021-01-01", "2022-01-01",
]
assert [e["end_date"] for e in QUAL_EPISODES] == [
    "2019-12-31", "2020-12-31", "2021-12-31", "2022-12-31",
]

# --- D_primary_rank1 (frozen genome, read verbatim from the same source ----
# file used throughout this experiment series -- NOT retyped, to avoid any
# float-literal precision drift, e.g. 0.35 vs the stored 0.35000000000000003)
D1_SOURCE_FILE = (
    ROOT / "evolution" / "s6b_runs" / "primary"
    / "D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca"
    / "development_top_eight.json"
)
D1_GENOME_ID = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
_d1_source = json.loads(D1_SOURCE_FILE.read_text())
_d1_entry = next(e for e in _d1_source["frozen"] if e["genome_id"] == D1_GENOME_ID)
assert _d1_entry["development_rank"] == 1 and _d1_entry["lineage"] == "D" and _d1_entry["verified"] is True
D1_GENOME = _d1_entry["genome"]
D1_SOURCE_SHA256 = hashlib.sha256(D1_SOURCE_FILE.read_bytes()).hexdigest()

# --- PASSIVE_ENVELOPE_EXPOSURE_MATCHED (identical to the immediately -------
# preceding GO's control -- same target exposure, same logic) ---------------
D_TARGET_EXPOSURE = 0.23
D_MATCHED_PER_ASSET = D_TARGET_EXPOSURE / len(UNIVERSE)  # 0.02875


def passive_envelope_exposure_matched_decide_fn(view, step):
    if step == 0:
        return {s: D_MATCHED_PER_ASSET for s in UNIVERSE}
    return None


def episode_sharpe(daily_returns):
    if len(daily_returns) < 2:
        return 0.0
    stdev = statistics.stdev(daily_returns)
    if stdev == 0:
        return 0.0
    return statistics.mean(daily_returns) / stdev * math.sqrt(ANNUALIZATION)


def episode_sortino(daily_returns):
    if len(daily_returns) < 2:
        return 0.0
    downside = [min(0.0, x) for x in daily_returns]
    downside_dev = math.sqrt(sum(d * d for d in downside) / len(daily_returns))
    if downside_dev == 0:
        return 0.0
    return statistics.mean(daily_returns) / downside_dev * math.sqrt(ANNUALIZATION)


def dollars_to_cents(value):
    return round(float(value) * 100)


def _simulate_d1_episode(bundle, genome, episode):
    """Byte-for-byte identical simulation logic to
    s6b_evaluator._simulate_admitted_episode's `code == "D"` branch,
    parameterized by an arbitrary (bundle, episode) pair instead of the
    DEVELOPMENT-hardcoded EPISODE_PROTOCOL/LANE_END. No behavior differs."""
    r.validate_genome("D", genome)
    engine = ReplayEngine(
        p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
        masked_time=True, random_seed=0, retention_end_date=QUAL_LANE_END,
        _verified_bundle=bundle,
    )
    view = AgentView(engine)
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    order_count = 0
    total_slippage_cents = 0
    step = 0

    needed = r.warmup("D", genome)
    h = {s: [float(row["adjusted_close"]) for row in view.history(s, needed)] for s in UNIVERSE} \
        if needed > 0 else {s: [] for s in UNIVERSE}

    while True:
        obs = view.observe()
        assets = obs["assets"]

        if step > 0:
            for s in UNIVERSE:
                h[s].append(float(assets[s]["adjusted_close"]))

        if pending is not None:
            for order in pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                total_slippage_cents += fill["slippage_cents"]
            pending = None

        mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in UNIVERSE}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if drawdown <= -DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted:
            target_weights = r.decide_D(genome, h, step)

        if target_weights is not None:
            target_weights = {s: w for s, w in target_weights.items() if s in UNIVERSE}
            max_asset_weight, max_total_exposure = genome["max_asset_weight"], genome["gross_exposure_cap"]
            risk.validate(
                target_weights, UNIVERSE, max_asset_weight=max_asset_weight,
                max_total_exposure=max_total_exposure,
                drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown,
            )
            current_shares = {s: portfolio.shares_of(s) for s in UNIVERSE if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, UNIVERSE, equity_cents, mark_prices, current_shares)
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
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "commission_cents": portfolio.total_commission_cents,
        "slippage_cents": total_slippage_cents,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
        "daily_returns": daily_returns,
    }


def _simulate_passive_matched_episode(bundle, episode):
    """Same simulation primitives, decide_fn is the fixed buy-once-at-step-0
    exposure-matched allocation (no genome, no regime logic)."""
    engine = ReplayEngine(
        p.ROOT, p.DATASET, episode["start_date"], episode["end_date"],
        masked_time=True, random_seed=0, retention_end_date=QUAL_LANE_END,
        _verified_bundle=bundle,
    )
    view = AgentView(engine)
    portfolio = execution.Portfolio(STARTING_CASH_CENTS)
    pending = None
    equity_curve = []
    order_count = 0
    total_slippage_cents = 0
    step = 0

    while True:
        obs = view.observe()
        assets = obs["assets"]

        if pending is not None:
            for order in pending:
                open_cents = dollars_to_cents(assets[order["symbol"]]["open"])
                fill = portfolio.apply_fill(order["symbol"], order["side"], order["shares"], open_cents)
                total_slippage_cents += fill["slippage_cents"]
            pending = None

        mark_prices = {s: dollars_to_cents(assets[s]["close"]) for s in UNIVERSE}
        equity_cents = portfolio.equity_cents(mark_prices)
        drawdown = portfolio.update_peak_and_drawdown(equity_cents)

        target_weights = None
        if drawdown <= -DRAWDOWN_HALT - 1e-9 and not portfolio.halted:
            portfolio.halted = True
            target_weights = {}
        elif not portfolio.halted:
            target_weights = passive_envelope_exposure_matched_decide_fn(view, step)

        if target_weights is not None:
            target_weights = {s: w for s, w in target_weights.items() if s in UNIVERSE}
            risk.validate(
                target_weights, UNIVERSE, max_asset_weight=0.35,
                max_total_exposure=D_TARGET_EXPOSURE,
                drawdown_halt_pct=DRAWDOWN_HALT, current_drawdown=drawdown,
            )
            current_shares = {s: portfolio.shares_of(s) for s in UNIVERSE if portfolio.shares_of(s) > 0}
            pending = execution.compute_orders(target_weights, UNIVERSE, equity_cents, mark_prices, current_shares)
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
        "final_equity_cents": final_equity_cents,
        "total_return": final_equity_cents / STARTING_CASH_CENTS - 1.0,
        "sharpe": episode_sharpe(daily_returns),
        "sortino": episode_sortino(daily_returns),
        "max_drawdown": max_drawdown,
        "halted": portfolio.halted,
        "turnover": portfolio.total_traded_notional_cents / STARTING_CASH_CENTS,
        "commission_cents": portfolio.total_commission_cents,
        "slippage_cents": total_slippage_cents,
        "transaction_cost_rate": transaction_cost_cents / STARTING_CASH_CENTS,
        "order_count": order_count,
        "fill_count": portfolio.fill_count,
        "step_count": step,
        "daily_returns": daily_returns,
    }


def run_all(bundle):
    d1 = [_simulate_d1_episode(bundle, D1_GENOME, ep) for ep in QUAL_EPISODES]
    pm = [_simulate_passive_matched_episode(bundle, ep) for ep in QUAL_EPISODES]
    return d1, pm


def strip_curve(episodes):
    return [{k: v for k, v in e.items() if k != "daily_returns"} for e in episodes]


def aggregate(episodes):
    if not episodes:
        return {}
    return {
        "n_episodes": len(episodes),
        "return_median": statistics.median(e["total_return"] for e in episodes),
        "return_worst": min(e["total_return"] for e in episodes),
        "return_best": max(e["total_return"] for e in episodes),
        "max_drawdown_worst": min(e["max_drawdown"] for e in episodes),
        "sharpe_median": statistics.median(e["sharpe"] for e in episodes),
        "sortino_median": statistics.median(e["sortino"] for e in episodes),
        "turnover_median": statistics.median(e["turnover"] for e in episodes),
        "pct_halted": 100.0 * sum(bool(e["halted"]) for e in episodes) / len(episodes),
    }


def compare(d1_episodes, pm_episodes):
    wins_by_d1 = {"total_return": 0, "max_drawdown": 0, "sharpe": 0, "sortino": 0}
    for d, m in zip(d1_episodes, pm_episodes):
        if d["total_return"] > m["total_return"]:
            wins_by_d1["total_return"] += 1
        if d["max_drawdown"] > m["max_drawdown"]:
            wins_by_d1["max_drawdown"] += 1
        if d["sharpe"] > m["sharpe"]:
            wins_by_d1["sharpe"] += 1
        if d["sortino"] > m["sortino"]:
            wins_by_d1["sortino"] += 1
    n = len(d1_episodes)
    return {k: v / n for k, v in wins_by_d1.items()}


def main():
    t0 = time.time()
    here = Path(__file__).resolve().parent

    print("Loading frozen QUALIFICATION bundle (existing, unmodified loader)...", flush=True)
    bundle = r.load_historical_bundle("qualification")
    print(f"Bundle loaded: dataset_revision={bundle.dataset_revision}", flush=True)

    print("Verifying bundle isolation (existing, unmodified assert_isolated)...", flush=True)
    isolation = qb.assert_isolated(bundle)
    print(f"Isolation OK: {isolation}", flush=True)

    print("Evaluating D_primary_rank1 and PASSIVE_ENVELOPE_EXPOSURE_MATCHED "
          "across the 4 frozen QUALIFICATION calendar-year episodes...", flush=True)
    d1_episodes, pm_episodes = run_all(bundle)

    print("Determinism rerun: full 4-episode evaluation, from scratch, both agents...", flush=True)
    bundle2 = r.load_historical_bundle("qualification")
    d1_rerun, pm_rerun = run_all(bundle2)

    mismatches = []
    for label, first, second in (("D1", d1_episodes, d1_rerun), ("PASSIVE_MATCHED", pm_episodes, pm_rerun)):
        for a, b in zip(first, second):
            for key in a:
                av, bv = a[key], b[key]
                if av != bv:
                    mismatches.append((label, key, a["episode_index"]))
    byte_identical = len(mismatches) == 0
    print(f"Determinism rerun byte-identical: {byte_identical}", flush=True)
    if not byte_identical:
        out = {"STOP": True, "reason": "unexplained nondeterminism", "mismatches": mismatches}
        (here / "STOP_nondeterminism.json").write_text(json.dumps(out, indent=2, default=str))
        print("STOP -- wrote STOP_nondeterminism.json", flush=True)
        return

    all_idx = list(range(4))
    comparison_all = compare(d1_episodes, pm_episodes)
    comparison_by_year = {
        QUAL_EPISODES[i]["start_date"][:4]: compare([d1_episodes[i]], [pm_episodes[i]])
        for i in all_idx
    }

    out = {
        "qualification_lane": {"start": s5b.QUALIFICATION_START, "end": s5b.QUALIFICATION_END,
                                "episode_rule": s5b.EPISODE_PROTOCOL["episode_rule"],
                                "episodes": QUAL_EPISODES},
        "dataset_revision": bundle.dataset_revision,
        "bundle_isolation_check": isolation,
        "tbots_head": "9e573323501684ef8adfcbfbe357aa314cfdbdb4",
        "d_primary_rank1": {
            "genome_id": D1_GENOME_ID,
            "genome": D1_GENOME,
            "source_file": str(D1_SOURCE_FILE),
            "source_file_sha256": D1_SOURCE_SHA256,
            "gross_exposure_cap": D_TARGET_EXPOSURE,
            "episode_metrics": strip_curve(d1_episodes),
            "aggregate_full_lane": aggregate(d1_episodes),
        },
        "passive_envelope_exposure_matched": {
            "target_exposure": D_TARGET_EXPOSURE,
            "per_asset_weight": D_MATCHED_PER_ASSET,
            "episode_metrics": strip_curve(pm_episodes),
            "aggregate_full_lane": aggregate(pm_episodes),
        },
        "comparison_fraction_d1_wins_full_lane": comparison_all,
        "comparison_fraction_d1_wins_by_calendar_year": comparison_by_year,
        "determinism_rerun": {"byte_identical": byte_identical, "mismatches": mismatches},
        "note_on_episode_independence": (
            "These 4 calendar-year QUALIFICATION episodes (2019, 2020, 2021, "
            "2022) are contiguous, non-overlapping annual slices of a single "
            "historical path, exactly as the 12 DEVELOPMENT episodes were -- "
            "NOT independent samples. Per-year figures are descriptive "
            "subdivisions of one correlated 4-year realization, not 4 "
            "independent trials."
        ),
        "known_limitation_dividend_accounting": (
            "The frozen execution.py simulator marks daily equity using "
            "each session's RAW (unadjusted) close and credits no dividend "
            "cash on ex-dividend dates anywhere in Portfolio. Preserved "
            "UNCHANGED in this experiment for comparability, per explicit "
            "GO instruction. Silently understates total return for both "
            "D_primary_rank1 and PASSIVE_ENVELOPE_EXPOSURE_MATCHED alike -- "
            "both hold dividend-paying universe assets during this window. "
            "Not fixed, not quantified in aggregate here."
        ),
        "methodological_note_new_script_not_evaluator_modification": (
            "See this file's module docstring: the frozen production "
            "evaluator (s6b_evaluator.py) is hash-bound to the DEVELOPMENT "
            "lane and cannot be pointed at QUALIFICATION without raising a "
            "BoundaryError/ProtocolError. This script is a new, "
            "self-contained file that reuses execution.py/risk.py/"
            "s6a_runtime.py (r.decide_D, r.warmup, r.validate_genome) "
            "UNMODIFIED and replicates s6b_evaluator._simulate_admitted_"
            "episode's per-step loop identically, parameterized by the "
            "QUALIFICATION bundle and the QUALIFICATION lane's own "
            "pre-existing, frozen 4-calendar-year episode manifest "
            "(s5b_config.EPISODE_PROTOCOL) instead of DEVELOPMENT's. No "
            "frozen file's bytes were changed."
        ),
    }

    out_path = here / "qualification_generalization_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, sha256={out_hash})", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
