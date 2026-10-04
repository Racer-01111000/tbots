"""Event-study evaluator driven entirely by a frozen spec (features/FEATURE_SPEC_v1.json). Targets per event:
react = close(t0)/close(t0-1)-1 (contemporaneous); drift_h = close(t0+h)/close(t0)-1 (post-reaction, what a bot could still act on); pre = close(t0-1)/close(t0-6)-1 (anticipation).
Seasonal baseline = unconditional same-horizon return by calendar month over the DISCOVERY years only."""
import datetime as dt
from .events import wasde_events, crop_events, drought_events, eia_events, transmission_events
from .prices import load
from .stats import perm_p, bh, spearman
_P = {}


def px(sym):
    if sym not in _P: _P[sym] = load(sym)
    return _P[sym]


def seasonal_base(sym, h, y0, y1):
    s = px(sym); acc = {}
    for i in range(1, len(s.dates) - max(h, 1)):
        y = int(s.dates[i][:4])
        if y0 <= y <= y1:
            a, b = (i - 1, i) if h == 0 else (i, i + h)
            r = s.ret(a, b)
            if r is not None: acc.setdefault(int(s.dates[i][5:7]), []).append(r)
    return {m: sum(v) / len(v) for m, v in acc.items()}


def attach(ev, target_h):
    s = px(ev["instrument"]); rd = ev["release_date"]
    d = dt.date.fromisoformat(rd) + (dt.timedelta(days=1) if ev["t0_rule"] == "next_session" else dt.timedelta(0))
    i0 = s.first_on_or_after(d.isoformat())
    if i0 is None or i0 < 6: return None
    if ev["t0_rule"] == "same_session" and s.dates[i0] != rd: return None      # release day not a session for this instrument -> unusable
    out = {"react": s.ret(i0 - 1, i0), "pre": s.ret(i0 - 6, i0 - 1)}
    for h in target_h: out[f"drift{h}"] = s.ret(i0, i0 + h) if i0 + h < len(s.dates) else None
    out["_i0"] = i0
    return out


def build_all():
    return wasde_events() + crop_events() + drought_events() + eia_events(set(px("HO").dates)) + transmission_events()


_ROLL = {}


def roll_indices(sym):
    """session indices of the contract-expiry session and the two after it (the empirical roll-gap location: big moves cluster at expiry+1)"""
    if sym not in _ROLL:
        from .rolls import expiries
        s = px(sym); out = set()
        for e in expiries(sym, 2006, 2023):
            i = s.first_on_or_after(e.isoformat())
            if i is not None: out |= {i, i + 1, i + 2}
        _ROLL[sym] = out
    return _ROLL[sym]


def contaminated(sym, i0, tgt):
    if sym not in ("ZS", "ZC", "ZW", "KE", "HO", "CL"): return False
    a, b = (i0 - 1, i0) if tgt == "react" else (i0 - 6, i0 - 1) if tgt == "pre" else (i0, i0 + int(tgt[5:]))
    return any(a < i <= b for i in roll_indices(sym))


def _mad_filter(xs, ys, st, k=4.0):
    if len(ys) < 10: return xs, ys, st
    med = sorted(ys)[len(ys) // 2]; mad = sorted(abs(y - med) for y in ys)[len(ys) // 2] * 1.4826 or 1e-12
    keep = [i for i, y in enumerate(ys) if abs(y - med) <= k * mad]
    return [xs[i] for i in keep], [ys[i] for i in keep], [st[i] for i in keep]


def run(spec, split, n_perm=2000, events=None, roll=False, outlier=False):
    y0, y1 = spec["splits"][split]; base_y0, base_y1 = spec["splits"]["discovery"]
    events = events or build_all()
    fam = spec["tests"]; rows = []
    for t in fam:
        evs = [e for e in events if e["feature"] == t["feature"] and e["commodity"] == t["commodity"] and y0 <= int(e["release_date"][:4]) <= y1]
        for tgt in t["targets"]:
            h = int(tgt[5:]) if tgt.startswith("drift") else 0
            xs, ys, st = [], [], []
            seasonal = seasonal_base(evs[0]["instrument"], h, base_y0, base_y1) if evs else {}
            if tgt == "pre": seasonal = {}                                  # anticipation measured as raw 5d change
            for e in evs:
                a = attach(e, spec["target_horizons"])
                if not a or a.get(tgt) is None: continue
                if roll and contaminated(e["instrument"], a["_i0"], tgt): continue
                xs.append(e["value"]); ys.append(a[tgt] - seasonal.get(e["month"], 0.0)); st.append(e["month"])
            if outlier: xs, ys, st = _mad_filter(xs, ys, st)
            if len(xs) < spec["min_events"]:
                rows.append({**t, "target": tgt, "split": split, "n": len(xs), "rho": None, "p": None}); continue
            rho, p = perm_p(xs, ys, st, n_perm=n_perm, seed=spec["seed"])
            rows.append({**t, "target": tgt, "split": split, "n": len(xs), "rho": rho, "p": p,
                         "sign_as_expected": None if t.get("expected_sign") in (None, 0) else (rho * t["expected_sign"] > 0)})
    ok = [r for r in rows if r["p"] is not None]
    rej, adj = bh([r["p"] for r in ok], spec["bh_q"])
    for r, a, k in zip(ok, adj, rej): r["q"] = a; r["fdr_significant"] = k
    return rows
