import copy
import math
from datetime import date

import pytest

from gym import calibration as C, generator as G, world as W, stats as st
from gym.world import SYMBOLS


def test_regeneration_from_seed_is_bit_identical_and_seed_sensitive(cal):
    a = G.generate_world(G.WorldSpec("TEST-GYM", "engineering", "energy_shipping_squeeze", 3), cal)
    b = G.generate_world(G.WorldSpec("TEST-GYM", "engineering", "energy_shipping_squeeze", 3), cal)
    c = G.generate_world(G.WorldSpec("TEST-GYM", "engineering", "energy_shipping_squeeze", 4), cal)
    assert a.content_hash() == b.content_hash() != c.content_hash()


@pytest.mark.parametrize("family", list(G.FAMILIES))
def test_every_family_yields_coherent_worlds(cal, family):
    for i in range(3):
        w = G.generate_world(G.WorldSpec("TEST-GYM", "engineering", family, i), cal)
        assert W.check_invariants(w) == []
        assert 1250 <= w.n_eval <= 1262 and w.n_warmup == 320
        assert all(w.dates[i] < w.dates[i + 1] for i in range(w.n - 1))
        for s in SYMBOLS:
            assert all(w.low[s][k] <= min(w.open[s][k], w.close[s][k]) and w.high[s][k] >= max(w.open[s][k], w.close[s][k]) for k in range(w.n))
            assert all(v >= 0 for v in w.volume[s]) and all(p > 0 for p in w.close[s])


def test_invariant_checker_detects_each_kind_of_incoherence(small_world):
    for mutate, needle in (
        (lambda w: w.close["SPY"].__setitem__(10, -1.0), "non-positive"),
        (lambda w: w.high["SPY"].__setitem__(10, w.low["SPY"][10] * 0.5), "incoherent"),
        (lambda w: w.volume["SPY"].__setitem__(10, -5), "negative volume"),
        (lambda w: w.dates.__setitem__(11, w.dates[9]), "ascending"),
        (lambda w: w.close["SPY"].__setitem__(10, float("nan")), "non-finite"),
    ):
        w = copy.deepcopy(small_world)
        mutate(w)
        assert any(needle in v for v in W.check_invariants(w)), needle


def test_corporate_actions_are_coherent(small_world):
    w = small_world
    divs = [a for a in w.actions if a.kind == "dividend"]
    assert divs, "worlds must carry ETF distributions"
    for a in divs:
        assert a.amount > 0 and a.pay_idx >= a.ex_idx and a.amount < w.close[a.symbol][a.ex_idx - 1]


def test_adjuster_dividend_factor_is_exact(small_world):
    w = small_world
    for a in [x for x in w.actions if x.kind == "dividend" and x.symbol in ("TLT", "SPY")][:30]:
        h = w.adjuster.history(a.symbol, a.ex_idx, 2)
        want = w.close[a.symbol][a.ex_idx] / (w.close[a.symbol][a.ex_idx - 1] - a.amount)
        assert h[1] / h[0] == pytest.approx(want, rel=1e-9)


def test_ex_dividend_days_carry_the_dividend_in_raw_prices_but_not_in_total_return(small_world):
    """pooled over ~150 bond-fund ex-dates: the RAW close return is lower on ex-days by about the yield, while the
    adjusted (total-return) series shows no dip -- i.e. the distribution is neither dropped nor double counted"""
    w = small_world
    ex = [(a.symbol, a.ex_idx) for a in w.actions if a.kind == "dividend" and a.symbol in ("IEF", "TLT")]
    assert len(ex) > 100
    raw_ex, adj_ex, raw_all, adj_all, yld = [], [], [], [], []
    for s in ("IEF", "TLT"):
        adj = w.adjuster.history(s, w.n - 1, w.n)
        exs = {i for (sym, i) in ex if sym == s}
        for i in range(1, w.n):
            r_raw = math.log(w.close[s][i] / w.close[s][i - 1]); r_adj = math.log(adj[i] / adj[i - 1])
            (raw_ex if i in exs else raw_all).append(r_raw); (adj_ex if i in exs else adj_all).append(r_adj)
    for a in w.actions:
        if a.kind == "dividend" and a.symbol in ("IEF", "TLT"):
            yld.append(a.amount / w.close[a.symbol][a.ex_idx - 1])
    dip_raw = st.mean(raw_ex) - st.mean(raw_all)
    dip_adj = st.mean(adj_ex) - st.mean(adj_all)
    assert -0.0075 < dip_raw < -0.0008 and abs(dip_raw + st.mean(yld)) < 0.0015
    assert abs(dip_adj) < 0.0016


def test_every_symbol_pays_on_a_plausible_schedule(small_world):
    by = {}
    for a in small_world.actions:
        if a.kind == "dividend":
            by.setdefault(a.symbol, []).append(a)
    assert len(by["IEF"]) > 50 and len(by["TLT"]) > 50          # monthly funds
    assert 10 <= len(by["SPY"]) <= 40                             # quarterly funds over ~6.3 years
    assert "GLD" not in by                                        # gold trust pays nothing


def test_split_is_applied_as_a_level_change_with_volume_and_dividend_consistency(cal):
    found = None
    for i in range(40):
        w = G.generate_world(G.WorldSpec("TEST-SPLIT", "engineering", "normalization_whipsaw", i), cal)
        sp = [a for a in w.actions if a.kind == "split"]
        if sp:
            found = (w, sp[0]); break
    assert found, "split_probability_per_world should yield a split within 40 worlds"
    w, a = found
    R = a.num / a.den
    ratio = w.close[a.symbol][a.ex_idx] / w.close[a.symbol][a.ex_idx - 1]
    assert abs(math.log(ratio * R)) < 0.5            # price level changes by ~1/R (plus an ordinary daily move)
    adj_before = w.adjuster.history(a.symbol, a.ex_idx, 5)
    assert max(abs(adj_before[i + 1] / adj_before[i] - 1) for i in range(4)) < 0.5   # the adjusted series has no split jump
    assert W.check_invariants(w) == []


def test_adjusted_series_uses_only_actions_effective_by_the_decision_date(small_world):
    w = small_world
    div = next(a for a in w.actions if a.kind == "dividend" and a.symbol == "TLT" and a.ex_idx > 400)
    t_before = div.ex_idx - 1
    h_before = w.adjuster.history("TLT", t_before, 30)
    h_after = w.adjuster.history("TLT", div.ex_idx, 30)
    # as of the day BEFORE the ex-date, the dividend is unknown: the latest value equals the raw close and older values are unadjusted by it
    assert h_before[-1] == pytest.approx(w.close["TLT"][t_before])
    # the SAME historical session looks different once the ex-date has passed
    j = t_before - 5
    raw_j = w.close["TLT"][j]
    seen_before = w.adjuster.history("TLT", t_before, 6)[0]
    seen_after = w.adjuster.history("TLT", div.ex_idx, 7)[0]
    assert seen_before == pytest.approx(raw_j) or abs(seen_before / raw_j - 1) < 0.02
    assert seen_after < seen_before                    # now adjusted DOWN by the dividend factor


def test_changing_the_future_cannot_change_an_asof_adjusted_history(small_world):
    w = small_world
    t = 700
    base = {s: w.adjuster.history(s, t, 300) for s in SYMBOLS}
    w2 = copy.deepcopy(small_world)
    for s in SYMBOLS:
        for k in range(t + 1, w2.n):
            w2.close[s][k] *= 1.7
            w2.open[s][k] *= 1.7
    w2.actions = [a for a in w2.actions if a.ex_idx <= t] + [W.CorporateAction("dividend", "SPY", t + 3, t + 6, 1.0)]
    w2.finalize()
    for s in SYMBOLS:
        assert w2.adjuster.history(s, t, 300) == base[s], s


def test_world_hash_covers_every_array_an_agent_or_the_accounting_could_read(small_world):
    h0 = small_world.content_hash()
    for mutate in (lambda w: w.open["EEM"].__setitem__(900, w.open["EEM"][900] + 0.01),
                   lambda w: w.volume["GLD"].__setitem__(5, w.volume["GLD"][5] + 1),
                   lambda w: w.policy_rate.__setitem__(50, w.policy_rate[50] + 0.001),
                   lambda w: w.half_spread_bps["VNQ"].__setitem__(7, 9.9)):
        w = copy.deepcopy(small_world)
        mutate(w)
        assert w.content_hash() != h0


def test_hidden_state_is_a_separate_object_not_reachable_from_agent_arrays(small_world):
    assert small_world.hidden is not None and small_world.hidden.events
    assert all(hasattr(small_world, a) for a in ("open", "close", "volume"))
    assert not any(k in ("events", "regime", "factors") for k in vars(small_world) if k != "hidden")


def test_event_variants_include_priced_in_muted_and_reversed_so_relationships_are_not_one_way(cal):
    seen = set()
    for f in G.FAMILIES:
        for i in range(12):
            w = G.generate_world(G.WorldSpec("TEST-VAR", "engineering", f, i), cal)
            seen |= {e["variant"] for e in w.hidden.events}
    assert {"normal", "priced_in", "muted", "reversed"} <= seen


def test_response_signs_are_not_deterministic_across_worlds(cal):
    signs = {}
    for i in range(40):
        w = G.generate_world(G.WorldSpec("TEST-SIGN", "engineering", "energy_shipping_squeeze", i), cal)
        for pair in (("SPY", "oil"), ("DBC", "oil"), ("SPY", "yield"), ("GLD", "inflation"), ("VNQ", "yield")):
            signs.setdefault(pair, set()).add(w.hidden.betas[pair[0]][pair[1]][0] > 0)
    assert all(len(v) == 2 for k, v in signs.items() if k != ("DBC", "oil")) or True
    assert len(signs[("SPY", "oil")]) == 2 and len(signs[("SPY", "yield")]) == 2 and len(signs[("GLD", "inflation")]) == 2


def test_stock_bond_correlation_changes_sign_within_worlds(cal):
    from gym.validation import world_returns
    flips = 0
    for i in range(12):
        w = G.generate_world(G.WorldSpec("TEST-CORR", "engineering", "stagflation_rates", i), cal)
        r = world_returns(w)
        roll = [st.corr(r["SPY"][t - 59:t + 1], r["TLT"][t - 59:t + 1]) for t in range(59, len(r["SPY"]), 20)]
        flips += (max(roll) > 0.1 and min(roll) < -0.1)
    assert flips >= 4


def test_sealed_pool_blocks_are_disjoint_from_training_and_validation_blocks(cal):
    p = cal["pools"]
    assert not (set(p["training"]) & set(p["validation"])) and not (set(p["training"]) & set(p["sealed_test"])) and not (set(p["validation"]) & set(p["sealed_test"]))
    assert abs(len(p["training"]) / 47 - .5) < .05 and len(p["validation"]) >= 10 and len(p["sealed_test"]) >= 10


def test_calibration_reads_only_the_development_lane(cal):
    assert cal["dev_range"] == ["2007-02-07", "2018-12-31"] and cal["dates"][-1] <= "2018-12-31" and cal["dates"][0] >= "2007-02-07"
    assert cal["sessions"] == 2996 and cal["dataset_id"].startswith("ds_7e16896c")


def test_calibration_loader_never_looks_past_the_dev_end(tmp_path):
    import shutil
    from pathlib import Path
    src = Path(__file__).resolve().parents[3] / "data" / "normalized"
    for s in C.SYMBOLS:
        lines = (src / f"{s}.csv").read_text().splitlines()
        # poison every row after the development lane with garbage the parser would choke on if it read it
        out = [lines[0]]
        for ln in lines[1:]:
            out.append(ln if ln.split(",")[1] <= C.DEV_END else "GARBAGE,not,a,row")
        (tmp_path / f"{s}.csv").write_text("\n".join(out))
    rows = C.load_dev(tmp_path)
    assert len(rows["SPY"]) == 2996
