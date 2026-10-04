"""Event tables (feature value + observation time + first session whose close can reflect it) built only from information published at the event time."""
import json, os
from datetime import date, datetime, timedelta
ROOT = os.path.join(os.path.dirname(__file__), "..")
J = lambda p: json.load(open(os.path.join(ROOT, p)))
WASDE_INSTR = {"corn": "ZC", "soybeans": "ZS", "wheat": "ZW"}


def wasde_events():
    out = []
    for r in J("derived/wasde_vintage.json")["rows"]:
        if r["stocks_to_use_prev_month"] is None or r["use_total_prev_month"] is None:
            continue                                   # first projection month of a marketing year has no previous-month projection
        d = r["release_date"]
        base = {"source": "WASDE", "commodity": r["commodity"], "instrument": WASDE_INSTR[r["commodity"]], "obs_time": d + "T12:00", "t0_rule": "same_session",
                "release_date": d, "month": int(d[5:7]), "marketing_year": r["marketing_year"]}
        feats = {"w_stu_chg_pp": (r["stocks_to_use"] - r["stocks_to_use_prev_month"]) * 100}
        if r["yield_prev_month"]:
            feats["w_yield_rev_pct"] = (r["yield"] / r["yield_prev_month"] - 1) * 100
        if r["production_prev_month"]:
            feats["w_prod_rev_pct"] = (r["production"] / r["production_prev_month"] - 1) * 100
        for k, v in feats.items():
            out.append({**base, "feature": k, "value": v})
    return out


def crop_events():
    rows = sorted(J("derived/crop_condition.json"), key=lambda r: r["release_date"])
    out, prev = [], {}
    for r in rows:
        key = r["crop"]; p = prev.get(key)
        if p and 5 <= (date.fromisoformat(r["release_date"]) - date.fromisoformat(p["release_date"])).days <= 10:
            out.append({"source": "CROP_PROGRESS", "commodity": key, "instrument": {"corn": "ZC", "soybeans": "ZS"}[key], "feature": "c_ge_chg_pp",
                        "value": r["good_excellent"] - p["good_excellent"], "obs_time": r["release_date"] + "T16:00", "t0_rule": "next_session",
                        "release_date": r["release_date"], "month": int(r["release_date"][5:7])})
        prev[key] = r
    return out


def drought_events(states=("IA", "IL", "IN", "NE", "MN")):
    d = J("derived/usdm_weekly_cumulative.json")
    weeks = {}
    for st in states:
        for r in d[st]:
            weeks.setdefault(r["map_date"], {})[st] = r["d1"]
    ds = sorted(w for w, v in weeks.items() if len(v) == len(states))
    out = []
    for a, b in zip(ds, ds[1:]):
        if (date.fromisoformat(b) - date.fromisoformat(a)).days != 7:
            continue
        pub = (date.fromisoformat(b) + timedelta(days=2)).isoformat()      # Thursday 08:30 ET
        m = int(pub[5:7])
        if not 4 <= m <= 9:
            continue                                                         # pre-specified growing-season window
        val = sum(weeks[b].values()) / len(states) - sum(weeks[a].values()) / len(states)
        for com, ins in (("corn", "ZC"), ("soybeans", "ZS")):
            out.append({"source": "USDM", "commodity": com, "instrument": ins, "feature": "d_belt_d1_chg_pp", "value": val, "obs_time": pub + "T08:30",
                        "t0_rule": "same_session", "release_date": pub, "month": m})
    return out


def eia_events(cal):
    """cal: set of trading dates of the fuel calendar. Release = Wednesday after week-ending Friday at 10:30 ET; a missing Monday session
    in the release week (federal holiday) shifts it one day later (approximation, disclosed)."""
    e = J("cache/eia_extract.json")
    def weekly(name):
        return {a: b for a, b in e[name]["data"]}
    stocks, util, crude = weekly("distillate_stocks_kbbl"), weekly("refinery_utilization_pct"), weekly("crude_stocks_ex_spr_kbbl")
    out = []
    def release(we):
        w = date.fromisoformat(f"{we[:4]}-{we[4:6]}-{we[6:]}"); rel = w + timedelta(days=5)
        mon = rel - timedelta(days=2)
        if mon.isoformat() not in cal:
            rel += timedelta(days=1)
        return rel.isoformat()
    def seasonal_surprise(series, we, kind):
        ds = sorted(series); i = ds.index(we)
        if i < 1: return None
        chg = series[we] - series[ds[i - 1]]
        ref = []
        for y in range(1, 6):
            tgt = (date.fromisoformat(f"{we[:4]}-{we[4:6]}-{we[6:]}") - timedelta(days=364 * y)).strftime("%Y%m%d")
            if tgt in series:
                j = ds.index(tgt)
                if j >= 1: ref.append(series[tgt] - series[ds[j - 1]])
        return chg - sum(ref) / len(ref) if len(ref) >= 3 else None
    for we in sorted(stocks):
        if we < "20070101": continue
        rd = release(we); month = int(rd[5:7])
        for feat, ser, ins in (("e_dist_stock_surprise_kbbl", stocks, "HO"), ("e_crude_stock_surprise_kbbl", crude, "CL")):
            if we in ser:
                v = seasonal_surprise(ser, we, "stock")
                if v is not None:
                    out.append({"source": "EIA_WPSR", "commodity": "distillate" if ins == "HO" else "crude", "instrument": ins, "feature": feat, "value": v,
                                "obs_time": rd + "T10:30", "t0_rule": "same_session", "release_date": rd, "month": month})
        if we in util:
            ds = sorted(util); i = ds.index(we)
            if i >= 1:
                out.append({"source": "EIA_WPSR", "commodity": "distillate", "instrument": "HO", "feature": "e_util_chg_pp", "value": util[we] - util[ds[i - 1]],
                            "obs_time": rd + "T10:30", "t0_rule": "same_session", "release_date": rd, "month": month})
    return out


def transmission_events():
    """Monthly (last session of each month, 16:00 ET close). Value = trailing 20-session return of the cost-side instrument measured to that close.
    Target = corn futures drift after the NEXT session's close (t0 = next session), 20 and 60 sessions."""
    from .prices import load
    z, ho, cf, spy = load("ZC"), load("HO"), load("CF"), load("SPY")
    out = []
    for i, d in enumerate(z.dates[:-1]):
        if d[:7] == z.dates[i + 1][:7] or not ("2007" <= d[:4] <= "2022"):
            continue
        month = int(d[5:7])
        base = {"source": "TRANSMISSION", "commodity": "corn", "instrument": "ZC", "obs_time": d + "T16:00", "t0_rule": "next_session", "release_date": d, "month": month}
        j = ho.pos.get(d)
        if j is not None and j >= 20:
            out.append({**base, "feature": "t_ho_ret20", "value": ho.ret(j - 20, j)})
        a, b = cf.pos.get(d), spy.pos.get(d)
        if a is not None and b is not None and a >= 20 and b >= 20:
            out.append({**base, "feature": "t_cf_rel_spy_ret20", "value": cf.ret(a - 20, a) - spy.ret(b - 20, b)})
    return [e for e in out if e["value"] is not None]
