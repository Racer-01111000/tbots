#!/usr/bin/env python3
"""GO -- QUALIFICATION HARNESS EQUIVALENCE VALIDATION (2026-09-23).

Validates the MEASUREMENT INSTRUMENT built for the D1 QUALIFICATION-lane
experiment (experiments/d1_qualification_generalization_20260923T054829Z/
run_qualification_generalization.py), not any trading hypothesis.

Method: import that script's own simulation functions
(_simulate_d1_episode, _simulate_passive_matched_episode) BY REFERENCE
(importlib, no source bytes copied or edited), monkeypatch only the
module-global QUAL_LANE_END from "2022-12-31" to "2018-12-31" at runtime
(the functions read retention_end_date via that free variable; this is
the one parameter that differs between a QUALIFICATION run and a
DEVELOPMENT run, and monkeypatching a module attribute at call time does
not alter the file on disk), then invoke the exact same functions against
the DEVELOPMENT bundle and DEVELOPMENT's own pre-existing 12-episode
EPISODE_PROTOCOL (s5a_config.py) instead of QUALIFICATION's 4-episode one.

Compares the result against:
  - D_primary_rank1: controls_comparison_20260921T064013Z/results.json
    ["results"]["D_primary_rank1"]["episode_metrics"] -- this is the
    actual FROZEN s6b_evaluator.py's own original DEV output (D is a
    real B-G lineage code, evaluated through s6b_evaluator.evaluate_genome
    in the very first controls-comparison GO).
  - PASSIVE_ENVELOPE_EXPOSURE_MATCHED: d_exposure_isolation_20260923T052542Z/
    exposure_isolation_results.json["passive_envelope_exposure_matched"]
    ["episode_metrics"] -- produced by harness.evaluate_generic_agent (the
    established parallel DB-free loop this same experiment series already
    uses for non-genome/generic agents, built from the same primitives in
    the same order as s6b_evaluator._simulate_admitted_episode).

Writes only into this directory. Modifies no frozen file, no existing
results file, no QUALIFICATION result.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUAL_DIR = ROOT / "experiments" / "d1_qualification_generalization_20260923T054829Z"
COMPARISON_DIR = ROOT / "experiments" / "controls_comparison_20260921T064013Z"
ISOLATION_DIR = ROOT / "experiments" / "d_exposure_isolation_20260923T052542Z"

sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import s5a_config as s5a  # noqa: E402  (frozen, unmodified -- DEV's own protocol)
import s6a_runtime as r  # noqa: E402  (frozen, unmodified)

# --- import the script under test BY REFERENCE (no bytes copied/edited) ----
SCRIPT_PATH = QUAL_DIR / "run_qualification_generalization.py"
spec = importlib.util.spec_from_file_location("qual_script_under_test", SCRIPT_PATH)
qual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qual)  # this executes the script's module-level code,
# which itself just loads D1's genome from its frozen source file and defines
# functions/constants -- it does NOT run main() (guarded by __name__ == "__main__")

SCRIPT_SHA256 = hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()

DEV_EPISODES = s5a.EPISODE_PROTOCOL["episodes"]
assert len(DEV_EPISODES) == 12
DEV_END = "2018-12-31"

FIELDS_TO_COMPARE = [
    "total_return", "max_drawdown", "sharpe", "sortino", "turnover",
    "commission_cents", "slippage_cents", "transaction_cost_cents",
    "transaction_cost_rate", "order_count", "fill_count", "step_count",
    "halted", "final_equity_cents", "start_date", "end_date",
]


def run_dev_with_new_harness():
    """Monkeypatch QUAL_LANE_END -> DEV_END for this call only, run the
    script's own unmodified simulation functions against the DEVELOPMENT
    bundle/episodes. Restores the original value afterward regardless."""
    original_lane_end = qual.QUAL_LANE_END
    try:
        qual.QUAL_LANE_END = DEV_END
        bundle = r.load_historical_bundle("development")
        d1_out = [qual._simulate_d1_episode(bundle, qual.D1_GENOME, ep) for ep in DEV_EPISODES]
        pm_out = [qual._simulate_passive_matched_episode(bundle, ep) for ep in DEV_EPISODES]
        return bundle.dataset_revision, d1_out, pm_out
    finally:
        qual.QUAL_LANE_END = original_lane_end


def build_transaction_cost_cents(episode_metrics_list):
    """The frozen-evaluator ground-truth files already carry
    transaction_cost_cents directly; the new harness's episode dict does
    not (it only has commission_cents + slippage_cents separately). Derive
    it for the new-harness side so the field is comparable either way."""
    out = []
    for e in episode_metrics_list:
        e = dict(e)
        e["transaction_cost_cents"] = e["commission_cents"] + e["slippage_cents"]
        out.append(e)
    return out


def compare_episode(new_e, truth_e, label, idx, mismatches):
    for field in FIELDS_TO_COMPARE:
        nv, tv = new_e.get(field), truth_e.get(field)
        if isinstance(nv, float) and isinstance(tv, float):
            exact = nv == tv
        else:
            exact = nv == tv
        if not exact:
            mismatches.append({
                "agent": label, "episode_index": idx, "field": field,
                "new_harness_value": nv, "frozen_ground_truth_value": tv,
                "abs_diff": (abs(nv - tv) if isinstance(nv, (int, float)) and isinstance(tv, (int, float)) else None),
            })

    # daily_returns: compare full series length + exact float equality per element
    new_dr = new_e.get("daily_returns")
    truth_dr = truth_e.get("daily_returns")
    if new_dr is not None and truth_dr is not None:
        if len(new_dr) != len(truth_dr):
            mismatches.append({
                "agent": label, "episode_index": idx, "field": "daily_returns_length",
                "new_harness_value": len(new_dr), "frozen_ground_truth_value": len(truth_dr),
            })
        else:
            n_diff = sum(1 for a, b in zip(new_dr, truth_dr) if a != b)
            if n_diff:
                first_i = next(i for i, (a, b) in enumerate(zip(new_dr, truth_dr)) if a != b)
                mismatches.append({
                    "agent": label, "episode_index": idx, "field": "daily_returns",
                    "n_elements_differing": n_diff, "of_total": len(new_dr),
                    "first_differing_index": first_i,
                    "new_harness_value_at_first_diff": new_dr[first_i],
                    "frozen_ground_truth_value_at_first_diff": truth_dr[first_i],
                })
    elif new_dr is None or truth_dr is None:
        mismatches.append({
            "agent": label, "episode_index": idx, "field": "daily_returns_presence",
            "new_harness_has_it": new_dr is not None, "ground_truth_has_it": truth_dr is not None,
        })


def main():
    t0 = time.time()
    here = Path(__file__).resolve().parent

    print("=== Step 1: state/hashes ===", flush=True)
    tbots_head = "9e573323501684ef8adfcbfbe357aa314cfdbdb4"  # recorded by parent before this fork ran
    evaluator_sha = "d306f6236149c6445e8ca21ccaae4d03339708284db547f8179d533fd9fae048"
    comparison_results_sha = hashlib.sha256((COMPARISON_DIR / "results.json").read_bytes()).hexdigest()
    isolation_results_sha = hashlib.sha256((ISOLATION_DIR / "exposure_isolation_results.json").read_bytes()).hexdigest()
    qual_results_sha = hashlib.sha256((QUAL_DIR / "qualification_generalization_results.json").read_bytes()).hexdigest()
    print(f"script under test sha256: {SCRIPT_SHA256}")
    print(f"frozen evaluator (s6b_evaluator.py) sha256: {evaluator_sha}")
    print(f"controls_comparison results.json sha256: {comparison_results_sha}")
    print(f"exposure_isolation_results.json sha256: {isolation_results_sha}")
    print(f"qualification_generalization_results.json sha256 (untouched, read-only reference): {qual_results_sha}")

    print("=== Step 3: run new harness's own functions against DEVELOPMENT ===", flush=True)
    dataset_rev_1, d1_run1, pm_run1 = run_dev_with_new_harness()
    print(f"dataset_revision observed: {dataset_rev_1}")

    print("=== Step 6: determinism -- new harness vs itself, second run ===", flush=True)
    dataset_rev_2, d1_run2, pm_run2 = run_dev_with_new_harness()
    self_mismatches = []
    for label, first, second in (("D1", d1_run1, d1_run2), ("PASSIVE_MATCHED", pm_run1, pm_run2)):
        for i, (a, b) in enumerate(zip(first, second)):
            for k in a:
                if a[k] != b[k]:
                    self_mismatches.append({"agent": label, "episode_index": i, "field": k})
    self_determinism_ok = len(self_mismatches) == 0
    print(f"new-harness self-determinism byte-identical: {self_determinism_ok}")
    if not self_determinism_ok:
        out = {
            "VERDICT": "FAIL",
            "reason": "unexplained nondeterminism in new harness's own two DEV runs -- automatic FAIL per GO step 6",
            "self_mismatches": self_mismatches,
        }
        (here / "equivalence_results.json").write_text(json.dumps(out, indent=2, default=str))
        print("Wrote FAIL result (self-nondeterminism). Stopping.", flush=True)
        return

    print("=== Step 4: load ground truth and compare ===", flush=True)
    comparison_data = json.loads((COMPARISON_DIR / "results.json").read_text())
    d1_truth = comparison_data["results"]["D_primary_rank1"]["episode_metrics"]

    isolation_data = json.loads((ISOLATION_DIR / "exposure_isolation_results.json").read_text())
    pm_truth = isolation_data["passive_envelope_exposure_matched"]["episode_metrics"]

    assert len(d1_truth) == 12 and len(pm_truth) == 12 and len(d1_run1) == 12 and len(pm_run1) == 12

    d1_new_with_tcc = build_transaction_cost_cents(d1_run1)
    pm_new_with_tcc = build_transaction_cost_cents(pm_run1)

    mismatches = []
    for idx in range(12):
        compare_episode(d1_new_with_tcc[idx], d1_truth[idx], "D1", idx, mismatches)
        compare_episode(pm_new_with_tcc[idx], pm_truth[idx], "PASSIVE_MATCHED", idx, mismatches)

    n_mismatches = len(mismatches)
    print(f"Total field/series mismatches vs frozen ground truth: {n_mismatches}")

    if n_mismatches == 0:
        verdict = "PASS"
    else:
        verdict = "FAIL"

    out = {
        "VERDICT": verdict,
        "tbots_head": tbots_head,
        "identities_compared": {
            "script_under_test": str(SCRIPT_PATH),
            "script_under_test_sha256": SCRIPT_SHA256,
            "frozen_evaluator_s6b_evaluator_py_sha256": evaluator_sha,
            "d1_ground_truth_source": str(COMPARISON_DIR / "results.json"),
            "d1_ground_truth_source_sha256": comparison_results_sha,
            "d1_ground_truth_producer": "frozen s6b_evaluator.py / harness.py, original controls-comparison GO run",
            "passive_matched_ground_truth_source": str(ISOLATION_DIR / "exposure_isolation_results.json"),
            "passive_matched_ground_truth_source_sha256": isolation_results_sha,
            "passive_matched_ground_truth_producer": "harness.evaluate_generic_agent (parallel frozen DB-free loop), prior exposure-isolation GO run",
            "qualification_result_untouched_sha256": qual_results_sha,
            "d1_genome_id": qual.D1_GENOME_ID,
            "d1_source_file": str(qual.D1_SOURCE_FILE),
            "d1_source_file_sha256": qual.D1_SOURCE_SHA256,
            "passive_target_exposure": qual.D_TARGET_EXPOSURE,
        },
        "dataset_revision_observed": dataset_rev_1,
        "comparison_coverage": {
            "episodes_compared": 12,
            "fields_compared_per_episode": FIELDS_TO_COMPARE + ["daily_returns (full per-day series, exact float equality)"],
            "equality_standard": "exact equality (== ), not tolerance-based, for every field including floats",
            "not_available_for_comparison": (
                "orders/fills/position-transitions/risk-state-transition event logs are not persisted in "
                "either ground-truth results.json file (only aggregate order_count/fill_count survive) -- "
                "daily_returns (implying the full daily equity path via cumulative product) and final_equity_cents "
                "are the finest-grained state actually available in both ground-truth artifacts, and both were compared."
            ),
        },
        "mismatch_count": n_mismatches,
        "first_mismatch": mismatches[0] if mismatches else None,
        "all_mismatches": mismatches,
        "self_determinism": {"byte_identical": self_determinism_ok, "mismatches": self_mismatches},
        "qualification_result_supported_by_equivalence_validated_path": (
            verdict == "PASS"
        ),
        "known_limitation_dividend_accounting": (
            "Not repaired here, per GO instruction. Both the new harness and the frozen ground truth "
            "share the same execution.py Portfolio (raw-close marking, no dividend credit) -- this "
            "experiment does not change or evaluate that behavior, only confirms both harnesses treat "
            "it identically (which the field-level equality check above establishes if VERDICT is PASS)."
        ),
    }
    out_path = here / "equivalence_results.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"VERDICT: {verdict}", flush=True)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, sha256={out_hash})", flush=True)
    print(f"Total wall time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
