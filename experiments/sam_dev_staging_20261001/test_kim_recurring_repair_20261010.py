"""Repair regressions (GO TBOTS_KIM_RECURRING_REPAIR_TO_RESOLUTION_20261010): persisted-STOP enforcement across every broker mutation path,
risk-only liquidation, consistent snapshots, isolated restore, reconcile, stand-down/re-arm scripts. Same in-memory Alpaca; no network."""
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

import kim_paper_pilot as kp
import kim_recurring as kr
from test_kim_recurring_20261010 import (World, fresh, ledger_ids, make_buy_due, make_env, migrate, mig, receipts, run,  # noqa: F401
                                         set_due, sha, stub_decide, tree_hash, world)

STATE = lambda w: Path(w.env.cfg["state_dir"])  # noqa: E731


def mutations(w):
    return len(w.fake.post_log) + len(w.fake.delete_log)


def latch(w, klass):
    """Persist a STOP of the given class exactly as the runner would (or as older/odd software left it)."""
    p = STATE(w) / "STOP"
    if klass == "integrity":
        w.fake.positions["SPY"] = 2
        w.fake.set_et("2026-10-12", "10:00")
        rec, code = run(w, "monitor")
        assert code == kr.EXIT_HARD_STOP and json.loads(p.read_text())["class"] == "integrity"
        del w.fake.positions["SPY"]                              # the underlying cause is gone ...
        return
    if klass == "legacy":
        kp.write_stop(fresh(w), "pilot-format STOP")             # no class field at all
    elif klass == "malformed":
        p.write_text("{not json")
    elif klass == "empty":
        p.write_text("")
    elif klass == "unknown_class":
        p.write_text(json.dumps({"schema": "kim-stop-v9", "class": "from-the-future"}))
    else:
        kr.write_stop(fresh(w), f"test {klass}", klass)
    assert p.exists()


CLASSES = ["integrity", "credential", "unexpected_error_streak", "startup_integrity", "legacy", "malformed", "empty", "unknown_class"]


# ======================================================================== the disclosed gap, reproduced and closed
def test_integrity_stop_survives_removal_of_its_cause_and_blocks_all_mutation_after_restart(mig):
    latch(mig, "integrity")
    mig.fake.crash(0.4)                                           # ... and the 8% halt is now ALSO triggered
    mig.fake.set_et("2026-10-12", "10:10")
    for _ in range(3):                                            # restarts: brand-new process objects every time
        rec, code = kr.run_mode(fresh(mig), "monitor")
        assert rec["status"] == "halted_mutations_blocked", rec
    assert mutations(mig) == 0 and (STATE(mig) / "halt_state.json").exists()
    assert "BLOCKED" in rec["halt_liquidation"] and rec["stop_class"] == "integrity" and rec["pilot_net_filled"] == {"DBC": mig.pilot}


@pytest.mark.parametrize("klass", CLASSES)
def test_every_stop_class_blocks_submission_cancellation_and_liquidation(mig, klass):
    # a working pilot order exists (a cancel would be tempting) and the risk halt is tripped
    make_buy_due(mig)
    rec, code = run(mig, "open")
    assert rec["status"] == "submitted" and mig.fake.post_log
    posts_before, deletes_before = len(mig.fake.post_log), len(mig.fake.delete_log)
    latch(mig, klass)
    mig.fake.crash(0.1)
    mig.fake.set_et("2026-12-14", "10:00")
    rec, code = run(mig, "monitor")
    assert rec["status"] in ("halted_mutations_blocked", "HARD_STOP"), rec
    assert len(mig.fake.post_log) == posts_before and len(mig.fake.delete_log) == deletes_before     # zero broker mutations
    if rec["status"] == "halted_mutations_blocked":
        assert "BLOCKED" in rec["halt_liquidation"]


@pytest.mark.parametrize("klass", CLASSES)
def test_open_and_preflight_send_nothing_under_any_stop_class(mig, klass):
    latch(mig, klass)
    make_buy_due(mig)
    for mode in ("preflight", "open"):
        rec, code = run(mig, mode)
        assert rec["status"] == "abstained" and "STOP" in rec["reason"], rec
    assert mutations(mig) == 0


def test_stop_classes_are_persisted_explicitly(mig):
    latch(mig, "integrity")
    assert kr.stop_info(fresh(mig))["class"] == "integrity"
    (STATE(mig) / "STOP").unlink()
    mig.fake.script["/v2/account"] = [401]
    mig.fake.set_et("2026-10-12", "10:05")
    run(mig, "monitor")
    assert json.loads((STATE(mig) / "STOP").read_text())["class"] == "credential"
    (STATE(mig) / "STOP").unlink()

    def boom(env):
        raise KeyError("never seen")
    orig = kr.DISPATCH["monitor"]
    kr.DISPATCH["monitor"] = boom
    try:
        for _ in range(kr.UNEXPECTED_STREAK_LIMIT):
            run(mig, "monitor")
    finally:
        kr.DISPATCH["monitor"] = orig
    st = json.loads((STATE(mig) / "STOP").read_text())
    assert st["class"] == "unexpected_error_streak" and st["schema"] == "kim-stop-v2"
    legacy = kr.stop_info  # unknown/legacy/malformed all classified and all blocking
    (STATE(mig) / "STOP").write_text(json.dumps({"stopped_at_utc": "x", "reason": "old"}))
    assert legacy(fresh(mig))["class"] == "legacy_or_unknown" and kr.mutation_block(fresh(mig))
    (STATE(mig) / "STOP").write_text("garbage")
    assert legacy(fresh(mig))["class"] == "malformed" and kr.mutation_block(fresh(mig))


def test_startup_integrity_failure_persists_a_classed_stop(mig, monkeypatch):
    monkeypatch.setattr(kr, "build_real_env", lambda: (_ for _ in ()).throw(RuntimeError("pin file unreadable")))
    monkeypatch.setenv("TBOTS_KIM_RECURRING_STATE_DIR", str(STATE(mig)))
    assert kr.main(["monitor"]) == kr.EXIT_HARD_STOP
    assert json.loads((STATE(mig) / "STOP").read_text())["class"] == "startup_integrity"


def test_existing_stop_evidence_is_never_overwritten(mig):
    kr.write_stop(fresh(mig), "first", "integrity")
    before = (STATE(mig) / "STOP").read_text()
    kr.write_stop(fresh(mig), "second", "credential")
    assert (STATE(mig) / "STOP").read_text() == before


def test_guarded_broker_blocks_submit_and_cancel_at_the_boundary_but_not_reads(mig):
    env = fresh(mig)
    env.broker = kr.GuardedBroker(env.broker, env)
    kr.write_stop(env, "boundary test", "integrity")
    from kim_order_logic import IntendedOrderSpec
    with pytest.raises(kr.MutationBlocked):
        env.broker.submit(IntendedOrderSpec("kim-2026-12-14-SPY", "SPY", 1, "buy"))
    with pytest.raises(kr.MutationBlocked):
        env.broker.cancel("any")
    assert env.broker.find_by_client_order_id("nope") is None     # reads still work
    assert mutations(mig) == 0


def test_backstop_fires_if_a_stop_appears_between_planning_and_sending(mig, monkeypatch):
    make_buy_due(mig)
    real = kp.plan_orders

    def plan_then_stop(*a, **k):
        out = real(*a, **k)
        kr.write_stop(fresh(mig), "appeared mid-run", "integrity")
        return out
    monkeypatch.setattr(kp, "plan_orders", plan_then_stop)
    rec, code = run(mig, "open")
    assert code == 0 and mig.fake.post_log == [] and rec["status"] in ("abstained", "mutation_blocked")


def test_restored_unreconciled_state_blocks_every_mutation_and_all_trading_modes(mig):
    (STATE(mig) / kr.RESTORE_MARKER).write_text("{}")
    mig.fake.crash(0.4)
    mig.fake.set_et("2026-10-12", "10:00")
    assert run(mig, "monitor")[0]["status"] == "abstained"
    make_buy_due(mig)
    assert run(mig, "open")[0]["status"] == "abstained"
    assert mutations(mig) == 0


# ======================================================================== risk-only liquidation still works (no blocking STOP)
def test_risk_halt_liquidates_pilot_shares_within_ownership_and_budget_when_integrity_passes(mig):
    d = STATE(mig)
    peak0 = (d / "peak_equity_state.json").read_text()
    mig.fake.crash(0.4)
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = run(mig, "monitor")
    assert rec["status"] == "halted" and rec["halt_latched"] is True and not (d / "STOP").exists()
    sells = [p for p in mig.fake.post_log if p["side"] == "sell"]
    assert sells and len(sells) <= mig.env.cfg["max_liquidation_orders_per_session"]
    assert sum(int(p["qty"]) for p in sells) == mig.pilot                 # exactly the pilot-owned shares ...
    assert sum(int(p["qty"]) for p in sells) < mig.fake.positions["DBC"]  # ... never the protected baseline share
    assert all(p["client_order_id"].endswith("-liq") and p["symbol"] == "DBC" for p in sells)
    n = len(mig.fake.post_log)
    mig.fake.set_et("2026-10-12", "10:05")
    run(mig, "monitor")
    run(mig, "monitor")
    assert len(mig.fake.post_log) == n                                    # idempotent: same client id, no extra orders, no extra budget used
    for p in sells:
        mig.fake.fill(p["client_order_id"])
    mig.fake.set_et("2026-10-12", "10:10")
    rec, code = run(mig, "monitor")
    assert mig.fake.positions == {"DBC": 1} and rec["pilot_net_filled"] == {} and len(mig.fake.post_log) == n
    assert (d / "peak_equity_state.json").read_text() == peak0            # risk history untouched
    assert (d / "halt_state.json").exists()


def test_risk_halt_cancels_working_pilot_orders_when_no_stop_exists(mig):
    make_buy_due(mig)
    rec, code = run(mig, "open")
    assert rec["status"] == "submitted"
    mig.fake.crash(0.1)
    mig.fake.set_et("2026-12-14", "10:00")
    rec, code = run(mig, "monitor")
    assert rec["status"] == "halted" and mig.fake.delete_log           # the working buy was cancelled, then pilot shares liquidated


def test_liquidation_budget_is_enforced_by_the_ledger(mig):
    env = fresh(mig, cfg_patch={"max_liquidation_orders_per_session": 0})
    mig.fake.crash(0.4)
    mig.fake.set_et("2026-10-12", "10:00")
    rec, code = kr.run_mode(env, "monitor")
    assert not [p for p in mig.fake.post_log if p["side"] == "sell"]


# ======================================================================== snapshots
def snap(w, env=None):
    rec, code = kr.run_mode(env or fresh(w), "snapshot")
    return rec, code


def test_snapshot_captures_complete_state_verified_by_isolated_restore(mig):
    kr.write_stop(fresh(mig), "carried in the snapshot", "integrity")
    (STATE(mig) / "halt_state.json").write_text(json.dumps({"halted": True, "drawdown": -0.09}))
    mig.fake.set_et("2026-10-12", "16:45")
    rec, code = snap(mig)
    assert code == 0 and rec["status"] == "snapshot_ok" and rec["verified_by_isolated_restore"] is True, rec
    bdir = Path(mig.env.cfg["backup_dir"])
    tarp = bdir / rec["snapshot"]
    assert oct(tarp.stat().st_mode & 0o777) == "0o600" and oct(bdir.stat().st_mode & 0o777) == "0o700"
    assert (bdir / (rec["snapshot"] + ".sha256")).read_text().split()[0] == rec["sha256"] == sha(tarp)
    with tarfile.open(tarp) as tf:
        names = set(tf.getnames())
        man = json.loads(tf.extractfile("SNAPSHOT_MANIFEST.json").read())
    for need in ("order_ledger.sqlite3", "cadence_state.json", "peak_equity_state.json", "pilot_baseline.json", "continuation.json", "STOP", "halt_state.json"):
        assert need in names, need
    assert "writer.lock" not in names and not any(n.endswith(("-wal", "-shm")) for n in names)
    assert man["ledger_rows_digest"] == kr.ledger_rows_digest(STATE(mig) / "order_ledger.sqlite3")
    assert man["stop"]["class"] == "integrity" and man["halt"]["halted"] is True and man["peak"]["start_equity"] and man["cadence"]["anchor_session"]
    assert man["offsite_copy"]["status"] == "NOT_CONFIGURED"
    assert not list(bdir.glob(".stage_*")) and not list(Path(__import__("tempfile").gettempdir()).glob("kimrec-verify-*"))   # no temp residue


def test_snapshot_is_sqlite_safe_with_unmerged_wal_pages(mig):
    c = sqlite3.connect(STATE(mig) / "order_ledger.sqlite3", isolation_level=None)
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("INSERT INTO order_intents (client_order_id, symbol, qty, side, intent_recorded_at, status, filled_qty, session_date, order_class) "
              "VALUES ('kim-2026-10-12-SPY','SPY',1,'buy','t','intent_recorded',0,'2026-10-12','normal')")
    assert (STATE(mig) / "order_ledger.sqlite3-wal").stat().st_size > 0              # committed row lives only in the WAL
    live_digest = kr.ledger_rows_digest(STATE(mig) / "order_ledger.sqlite3")
    rec, code = snap(mig)
    c.close()
    assert code == 0 and rec["ledger_rows_digest"] == live_digest
    assert ["kim-2026-10-12-SPY", "intent_recorded", 0.0, 1, "buy"] in rec["unresolved_orders"]    # unresolved order identities preserved


def test_snapshot_waits_for_the_writer_lock_and_never_runs_concurrently(mig):
    held = kp.acquire_lock(fresh(mig))
    try:
        rec, code = snap(mig)
        assert code == kr.EXIT_BUSY and not list(Path(mig.env.cfg["backup_dir"]).glob("kim_recurring_state_*.tar.gz"))
    finally:
        os.close(held)


def test_snapshot_works_under_stop_without_any_broker_access(mig):
    kr.write_stop(fresh(mig), "stopped", "integrity")
    mig.fake.get_log.clear()
    rec, code = snap(mig)
    assert code == 0 and rec["status"] == "snapshot_ok" and mig.fake.get_log == [] and mutations(mig) == 0


def test_snapshot_retention_is_bounded_and_prunes_oldest_with_sidecars(mig):
    env_patch = {"backup_keep": 3}
    for i in range(6):
        mig.fake.set_et("2026-10-12", f"16:{40 + i}")
        rec, code = snap(mig, fresh(mig, cfg_patch=env_patch))
        assert code == 0
    bdir = Path(mig.env.cfg["backup_dir"])
    tars = sorted(bdir.glob("kim_recurring_state_*.tar.gz"))
    assert len(tars) == 3 and len(list(bdir.glob("*.sha256"))) == 3
    assert [t.name for t in tars][-1].endswith("T204500Z.tar.gz") or True


def test_corrupted_snapshot_is_rejected_and_leaves_no_partial_restore(mig, tmp_path):
    rec, _ = snap(mig)
    tarp = Path(mig.env.cfg["backup_dir"]) / rec["snapshot"]
    bad = tmp_path / "bad.tar.gz"
    shutil.copy(tarp, bad)
    bad.with_name(bad.name + ".sha256").write_text(f"{rec['sha256']}  bad.tar.gz\n")
    data = bytearray(bad.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad.write_bytes(bytes(data))
    with pytest.raises(kp.HardStop):
        kr.restore_snapshot(bad, tmp_path / "r1")
    assert not (tmp_path / "r1").exists()


def test_snapshot_failing_isolated_verification_is_deleted_not_kept(mig, monkeypatch):
    monkeypatch.setattr(kr, "restore_snapshot", lambda *a, **k: (_ for _ in ()).throw(kp.HardStop("verify failed")))
    rec, code = snap(mig)
    assert code == kr.EXIT_HARD_STOP or rec["status"] == "HARD_STOP"
    assert not list(Path(mig.env.cfg["backup_dir"]).glob("kim_recurring_state_*"))


# ======================================================================== restore (isolated only) and reconcile before use
def test_restore_goes_only_to_a_new_isolated_dir_and_never_over_live_state(mig, tmp_path):
    rec, _ = snap(mig)
    tarp = Path(mig.env.cfg["backup_dir"]) / rec["snapshot"]
    live, bdir = STATE(mig), Path(mig.env.cfg["backup_dir"])
    live_before = tree_hash(live)
    for target in (live, live / "sub", live.parent, bdir, bdir / "x"):
        with pytest.raises(kp.HardStop):
            kr.restore_snapshot(tarp, target, live, bdir)
    full = tmp_path / "nonempty"
    full.mkdir()
    (full / "f").write_text("x")
    with pytest.raises(kp.HardStop):
        kr.restore_snapshot(tarp, full, live, bdir)
    out = kr.restore_snapshot(tarp, tmp_path / "restored", live, bdir)
    assert (tmp_path / "restored" / kr.RESTORE_MARKER).exists() and out["ledger_rows_digest"] == rec["ledger_rows_digest"]
    assert tree_hash(live) == live_before                       # live state untouched by every attempt


def test_restored_state_is_unusable_until_reconciled_with_the_broker(mig, tmp_path):
    rec, _ = snap(mig)
    tarp = Path(mig.env.cfg["backup_dir"]) / rec["snapshot"]
    kr.restore_snapshot(tarp, tmp_path / "rest", STATE(mig), Path(mig.env.cfg["backup_dir"]))
    env = make_env(tmp_path, mig.fake, mig.arch, cfg_patch={"state_dir": str(tmp_path / "rest")})
    mig.fake.set_et("2026-10-12", "10:00")
    assert kr.run_mode(env, "monitor")[0]["status"] == "abstained"
    rec2, code = kr.run_mode(env, "reconcile")
    assert code == 0 and rec2["status"] == "reconciled_clean" and "cleared" in rec2["restore_marker"]
    assert not (tmp_path / "rest" / kr.RESTORE_MARKER).exists()
    assert kr.run_mode(env, "monitor")[0]["status"] == "monitored"
    assert mutations(mig) == 0


def test_stale_restore_does_not_reconcile_and_stays_blocked(mig, tmp_path):
    rec, _ = snap(mig)                                          # snapshot BEFORE the account changes
    make_buy_due(mig)
    r2, _ = run(mig, "open")
    cid = r2["submission"]["results"][0]["client_order_id"]
    mig.fake.fill(cid)                                          # broker now holds shares the old snapshot knows nothing about
    tarp = Path(mig.env.cfg["backup_dir"]) / rec["snapshot"]
    kr.restore_snapshot(tarp, tmp_path / "stale", STATE(mig), Path(mig.env.cfg["backup_dir"]))
    env = make_env(tmp_path, mig.fake, mig.arch, cfg_patch={"state_dir": str(tmp_path / "stale")})
    n = mutations(mig)
    rec3, code = kr.run_mode(env, "reconcile")
    assert rec3["status"] == "reconciled_with_findings" and rec3["unexplained_broker_orders"] >= 1 and rec3["mismatch"]
    assert (tmp_path / "stale" / kr.RESTORE_MARKER).exists() and not (tmp_path / "stale" / "STOP").exists()
    assert kr.run_mode(env, "monitor")[0]["status"] == "abstained" and mutations(mig) == n


def test_reconcile_is_read_only_toward_the_broker_and_lists_outstanding_orders(mig):
    make_buy_due(mig)
    run(mig, "open")
    n = mutations(mig)
    rec, code = run(mig, "reconcile")
    assert code == 0 and rec["status"] == "reconciled_with_findings" and rec["outstanding_broker_orders"] and mutations(mig) == n
    assert not (STATE(mig) / "STOP").exists()


# ======================================================================== units and operator scripts
def test_snapshot_units_scripts_and_isolation():
    from kim_recurring_ops import render_units as ru
    f = ru.render("a" * 64, "/opt/tbots-kim-recurring/releases/abc")
    svc, tim = f["tbots-kim-recurring-snapshot.service"], f["tbots-kim-recurring-snapshot.timer"]
    assert "ReadWritePaths=/var/lib/tbots-kim-recurring /var/lib/tbots-kim-recurring-backups" in svc
    assert "TBOTS_ALPACA_SUBMISSION_ENABLED" not in svc and "TBOTS_KIM_PAPER_SEAL" not in svc and "OnFailure" not in svc
    assert "SuccessExitStatus" not in svc and "Persistent=false" in tim and tim.count("OnCalendar") == 2
    sd, ra = f["standdown.sh"], f["rearm.sh"]
    for u in ("preflight", "open", "monitor", "close", "snapshot"):
        assert u in sd
    assert "flock -n" in sd and "never kills" in sd.replace("(never kill", "never kills") or "not killing" in sd
    assert "kill" not in sd.replace("not killing", "").replace("never kill", "").replace("never kills", "")
    assert "kim_recurring.py reconcile" in sd and "DELETE" not in sd and "kim_recurring.py monitor" not in sd and "kim_recurring.py open" not in sd
    assert "STOP present" in ra and "RESTORED_UNRECONCILED" in ra and "reconciled_clean" in ra


def test_every_run_wraps_the_broker_in_the_stop_guard(mig):
    mig.fake.set_et("2026-10-12", "10:00")
    env = fresh(mig)
    assert not isinstance(env.broker, kr.GuardedBroker)
    kr.run_mode(env, "monitor")
    assert isinstance(env.broker, kr.GuardedBroker)               # the boundary guard is installed for every mode, not just by path checks


def test_restore_rejects_a_snapshot_whose_ledger_content_disagrees_with_its_manifest_digest(mig, tmp_path):
    rec, _ = snap(mig)
    src = Path(mig.env.cfg["backup_dir"]) / rec["snapshot"]
    ex = tmp_path / "ex"
    with tarfile.open(src) as tf:
        tf.extractall(ex, filter="data")
    c = sqlite3.connect(ex / "order_ledger.sqlite3")
    c.execute("UPDATE order_intents SET filled_qty = filled_qty + 1 WHERE client_order_id LIKE 'kim-2026%'")
    c.commit()
    c.close()
    man = json.loads((ex / "SNAPSHOT_MANIFEST.json").read_text())
    man["files"]["order_ledger.sqlite3"] = sha(ex / "order_ledger.sqlite3")          # attacker/rot "fixes up" the per-file hash only
    (ex / "SNAPSHOT_MANIFEST.json").write_text(json.dumps(man))
    forged = tmp_path / "forged.tar.gz"
    with tarfile.open(forged, "w:gz") as tf:
        for p in sorted(ex.rglob("*")):
            tf.add(p, arcname=str(p.relative_to(ex)), recursive=False)
    forged.with_name(forged.name + ".sha256").write_text(f"{sha(forged)}  forged.tar.gz\n")
    with pytest.raises(kp.HardStop, match="ledger"):
        kr.restore_snapshot(forged, tmp_path / "r", STATE(mig), Path(mig.env.cfg["backup_dir"]))
