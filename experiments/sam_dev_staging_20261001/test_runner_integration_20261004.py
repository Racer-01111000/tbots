"""Drives the REAL run_session() and the REAL AlpacaPaperBroker against an
in-memory Alpaca (fake_alpaca.FakeAlpaca). Covers the 2026-10-04 review's
four areas: drawdown on every session, calendar-derived cadence, restart-safe
order accounting, and the decision-runner -> ledger -> limits -> adapter
connection."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import kim_once_per_session_v2 as k
import atomic_io
from alpaca_paper_broker import AlpacaPaperBroker
from fake_alpaca import FakeAlpaca, weekdays
from kim_order_logic import PersistentIntentLedger

SEL = json.loads((HERE / "selected_development_bot.json").read_text())
GENOME = SEL["selected_genome_definition"]
CFG = {
    "genome_id": "x", "max_orders_per_session": 8, "drawdown_halt_pct": 0.08, "max_asset_weight": 0.18,
    "gross_exposure_ceiling": 0.18, "min_cash_reserve_pct": 0.10, "time_in_force": "day",
    "kill_switch_file": "KILL_SWITCH", "expiry_date": "2026-11-01", "session_zero_date": "2026-10-05",
    "rebalance_every_n_sessions": 50,
}


def make(tmp_path, fake=None, gate=False, cfg=None, validate_only=False, at=(16, 45)):
    fake = fake or FakeAlpaca()
    gate_box = {"open": gate}
    broker = AlpacaPaperBroker({"k": "v"}, http=fake.http, submission_gate=lambda: gate_box["open"])
    deps = k.Deps(
        get_json=fake.get_json, headers={}, broker=broker, now_utc=lambda: fake.now_utc(at),
        submission_gate=lambda: gate_box["open"], state_dir=tmp_path, genome=GENOME, genome_id="x",
        config={**CFG, **(cfg or {})}, validate_only=validate_only,
    )
    return fake, deps, gate_box


def state(tmp_path, name):
    p = tmp_path / name
    return json.loads(p.read_text()) if p.exists() else None


def evaluated(tmp_path):
    c = state(tmp_path, "cadence_state.json")
    return c["last_evaluated_session_date"] if c else None


def advance(fake, deps, n=1):
    """Move to the next trading session (fills outstanding orders first)."""
    fake.fill_all()
    i = fake.sessions.index(fake.today)
    fake.today = fake.sessions[i + n]


# ---------------------------------------------------------------- wiring ----

def test_shadow_mode_decides_but_touches_neither_ledger_nor_broker(tmp_path):
    fake, deps, _ = make(tmp_path, gate=False)
    rec = k.run_session(deps)
    assert rec["status"] == "decided"
    assert rec["intended_orders_not_submitted"], "expected at least one intended order"
    assert fake.post_log == []
    assert PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3")).count_all() == 0
    assert rec["submission"]["skipped"] is True
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] is None   # shadow never spends the rebalance


def test_enabled_mode_sends_through_ledger_and_adapter(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    rec = k.run_session(deps)
    assert rec["status"] == "decided"
    assert len(fake.post_log) >= 1
    for b in fake.post_log:
        assert b["time_in_force"] == "day" and b["type"] == "market"
        assert b["client_order_id"] == f"kim-2026-10-05-{b['symbol']}"
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    assert ledger.count_for_session("2026-10-05") == len(fake.post_log)
    # rerun same day: no-op, nothing re-sent
    n = len(fake.post_log)
    assert k.run_session(deps)["status"] == "already_evaluated"
    assert len(fake.post_log) == n


def test_disabled_mode_leaves_ledger_empty_so_enabling_later_seeds_from_broker(tmp_path):
    fake, deps, gate = make(tmp_path, gate=False)
    fake.positions = [{"symbol": "DBC", "qty": "1"}]
    k.run_session(deps)
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    assert ledger.count_all() == 0  # the trap: disabled mode must not fill the ledger
    # next rebalance-less session with the gate open still seeds on first send
    gate["open"] = True
    state_p = tmp_path / "cadence_state.json"
    advance(fake, deps)                      # next session, NO hand-edited state
    rec = k.run_session(deps)
    assert rec["first_submission_session_seeded_count"] >= 1


def test_crash_after_send_before_state_persist_does_not_double_send(tmp_path, monkeypatch):
    fake, deps, _ = make(tmp_path, gate=True)
    real = k.write_json_atomic
    def boom(path, obj):
        if Path(path).name == "cadence_state.json":
            raise RuntimeError("simulated crash before cadence persist")
        return real(path, obj)
    monkeypatch.setattr(k, "write_json_atomic", boom)
    with pytest.raises(RuntimeError):
        k.run_session(deps)
    sent = list(fake.post_log)
    assert sent
    monkeypatch.setattr(k, "write_json_atomic", real)
    rec = k.run_session(deps)
    assert len(fake.post_log) == len(sent), "restart re-sent an order"
    assert rec["status"] in ("decided", "hold")


def test_sells_precede_buys_and_cap_is_enforced(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"max_orders_per_session": 1})
    fake.positions = [{"symbol": "TLT", "qty": "40"}, {"symbol": "VNQ", "qty": "30"}]
    rec = k.run_session(deps)
    sides = [b["side"] for b in fake.post_log]
    assert sides == sorted(sides, key=lambda s: 0 if s == "sell" else 1)
    assert len(fake.post_log) == 1                      # cap of 1
    assert rec["submission"]["blocked"] >= 1
    assert evaluated(tmp_path) is None  # retryable


def test_kill_switch_blocks_everything_and_session_stays_retryable(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    (tmp_path / "KILL_SWITCH").write_text("stop")
    rec = k.run_session(deps)
    assert fake.post_log == [] and rec["submission"]["startup_blocked"] is True
    (tmp_path / "KILL_SWITCH").unlink()
    assert k.run_session(deps)["status"] == "decided" and fake.post_log


def test_timed_out_post_with_nothing_at_broker_is_resubmitted_exactly_once(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.fail["post"] = "timeout"            # POST times out; the broker never received it (lookup -> 404)
    rec = k.run_session(deps)
    assert rec["submission"]["results"][0]["outcome"] == "uncertain_unresolved"
    assert evaluated(tmp_path) is None and state(tmp_path, "cadence_state.json") is None \
        or state(tmp_path, "cadence_state.json")["last_rebalanced_step"] is None
    assert fake.orders == []
    del fake.fail["post"]
    rec2 = k.run_session(deps)               # retry: lookup 404 -> resubmit the SAME client_order_id
    ids = [o["client_order_id"] for o in fake.orders]
    assert ids and len(ids) == len(set(ids)), "broker must end with exactly one order per id"
    assert rec2["status"] == "decided" and evaluated(tmp_path) == "2026-10-05"
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] == 0


def test_timed_out_post_that_actually_reached_the_broker_is_adopted_not_resent(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    real_http = fake.http
    def lossy(method, url, headers, body, timeout=10.0):
        out = real_http(method, url, headers, body, timeout)
        if method == "POST":
            raise TimeoutError("response lost after the broker accepted it")
        return out
    deps.broker._http = lossy
    k.run_session(deps)                       # lookup finds it -> adopted, no resend needed
    ids = [o["client_order_id"] for o in fake.orders]
    assert ids and len(ids) == len(set(ids))
    n = len(fake.orders)
    deps.broker._http = real_http
    k.run_session(deps)
    assert len(fake.orders) == n


def test_lookup_failure_after_timeout_blocks_resend_and_keeps_session_retryable(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.fail["post"] = "timeout"; fake.fail["lookup"] = 503
    k.run_session(deps)
    assert fake.orders == [] and evaluated(tmp_path) is None
    del fake.fail["post"]                     # lookup still unanswerable -> must NOT send blind
    k.run_session(deps)
    assert fake.orders == []
    del fake.fail["lookup"]
    k.run_session(deps)
    assert fake.orders and evaluated(tmp_path) == "2026-10-05"


def test_definitive_rejection_escalates_and_does_not_spend_the_rebalance(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    real_http = fake.http
    deps.broker._http = lambda m, u, h, b, t=10.0: (403, {"message": "insufficient buying power"}) if m == "POST" else real_http(m, u, h, b, t)
    rec = k.run_session(deps)
    assert rec["escalate"] is True and rec["submission_rejected"] is True
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] is None


def test_canceled_or_expired_orders_are_escalated_on_the_next_run(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)
    for o in fake.orders:
        o["status"] = "expired"
    advance(fake, deps)
    rec = k.run_session(deps)
    assert rec["escalate"] is True and rec["unfilled_orders_since_last_run"]


def test_broker_state_unreadable_abstains_instead_of_assuming_flat(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.positions = [{"symbol": "TLT", "qty": "40"}]
    fake.fail["positions"] = 500
    rec = k.run_session(deps)
    assert rec["status"] == "abstained_broker_state_unavailable" and fake.post_log == []


# --------------------------------------------------------------- drawdown ---

def test_drawdown_recorded_on_non_rebalance_session_and_peak_survives_restart(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)                                   # step 0, rebalance (live: spends it)
    advance(fake, deps)
    fake.set_equity(95_000.0)
    rec = k.run_session(deps)                             # step 1, NOT a rebalance
    assert rec["status"] == "hold"
    ps = state(tmp_path, "peak_equity_state.json")
    assert set(ps["history"]) == {"2026-10-05", "2026-10-06"}
    assert rec["current_drawdown"] == pytest.approx(-0.05) and ps["peak_equity"] == 100_000.0
    # "restart": brand-new Deps over the same state dir
    fake2, deps2, _ = make(tmp_path, fake=fake)
    advance(fake, deps2)
    fake.set_equity(96_000.0)
    rec2 = k.run_session(deps2)
    assert rec2["peak_equity"] == 100_000.0 and rec2["current_drawdown"] == pytest.approx(-0.04)


def test_halt_latches_liquidates_and_stays_flat_across_restarts(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)                                   # enter positions at step 0
    advance(fake, deps)
    fake.set_equity(90_000.0)                             # -10% breaches the 8% pilot halt on a non-rebalance day
    n_before = len(fake.post_log)
    rec = k.run_session(deps)
    assert rec["status"] == "halted" and rec["halt_latched"] is True
    new = fake.post_log[n_before:]
    assert new and all(b["side"] == "sell" for b in new)
    assert state(tmp_path, "halt_state.json")["halted"] is True
    advance(fake, deps)
    fake.set_equity(100_000.0)                            # recovery must NOT un-halt
    fake2, deps2, _ = make(tmp_path, fake=fake, gate=True)
    rec2 = k.run_session(deps2)
    assert rec2["status"] == "halted"
    assert all(b["side"] == "sell" for b in fake.post_log[n_before:])


def test_peak_state_corrupt_or_missing_abstains_loudly(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)
    advance(fake, deps)
    (tmp_path / "peak_equity_state.json").write_text("{truncated")
    rec = k.run_session(deps)
    assert rec["status"] == "abstained_peak_state_unusable" and rec["critical"] is True
    (tmp_path / "peak_equity_state.json").unlink()
    assert k.run_session(deps)["status"] == "abstained_peak_state_unusable"


# ---------------------------------------------------------------- cadence ---

def test_holiday_changes_nothing_and_does_not_advance_cadence(tmp_path):
    fake, deps, _ = make(tmp_path)
    k.run_session(deps)
    before = (tmp_path / "cadence_state.json").read_bytes()
    fake.sessions = [s for s in fake.sessions if s != "2026-10-06"]
    fake.today = "2026-10-06"
    rec = k.run_session(deps)
    assert rec["status"] == "non_trading_day"
    assert (tmp_path / "cadence_state.json").read_bytes() == before


def test_calendar_api_failure_is_not_mistaken_for_a_holiday(tmp_path):
    fake, deps, _ = make(tmp_path)
    fake.fail["calendar"] = 503
    rec = k.run_session(deps)
    assert rec["status"] == "abstained_calendar_unavailable"
    assert not (tmp_path / "cadence_state.json").exists()


def test_step_is_calendar_derived_not_run_derived(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)                                   # step 0
    fake.today = fake.sessions[7]                         # job didn't run for 6 sessions (outage)
    rec = k.run_session(deps)
    assert rec["cadence_step_used"] == 7 and rec["status"] == "hold"


def test_too_early_catch_up_run_changes_nothing(tmp_path):
    fake, deps, _ = make(tmp_path, at=(9, 5))              # Persistent=true boot catch-up before cutoff
    rec = k.run_session(deps)
    assert rec["status"] == "too_early" and not (tmp_path / "cadence_state.json").exists()
    assert not (tmp_path / "peak_equity_state.json").exists()


def test_abstained_rebalance_stays_pending_and_escalates(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.missing_bars = {"IEF"}
    r1 = k.run_session(deps)
    assert r1["status"] == "abstained_rebalance_pending"
    c = state(tmp_path, "cadence_state.json")
    assert c["last_rebalanced_step"] is None and c["last_evaluated_session_date"] is None
    for _ in range(2):
        advance(fake, deps)
        r = k.run_session(deps)
    assert r["consecutive_abstained_sessions"] == 3 and r["escalate"] is True
    fake.missing_bars = set()
    advance(fake, deps)
    r4 = k.run_session(deps)
    assert r4["status"] == "decided" and r4["cadence_step_used"] == 3        # caught up, not skipped for 50
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] == 0  # next due at step 50, unshifted


def test_next_rebalance_is_exactly_step_50(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True, cfg={"expiry_date": "2027-06-01"})
    k.run_session(deps)
    fake.today = fake.sessions[49]
    assert k.run_session(deps)["status"] == "hold"
    fake.today = fake.sessions[50]
    assert k.run_session(deps)["status"] == "decided"


def test_expired_pilot_does_nothing(tmp_path):
    fake, deps, _ = make(tmp_path, cfg={"expiry_date": "2026-10-05"})
    assert k.run_session(deps)["status"] == "expired" and not (tmp_path / "peak_equity_state.json").exists()


def test_validate_only_writes_no_state_and_never_sends_even_with_gate_open(tmp_path):
    fake, deps, _ = make(tmp_path, validate_only=True, gate=True)
    rec = k.run_session(deps)
    assert fake.post_log == []
    assert rec["status"] == "decided" and rec["intended_orders_not_submitted"]
    for name in ("cadence_state.json", "peak_equity_state.json", "halt_state.json", "order_ledger.sqlite3"):
        assert not (tmp_path / name).exists(), name


def test_shadow_session_then_live_session_next_day_sends_the_entry_with_no_state_edits(tmp_path):
    fake, deps, gate = make(tmp_path, gate=False)
    assert k.run_session(deps)["status"] == "decided" and fake.post_log == []
    gate["open"] = True
    advance(fake, deps)
    rec = k.run_session(deps)                 # step 1, gate open: the pending step-0 entry is taken now
    assert rec["status"] == "decided" and rec["cadence_step_used"] == 1
    assert fake.post_log, "the activation-window entry was consumed by the shadow run"
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] == 0


def test_drawdown_and_halt_latch_recorded_even_when_bars_are_not_ready(tmp_path):
    fake, deps, _ = make(tmp_path)
    k.run_session(deps)
    advance(fake, deps)
    fake.set_equity(88_000.0)
    fake.missing_bars = set(["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"])
    rec = k.run_session(deps)
    assert rec["status"] == "abstained_data_not_ready"
    ps = state(tmp_path, "peak_equity_state.json")
    assert ps["history"]["2026-10-06"] == 88_000.0
    assert state(tmp_path, "halt_state.json")["halted"] is True      # monitoring never depends on bar availability


def test_first_live_session_catches_up_a_rebalance_that_shadow_never_consumed(tmp_path):
    """cadence_state.json from the shadow period has session_zero in the past and
    no last_rebalanced_step: the first live session must take the pending entry."""
    fake, deps, _ = make(tmp_path, cfg={"session_zero_date": "2026-10-05"})
    fake.today = fake.sessions[3]
    rec = k.run_session(deps)
    assert rec["status"] == "decided" and rec["cadence_step_used"] == 3
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] is None  # gate closed -> still pending


def test_corrupt_halt_state_abstains(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    (tmp_path / "halt_state.json").write_text("{x")
    assert k.run_session(deps)["status"] == "abstained_halt_state_unusable" and fake.post_log == []


# ----------------------------------------------- delayed broker visibility ---

def test_delayed_visibility_after_timeout_never_creates_a_duplicate_order(tmp_path):
    """POST times out AFTER the broker accepted the order, and the order stays invisible to lookups
    for the next two checks. The client_order_id must carry us through: no second broker order, no
    false 'resolved', and the entry is only spent once the order is actually seen."""
    fake, deps, _ = make(tmp_path, gate=True)
    fake.hide_new_for = 4     # lookups: run1 #1; run2 start-reconcile #2, retry #3, post-duplicate #4 -> all 404; run3 sees it
    fake.fail["post"] = "timeout_after_accept_once"
    r1 = k.run_session(deps)                                   # lookup #1 -> 404 (invisible)
    assert r1["submission"]["results"][0]["outcome"] == "uncertain_unresolved"
    assert len(fake.orders) == 1 and evaluated(tmp_path) is None
    r2 = k.run_session(deps)                                   # lookup #2 404 -> resubmit same id -> broker says duplicate
    assert len(fake.orders) == 1, "duplicate broker order created while the first was still invisible"
    assert r2["submission"]["results"][0]["outcome"] == "uncertain_unresolved"
    assert evaluated(tmp_path) is None and (state(tmp_path, "cadence_state.json") or {}).get("last_rebalanced_step") is None
    r3 = k.run_session(deps)                                   # now visible -> adopted, no new POST needed
    posts_before = len(fake.post_log)
    assert len(fake.orders) == 1 and r3["status"] == "decided" and evaluated(tmp_path) == "2026-10-05"
    assert state(tmp_path, "cadence_state.json")["last_rebalanced_step"] == 0
    k.run_session(deps)
    assert len(fake.post_log) == posts_before and len(fake.orders) == 1
    ledger = PersistentIntentLedger(str(tmp_path / "order_ledger.sqlite3"))
    assert ledger.count_for_session("2026-10-05") == 1
    assert all(row["status"] not in ("intent_recorded", "uncertain_unresolved") for row in ledger.get_unresolved())

def test_order_invisible_in_the_open_orders_list_is_not_sized_twice_next_run(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.hide_new_for = 1
    k.run_session(deps)                                        # accepted, but hidden from /v2/orders once
    n = len(fake.orders)
    k.run_session(deps)                                        # same session rerun: already_evaluated or retry-by-id only
    assert len(fake.orders) == n


# ------------------------------------------- halt: frequency and no prices ---

def test_halt_with_no_prices_cannot_liquidate_but_latches_and_escalates(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    k.run_session(deps)
    fake.fill_all()
    advance(fake, deps)
    fake.set_equity(88_000.0)
    fake.missing_bars = set(["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"])
    n = len(fake.post_log)
    rec = k.run_session(deps)
    assert rec["status"] == "abstained_data_not_ready"
    assert state(tmp_path, "halt_state.json")["halted"] is True
    assert len(fake.post_log) == n, "must not sell blind with no prices"
    assert rec["halt_latched_but_unliquidated"] is True and rec["escalate"] is True
    fake.missing_bars = set()                                   # prices back: liquidation happens at the next valid session
    advance(fake, deps)
    fake.set_equity(88_000.0)
    rec2 = k.run_session(deps)
    assert rec2["status"] == "halted" and all(b["side"] == "sell" for b in fake.post_log[n:]) and len(fake.post_log) > n


def test_halt_with_one_unpriced_held_symbol_sells_the_rest_and_flags_that_one(tmp_path):
    fake, deps, _ = make(tmp_path, gate=True)
    fake.positions = [{"symbol": "TLT", "qty": "40"}, {"symbol": "GLD", "qty": "10"}]
    (tmp_path / "halt_state.json").write_text(json.dumps({"halted": True}))
    (tmp_path / "peak_equity_state.json").write_text(json.dumps(
        {"peak_equity": 100000.0, "peak_session_date": "2026-10-01", "history": {"2026-10-01": 100000.0}}))
    (tmp_path / "cadence_state.json").write_text(json.dumps(
        {"session_zero_date": "2026-10-05", "last_evaluated_session_date": "2026-10-01"}))
    fake.missing_bars = {"GLD"}
    rec = k.run_session(deps)
    # a missing price for ANY symbol means that symbol is never traded, even under a halt; the rest is liquidated
    assert rec["status"] == "halted"
    assert {(b["symbol"], b["side"]) for b in fake.post_log} == {("TLT", "sell")}
    assert rec["halt_unliquidated_symbols"] == ["GLD"] and rec["escalate"] is True
