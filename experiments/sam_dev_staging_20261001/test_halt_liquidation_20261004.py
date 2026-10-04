"""Quantity-based liquidation under a latched halt (2026-10-04 GO). No price is needed to sell
held shares, so missing bars must not strand a halted account. Sells only; never oversell;
fresh broker truth; ledger/cap/kill switch/duplicate protection unchanged."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
import session_guards as sg
from kim_order_logic import IntendedOrderSpec, PersistentIntentLedger
from test_runner_integration_20261004 import make, state, evaluated, advance

UNI = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
ALL_MISSING = set(UNI)


def arm(tmp_path, fake, positions, session_zero="2026-10-05"):
    """A latched halt with the persisted state a real run would have left behind."""
    fake.positions = [dict(p) for p in positions]
    (tmp_path / "halt_state.json").write_text(json.dumps({"halted": True, "halted_session_date": "2026-10-01"}))
    (tmp_path / "peak_equity_state.json").write_text(json.dumps(
        {"peak_equity": 100000.0, "peak_session_date": "2026-10-01", "history": {"2026-10-01": 100000.0}}))
    (tmp_path / "cadence_state.json").write_text(json.dumps(
        {"session_zero_date": session_zero, "last_evaluated_session_date": "2026-10-01"}))


def pos(sym, qty):
    return {"symbol": sym, "qty": str(qty)}


def order(sym, side, qty, filled=0, status="accepted", cid=None):
    return {"id": f"o-{sym}-{side}", "client_order_id": cid or f"x-{sym}-{side}", "symbol": sym, "qty": str(qty),
            "side": side, "status": status, "filled_qty": str(filled)}


# ================================================================ planner (pure)
def plan(positions, orders=(), ledger=()):
    return sg.plan_halt_liquidation(positions, list(orders), list(ledger), UNI)


def test_sells_full_long_quantity_and_nothing_else():
    o, n = plan([pos("TLT", 40), pos("SPY", 7), pos("GLD", 0)])
    assert o == [{"symbol": "SPY", "side": "sell", "shares": 7}, {"symbol": "TLT", "side": "sell", "shares": 40}]


def test_never_buys_never_shorts_and_flags_a_short_for_a_human():
    o, n = plan([pos("TLT", -5), pos("SPY", 3)])
    assert [x["symbol"] for x in o] == ["SPY"] and all(x["side"] == "sell" for x in o)
    assert n["short_positions"] == ["TLT"]


def test_non_universe_holdings_are_ignored_but_reported():
    o, n = plan([pos("AAPL", 10), pos("SPY", 1)])
    assert [x["symbol"] for x in o] == ["SPY"] and n["non_universe_positions"] == ["AAPL"]


def test_pending_sell_quantity_is_subtracted():
    o, _ = plan([pos("TLT", 40)], [order("TLT", "sell", 10)])
    assert o == [{"symbol": "TLT", "side": "sell", "shares": 30}]


def test_partially_filled_pending_sell_commits_only_its_remainder():
    o, _ = plan([pos("TLT", 25)], [order("TLT", "sell", 40, filled=15, status="partially_filled")])
    assert o == []            # 25 held, 25 remaining already committed -> nothing more to sell


def test_never_oversells_when_pending_sells_cover_or_exceed_the_position():
    assert plan([pos("TLT", 10)], [order("TLT", "sell", 10)])[0] == []
    assert plan([pos("TLT", 10)], [order("TLT", "sell", 15)])[0] == []
    assert plan([pos("TLT", 10)], [order("TLT", "sell", 6), order("TLT", "sell", 6, cid="other")])[0] == []


def test_ledger_known_sell_not_yet_visible_at_the_broker_still_counts_as_committed():
    row = {"client_order_id": "kim-2026-10-05-TLT-liq", "symbol": "TLT", "side": "sell", "qty": 40, "filled_qty": 0,
           "status": "accepted"}
    assert plan([pos("TLT", 40)], [], [row])[0] == []


def test_same_order_in_broker_list_and_ledger_is_counted_once():
    row = {"client_order_id": "dup", "symbol": "TLT", "side": "sell", "qty": 10, "filled_qty": 0, "status": "accepted"}
    o, _ = plan([pos("TLT", 40)], [order("TLT", "sell", 10, cid="dup")], [row])
    assert o == [{"symbol": "TLT", "side": "sell", "shares": 30}]


def test_pending_buys_are_reported_not_cancelled_and_do_not_reduce_the_sell():
    o, n = plan([pos("SPY", 5)], [order("SPY", "buy", 9)])
    assert o == [{"symbol": "SPY", "side": "sell", "shares": 5}] and n["pending_buys"] == ["SPY"]


def test_terminal_orders_do_not_commit_quantity():
    o, _ = plan([pos("TLT", 40)], [order("TLT", "sell", 40, status="canceled"), order("TLT", "sell", 40, filled=40, status="filled")])
    assert o == [{"symbol": "TLT", "side": "sell", "shares": 40}]


def test_fractional_residual_is_floored_and_flagged():
    o, n = plan([pos("TLT", 3.4)])
    assert o == [{"symbol": "TLT", "side": "sell", "shares": 3}] and n["fractional_residual"]["TLT"] == pytest.approx(0.4)


def test_liquidation_specs_refuse_a_buy_or_zero_quantity():
    with pytest.raises(sg.OrderGuardError):
        sg.build_liquidation_specs([{"symbol": "SPY", "side": "buy", "shares": 1}], "2026-10-05")
    with pytest.raises(sg.OrderGuardError):
        sg.build_liquidation_specs([{"symbol": "SPY", "side": "sell", "shares": 0}], "2026-10-05")
    s = sg.build_liquidation_specs([{"symbol": "SPY", "side": "sell", "shares": 2}], "2026-10-05")
    assert s[0].client_order_id == "kim-2026-10-05-SPY-liq" and s[0].side == "sell" and s[0].time_in_force == "day"


# ================================================================ runner: core behaviour
def test_missing_prices_do_not_prevent_liquidation(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40), pos("GLD", 10)])
    fake.missing_bars = ALL_MISSING
    rec = k.run_session(deps)
    assert rec["status"] == "halted"
    assert {(b["symbol"], b["side"], b["qty"]) for b in fake.post_log} == {("TLT", "sell", "40"), ("GLD", "sell", "10")}
    assert all(b["time_in_force"] == "day" and b["type"] == "market" for b in fake.post_log)
    assert all(b["client_order_id"].endswith("-liq") for b in fake.post_log)


def test_halt_stays_latched_and_flat_is_confirmed_from_the_broker_not_assumed(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    r1 = k.run_session(deps)
    assert r1["halt_strategy_universe_flat"] is False                  # position still held + our sell still open
    assert state(tmp_path, "halt_state.json")["halted"] is True
    advance(fake, deps)                                        # fills the sell
    r2 = k.run_session(deps)
    assert r2["halt_strategy_universe_flat"] is True and r2["status"] == "halted"
    h = state(tmp_path, "halt_state.json")
    assert h["halted"] is True and h["strategy_universe_flat"] is True and h["unflat_sessions"] == 0
    fake.set_equity(100_000.0)                                 # recovery never un-halts, and nothing is bought
    n = len(fake.post_log)
    advance(fake, deps)
    r3 = k.run_session(deps)
    assert r3["status"] == "halted" and len(fake.post_log) == n and r3["halt_strategy_universe_flat"] is True


def test_partial_fill_then_next_session_only_sells_what_is_still_uncommitted(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    k.run_session(deps)
    liq = fake.orders[0]
    liq["status"], liq["filled_qty"] = "partially_filled", "15"       # 15 sold, 25 still working
    fake.positions = [pos("TLT", 25)]
    n = len(fake.post_log)
    advance_only(fake)
    r = k.run_session(deps)
    assert len(fake.post_log) == n, "oversold: the remaining 25 are already committed to the working sell"
    assert r["halt_strategy_universe_flat"] is False and state(tmp_path, "halt_state.json")["halted"] is True
    liq["status"], liq["filled_qty"] = "filled", "40"
    fake.positions = []
    advance_only(fake)
    assert k.run_session(deps)["halt_strategy_universe_flat"] is True


def advance_only(fake):
    i = fake.sessions.index(fake.today)
    fake.today = fake.sessions[i + 1]


def test_unfilled_liquidation_order_is_replaced_next_session_without_overselling(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    k.run_session(deps)
    fake.orders[0]["status"] = "expired"                       # day order died unfilled
    advance_only(fake)
    k.run_session(deps)
    sells = [b for b in fake.post_log]
    assert [b["qty"] for b in sells] == ["40", "40"] and sells[0]["client_order_id"] != sells[1]["client_order_id"]
    assert len(fake.orders) == 2 and sum(1 for o in fake.orders if o["status"] == "accepted") == 1


def test_existing_pending_sell_from_before_the_halt_is_respected(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    fake.orders.append(order("TLT", "sell", 10))
    k.run_session(deps)
    assert [b["qty"] for b in fake.post_log] == ["30"]


def test_a_pending_buy_is_flagged_and_never_cancelled_or_added_to(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("SPY", 5)])
    fake.orders.append(order("SPY", "buy", 9))
    rec = k.run_session(deps)
    assert rec["escalate"] is True and rec["halt_liquidation"]["pending_buys"] == ["SPY"]
    assert all(b["side"] == "sell" for b in fake.post_log)


def test_a_short_position_is_never_bought_back_and_is_escalated(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", -5), pos("SPY", 3)])
    rec = k.run_session(deps)
    assert {b["symbol"] for b in fake.post_log} == {"SPY"} and rec["escalate"] is True


# ================================================================ runner: hold conditions
def test_unreadable_positions_hold_all_submissions_and_flag(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    fake.fail["positions"] = 500          # fails the very first broker-state read -> abstains before sizing anything
    rec = k.run_session(deps)
    assert fake.post_log == [] and rec["status"].startswith("abstained")


def test_fresh_reconciliation_failure_holds_even_if_the_first_read_succeeded(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    real = fake.get_json
    calls = {"n": 0}
    def flaky(url, headers):
        if "/v2/orders?status=open" in url:
            return 503, None
        return real(url, headers)
    deps.get_json = flaky
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and rec["escalate"] is True and fake.post_log == []
    assert evaluated(tmp_path) == "2026-10-01"          # the session is NOT marked done; it will be retried
    deps.get_json = real
    assert k.run_session(deps)["status"] == "halted" and len(fake.post_log) == 1


def test_ledger_lookup_failure_holds(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-old", "TLT", 3, "sell"), "2026-10-01", 8)
    ledger.update_status("kim-old", "id", "accepted", 0)
    fake.fail["lookup"] = 503
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and fake.post_log == []


def test_an_unresolved_earlier_BUY_is_never_resubmitted_and_holds_everything(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-SPY", "SPY", 5, "buy"), "2026-10-01", 8)   # crashed mid-send
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and rec["unresolved_submissions"] == ["kim-2026-10-01-SPY"]
    assert fake.post_log == [] and rec["escalate"] is True


def test_kill_switch_blocks_liquidation_too(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    (tmp_path / "KILL_SWITCH").write_text("stop")
    rec = k.run_session(deps)
    assert fake.post_log == [] and rec["submission"]["startup_blocked"] is True and rec["escalate"] is True
    assert state(tmp_path, "halt_state.json")["halted"] is True and evaluated(tmp_path) == "2026-10-01"
    (tmp_path / "KILL_SWITCH").unlink()
    assert k.run_session(deps)["status"] == "halted" and len(fake.post_log) == 1


def test_order_cap_still_applies_and_the_remainder_waits_for_the_next_session(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"max_orders_per_session": 1})
    arm(tmp_path, fake, [pos("TLT", 40), pos("GLD", 10)])
    rec = k.run_session(deps)
    assert len(fake.post_log) == 1 and rec["submission"]["blocked"] == 1 and rec["escalate"] is True
    advance_only(fake); fake.fill_all()
    k.run_session(deps)
    assert {b["symbol"] for b in fake.post_log} == {"TLT", "GLD"} and len(fake.orders) == 2


# ================================================================ runner: timeouts + restart recovery
def test_timeout_where_the_broker_never_got_it_is_recovered_with_the_same_id_exactly_once(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    fake.fail["post"] = "timeout"
    r1 = k.run_session(deps)
    assert r1["submission"]["results"][0]["outcome"] == "uncertain_unresolved" and fake.orders == []
    assert evaluated(tmp_path) == "2026-10-01"                 # not marked done
    del fake.fail["post"]
    r2 = k.run_session(deps)                                   # recovery: lookup 404 -> same id resubmitted
    assert len(fake.orders) == 1 and fake.orders[0]["qty"] == "40" and fake.orders[0]["side"] == "sell"
    assert r2["status"] == "halted" and evaluated(tmp_path) == "2026-10-05"


def test_timeout_after_the_broker_accepted_with_delayed_visibility_never_double_sells(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    fake.hide_new_for = 4
    fake.fail["post"] = "timeout_after_accept_once"
    k.run_session(deps)
    assert len(fake.orders) == 1
    for _ in range(2):
        k.run_session(deps)                                    # still invisible: hold / same-id retry only
        assert len(fake.orders) == 1, "second broker order created while the first was invisible"
    k.run_session(deps)                                        # now visible: adopted
    assert len(fake.orders) == 1 and sum(int(o["qty"]) for o in fake.orders) == 40


def test_crash_after_sending_before_state_persist_does_not_double_sell_on_restart(tmp_path, monkeypatch):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    real = k.write_json_atomic
    def boom(path, obj):
        if Path(path).name == "halt_state.json":
            raise RuntimeError("simulated crash after the send")
        return real(path, obj)
    monkeypatch.setattr(k, "write_json_atomic", boom)
    with pytest.raises(RuntimeError):
        k.run_session(deps)
    sent = len(fake.post_log)
    assert sent == 1
    monkeypatch.setattr(k, "write_json_atomic", real)
    k.run_session(deps)                                        # restart: same -liq id, plus the working sell is committed
    assert len(fake.post_log) == sent and len(fake.orders) == 1
    assert state(tmp_path, "halt_state.json")["halted"] is True


def test_rerun_on_the_same_session_does_not_resend(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    k.run_session(deps)
    assert k.run_session(deps)["status"] == "already_evaluated" and len(fake.post_log) == 1


# ================================================================ runner: not live
def test_shadow_mode_plans_but_sends_nothing_and_writes_no_ledger(tmp_path):
    fake, deps, _ = make(tmp_path, gate=False)
    arm(tmp_path, fake, [pos("TLT", 40)])
    rec = k.run_session(deps)
    assert rec["intended_orders_not_submitted"] == [{"symbol": "TLT", "side": "sell", "shares": 40}]
    assert fake.post_log == [] and PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3")).count_all() == 0


def test_validate_only_writes_nothing_and_sends_nothing(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, validate_only=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    before = {n: (tmp_path / n).read_text() for n in ("halt_state.json", "cadence_state.json", "peak_equity_state.json")}
    rec = k.run_session(deps)
    assert fake.post_log == [] and rec["status"] == "halted"
    assert {n: (tmp_path / n).read_text() for n in before} == before
    assert not (tmp_path / "order_ledger.sqlite3").exists()


def test_persistent_unflat_is_escalated_after_two_sessions(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    k.run_session(deps)
    fake.orders[0]["status"] = "accepted"
    advance_only(fake)
    r2 = k.run_session(deps)                                   # still held, working sell commits it: nothing new, not flat
    assert r2["halt_strategy_universe_flat"] is False and r2["escalate"] is True
    assert state(tmp_path, "halt_state.json")["unflat_sessions"] == 2


def test_a_stale_unresolved_sell_larger_than_the_position_is_not_recovered(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 10)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 40, "sell"), "2026-10-01", 8)   # crashed before send
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and rec["oversell_risk"] == ["kim-2026-10-01-TLT"]
    assert fake.post_log == [] and rec["escalate"] is True


def test_a_unresolved_sell_that_fits_the_position_is_recovered_with_its_own_id(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 40, "sell"), "2026-10-01", 8)
    k.run_session(deps)
    assert [b["client_order_id"] for b in fake.post_log] == ["kim-2026-10-01-TLT"] and len(fake.orders) == 1
