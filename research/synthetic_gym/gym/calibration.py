"""Historical calibration from the DEVELOPMENT lane ONLY (2007-02-07 .. 2018-12-31, dataset
ds_7e16896c...; the accepted normalized dataset with its dividend accounting repairs).
Nothing from the qualification (2019-2022), championship (2023-2025) or reserve (2026) lanes is read: rows
after DEV_END are never loaded. Deterministic; no randomness."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

from . import stats as st

SYMBOLS = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
DEV_START, DEV_END = "2007-02-07", "2018-12-31"
EXPECTED_ROWS = 2996
DATASET_ID = "ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131"
GARCH_GRID = [(0.05, 0.93), (0.07, 0.92), (0.09, 0.90), (0.12, 0.86)]
BLOCK_LEN = 63
REGIME_NAMES = ["calm", "normal", "stress", "crisis"]


class CalibrationError(RuntimeError):
    pass


def load_dev(data_dir: Path) -> dict:
    """rows by symbol, DEV lane only, aligned; the raw CSV is read line by line and anything after DEV_END is dropped unread"""
    out = {}
    for s in SYMBOLS:
        rows = []
        with open(Path(data_dir) / f"{s}.csv") as f:
            for r in csv.DictReader(f):
                t = r["timestamp"]
                if t < DEV_START:
                    continue
                if t > DEV_END:
                    break
                ca = json.loads(r["corporate_action"]) if r["corporate_action"] else {}
                rows.append({"t": t, "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
                             "v": float(r["volume"]), "div": float(ca.get("dividend_amount", 0.0))})
        out[s] = rows
    dates = [r["t"] for r in out["SPY"]]
    for s in SYMBOLS:
        if [r["t"] for r in out[s]] != dates:
            raise CalibrationError(f"{s} dates not aligned with SPY inside the development lane")
    if len(dates) != EXPECTED_ROWS:
        raise CalibrationError(f"expected {EXPECTED_ROWS} development sessions, found {len(dates)}")
    return out


def dev_data_digest(data_dir: Path) -> str:
    rows = load_dev(data_dir)
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _filter_sigma(rets, V, a, b):
    """GARCH(1,1)-style variance filter used BOTH to standardize history and to generate worlds"""
    s2, out = V, []
    for r in rets:
        out.append(math.sqrt(s2))
        s2 = V * (1 - a - b) + a * r * r + b * s2
    return out  # out[t] = sigma used to scale r[t] (known at t-1)


def build(data_dir: Path) -> dict:
    rows = load_dev(data_dir)
    n = len(rows["SPY"]) - 1                     # features start at index 1 (need previous close)
    feat = {s: {"r": [], "o": [], "i": [], "hi": [], "lo": [], "lv": [], "div": [], "yield": []} for s in SYMBOLS}
    for s in SYMBOLS:
        R = rows[s]
        for t in range(1, len(R)):
            c0, d = R[t - 1]["c"], R[t]["div"]
            r = math.log((R[t]["c"] + d) / c0)
            ov = math.log((R[t]["o"] + d) / c0)
            f = feat[s]
            f["r"].append(r); f["o"].append(ov); f["i"].append(r - ov)
            f["hi"].append(max(0.0, math.log(R[t]["h"] / max(R[t]["o"], R[t]["c"]))))
            f["lo"].append(max(0.0, math.log(min(R[t]["o"], R[t]["c"]) / R[t]["l"])))
            f["lv"].append(math.log(max(R[t]["v"], 1.0)))
            f["div"].append(d); f["yield"].append(d / c0)
    # volume deviations from a trailing median
    for s in SYMBOLS:
        lv = feat[s]["lv"]; dev = []
        for t in range(len(lv)):
            w = sorted(lv[max(0, t - 63):t]) or [lv[t]]
            dev.append(lv[t] - w[len(w) // 2])
        feat[s]["vdev"] = dev
    # variance filter parameters: pick the grid point with the best summed quasi-likelihood
    V = {s: st.var(feat[s]["r"]) for s in SYMBOLS}
    best, best_ll = None, -1e300
    for a, b in GARCH_GRID:
        ll = 0.0
        for s in SYMBOLS:
            sig = _filter_sigma(feat[s]["r"], V[s], a, b)
            ll += sum(-math.log(x) - 0.5 * (r / x) ** 2 for r, x in zip(feat[s]["r"], sig)) / len(sig)
        if ll > best_ll:
            best, best_ll = (a, b), ll
    a, b = best
    sigma = {s: _filter_sigma(feat[s]["r"], V[s], a, b) for s in SYMBOLS}
    z = {s: {"o": [feat[s]["o"][t] / sigma[s][t] for t in range(n)], "i": [feat[s]["i"][t] / sigma[s][t] for t in range(n)],
             "hi": [feat[s]["hi"][t] / sigma[s][t] for t in range(n)], "lo": [feat[s]["lo"][t] / sigma[s][t] for t in range(n)],
             "vdev": feat[s]["vdev"]} for s in SYMBOLS}
    # regime tags from SPY trailing-21d realized vol percentile and trailing drawdown
    spy = feat["SPY"]["r"]
    rv = [st.sd(spy[max(0, t - 20):t + 1]) * math.sqrt(252) if t >= 5 else None for t in range(n)]
    known = sorted(x for x in rv if x is not None)
    p35, p80, p96 = st.quantile(known, .35), st.quantile(known, .80), st.quantile(known, .96)
    lvl, peak, tag = 0.0, 0.0, []
    for t in range(n):
        lvl += spy[t]; peak = max(peak, lvl)
        dd = math.exp(lvl - peak) - 1
        v = rv[t] if rv[t] is not None else p35
        tag.append(3 if (v >= p96 or dd <= -0.20) else 2 if v >= p80 else 1 if v >= p35 else 0)
    # quarter blocks -> disjoint train/validation/test pools, stratified by dominant regime, 50/25/25
    nblocks = n // BLOCK_LEN
    dom = []
    for k in range(nblocks):
        seg = tag[k * BLOCK_LEN:(k + 1) * BLOCK_LEN]
        dom.append(max(range(4), key=lambda g: (seg.count(g), -g)))
    pools = {"training": [], "validation": [], "sealed_test": []}
    pattern = ["training", "validation", "training", "sealed_test"]
    for g in range(4):
        members = [k for k in range(nblocks) if dom[k] == g]
        for j, k in enumerate(members):
            pools[pattern[(j + g) % 4]].append(k)
    for v in pools.values():
        v.sort()
    # dividend templates: complete calendar years 2008-2018 -> [(month, day, yield)]
    divt = {s: {} for s in SYMBOLS}
    for s in SYMBOLS:
        R = rows[s]
        for t in range(1, len(R)):
            if R[t]["div"] > 0:
                y, m, d = int(R[t]["t"][:4]), int(R[t]["t"][5:7]), int(R[t]["t"][8:10])
                if y >= 2008:
                    divt[s].setdefault(y, []).append((m, d, R[t]["div"] / R[t - 1]["c"]))
    level = {s: st.mean([x for x in feat[s]["lv"]]) for s in SYMBOLS}
    price0 = {s: rows[s][-1]["c"] for s in SYMBOLS}
    cal = {"dataset_id": DATASET_ID, "dev_range": [DEV_START, DEV_END], "sessions": n + 1, "symbols": SYMBOLS,
           "garch": {"a": a, "b": b}, "long_run_var": V, "z": z, "tag": tag, "tag_cuts_annualized_spy_vol": [p35, p80, p96],
           "tag_counts": [tag.count(g) for g in range(4)], "block_len": BLOCK_LEN, "dominant_regime_per_block": dom, "pools": pools,
           "dividend_templates": divt, "log_volume_level": level, "last_close_in_dev": price0,
           "dates": [r["t"] for r in rows["SPY"]][1:]}
    cal["summary"] = summarize_series({s: feat[s]["r"] for s in SYMBOLS}, {s: feat[s]["yield"] for s in SYMBOLS})
    return cal


def summarize_series(rets: dict, yields: dict | None = None) -> dict:
    """per-asset and cross-asset statistics of daily total log returns (identical code for history and synthetic worlds)"""
    out = {"asset": {}, "corr": {}, "rolling_corr_spy_tlt": {}}
    for s, r in rets.items():
        a = [abs(x) for x in r]
        out["asset"][s] = {"ann_vol": st.ann_vol(r), "skew": st.skew(r), "ex_kurt": st.ex_kurt(r), "ac1_ret": st.autocorr(r, 1),
                           "ac1_abs": st.autocorr(a, 1), "ac5_abs": st.autocorr(a, 5), "mean_ann": st.mean(r) * 252,
                           "worst_day": min(r), "best_day": max(r)}
        if yields:
            out["asset"][s]["ann_div_yield"] = sum(yields[s]) / len(yields[s]) * 252
    for i, a in enumerate(SYMBOLS):
        for b in SYMBOLS[i + 1:]:
            out["corr"][f"{a}-{b}"] = st.corr(rets[a], rets[b])
    spy, tlt = rets["SPY"], rets["TLT"]
    rc = [st.corr(spy[t - 59:t + 1], tlt[t - 59:t + 1]) for t in range(59, len(spy), 20)]
    out["rolling_corr_spy_tlt"] = {"min": min(rc), "p10": st.quantile(rc, .10), "median": st.quantile(rc, .5), "p90": st.quantile(rc, .9), "max": max(rc)}
    return out
