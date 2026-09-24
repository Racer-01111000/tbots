#!/usr/bin/env python3
"""GO -- D-LINEAGE EXPOSURE-ISOLATION EXPERIMENT (2026-09-23).

Bounded question: does D_primary_rank1's DEV-lane risk-adjusted advantage
over PASSIVE_ENVELOPE_CONTROL survive when the passive control is scaled
down to D's own frozen exposure ceiling (gross_exposure_cap=0.23), or does
it substantially disappear? See PREDECLARATION.md (written before this
script ran) for the normalization method and rejected alternatives.

This script:
  1. Defines PASSIVE_ENVELOPE_EXPOSURE_MATCHED -- literally the existing
     PASSIVE_ENVELOPE_CONTROL decide function (equal weight, buy-once,
     never-rebalance, imported unmodified from holdout_controls.py) with
     ONLY max_total_exposure changed from 0.80 to 0.23 and the per-asset
     weight recomputed as 0.23/8. No other logic changes.
  2. Evaluates it via harness.evaluate_generic_agent -- the same frozen
     DB-free evaluator used for every agent in this experiment series --
     across the same 12 DEVELOPMENT episodes.
  3. Loads D_primary_rank1's ALREADY-RECORDED 12-episode DEV results from
     controls_comparison_20260921T064013Z/results.json (NOT rerun -- its
     signal logic is frozen and untouched).
  4. Loads the ORIGINAL 80%-exposure PASSIVE_ENVELOPE_CONTROL's
     already-recorded DEV results (NOT rerun) as a reference-only series.
  5. Builds the per-episode and aggregate (all-12 / non-stress-10 /
     stress-2) comparison between D_primary_rank1 and
     PASSIVE_ENVELOPE_EXPOSURE_MATCHED.
  6. Reruns PASSIVE_ENVELOPE_EXPOSURE_MATCHED from scratch and asserts
     byte-identical determinism.

Writes only into this directory. Does not modify execution.py, risk.py,
s6b_evaluator.py, s6a_runtime.py, holdout_controls.py, or any existing
results file. No genome mutation, no NODE access, no commit/push.
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
DEV_GAP_DIR = ROOT / "experiments" / "passive_envelope_dev_gap_20260923T045250Z"

sys.path[:0] = [str(COMPARISON_DIR), str(HOLDOUT_DIR), str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import s6a_final as p  # noqa: E402  (frozen, unmodified)
import s6a_runtime as r  # noqa: E402  (frozen, unmodified)

import harness  # noqa: E402  (controls_comparison dir, unmodified)
import holdout_controls as hc  # noqa: E402  (holdout dir, unmodified -- decide fn reused verbatim)

STRESS_EPISODE_INDICES = {1, 8}  # 2008-02..2009-02 and 2015-02..2016-02, per passive_envelope_dev_gap run

# --- PASSIVE_ENVELOPE_EXPOSURE_MATCHED -------------------------------------
# Literally hc.passive_envelope_decide_fn (imported unmodified) -- the
# underlying per-asset TARGET SET is the same 8-asset equal-weight universe.
# Only the risk envelope kwargs passed to the frozen evaluator change: the
# evaluator's own execution.compute_orders sizes each order from
# target_weights * equity, so re-declaring the decide_fn's return dict to the
# D-matched per-asset weight (rather than relying on risk.validate to scale
# it down) is what actually changes the realized allocation. This mirrors
# hc.passive_envelope_decide_fn's own structure exactly, just parameterized.
UNIVERSE = hc.UNIVERSE
D_TARGET_EXPOSURE = 0.23  # D_primary_rank1 gross_exposure_cap, from PREDECLARATION.md
D_MATCHED_PER_ASSET = D_TARGET_EXPOSURE / len(UNIVERSE)  # 0.02875
D_MATCHED_RISK = {"max_asset_weight": 0.35, "max_total_exposure": D_TARGET_EXPOSURE}


def passive_envelope_exposure_matched_decide_fn(view, step):
    if step == 0:
        return {s: D_MATCHED_PER_ASSET for s in UNIVERSE}
    return None


def compare_pair(a_episodes: list[dict], b_episodes: list[dict], b_wins_label: str) -> dict:
    """Fraction of episodes where series `a` beats series `b` on each metric.
    (a=PASSIVE_ENVELOPE_EXPOSURE_MATCHED, b=D_primary_rank1 in call sites
    below, but kept generic.)"""
    def frac(indices):
        if not indices:
            return None
        wins = {"total_return": 0, "max_drawdown": 0, "sharpe": 0, "sortino": 0}
        for i in indices:
            ae, be = a_episodes[i], b_episodes[i]
            if ae["total_return"] > be["total_return"]:
                wins["total_return"] += 1
            if ae["max_drawdown"] > be["max_drawdown"]:
                wins["max_drawdown"] += 1
            if ae["sharpe"] > be["sharpe"]:
                wins["sharpe"] += 1
            if ae["sortino"] > be["sortino"]:
                wins["sortino"] += 1
        return {k: v / len(indices) for k, v in wins.items()}

    all_idx = list(range(len(a_episodes)))
    stress_idx = [i for i in all_idx if i in STRESS_EPISODE_INDICES]
    nonstress_idx = [i for i in all_idx if i not in STRESS_EPISODE_INDICES]
    return {
        "all_12": frac(all_idx),
        "stress_2": frac(stress_idx),
        "non_stress_10": frac(nonstress_idx),
        "label": b_wins_label,
    }


def aggregate(episodes: list[dict], indices: list[int]) -> dict:
    sub = [episodes[i] for i in indices]
    if not sub:
        return {}
    return {
        "n_episodes": len(sub),
        "return_median": statistics.median(e["total_return"] for e in sub),
        "return_worst": min(e["total_return"] for e in sub),
        "return_best": max(e["total_return"] for e in sub),
        "max_drawdown_worst": min(e["max_drawdown"] for e in sub),
        "sharpe_median": statistics.median(e["sharpe"] for e in sub),
        "sortino_median": statistics.median(e["sortino"] for e in sub),
        "turnover_median": statistics.median(e["turnover"] for e in sub),
        "pct_halted": 100.0 * sum(bool(e["halted"]) for e in sub) / len(sub),
    }


def main():
    t0 = time.time()

    print("Loading verified DEVELOPMENT bundle...", flush=True)
    bundle = r.load_historical_bundle("development")
    print(f"Bundle loaded: dataset_revision={bundle.dataset_revision}", flush=True)

    # --- PASSIVE_ENVELOPE_EXPOSURE_MATCHED, via frozen unmodified evaluator
    print("Evaluating PASSIVE_ENVELOPE_EXPOSURE_MATCHED (target_exposure=0.23) "
          "via frozen harness.evaluate_generic_agent...", flush=True)
    official = harness.evaluate_generic_agent(
        bundle, lambda idx: passive_envelope_exposure_matched_decide_fn, **D_MATCHED_RISK,
    )
    matched_episodes = official["episode_metrics"]

    # --- Determinism rerun, from scratch --------------------------------
    print("Determinism rerun: full 12-episode PASSIVE_ENVELOPE_EXPOSURE_MATCHED, from scratch...", flush=True)
    bundle2 = r.load_historical_bundle("development")
    rerun = harness.evaluate_generic_agent(
        bundle2, lambda idx: passive_envelope_exposure_matched_decide_fn, **D_MATCHED_RISK,
    )["episode_metrics"]
    mismatches = []
    for a, b in zip(matched_episodes, rerun):
        for key in a:
            if key == "daily_returns":
                if a[key] != b[key]:
                    mismatches.append(("daily_returns", a["episode_index"]))
                continue
            av, bv = a[key], b[key]
            if isinstance(av, float):
                if av != bv:
                    mismatches.append((key, a["episode_index"], av, bv))
            elif av != bv:
                mismatches.append((key, a["episode_index"], av, bv))
    byte_identical = len(mismatches) == 0
    print(f"Determinism rerun byte-identical: {byte_identical}", flush=True)
    if not byte_identical:
        out = {
            "STOP": True,
            "reason": "unexplained nondeterminism in PASSIVE_ENVELOPE_EXPOSURE_MATCHED rerun",
            "mismatches": mismatches,
        }
        out_path = HERE / "STOP_nondeterminism.json"
        out_path.write_text(json.dumps(out, indent=2, default=str))
        print(f"STOP -- wrote {out_path}", flush=True)
        return

    # --- Load D_primary_rank1's already-recorded DEV results (not rerun) -
    print("Loading D_primary_rank1's already-recorded DEV results (not rerun)...", flush=True)
    recorded = json.loads((COMPARISON_DIR / "results.json").read_text())
    d1_episodes = recorded["results"]["D_primary_rank1"]["episode_metrics"]
    d1_genome_id = recorded["results"]["D_primary_rank1"].get("genome_id")

    # --- Load original 80%-exposure PASSIVE_ENVELOPE_CONTROL results -----
    # (reference only -- not rerun, not rewritten)
    print("Loading original PASSIVE_ENVELOPE_CONTROL (80% exposure) DEV results as reference...", flush=True)
    original_passive = json.loads((DEV_GAP_DIR / "passive_envelope_dev_results.json").read_text())
    original_passive_episodes = original_passive["passive_envelope_episode_metrics"]
    original_passive_summary = original_passive["passive_envelope_summary"]

    # --- Comparisons -------------------------------------------------------
    comparison_matched_vs_d1 = compare_pair(matched_episodes, d1_episodes, "fraction PASSIVE_ENVELOPE_EXPOSURE_MATCHED beats D_primary_rank1")

    all_idx = list(range(12))
    stress_idx = sorted(STRESS_EPISODE_INDICES)
    nonstress_idx = [i for i in all_idx if i not in STRESS_EPISODE_INDICES]

    out = {
        "predeclaration_file": str(HERE / "PREDECLARATION.md"),
        "dataset_revision": bundle.dataset_revision,
        "tbots_head": "9e573323501684ef8adfcbfbe357aa314cfdbdb4",
        "d_primary_rank1": {
            "genome_id": d1_genome_id or "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf",
            "gross_exposure_cap": D_TARGET_EXPOSURE,
            "source": "experiments/controls_comparison_20260921T064013Z/results.json (REUSED, NOT RERUN)",
            "episode_metrics": d1_episodes,
            "aggregate_all_12": aggregate(d1_episodes, all_idx),
            "aggregate_non_stress_10": aggregate(d1_episodes, nonstress_idx),
            "aggregate_stress_2": aggregate(d1_episodes, stress_idx),
        },
        "passive_envelope_exposure_matched": {
            "target_exposure": D_TARGET_EXPOSURE,
            "per_asset_weight": D_MATCHED_PER_ASSET,
            "episode_metrics": matched_episodes,
            "aggregate_all_12": aggregate(matched_episodes, all_idx),
            "aggregate_non_stress_10": aggregate(matched_episodes, nonstress_idx),
            "aggregate_stress_2": aggregate(matched_episodes, stress_idx),
            "determinism_rerun": {"byte_identical": byte_identical, "mismatches": mismatches},
        },
        "passive_envelope_original_80pct_REFERENCE_ONLY_NOT_RERUN": {
            "source": str(DEV_GAP_DIR / "passive_envelope_dev_results.json"),
            "summary_all_12_episodes": original_passive_summary,
        },
        "comparison_matched_vs_d1": comparison_matched_vs_d1,
        "stress_episode_indices": sorted(STRESS_EPISODE_INDICES),
        "note_on_episode_independence": (
            "The 12 DEVELOPMENT episodes are contiguous, non-overlapping, "
            "back-to-back annual slices of a single 2007-2018 historical "
            "path -- NOT 12 statistically independent samples. Every "
            "episode-count-based fraction in this file describes one "
            "correlated historical realization examined at up to 12 "
            "checkpoints (2 in the stress subset, 10 in the non-stress "
            "subset), not independent trials."
        ),
        "known_limitation_dividend_accounting": (
            "The frozen execution.py simulator marks daily equity using "
            "each session's RAW (unadjusted) close and credits no dividend "
            "cash on ex-dividend dates anywhere in Portfolio. This defect "
            "is preserved UNCHANGED in this experiment for comparability "
            "with all prior results, per explicit GO instruction (Sec 7: "
            "'preserve the existing dividend-accounting behavior unchanged "
            "...  Do not repair it under this GO'). It silently "
            "understates total return for D_primary_rank1, for "
            "PASSIVE_ENVELOPE_EXPOSURE_MATCHED, and for the original "
            "80%-exposure PASSIVE_ENVELOPE_CONTROL alike -- all three hold "
            "dividend-paying universe assets (the exposure-matched and "
            "original passive controls hold all 8; D_primary_rank1 rotates "
            "among up to 3 of the 8 depending on regime). Not fixed, not "
            "quantified in aggregate here."
        ),
    }

    out_path = HERE / "exposure_isolation_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, sha256={out_hash})", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
