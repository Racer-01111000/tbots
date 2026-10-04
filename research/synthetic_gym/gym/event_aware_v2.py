"""EXPERIMENT SYNGYM-20261004-EVT-V2 (new version; the v1 generator, metrics, scoring and results are untouched).

indicator-schema-v2: physical-evidence observations (inventory surprise, stocks-to-use revision, crop-condition change) derived from the hidden
event list, released on a weekly/monthly schedule, with delay, missing coverage, preliminary-then-revised values and contradictory reports.
The evidence from the real-data module (research/food_fuel) is that these indicators are PRICED ON RELEASE and show no post-release drift; the
synthetic feed therefore releases evidence AFTER an event has begun (0-3 sessions) and asset responses keep the generator's random lags
[-6,+12] and priced-in/muted/reversed variants. Whether an event-aware overlay helps here is an open question, not an assumption.

EVT_AWARE_v1 is a NEW strategy identity: parent = a frozen price-only genome (unchanged, run separately as the control); the overlay constants below are
fixed before any v2 result exists and are hashed into the strategy id. It never replaces Kim, Sam or D-primary-rank3."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict

from .engine import Strategy
from .rng import Rng, derive_seed
from . import strategies as S

SCHEMA = "indicator-schema-v2"
CONDITIONS = ["clean", "delayed", "missing", "revised", "contradictory"]
# indicator vocabulary an outside observer could use (not scenario labels); +1 means commodity-bullish evidence
INDICATOR_OF = {"oil": "inventory_surprise", "diesel": "inventory_surprise", "natgas": "inventory_surprise", "freight": "inventory_surprise",
                "fertilizer": "input_cost_index", "crop": "crop_condition_change", "food": "stocks_to_use_revision"}
SIGN = {"crop": -1}          # better crop condition is commodity-bearish
PARAMS = {"window_sessions": 20, "threshold": 2.0, "tilt": 0.03, "risk_assets": ["SPY", "EFA", "EEM", "VNQ"], "commodity": "DBC", "min_conf": 0.4}
RELEASE_PERIOD = {"inventory_surprise": 5, "input_cost_index": 21, "crop_condition_change": 5, "stocks_to_use_revision": 21}


@dataclass(frozen=True)
class Indicator:
    obs_id: str
    thread_id: str
    indicator: str
    kind: str                 # release | revision | contradiction
    direction: int
    magnitude_bucket: int
    confidence: float
    release_idx: int
    ingest_idx: int
    revision: int
    schema: str = SCHEMA

    def to_json(self):
        return asdict(self)


def _bucket(x):
    x = abs(x)
    return 0 if x < .04 else 1 if x < .12 else 2 if x < .30 else 3


def _oid(*p):
    return hashlib.sha256("|".join(map(str, p)).encode()).hexdigest()[:16]


class IndicatorFeed:
    def __init__(self, world, condition: str = "clean"):
        assert condition in CONDITIONS
        self.condition, self.n = condition, world.n
        seed = derive_seed(world.seed, "indicator-v2")                      # identical raw feed for every condition; degradation draws are separate
        rng = Rng(seed); drng = Rng(derive_seed(world.seed, "indicator-v2-degrade", condition))
        obs = []
        for k, ev in enumerate(world.hidden.events):
            cand = {f: a for f, a in ev["amps"].items() if f in INDICATOR_OF}
            if not cand:
                continue
            f, amp = max(cand.items(), key=lambda kv: abs(kv[1]))
            ind = INDICATOR_OF[f]; per = RELEASE_PERIOD[ind]
            true_dir = (1 if amp > 0 else -1) * SIGN.get(f, 1)
            thread = _oid(seed, "t", k)
            raw = ev["t0"] + rng.int_below(4)
            rel = ((raw + per - 1) // per) * per                              # next scheduled release date
            first_dir, first_mag = true_dir, _bucket(amp)
            if ev["variant"] == "reversed":
                first_dir = -true_dir
            conf = .75 + .2 * rng.u()
            obs.append(Indicator(_oid(seed, "r", k), thread, ind, "release", first_dir, first_mag, conf, rel, rel + rng.int_below(2), 0))
            if ev["variant"] in ("reversed", "muted") or rng.u() < .2:
                rev = rel + per * (1 + rng.int_below(3))
                obs.append(Indicator(_oid(seed, "v", k), thread, ind, "revision", true_dir, _bucket(amp), min(.97, conf + .05), rev, rev + rng.int_below(2), 1))
        if condition == "delayed":
            obs = [Indicator(**{**o.to_json(), "ingest_idx": o.ingest_idx + 3}) for o in obs]
        elif condition == "missing":
            obs = [o for o in obs if drng.u() >= .40]
        elif condition == "revised":                                          # every first print is preliminary: one bucket off, 25% wrong direction
            out = []
            for o in obs:
                if o.kind == "release":
                    d = -o.direction if drng.u() < .25 else o.direction
                    out.append(Indicator(**{**o.to_json(), "direction": d, "magnitude_bucket": max(0, o.magnitude_bucket - 1)}))
                    per = RELEASE_PERIOD[o.indicator]
                    fix = o.release_idx + per * (1 + drng.int_below(2))
                    if not any(x.thread_id == o.thread_id and x.kind == "revision" for x in obs):
                        out.append(Indicator(_oid(o.obs_id, "fix"), o.thread_id, o.indicator, "revision", o.direction, o.magnitude_bucket, o.confidence, fix, fix + 1, 1))
                out.append(o) if o.kind == "revision" else None
            obs = out
        elif condition == "contradictory":
            extra = []
            for o in obs:
                if o.kind == "release" and drng.u() < .5:
                    d = o.release_idx + drng.int_below(6)
                    extra.append(Indicator(_oid(o.obs_id, "x"), o.thread_id, o.indicator, "contradiction", -o.direction, max(0, o.magnitude_bucket - 1), .6, d, d + drng.int_below(2), 0))
            obs = obs + extra
        self._obs = sorted(obs, key=lambda o: (o.ingest_idx, o.obs_id))

    def visible(self, as_of_idx: int):
        return [o for o in self._obs if o.ingest_idx <= as_of_idx]


def stress_score(feed: IndicatorFeed, idx: int, p=PARAMS) -> float:
    """commodity-bullish evidence in the last window; per thread only the latest ingested revision counts; contradictions count at their stated confidence"""
    vis = [o for o in feed.visible(idx) if idx - o.ingest_idx <= p["window_sessions"]]
    latest = {}
    for o in vis:
        if o.kind in ("release", "revision"):
            cur = latest.get(o.thread_id)
            if cur is None or o.revision > cur.revision or (o.revision == cur.revision and o.ingest_idx >= cur.ingest_idx):
                latest[o.thread_id] = o
    score = sum(o.direction * (o.magnitude_bucket + 1) * o.confidence for o in latest.values() if o.confidence >= p["min_conf"])
    score += sum(o.direction * (o.magnitude_bucket + 1) * o.confidence for o in vis if o.kind == "contradiction" and o.thread_id not in latest)
    return score


def overlay_weights(base: dict | None, score: float, max_asset_weight: float, p=PARAMS):
    if base is None:
        return None
    w = dict(base); t = p["tilt"]; c = p["commodity"]; ra = [s for s in p["risk_assets"] if w.get(s, 0) > 0]
    if score >= p["threshold"] and ra:
        move = min(t, sum(w[s] for s in ra), max_asset_weight - w.get(c, 0.0))
        if move > 0:
            tot = sum(w[s] for s in ra)
            for s in ra: w[s] -= move * w[s] / tot
            w[c] = w.get(c, 0.0) + move
    elif score <= -p["threshold"] and w.get(c, 0) > 0:
        w[c] = max(0.0, w[c] - t)                                             # reduce commodities; the freed weight stays in cash
    return w


def strategy_id(base_genome_id: str) -> str:
    return "evt_" + hashlib.sha256(json.dumps({"family": "EVT_AWARE_v1", "schema": SCHEMA, "parent": base_genome_id, "params": PARAMS}, sort_keys=True).encode()).hexdigest()


def event_aware_strategy(name: str, family: str, genome: dict, world, feed: IndicatorFeed) -> Strategy:
    base = S.strategy_for(name + "_base", family, genome)
    nw = world.n_warmup

    def weights(h, step, base=base, feed=feed, nw=nw):
        w = base.weights(h, step)
        return overlay_weights(w, stress_score(feed, nw + step), base.max_asset_weight)
    return Strategy(name, strategy_id(base.genome_id), base.due, weights, base.window, base.max_asset_weight, base.max_total_exposure, base.halt)
