"""Accounting proofs: parity with the accepted evaluator, cash conservation (independent ledger), reservations,
no assumed sell proceeds, deferrals, splits, overlays, halts, matched controls, look-ahead."""
import copy
import importlib.util
import math
from datetime import date
from pathlib import Path

import pytest

from gym import engine as E, generator as G, strategies as S, world as W
from gym.world import CorporateAction, SYMBOLS

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def harness():
    import sys
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    spec = importlib.util.spec_from_file_location("replay_harness", REPO / "experiments/sam_15_month_replay_20261001/replay_harness.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def refs():
    return S.load_references()


def plain_world(cal, family="normalization_whipsaw", i=0, dividends=False):
    w = G.generate_world(G.WorldSpec("TEST-ENG", "engineering", family, i), cal)
    w.actions = [a for a in w.actions if (a.kind == "dividend" and dividends)]
    return w.finalize()


def harness_rows(w, with_div):
    div = {(a.symbol, a.ex_idx): a.amount for a in w.actions if a.kind == "dividend"}
    rows = {}
    for s in SYMBOLS:
        rows[s] = []
        for i in range(w.n):
            ca = '{"dividend_amount": %r}' % div[(s, i)] if (with_div and (s, i) in div) else None
            rows[s].append({"timestamp": w.dates[i].isoformat(), "open": w.open[s][i], "high": w.high[s][i], "low": w.low[s][i],
                            "close": w.close[s][i], "adjusted_close": w.adjuster.history(s, i, 1)[0] if False else w.close[s][i],
                            "volume": w.volume[s][i], "corporate_action": ca})
    return rows


PARAMS_SIMPLE = dict(interest=False, cost=E.CostCase(10, 5))


@pytest.mark.parametrize("name", ["sam", "d_primary_rank3", "champion"])
def test_gym_engine_equals_the_accepted_evaluator_without_corporate_actions(cal, harness, refs, name):
    w = plain_world(cal)
    ref = refs[name]
    rows = harness_rows(w, False)
    ws, we = w.dates[w.n_warmup].isoformat(), w.dates[-1].isoformat()
    START = 10_000_000
    if ref["family"] == "D":
        acc = harness.simulate(None, per_symbol_rows=rows, window_start=ws, window_end=we, max_asset_weight=0.18, max_total_exposure=0.18,
                               starting_cash_cents=START, slippage_bps=10, use_decide_D=True, genome_for_D=ref["genome"], ceiling=0.18)
    else:
        acc = harness.simulate(harness.champion_decide_fn_factory(ref["genome"]), per_symbol_rows=rows, window_start=ws, window_end=we,
                               max_asset_weight=0.18, max_total_exposure=0.18, starting_cash_cents=START, slippage_bps=10, ceiling=0.18)
    mine = E.run(w, S.strategy_for(name, ref["family"], ref["genome"]), E.EngineParams(ceiling=0.18, **PARAMS_SIMPLE))
    assert mine.ok
    assert mine.equity[-1] == acc["final_equity_cents"]
    assert mine.fills == acc["fill_count"] and mine.orders == acc["order_count"]
    assert mine.commission_cents + mine.slippage_cents == acc["transaction_cost_cents"]
    assert mine.traded_notional_cents / START == acc["turnover"]


def fixed_weights_strategy(weights, every=21):
    return E.Strategy("fixed", "fixed", lambda step, idx, w: step % every == 0, lambda h, step: dict(weights), 2, 1.0, 1.0, halt=False)


def test_dividend_receivable_accounting_equals_the_accepted_ex_date_credit_in_equity(cal, harness):
    w = plain_world(cal, dividends=True)
    rows = harness_rows(w, True)
    wts = {"SPY": .2, "TLT": .2, "IEF": .2, "VNQ": .1, "EFA": .1}
    ws, we = w.dates[w.n_warmup].isoformat(), w.dates[-1].isoformat()
    acc = harness.simulate(lambda view, step: dict(wts) if step % 21 == 0 else None, per_symbol_rows=rows, window_start=ws, window_end=we,
                           max_asset_weight=1.0, max_total_exposure=1.0, starting_cash_cents=10_000_000, slippage_bps=10)
    mine = E.run(w, fixed_weights_strategy(wts), E.EngineParams(ceiling=None, **PARAMS_SIMPLE))
    assert mine.ok and mine.dividends_cents > 0
    assert mine.equity[-1] == acc["final_equity_cents"]            # cash-at-pay-date + receivable == credit-at-ex-date, in equity
    assert acc["dividend_cents"] == mine.dividends_cents          # same total distributed, no double credit


def audit_cash(w, res, start_cents):
    """independent ledger: rebuild final cash from the fill log and cash events only"""
    cash = start_cents
    for (_, _, side, qty, px, fee) in res.fill_log:
        cash += (-1 if side == "buy" else 1) * qty * px - fee
    cash += sum(c for (_, kind, c) in res.cash_events)
    return cash


def test_cash_conservation_against_an_independent_ledger_with_dividends_interest_and_splits(cal):
    for i in range(6):
        w = G.generate_world(G.WorldSpec("TEST-ENG", "engineering", "energy_shipping_squeeze", i), cal)
        for strat in (S.passive18(), fixed_weights_strategy({"SPY": .3, "TLT": .3, "GLD": .2, "DBC": .2})):
            res = E.run(w, strat, E.EngineParams(ceiling=None), record_fills=True)
            assert res.ok, res.violations
            assert audit_cash(w, res, 10_000_000) == res.final_cash_cents
            assert min(x for x in res.equity) > 0


def test_equity_identity_start_plus_pnl_flows_minus_costs(cal):
    w = G.generate_world(G.WorldSpec("TEST-ENG", "engineering", "stagflation_rates", 1), cal)
    res = E.run(w, S.passive18(), E.EngineParams(ceiling=None), record_fills=True)
    last = {s: round(w.close[s][-1] * 100) for s in SYMBOLS}
    pos = sum(q * last[s] for s, q in res.final_shares.items())
    assert res.equity[-1] == res.final_cash_cents + res.final_receivables_cents + pos


def test_interest_accrues_identically_on_cash_for_every_portfolio(cal):
    w = plain_world(cal)
    res = E.run(w, S.cash_control(), E.EngineParams(ceiling=None), record_fills=True)
    cash = 10_000_000
    for idx in range(w.n_warmup, w.n):
        cash += round(cash * w.policy_rate[idx] / 252.0)
    assert res.final_cash_cents == cash and res.fills == 0 and res.interest_cents == cash - 10_000_000


def test_passive_control_is_just_another_strategy_through_the_same_code_path(cal):
    w = plain_world(cal, dividends=True)
    a = E.run(w, S.passive18(), E.EngineParams())
    clone = S.passive18(); clone.name = "clone"
    b = E.run(w, clone, E.EngineParams())
    assert a.equity == b.equity and a.fills == b.fills and a.commission_cents == b.commission_cents


def test_passive18_rebalances_on_month_starts_and_holds_18_percent_gross(cal):
    w = plain_world(cal)
    res = E.run(w, S.passive18(), E.EngineParams(), record_decisions=True)
    months = [i for i in range(w.n_warmup, w.n) if i in w._month_starts]
    assert [d[0] for d in res.decisions][0] == w.n_warmup and len(res.decisions) >= 58
    assert all(abs(sum(d[2].values()) - 0.18) < 1e-9 for d in res.decisions)
    assert 0.15 < sum(res.invested[-5:]) / 5 < 0.21


def test_unaffordable_gap_buy_is_reduced_to_cash_and_never_creates_negative_cash(cal):
    w = plain_world(cal)
    greedy = E.Strategy("greedy", "greedy", lambda s, i, wd: s == 0, lambda h, s: {"SPY": 1.0}, 2, 1.0, 1.0, halt=False)
    w2 = copy.deepcopy(w)
    i0 = w2.n_warmup + 1
    w2.open["SPY"][i0] = w2.close["SPY"][i0 - 1] * 1.08                  # an 8% gap-up above the sizing close
    w2.high["SPY"][i0] = max(w2.high["SPY"][i0], w2.open["SPY"][i0], w2.close["SPY"][i0]); w2.finalize()
    res = E.run(w2, greedy, E.EngineParams(ceiling=None, interest=False), record_fills=True)
    assert res.ok and res.unaffordable_reductions == 1
    assert res.final_cash_cents >= 0 and res.final_shares["SPY"] > 0
    assert audit_cash(w2, res, 10_000_000) == res.final_cash_cents


def test_buys_reserve_cash_in_order_and_later_buys_are_reduced_not_funded_by_imaginary_proceeds(cal):
    w = plain_world(cal)
    s = E.Strategy("two", "two", lambda st, i, wd: st == 0, lambda h, st: {"EEM": .5, "EFA": .5}, 2, 1.0, 1.0, halt=False)
    w2 = copy.deepcopy(w); i0 = w2.n_warmup + 1
    for sym in ("EEM", "EFA"):
        w2.open[sym][i0] = w2.close[sym][i0 - 1] * 1.10
        w2.high[sym][i0] = max(w2.high[sym][i0], w2.open[sym][i0])
    w2.finalize()
    res = E.run(w2, s, E.EngineParams(ceiling=None, interest=False), record_fills=True)
    assert res.ok and res.unaffordable_reductions >= 1 and res.final_cash_cents >= 0
    buys = [f for f in res.fill_log if f[2] == "buy"]
    assert [f[1] for f in buys] == sorted(f[1] for f in buys)             # deterministic priority: symbol order, earlier buys keep their cash


def test_sell_proceeds_are_not_assumed_when_a_sell_is_capped_by_volume(cal):
    w = plain_world(cal)
    def weights(h, st): return {"SPY": 0.5} if st == 0 else ({} if st == 21 else None)
    strat = E.Strategy("churn", "churn", lambda st, i, wd: st in (0, 21), weights, 2, 1.0, 1.0, halt=False)
    w2 = copy.deepcopy(w)
    idx_sell = w2.n_warmup + 22
    w2.volume["SPY"][idx_sell] = 1000                                      # tiny volume => the sell is capped, remainder deferred
    w2.finalize()
    res = E.run(w2, strat, E.EngineParams(ceiling=None, interest=False, participation_cap=0.05), record_fills=True)
    assert res.ok and res.deferred >= 1
    sells = [f for f in res.fill_log if f[2] == "sell"]
    assert sells[0][3] <= 50                                              # 5% of 1000 shares
    assert audit_cash(w2, res, 10_000_000) == res.final_cash_cents


def test_never_oversell_and_deferred_sells_are_dropped_after_the_age_limit(cal):
    w = plain_world(cal)
    strat = E.Strategy("x", "x", lambda st, i, wd: st in (0, 3), lambda h, st: {"SPY": 0.5} if st == 0 else {}, 2, 1.0, 1.0, halt=False)
    w2 = copy.deepcopy(w)
    for k in range(w2.n_warmup, w2.n_warmup + 40):
        w2.volume["SPY"][k] = 10                                          # chronically illiquid
    w2.finalize()
    res = E.run(w2, strat, E.EngineParams(ceiling=None, interest=False, participation_cap=0.05, max_defer_sessions=2), record_fills=True)
    assert res.ok and all(q >= 0 for q in res.final_shares.values())
    assert res.dropped_after_defer >= 1


def test_split_scales_shares_pending_orders_and_pays_cash_in_lieu(cal):
    w = plain_world(cal)
    i = w.n_warmup + 30
    w2 = copy.deepcopy(w)
    for k in ("open", "high", "low", "close"):
        for j in range(i, w2.n):
            getattr(w2, k)["SPY"][j] = getattr(w2, k)["SPY"][j] / 1.5
    for j in range(i, w2.n):
        w2.volume["SPY"][j] = int(w2.volume["SPY"][j] * 1.5)
    w2.actions = [CorporateAction("split", "SPY", i, None, 0.0, 3, 2)]
    w2.finalize()
    strat = E.Strategy("h", "h", lambda st, ix, wd: st == 0, lambda h, st: {"SPY": 0.5}, 2, 1.0, 1.0, halt=False)
    res = E.run(w2, strat, E.EngineParams(ceiling=None, interest=False), record_fills=True)
    assert res.ok and res.final_shares["SPY"] > 0
    bought = sum(f[3] for f in res.fill_log if f[2] == "buy")
    assert res.final_shares["SPY"] in (bought * 3 // 2,)                 # whole shares after the split (fraction paid in cash)
    assert audit_cash(w2, res, 10_000_000) == res.final_cash_cents
    # value continuity: equity does not jump at the split
    j = i - w.n_warmup
    assert abs(res.equity[j] / res.equity[j - 1] - 1) < 0.08


def test_halt_latches_and_liquidates_after_a_crash(cal):
    w = plain_world(cal)
    w2 = copy.deepcopy(w)
    start = w2.n_warmup + 60
    for s in SYMBOLS:
        for k in ("open", "high", "low", "close"):
            arr = getattr(w2, k)[s]
            for j in range(start, w2.n):
                arr[j] *= 0.5
    w2.finalize()
    all_in = E.Strategy("allin", "allin", lambda st, i, wd: st == 0, lambda h, st: {s: 1 / 8 for s in SYMBOLS}, 2, 1.0, 1.0, halt=True)
    res = E.run(w2, all_in, E.EngineParams(ceiling=None, interest=False))
    assert res.halted and res.final_shares == {} and res.ok


def test_stress_overlays_hit_every_strategy_identically_and_are_counted(cal):
    w = plain_world(cal)
    ov = E.StressOverlay(partial_fill_prob=.3, reject_prob=.1, stale_feed_prob=.2, symbol_halt_prob=.1, outage_prob=.02, gap_shock_bps=50)
    a = E.run(w, S.passive18(), E.EngineParams(overlay=ov))
    b = E.run(w, S.passive18(), E.EngineParams(overlay=ov))
    assert a.equity == b.equity                                           # deterministic
    assert a.partial_fills + a.rejected + a.stale_skips + a.outage_sessions + a.halted_symbol_blocks > 0
    base = E.run(w, S.passive18(), E.EngineParams())
    assert base.rejected == base.partial_fills == base.stale_skips == 0 and a.ok


def test_spread_floor_is_a_max_not_an_addition(cal):
    w = plain_world(cal)
    cheap = E.run(w, S.passive18(), E.EngineParams(cost=E.CostCase(10, 5, spread_floor=False)))
    floor = E.run(w, S.passive18(), E.EngineParams(cost=E.CostCase(10, 5, spread_floor=True)))
    assert floor.slippage_cents >= cheap.slippage_cents
    # worlds' half-spreads are mostly below the 10 bps allowance, so the floor rarely binds => costs stay close (no double count)
    assert floor.slippage_cents < cheap.slippage_cents * 1.5


def test_decisions_depend_only_on_information_available_at_the_decision_date(cal, refs):
    w = G.generate_world(G.WorldSpec("TEST-LOOK", "engineering", "technology_boom_reversal", 0), cal)
    cut = w.n_warmup + 400
    w2 = copy.deepcopy(w)
    for s in SYMBOLS:
        for arr in (w2.open[s], w2.high[s], w2.low[s], w2.close[s]):
            for j in range(cut + 1, w2.n):
                arr[j] *= 1.9
    w2.actions = [a for a in w2.actions if a.ex_idx <= cut]
    w2.finalize()
    for name in ("champion", "sam"):
        r1 = E.run(w, S.strategy_for(name, refs[name]["family"], refs[name]["genome"]), E.EngineParams(), record_decisions=True)
        r2 = E.run(w2, S.strategy_for(name, refs[name]["family"], refs[name]["genome"]), E.EngineParams(), record_decisions=True)
        d1 = [d for d in r1.decisions if d[0] <= cut]; d2 = [d for d in r2.decisions if d[0] <= cut]
        assert d1 and d1 == d2, name
        assert r1.equity[:cut - w.n_warmup + 1] == r2.equity[:cut - w.n_warmup + 1]


def test_history_given_to_a_strategy_equals_the_asof_adjuster_and_ends_at_the_decision_close(cal):
    w = G.generate_world(G.WorldSpec("TEST-LOOK", "engineering", "recession_credit_shock", 1), cal)
    seen = []
    def wts(h, step):
        seen.append({s: list(h[s]) for s in SYMBOLS}); return None
    strat = E.Strategy("probe", "probe", lambda st, i, wd: st % 40 == 0, wts, 120, 1.0, 1.0, halt=False)
    E.run(w, strat, E.EngineParams(), record_decisions=False)
    assert seen
    for k, h in enumerate(seen):
        idx = w.n_warmup + 40 * k
        for s in SYMBOLS:
            assert h[s] == w.adjuster.history(s, idx, 120) and len(h[s]) == 120 and h[s][-1] == w.close[s][idx]
