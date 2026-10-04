import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
for sub in ("", "alpaca_adapter", "kim_order_simulation_tests"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import alpaca_adapter as aa
import atomic_io
import peak_equity
import session_calendar as sc
import session_guards as sg
from alpaca_paper_broker import AlpacaPaperBroker
from kim_order_logic import IntendedOrderSpec, UncertainSubmissionError, BrokerLookupError
from pilot_config import load_pilot_config, PilotConfigError
from lib.ids import genome_id

SPEC = IntendedOrderSpec("kim-2026-10-05-SPY", "SPY", 5, "buy")
SEL = json.loads((HERE / "selected_development_bot.json").read_text())
GENOME = SEL["selected_genome_definition"]


def broker(http, open_=True):
    return AlpacaPaperBroker({"k": "v"}, http=http, submission_gate=lambda: open_)


# ---- AlpacaPaperBroker ------------------------------------------------------
def test_submit_refuses_when_gate_closed_even_if_called_directly():
    called = []
    b = broker(lambda *a, **k: called.append(1), open_=False)
    with pytest.raises(aa.SubmissionDisabledError):
        b.submit(SPEC)
    assert called == []


def test_default_gate_is_the_real_hardcoded_false():
    assert aa.submission_enabled() is False
    b = AlpacaPaperBroker({"k": "v"}, http=lambda *a, **k: pytest.fail("network call attempted"))
    with pytest.raises(aa.SubmissionDisabledError):
        b.submit(SPEC)


def test_non_paper_host_is_refused():
    with pytest.raises(aa.HostNotAllowedError):
        AlpacaPaperBroker({}, host="https://api.alpaca.markets")


def test_payload_and_success_mapping():
    seen = {}
    def http(method, url, headers, body, timeout=10.0):
        seen.update(method=method, url=url, body=json.loads(body))
        return 200, {"id": "abc", "status": "accepted", "filled_qty": "0"}
    r = broker(http).submit(SPEC)
    assert r == {"id": "abc", "status": "accepted", "filled_qty": 0.0}
    assert seen["url"] == "https://paper-api.alpaca.markets/v2/orders"
    assert seen["body"] == {"symbol": "SPY", "qty": "5", "side": "buy", "type": "market",
                            "time_in_force": "day", "client_order_id": "kim-2026-10-05-SPY"}


@pytest.mark.parametrize("resp", [(500, None), (503, None), (429, None), (408, None),
                                  (422, {"message": "client_order_id must be unique"})])
def test_uncertain_outcomes_raise_uncertain(resp):
    with pytest.raises(UncertainSubmissionError):
        broker(lambda *a, **k: resp).submit(SPEC)


def test_transport_failure_is_uncertain():
    def http(*a, **k):
        raise TimeoutError("t/o")
    with pytest.raises(UncertainSubmissionError):
        broker(http).submit(SPEC)


def test_definitive_rejection_is_recorded_terminal_not_retried():
    r = broker(lambda *a, **k: (403, {"message": "insufficient buying power"})).submit(SPEC)
    assert r["status"] == "rejected" and r["id"] is None


def test_lookup_404_is_none_but_5xx_and_timeouts_are_errors_not_none():
    assert broker(lambda *a, **k: (404, {})).find_by_client_order_id("x") is None
    with pytest.raises(BrokerLookupError):
        broker(lambda *a, **k: (503, None)).find_by_client_order_id("x")
    def http(*a, **k):
        raise TimeoutError("t/o")
    with pytest.raises(BrokerLookupError):
        broker(http).find_by_client_order_id("x")


def test_lookup_success_maps_fields():
    r = broker(lambda *a, **k: (200, {"id": "i", "status": "pending_new", "filled_qty": "2"})).find_by_client_order_id("x")
    assert r == {"id": "i", "status": "pending_new", "filled_qty": 2.0}


# ---- guards ---------------------------------------------------------------
def test_cash_constraint_scales_buys_and_never_touches_sells():
    orders = [{"symbol": "SPY", "side": "buy", "shares": 100}, {"symbol": "TLT", "side": "sell", "shares": 7}]
    out, notes = sg.constrain_buys_to_cash(orders, {"SPY": 100_00, "TLT": 90_00}, cash_cents=5_000_00,
                                           equity_cents=100_000_00, reserve_pct=0.0)
    assert {o["symbol"]: o["shares"] for o in out} == {"SPY": 50, "TLT": 7} and notes


def test_cash_constraint_respects_reserve_and_gives_no_credit_for_same_batch_sells():
    orders = [{"symbol": "SPY", "side": "buy", "shares": 10}, {"symbol": "TLT", "side": "sell", "shares": 1000}]
    out, _ = sg.constrain_buys_to_cash(orders, {"SPY": 100_00, "TLT": 90_00}, cash_cents=10_000_00,
                                       equity_cents=100_000_00, reserve_pct=0.10)
    assert [o["symbol"] for o in out] == ["TLT"]  # budget 0 after the 10% reserve -> buy dropped


def test_cash_constraint_passthrough_when_affordable():
    orders = [{"symbol": "SPY", "side": "buy", "shares": 10}]
    out, notes = sg.constrain_buys_to_cash(orders, {"SPY": 100_00}, 50_000_00, 100_000_00, 0.10)
    assert out == orders and notes == []


def test_post_trade_guard_rejects_oversize_and_shorts():
    with pytest.raises(sg.OrderGuardError):
        sg.check_post_trade_limits([{"symbol": "SPY", "side": "buy", "shares": 200}], {}, {"SPY": 100_00},
                                   100_000_00, 0.18, 0.18)
    with pytest.raises(sg.OrderGuardError):
        sg.check_post_trade_limits([{"symbol": "SPY", "side": "sell", "shares": 5}], {"SPY": 1}, {"SPY": 100_00},
                                   100_000_00, 0.18, 0.18)
    sg.check_post_trade_limits([{"symbol": "SPY", "side": "buy", "shares": 180}], {}, {"SPY": 100_00},
                               100_000_00, 0.18, 0.18)


def test_build_specs_sells_first_deterministic_ids_day_tif():
    specs = sg.build_specs([{"symbol": "SPY", "side": "buy", "shares": 3},
                            {"symbol": "TLT", "side": "sell", "shares": 2},
                            {"symbol": "GLD", "side": "buy", "shares": 1}], "2026-10-05", "day")
    assert [(s.symbol, s.side) for s in specs] == [("TLT", "sell"), ("GLD", "buy"), ("SPY", "buy")]
    assert all(s.time_in_force == "day" and s.client_order_id == f"kim-2026-10-05-{s.symbol}" for s in specs)


# ---- calendar -------------------------------------------------------------
def test_session_step_counts_sessions_not_calendar_days():
    sessions = ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]  # 11-26 Thanksgiving closed
    assert sc.session_step(sessions, "2026-11-24", "2026-11-27") == 2
    assert sc.session_step(sessions, "2026-11-24", "2026-11-26") is None
    assert sc.latest_due_rebalance_step(49, 50) == 0 and sc.latest_due_rebalance_step(50, 50) == 50


def test_calendar_rejects_malformed_payloads():
    with pytest.raises(sc.CalendarUnavailable):
        sc.fetch_sessions(lambda u, h: (200, {"oops": 1}), "h", {}, "a", "b")
    with pytest.raises(sc.CalendarUnavailable):
        sc.fetch_sessions(lambda u, h: (200, [{"date": "garbage"}]), "h", {}, "a", "b")
    def boom(u, h):
        raise OSError("down")
    with pytest.raises(sc.CalendarUnavailable):
        sc.fetch_sessions(boom, "h", {}, "a", "b")


# ---- atomic state ---------------------------------------------------------
def test_atomic_write_leaves_no_temp_and_replaces_whole(tmp_path):
    p = tmp_path / "s.json"
    atomic_io.write_json_atomic(p, {"a": 1})
    atomic_io.write_json_atomic(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]


def test_corrupt_state_raises_instead_of_resetting(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{trunc")
    with pytest.raises(atomic_io.StateCorrupt):
        atomic_io.read_json_strict(p)
    with pytest.raises(atomic_io.StateCorrupt):
        peak_equity.load_state(p)


def test_missing_peak_state_with_history_is_refused(tmp_path):
    with pytest.raises(peak_equity.PeakStateMissing):
        peak_equity.load_state(tmp_path / "nope.json", require_existing=True)


def test_drawdown_sign_convention_is_negative_and_matches_portfolio():
    import execution
    pf = execution.Portfolio(100_000_00)
    pf.update_peak_and_drawdown(100_000_00)
    port_dd = pf.update_peak_and_drawdown(85_000_00)
    _, _, ours = peak_equity.evaluate({"history": {"d1": 100_000.0}}, "d2", 85_000.0)
    assert port_dd == pytest.approx(ours) and ours < 0


# ---- pilot config ---------------------------------------------------------
def load(tmp_path, **over):
    c = json.loads((HERE / "pilot_config.json").read_text())
    c.update(over)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(c))
    return load_pilot_config(p, GENOME, genome_id(GENOME))


def test_committed_pilot_config_validates_against_selected_genome(tmp_path):
    c = load(tmp_path)
    assert c["genome_id"] == SEL["selected_genome_id"]


@pytest.mark.parametrize("over", [
    {"drawdown_halt_pct": 0.5}, {"max_asset_weight": 0.5}, {"gross_exposure_ceiling": 0.95},
    {"time_in_force": "opg"}, {"max_orders_per_session": 99}, {"rebalance_every_n_sessions": 10},
    {"genome_id": "gen_other"}, {"min_cash_reserve_pct": 1.0},
])
def test_config_can_only_tighten_never_loosen(tmp_path, over):
    with pytest.raises(PilotConfigError):
        load(tmp_path, **over)
