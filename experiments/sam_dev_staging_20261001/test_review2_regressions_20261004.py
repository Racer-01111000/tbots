"""Second external review of 67fe142 (activation-blocking findings). Each case below was reproduced
red against 67fe142 before the fix."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
from kim_order_logic import IntendedOrderSpec, PersistentIntentLedger
from test_halt_liquidation_20261004 import arm, pos, order, advance_only
from test_runner_integration_20261004 import make, state, evaluated, advance


def total_sell_qty(fake):
    return sum(int(o["qty"]) for o in fake.orders if o["side"] == "sell" and o["status"] not in ("canceled", "expired", "rejected"))


# ---- Finding 1: halt recovery must reserve quantity ACROSS all pending + uncertain sells ----
def test_two_uncertain_8_share_sells_against_10_held_are_never_both_sent(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 10)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-09-30-TLT", "TLT", 8, "sell"), "2026-09-30", 8)
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 8, "sell"), "2026-10-01", 8)
    rec = k.run_session(deps)
    assert total_sell_qty(fake) <= 10, f"oversold: {total_sell_qty(fake)} committed against 10 held"
    assert rec["status"] == "halt_liquidation_held" and rec["escalate"] is True


def test_uncertain_8_share_sell_plus_existing_pending_8_share_sell_is_not_recovered(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 10)])
    fake.orders.append(order("TLT", "sell", 8))               # already working at the broker
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 8, "sell"), "2026-10-01", 8)
    rec = k.run_session(deps)
    assert len(fake.post_log) == 0 and total_sell_qty(fake) <= 10
    assert rec["status"] == "halt_liquidation_held"


def test_recovery_that_fits_in_aggregate_still_works(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 20)])
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-09-30-TLT", "TLT", 8, "sell"), "2026-09-30", 8)
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-TLT", "TLT", 8, "sell"), "2026-10-01", 8)
    k.run_session(deps)
    assert sorted(b["client_order_id"] for b in fake.post_log if not b["client_order_id"].endswith("-liq")) == \
        ["kim-2026-09-30-TLT", "kim-2026-10-01-TLT"]
    assert total_sell_qty(fake) <= 20


# ---- Finding 2: an unresolved earlier submission must block NEW orders in the normal path ----
def test_monday_accepted_but_invisible_buy_is_not_duplicated_on_tuesday(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.hide_new_for = 99                                     # the broker has it; we cannot see it
    fake.fail["post"] = "timeout_after_accept_once"
    k.run_session(deps)                                        # Monday
    monday = len(fake.orders)
    assert monday >= 1
    advance_only(fake)                                         # Tuesday, new date => new date-based client_order_id
    rec = k.run_session(deps)
    assert len(fake.orders) == monday, "Tuesday created a second broker order while Monday's was unresolved"
    assert rec["status"] == "held_unresolved_prior_submission" and rec["escalate"] is True
    assert evaluated(tmp_path) is None


def test_prior_session_unresolved_is_resolved_by_lookup_then_trading_resumes(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.hide_new_for = 2
    fake.fail["post"] = "timeout_after_accept_once"
    k.run_session(deps)
    n = len(fake.orders)
    advance_only(fake)
    for _ in range(3):
        k.run_session(deps)
    assert len(fake.orders) == n                               # still exactly Monday's order(s)


def test_prior_unresolved_buy_is_never_resubmitted_from_a_stale_session(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-02-SPY", "SPY", 290, "buy"), "2026-10-02", 8)   # crashed before send
    rec = k.run_session(deps)                                  # today is 2026-10-05
    assert fake.post_log == [] and rec["status"] == "held_unresolved_prior_submission"
    assert rec["unresolved_submissions"] == ["kim-2026-10-02-SPY"]


def test_operator_can_abandon_an_unresolved_row_after_checking_the_broker(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-02-SPY", "SPY", 290, "buy"), "2026-10-02", 8)
    assert k.run_session(deps)["status"] == "held_unresolved_prior_submission"
    ledger.abandon("kim-2026-10-02-SPY", "operator verified at broker: no such order")
    assert ledger.get("kim-2026-10-02-SPY")["status"] == "abandoned_by_operator"
    assert k.run_session(deps)["status"] == "decided" and fake.post_log
    with pytest.raises(Exception):
        ledger.abandon("kim-2026-10-02-SPY", "again")          # only unresolved rows can be abandoned


# ---- Finding 3: 'flat' must say what it covers ----
def test_strategy_universe_flat_is_not_account_flat(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40), pos("AAPL", 10)])
    k.run_session(deps)
    advance_only(fake); fake.fill_all()
    rec = k.run_session(deps)
    assert rec["halt_strategy_universe_flat"] is True
    assert rec["halt_account_flat"] is False
    assert rec["escalate"] is True and rec["halt_liquidation"]["non_universe_positions"] == ["AAPL"]
    assert state(tmp_path, "halt_state.json")["account_flat"] is False
    assert "halt_flat_confirmed" not in rec


def test_account_flat_true_only_when_nothing_at_all_is_held_or_working(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, [pos("TLT", 40)])
    k.run_session(deps)
    advance_only(fake); fake.fill_all()
    rec = k.run_session(deps)
    assert rec["halt_strategy_universe_flat"] is True and rec["halt_account_flat"] is True


# ---- boundary cases for the aggregate reservation (pure) ----
import session_guards as sg


def U(cid, sym, qty):
    return {"client_order_id": cid, "symbol": sym, "qty": qty, "side": "sell", "status": "uncertain_unresolved"}


def test_reservation_exact_fit_is_allowed_one_over_is_not():
    held = [pos("TLT", 10)]
    assert sg.reserve_sell_recoveries([U("a", "TLT", 4), U("b", "TLT", 6)], held, [], []) == []
    assert sg.reserve_sell_recoveries([U("a", "TLT", 4), U("b", "TLT", 7)], held, [], []) == ["b"]


def test_reservation_counts_broker_pending_and_ledger_only_confirmed_sells():
    held = [pos("TLT", 10)]
    assert sg.reserve_sell_recoveries([U("a", "TLT", 8)], held, [order("TLT", "sell", 8)], []) == ["a"]
    ledger_only = [{"client_order_id": "c", "symbol": "TLT", "side": "sell", "qty": 8, "filled_qty": 0, "status": "accepted"}]
    assert sg.reserve_sell_recoveries([U("a", "TLT", 8)], held, [], ledger_only) == ["a"]
    partial = [order("TLT", "sell", 8, filled=6, status="partially_filled")]      # only 2 still committed
    assert sg.reserve_sell_recoveries([U("a", "TLT", 8)], [pos("TLT", 10)], partial, []) == []


def test_reservation_is_per_symbol_and_never_lets_one_symbol_borrow_from_another():
    held = [pos("TLT", 10), pos("GLD", 100)]
    assert sg.reserve_sell_recoveries([U("a", "TLT", 8), U("b", "GLD", 90)], held, [], []) == []
    assert sg.reserve_sell_recoveries([U("a", "TLT", 11), U("b", "GLD", 5)], held, [], []) == ["a"]


def test_uncertain_sell_for_a_symbol_not_held_at_all_is_refused():
    assert sg.reserve_sell_recoveries([U("a", "SPY", 1)], [pos("TLT", 10)], [], []) == ["a"]


def test_flat_status_scopes():
    assert sg.flat_status([pos("AAPL", 10)], [], ["TLT"]) == (True, False)
    assert sg.flat_status([], [order("AAPL", "buy", 1)], ["TLT"]) == (True, False)
    assert sg.flat_status([pos("TLT", 1)], [], ["TLT"]) == (False, False)
    assert sg.flat_status([], [order("TLT", "sell", 1, status="canceled")], ["TLT"]) == (True, True)
    assert sg.flat_status([], [], ["TLT"]) == (True, True)
