"""Timestamped event/news interface (schema news-schema-v1).

HIDDEN world state (scenario family, event kinds, severities, variants, factor paths) never leaves `HiddenState`.
Agents see only `Observation`s, each with: source id, topic, report kind, claimed subject/direction/magnitude bucket,
first-publication session, ingest session (when it becomes visible), and a revision sequence. The feed includes rumors,
corrections, duplicates, contradictions and missing coverage. `NewsFeed.visible(as_of)` returns only what has been
INGESTED by `as_of`, at the revision level known by then (a later correction, or the final revised value, is invisible
until its own ingest session).

NO existing genome (champion, Sam, D-primary-rank3) reads this feed: they are price-driven and are run unchanged on
price inputs. Storing news does not make them news-aware; an event-aware lineage would need its own schema and identity."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict

from .rng import Rng, derive_seed

SCHEMA = "news-schema-v1"
SOURCES = ["wire_a", "wire_b", "trade_press", "agency_x", "blogger_q", "official_notice"]
# generic news topic/subject per hidden factor -- a vocabulary an outside observer could use, not a scenario label
SUBJECT = {"oil": "crude_oil", "diesel": "refined_fuel", "natgas": "gas", "fertilizer": "fertilizer", "freight": "shipping", "crop": "harvest",
           "food": "food_prices", "inflation": "inflation", "yield": "interest_rates", "credit": "credit_conditions", "usd": "currency",
           "tech": "technology_sector", "liquidity": "market_liquidity", "growth": "economic_activity"}
SOURCE_RELIABILITY = {"wire_a": .90, "wire_b": .85, "trade_press": .75, "agency_x": .90, "blogger_q": .50, "official_notice": .95}


@dataclass(frozen=True)
class Observation:
    obs_id: str                 # opaque
    thread_id: str              # opaque id grouping the reports/revisions of one real-world matter
    source_id: str
    subject: str
    kind: str                   # report | rumor | correction | duplicate | contradiction
    direction: int              # -1 / 0 / +1 as claimed
    magnitude_bucket: int       # 0..3 as claimed
    confidence: float
    first_published_idx: int
    ingest_idx: int
    revision: int               # increases with each correction of the same thread/source
    schema: str = SCHEMA

    def to_json(self):
        return asdict(self)


def _opaque(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _bucket(x):
    x = abs(x)
    return 0 if x < .04 else 1 if x < .12 else 2 if x < .30 else 3


class NewsFeed:
    def __init__(self, world, seed_tag: str = "news"):
        self.n = world.n
        self._obs = self._build(world, derive_seed(world.seed, seed_tag))
        self._obs.sort(key=lambda o: (o.ingest_idx, o.obs_id))

    # ---------------------------------------------------------------- construction (uses hidden state; output is opaque)
    def _build(self, world, seed):
        rng = Rng(seed)
        out = []
        h = world.hidden
        for k, ev in enumerate(h.events):
            main = max(ev["amps"].items(), key=lambda kv: abs(kv[1]))
            factor, amp = main
            true_dir = 1 if amp > 0 else -1
            thread = _opaque(seed, "thread", k)
            covered = rng.u() < .80
            t0 = ev["t0"]
            # rumor before the event (coarse, low confidence, often wrong) -- allowed anticipatory information
            if rng.u() < .30:
                src = SOURCES[rng.int_below(len(SOURCES))]
                pub = max(0, t0 - 1 - rng.int_below(10))
                d = true_dir if rng.u() < .65 else -true_dir
                out.append(Observation(_opaque(seed, "rumor", k), thread, src, SUBJECT[factor], "rumor", d, 1 + rng.int_below(2), .25 + .2 * rng.u(),
                                       pub, pub + rng.int_below(2), 0))
            if not covered:
                continue
            src = SOURCES[rng.int_below(len(SOURCES))]
            pub = t0 + rng.int_below(6)
            claimed_dir = true_dir
            claimed_mag = _bucket(amp)
            if ev["variant"] == "reversed":
                claimed_dir = -true_dir * 1                    # the initial report claims the (wrong) obvious reading
            if ev["variant"] == "muted":
                claimed_mag = min(3, claimed_mag + 1)
            rel = SOURCE_RELIABILITY[src]
            out.append(Observation(_opaque(seed, "report", k), thread, src, SUBJECT[factor], "report", claimed_dir, claimed_mag,
                                   rel * (.8 + .2 * rng.u()), pub, pub + rng.int_below(3), 0))
            # corrections: always for reversed/muted events, sometimes otherwise
            if ev["variant"] in ("reversed", "muted") or rng.u() < .25:
                cpub = pub + 3 + rng.int_below(28)
                out.append(Observation(_opaque(seed, "corr", k), thread, src, SUBJECT[factor], "correction", true_dir, _bucket(amp),
                                       min(.97, rel + .05), cpub, cpub + rng.int_below(3), 1))
            if rng.u() < .25:                                  # duplicate report from another outlet
                src2 = SOURCES[rng.int_below(len(SOURCES))]
                dpub = pub + rng.int_below(3)
                out.append(Observation(_opaque(seed, "dup", k), thread, src2, SUBJECT[factor], "duplicate", claimed_dir, claimed_mag,
                                       SOURCE_RELIABILITY[src2] * .9, dpub, dpub + rng.int_below(3), 0))
            if rng.u() < .15:                                  # contradictory evidence
                src3 = SOURCES[rng.int_below(len(SOURCES))]
                cpub = pub + rng.int_below(6)
                out.append(Observation(_opaque(seed, "contra", k), thread, src3, SUBJECT[factor], "contradiction", -claimed_dir, max(0, claimed_mag - 1),
                                       SOURCE_RELIABILITY[src3] * .6, cpub, cpub + rng.int_below(3), 0))
        # pure noise: rumors with no event behind them
        for j in range(3 + rng.int_below(6)):
            subj = list(SUBJECT.values())[rng.int_below(len(SUBJECT))]
            pub = rng.int_below(self.n)
            out.append(Observation(_opaque(seed, "noise", j), _opaque(seed, "noise-thread", j), SOURCES[rng.int_below(len(SOURCES))], subj, "rumor",
                                   1 if rng.u() < .5 else -1, 1, .2 + .15 * rng.u(), pub, pub + rng.int_below(2), 0))
        return out

    # ---------------------------------------------------------------- the ONLY agent-facing read
    def visible(self, as_of_idx: int) -> list:
        """observations ingested on or before `as_of_idx`; a thread's latest INGESTED revision supersedes older ones only in the
        sense that both remain listed with their revision numbers -- nothing newer than as_of is ever returned"""
        return [o for o in self._obs if o.ingest_idx <= as_of_idx]

    def count(self):
        return len(self._obs)


def agent_view(world, feed: NewsFeed, as_of_idx: int, window: int = 300) -> dict:
    """what an event-aware bot may see at the close of session `as_of_idx`: prices up to and including that close (as-of adjusted),
    and ingested news. No hidden state, no scenario label, nothing later."""
    from .world import SYMBOLS
    return {"as_of_idx": as_of_idx, "date": world.dates[as_of_idx].isoformat(),
            "prices": {s: world.adjuster.history(s, as_of_idx, window) for s in SYMBOLS},
            "news": [o.to_json() for o in feed.visible(as_of_idx)], "schema": SCHEMA}
