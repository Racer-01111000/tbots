"""Plausibility validation of the generator against the development-lane calibration set. Uses ENGINEERING worlds
(disjoint seeds, training pool) -- never the sealed-test worlds. A statistic passes when the historical value lies in
the synthetic [P05, P95] band OR the synthetic median is within the stated tolerance of it. Failures are reported."""
from __future__ import annotations

import math

from . import calibration as C
from . import stats as st
from .world import SYMBOLS, World

REL_TOL = 0.35          # relative tolerance on the median for positive-scale statistics
ABS_TOL_CORR = 0.25     # absolute tolerance for correlations / autocorrelations
ASSET_STATS = ["ann_vol", "ex_kurt", "skew", "ac1_abs", "ac1_ret", "mean_ann", "worst_day"]


def world_returns(w: World) -> dict:
    """daily total log returns per symbol from the as-of-final adjusted series"""
    out = {}
    for s in SYMBOLS:
        a = w.adjuster.history(s, w.n - 1, w.n)
        out[s] = [math.log(a[i] / a[i - 1]) for i in range(1, len(a))]
    return out


def _band(vals):
    return st.quantile(vals, .05), st.quantile(vals, .5), st.quantile(vals, .95)


def _check(hist, synth, absolute):
    lo, med, hi = _band(synth)
    inside = lo <= hist <= hi
    tol = ABS_TOL_CORR if absolute else REL_TOL * max(abs(hist), 1e-9)
    near = abs(med - hist) <= tol
    return {"hist": hist, "synth_p05": lo, "synth_median": med, "synth_p95": hi, "pass": bool(inside or near), "inside_band": bool(inside), "near_median": bool(near)}


def validate(cal: dict, worlds: list) -> dict:
    summaries = [C.summarize_series(world_returns(w)) for w in worlds]
    H = cal["summary"]
    rows, fails = [], []
    for s in SYMBOLS:
        for k in ASSET_STATS:
            absolute = k in ("ac1_ret", "skew") or k == "ac1_abs"
            r = _check(H["asset"][s][k], [x["asset"][s][k] for x in summaries], absolute)
            r.update(kind="asset", symbol=s, stat=k); rows.append(r)
            if not r["pass"]:
                fails.append(f"{s}.{k}")
    for pair, hv in H["corr"].items():
        r = _check(hv, [x["corr"][pair] for x in summaries], True)
        r.update(kind="corr", pair=pair); rows.append(r)
        if not r["pass"]:
            fails.append(f"corr {pair}")
    for k in ("min", "median", "max"):
        r = _check(H["rolling_corr_spy_tlt"][k], [x["rolling_corr_spy_tlt"][k] for x in summaries], True)
        r.update(kind="rolling_corr", stat=k); rows.append(r)
        if not r["pass"]:
            fails.append(f"rolling SPY-TLT corr {k}")
    # properties the brief requires but history cannot show: stock/bond correlation must CHANGE sign across worlds/time
    pos = sum(1 for x in summaries if x["rolling_corr_spy_tlt"]["max"] > 0.15)
    neg = sum(1 for x in summaries if x["rolling_corr_spy_tlt"]["min"] < -0.15)
    return {"n_worlds": len(worlds), "n_stats": len(rows), "n_pass": sum(r["pass"] for r in rows), "failures": fails, "rows": rows,
            "stock_bond_corr_changes": {"worlds_with_positive_stretch(>0.15)": pos, "worlds_with_negative_stretch(<-0.15)": neg},
            "historical_caveat": "development lane 2007-2018 has median SPY-TLT correlation %.2f and no sustained inflationary regime; positive stock/bond co-movement is a declared scenario assumption, not a calibrated feature" % H["rolling_corr_spy_tlt"]["median"]}
