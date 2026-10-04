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


# ----------------------------------------------------------------------------- tail / path-severity checks
# Thresholds were written down BEFORE the generator was tuned against them (generator iterations are logged in docs/).
# Historical reference values come from the development lane (2007-02..2018-12): SPY worst day -0.104; share of days with
# rolling-20d SPY vol >40% = 3.7% and >60% = 1.6%; SPY 5y max drawdowns [-.55 -.51 -.22 -.19 -.19 -.13 -.13] (median -.19);
# worst-asset trough/start over 5y windows [.27 .37 .68 .76 .42 .42 .42] (median .42).
TAIL_CRITERIA = {
    "spy_worst_day_median": (-0.156, -0.073),
    "share_days_roll20_vol_gt40": (0.019, 0.110),
    "share_days_roll20_vol_gt60": (0.005, 0.064),
    "spy_5y_maxdd_median": (-0.33, -0.15),
    "spy_5y_maxdd_p10_worst": (-0.65, 0.0),
    "spy_5y_maxdd_min": (-0.80, 0.0),
    "worst_asset_trough_median": (0.30, 0.65),
    "worst_asset_trough_p05": (0.15, 1.0),
    "worst_asset_trough_min": (0.10, 1.0),
    "spy_max_roll20_vol_median": (0.0, 0.90),
    "spy_max_roll20_vol_p95": (0.0, 1.40),
}


def tail_checks(worlds: list) -> dict:
    rets = [world_returns(w) for w in worlds]
    vals = {}
    vals["spy_worst_day_median"] = st.quantile([min(r["SPY"]) for r in rets], .5)
    rv_all, mx = [], []
    for r in rets:
        rv = [st.sd(r["SPY"][k - 20:k]) * math.sqrt(252) for k in range(20, len(r["SPY"]))]
        rv_all += rv; mx.append(max(rv))
    vals["share_days_roll20_vol_gt40"] = sum(1 for x in rv_all if x > .4) / len(rv_all)
    vals["share_days_roll20_vol_gt60"] = sum(1 for x in rv_all if x > .6) / len(rv_all)
    dd = []
    trough = []
    for w in worlds:
        a = w.adjuster.history("SPY", w.n - 1, w.n)[w.n_warmup:]
        dd.append(st.max_drawdown_from_levels(a))
        trough.append(min(min(w.adjuster.history(s, w.n - 1, w.n)[w.n_warmup:]) / w.adjuster.history(s, w.n - 1, w.n)[w.n_warmup] for s in SYMBOLS))
    vals["spy_5y_maxdd_median"] = st.quantile(dd, .5)
    vals["spy_5y_maxdd_p10_worst"] = st.quantile(dd, .10)
    vals["spy_5y_maxdd_min"] = min(dd)
    vals["worst_asset_trough_median"] = st.quantile(trough, .5)
    vals["worst_asset_trough_p05"] = st.quantile(trough, .05)
    vals["worst_asset_trough_min"] = min(trough)
    vals["spy_max_roll20_vol_median"] = st.quantile(mx, .5)
    vals["spy_max_roll20_vol_p95"] = st.quantile(mx, .95)
    rows = {k: {"value": v, "band": TAIL_CRITERIA[k], "pass": TAIL_CRITERIA[k][0] <= v <= TAIL_CRITERIA[k][1]} for k, v in vals.items()}
    return {"rows": rows, "n_pass": sum(r["pass"] for r in rows.values()), "n": len(rows), "failures": [k for k, r in rows.items() if not r["pass"]]}
