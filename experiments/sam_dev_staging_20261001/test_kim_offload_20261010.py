"""Tests for the off-instance backup offload and the three-month archive cycle (GO TBOTS_KIM_OFFLOAD_ARCHIVE_AND_REARM_20261010).
In-memory store with S3 semantics (conditional create, prefix list, fault injection); the real CLI wrapper is exercised through a recording runner.
No network, no credentials, no AWS calls."""
import hashlib
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import kim_offload as ko
import kim_recurring as kr
from test_kim_recurring_20261010 import fresh, make_buy_due, mig, run, set_due, sha, stub_decide, world  # noqa: F401
from test_kim_recurring_repair_20261010 import STATE, snap  # noqa: F401

NOW = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)


def bdir(w):
    return Path(w.env.cfg["backup_dir"])


def env_with(w, store, **patch):
    e = fresh(w, cfg_patch=patch or None)
    e.store = store
    return e


def make_snapshots(w, n):
    """n real snapshots with distinct timestamps."""
    names = []
    for i in range(n):
        w.fake.set_et("2026-10-12", f"16:{10 + i}")
        rec, code = snap(w)
        assert code == 0, rec
        names.append(rec["snapshot"])
    return names


def listing(store, prefix):
    return [o["key"] for o in store.list(prefix)]


# ================================================================== verified offload, then local deletion
def test_offload_uploads_verifies_remote_bytes_then_deletes_local_staging(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    local = bdir(mig) / name
    want = sha(local)
    rec, code = kr.run_mode(env_with(mig, store), "offload")
    assert code == 0 and rec["status"] == "offloaded" and rec["remaining_local"] == 0
    key = ko.snapshot_key(name)
    assert key.startswith("daily/2026/10/") and hashlib.sha256(store.objects[key]).hexdigest() == want
    assert key + ".sha256" in store.objects
    assert not local.exists() and not (bdir(mig) / (name + ".sha256")).exists()           # no accumulating local history
    assert not list(bdir(mig).glob("kimrec-offload-*"))                                 # no staging residue
    assert STATE(mig).exists() and (STATE(mig) / "order_ledger.sqlite3").exists()      # active state untouched
    assert kr.run_mode(env_with(mig, store), "offload")[0]["status"] == "quiet"        # nothing pending -> silent no-op


def test_remote_checksum_mismatch_keeps_local_copy_and_reports_failure(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    store.corrupt_get.add(ko.snapshot_key(name))
    rec, code = kr.run_mode(env_with(mig, store), "offload")
    assert code == 1 and rec["status"] == "OFFLOAD_FAILED" and rec["failed"][0]["attempts"] == 1
    assert (bdir(mig) / name).exists() and store.deleted == []                         # the only recoverable copy was NOT deleted
    receipts = sorted((STATE(mig) / "receipts").glob("*_offload.json"))
    assert receipts and json.loads(receipts[-1].read_text())["status"] == "OFFLOAD_FAILED"       # visible failure receipt


def test_outage_retains_pending_copy_then_retry_succeeds_and_clears_attempts(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    store.fail_put.add("daily/")
    for _ in range(3):
        rec, code = kr.run_mode(env_with(mig, store), "offload")
        assert code == 1
    assert (bdir(mig) / name).exists()
    assert json.loads((bdir(mig) / ko.STATE_FILE).read_text())[name]["attempts"] == 3
    store.fail_put.clear()
    rec, code = kr.run_mode(env_with(mig, store), "offload")
    assert code == 0 and rec["status"] == "offloaded" and not (bdir(mig) / name).exists()
    assert name not in json.loads((bdir(mig) / ko.STATE_FILE).read_text())


def test_bounded_retries_then_stuck_stays_visible_and_retained(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    store.fail_put.add("daily/")
    for _ in range(4):
        kr.run_mode(env_with(mig, store, offload_max_attempts=3), "offload")
    rec, code = kr.run_mode(env_with(mig, store, offload_max_attempts=3), "offload")
    assert code == 1 and rec["stuck"] and rec["stuck"][0]["snapshot"] == name and (bdir(mig) / name).exists()


def test_corrupt_local_snapshot_is_never_uploaded(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    p = bdir(mig) / name
    data = bytearray(p.read_bytes())
    data[10] ^= 0xFF
    p.write_bytes(bytes(data))
    rec, code = kr.run_mode(env_with(mig, store), "offload")
    assert code == 1 and store.objects == {} and p.exists()


def test_existing_remote_object_counts_only_if_bytes_are_identical(mig):
    store = ko.MemoryStore()
    (name,) = make_snapshots(mig, 1)
    store.objects[ko.snapshot_key(name)] = b"someone else's bytes"
    rec, code = kr.run_mode(env_with(mig, store), "offload")
    assert code == 1 and (bdir(mig) / name).exists()                                 # exists but different: failure, local kept
    store.objects[ko.snapshot_key(name)] = (bdir(mig) / name).read_bytes()
    rec, code = kr.run_mode(env_with(mig, store), "offload")                          # idempotent re-upload of identical bytes is success
    assert code == 0 and not (bdir(mig) / name).exists()


# ================================================================== storage pressure is explicit
def test_pending_backlog_refuses_new_snapshots_without_deleting_anything(mig):
    store = ko.MemoryStore()
    names = make_snapshots(mig, 3)
    mig.fake.set_et("2026-10-12", "16:50")
    rec, code = kr.run_mode(fresh(mig, cfg_patch={"backup_max_pending": 3}), "snapshot-now")
    assert code == 1 and rec["status"] == "SNAPSHOT_REFUSED_STORAGE_PRESSURE" and rec["storage"]["pending"] == 3
    assert [p.name for p in ko.pending_snapshots(bdir(mig))] == sorted(names)         # nothing was deleted to make room


def test_low_free_space_refuses_new_snapshots_and_failed_offload_reports_pressure(mig):
    huge = 10 ** 9
    mig.fake.set_et("2026-10-12", "16:50")
    rec, code = kr.run_mode(fresh(mig, cfg_patch={"backup_min_free_mb": huge}), "snapshot-now")
    assert code == 1 and rec["status"] == "SNAPSHOT_REFUSED_STORAGE_PRESSURE" and rec["storage"]["pressure"] is True
    store = ko.MemoryStore()
    make_snapshots(mig, 1)
    store.fail_put.add("daily/")
    rec, code = kr.run_mode(env_with(mig, store, backup_min_free_mb=huge), "offload")
    assert code == 1 and "storage_pressure" in rec and len(ko.pending_snapshots(bdir(mig))) == 1


# ================================================================== post-close scheduling (calendar-aware)
def scheduled(w, store):
    return kr.run_cli_mode(env_with(w, store), "snapshot")


def test_scheduled_snapshot_waits_for_close_and_final_reconciliation_then_offloads(mig):
    store = ko.MemoryStore()
    mig.fake.set_et("2026-10-12", "16:02")
    assert scheduled(mig, store)[0]["status"] == "quiet"                              # not closed long enough
    mig.fake.set_et("2026-10-12", "16:25")
    assert scheduled(mig, store)[0]["status"] == "quiet" and not store.objects        # closed, but final reconciliation not done
    mig.fake.set_et("2026-10-12", "16:10")
    assert run(mig, "close")[0]["status"] == "closed_reconciled"
    mig.fake.set_et("2026-10-12", "16:25")
    rec, code = scheduled(mig, store)
    assert code == 0 and rec["status"] == "snapshot_ok" and rec["offload"]["status"] == "offloaded"
    assert any(k.startswith("daily/2026/10/") for k in store.objects) and not ko.pending_snapshots(bdir(mig))
    mig.fake.set_et("2026-10-12", "16:40")
    assert scheduled(mig, store)[0]["status"] == "quiet" and len(store.list("daily/")) == 2   # once per session (tar + sidecar)


def test_missing_final_reconciliation_is_reported_loudly_at_the_deadline(mig):
    store = ko.MemoryStore()
    mig.fake.set_et("2026-10-12", "16:55")
    rec, code = scheduled(mig, store)
    assert rec["status"] == "abstained" and "final reconciliation" in rec["reason"] and not store.objects
    assert sorted((STATE(mig) / "receipts").glob("*_snapshot.json"))


def test_early_close_session_snapshots_after_its_own_close_and_later_slots_are_noops(mig):
    from fake_alpaca_recurring import FakeRecurringAlpaca
    fake = FakeRecurringAlpaca(now_et=("2026-11-27", "13:10:00"), holidays=("2026-11-26",), early_close={"2026-11-27": "13:00"})
    fake.positions, fake.cash, fake.orders = mig.fake.positions, mig.fake.cash, mig.fake.orders
    mig.fake = fake
    store = ko.MemoryStore()
    assert run(mig, "close")[0]["status"] == "closed_reconciled"
    fake.set_et("2026-11-27", "13:25")
    rec, code = scheduled(mig, store)
    assert rec["status"] == "snapshot_ok" and any(k.startswith("daily/2026/11/") for k in store.objects)
    for hhmm in ("13:40", "16:25", "16:55"):
        fake.set_et("2026-11-27", hhmm)
        assert scheduled(mig, store)[0]["status"] == "quiet"


def test_no_snapshot_on_holidays_or_weekends(mig):
    store = ko.MemoryStore()
    mig.fake.set_et("2026-10-10", "16:25")                                            # Saturday
    assert scheduled(mig, store)[0]["status"] == "quiet" and not store.objects


# ================================================================== three-month archive cycle (isolated fixtures)
def put_daily(store, ym, days, payload=b"x"):
    y, m = ym
    names = []
    for d in days:
        name = f"kim_recurring_state_{y:04d}{m:02d}{d:02d}T210000Z.tar.gz"
        data = (payload + f"{y}{m}{d}".encode()) * 50
        store.objects[f"daily/{y:04d}/{m:02d}/{name}"] = data
        store.objects[f"daily/{y:04d}/{m:02d}/{name}.sha256"] = f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()
        names.append(name)
    return names


def test_eligibility_is_three_full_calendar_months_after_the_month_ends():
    keys = ["daily/2026/10/a.tar.gz", "daily/2026/11/b.tar.gz", "daily/2026/12/c.tar.gz"]
    assert ko.eligible_months(keys, datetime(2027, 1, 31, tzinfo=timezone.utc), 3) == []
    assert ko.eligible_months(keys, datetime(2027, 2, 1, tzinfo=timezone.utc), 3) == [(2026, 10)]
    assert ko.eligible_months(keys, datetime(2027, 3, 1, tzinfo=timezone.utc), 3) == [(2026, 10), (2026, 11)]


def test_consolidation_builds_verified_archive_then_deletes_only_verified_daily_objects(tmp_path):
    store = ko.MemoryStore()
    put_daily(store, (2026, 10), [12, 13, 14])
    put_daily(store, (2026, 11), [2, 3])
    put_daily(store, (2027, 1), [4])
    store.objects["daily/2026/10/README-not-a-snapshot.txt"] = b"left alone"
    before_sizes = sum(len(v) for k, v in store.objects.items() if k.startswith("daily/2026/10/") and k.endswith(".tar.gz"))
    rep = ko.consolidate(tmp_path, store, datetime(2027, 2, 2, tzinfo=timezone.utc))
    assert not rep["refused"] and len(rep["archives"]) == 1 and rep["deleted_daily_objects"] == 6
    arch = "archives/kim_state_snapshots_2026-10.tar"
    assert arch in store.objects and arch.replace(".tar", ".manifest.json") in store.objects and arch + ".sha256" in store.objects
    man = json.loads(store.objects["archives/kim_state_snapshots_2026-10.manifest.json"])
    assert len(man["members"]) == 3 and man["archive_sha256"] == hashlib.sha256(store.objects[arch]).hexdigest()
    assert not [k for k in store.objects if k.startswith("daily/2026/10/") and (k.endswith(".tar.gz") or k.endswith(".sha256"))]
    assert "daily/2026/10/README-not-a-snapshot.txt" in store.objects                # only verified members are ever deleted
    assert len([k for k in store.objects if k.startswith("daily/2026/11/")]) == 4 and any(k.startswith("daily/2027/01/") for k in store.objects)
    assert all(k.startswith("daily/") for k in store.deleted)                         # automation never deletes archives
    size = len(store.objects[arch])
    assert size >= before_sizes                                                       # uncompressed tar of compressed data: no pretend shrinkage
    assert not list(tmp_path.glob("kimrec-consolidate-*"))                            # bounded staging removed
    assert ko.consolidate(tmp_path, store, datetime(2027, 2, 3, tzinfo=timezone.utc))["archives"] == []     # idempotent


def test_archive_members_round_trip_to_the_original_snapshots(tmp_path):
    store = ko.MemoryStore()
    names = put_daily(store, (2026, 10), [12, 13])
    originals = {n: store.objects[f"daily/2026/10/{n}"] for n in names}
    ko.consolidate(tmp_path, store, datetime(2027, 2, 2, tzinfo=timezone.utc))
    out = tmp_path / "x.tar"
    out.write_bytes(store.objects["archives/kim_state_snapshots_2026-10.tar"])
    with tarfile.open(out) as tf:
        for n, data in originals.items():
            assert tf.extractfile(n).read() == data


@pytest.mark.parametrize("fault", ["member_corrupt", "archive_corrupt_on_verify", "upload_fails", "missing_sidecar", "stage_cap", "archive_exists_different"])
def test_any_verification_or_upload_fault_deletes_nothing(tmp_path, fault):
    store = ko.MemoryStore()
    names = put_daily(store, (2026, 10), [12, 13])
    k0 = f"daily/2026/10/{names[0]}"
    kw = {}
    if fault == "member_corrupt":
        store.corrupt_get.add(k0)
    elif fault == "archive_corrupt_on_verify":
        store.corrupt_get.add("archives/kim_state_snapshots_2026-10.tar")
    elif fault == "upload_fails":
        store.fail_put.add("archives/")
    elif fault == "missing_sidecar":
        del store.objects[k0 + ".sha256"]
    elif fault == "stage_cap":
        kw["stage_cap_mb"] = 0
    elif fault == "archive_exists_different":
        store.objects["archives/kim_state_snapshots_2026-10.tar"] = b"older different archive"
    snapshot_before = {k: v for k, v in store.objects.items() if k.startswith("daily/")}
    rep = ko.consolidate(tmp_path, store, datetime(2027, 2, 2, tzinfo=timezone.utc), **kw)
    assert rep["refused"] and rep["archives"] == [] and store.deleted == []
    assert {k: v for k, v in store.objects.items() if k.startswith("daily/")} == snapshot_before
    assert not list(tmp_path.glob("kimrec-consolidate-*"))


def test_consolidate_mode_visible_failure_and_quiet_when_nothing_eligible(mig):
    store = ko.MemoryStore()
    put_daily(store, (2026, 12), [1])
    mig.fake.set_et("2027-02-01", "12:00")
    assert kr.run_mode(env_with(mig, store), "consolidate")[0]["status"] == "quiet"
    put_daily(store, (2026, 10), [12])
    store.fail_put.add("archives/")
    rec, code = kr.run_mode(env_with(mig, store), "consolidate")
    assert code == 1 and rec["status"] == "consolidation_refused"
    store.fail_put.clear()
    rec, code = kr.run_mode(env_with(mig, store), "consolidate")
    assert code == 0 and rec["status"] == "consolidated"


# ================================================================== restore from S3, isolated only
def test_fetch_from_s3_and_isolated_restore_then_reconcile(mig, tmp_path):
    store = ko.MemoryStore()
    make_snapshots(mig, 1)
    kr.run_mode(env_with(mig, store), "offload")
    p = ko.fetch_snapshot(store, "latest", tmp_path / "dl")
    out = kr.restore_snapshot(p, tmp_path / "restored", STATE(mig), bdir(mig))
    assert (tmp_path / "restored" / kr.RESTORE_MARKER).exists() and out["ledger_rows_digest"]
    with pytest.raises(kr.HardStop):
        kr.restore_snapshot(p, STATE(mig), STATE(mig), bdir(mig))                    # never over live state


def test_fetch_rejects_tampered_remote_snapshot(mig, tmp_path):
    store = ko.MemoryStore()
    make_snapshots(mig, 1)
    kr.run_mode(env_with(mig, store), "offload")
    key = [k for k in store.objects if k.endswith(".tar.gz")][0]
    store.corrupt_get.add(key)
    with pytest.raises(ko.OffloadError):
        ko.fetch_snapshot(store, key, tmp_path / "dl")


# ================================================================== the real CLI wrapper
class Runner:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out, err = self.script.pop(0) if self.script else (0, "{}", "")
        return subprocess.CompletedProcess(argv, rc, out, err)


def test_cli_store_commands_enforce_encryption_no_overwrite_checksum_and_prefix(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    r = Runner([(0, "{}", ""), (254, "", "An error occurred (PreconditionFailed) when calling the PutObject operation"),
                (0, json.dumps({"Contents": [{"Key": "daily/a", "Size": 3}]}), ""), (0, "{}", "")])
    st = ko.AwsCliStore("b", "ap-southeast-1", runner=r, sleep=lambda s: None)
    assert st.put_new("daily/2026/10/x", f) == "created" and st.put_new("daily/2026/10/x", f) == "exists"
    a = r.calls[0]
    assert a[:3] == ["aws", "s3api", "put-object"] and "--server-side-encryption" in a and a[a.index("--server-side-encryption") + 1] == "AES256"
    assert a[a.index("--if-none-match") + 1] == "*" and "--checksum-algorithm" in a and a[a.index("--bucket") + 1] == "b"
    assert st.list("daily/") == [{"key": "daily/a", "size": 3}] and "--prefix" in r.calls[2] and r.calls[2][r.calls[2].index("--prefix") + 1] == "daily/"
    st.delete("daily/a")
    assert r.calls[3][2] == "delete-object"


def test_cli_store_retries_transient_failures_boundedly_but_not_definitive_answers(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    naps = []
    r = Runner([(1, "", "Connection reset by peer")] * 5)
    st = ko.AwsCliStore("b", "r", runner=r, retries=(1.0, 2.0), sleep=naps.append)
    with pytest.raises(ko.OffloadError):
        st.put_new("daily/k", f)
    assert len(r.calls) == 3 and naps == [1.0, 2.0]                                   # 1 try + 2 retries, then give up
    r2 = Runner([(254, "", "An error occurred (AccessDenied) when calling the PutObject")] * 3)
    st2 = ko.AwsCliStore("b", "r", runner=r2, sleep=lambda s: None)
    with pytest.raises(ko.OffloadError):
        st2.put_new("daily/k", f)
    assert len(r2.calls) == 1                                                         # a definitive denial is not hammered


# ================================================================== units, config, destination file
def test_offload_units_are_visible_on_failure_and_cannot_stop_trading_timers():
    from kim_recurring_ops import render_units as ru
    f = ru.render("a" * 64, "/opt/x/current")
    for m in ("offload", "consolidate", "snapshot"):
        svc = f[f"tbots-kim-recurring-{m}.service"]
        assert "OnFailure" not in svc and "SuccessExitStatus" not in svc and "Environment=HOME=/tmp" in svc
        assert "TBOTS_ALPACA_SUBMISSION_ENABLED" not in svc and "ReadWritePaths=/var/lib/tbots-kim-recurring /var/lib/tbots-kim-recurring-backups" in svc
    assert "OnCalendar=*-*-* *:20:00 UTC" in f["tbots-kim-recurring-offload.timer"] and "Sun" in f["tbots-kim-recurring-consolidate.timer"]
    assert f["tbots-kim-recurring-snapshot.timer"].count("OnCalendar") == 6
    assert "offload" in f["standdown.sh"] and "consolidate" in f["standdown.sh"] and "consolidate" in f["rearm.sh"]


def test_committed_config_has_no_account_identifier_and_destination_file_is_used(tmp_path):
    cfg, _ = kr.load_config(Path(__file__).resolve().parent / "kim_recurring_config.json")
    assert "s3_bucket" not in cfg and "278635088520" not in json.dumps(cfg)
    d = tmp_path / "offload.json"
    d.write_text(json.dumps({"bucket": "private-b", "region": "ap-southeast-1"}))
    assert kr.offload_target({**cfg, "offload_config_file": str(d)}) == ("private-b", "ap-southeast-1")
    with pytest.raises(kr.HardStop):
        kr.offload_target({**cfg, "offload_config_file": str(tmp_path / "missing.json")})


def test_independent_post_upload_archive_verification_catches_a_late_corruption(tmp_path):
    store = ko.MemoryStore()
    put_daily(store, (2026, 10), [12, 13])
    store.corrupt_on_get_number["archives/kim_state_snapshots_2026-10.tar"] = {2}      # upload-verify read is clean, the independent re-read is not
    rep = ko.consolidate(tmp_path, store, datetime(2027, 2, 2, tzinfo=timezone.utc))
    assert rep["refused"] and store.deleted == [] and "failed checksum verification" in rep["refused"][0]["reason"]


def test_member_content_must_match_the_manifest_before_any_daily_object_is_deleted(tmp_path, monkeypatch):
    store = ko.MemoryStore()
    put_daily(store, (2026, 10), [12, 13])
    real, seen = ko.sha_file, {}

    def lying_manifest(p):
        p = Path(p)
        if p.name.startswith("m_") and not p.name.endswith(".sha256"):
            seen[p.name] = seen.get(p.name, 0) + 1
            if seen[p.name] == 2:                                                       # the call that fills the manifest
                return "0" * 64
        return real(p)
    monkeypatch.setattr(ko, "sha_file", lying_manifest)
    rep = ko.consolidate(tmp_path, store, datetime(2027, 2, 2, tzinfo=timezone.utc))
    assert rep["refused"] and store.deleted == [] and "do not match the manifest" in rep["refused"][0]["reason"]
