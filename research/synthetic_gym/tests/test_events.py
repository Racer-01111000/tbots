import copy
import json

import pytest

from gym import events as EV, generator as G
from gym.world import SYMBOLS

HIDDEN_WORDS = ["supply_disruption", "refinery_squeeze", "reopening", "reserve_release", "demand_destruction", "input_cost_shock",
                "recession_onset", "policy_response", "tech_boom", "tech_reversal", "false_breakout", "inflation_surprise", "rate_hike_cycle",
                "energy_shipping_squeeze", "fertilizer_harvest_stress", "stagflation_rates", "recession_credit_shock", "technology_boom_reversal",
                "normalization_whipsaw", "priced_in", "muted", "reversed", "severity", "variant", "amps", "regime", "stress"]


@pytest.fixture(scope="module")
def world_feed(cal):
    w = G.generate_world(G.WorldSpec("TEST-NEWS", "engineering", "energy_shipping_squeeze", 2), cal)
    return w, EV.NewsFeed(w)


def test_nothing_is_visible_before_its_ingest_session(world_feed):
    w, feed = world_feed
    for t in (0, 50, 400, 900, w.n - 1):
        vis = feed.visible(t)
        assert all(o.ingest_idx <= t for o in vis)
    all_obs = feed._obs
    assert any(o.ingest_idx > 800 for o in all_obs) and not any(o.ingest_idx > 800 for o in feed.visible(800))


def test_visible_set_only_grows_over_time(world_feed):
    _, feed = world_feed
    prev = set()
    for t in range(0, 1500, 37):
        cur = {o.obs_id for o in feed.visible(t)}
        assert prev <= cur
        prev = cur


def test_agent_view_exposes_no_hidden_parameter_label_or_future(world_feed):
    w, feed = world_feed
    for t in (300, 700, 1100):
        v = EV.agent_view(w, feed, t)
        blob = json.dumps(v)
        for word in HIDDEN_WORDS:
            assert word not in blob, word
        assert all(len(v["prices"][s]) <= 300 for s in SYMBOLS)
        assert v["prices"]["SPY"][-1] == w.close["SPY"][t]
        assert v["date"] == w.dates[t].isoformat()
        assert set(v) == {"as_of_idx", "date", "prices", "news", "schema"}


def test_a_late_correction_is_invisible_until_its_own_ingest_session(world_feed):
    _, feed = world_feed
    corr = [o for o in feed._obs if o.kind == "correction"]
    assert corr
    o = corr[0]
    assert o.obs_id not in {x.obs_id for x in feed.visible(o.ingest_idx - 1)} and o.obs_id in {x.obs_id for x in feed.visible(o.ingest_idx)}
    # the original report of the same thread is visible earlier, with a lower revision
    first = [x for x in feed._obs if x.thread_id == o.thread_id and x.kind == "report"]
    assert first and first[0].ingest_idx <= o.ingest_idx and first[0].revision < o.revision


def test_feed_contains_rumors_corrections_duplicates_contradictions_and_missing_coverage(cal):
    kinds, covered, total = set(), 0, 0
    for i in range(8):
        w = G.generate_world(G.WorldSpec("TEST-NEWS2", "engineering", "recession_credit_shock", i), cal)
        feed = EV.NewsFeed(w)
        kinds |= {o.kind for o in feed._obs}
        threads = {o.thread_id for o in feed._obs if o.kind in ("report", "correction")}
        total += len(w.hidden.events); covered += len(threads)
    assert {"report", "rumor", "correction", "duplicate", "contradiction"} <= kinds
    assert covered < total                       # some real events are never reported


def test_first_report_can_be_wrong_and_is_later_corrected(cal):
    from gym.rng import derive_seed
    checked = 0
    for i in range(30):
        w = G.generate_world(G.WorldSpec("TEST-NEWS3", "engineering", "stagflation_rates", i), cal)
        feed = EV.NewsFeed(w)
        seed = derive_seed(w.seed, "news")
        for k, ev in enumerate(w.hidden.events):
            if ev["variant"] != "reversed":
                continue
            thread = EV._opaque(seed, "thread", k)
            reps = [o for o in feed._obs if o.thread_id == thread and o.kind == "report"]
            cors = [o for o in feed._obs if o.thread_id == thread and o.kind == "correction"]
            if not reps:
                continue                                       # uncovered event
            main = max(ev["amps"].items(), key=lambda kv: abs(kv[1]))[1]
            true_dir = 1 if main > 0 else -1
            assert reps[0].direction == -true_dir              # the initial report points the wrong way
            assert cors and cors[0].direction == true_dir and cors[0].ingest_idx > reps[0].first_published_idx and cors[0].revision == 1
            checked += 1
    assert checked >= 2


def test_same_world_gives_the_same_feed_and_a_different_world_a_different_one(cal):
    w = G.generate_world(G.WorldSpec("TEST-NEWS4", "engineering", "normalization_whipsaw", 0), cal)
    a, b = EV.NewsFeed(w), EV.NewsFeed(w)
    assert [o.to_json() for o in a._obs] == [o.to_json() for o in b._obs]
    w2 = G.generate_world(G.WorldSpec("TEST-NEWS4", "engineering", "normalization_whipsaw", 1), cal)
    assert [o.obs_id for o in a._obs] != [o.obs_id for o in EV.NewsFeed(w2)._obs]


def test_existing_genomes_run_without_any_news_object_so_storing_news_does_not_make_them_news_aware(cal):
    from gym import engine as E, strategies as S
    refs = S.load_references()
    w = G.generate_world(G.WorldSpec("TEST-NEWS5", "engineering", "technology_boom_reversal", 0), cal)
    for name in ("champion", "sam", "d_primary_rank3"):
        r = E.run(w, S.strategy_for(name, refs[name]["family"], refs[name]["genome"]), E.EngineParams())
        assert r.ok and r.equity
    import inspect
    assert "news" not in inspect.signature(E.run).parameters and "feed" not in inspect.signature(E.run).parameters
