"""V4 generator admission checks. (1) Length-matched statistic validation against the FROZEN targets (configs/calibration_targets_v4.json);
(2) world-structure checks; (3) family-condition exercise; (4) execution-stress effectiveness. The original v1 tail/path criteria are re-used UNCHANGED
(validation.tail_checks). Nothing here reads a training/validation/sealed world: callers pass engineering worlds."""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import calibration as C
from . import engine as E
from . import stats as st
from . import validation as V0
from .world import SYMBOLS, check_invariants

TARGETS = Path(__file__).resolve().parent.parent / "configs" / "calibration_targets_v4.json"
REL_TOL, ABS_TOL = 0.35, 0.25      # unchanged from v1
# family -> (human description of the declared adverse condition, predicate over the hidden factor paths)
FAMILY_CONDITION = {
    "energy_shipping_squeeze": ("oil or diesel factor peak >= 0.15", lambda f: max(max(f["oil"]), max(f["diesel"])) >= 0.15),
    "fertilizer_harvest_stress": ("fertilizer factor peak >= 0.10 or crop trough <= -0.10", lambda f: max(f["fertilizer"]) >= 0.10 or min(f["crop"]) <= -0.10),
    "stagflation_rates": ("inflation factor peak >= 0.08 or 10y-yield factor peak >= 0.008", lambda f: max(f["inflation"]) >= 0.08 or max(f["yield"]) >= 0.008),
    "recession_credit_shock": ("credit factor peak >= 0.20 or growth trough <= -0.05", lambda f: max(f["credit"]) >= 0.20 or min(f["growth"]) <= -0.05),
    "technology_boom_reversal": ("|tech factor| peak >= 0.20", lambda f: max(abs(x) for x in f["tech"]) >= 0.20),
    "normalization_whipsaw": ("a growth excursion >= 0.02 that reverses to < 30% of its peak within 120 sessions", None),
}
FAMILY_MIN_SHARE = 0.90


def window_validate(worlds: list, targets: dict | None = None) -> dict:
    T = targets or json.loads(TARGETS.read_text()); L = T["window_sessions"]
    sm = []
    for w in worlds:
        r = V0.world_returns(w); sm.append(C.summarize_series({s: x[-L:] for s, x in r.items()}))
    rows, fails = [], []
    for s in SYMBOLS:
        for k in V0.ASSET_STATS:
            ref = T["assets"][s][k]["median"]; vals = [x["asset"][s][k] for x in sm]
            lo, med, hi = st.quantile(vals, .05), st.quantile(vals, .5), st.quantile(vals, .95)
            tol = ABS_TOL if k in ("ac1_ret", "skew", "ac1_abs") else REL_TOL * max(abs(ref), 1e-9)
            ok = bool(lo <= ref <= hi or abs(med - ref) <= tol)
            rows.append({"symbol": s, "stat": k, "hist_window_median": ref, "synth_p05": lo, "synth_median": med, "synth_p95": hi, "pass": ok})
            if not ok: fails.append(f"{s}.{k}")
    for pair, d in T["corr"].items():
        vals = [x["corr"][pair] for x in sm]; lo, med, hi = st.quantile(vals, .05), st.quantile(vals, .5), st.quantile(vals, .95)
        ok = bool(lo <= d["median"] <= hi or abs(med - d["median"]) <= ABS_TOL); rows.append({"pair": pair, "hist_window_median": d["median"], "synth_median": med, "pass": ok})
        if not ok: fails.append(f"corr {pair}")
    return {"window_sessions": L, "n_worlds": len(worlds), "n_stats": len(rows), "n_pass": sum(r["pass"] for r in rows), "failures": fails, "rows": rows,
            "power_note": "history supplies %d overlapping windows (~2 independent); the check is a plausibility screen, not a goodness-of-fit test" % T["n_windows"]}


def structure_checks(worlds: list) -> dict:
    bad = []
    for w in worlds:
        for v in check_invariants(w): bad.append((w.world_id, v))
        if any(d.weekday() >= 5 for d in w.dates): bad.append((w.world_id, "weekend session"))
        if len(set(w.dates)) != len(w.dates): bad.append((w.world_id, "duplicate dates"))
        for s in SYMBOLS:
            a = w.adjuster.history(s, w.n - 1, w.n)
            if any((not math.isfinite(x)) or x <= 0 for x in a): bad.append((w.world_id, f"{s} adjusted non-finite/non-positive"))
            else:
                big = max(abs(math.log(a[i] / a[i - 1])) for i in range(1, len(a)))
                if big > 0.40: bad.append((w.world_id, f"{s} adjusted one-day move {big:.2f} (> 0.40)"))
        for x in w.actions:
            if x.kind == "split":
                i = x.ex_idx; r = math.log(w.close[x.symbol][i] / w.close[x.symbol][i - 1]) - math.log(x.den / x.num)
                if abs(r) > 0.45: bad.append((w.world_id, f"split {x.symbol}@{i}: raw price step inconsistent with ratio ({r:.2f})"))
            if x.kind == "dividend" and (x.pay_idx - x.ex_idx) > 40: bad.append((w.world_id, f"dividend pay lag {x.pay_idx - x.ex_idx} > 40"))
    return {"n_worlds": len(worlds), "violations": bad[:50], "n_violations": len(bad), "pass": not bad}


def family_exercise(worlds: list) -> dict:
    by = {}
    for w in worlds:
        f = w.hidden.factors; ok = False
        if w.family == "normalization_whipsaw":
            g = f["growth"]
            for i in range(0, len(g) - 1):
                pk = max(range(i, min(len(g), i + 120)), key=lambda j: abs(g[j]))
                if abs(g[pk]) >= 0.02 and min(abs(g[j]) for j in range(pk, min(len(g), pk + 120))) < 0.30 * abs(g[pk]): ok = True; break
        else:
            ok = bool(FAMILY_CONDITION[w.family][1](f))
        by.setdefault(w.family, []).append(ok)
    kinds = {}
    for w in worlds: kinds.setdefault(w.family, set()).update(e["kind"] for e in w.hidden.events)
    rows = {fam: {"condition": FAMILY_CONDITION[fam][0], "share_exercised": sum(v) / len(v), "n": len(v), "pass": sum(v) / len(v) >= FAMILY_MIN_SHARE} for fam, v in by.items()}
    distinct = len({frozenset(k) for k in kinds.values()}) == len(kinds)
    return {"rows": rows, "min_share": FAMILY_MIN_SHARE, "event_kind_sets_distinct_across_families": distinct, "pass": all(r["pass"] for r in rows.values()) and distinct}


def stress_effective(worlds: list, strategy, evaluator_stresses) -> dict:
    out = {}
    for ov in evaluator_stresses:
        tot = {"partial_fills": 0, "rejected": 0, "stale_skips": 0, "outage_sessions": 0, "halted_symbol_blocks": 0, "deferred": 0, "violations": 0}
        for w in worlds:
            r = E.run(w, strategy, E.EngineParams(overlay=ov))
            for k in tot: tot[k] += len(r.violations) if k == "violations" else getattr(r, k)
        out[ov.name] = tot
    names = list(out); mod, sev = out[names[0]], out[names[1]]
    ok = all(mod[k] > 0 and sev[k] > 0 for k in tot if k != "violations") and sum(v for k, v in sev.items() if k != "violations") > sum(v for k, v in mod.items() if k != "violations") and mod["violations"] == 0 == sev["violations"]
    return {"counts": out, "pass": bool(ok), "criterion": "every stress counter > 0 under both overlays, severe total > moderate total, zero integrity violations"}
