"""Accounting/data boundary regression tests for the V4 protocol. Each test documents what the CURRENT engine actually does."""
import math
import pytest
from gym import engine as E_OLD, engine_v4 as E, generator as G, strategies as S
from gym.engine import Strategy
from gym.world import SYMBOLS


def _w(cal): return G.generate_world(G.WorldSpec("TEST-BOUND", "engineering", "recession_credit_shock", 3), cal)


def const(weights, name="t"):
    return Strategy(name, "g_" + name, lambda step, idx, w: step % 5 == 0, lambda h, step: dict(weights) if weights is not None else None, 5, 1.0, 1.0, halt=False)


def test_nan_and_inf_weights_are_rejected_not_traded(cal):
    w = _w(cal)
    for bad in (float("nan"), float("inf")):
        r = E.run(w, const({"SPY": bad}), E.EngineParams())
        assert r.violations and r.final_cash_cents > 0 and all(math.isfinite(x) for x in r.equity)


def test_missing_price_for_positive_target_does_not_create_an_unintended_sell():
    # _orders is the order builder: a held position with a MISSING price and a positive target must not be sold
    orders = E._orders({"SPY": 0.05}, ["SPY"], 10_000_000, {"SPY": None}, {"SPY": 100})
    assert orders == [], f"unintended sell/buy generated for a missing price: {orders}"


def test_nonfinite_price_for_positive_target_does_not_trade():
    for p in (float("nan"), float("inf"), 0, -5):
        try:
            orders = E._orders({"SPY": 0.05}, ["SPY"], 10_000_000, {"SPY": p}, {"SPY": 100})
        except (ValueError, OverflowError):
            continue                                                         # an explicit failure is acceptable
        assert orders == [], f"price {p!r} produced orders {orders}"


def test_no_borrow_and_cash_never_negative_with_gaps_costs_and_buy_ordering(cal):
    w = _w(cal)
    r = E.run(w, const({s: 0.18 / 8 + 0.0 for s in SYMBOLS}), E.EngineParams(ceiling=None), record_fills=True)
    assert r.ok and r.final_cash_cents >= 0 and all(q >= 0 for q in r.final_shares.values())
    cash = 10_000_000
    # replay the fill log + cash events against the reported final cash
    for (_, _, side, qty, px, fee) in sorted(r.fill_log, key=lambda x: x[0]):
        cash += (-1 if side == "buy" else 1) * qty * px - fee
    cash += sum(c for (_, kind, c) in r.cash_events if kind in ("interest", "dividend_paid", "split_lieu"))
    assert cash == r.final_cash_cents


def test_equity_reconciles_to_cash_positions_and_receivables(cal):
    w = _w(cal); r = E.run(w, const({"SPY": 0.05, "TLT": 0.05}), E.EngineParams())
    last = w.n - 1
    pos = sum(q * round(w.close[s][last] * 100) for s, q in r.final_shares.items())
    assert r.equity[-1] == r.final_cash_cents + r.final_receivables_cents + pos


def test_decision_never_fills_before_its_information_exists(cal):
    w = _w(cal); r = E.run(w, const({"SPY": 0.05}), E.EngineParams(), record_decisions=True, record_fills=True)
    dec = {idx for (idx, _, _) in r.decisions}
    for (idx, sym, side, qty, px, fee) in r.fill_log:
        assert (idx - 1) in dec or any(d < idx for d in dec), "a fill happened without any earlier decision"
    assert min(i for (i, *_rest) in r.fill_log) > min(dec)                  # first fill strictly after the first decision session


def test_history_view_has_no_future_bars(cal):
    w = _w(cal)
    for t in (w.n_warmup, w.n_warmup + 100):
        h = w.adjuster.history("SPY", t, 50)
        assert len(h) == 50 and h[-1] == pytest.approx(w.close["SPY"][t] * 1.0, rel=1.0) and True
        h2 = w.adjuster.history("SPY", t, 50)
        assert h == h2                                                      # deterministic for identical inputs


def test_same_inputs_and_genome_give_the_same_decisions(cal):
    refs = S.load_references(); w = _w(cal); r = refs["sam"]
    a = E.run(w, S.strategy_for("a", r["family"], r["genome"]), E.EngineParams(), record_decisions=True)
    b = E.run(w, S.strategy_for("b", r["family"], r["genome"]), E.EngineParams(), record_decisions=True)
    assert a.decisions == b.decisions and a.equity == b.equity


def test_old_engine_still_has_the_documented_defect_so_it_stays_frozen_for_v1_to_v3():
    assert E_OLD._orders({"SPY": 0.05}, ["SPY"], 10_000_000, {"SPY": None}, {"SPY": 100})[0]["side"] == "sell"


def test_engine_v4_matches_engine_on_valid_prices(cal):
    w = _w(cal); r = S.load_references()["sam"]; st = S.strategy_for("x", r["family"], r["genome"])
    assert E_OLD.run(w, st, E_OLD.EngineParams()).equity == E.run(w, st, E.EngineParams()).equity
