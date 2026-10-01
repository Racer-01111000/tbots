"""Finish outstanding items for TBOTS_SAM_15_MONTH_REPLAY_AND_LIVE_SHADOW_20261001:
native/5bps full-matrix for all 25 candidates, a determinism check of the
ranking computation, a data-layer reproduction check (row counts/date
coverage vs data_v2/MANIFEST.json -- NOT a byte-diff of the original rows,
which were never persisted to disk), and an actual selected-runner check
that executes the staged development bot's real decision pathway.

Read-only with respect to all frozen/accepted artifacts. Writes only new
files under experiments/sam_15_month_replay_20261001/data_v2/. No network
calls except the same public Yahoo Finance chart endpoint already used by
replay_harness.py. No credentials touched, no brokerage calls, no orders.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path("/home/ec2-user/fitness_v2_authoritative_transfer_20261001/tbots")
EXP = REPO / "experiments/sam_15_month_replay_20261001"
sys.path.insert(0, str(EXP))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts/lib"))

import s6b_evaluator as s6b  # noqa: E402
from lib.ids import genome_id  # noqa: E402
from lib.normalize import normalize_yahoo_chart  # noqa: E402
from replay_harness import (  # noqa: E402
    UNIVERSE, fetch_gap, simulate, champion_decide_fn_factory,
    make_passive_decide_fn, load_sam_genome, load_champion_genome,
    SAM_GENOME_ID, CHAMPION_GENOME_ID,
)
from replay_harness_bc import simulate_code  # noqa: E402

OUT = EXP / "data_v2"
STARTING_CASH_CENTS = 100_000_000
WINDOWS = {
    "primary_15mo": ("2025-07-01", "2026-09-30"),
    "supplementary_21mo": ("2025-01-01", "2026-09-30"),
}


def log(msg):
    print(f"[{time.time()-T0:7.1f}s] {msg}", flush=True)


def fresh_fetch():
    ingested_at = "repro-check-fixed-marker"
    merged = {}
    row_report = {}
    for symbol in UNIVERSE:
        raw, url = fetch_gap(symbol, "1990-01-01", "2026-09-30")
        rows, report = normalize_yahoo_chart(raw, symbol, "repro_check_20261001", url, ingested_at)
        merged[symbol] = rows
        row_report[symbol] = {
            "raw_row_count": report["raw_row_count"],
            "output_row_count": report["output_row_count"],
            "start": report["start"],
            "end": report["end"],
        }
    return merged, row_report


def data_layer_reproduction_check(row_report):
    manifest = json.loads((OUT / "MANIFEST.json").read_text())
    diffs = {}
    for symbol, fresh in row_report.items():
        orig = manifest["symbols"].get(symbol, {})
        mismatch = {
            k: {"original": orig.get(k), "fresh": fresh.get(k)}
            for k in ("raw_row_count", "output_row_count", "start", "end")
            if orig.get(k) != fresh.get(k)
        }
        if mismatch:
            diffs[symbol] = mismatch
    return {
        "method": "row_count_and_date_coverage_vs_manifest",
        "limitation": (
            "data_v2's original per-symbol normalized rows were never persisted to "
            "disk (only MANIFEST.json's counts/hashes and the derived ranking/rerun "
            "outputs survive) -- a byte-exact diff of price/volume content against "
            "the original fetch is not possible this gate. This check instead "
            "confirms row counts and date coverage match exactly, which rules out "
            "any new/missing trading day or truncated fetch."
        ),
        "symbols_checked": len(row_report),
        "mismatches": diffs,
        "reproduced": len(diffs) == 0,
    }


def load_candidate_inventory():
    d = json.loads((REPO / "experiments/controls_comparison_20260921T064013Z/results.json").read_text())
    seen = {}
    for agent_id, entry in d["results"].items():
        meta = entry["agent_meta"]
        if "genome" not in meta:
            continue
        genome = meta["genome"]
        gid = genome_id(genome)
        assert gid == meta["genome_id"], f"genome_id mismatch for {agent_id}"
        if gid == CHAMPION_GENOME_ID:
            lineage = "champion"
        else:
            lineage = meta["lineage"]
        if gid not in seen:
            seen[gid] = {"genome_id": gid, "lineage": lineage, "genome": genome, "aliases": []}
        seen[gid]["aliases"].append(agent_id)
    candidates = list(seen.values())
    assert len(candidates) == 25, f"expected 25 unique candidates, got {len(candidates)}"
    return candidates


def run_candidate(cand, merged, slippage_bps):
    gid, lineage, genome = cand["genome_id"], cand["lineage"], cand["genome"]
    out = {"genome_id": gid, "lineage": lineage, "aliases": cand["aliases"], "windows": {}}
    try:
        if lineage == "champion":
            native_aw, native_te = genome["max_asset_weight"], genome["target_max_exposure"]
        else:
            native_aw, native_te = s6b.risk_bounds(lineage, genome)

        for wname, (ws, we) in WINDOWS.items():
            if lineage == "champion":
                run = simulate(
                    champion_decide_fn_factory(genome), per_symbol_rows=merged,
                    window_start=ws, window_end=we,
                    max_asset_weight=native_aw, max_total_exposure=native_te,
                    starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps,
                )
            else:
                run = simulate_code(
                    lineage, genome, per_symbol_rows=merged, window_start=ws, window_end=we,
                    starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps,
                )
            passive = simulate(
                make_passive_decide_fn(native_te), per_symbol_rows=merged,
                window_start=ws, window_end=we,
                max_asset_weight=native_te / len(UNIVERSE) + 1e-6, max_total_exposure=native_te,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps,
            )
            out["windows"][wname] = {
                "total_return": run["total_return"],
                "annualized": run["total_return"] * (252 / run["step_count"]) if run["step_count"] else None,
                "excess_vs_native_passive": run["total_return"] - passive["total_return"],
                "native_passive_exposure": native_te,
                "max_drawdown": run["max_drawdown"],
                "sharpe": run["sharpe"],
                "halted": run["halted"],
                "step_count": run["step_count"],
            }
        finite = all(
            all(isinstance(v, (int, float)) and v == v and abs(v) != float("inf")
                for k, v in w.items() if k in ("total_return", "max_drawdown", "sharpe"))
            for w in out["windows"].values()
        )
        halted_any = any(w["halted"] for w in out["windows"].values())
        out["eligible"] = finite and not halted_any
        out["ineligible_reason"] = None if out["eligible"] else ("halted" if halted_any else "nonfinite_metric")
    except Exception as e:
        out["eligible"] = False
        out["ineligible_reason"] = f"exception: {type(e).__name__}: {e}"
    return out


def rank(results):
    scored = []
    for r in results:
        if not r["eligible"]:
            continue
        primary = r["windows"]["primary_15mo"]["excess_vs_native_passive"]
        supp = r["windows"]["supplementary_21mo"]["excess_vs_native_passive"]
        score = min(primary, supp)
        worst_dd = min(r["windows"][w]["max_drawdown"] for w in r["windows"])
        min_sharpe = min(r["windows"][w]["sharpe"] for w in r["windows"])
        scored.append((score, -abs(worst_dd), min_sharpe, r["genome_id"], r))
    scored.sort(key=lambda t: (-t[0], t[1] * -1, -t[2], t[3]))
    return [
        {"rank": i + 1, "genome_id": r["genome_id"], "lineage": r["lineage"], "aliases": r["aliases"],
         "score": s, "primary_excess": r["windows"]["primary_15mo"]["excess_vs_native_passive"],
         "supp_excess": r["windows"]["supplementary_21mo"]["excess_vs_native_passive"]}
        for i, (s, _, _, _, r) in enumerate(scored)
    ]


def determinism_check(candidates, merged, slippage_bps):
    run1 = [run_candidate(c, merged, slippage_bps) for c in candidates]
    run2 = [run_candidate(c, merged, slippage_bps) for c in candidates]
    mismatches = []
    for a, b in zip(run1, run2):
        assert a["genome_id"] == b["genome_id"]
        for wname in WINDOWS:
            wa, wb = a["windows"].get(wname, {}), b["windows"].get(wname, {})
            for k in ("total_return", "max_drawdown", "sharpe", "step_count"):
                if wa.get(k) != wb.get(k):
                    mismatches.append({"genome_id": a["genome_id"], "window": wname, "field": k,
                                        "run1": wa.get(k), "run2": wb.get(k)})
    return run1, {"mismatches": mismatches, "deterministic": len(mismatches) == 0, "candidates_checked": len(run1)}


def selected_runner_check(merged):
    sel = json.loads((REPO / "experiments/sam_dev_staging_20261001/selected_development_bot.json").read_text())
    genome = sel["selected_genome_definition"]
    gid = genome_id(genome)
    champ_genome = load_champion_genome()
    expected_gid = genome_id(champ_genome)
    identity_ok = gid == expected_gid == CHAMPION_GENOME_ID

    ws, we = WINDOWS["primary_15mo"]
    run = simulate(
        champion_decide_fn_factory(genome), per_symbol_rows=merged,
        window_start=ws, window_end=we,
        max_asset_weight=0.18, max_total_exposure=0.18,
        starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=10, ceiling=0.18,
    )
    orig = json.loads((OUT / "ranking_final.json").read_text())
    champ_row = next(r for r in orig["full_ranking"] if r["genome_id"] == CHAMPION_GENOME_ID)
    return {
        "staged_genome_id": gid,
        "matches_champion_id": identity_ok,
        "executed_via": "control_agent.decide through champion_decide_fn_factory (same pathway named in selected_development_bot.json)",
        "fresh_data_total_return_18pct_10bps_primary": run["total_return"],
        "original_ranking_primary_excess_for_reference": champ_row["primary_excess"],
        "ran_without_exception": True,
        "order_submission_attempted": False,
    }




def run_candidate_ceiling18(cand, merged, slippage_bps):
    """Same methodology as the accepted ranking_final.json (shared 18pct
    ceiling, 18pct-matched passive) -- only slippage differs. This is the
    properly apples-to-apples sensitivity test the GO's own text asks for
    ("does the ordering change at 5bps?"), unlike the native-sizing matrix
    above which compares each candidate against a passive sized to its own
    NOMINAL cap -- unfair to any max_positions-constrained candidate (e.g.
    the champion's nominal 89pct cap vs its realized ~18pct single-position
    exposure), the same nominal-vs-effective trap this GO already caught
    once in the Sam-vs-champion framing."""
    gid, lineage, genome = cand["genome_id"], cand["lineage"], cand["genome"]
    out = {"genome_id": gid, "lineage": lineage, "aliases": cand["aliases"], "windows": {}}
    try:
        for wname, (ws, we) in WINDOWS.items():
            if lineage == "champion":
                run = simulate(
                    champion_decide_fn_factory(genome), per_symbol_rows=merged,
                    window_start=ws, window_end=we,
                    max_asset_weight=0.18, max_total_exposure=0.18,
                    starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps, ceiling=0.18,
                )
            else:
                run = simulate_code(
                    lineage, genome, per_symbol_rows=merged, window_start=ws, window_end=we,
                    starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps, ceiling=0.18,
                )
            passive = simulate(
                make_passive_decide_fn(0.18), per_symbol_rows=merged,
                window_start=ws, window_end=we,
                max_asset_weight=0.18 / len(UNIVERSE) + 1e-6, max_total_exposure=0.18,
                starting_cash_cents=STARTING_CASH_CENTS, slippage_bps=slippage_bps,
            )
            out["windows"][wname] = {
                "total_return": run["total_return"],
                "excess_vs_passive18": run["total_return"] - passive["total_return"],
                "max_drawdown": run["max_drawdown"],
                "sharpe": run["sharpe"],
                "halted": run["halted"],
                "step_count": run["step_count"],
            }
        finite = all(
            all(isinstance(v, (int, float)) and v == v and abs(v) != float("inf")
                for k, v in w.items() if k in ("total_return", "max_drawdown", "sharpe"))
            for w in out["windows"].values()
        )
        halted_any = any(w["halted"] for w in out["windows"].values())
        out["eligible"] = finite and not halted_any
        out["ineligible_reason"] = None if out["eligible"] else ("halted" if halted_any else "nonfinite_metric")
    except Exception as e:
        out["eligible"] = False
        out["ineligible_reason"] = f"exception: {type(e).__name__}: {e}"
    return out


def rank_ceiling18(results):
    scored = []
    for r in results:
        if not r["eligible"]:
            continue
        primary = r["windows"]["primary_15mo"]["excess_vs_passive18"]
        supp = r["windows"]["supplementary_21mo"]["excess_vs_passive18"]
        score = min(primary, supp)
        worst_dd = min(r["windows"][w]["max_drawdown"] for w in r["windows"])
        min_sharpe = min(r["windows"][w]["sharpe"] for w in r["windows"])
        scored.append((score, -abs(worst_dd), min_sharpe, r["genome_id"], r))
    scored.sort(key=lambda t: (-t[0], t[1] * -1, -t[2], t[3]))
    return [
        {"rank": i + 1, "genome_id": r["genome_id"], "lineage": r["lineage"], "aliases": r["aliases"],
         "score": s, "primary_excess": r["windows"]["primary_15mo"]["excess_vs_passive18"],
         "supp_excess": r["windows"]["supplementary_21mo"]["excess_vs_passive18"]}
        for i, (s, _, _, _, r) in enumerate(scored)
    ]


def main():
    global T0
    T0 = time.time()

    log("Fetching fresh single-coherent dataset (1990-01-01..2026-09-30)...")
    merged, row_report = fresh_fetch()
    log("Fetch complete.")

    repro = data_layer_reproduction_check(row_report)
    log(f"Data-layer reproduction: reproduced={repro['reproduced']} mismatches={len(repro['mismatches'])}")

    candidates = load_candidate_inventory()
    log(f"Loaded {len(candidates)} candidates.")

    log("Running native/5bps full matrix (pass 1 of determinism check)...")
    native5bps_results, determinism = determinism_check(candidates, merged, 5)
    log(f"Determinism check: deterministic={determinism['deterministic']} mismatches={len(determinism['mismatches'])}")

    native5bps_ranking = rank(native5bps_results)
    log(f"Native/5bps winner: {native5bps_ranking[0]['genome_id']} ({native5bps_ranking[0]['lineage']})")

    log("Running selected-runner check...")
    runner_check = selected_runner_check(merged)
    log(f"Selected-runner check: matches_champion_id={runner_check['matches_champion_id']}")

    log("Running apples-to-apples 18pct-ceiling/5bps sensitivity (same methodology as accepted ranking, slippage only changed)...")
    ceiling18_5bps_results = [run_candidate_ceiling18(c, merged, 5) for c in candidates]
    ceiling18_5bps_ranking = rank_ceiling18(ceiling18_5bps_results)
    log(f"18pct-ceiling/5bps winner: {ceiling18_5bps_ranking[0]['genome_id']} ({ceiling18_5bps_ranking[0]['lineage']})")

    out = {
        "go": "TBOTS_SAM_15_MONTH_REPLAY_AND_LIVE_SHADOW_20261001 -- finish outstanding items",
        "ceiling18_5bps_sensitivity_full_matrix": ceiling18_5bps_results,
        "ceiling18_5bps_sensitivity_ranking": ceiling18_5bps_ranking,
        "ceiling18_5bps_winner_is_champion": ceiling18_5bps_ranking[0]["genome_id"] == CHAMPION_GENOME_ID,
        "ordering_changes_at_5bps_same_18pct_ceiling": ceiling18_5bps_ranking[0]["genome_id"] != CHAMPION_GENOME_ID,
        "native_sizing_caveat": (
            "The native_5bps_full_matrix/native_5bps_ranking fields above compare each "
            "candidate against a passive benchmark matched to its own NOMINAL exposure "
            "cap. This is unfair to any max_positions-constrained candidate -- the "
            "champion's nominal native cap is 89pct but max_positions=1 means it never "
            "realizes more than one position's weight in practice, so the 89pct-exposure "
            "passive it is compared against is not the benchmark it actually competes "
            "against day to day. This is the same nominal-vs-effective sizing mismatch "
            "this GO already caught once (the original Sam-beats-champion framing bug). "
            "The ceiling18_5bps_sensitivity fields are the properly apples-to-apples test: "
            "identical 18pct-ceiling-matched-passive methodology as the accepted "
            "ranking_final.json, slippage alone dropped from 10bps to 5bps."
        ),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_layer_reproduction_check": repro,
        "determinism_check": determinism,
        "native_5bps_full_matrix": native5bps_results,
        "native_5bps_ranking": native5bps_ranking,
        "native_5bps_winner_is_champion": native5bps_ranking[0]["genome_id"] == CHAMPION_GENOME_ID,
        "ordering_changes_at_5bps_native_vs_10bps_18pct": native5bps_ranking[0]["genome_id"] != CHAMPION_GENOME_ID,
        "selected_runner_check": runner_check,
    }
    out_path = OUT / "native_5bps_and_reproduction_20261001.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    log(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
    log(f"Total wall time: {time.time()-T0:.1f}s")


if __name__ == "__main__":
    main()

