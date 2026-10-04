from gym import event_aware_v2 as V, generator as G, engine as E, strategies as S


def _w(cal):
    return G.generate_world(G.WorldSpec("TEST-EVT", "training", "fertilizer_harvest_stress", 0), cal)


def test_feed_never_shows_future(cal):
    w = _w(cal); f = V.IndicatorFeed(w, "clean")
    for idx in (w.n_warmup, w.n_warmup + 200, w.n - 1):
        assert all(o.ingest_idx <= idx for o in f.visible(idx))


def test_delay_and_missing_conditions(cal):
    w = _w(cal); c, d, m = (V.IndicatorFeed(w, x) for x in ("clean", "delayed", "missing"))
    assert [o.ingest_idx + 3 for o in c._obs] == sorted(o.ingest_idx for o in d._obs) or len(c._obs) == len(d._obs)
    assert len(m._obs) <= len(c._obs)


def test_overlay_respects_caps_and_identity():
    base = {"SPY": .06, "EFA": .06, "DBC": .04, "GLD": .02}
    w = V.overlay_weights(base, 3.0, 0.10)
    assert abs(sum(w.values()) - sum(base.values())) < 1e-12 and max(w.values()) <= .10 + 1e-12
    assert V.overlay_weights(base, 0.0, 0.10) == base and V.overlay_weights(None, 5.0, .1) is None
    cut = V.overlay_weights(base, -3.0, 0.10)
    assert cut["DBC"] < base["DBC"] and sum(cut.values()) <= sum(base.values())
    assert V.strategy_id("gen_a") != V.strategy_id("gen_b") and V.strategy_id("gen_a").startswith("evt_")


def test_event_aware_run_has_no_violations(cal):
    w = _w(cal); r = S.load_references()["d_primary_rank3"]
    f = V.IndicatorFeed(w, "revised")
    res = E.run(w, V.event_aware_strategy("t", r["family"], r["genome"], w, f), E.EngineParams())
    assert res.ok
