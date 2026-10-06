"""Tests for the Kim four-session paper pilot (kim_paper_pilot.py). The REAL runner, REAL AlpacaPaperBroker, REAL ledger, REAL
identity/data checks run against an in-memory Alpaca (fake_alpaca_pilot). Only the decision weights are stubbed where a test needs a
predictable target; test_real_decision_function_runs uses the frozen control_agent unmodified."""
import copy
import json
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

import alpaca_adapter as aa
import kim_paper_pilot as kp
import pilot_identity
from alpaca_paper_broker import AlpacaPaperBroker
from fake_alpaca_pilot import ACCOUNT_ID, UNIVERSE, FakePilotAlpaca, pin_for

HERE = Path(__file__).resolve().parent
SAM = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
KIM = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"
REAL_DECIDE = kp.control_agent.decide      # the frozen decision function, captured before any stub


@pytest.fixture(autouse=True)
def stub_decide(monkeypatch):
    box = {"weights": {"DBC": 0.18}}
    monkeypatch.setattr(kp.control_agent, "decide", lambda view, genome: {"selected": list(box["weights"]), "weights": dict(box["weights"]), "evaluations": {}})
    return box


def make_env(tmp_path, fake=None, gate=True, cfg_patch=None, pin_patch=None):
    fake = fake or FakePilotAlpaca()
    cfg, sha = kp.load_config(HERE / "kim_paper_pilot_config.json")
    cfg = {**cfg, **(cfg_patch or {})}
    gate_box = {"open": gate}
    broker = AlpacaPaperBroker({"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}, http=fake.http,
                               submission_gate=lambda: gate_box["open"])
    env = kp.Env(cfg=cfg, cfg_sha=sha, get_json=fake.get_json, headers={}, broker=broker, now_utc=fake.now_utc,
                 submission_gate=lambda: gate_box["open"], state_dir=tmp_path, pin={**pin_for(fake), **(pin_patch or {})}, sleep=fake.sleep)
    env.gate_box, env.fake = gate_box, fake
    return env


def armed(tmp_path, fake=None, gate=True, first="2026-10-06", **kw):
    env = make_env(tmp_path, fake, gate, **kw)
    rec, code = kp.run_mode(env, "freeze-campaign", first)
    assert code == 0 and rec["status"] == "frozen", rec
    rec, code = kp.run_mode(env, "init")
    assert code == 0 and rec["status"] == "initialized", rec
    return env


def fresh_env(env, tmp_path):
    """A second process: same state dir, same fake broker, new objects (restart)."""
    return make_env(tmp_path, env.fake, env.gate_box["open"])


def run(env, mode):
    rec, code = kp.run_mode(env, mode)
    return rec, code


def ledger_rows(tmp_path):
    c = sqlite3.connect(tmp_path / "order_ledger.sqlite3")
    return c.execute("SELECT client_order_id, symbol, side, qty, status, filled_qty, session_date, order_class FROM order_intents").fetchall()


# ---------------------------------------------------------------- identity / seal / paper-only
def test_identity_loads_kim_and_verifies_hashes():
    cfg, _ = kp.load_config(HERE / "kim_paper_pilot_config.json")
    g, ev = pilot_identity.load_kim(HERE, HERE.parents[1], cfg)
    assert ev["genome_ids"]["independent_sha256"] == KIM and g["rebalance_every_n_sessions"] == 50
    assert set(ev["decision_function_sha256"]) == set(cfg["decision_function_sha256"])


def test_kim_cannot_be_replaced_by_sam_or_anything_else(tmp_path):
    cfg, _ = kp.load_config(HERE / "kim_paper_pilot_config.json")
    for bad in ({"genome_id": SAM}, {"strategy": "sam"}, {"genome_id": "gen_" + "0" * 64}, {"forbidden_genome_ids": [KIM]}):
        with pytest.raises(pilot_identity.IdentityError):
            pilot_identity.load_kim(HERE, HERE.parents[1], {**cfg, **bad})
    # the selected-bot record is swapped for Sam's genome: refused even if the config still says Kim
    dev = tmp_path / "dev"
    shutil.copytree(HERE, dev, ignore=shutil.ignore_patterns("__pycache__", "kim_shadow_pilot", "*.sqlite3"))
    sel = json.loads((dev / "selected_development_bot.json").read_text())
    sam = json.loads((HERE / "SAM_MANIFEST_20261001.json").read_text())["genome_definition"]
    sel["selected_genome_definition"], sel["selected_genome_id"] = sam, SAM
    (dev / "selected_development_bot.json").write_text(json.dumps(sel))
    with pytest.raises(pilot_identity.IdentityError):
        pilot_identity.load_kim(dev, HERE.parents[1], cfg)


def test_tampered_decision_function_or_frozen_file_hash_is_refused():
    cfg, _ = kp.load_config(HERE / "kim_paper_pilot_config.json")
    t = copy.deepcopy(cfg)
    t["decision_function_sha256"]["scripts/control_agent.py"] = "0" * 64
    with pytest.raises(pilot_identity.IdentityError):
        pilot_identity.load_kim(HERE, HERE.parents[1], t)
    with pytest.raises(pilot_identity.IdentityError):
        pilot_identity.load_kim(HERE, HERE.parents[1], {**cfg, "frozen_champion_file_sha256": "0" * 64})


def test_runner_hard_stops_on_identity_failure_and_blocks_exposure(tmp_path):
    env = armed(tmp_path)
    env.cfg = {**env.cfg, "genome_id": SAM}
    rec, code = run(env, "open")
    assert code == kp.EXIT_HARD_STOP and rec["status"] == "HARD_STOP" and (tmp_path / "STOP").exists()
    assert env.fake.post_log == []


def test_submission_needs_flag_const_and_seal(monkeypatch, tmp_path):
    cfg_path = aa.SEALED_PILOT_CONFIG
    sha = __import__("hashlib").sha256(cfg_path.read_bytes()).hexdigest()
    flag = "true-i-understand-the-risk"
    monkeypatch.setattr(aa, "_SUBMISSION_ENABLED_CONST", True)
    monkeypatch.delenv("TBOTS_ALPACA_SUBMISSION_ENABLED", raising=False)
    monkeypatch.delenv("TBOTS_KIM_PAPER_SEAL", raising=False)
    assert aa.submission_enabled() is False
    monkeypatch.setenv("TBOTS_ALPACA_SUBMISSION_ENABLED", flag)
    assert aa.submission_enabled() is False                       # flag alone is not enough
    monkeypatch.setenv("TBOTS_KIM_PAPER_SEAL", "0" * 64)
    assert aa.submission_enabled() is False                       # wrong seal
    monkeypatch.setenv("TBOTS_KIM_PAPER_SEAL", sha)
    assert aa.submission_enabled() is True
    monkeypatch.setattr(aa, "_SUBMISSION_ENABLED_CONST", False)
    assert aa.submission_enabled() is False                       # the hardcoded constant still matters
    monkeypatch.setattr(aa, "_SUBMISSION_ENABLED_CONST", True)
    other = tmp_path / "cfg.json"
    other.write_text(json.dumps({"submission_scope": "something_else", "paper_host": aa.PAPER_TRADING_HOST}))
    monkeypatch.setattr(aa, "SEALED_PILOT_CONFIG", other)
    monkeypatch.setenv("TBOTS_KIM_PAPER_SEAL", __import__("hashlib").sha256(other.read_bytes()).hexdigest())
    assert aa.submission_enabled() is False                       # right hash, wrong scope


def test_live_endpoint_rejected_independent_of_every_switch(tmp_path):
    with pytest.raises(aa.HostNotAllowedError):
        aa.assert_trading_host_allowed("https://api.alpaca.markets")
    with pytest.raises(aa.HostNotAllowedError):
        AlpacaPaperBroker({}, http=lambda *a, **k: (200, {}), host="https://api.alpaca.markets", submission_gate=lambda: True)
    env = armed(tmp_path)
    env.trading_host = "https://api.alpaca.markets"
    rec, code = run(env, "open")
    assert code == kp.EXIT_HARD_STOP and env.fake.post_log == []


def test_sealed_config_loosening_is_refused(tmp_path):
    base = json.loads((HERE / "kim_paper_pilot_config.json").read_text())
    for patch in ({"max_orders_per_session": 5}, {"drawdown_halt_pct": 0.09}, {"max_asset_weight": 0.2}, {"extended_hours": True},
                  {"time_in_force": "opg"}, {"warmup_bars": 100}, {"rebalance_every_n_sessions": 10}, {"campaign_sessions": 5},
                  {"max_liquidation_orders_per_session": 9}, {"gross_exposure_ceiling": 0.25}):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({**base, **patch}))
        with pytest.raises(kp.HardStop):
            kp.load_config(p)


# ---------------------------------------------------------------- campaign / init / cadence anchor
def test_campaign_freezes_exactly_four_sessions_write_once_with_conversions(tmp_path):
    env = make_env(tmp_path, FakePilotAlpaca(holidays=("2026-10-08",), early_close={"2026-10-09": "13:00"}))
    rec, code = run(env, "freeze-campaign") if False else kp.run_mode(env, "freeze-campaign", "2026-10-06")
    c = rec["campaign"]
    assert [s["date"] for s in c["sessions"]] == ["2026-10-06", "2026-10-07", "2026-10-09", "2026-10-12"]
    s0 = c["sessions"][0]
    assert s0["open_utc"].startswith("2026-10-06T13:30:00") and s0["close_utc"].startswith("2026-10-06T20:00:00") and s0["utc_offset_at_open"] == "-0400"
    assert c["sessions"][2]["early_close"] is True and c["sessions"][2]["close_utc"].startswith("2026-10-09T17:00:00")
    assert c["hold_deadline_utc"].startswith("2026-10-12T21:00:00")      # close 20:00Z + 60 min
    mode = (tmp_path / "campaign.json").stat().st_mode
    assert not mode & stat.S_IWUSR
    again, _ = kp.run_mode(env, "freeze-campaign", "2026-10-13")         # cannot be re-frozen or extended
    assert again["status"] == "already_frozen" and again["campaign"]["anchor_session"] == "2026-10-06"


def test_campaign_refuses_a_non_session_first_date(tmp_path):
    env = make_env(tmp_path)
    rec, code = kp.run_mode(env, "freeze-campaign", "2026-10-10")        # Saturday
    assert code == kp.EXIT_HARD_STOP and not (tmp_path / "campaign.json").exists()


def test_dst_change_conversion(tmp_path):
    env = make_env(tmp_path, FakePilotAlpaca(now_et=("2026-11-02", "09:30:05")))
    rec, _ = kp.run_mode(env, "freeze-campaign", "2026-10-30")
    s = {x["date"]: x for x in rec["campaign"]["sessions"]}
    assert s["2026-10-30"]["utc_offset_at_open"] == "-0400" and s["2026-11-02"]["utc_offset_at_open"] == "-0500"
    assert s["2026-11-02"]["open_utc"].startswith("2026-11-02T14:30:00")


def test_init_reconciles_broker_first_and_seeds_baseline_once(tmp_path):
    env = armed(tmp_path)
    base = json.loads((tmp_path / "pilot_baseline.json").read_text())
    assert base["positions"] == {"DBC": 1}
    cad = json.loads((tmp_path / "cadence_state.json").read_text())
    assert cad["anchor_session"] == "2026-10-06" and cad["last_rebalanced_step"] is None
    peak = json.loads((tmp_path / "peak_equity_state.json").read_text())
    assert peak["start_equity"] == peak["peak_equity"] == env.fake.equity()
    rec, _ = kp.run_mode(env, "init")
    assert rec["status"] == "already_initialized"
    rows = ledger_rows(tmp_path)
    assert [r[0] for r in rows] == ["kim-manual-test-20261002T025247Z"]      # the diagnostic order is baseline, not a pilot order


def test_unexpected_holdings_or_orders_block_activation(tmp_path):
    fake = FakePilotAlpaca()
    fake.positions["SPY"] = 3
    env = make_env(tmp_path, fake)
    kp.run_mode(env, "freeze-campaign", "2026-10-06")
    rec, code = kp.run_mode(env, "init")
    assert code == kp.EXIT_HARD_STOP and not (tmp_path / "pilot_baseline.json").exists()
    fake2 = FakePilotAlpaca()
    fake2.orders.append({"id": "x", "client_order_id": "someone-else", "symbol": "SPY", "side": "buy", "qty": "1",
                         "filled_qty": "0", "status": "accepted"})
    t2 = tmp_path / "b"
    env2 = make_env(t2, fake2)
    kp.run_mode(env2, "freeze-campaign", "2026-10-06")
    rec, code = kp.run_mode(env2, "init")
    assert code == kp.EXIT_HARD_STOP and "unexplained" in rec["reason"]


def test_account_mismatch_blocks_everything_and_never_auto_resumes(tmp_path):
    env = armed(tmp_path)
    env.pin = {**env.pin, "account_id_sha256": "f" * 64}
    rec, code = run(env, "open")
    assert code == kp.EXIT_HARD_STOP and (tmp_path / "STOP").exists() and env.fake.post_log == []
    env.pin = pin_for(env.fake)                                            # even with the pin restored
    rec, code = run(env, "open")
    assert rec["status"] == "abstained" and "STOP" in rec["reason"] and env.fake.post_log == []


def test_blocked_or_inactive_account_is_a_hard_stop(tmp_path):
    env = armed(tmp_path)
    env.fake.account["trading_blocked"] = True
    rec, code = run(env, "open")
    assert code == kp.EXIT_HARD_STOP and env.fake.post_log == []


# ---------------------------------------------------------------- opening path
def test_opening_decision_uses_prior_completed_session_and_submits_in_window(tmp_path):
    env = armed(tmp_path)
    rec, code = run(env, "open")
    assert code == 0 and rec["status"] == "submitted", rec
    assert rec["completed_session_used"] == "2026-10-05"                  # Oct 6 opening -> inputs through Oct 5
    assert all("end=2026-10-05" in u for u in env.fake.get_log if "data.alpaca" in u) and any("feed=sip" in u for u in env.fake.get_log)
    assert rec["data_retrieved_at_utc"] <= rec["decision_computed_at_utc"] <= rec["submission_started_at_utc"]    # real timestamps, not backdated
    assert len(env.fake.post_log) == 1
    o = env.fake.post_log[0]
    assert o["client_order_id"] == "kim-2026-10-06-DBC" and o["time_in_force"] == "day" and o["extended_hours"] is False
    assert o["type"] == "market" and o["side"] == "buy" and "." not in o["qty"]
    # sizing uses the RAW close of the completed session (fake raw = adjusted*1.02), whole shares, minus the held baseline share
    raw_cents = round(env.fake.bars("DBC", "raw", "2025-02-09", "2026-10-05")[-1]["c"] * 100)
    assert int(o["qty"]) == int(0.18 * round(env.fake.equity() * 100) // raw_cents) - 1
    cad = json.loads((tmp_path / "cadence_state.json").read_text())
    assert cad["last_rebalanced_step"] == 0 and cad["anchor_session"] == "2026-10-06"
    assert list((tmp_path / "receipts").glob("*_open.json"))


def test_never_asks_for_todays_bar(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    assert not any("end=2026-10-06" in u for u in env.fake.get_log if "data.alpaca" in u)


def test_open_waits_for_the_clock_and_never_submits_before_open(tmp_path):
    fake = FakePilotAlpaca(now_et=("2026-10-06", "09:29:50"))
    env = armed(tmp_path, fake)
    rec, code = run(env, "open")
    assert rec["status"] == "submitted"
    assert rec["clock_confirmed_open_at_utc"] >= "2026-10-06T13:30:00"


def test_no_order_if_market_never_confirms_open_in_window(tmp_path):
    fake = FakePilotAlpaca()
    fake.market_open_override = False
    env = armed(tmp_path, fake)
    rec, code = run(env, "open")
    assert rec["status"] == "abstained" and fake.post_log == []
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] is None


def test_no_order_after_the_window_closes(tmp_path):
    env = armed(tmp_path, FakePilotAlpaca(now_et=("2026-10-06", "09:36:00")))
    rec, _ = run(env, "open")
    assert rec["status"] == "abstained" and env.fake.post_log == []


def test_not_a_campaign_session_abstains(tmp_path):
    env = armed(tmp_path, FakePilotAlpaca(now_et=("2026-10-13", "09:30:05")))
    rec, _ = run(env, "open")
    assert rec["status"] == "abstained" and "frozen campaign" in rec["reason"] and env.fake.post_log == []


def test_cadence_and_anchor_persist_across_restarts_and_days_without_reset(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.fill("kim-2026-10-06-DBC")
    env2 = fresh_env(env, tmp_path)                                       # restart
    rec, _ = run(env2, "open")                                            # same day again
    assert rec["status"] == "hold_not_due" and len(env.fake.post_log) == 1
    env.fake.set_et("2026-10-07", "09:30")
    rec, _ = run(fresh_env(env, tmp_path), "open")
    assert rec["status"] == "hold_not_due" and rec["step"] == 1 and len(env.fake.post_log) == 1
    cad = json.loads((tmp_path / "cadence_state.json").read_text())
    assert cad["anchor_session"] == "2026-10-06" and cad["last_rebalanced_step"] == 0     # never reset to manufacture trades


def test_duplicate_start_is_blocked_by_the_single_writer_lock(tmp_path):
    env = armed(tmp_path)
    fd = kp.acquire_lock(env)
    try:
        rec, code = kp.run_mode(make_env(tmp_path, env.fake), "monitor")
        assert code == kp.EXIT_BUSY and rec["status"].startswith("busy")
    finally:
        import fcntl, os
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert env.fake.post_log == []


def test_shadow_run_with_gate_closed_places_nothing_and_leaves_ledger_alone(tmp_path):
    env = armed(tmp_path, gate=False)
    rec, _ = run(env, "open")
    assert rec["status"] == "shadow_no_submission" and env.fake.post_log == []
    assert [r[0] for r in ledger_rows(tmp_path)] == ["kim-manual-test-20261002T025247Z"]
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] is None


# ---------------------------------------------------------------- invalid history
def _drop(i):
    return lambda sym, adj, bars: bars[:i] + bars[i + 1:]


def _dup(sym, adj, bars):
    return bars[:100] + [bars[100]] + bars[100:]


def _future(sym, adj, bars):
    return bars + [{"t": "2026-10-06T04:00:00Z", "o": 1.0, "c": 1.0}]


def _swap(sym, adj, bars):
    b = list(bars)
    b[200], b[201] = b[201], b[200]
    return b


def _nan(sym, adj, bars):
    b = [dict(x) for x in bars]
    if sym == "GLD" and adj == "all":
        b[50]["c"] = float("nan")
    return b


def _neg(sym, adj, bars):
    b = [dict(x) for x in bars]
    if sym == "TLT" and adj == "all":
        b[60]["c"] = -3.0
    return b


def _short(sym, adj, bars):
    return bars[-200:]


def _no_raw_last(sym, adj, bars):
    return bars[:-1] if (sym == "DBC" and adj == "raw") else bars


@pytest.mark.parametrize("mut", [_drop(150), _dup, _future, _swap, _nan, _neg, _short, _no_raw_last],
                         ids=["gap", "duplicate", "future-dated", "out-of-order", "non-finite", "non-positive", "short-warmup", "missing-sizing-bar"])
def test_invalid_history_abstains_without_any_order_or_sale(tmp_path, mut):
    env = armed(tmp_path)
    env.fake.bar_mutation = mut
    rec, code = run(env, "open")
    assert code == 0 and rec["status"] == "abstained" and "data boundary" in rec["reason"], rec
    assert env.fake.post_log == [] and env.fake.delete_log == []
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] is None   # stays pending


def test_bar_endpoint_failure_abstains(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["bars"] = 500
    rec, _ = run(env, "open")
    assert rec["status"] == "abstained" and env.fake.post_log == []


def test_broker_state_unreadable_is_never_treated_as_flat(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["positions"] = 500
    rec, _ = run(env, "open")
    assert rec["status"] == "abstained" and env.fake.post_log == []


def test_clock_skew_is_a_hard_stop_and_unreadable_clock_abstains(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["clock"] = 500
    rec, code = run(env, "open")
    assert code == 0 and rec["status"] == "abstained" and env.fake.post_log == []
    t2 = tmp_path / "b"
    env2 = armed(t2)
    orig = env2.fake.get_json
    def skewed(url, headers):
        s, body = orig(url, headers)
        if url.endswith("/v2/clock") and isinstance(body, dict):
            body["timestamp"] = "2026-10-06T10:00:00-04:00"
        return s, body
    env2.get_json = skewed
    rec, code = run(env2, "open")
    assert code == kp.EXIT_HARD_STOP and env2.fake.post_log == []


# ---------------------------------------------------------------- risk limits / orders
def test_risk_limits_refuse_oversized_targets_without_orders(tmp_path, stub_decide):
    env = armed(tmp_path)
    stub_decide["weights"] = {"DBC": 0.5}
    rec, _ = run(env, "open")
    assert rec["status"] == "order_refused" and env.fake.post_log == []
    t2 = tmp_path / "b"
    env2 = armed(t2)
    stub_decide["weights"] = {s: 0.03 for s in ("SPY", "EFA", "EEM", "IEF", "TLT", "GLD")}    # 6 orders > normal cap 4
    rec, _ = run(env2, "open")
    assert rec["status"] == "order_refused" and env2.fake.post_log == []


def test_baseline_share_does_not_count_toward_the_pilot_caps(tmp_path, stub_decide):
    env = armed(tmp_path)
    stub_decide["weights"] = {"SPY": 0.18}                                # a target that does not include the baseline symbol (DBC)
    rec, _ = run(env, "open")
    assert rec["status"] == "submitted" and [o["symbol"] for o in env.fake.post_log] == ["SPY"]


def test_four_orders_are_allowed_and_ids_are_stable(tmp_path, stub_decide):
    env = armed(tmp_path)
    stub_decide["weights"] = {s: 0.04 for s in ("SPY", "EFA", "EEM", "IEF")}
    rec, _ = run(env, "open")
    assert rec["status"] == "submitted" and len(env.fake.post_log) == 4
    assert sorted(o["client_order_id"] for o in env.fake.post_log) == sorted(f"kim-2026-10-06-{s}" for s in ("SPY", "EFA", "EEM", "IEF"))


def test_cash_reserve_blocks_buys_that_would_eat_the_reserve(tmp_path):
    fake = FakePilotAlpaca()
    fake.price_scale["DBC"] = 500.0                  # baseline share is worth ~$55k, cash only $3k -> no cash above the 10% reserve
    fake.cash = 3_000.0
    env = armed(tmp_path, fake)
    rec, _ = run(env, "open")
    assert rec["status"] == "decided_no_orders" and fake.post_log == [] and rec["order_guard_notes"]


def test_decided_cash_target_is_a_legitimate_no_order_result(tmp_path, stub_decide):
    env = armed(tmp_path)
    stub_decide["weights"] = {}
    rec, _ = run(env, "open")
    assert rec["status"] == "decided_no_orders" and env.fake.post_log == [] and env.fake.delete_log == []
    assert env.fake.positions == {"DBC": 1}                                # the baseline share is never sold
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] == 0


def test_baseline_holding_is_never_sold_even_when_target_is_zero_after_a_pilot_buy(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.fill("kim-2026-10-06-DBC")
    led = kp.open_ledger(env)
    kp.reconcile_and_check(env, led, json.loads((tmp_path / "pilot_baseline.json").read_text()), kp.read_broker(env))
    held_total = env.fake.positions["DBC"]
    prep = {"decision": {"weights": {}}, "data": {"sizing_cents": {s: 5000 for s in UNIVERSE}}}
    b = kp.read_broker(env)
    orders, notes = kp.plan_orders(env, prep, b, 0.0, led, json.loads((tmp_path / "pilot_baseline.json").read_text()))
    sells = [o for o in orders if o["side"] == "sell"]
    assert sum(o["shares"] for o in sells) == held_total - 1               # exactly the pilot-owned shares; baseline share stays


def test_missing_sizing_price_never_produces_a_sale(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.fill("kim-2026-10-06-DBC")
    env.fake.bar_mutation = _no_raw_last
    pos_before = dict(env.fake.positions)
    env.fake.set_et("2026-10-07", "09:30")
    rec, _ = run(fresh_env(env, tmp_path), "open")
    assert env.fake.positions == pos_before and len(env.fake.post_log) == 1


# ---------------------------------------------------------------- acknowledgement / crash / duplicates
def test_lost_ack_after_acceptance_is_reconciled_with_the_same_id(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["post"] = "timeout_after_accept_once"
    rec, _ = run(env, "open")
    assert rec["status"] == "submitted" and len(env.fake.post_log) == 1
    assert [r[0] for r in ledger_rows(tmp_path) if r[0].startswith("kim-2026")] == ["kim-2026-10-06-DBC"]
    assert len([o for o in env.fake.orders if o["client_order_id"] == "kim-2026-10-06-DBC"]) == 1


def test_lost_ack_with_delayed_visibility_blocks_then_retries_the_same_id_only(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["post"] = "timeout_after_accept_once"
    env.fake.hidden["kim-2026-10-06-DBC"] = 1                            # first lookup after the timeout 404s
    rec, _ = run(env, "open")
    assert rec["status"] == "blocked_or_uncertain" and rec["escalate"]
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] is None
    rec2, _ = run(fresh_env(env, tmp_path), "open")                      # restart inside the window
    assert len([o for o in env.fake.orders if o["client_order_id"] == "kim-2026-10-06-DBC"]) == 1
    ids = {o["client_order_id"] for o in env.fake.orders}
    assert ids == {"kim-manual-test-20261002T025247Z", "kim-2026-10-06-DBC"}      # no fresh id was ever created


def test_crash_after_submit_before_acknowledgement_recovers_without_a_second_order(tmp_path):
    env = armed(tmp_path)
    led = kp.open_ledger(env)
    from kim_order_logic import IntendedOrderSpec
    led.record_intent(IntendedOrderSpec("kim-2026-10-06-DBC", "DBC", 100, "buy"), "2026-10-06", 4)   # intent written, then the process died
    env.fake.orders.append({"id": "id-x", "client_order_id": "kim-2026-10-06-DBC", "symbol": "DBC", "side": "buy", "qty": "100",
                            "filled_qty": "0", "status": "accepted", "time_in_force": "day"})
    kp.close_ledgers(env)
    rec, _ = run(fresh_env(env, tmp_path), "open")
    assert len([o for o in env.fake.orders if o["client_order_id"] == "kim-2026-10-06-DBC"]) == 1 and env.fake.post_log == []


def test_definitive_rejection_does_not_spend_the_rebalance(tmp_path):
    env = armed(tmp_path)
    env.fake.fail["post"] = 403
    rec, _ = run(env, "open")
    assert rec["status"] == "rejected" and rec["escalate"]
    assert json.loads((tmp_path / "cadence_state.json").read_text())["last_rebalanced_step"] is None


def test_partial_fills_are_reconciled_and_explained(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.set_et("2026-10-06", "10:00")
    env.fake.fill("kim-2026-10-06-DBC", qty=40)
    rec, code = run(fresh_env(env, tmp_path), "monitor")
    assert code == 0 and rec["status"] == "monitored" and rec["pilot_net_filled"] == {"DBC": 40}
    row = [r for r in ledger_rows(tmp_path) if r[0] == "kim-2026-10-06-DBC"][0]
    assert row[4] == "partially_filled" and row[5] == 40
    env.fake.set_et("2026-10-06", "10:05")
    n = int(env.fake.post_log[0]["qty"])
    env.fake.fill("kim-2026-10-06-DBC")
    rec, _ = run(fresh_env(env, tmp_path), "monitor")
    assert rec["pilot_net_filled"] == {"DBC": n}


def test_unexplained_order_or_holding_is_a_hard_stop(tmp_path):
    env = armed(tmp_path)
    env.fake.set_et("2026-10-06", "10:00")
    env.fake.orders.append({"id": "z", "client_order_id": "manual-oops", "symbol": "SPY", "side": "buy", "qty": "1", "filled_qty": "0", "status": "accepted"})
    rec, code = run(env, "monitor")
    assert code == kp.EXIT_HARD_STOP and (tmp_path / "STOP").exists()
    t2 = tmp_path / "b"
    env2 = armed(t2)
    env2.fake.set_et("2026-10-06", "10:00")
    env2.fake.positions["SPY"] = 7
    rec, code = run(env2, "monitor")
    assert code == kp.EXIT_HARD_STOP and "not explained" in rec["reason"]


# ---------------------------------------------------------------- preflight
def test_preflight_validates_without_ever_submitting_and_open_matches_it(tmp_path):
    fake = FakePilotAlpaca(now_et=("2026-10-06", "09:15:05"))
    env = armed(tmp_path, fake)
    rec, code = run(env, "preflight")
    assert code == 0 and rec["status"] == "preflight_ok" and rec["submission_attempted"] is False
    assert rec["prepared"]["completed_session"] == "2026-10-05" and fake.post_log == []
    fake.set_et("2026-10-06", "09:30")
    rec, _ = run(fresh_env(env, tmp_path), "open")
    assert rec["status"] == "submitted" and rec["matches_preflight_decision"] is True


def test_open_fails_closed_if_the_decision_differs_from_preflight(tmp_path, stub_decide):
    env = armed(tmp_path, FakePilotAlpaca(now_et=("2026-10-06", "09:15:05")))
    run(env, "preflight")
    stub_decide["weights"] = {"EFA": 0.18}
    env.fake.set_et("2026-10-06", "09:30")
    rec, _ = run(fresh_env(env, tmp_path), "open")
    assert rec["status"] == "abstained" and "differs" in rec["reason"] and env.fake.post_log == []


def test_real_decision_function_runs_through_the_whole_path(tmp_path, monkeypatch):
    monkeypatch.setattr(kp.control_agent, "decide", REAL_DECIDE)
    env = armed(tmp_path)
    rec, code = run(env, "open")
    assert code == 0 and rec["status"] in ("submitted", "decided_no_orders"), rec
    assert set(rec["decision"]) == {"selected", "weights"} and rec["completed_session_used"] == "2026-10-05"


# ---------------------------------------------------------------- halt
def _crash_setup(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.fill("kim-2026-10-06-DBC")
    # a second pilot order still working (would be a conflicting buy at halt time)
    env.fake.orders.append({"id": "w-1", "client_order_id": "kim-2026-10-06-SPY", "symbol": "SPY", "side": "buy", "qty": "5",
                            "filled_qty": "0", "status": "accepted", "time_in_force": "day"})
    led = kp.open_ledger(env)
    from kim_order_logic import IntendedOrderSpec
    led.record_intent(IntendedOrderSpec("kim-2026-10-06-SPY", "SPY", 5, "buy"), "2026-10-06", 4)
    led.update_status("kim-2026-10-06-SPY", "w-1", "accepted", 0)
    kp.close_ledgers(env)
    return env


def test_halt_latches_cancels_conflicts_and_liquidates_only_pilot_owned_shares(tmp_path):
    env = _crash_setup(tmp_path)
    env.fake.set_et("2026-10-06", "11:00")
    env.fake.crash(0.3)
    rec, code = run(fresh_env(env, tmp_path), "monitor")
    assert code == 0 and rec["status"] == "halted" and rec["halt_latched"]
    assert json.loads((tmp_path / "halt_state.json").read_text())["halted"] is True
    assert env.fake.delete_log == ["w-1"]                                   # the conflicting pilot buy was cancelled
    liq = [o for o in env.fake.post_log if o["client_order_id"].endswith("-liq")]
    owned = int(env.fake.post_log[0]["qty"])
    assert len(liq) == 1 and liq[0]["side"] == "sell" and liq[0]["symbol"] == "DBC" and int(liq[0]["qty"]) == owned
    assert env.fake.positions["DBC"] == owned + 1                            # baseline share not touched by the sell
    env.fake.fill("kim-2026-10-06-DBC-liq")
    assert env.fake.positions == {"DBC": 1}
    again, _ = run(fresh_env(env, tmp_path), "monitor")                      # second tick: no second liquidation order
    assert len([o for o in env.fake.post_log if o["client_order_id"].endswith("-liq")]) == 1
    open_rec, _ = run(fresh_env(env, tmp_path), "open")                      # halted: no new exposure
    assert open_rec["status"] == "abstained"


def test_halt_waits_for_regular_hours_before_selling(tmp_path):
    env = _crash_setup(tmp_path)
    env.fake.crash(0.3)
    env.fake.set_et("2026-10-06", "16:10")                                    # closed, within the monitor grace
    rec, _ = run(fresh_env(env, tmp_path), "monitor")
    assert rec["halt_latched"] and "market closed" in rec.get("halt_hold", "")
    assert not [o for o in env.fake.post_log if o["client_order_id"].endswith("-liq")]
    assert json.loads((tmp_path / "halt_state.json").read_text())["halted"]     # latched anyway


def test_halt_cancel_fill_race_is_reconciled_before_selling(tmp_path):
    env = _crash_setup(tmp_path)
    env.fake.set_et("2026-10-06", "11:00")
    env.fake.crash(0.3)
    orig = env.fake.http
    def racing(method, url, headers, body, timeout=10.0):
        if method == "DELETE":
            env.fake.fill("kim-2026-10-06-SPY")                              # filled while the cancel was in flight
        return orig(method, url, headers, body, timeout)
    e2 = fresh_env(env, tmp_path)
    e2.broker._http = racing
    rec, _ = run(e2, "monitor")
    liq = {o["symbol"]: int(o["qty"]) for o in env.fake.post_log if o["client_order_id"].endswith("-liq")}
    assert liq.get("SPY") == 5 and "DBC" in liq                              # the raced fill is liquidated too
    assert rec["cancellations"][0]["final_status"] == "filled"


def test_liquidation_budget_survives_restarts(tmp_path):
    from kim_order_logic import IntendedOrderSpec
    from order_limits import DailyOrderLimitReached
    env = _crash_setup(tmp_path)
    led = kp.open_ledger(env)
    for s in ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "VNQ", "DBC"]:
        led.record_intent(IntendedOrderSpec(f"kim-2026-10-06-{s}-liq", s, 1, "sell"), "2026-10-06", 8, "liquidation")
    kp.close_ledgers(env)
    led2 = kp.open_ledger(fresh_env(env, tmp_path))                          # a new process after a crash
    with pytest.raises(DailyOrderLimitReached):
        led2.record_intent(IntendedOrderSpec("kim-2026-10-06-XYZ-liq", "SPY", 1, "sell"), "2026-10-06", 8, "liquidation")
    led2.record_intent(IntendedOrderSpec("kim-2026-10-06-NRM", "SPY", 1, "buy"), "2026-10-06", 4, "normal")   # normal budget is separate


def test_monitor_outside_market_hours_does_nothing(tmp_path):
    env = armed(tmp_path)
    env.fake.set_et("2026-10-06", "20:00")
    rec, _ = run(env, "monitor")
    assert rec["status"] == "abstained"


def test_stop_blocks_new_exposure_but_monitor_keeps_reconciling(tmp_path):
    env = armed(tmp_path)
    kp.write_stop(env, "test")
    env.fake.set_et("2026-10-06", "10:00")
    rec, code = run(env, "monitor")
    assert code == 0 and rec["status"] == "monitored" and rec["stop_present"] is True
    env.fake.set_et("2026-10-06", "09:30")
    rec, _ = run(env, "open")
    assert rec["status"] == "abstained" and env.fake.post_log == []


# ---------------------------------------------------------------- expiry / hold
def test_expiry_blocks_orders_cancels_pilot_orders_and_reports_hold_with_positions(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    env.fake.fill("kim-2026-10-06-DBC")
    env.fake.orders.append({"id": "w-9", "client_order_id": "kim-2026-10-09-SPY", "symbol": "SPY", "side": "buy", "qty": "2",
                            "filled_qty": "0", "status": "accepted"})
    led = kp.open_ledger(env)
    from kim_order_logic import IntendedOrderSpec
    led.record_intent(IntendedOrderSpec("kim-2026-10-09-SPY", "SPY", 2, "buy"), "2026-10-09", 4)
    led.update_status("kim-2026-10-09-SPY", "w-9", "accepted", 0)
    kp.close_ledgers(env)
    env.fake.set_et("2026-10-09", "15:00")
    rec, _ = run(fresh_env(env, tmp_path), "expiry")
    assert rec["status"] == "abstained"                                       # not closed yet
    env.fake.set_et("2026-10-09", "16:10")
    rec, code = run(fresh_env(env, tmp_path), "expiry")
    assert code == 0 and rec["status"] == "HOLD_WITH_POSITIONS", rec
    assert env.fake.delete_log == ["w-9"] and rec["open_orders_remaining"] == 0
    assert rec["positions"]["DBC"] == env.fake.positions["DBC"]                # positions retained, nothing liquidated
    assert (tmp_path / "HOLD").exists() and (tmp_path / "EXPIRED").exists()
    assert not [o for o in env.fake.post_log if o["client_order_id"].endswith("-liq")]
    for mode in ("open", "monitor", "preflight"):
        env.fake.set_et("2026-10-09", "09:30")
        r, _ = run(fresh_env(env, tmp_path), mode)
        assert r["status"] == "abstained" and "HOLD" in r["reason"]


def test_receipts_contain_no_credentials_or_account_identity(tmp_path):
    env = armed(tmp_path)
    run(env, "open")
    blob = "".join(p.read_text() for p in (tmp_path / "receipts").glob("*.json"))
    assert ACCOUNT_ID not in blob and "APCA" not in blob and pin_for(env.fake)["account_id_sha256"] not in blob
