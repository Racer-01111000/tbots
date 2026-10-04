"""Hybrid synthetic-world generator (gen-v2: REPAIRED copy of gen-v1; gen-v1 files are untouched so v1 results stay reproducible).
Repairs are parameter-level and listed in docs/GENERATOR_V2_REPAIRS.md. Original v1 docstring follows. (gen-v1). See configs/generator_v1.json and gym/factors.py for the declared
assumptions. A world is a pure function of (spec, calibration): all randomness flows from Rng(seed)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import sessions as SESS
from .factors import (BASE_HALF_SPREAD_BPS, BETAS, DBC_EXPENSE_RATIO, DURATION, ANNUAL_DRIFT_PRIOR, EVENT_KINDS, FACTORS, FAMILIES,
                      JUMP_LOADING, JUMP_RATE_PER_YEAR, REGIME_BASE_P, REGIME_EXPONENT, REGIME_VOL_MULT, SHORT_EVENTS, envelope)
from .rng import Rng, derive_seed
from .world import CorporateAction, SYMBOLS, World, check_invariants

CFG = json.loads((Path(__file__).resolve().parent.parent / "configs" / "generator_v2.json").read_text())
GENERATOR_VERSION = CFG["version"]
TAIL_CAP = CFG.get("tail_cap", 1.5)
JUMP_NEG_PROB = CFG.get("jump_neg_prob", 0.70)
JUMP_CLAMP = CFG.get("jump_clamp", 0.10)
RATIO_HI_MULT = CFG.get("ratio_hi_mult", 1.0)     # widens the historical-range clamp on the volatility state / ceiling (v1: 1.0)
PATH_GUARD = 1.15      # rolling-window return limits as a multiple of the development-lane extremes
OVERLAY_CAP = 0.45     # |cumulative event overlay| per asset (log return)
SPLIT_POOL = {"training": "training", "validation": "validation", "sealed_test": "sealed_test", "engineering": "training"}


@dataclass
class WorldSpec:
    experiment_id: str
    split: str
    family: str
    index: int
    start: date = date(2100, 1, 4)
    n_warmup: int = 400
    years: int = 5

    @property
    def world_id(self):
        return f"{self.experiment_id}:{self.split}:{self.family}:{self.index}"

    @property
    def seed(self):
        return derive_seed(self.experiment_id, self.split, self.family, self.index)


@dataclass
class HiddenState:
    regime: list
    factors: dict            # name -> path (list)
    events: list             # dicts: never visible to agents
    betas: dict
    jumps: list
    bond_flips: list
    stress_driver: list
    curve_state: list
    params: dict
    liquidity: list = field(default_factory=list)


def _transition_matrix(rng: Rng, noise_sd: float, stress_bias: float, calm_bias: bool):
    P = []
    for i, row in enumerate(REGIME_BASE_P):
        off = []
        for j, p in enumerate(row):
            if i == j:
                off.append(0.0); continue
            m = p * math.exp(rng.normal(0, noise_sd))
            if j > i:
                m *= stress_bias
            if calm_bias and j == 0:
                m *= 2.0
            off.append(m)
        tot = sum(off)
        stay = max(0.80, 1 - tot)
        scale = (1 - stay) / tot if tot > 0 else 0
        P.append([(stay if k == i else off[k] * scale) for k in range(4)])
    return P


def _build_events(rng, family: str, split: str, N: int, n_warm: int):
    tpl = FAMILIES[family]
    lo, hi = tpl["n_events"]
    shift = CFG["event_count_shift"][split]
    K = lo + rng.int_below(hi - lo + 1) + shift
    sev_lo, sev_hi = CFG["severity_range"][split]
    kinds, weights = zip(*tpl["menu"])
    vp = CFG["variant_probs"]
    variants, vweights = list(vp), list(vp.values())
    events, pending = [], []
    for _ in range(K):
        kind = kinds[rng.weighted_index(weights)]
        t0 = 20 + rng.int_below(max(1, N - 80))
        pending.append((kind, t0))
        for trig, follow, (a, b) in tpl.get("composite", []):
            if kind == trig:
                pending.append((follow[rng.int_below(len(follow))], min(N - 30, t0 + a + rng.int_below(b - a + 1))))
    for kind, t0 in pending:
        sev = rng.uniform(sev_lo, sev_hi)
        variant = variants[rng.weighted_index(vweights)]
        if kind in SHORT_EVENTS:
            rise, hold, decay, resid = 3 + rng.int_below(6), 5 + rng.int_below(11), 10 + rng.int_below(21), 0.0
        else:
            rise, hold, decay, resid = 5 + rng.int_below(36), rng.int_below(121), 20 + rng.int_below(231), rng.uniform(0, .6)
        mult = {"normal": 1.0, "priced_in": 0.35, "muted": 0.25, "reversed": -0.8}[variant]
        lead = (3 + rng.int_below(6)) if variant == "priced_in" else 0
        amps, stress = EVENT_KINDS[kind]
        events.append({"kind": kind, "t0": t0, "severity": sev, "variant": variant, "rise": rise, "hold": hold, "decay": decay,
                       "resid": resid, "lead": lead, "mult": mult, "stress": stress,
                       "amps": {f: a * sev * rng.uniform(.7, 1.3) * mult for f, a in amps.items()}})
    return events


def _draw_betas(rng):
    out = {}
    for s in SYMBOLS:
        out[s] = {}
        for f, (b, pflip) in BETAS[s].items():
            sign = -1.0 if rng.u() < pflip else 1.0
            out[s][f] = (sign * b * math.exp(rng.normal(0, .3)), rng.int_below(19) - 6)    # lag in [-6, +12]
    return out


def generate_world(spec: WorldSpec, cal: dict) -> World:
    rng = Rng(spec.seed)
    split = spec.split
    n_eval_dates = SESS.sessions_in_years(spec.start, spec.years)
    warm_dates = SESS.sessions_before(spec.start, spec.n_warmup)
    dates = warm_dates + n_eval_dates
    N, NW = len(dates), len(warm_dates)

    # ---- world-level draws
    L = rng.uniform(*CFG["policy_rate_level_range"])
    vol_scale = rng.uniform(*CFG["world_vol_scale_range"])
    tpl = FAMILIES[spec.family]
    P = _transition_matrix(rng, CFG["transition_noise_sd"][split], tpl["regime_stress_bias"], bool(tpl.get("calm_bias")))
    events = _build_events(rng, spec.family, split, N, NW)
    betas = _draw_betas(rng)

    # ---- event envelopes -> factor deviation paths and stress driver
    env = [[envelope(t, e["t0"], e["rise"], e["hold"], e["decay"], e["resid"]) for t in range(-60, N + 60)] for e in events]
    off = 60
    U = {f: [0.0] * N for f in FACTORS}
    stress_driver = [0.0] * N
    for e, ev in zip(env, events):
        for f, a in ev["amps"].items():
            u = U[f]
            for t in range(N):
                u[t] += a * ev_env(e, t, off)
        # the market-stress driver is TRANSIENT (envelope with no permanent residual): an event's factor effect may persist, but it
        # must not keep nudging the regime chain toward crisis forever
        for t in range(N):
            tr = envelope(t, ev["t0"], ev["rise"], ev["hold"], ev["decay"], 0.0)
            stress_driver[t] = max(stress_driver[t], ev["stress"] * tr * min(1.0, abs(ev["mult"]) + .2))

    # ---- rates
    pol, y10 = [0.0] * N, [0.0] * N
    ou_s, ou_tp = 0.0, 0.012
    for t in range(N):
        ou_s += -0.02 * ou_s + rng.normal(0, .0004)
        ou_tp += 0.02 * (0.012 - ou_tp) + rng.normal(0, .0005)
        pol[t] = min(.12, max(0.0, L + .7 * U["yield"][max(0, t - 10)] + ou_s))
        y10[t] = min(.15, max(.002, L + ou_tp + U["yield"][t] + ou_s))

    # ---- annual drift per asset (total-return drift per day)
    drift = {}
    for s in SYMBOLS:
        m, sd_, kind = ANNUAL_DRIFT_PRIOR[s]
        mu = rng.normal(m, sd_)
        if kind == "excess":
            mu += L
        drift[s] = (mu - CFG["drift_bias_correction_annual"][s]) / 252.0   # frozen moment-matching constant (see generator_v1.json)

    # ---- regimes (Markov chain coupled to event stress)
    regime, cur = [], 1
    for t in range(N):
        sd_ = stress_driver[t]
        if sd_ > .5 and cur < 2 and rng.u() < .08:
            cur = 2
        elif sd_ > .85 and cur < 3 and rng.u() < .04:
            cur = 3
        else:
            cur = rng.weighted_index(P[cur])
        regime.append(cur)

    # ---- bond sign flips: intervals with positive stock/bond co-movement (assumption; history has mostly negative)
    flips, t = [], 30
    while t < N - 30:
        if rng.u() < tpl["bond_flip_prob"] / 10.0:
            a, b = CFG["bond_flip_interval_sessions"]
            ln = a + rng.int_below(b - a + 1)
            flips.append((t, min(N, t + ln)))
            t += ln
        else:
            t += 25
    flipped = [False] * N
    for a, b in flips:
        for i in range(a, b):
            flipped[i] = True

    # ---- block pools
    pool_name = SPLIT_POOL[split]
    B = cal["block_len"]
    starts_by_regime = {g: [] for g in range(4)}
    for k in cal["pools"][pool_name]:
        for i in range(k * B, (k + 1) * B):
            starts_by_regime[cal["tag"][i]].append(i)
    for g in range(4):
        if not starts_by_regime[g]:      # regime absent from this pool: borrow the nearest populated regime
            alt = min((h for h in range(4) if starts_by_regime[h]), key=lambda h: abs(h - g))
            starts_by_regime[g] = starts_by_regime[alt]
    z = cal["z"]
    # Demean residuals WITHIN the pool being sampled AND within each regime tag. The drawn per-world drift alone then sets
    # the long-run mean: a world's mean return cannot be moved by the (fixed, same-sign) mean of the particular
    # historical quarters in its pool, nor by how long it spends in a historically directional regime.
    zmean = _pool_zmean(cal, pool_name)

    # ---- asset event overlays (per-event leads, per-pair lags)
    overlay = {s: [0.0] * N for s in SYMBOLS}
    for e, ev in zip(env, events):
        for s in SYMBOLS:
            ov = overlay[s]
            for f, a in ev["amps"].items():
                if f not in betas[s]:
                    continue
                beta, lag = betas[s][f]
                sh = lag - ev["lead"]
                for t in range(N):
                    ov[t] += beta * a * (ev_env(e, t - sh, off) - ev_env(e, t - 1 - sh, off))
    # Re-center the event-induced overlay per asset so scenarios create SWINGS, not a net drift bias: the drift
    # (a per-world random draw) alone sets the long-run mean, so a strategy cannot win merely by holding an asset the
    # generator happens to tilt. The swings (and their path structure) are untouched.
    for s_ in SYMBOLS:
        m_ = sum(overlay[s_]) / N
        overlay[s_] = [x - m_ for x in overlay[s_]]
    # fund/roll behaviour for DBC (not a copy of spot): roll yield follows the oil-tightness state; collateral = cash; expense
    curve = [0.0] * N
    cs = 0.0
    for t in range(N):
        target = max(-1.0, min(1.0, 2.5 * U["oil"][t]))
        cs += .05 * (target - cs)
        curve[t] = cs
        overlay["DBC"][t] += (.04 * cs) / 252.0 - DBC_EXPENSE_RATIO / 252.0
    # bound the cumulative overlay so no single world can be driven by a runaway factor
    for s in SYMBOLS:
        run = 0.0
        for t in range(N):
            nxt = max(-OVERLAY_CAP, min(OVERLAY_CAP, run + overlay[s][t]))
            overlay[s][t] = nxt - run
            run = nxt

    # ---- main loop: block-bootstrapped residual vectors -> prices
    a_, b_ = cal["garch"]["a"], cal["garch"]["b"]
    V = {s: cal["long_run_var"][s] * vol_scale ** 2 * CFG["variance_scale"][s] for s in SYMBOLS}   # variance_scale: frozen moment-matching constants
    s2 = dict(V)
    price0 = {s: cal["last_close_in_dev"][s] * math.exp(rng.uniform(math.log(.7), math.log(1.4))) for s in SYMBOLS}
    cont = {s: price0[s] for s in SYMBOLS}                 # continuous (split-free) closes
    O, H, Lw, C = ({s: [] for s in SYMBOLS} for _ in range(4))
    Vol = {s: [] for s in SYMBOLS}
    Spread = {s: [] for s in SYMBOLS}
    div_cont = {s: {} for s in SYMBOLS}                    # idx -> dividend amount in continuous units
    ex_plan = _plan_dividends(rng, cal, dates)
    jumps = []
    liq = []
    cumlog = {s_: 0.0 for s_ in SYMBOLS}
    hist63 = {s_: [] for s_ in SYMBOLS}
    hist252 = {s_: [] for s_ in SYMBOLS}
    diag = {s_: {"noise": 0.0, "overlay": 0.0, "drift": 0.0, "jump": 0.0, "tot": 0.0} for s_ in SYMBOLS}
    block_i, block_left = 0, 0
    vshift = {s: rng.normal(0, .25) for s in SYMBOLS}
    for t in range(N):
        g = regime[t]
        if block_left <= 0 or (t > 0 and regime[t] != regime[t - 1] and rng.u() < .5):
            block_i = starts_by_regime[g][rng.int_below(len(starts_by_regime[g]))]
            end_of_quarter = (block_i // B + 1) * B
            block_left = min(rng.geometric(CFG["block_mean_len"], CFG["block_cap"]), end_of_quarter - block_i)
        zi_ = block_i
        block_i += 1; block_left -= 1
        mult = REGIME_VOL_MULT[g]
        # jumps (common factor with asset loadings) -- mostly overnight
        jm = 0.0
        if rng.u() < JUMP_RATE_PER_YEAR[g] / 252.0:
            jm = max(-JUMP_CLAMP, min(JUMP_CLAMP, -abs(rng.student_t(3)) * .010 * rng.uniform(1, 2) * (1 if rng.u() < JUMP_NEG_PROB else -1)))
            jumps.append((t, jm))
        liq_t = min(1.5, .2 * g + .8 * max(0.0, U["liquidity"][t]) + (.25 if jm else 0.0))
        liq.append(liq_t)
        for s in SYMBOLS:
            mult_s = mult ** REGIME_EXPONENT[s]
            sig = min(math.sqrt(s2[s]) * mult_s, math.sqrt(V[s] * cal["sigma2_ratio_range"][s][1] * RATIO_HI_MULT))   # state x regime cannot exceed the worst volatility ever filtered in history
            tg = cal["tag"][zi_]
            zo = (z[s]["o"][zi_] - zmean[s]["o"][tg]) / zmean[s]["scale"][tg]
            zz = (z[s]["i"][zi_] - zmean[s]["i"][tg]) / zmean[s]["scale"][tg]
            if s in ("IEF", "TLT") and flipped[t]:
                zo, zz = -zo, -zz
            anchor = _anchor(cumlog[s])
            diag[s]["noise"] += sig * (zo + zz); diag[s]["overlay"] += overlay[s][t]; diag[s]["drift"] += drift[s]; diag[s]["jump"] += JUMP_LOADING[s] * jm
            o_ret = sig * zo + .30 * (overlay[s][t] + drift[s] + anchor) + JUMP_LOADING[s] * jm * .6
            i_ret = sig * zz + .70 * (overlay[s][t] + drift[s] + anchor) + JUMP_LOADING[s] * jm * .4
            noise = sig * (zo + zz)
            cap = TAIL_CAP * cal["abs_worst_daily"][s]            # no single day's noise beyond 1.5x the worst day in the development lane
            if abs(noise) > cap:
                k_ = cap / abs(noise); zo, zz = zo * k_, zz * k_
                o_ret = sig * zo + .30 * (overlay[s][t] + drift[s] + anchor) + JUMP_LOADING[s] * jm * .6
                i_ret = sig * zz + .70 * (overlay[s][t] + drift[s] + anchor) + JUMP_LOADING[s] * jm * .4
            # path-speed guard: rolling 63- and 252-session cumulative total return may not leave 1.15x the range the development lane showed
            r63, r252 = cal["roll63_range"][s], cal["roll252_range"][s]
            w63 = sum(hist63[s]); w252 = sum(hist252[s])
            lo_a = max(PATH_GUARD * r63[0] - w63 if r63[0] < 0 else -1e9, PATH_GUARD * r252[0] - w252 if r252[0] < 0 else -1e9)
            hi_a = min(PATH_GUARD * r63[1] - w63 if r63[1] > 0 else 1e9, PATH_GUARD * r252[1] - w252 if r252[1] > 0 else 1e9)
            tot_try = o_ret + i_ret
            if tot_try < lo_a:
                i_ret += lo_a - tot_try
            elif tot_try > hi_a:
                i_ret -= tot_try - hi_a
            hi_x = sig * max(0.0, z[s]["hi"][zi_])
            lo_x = sig * max(0.0, z[s]["lo"][zi_])
            D = 0.0
            if t in ex_plan[s]:
                D = ex_plan[s][t] * (_div_scale(s, y10[t], rng)) * cont[s]
                D = min(D, .08 * cont[s])
                div_cont[s][t] = D
            op = cont[s] * math.exp(o_ret) - D
            if op <= .2 * cont[s]:
                op = .2 * cont[s]
            cl = (op + D) * math.exp(i_ret) - D
            if cl <= .2 * op:
                cl = .2 * op
            hi_p = max(op, cl) * math.exp(hi_x)
            lo_p = min(op, cl) * math.exp(-lo_x)
            O[s].append(op); C[s].append(cl); H[s].append(hi_p); Lw[s].append(lo_p)
            tot = math.log((cl + D) / cont[s])
            diag[s]["tot"] += tot
            cumlog[s] += tot
            hist63[s].append(tot); hist252[s].append(tot)
            if len(hist63[s]) > 62: hist63[s].pop(0)
            if len(hist252[s]) > 251: hist252[s].pop(0)
            # filter state lives on the BASE scale (regime multiplier removed from the feedback), so it is stable by
            # construction: persistence a+b < 1 regardless of regime; realized vol = sqrt(s2) * regime multiplier
            s2[s] = V[s] * (1 - a_ - b_) + a_ * (tot / mult_s) ** 2 + b_ * s2[s]
            lo_r, hi_r = cal["sigma2_ratio_range"][s]
            s2[s] = min(max(s2[s], V[s] * lo_r), V[s] * hi_r * RATIO_HI_MULT)        # variance state confined to the historical range of the filter
            cont[s] = cl
            lv = cal["log_volume_level"][s] + vshift[s] + z[s]["vdev"][zi_] + .5 * liq_t
            Vol[s].append(max(0.0, math.exp(lv)))
            Spread[s].append(BASE_HALF_SPREAD_BPS[s] * (1 + 3.0 * liq_t))

    # ---- splits (applied as a level change of the continuous series)
    actions = []
    splits = []
    if rng.u() < CFG["split_probability_per_world"]:
        sym = SYMBOLS[rng.int_below(8)]
        num, den = [(2, 1), (3, 1), (1, 2)][rng.int_below(3)]
        idx = NW + 60 + rng.int_below(N - NW - 120)
        splits.append((sym, idx, num, den))
    out = {k: {s: list(v[s]) for s in SYMBOLS} for k, v in (("o", O), ("h", H), ("l", Lw), ("c", C), ("v", Vol))}
    for sym, idx, num, den in splits:
        R = num / den
        for t in range(idx, N):
            for k in ("o", "h", "l", "c"):
                out[k][sym][t] /= R
            out["v"][sym][t] *= R
        for t in list(div_cont[sym]):
            if t >= idx:
                div_cont[sym][t] /= R
        actions.append(CorporateAction("split", sym, idx, None, 0.0, num, den))
    for s in SYMBOLS:
        for t, D in sorted(div_cont[s].items()):
            if D <= 0:
                continue
            pay = t + 3 + rng.int_below(3)
            actions.append(CorporateAction("dividend", s, t, pay, round(D, 6)))
    w = World(spec.world_id, split, spec.family, spec.seed, dates, NW,
              {s: [round(x, 4) for x in out["o"][s]] for s in SYMBOLS}, {s: [round(x, 4) for x in out["h"][s]] for s in SYMBOLS},
              {s: [round(x, 4) for x in out["l"][s]] for s in SYMBOLS}, {s: [round(x, 4) for x in out["c"][s]] for s in SYMBOLS},
              {s: [int(round(x)) for x in out["v"][s]] for s in SYMBOLS}, {s: [round(x, 3) for x in Spread[s]] for s in SYMBOLS},
              [round(x, 6) for x in pol], [round(x, 6) for x in y10], actions)
    w.meta = {"generator": GENERATOR_VERSION, "policy_level": L, "vol_scale": vol_scale, "n_events": len(events)}
    w.hidden = HiddenState(regime, U, events, betas, jumps, flips, stress_driver, curve, {"L": L, "vol_scale": vol_scale, "drift": drift, "splits": splits, "diag": diag, "N": N}, liq)
    _repair_rounding(w)
    return w.finalize()


def _pool_zmean(cal, pool_name):
    """per (symbol, regime tag) within the sampled pool: the mean of each residual component (removed, so the per-world drift draw alone
    sets the long-run mean) and the RMS of their sum (divided out, so residuals have UNIT variance in every regime; the regime
    multiplier alone supplies stress severity and the variance filter's persistence is a+b<1 on average => stable)."""
    cache = cal.setdefault("_zmean_cache", {})
    if pool_name in cache:
        return cache[pool_name]
    B = cal["block_len"]
    idxs = [i for k in cal["pools"][pool_name] for i in range(k * B, (k + 1) * B)]
    out = {}
    for s in SYMBOLS:
        out[s] = {"o": [], "i": [], "scale": []}
        acc = [[0.0, 0.0, 0] for _ in range(4)]
        for i in idxs:
            a_ = acc[cal["tag"][i]]
            a_[0] += cal["z"][s]["o"][i]; a_[1] += cal["z"][s]["i"][i]; a_[2] += 1
        means = [(a_[0] / a_[2], a_[1] / a_[2]) if a_[2] else (0.0, 0.0) for a_ in acc]
        ss = [0.0] * 4; cnt = [0] * 4
        for i in idxs:
            g = cal["tag"][i]
            ss[g] += (cal["z"][s]["o"][i] - means[g][0] + cal["z"][s]["i"][i] - means[g][1]) ** 2; cnt[g] += 1
        for g in range(4):
            out[s]["o"].append(means[g][0]); out[s]["i"].append(means[g][1])
            out[s]["scale"].append(math.sqrt(ss[g] / cnt[g]) if cnt[g] and ss[g] > 0 else 1.0)
    cache[pool_name] = out
    return out


def _anchor(cum):
    """weak symmetric pull back toward the world's starting level once |log change| exceeds the threshold"""
    a = CFG["price_anchor"]
    ex = abs(cum) - a["threshold_log"]
    return 0.0 if ex <= 0 else -a["k_per_day"] * ex * (1.0 if cum > 0 else -1.0)


def ev_env(e, t, off):
    """envelope lookup with the -60 offset used when precomputing"""
    i = t + off
    if i < 0:
        return e[0]
    if i >= len(e):
        return e[-1]
    return e[i]


def _div_scale(sym, y10_t, rng):
    if sym in ("IEF", "TLT"):
        return max(.25, min(3.0, .25 + .75 * (y10_t / .025))) * rng.uniform(.9, 1.1)
    return rng.uniform(.8, 1.2)


def _plan_dividends(rng, cal, dates):
    """ex-date index -> historical yield (a template year of the calibration set is sampled per symbol and world-year)"""
    plan = {s: {} for s in SYMBOLS}
    years = sorted({d.year for d in dates})
    idx_by_date = {d: i for i, d in enumerate(dates)}
    for s in SYMBOLS:
        tmpl = cal["dividend_templates"][s]
        ty = sorted(tmpl)
        if not ty:
            continue
        for Y in years:
            events = tmpl[ty[rng.int_below(len(ty))]]
            for (m, d, yld) in events:
                try:
                    target = date(Y, m, min(d, 28))
                except ValueError:
                    continue
                j = next((i for i, dt in enumerate(dates) if dt >= target and dt.year == Y), None)
                if j is not None and j >= 2:
                    plan[s][j] = max(plan[s].get(j, 0.0), yld)
    return plan


def _repair_rounding(w: World):
    """rounding to 4 d.p. can nudge a bound by 1e-4; restore OHLC coherence deterministically"""
    for s in SYMBOLS:
        for i in range(w.n):
            o, c = w.open[s][i], w.close[s][i]
            if w.high[s][i] < max(o, c):
                w.high[s][i] = max(o, c)
            if w.low[s][i] > min(o, c):
                w.low[s][i] = min(o, c)
            if min(o, c, w.low[s][i]) < 0.0001:
                w.open[s][i] = max(o, .0001); w.close[s][i] = max(c, .0001); w.low[s][i] = max(w.low[s][i], .0001)
                w.high[s][i] = max(w.high[s][i], w.open[s][i], w.close[s][i])
