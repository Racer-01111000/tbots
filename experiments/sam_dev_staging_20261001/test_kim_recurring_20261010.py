"""Regression suite for the Kim RECURRING unattended paper lifecycle (kim_recurring.py). The REAL recurring runner, REAL pilot guard
logic, REAL AlpacaPaperBroker, REAL ledger and REAL identity checks run against an in-memory Alpaca (fake_alpaca_recurring). Only the
decision weights are stubbed where a test needs a predictable target. No network, no credentials, no orders."""
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

import alpaca_adapter as aa
import kim_paper_pilot as kp
import kim_recurring as kr
from alpaca_paper_broker import AlpacaPaperBroker
from fake_alpaca_pilot import pin_for
from fake_alpaca_recurring import FakeRecurringAlpaca

HERE = Path(__file__).resolve().parent
FLAG = "true-i-understand-the-risk"


@pytest.fixture(autouse=True)
def stub_decide(monkeypatch):
    box = {"weights": {"DBC": 0.18}}
    monkeypatch.setattr(kp.control_agent, "decide", lambda view, genome: {"selected": list(box["weights"]), "weights": dict(box["weights"]), "evaluations": {}})
    return box


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree_hash(d: Path) -> dict:
    return {str(p.relative_to(d)): sha(p) for p in sorted(Path(d).rglob("*")) if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def pilot_env(tmp, fake, gate=True):
    cfg, csha = kp.load_config(HERE / "kim_paper_pilot_config.json")
    broker = AlpacaPaperBroker({"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}, http=fake.http, submission_gate=lambda: gate)
    return kp.Env(cfg=cfg, cfg_sha=csha, get_json=fake.get_json, headers={}, broker=broker, now_utc=fake.now_utc,
                  submission_gate=lambda: gate, state_dir=tmp, pin=pin_for(fake), sleep=fake.sleep)


def build_archive(tmp, fake):
    """Reproduce what the real four-session pilot left behind: buy 554 DBC on session 0, then expiry -> HOLD/EXPIRED."""
    arch = tmp / "archived_pilot"
    env = pilot_env(arch, fake)
    fake.fill_on_post = True
    assert kp.run_mode(env, "freeze-campaign", "2026-10-06")[1] == 0
    assert kp.run_mode(env, "init")[1] == 0
    rec, code = kp.run_mode(env, "open")
    assert rec["status"] == "submitted", rec
    for o in fake.orders:
        if o["client_order_id"].startswith("kim-2026"):
            fake.record_fill_activity(o["client_order_id"])
    diag = next(o for o in fake.orders if o["client_order_id"].startswith("kim-manual"))
    fake.activities.insert(0, {"id": "act-d", "order_id": diag["id"], "side": "buy", "qty": "1"})
    fake.fill_on_post = False
    fake.set_et("2026-10-09", "16:10")
    rec, code = kp.run_mode(env, "expiry")
    assert rec["status"] == "HOLD_WITH_POSITIONS", rec
    return arch


def write_manifest(arch, path):
    names = ("order_ledger.sqlite3", "cadence_state.json", "peak_equity_state.json", "pilot_baseline.json")
    man = {"fills_after": "2026-10-01T00:00:00Z", "ledger_rows_digest": kr.ledger_rows_digest(arch / "order_ledger.sqlite3", immutable=True),
           "source_sha256": {n: sha(arch / n) for n in names},
           "archived_pilot_evidence_sha256": {n: sha(arch / n) for n in ("HOLD", "EXPIRED", "campaign.json")}}
    path.write_text(json.dumps(man))
    return man


class World:
    pass


def make_env(tmp, fake, arch, gate=True, cfg_patch=None, pin_patch=None, budget=None):
    cfg, csha = kr.load_config(HERE / "kim_recurring_config.json")
    cfg = {**cfg, "state_dir": str(tmp / "recurring"), "archived_pilot_state_dir": str(arch), "backup_dir": str(tmp / "backups"),
           "snapshot_lock_wait_s": 0.4, "s3_bucket": "test-bucket", "offload_config_file": str(tmp / "no-such-offload.json"),
           "backup_min_free_mb": 1, "backup_max_pending": 14, **(cfg_patch or {})}
    box = {"open": gate}
    broker = AlpacaPaperBroker({"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}, http=kr.safe_http(fake.http), submission_gate=lambda: box["open"])
    env = kp.Env(cfg=cfg, cfg_sha=csha, get_json=kr.resilient_get_json(fake.get_json, fake.sleep, budget), headers={}, broker=broker,
                 now_utc=fake.now_utc, submission_gate=lambda: box["open"], state_dir=Path(cfg["state_dir"]),
                 pin={**pin_for(fake), **(pin_patch or {})}, sleep=fake.sleep)
    env.gate_box, env.fake = box, fake
    return env


@pytest.fixture
def world(tmp_path):
    fake = FakeRecurringAlpaca(now_et=("2026-10-06", "09:30:05"))
    arch = build_archive(tmp_path, fake)
    man = tmp_path / "manifest.json"
    write_manifest(arch, man)
    fake.set_et("2026-10-10", "11:00")                      # the Saturday the GO was given
    w = World()
    w.fake, w.arch, w.manifest, w.tmp = fake, arch, man, tmp_path
    fake.post_log.clear()                                    # forget the archived pilot's own order
    w.arch_hashes = tree_hash(arch)
    w.pilot = fake.positions["DBC"] - 1
    w.env = make_env(tmp_path, fake, arch)
    return w


def migrate(w, env=None):
    env = env or w.env
    fd = kp.acquire_lock(env)
    try:
        return kr.run_migrate(env, w.manifest)
    finally:
        kp.close_ledgers(env)
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.fixture
def mig(world):
    r = migrate(world)
    assert r["status"] == "migrated", r
    world.mig = r
    return world


def fresh(w, **kw):
    return make_env(w.tmp, w.fake, w.arch, w.env.gate_box["open"], **kw)


def run(w, mode, env=None):
    return kr.run_mode(env or fresh(w), mode)


def receipts(w, kind=None):
    d = Path(w.env.cfg["state_dir"]) / "receipts"
    return sorted(p for p in d.glob("*.json") if not kind or p.name.endswith(f"_{kind}.json")) if d.exists() else []


def ledger_ids(w):
    c = sqlite3.connect(Path(w.env.cfg["state_dir"]) / "order_ledger.sqlite3")
    return [r[0] for r in c.execute("SELECT client_order_id FROM order_intents ORDER BY client_order_id")]


def make_buy_due(w, target="2026-12-14"):
    """Make `target` the step-50 session AND leave the account under-invested so the frozen weights call for a BUY."""
    set_due(w, target)
    w.fake.cash += 20000
    w.fake.set_et(target, "09:30:05")


def set_due(w, target, last=0):
    """Make `target` the exact step-50 session of the cadence by moving the anchor 50 sessions earlier (continuation test aid)."""
    s = w.fake.sessions
    anchor = s[s.index(target) - 50]
    p = Path(w.env.cfg["state_dir"]) / "cadence_state.json"
    c = json.loads(p.read_text())
    c.update(anchor_session=anchor, session_zero_date=anchor, last_rebalanced_step=last)
    p.write_text(json.dumps(c))


# =============================================================================================== config / seal / live endpoint
def test_config_matches_authorized_terms_and_cannot_loosen():
    cfg, _ = kr.load_config(HERE / "kim_recurring_config.json")
    assert cfg["max_orders_per_session"] == 4 and cfg["max_liquidation_orders_per_session"] == 8 and cfg["drawdown_halt_pct"] == 0.08
    assert cfg["max_asset_weight"] == 0.18 == cfg["gross_exposure_ceiling"] and cfg["time_in_force"] == "day" and cfg["extended_hours"] is False
    assert cfg["open_window_et"] == ["09:30", "09:35"] and cfg["rebalance_every_n_sessions"] == 50 and "campaign_sessions" not in cfg
    for patch in ({"max_orders_per_session": 5}, {"drawdown_halt_pct": 0.09}, {"max_asset_weight": 0.2}, {"time_in_force": "gtc"},
                  {"extended_hours": True}, {"open_window_et": ["09:30", "09:45"]}, {"paper_host": "https://api.alpaca.markets"}):
        p = HERE / "_tmp_cfg.json"
        p.write_text(json.dumps({**cfg, **patch}))
        try:
            with pytest.raises(kp.HardStop):
                kr.load_config(p)
        finally:
            p.unlink()


def test_seal_accepts_only_the_recurring_config(monkeypatch):
    assert aa.SEALED_PILOT_CONFIG.name == "kim_recurring_config.json" and aa.SEALED_SCOPE == "alpaca_paper_only_sealed_kim_recurring"
    monkeypatch.setenv("TBOTS_ALPACA_SUBMISSION_ENABLED", FLAG)
    monkeypatch.setenv("TBOTS_KIM_PAPER_SEAL", sha(aa.SEALED_PILOT_CONFIG))
    assert aa.submission_enabled() is True
    monkeypatch.setenv("TBOTS_KIM_PAPER_SEAL", sha(HERE / "kim_paper_pilot_config.json"))
    assert aa.submission_enabled() is False                 # the archived pilot config can no longer arm anything


def test_live_endpoint_rejected_and_latched(mig):
    env = fresh(mig)
    env.trading_host = "https://api.alpaca.markets"
    mig.fake.set_et("2026-10-12", "09:31")
    rec, code = kr.run_mode(env, "open")
    assert code == kr.EXIT_HARD_STOP and (Path(env.cfg["state_dir"]) / "STOP").exists() and mig.fake.post_log == [] or len(mig.fake.post_log) == 1
    with pytest.raises(aa.HostNotAllowedError):
        AlpacaPaperBroker({}, http=lambda *a, **k: (200, {}), host="https://api.alpaca.markets", submission_gate=lambda: True)


# =============================================================================================== migration / continuation
def test_migrate_preserves_state_byte_for_byte_and_archive_untouched(world):
    mig = world
    before = world.arch_hashes
    r = migrate(world)
    assert r["status"] == "migrated" and len(r["migrated_state_identity_sha256"]) == 64
    dest = Path(world.env.cfg["state_dir"])
    for n in ("cadence_state.json", "peak_equity_state.json", "pilot_baseline.json"):
        assert sha(dest / n) == sha(world.arch / n)
    assert kr.ledger_rows_digest(dest / "order_ledger.sqlite3") == kr.ledger_rows_digest(world.arch / "order_ledger.sqlite3", immutable=True)
    assert not (dest / "HOLD").exists() and not (dest / "EXPIRED").exists() and not (dest / "campaign.json").exists()
    assert tree_hash(world.arch) == before                      # expired pilot evidence intact, bit for bit
    cont = json.loads((dest / "continuation.json").read_text())
    assert cont["not_cleared"][:2] == ["STOP", "halt_state"] and cont["reconciliation"]["fills_equal_ledger"] is True
    assert cont["reconciliation"]["positions"] == {"DBC": mig.pilot + 1} and cont["reconciliation"]["pilot_net_filled"] == {"DBC": mig.pilot}
    assert cont["reconciliation"]["cadence"]["last_rebalanced_step"] == 0
    again = migrate(world)
    assert again["status"] == "already_migrated"


def test_migrate_refuses_tampered_source(world):
    p = world.arch / "peak_equity_state.json"
    os.chmod(p, 0o644)
    d = json.loads(p.read_text())
    d["peak_equity"] += 1000
    p.write_text(json.dumps(d))
    with pytest.raises(kp.HardStop):
        migrate(world)
    assert not (Path(world.env.cfg["state_dir"]) / "continuation.json").exists()


@pytest.mark.parametrize("sentinel", ["STOP", "halt_state.json"])
def test_migrate_refuses_source_stop_or_latched_halt(world, sentinel):
    (world.arch / sentinel).write_text(json.dumps({"halted": True}))
    with pytest.raises(kp.HardStop):
        migrate(world)


def test_migrate_refuses_unexplained_broker_state(world):
    world.fake.positions["SPY"] = 3                              # a holding nobody explains
    with pytest.raises(kp.HardStop):
        migrate(world)
    assert not (Path(world.env.cfg["state_dir"]) / "continuation.json").exists()


def test_migrate_refuses_fill_activity_that_disagrees_with_ledger(world):
    world.fake.activities[-1]["qty"] = "553"
    with pytest.raises(kp.HardStop, match="fill activities"):
        migrate(world)


def test_migrate_refuses_open_broker_orders(world):
    world.fake.orders.append({"id": "x", "client_order_id": "kim-2026-10-09-SPY", "symbol": "SPY", "side": "buy", "qty": "1",
                              "filled_qty": "0", "status": "accepted", "time_in_force": "day"})
    with pytest.raises(kp.HardStop):
        migrate(world)


def test_runner_refuses_without_continuation_record(world):
    world.fake.set_et("2026-10-12", "09:31")
    rec, code = run(world, "open")
    assert code == kr.EXIT_HARD_STOP and "continuation" in rec["reason"]


# =============================================================================================== recurring sessions
def test_first_continued_session_holds_and_keeps_cadence_peak_ledger_holdings(mig):
    d = Path(mig.env.cfg["state_dir"])
    peak0, cad0, led0 = sha(d / "peak_equity_state.json"), json.loads((d / "cadence_state.json").read_text()), kr.ledger_rows_digest(d / "order_ledger.sqlite3")
    mig.fake.set_et("2026-10-12", "09:30:05")
    rec, code = run(mig, "open")
    assert code == 0 and rec["status"] == "hold_not_due" and rec["step"] == 4 and rec["positions"] == {"DBC": mig.pilot + 1}
    assert mig.fake.post_log == [] and sha(d / "peak_equity_state.json") == peak0
    cad = json.loads((d / "cadence_state.json").read_text())
    assert cad["last_rebalanced_step"] == cad0["last_rebalanced_step"] == 0 and cad["anchor_session"] == "2026-10-06"
    assert kr.ledger_rows_digest(d / "order_ledger.sqlite3") == led0


def test_duplicate_starts_are_noops_and_overlap_is_refused(mig):
    mig.fake.set_et("2026-10-12", "09:30:05")
    assert run(mig, "open")[0]["status"] == "hold_not_due"
    n = len(receipts(mig))
    rec, code = run(mig, "open")                                  # 09:32 firing
    assert code == 0 and rec["status"] == "quiet" and len(receipts(mig)) == n
    env = fresh(mig)
    fd = kp.acquire_lock(env)
    try:
        rec, code = kr.run_mode(fresh(mig), "monitor")
        assert code == kr.EXIT_BUSY
    finally:
        os.close(fd)


def test_holiday_is_not_a_session_and_nothing_trades(mig):
    fake = FakeRecurringAlpaca(now_et=("2026-11-26", "09:30:05"), holidays=("2026-11-26",))
    fake.positions, fake.cash, fake.orders = mig.fake.positions, mig.fake.cash, mig.fake.orders
    mig.fake = fake
    for mode in ("preflight", "open", "close"):
        rec, code = run(mig, mode)
        assert code == 0 and rec["status"] == "not_a_session" and not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()
    n = len(receipts(mig))
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "quiet" and len(receipts(mig)) == n and fake.post_log == []


def test_early_close_session_hours_and_close_slot(mig):
    fake = FakeRecurringAlpaca(now_et=("2026-11-27", "13:02:00"), holidays=("2026-11-26",), early_close={"2026-11-27": "13:00"})
    fake.positions, fake.cash, fake.orders = mig.fake.positions, mig.fake.cash, mig.fake.orders
    mig.fake = fake
    fake.set_et("2026-11-27", "13:02:00")
    assert run(mig, "close")[0]["status"] == "quiet"              # 13:02 is not yet close + 5 min
    fake.set_et("2026-11-27", "13:10")
    rec, code = run(mig, "close")
    assert code == 0 and rec["status"] == "closed_reconciled" and rec["early_close"] is True and rec["close_et"] == "13:00"
    assert run(mig, "close")[0]["status"] == "quiet"              # the 13:20 slot and the 16:xx slots are no-ops
    fake.set_et("2026-11-27", "13:25")
    assert run(mig, "monitor")[0]["status"] == "monitored"        # grace window after the early close
    fake.set_et("2026-11-27", "14:00")
    assert run(mig, "monitor")[0]["status"] == "quiet"
    fake.set_et("2026-11-27", "16:10")
    assert run(mig, "close")[0]["status"] == "quiet"


def test_dst_transition_open_window_uses_new_york_wall_clock(mig):
    for day, off in (("2026-10-30", "-0400"), ("2026-11-02", "-0500")):
        row = kr.session_row(fresh(mig), day) if False else {"date": day, "open": "09:30", "close": "16:00"}
        st = kp.session_times(row)
        assert st["utc_offset_at_open"] == off
    mig.fake.set_et("2026-11-02", "09:30:05")                    # first session after DST ends: 14:30Z
    assert mig.fake.now.strftime("%H:%M") == "14:35" or mig.fake.now.hour == 14
    rec, code = run(mig, "open")
    assert code == 0 and rec["status"] in ("hold_not_due", "missed_rebalance_not_caught_up")


def test_missed_opening_window_never_catches_up(mig):
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-14", "09:36")
    rec, code = run(mig, "open")
    assert rec["status"] == "abstained" and "window closed" in rec["reason"] and mig.fake.post_log == []
    mig.fake.set_et("2026-12-14", "10:30")
    assert run(mig, "open")[0]["status"] == "abstained" and mig.fake.post_log == []
    mig.fake.set_et("2026-12-15", "09:30:05")                    # next session: the due step has passed unserved
    rec, code = run(mig, "open")
    assert rec["status"] == "missed_rebalance_not_caught_up" and mig.fake.post_log == []
    d = Path(mig.env.cfg["state_dir"])
    assert json.loads((d / "cadence_state.json").read_text())["last_rebalanced_step"] == 50
    mig.fake.set_et("2026-12-16", "09:30:05")
    assert run(mig, "open")[0]["status"] == "hold_not_due"


def test_absent_session_instance_down_then_step_after_due_does_not_trade(mig):
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-15", "09:30:05")                    # nothing ran on the due day
    assert run(mig, "open")[0]["status"] == "missed_rebalance_not_caught_up" and mig.fake.post_log == []


def test_due_session_rebalances_once_within_caps_and_never_sells_baseline(mig):
    set_due(mig, "2026-12-14")
    mig.fake.cash += 20000
    mig.fake.set_et("2026-12-14", "09:15")
    rec, code = run(mig, "preflight")
    assert rec["status"] == "preflight_ok" and rec["rebalance_due"] is True and "prepared" in rec and mig.fake.post_log == []
    mig.fake.set_et("2026-12-14", "09:30:05")
    rec, code = run(mig, "open")
    assert rec["status"] in ("submitted", "decided_no_orders"), rec
    cfg = mig.env.cfg
    eq = float(mig.fake.equity())
    gross = sum(q * mig.fake.mark(s) for s, q in mig.fake.positions.items())
    assert len(mig.fake.post_log) <= cfg["max_orders_per_session"]
    assert all(o["side"] == "buy" for o in mig.fake.post_log)
    if mig.fake.post_log:
        mig.fake.fill_on_post = True
    assert mig.fake.positions.get("DBC", 0) >= mig.pilot + 1
    assert run(mig, "open")[0]["status"] == "quiet"
    assert len(mig.fake.post_log) <= 1                           # one order, one session, no duplicates
    assert gross / eq <= 1.0


def test_target_zero_sells_only_pilot_owned_never_the_baseline_share(mig, stub_decide):
    stub_decide["weights"] = {}
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-14", "09:30:05")
    rec, code = run(mig, "open")
    sells = [p for p in mig.fake.post_log if p["side"] == "sell"]
    assert rec["status"] == "submitted" and sells and sum(int(p["qty"]) for p in sells) <= mig.pilot
    for p in sells:
        mig.fake.fill(p["client_order_id"])
    assert mig.fake.positions == {"DBC": 1}                       # the protected baseline share survives a full exit
    mig.fake.set_et("2026-12-14", "10:00")
    assert run(mig, "monitor")[0]["status"] == "monitored"


def test_holdings_already_over_cap_do_not_stop_anything_and_are_never_bought_into(mig):
    mig.fake.cash -= 4000                                         # equity shrinks, so the held DBC now exceeds 18% of equity
    mig.fake.set_et("2026-10-12", "09:15")
    assert run(mig, "preflight")[0]["status"] == "preflight_ok"
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "monitored" and not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-14", "09:30:05")
    rec, code = run(mig, "open")
    assert code == 0 and rec["status"] in ("decided_no_orders", "order_refused", "submitted")
    assert not any(o["side"] == "buy" and o["symbol"] == "DBC" for o in mig.fake.post_log)   # no buy while already over the cap
    assert not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()


# =============================================================================================== orders: ambiguity / partial fills
def test_order_accepted_but_response_lost_is_reconciled_not_duplicated(mig):
    make_buy_due(mig)
    mig.fake.fail["post"] = "timeout_after_accept_once"
    rec, code = run(mig, "open")
    assert rec["status"] == "submitted" and len(mig.fake.post_log) == 1
    assert run(mig, "open")[0]["status"] == "quiet" and len(mig.fake.post_log) == 1


def test_ambiguous_post_then_lookup_404_resubmits_same_client_id_once(mig):
    make_buy_due(mig)
    mig.fake.fail["post"] = "timeout"
    rec, code = run(mig, "open")
    assert rec["status"] == "blocked_or_uncertain" and rec["escalate"] is True
    cid = rec["submission"]["results"][0]["client_order_id"]
    assert len(mig.fake.post_log) == 1 and not any(o["client_order_id"] == cid for o in mig.fake.orders)
    del mig.fake.fail["post"]
    mig.fake.set_et("2026-12-14", "09:32:05")                    # second firing of the same window
    rec, code = run(mig, "open")
    assert rec["status"] == "submitted"
    assert [o["client_order_id"] for o in mig.fake.orders].count(cid) == 1
    assert [p["client_order_id"] for p in mig.fake.post_log] == [cid, cid]


def test_garbage_200_body_on_post_is_uncertain_and_reconciled(mig):
    make_buy_due(mig)
    mig.fake.http_script["POST"] = ["garbage"]
    rec, code = run(mig, "open")
    assert code == 0 and rec["status"] == "blocked_or_uncertain" and not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()


def test_partial_fill_is_explained_and_does_not_latch(mig):
    make_buy_due(mig)
    rec, code = run(mig, "open")
    cid = rec["submission"]["results"][0]["client_order_id"]
    assert mig.fake.post_log[0]["side"] == "buy" and int(mig.fake.post_log[0]["qty"]) > 10
    mig.fake.fill(cid, 10)
    mig.fake.set_et("2026-12-14", "09:40")
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "monitored" and rec["pilot_net_filled"]["DBC"] == mig.pilot + 10
    mig.fake.fill(cid)
    mig.fake.set_et("2026-12-14", "16:10")
    rec, code = run(mig, "close")
    assert rec["status"] == "closed_reconciled" and rec["open_orders"] == 0 and rec["unresolved_ledger_rows"] == 0


# =============================================================================================== failure classification
def test_transient_5xx_retries_with_backoff_then_succeeds(mig):
    mig.fake.script["/v2/account"] = [503, 502]
    mig.fake.set_et("2026-10-12", "10:00")
    t0 = mig.fake.now
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "monitored"
    assert (mig.fake.now - t0).total_seconds() == sum(kr.RETRY_DELAYS_S[:2])


def test_transient_failure_exhausts_retries_then_abstains_without_latching(mig):
    mig.fake.script["/v2/account"] = [503] * 20
    mig.fake.set_et("2026-10-12", "10:00")
    t0 = mig.fake.now
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "abstained" and "retry limit" in rec["reason"]
    assert (mig.fake.now - t0).total_seconds() == sum(kr.RETRY_DELAYS_S) and not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()
    mig.fake.script.clear()
    assert run(mig, "monitor")[0]["status"] == "monitored"        # the next 5-minute slot simply works


@pytest.mark.parametrize("shape", ["oserror", "garbage", 429, 500, 504])
def test_http_failure_shapes_are_transient_not_latched(mig, shape):
    mig.fake.script["/v2/clock"] = [shape] * 30
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = run(mig, "monitor")
    assert code == 0 and rec["status"] == "abstained" and "retry limit" in rec["reason"]      # retried, then gave up quietly
    assert not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()
    assert not (Path(mig.env.cfg["state_dir"]) / "unexpected_error_streak.json").exists()   # classified, not "unexpected"


def test_retry_budget_is_bounded_per_run(mig):
    budget = kr.RetryBudget(limit=1)
    mig.fake.script["/v2/account"] = [503, 503]
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = kr.run_mode(fresh(mig, budget=budget), "monitor")
    assert rec["status"] == "abstained" and budget.used == 1


@pytest.mark.parametrize("url_frag,status,latched", [("/v2/account", 401, True), ("/v2/clock", 403, True), ("/v2/positions", 401, True)])
def test_credential_failures_on_paper_api_latch(mig, url_frag, status, latched):
    mig.fake.script[url_frag] = [status]
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = run(mig, "monitor")
    assert code == kr.EXIT_HARD_STOP and (Path(mig.env.cfg["state_dir"]) / "STOP").exists() and "credential" in rec["reason"]


def test_data_host_403_is_an_abstain_not_a_latch(mig):
    set_due(mig, "2026-12-14")
    mig.fake.script["data.alpaca.markets"] = [403] * 40
    mig.fake.set_et("2026-12-14", "09:30:05")
    rec, code = run(mig, "open")
    assert code == 0 and rec["status"] == "abstained" and mig.fake.post_log == [] and not (Path(mig.env.cfg["state_dir"]) / "STOP").exists()


def test_unexpected_errors_abstain_then_latch_after_the_streak_limit(mig, monkeypatch):
    def boom(env):
        raise KeyError("shape the code has never seen")
    monkeypatch.setitem(kr.DISPATCH, "monitor", boom)
    mig.fake.set_et("2026-10-12", "10:00")
    for i in range(kr.UNEXPECTED_STREAK_LIMIT - 1):
        rec, code = run(mig, "monitor")
        assert code == 0 and rec["status"] == "abstained", i
    rec, code = run(mig, "monitor")
    assert code == kr.EXIT_HARD_STOP and (Path(mig.env.cfg["state_dir"]) / "STOP").exists()


def test_unexpected_streak_resets_on_a_clean_run(mig, monkeypatch):
    orig = kr.DISPATCH["monitor"]
    monkeypatch.setitem(kr.DISPATCH, "monitor", lambda env: (_ for _ in ()).throw(KeyError("x")))
    mig.fake.set_et("2026-10-12", "10:00")
    for _ in range(3):
        run(mig, "monitor")
    monkeypatch.setitem(kr.DISPATCH, "monitor", orig)
    assert run(mig, "monitor")[0]["status"] == "monitored"
    assert not (Path(mig.env.cfg["state_dir"]) / "unexpected_error_streak.json").exists()


# =============================================================================================== integrity failures stay latched
def test_identity_drift_latches_and_no_order_is_sent(mig):
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-14", "09:30:05")
    env = fresh(mig)
    env.cfg = {**env.cfg, "genome_id": "gen_" + "0" * 64}
    rec, code = kr.run_mode(env, "open")
    assert code == kr.EXIT_HARD_STOP and mig.fake.post_log == []


def test_account_mismatch_latches(mig):
    mig.fake.set_et("2026-10-12", "10:00")
    env = fresh(mig, pin_patch={"account_id_sha256": "0" * 64})
    rec, code = kr.run_mode(env, "monitor")
    assert code == kr.EXIT_HARD_STOP and (Path(env.cfg["state_dir"]) / "STOP").exists()


def test_unexplained_holding_and_unexplained_order_latch(mig):
    mig.fake.set_et("2026-10-12", "10:00")
    mig.fake.positions["SPY"] = 2
    rec, code = run(mig, "monitor")
    assert code == kr.EXIT_HARD_STOP and "not explained" in rec["reason"]


def test_unexplained_order_in_broker_latches(mig):
    mig.fake.orders.append({"id": "zz", "client_order_id": "someone-else-1", "symbol": "SPY", "side": "buy", "qty": "1", "filled_qty": "0",
                            "status": "canceled", "time_in_force": "day"})
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = run(mig, "monitor")
    assert code == kr.EXIT_HARD_STOP and "not in the pilot ledger" in rec["reason"]


def test_stop_persists_blocks_new_exposure_and_is_never_auto_cleared(mig):
    d = Path(mig.env.cfg["state_dir"])
    kp.write_stop(fresh(mig), "test latch")
    stop0 = sha(d / "STOP")
    set_due(mig, "2026-12-14")
    mig.fake.set_et("2026-12-14", "09:15")
    assert run(mig, "preflight")[0]["reason"].startswith("STOP in force")
    mig.fake.set_et("2026-12-14", "09:30:05")
    assert run(mig, "open")[0]["status"] == "abstained" and mig.fake.post_log == []
    mig.fake.set_et("2026-12-14", "10:00")
    assert run(mig, "monitor")[0]["status"] == "monitored"        # reconciliation keeps running under STOP
    assert run(mig, "close")[0]["status"] in ("quiet", "closed_reconciled")
    assert sha(d / "STOP") == stop0
    assert migrate(mig)["status"] == "already_migrated" and sha(d / "STOP") == stop0


# =============================================================================================== halt, baseline, peak
def test_drawdown_halt_liquidates_only_pilot_shares_keeps_baseline_and_never_resets_peak(mig):
    d = Path(mig.env.cfg["state_dir"])
    peak0 = json.loads((d / "peak_equity_state.json").read_text())
    mig.fake.set_et("2026-10-12", "10:00")
    mig.fake.crash(0.4)
    mig.fake.http_script.clear()
    rec, code = run(mig, "monitor")
    assert rec["status"] == "halted" and rec["halt_latched"] is True
    sells = [p for p in mig.fake.post_log if p["side"] == "sell"]
    assert len(sells) <= 8 and sum(int(p["qty"]) for p in sells) == mig.pilot and all(p["client_order_id"].endswith("-liq") for p in sells)
    mig.fake.fill(sells[0]["client_order_id"])
    mig.fake.set_et("2026-10-12", "10:05")
    rec, code = run(mig, "monitor")
    assert mig.fake.positions == {"DBC": 1} and rec["pilot_net_filled"] == {}
    assert json.loads((d / "peak_equity_state.json").read_text()) == peak0       # peak never reset to make continuation easier
    mig.fake.set_et("2026-10-13", "09:30:05")
    rec, code = run(mig, "open")
    assert rec["status"] == "abstained" and "halt" in rec["reason"]
    assert (d / "halt_state.json").exists()                                      # latched until a human decides




# =============================================================================================== receipts, housekeeping, restart
def test_monitor_appends_one_file_per_session_not_one_per_run(mig):
    mig.fake.set_et("2026-10-12", "09:35")
    for _ in range(12):
        assert run(mig, "monitor")[0]["status"] == "monitored"
        mig.fake.sleep(300)
    d = Path(mig.env.cfg["state_dir"]) / "receipts"
    assert [p.name for p in d.glob("monitor_*")] == ["monitor_2026-10-12.jsonl"]
    assert len((d / "monitor_2026-10-12.jsonl").read_text().splitlines()) == 12


def test_housekeeping_archives_only_after_verification(mig):
    d = Path(mig.env.cfg["state_dir"]) / "receipts"
    d.mkdir(exist_ok=True)
    old, new = d / "monitor_2026-08-01.jsonl", d / "monitor_2026-10-11.jsonl"
    old.write_text('{"a":1}\n')
    new.write_text('{"b":2}\n')
    want = sha(old)
    env = fresh(mig)
    r = kr.archive_old_monitor_logs(env)
    assert r["archived"] == 1 and not old.exists() and new.exists()
    arch = Path(env.cfg["state_dir"]) / "archive" / r["archive"]
    with tarfile.open(arch) as t:
        assert hashlib.sha256(t.extractfile("monitor_2026-08-01.jsonl").read()).hexdigest() == want
    assert json.loads(arch.with_suffix(arch.suffix + ".manifest.json").read_text())["sha256"] == r["sha256"]


def test_housekeeping_failed_verification_keeps_originals(mig, monkeypatch):
    d = Path(mig.env.cfg["state_dir"]) / "receipts"
    d.mkdir(exist_ok=True)
    old = d / "monitor_2026-08-01.jsonl"
    old.write_text('{"a":1}\n')
    env = fresh(mig)
    real = kr.hashlib.sha256
    seen = {"n": 0}

    def flaky(b=b""):
        if b == b'{"a":1}\n':
            seen["n"] += 1
            if seen["n"] >= 2:
                return real(b"bit rot")                          # the verification read-back disagrees
        return real(b)
    monkeypatch.setattr(kr.hashlib, "sha256", flaky)
    r = kr.archive_old_monitor_logs(env)
    assert r["archived"] == 0 and "error" in r and old.exists()


def test_restart_between_processes_keeps_ledger_ownership_and_peak(mig):
    mig.fake.set_et("2026-10-12", "10:00")
    assert run(mig, "monitor")[0]["status"] == "monitored"
    ids = ledger_ids(mig)
    e2 = fresh(mig)                                               # a "reboot": new objects, same state dir
    mig.fake.set_et("2026-10-12", "10:05")
    rec, code = kr.run_mode(e2, "monitor")
    assert rec["status"] == "monitored" and rec["pilot_net_filled"] == {"DBC": mig.pilot} and ledger_ids(mig) == ids


def test_close_writes_summary_marker_and_is_idempotent(mig):
    mig.fake.set_et("2026-10-12", "16:10")
    rec, code = run(mig, "close")
    assert rec["status"] == "closed_reconciled" and rec["reconciliation"] == "broker==ledger+baseline" and rec["pilot_net_filled"] == {"DBC": mig.pilot}
    assert (Path(mig.env.cfg["state_dir"]) / "close_done_2026-10-12.json").exists()
    assert run(mig, "close")[0]["status"] == "quiet"


# =============================================================================================== plan-check and units
def test_plan_check_forces_decision_path_without_writes_or_orders(mig):
    scratch = mig.tmp / "scratch_copy"
    shutil.copytree(mig.env.cfg["state_dir"], scratch)
    env = make_env(mig.tmp, mig.fake, mig.arch, gate=False, cfg_patch={"state_dir": str(scratch)})
    before = {k: v for k, v in tree_hash(scratch).items() if k.endswith(".json")}
    mig.fake.set_et("2026-10-10", "11:00")
    rec, code = kr.run_mode(env, "plan-check")
    assert code == 0 and rec["status"] == "plan_check_ok" and rec["next_session"] == "2026-10-12" and rec["next_session_step"] == 4
    assert rec["next_rebalance_due_step"] == 50 and rec["next_rebalance_due_session"] > "2026-12-01"
    assert "forced_decision" in rec and "forced_plan" in rec and mig.fake.post_log == []
    assert {k: v for k, v in tree_hash(scratch).items() if k.endswith(".json")} == before


def test_rendered_units_are_isolated_recurring_and_never_replay_missed_slots():
    from kim_recurring_ops import render_units as ru
    files = ru.render("a" * 64, "/opt/tbots-kim-recurring/releases/abc")
    assert {n for n in files if n.endswith(".timer")} == {f"tbots-kim-recurring-{m}.timer" for m in ("preflight", "open", "monitor", "close", "snapshot", "offload", "consolidate")}
    for n, t in files.items():
        assert "tbots-kim-paper" not in t and "/var/lib/tbots-kim-paper" not in t
        if n.endswith(".timer"):
            assert "Persistent=false" in t
            if not any(x in n for x in ("offload", "consolidate")):
                assert "America/New_York" in t and "Mon..Fri" in t
        if n.endswith(".service") and "hardstop" not in n:
            assert "Restart=no" in t and "ReadWritePaths=/var/lib/tbots-kim-recurring" in t
            assert ("OnFailure=tbots-kim-recurring-hardstop.service" in t) == (not any(x in n for x in ("snapshot", "offload", "consolidate")))
    assert "TBOTS_ALPACA_SUBMISSION_ENABLED" not in files["tbots-kim-recurring-preflight.service"]
    assert "TBOTS_ALPACA_SUBMISSION_ENABLED" not in files["tbots-kim-recurring-close.service"]
    assert "TBOTS_ALPACA_SUBMISSION_ENABLED" in files["tbots-kim-recurring-open.service"]
    assert "OnCalendar" in files["tbots-kim-recurring-open.timer"] and files["tbots-kim-recurring-open.timer"].count("OnCalendar") == 3
    assert "expir" not in " ".join(files).lower() and "hold" not in " ".join(files).lower().replace("hold.sh", "")
    assert "tbots-kim-recurring-hardstop" in files["hardstop.sh"] or "disable --now tbots-kim-recurring-open.timer" in files["hardstop.sh"]
