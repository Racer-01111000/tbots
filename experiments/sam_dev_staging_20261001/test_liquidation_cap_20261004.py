"""Bounded liquidation-cap exemption (Rick's GO): normal trading keeps the 4-per-session cap; a latched-halt
liquidation may exit more than four held symbols in one session under its OWN explicit finite limit
(max_liquidation_orders_per_session, 1..universe size), counted atomically in the ledger, with one
deterministic -liq id per (session, symbol), no oversell, no duplicates, and every unresolved-order
protection intact."""
import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
import order_limits as ol
from kim_order_logic import IntendedOrderSpec, PersistentIntentLedger, MockBroker, submit_orders_with_limits
from pilot_config import load_pilot_config, PilotConfigError
from lib.ids import genome_id
from test_halt_liquidation_20261004 import arm, pos, order, advance_only
from test_runner_integration_20261004 import make, state, evaluated

UNI = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
SIX = [pos(s, 10 + i) for i, s in enumerate(UNI[:6])]
GENOME = json.loads((HERE / "selected_development_bot.json").read_text())["selected_genome_definition"]


def sells(fake):
    return [b for b in fake.post_log if b["side"] == "sell"]


# ---------------- the exemption: more than four symbols exit in ONE session ----------------
def test_halt_liquidation_exits_six_symbols_in_one_session_despite_the_normal_cap_of_four(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"max_orders_per_session": 4, "max_liquidation_orders_per_session": 8})
    arm(tmp_path, fake, SIX)
    rec = k.run_session(deps)
    assert rec["status"] == "halted" and rec["submission"]["blocked"] == 0
    assert {b["symbol"] for b in sells(fake)} == set(UNI[:6]) and len(fake.post_log) == 6
    assert {b["symbol"]: int(b["qty"]) for b in fake.post_log} == {s["symbol"]: int(s["qty"]) for s in SIX}
    assert all(b["client_order_id"] == f"kim-2026-10-05-{b['symbol']}-liq" for b in fake.post_log)


def test_normal_trading_is_still_capped_at_four(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"max_orders_per_session": 1, "max_liquidation_orders_per_session": 8})
    fake.positions = [pos("TLT", 40), pos("VNQ", 30)]         # not halted: rotation sells exceed the normal cap of 1
    rec = k.run_session(deps)
    assert len(fake.post_log) == 1 and rec["submission"]["blocked"] >= 1


# ---------------- explicit finite limit ----------------
def test_the_liquidation_limit_is_finite_and_enforced_with_the_remainder_flagged_for_next_session(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"max_orders_per_session": 4, "max_liquidation_orders_per_session": 5})
    arm(tmp_path, fake, SIX)
    rec = k.run_session(deps)
    assert len(fake.post_log) == 5 and rec["submission"]["blocked"] == 1 and rec["escalate"] is True
    advance_only(fake); fake.fill_all()
    rec2 = k.run_session(deps)                                 # next session: only the one left, no re-sells of the five
    assert len(fake.post_log) == 6 and rec2["halt_account_flat"] is False   # the sixth sell is working, not yet filled
    assert sorted(b["symbol"] for b in fake.post_log) == sorted(UNI[:6])
    advance_only(fake); fake.fill_all()
    rec3 = k.run_session(deps)
    assert len(fake.post_log) == 6 and rec3["halt_account_flat"] is True and rec3["halt_strategy_universe_flat"] is True


def test_liquidation_limit_of_exactly_the_number_held_succeeds_and_one_less_blocks_one(tmp_path):
    for limit, blocked in ((6, 0), (5, 1)):
        d = tmp_path / f"l{limit}"
        d.mkdir()
        fake, deps, _ = make(d, gate=True, cfg={"max_liquidation_orders_per_session": limit})
        arm(d, fake, SIX)
        assert k.run_session(deps)["submission"]["blocked"] == blocked


# ---------------- independent accounting ----------------
def test_normal_and_liquidation_counts_do_not_consume_each_other(tmp_path):
    ledger = PersistentIntentLedger(str(tmp_path / "l.sqlite3"))
    for s in UNI[:4]:
        ledger.record_intent(IntendedOrderSpec(f"kim-2026-10-05-{s}", s, 1, "buy"), "2026-10-05", 4, "normal")
    with pytest.raises(ol.DailyOrderLimitReached):
        ledger.record_intent(IntendedOrderSpec("kim-2026-10-05-VNQ", "VNQ", 1, "buy"), "2026-10-05", 4, "normal")
    for s in UNI[:6]:                                           # a full normal book does not block liquidation
        ledger.record_intent(IntendedOrderSpec(f"kim-2026-10-05-{s}-liq", s, 1, "sell"), "2026-10-05", 8, "liquidation")
    assert ledger.count_for_session("2026-10-05", "normal") == 4 and ledger.count_for_session("2026-10-05", "liquidation") == 6
    assert ledger.count_for_session("2026-10-05") == 10


def test_liquidation_class_is_capped_by_its_own_limit_inside_the_atomic_transaction(tmp_path):
    ledger = PersistentIntentLedger(str(tmp_path / "l.sqlite3"))
    for s in UNI[:3]:
        ledger.record_intent(IntendedOrderSpec(f"kim-2026-10-05-{s}-liq", s, 1, "sell"), "2026-10-05", 3, "liquidation")
    with pytest.raises(ol.DailyOrderLimitReached):
        ledger.record_intent(IntendedOrderSpec("kim-2026-10-05-VNQ-liq", "VNQ", 1, "sell"), "2026-10-05", 3, "liquidation")
    assert ledger.get("kim-2026-10-05-VNQ-liq") is None


def test_concurrent_processes_cannot_exceed_the_liquidation_limit(tmp_path):
    """Spawned (not forked) workers: no worker can inherit the parent's open SQLite connection."""
    import race_helpers
    db = str(tmp_path / "race.sqlite3")
    PersistentIntentLedger(db).close()
    with mp.get_context("spawn").Pool(8) as pool:
        got = pool.map(race_helpers.record_one, [(db, i, 7, "liquidation", "2026-10-05") for i in range(24)])
    check = PersistentIntentLedger(db)
    assert sum(got) == 7 and check.count_for_session("2026-10-05", "liquidation") == 7
    check.close()


def test_concurrent_normal_class_is_capped_independently_of_liquidation(tmp_path):
    import race_helpers
    db = str(tmp_path / "race2.sqlite3")
    PersistentIntentLedger(db).close()
    jobs = [(db, i, 4, "normal", "2026-10-05") for i in range(12)] + [(db, 100 + i, 8, "liquidation", "2026-10-05") for i in range(12)]
    with mp.get_context("spawn").Pool(8) as pool:
        got = pool.map(race_helpers.record_one, jobs)
    check = PersistentIntentLedger(db)
    assert sum(got[:12]) == 4 and sum(got[12:]) == 8
    assert check.count_for_session("2026-10-05", "normal") == 4 and check.count_for_session("2026-10-05", "liquidation") == 8
    check.close()


def test_old_ledgers_without_the_class_column_migrate_and_count_as_normal(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.sqlite3")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE order_intents (client_order_id TEXT PRIMARY KEY, symbol TEXT, qty INTEGER, side TEXT, "
              "intent_recorded_at TEXT NOT NULL, broker_order_id TEXT, status TEXT, filled_qty REAL, last_updated_at TEXT, session_date TEXT)")
    c.execute("INSERT INTO order_intents VALUES ('x','SPY',1,'buy','t',NULL,'filled',1,NULL,'2026-10-05')")
    c.commit(); c.close()
    ledger = PersistentIntentLedger(db)
    assert ledger.count_for_session("2026-10-05", "normal") == 1 and ledger.count_for_session("2026-10-05", "liquidation") == 0


# ---------------- no duplicate sells, no oversell, with more than four symbols ----------------
def test_rerun_restart_and_second_pass_never_double_sell_six_symbols(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    k.run_session(deps)
    n = len(fake.post_log)
    assert k.run_session(deps)["status"] == "already_evaluated" and len(fake.post_log) == n
    fake2, deps2, _ = make(tmp_path, fake=fake, gate=True)      # "restart"
    advance_only(fake); 
    k.run_session(deps2)                                        # next session, nothing filled yet: all six working sells commit the lot
    assert len(fake.post_log) == n == 6
    held = {p["symbol"]: int(p["qty"]) for p in fake.positions}
    work = {}
    for o in fake.orders:
        work[o["symbol"]] = work.get(o["symbol"], 0) + int(o["qty"])
    assert work == held


def test_partial_fills_across_six_symbols_never_cause_an_oversell(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    k.run_session(deps)
    for o in fake.orders[:3]:
        done = int(o["qty"]) // 2
        o["status"], o["filled_qty"] = "partially_filled", str(done)
        for p in fake.positions:
            if p["symbol"] == o["symbol"]:
                p["qty"] = str(int(p["qty"]) - done)
    n = len(fake.post_log)
    advance_only(fake)
    k.run_session(deps)
    assert len(fake.post_log) == n
    for o in fake.orders:
        remaining = int(o["qty"]) - int(float(o["filled_qty"]))
        held = int(next(p["qty"] for p in fake.positions if p["symbol"] == o["symbol"]))
        assert remaining <= held


def test_pending_sells_on_some_of_six_symbols_reduce_only_those(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    fake.orders.append(order("SPY", "sell", 4))
    fake.orders.append(order("TLT", "sell", 14))                # TLT held 14 -> fully committed
    k.run_session(deps)
    got = {b["symbol"]: int(b["qty"]) for b in fake.post_log}
    assert got["SPY"] == 10 - 4 and "TLT" not in got and len(got) == 5


def test_a_buy_is_never_produced_by_the_exemption(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX + [pos("VNQ", -3)])
    k.run_session(deps)
    assert all(b["side"] == "sell" for b in fake.post_log) and "VNQ" not in {b["symbol"] for b in fake.post_log}


# ---------------- protections preserved with more than four symbols ----------------
def test_kill_switch_still_blocks_all_six(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    (tmp_path / "KILL_SWITCH").write_text("stop")
    rec = k.run_session(deps)
    assert fake.post_log == [] and rec["submission"]["startup_blocked"] is True


def test_unresolved_earlier_buy_still_holds_everything_even_with_six_to_sell(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3")).record_intent(
        IntendedOrderSpec("kim-2026-10-02-SPY", "SPY", 5, "buy"), "2026-10-02", 4, "normal")
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and fake.post_log == []


def test_aggregate_reservation_still_prevents_oversell_with_many_symbols(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    ledger.record_intent(IntendedOrderSpec("kim-2026-09-30-SPY", "SPY", 8, "sell"), "2026-09-30", 4, "normal")
    ledger.record_intent(IntendedOrderSpec("kim-2026-10-01-SPY", "SPY", 8, "sell"), "2026-10-01", 4, "normal")   # 16 > 10 held
    rec = k.run_session(deps)
    assert rec["status"] == "halt_liquidation_held" and fake.post_log == []


def test_timeout_on_one_of_six_leaves_exactly_one_order_per_symbol_after_recovery(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    arm(tmp_path, fake, SIX)
    fake.hide_new_for = 3
    fake.fail["post"] = "timeout_after_accept_once"
    k.run_session(deps)
    for _ in range(3):
        k.run_session(deps)
    per_symbol = {}
    for o in fake.orders:
        per_symbol[o["symbol"]] = per_symbol.get(o["symbol"], 0) + 1
    assert all(v == 1 for v in per_symbol.values()) and set(per_symbol) <= set(UNI[:6])
    total = sum(int(o["qty"]) for o in fake.orders)
    assert total <= sum(int(p["qty"]) for p in SIX)


# ---------------- config boundaries ----------------
def cfg(tmp_path, **over):
    c = json.loads((HERE / "pilot_config.json").read_text())
    c.update(over)
    f = tmp_path / "c.json"
    f.write_text(json.dumps(c))
    return load_pilot_config(f, GENOME, genome_id(GENOME))


def test_committed_config_has_an_explicit_finite_liquidation_limit_and_keeps_the_normal_cap(tmp_path):
    c = cfg(tmp_path)
    assert c["max_orders_per_session"] == 4 and c["max_liquidation_orders_per_session"] == 8


@pytest.mark.parametrize("over", [{"max_liquidation_orders_per_session": 0}, {"max_liquidation_orders_per_session": 9},
                                  {"max_liquidation_orders_per_session": 3}, {"max_liquidation_orders_per_session": -1},
                                  {"max_liquidation_orders_per_session": "8"}, {"max_liquidation_orders_per_session": 4.5}])
def test_liquidation_limit_must_be_a_finite_int_between_the_normal_cap_and_the_universe_size(tmp_path, over):
    with pytest.raises(PilotConfigError):
        cfg(tmp_path, **over)


def test_missing_liquidation_limit_is_refused(tmp_path):
    c = json.loads((HERE / "pilot_config.json").read_text())
    del c["max_liquidation_orders_per_session"]
    f = tmp_path / "c.json"; f.write_text(json.dumps(c))
    with pytest.raises(PilotConfigError):
        load_pilot_config(f, GENOME, genome_id(GENOME))


@pytest.mark.parametrize("ok", [4, 5, 8])
def test_boundary_values_accepted(tmp_path, ok):
    assert cfg(tmp_path, max_liquidation_orders_per_session=ok)["max_liquidation_orders_per_session"] == ok
