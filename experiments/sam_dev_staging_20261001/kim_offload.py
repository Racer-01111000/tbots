"""Off-instance backup offload and archive cycle for the Kim recurring paper lifecycle (GO TBOTS_KIM_OFFLOAD_ARCHIVE_AND_REARM_20261010).

Local snapshot files are TEMPORARY STAGING ONLY. A snapshot is deleted locally only after its bytes have been downloaded back from S3 and
matched against the local sha256. Nothing here ever deletes active trading state, receipts, or anything outside the backup staging directory.

S3 layout (private bucket, SSE-S3, TLS-only, public access blocked; the instance role may write/delete ONLY under daily/ and may write but
never delete under archives/):
  daily/YYYY/MM/<snapshot>.tar.gz   (+ .sha256 sidecar)   individual daily snapshots, kept for three calendar months
  archives/kim_state_snapshots_YYYY-MM.tar (+ .manifest.json, + .tar.sha256)   monthly consolidation (UNcompressed tar of already-compressed
                                    snapshots; consolidation reduces object count, not bytes). Final archives are never deleted by automation.

Failure policy: a failed upload keeps the pending local copy, is retried by the hourly offload timer up to `offload_max_attempts`, produces a
visible failure receipt and a non-zero exit (failed systemd unit). Storage pressure never deletes an unoffloaded copy: snapshot creation is
refused instead, visibly. Remote existence is never taken as success without a byte-for-byte checksum comparison.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SNAP_RE = re.compile(r"^kim_recurring_state_(\d{4})(\d{2})(\d{2})T\d{6}Z\.tar\.gz$")
STATE_FILE = "offload_state.json"
LOCK_FILE = ".offload.lock"


class OffloadError(RuntimeError):
    pass


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- store interface
class AwsCliStore:
    """S3 through the instance's own AWS CLI v2 and its instance-role credentials (never read or logged here). Every call is bounded."""

    def __init__(self, bucket: str, region: str, runner=subprocess.run, retries=(2.0, 5.0), sleep=None):
        import time
        self.bucket, self.region, self._run, self._retries = bucket, region, runner, retries
        self._sleep = sleep or time.sleep

    def _cli(self, *args: str, timeout=120) -> subprocess.CompletedProcess:
        env = {**os.environ, "AWS_DEFAULT_REGION": self.region, "AWS_PAGER": "", "HOME": os.environ.get("HOME", "/tmp") if os.access(os.environ.get("HOME", "/nonexistent"), os.W_OK) else "/tmp"}
        last = None
        for attempt in range(len(self._retries) + 1):
            p = self._run(["aws", "s3api", *args], capture_output=True, text=True, timeout=timeout, env=env)
            if p.returncode == 0:
                return p
            last = p
            err = (p.stderr or "")
            if "PreconditionFailed" in err or "NoSuchKey" in err or "AccessDenied" in err or "404" in err:
                break                                   # definitive answers are not retried
            if attempt < len(self._retries):
                self._sleep(self._retries[attempt])
        return last

    def put_new(self, key: str, path: Path) -> str:
        """Create `key` only if it does not exist. -> 'created' | 'exists'. Any other failure raises."""
        p = self._cli("put-object", "--bucket", self.bucket, "--key", key, "--body", str(path), "--server-side-encryption", "AES256",
                      "--if-none-match", "*", "--checksum-algorithm", "SHA256", "--output", "json", timeout=600)
        if p.returncode == 0:
            return "created"
        if "PreconditionFailed" in (p.stderr or ""):
            return "exists"
        raise OffloadError(f"put {key} failed: {(p.stderr or '')[-300:]}")

    def get(self, key: str, dest: Path) -> None:
        p = self._cli("get-object", "--bucket", self.bucket, "--key", key, str(dest), "--output", "json", timeout=600)
        if p.returncode != 0:
            raise OffloadError(f"get {key} failed: {(p.stderr or '')[-300:]}")

    def list(self, prefix: str) -> list[dict]:
        p = self._cli("list-objects-v2", "--bucket", self.bucket, "--prefix", prefix, "--output", "json")
        if p.returncode != 0:
            raise OffloadError(f"list {prefix} failed: {(p.stderr or '')[-300:]}")
        out = json.loads(p.stdout) if (p.stdout or "").strip() else {}
        return [{"key": o["Key"], "size": o["Size"]} for o in (out.get("Contents") or [])]

    def delete(self, key: str) -> None:
        p = self._cli("delete-object", "--bucket", self.bucket, "--key", key)
        if p.returncode != 0:
            raise OffloadError(f"delete {key} failed: {(p.stderr or '')[-300:]}")


class MemoryStore:
    """In-memory store with the same semantics, for tests: conditional create, prefix listing, fault injection."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put: set[str] = set()
        self.corrupt_get: set[str] = set()
        self.corrupt_on_get_number: dict[str, set[int]] = {}      # key -> which GET calls (1-based) return flipped bytes
        self._gets: dict[str, int] = {}
        self.deleted: list[str] = []
        self.denied_delete_prefixes: tuple = ("archives/",)

    def put_new(self, key, path):
        if any(f in key for f in self.fail_put):
            raise OffloadError(f"put {key} failed: simulated outage")
        if key in self.objects:
            return "exists"
        self.objects[key] = Path(path).read_bytes()
        return "created"

    def get(self, key, dest):
        if key not in self.objects:
            raise OffloadError(f"get {key} failed: NoSuchKey")
        data = self.objects[key]
        self._gets[key] = self._gets.get(key, 0) + 1
        if key in self.corrupt_get or self._gets[key] in self.corrupt_on_get_number.get(key, ()):
            data = data[:-1] + bytes([data[-1] ^ 1])
        Path(dest).write_bytes(data)

    def list(self, prefix):
        return [{"key": k, "size": len(v)} for k, v in sorted(self.objects.items()) if k.startswith(prefix)]

    def delete(self, key):
        if key.startswith(self.denied_delete_prefixes):
            raise OffloadError(f"delete {key} failed: AccessDenied")
        self.objects.pop(key, None)
        self.deleted.append(key)


# --------------------------------------------------------------------------- helpers
def snapshot_key(name: str) -> str:
    m = SNAP_RE.match(name)
    if not m:
        raise OffloadError(f"not a snapshot name: {name}")
    return f"daily/{m.group(1)}/{m.group(2)}/{name}"


def _state_path(bdir: Path) -> Path:
    return bdir / STATE_FILE


def _load_state(bdir: Path) -> dict:
    try:
        return json.loads(_state_path(bdir).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(bdir: Path, st: dict) -> None:
    tmp = _state_path(bdir).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    os.replace(tmp, _state_path(bdir))


def pending_snapshots(bdir: Path) -> list[Path]:
    return sorted(p for p in Path(bdir).glob("kim_recurring_state_*.tar.gz") if SNAP_RE.match(p.name))


def storage_check(bdir: Path, min_free_mb: int, max_pending: int) -> dict:
    free_mb = shutil.disk_usage(bdir).free // (1 << 20)
    pend = len(pending_snapshots(bdir))
    return {"free_mb": free_mb, "pending": pend, "min_free_mb": min_free_mb, "max_pending": max_pending,
            "pressure": free_mb < min_free_mb or pend >= max_pending}


class _Lock:
    def __init__(self, bdir: Path):
        self.path = Path(bdir) / LOCK_FILE
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise OffloadError("another offload/consolidation is running")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _verified_upload(store, key: str, local: Path, tmp: Path) -> str:
    """Upload then download the object and compare with the LOCAL bytes. 'exists' counts only if the remote bytes are identical."""
    want = sha_file(local)
    outcome = store.put_new(key, local)
    dl = tmp / "verify.dl"
    store.get(key, dl)
    got = sha_file(dl)
    dl.unlink(missing_ok=True)
    if got != want:
        raise OffloadError(f"remote content for {key} differs from the local checksum (remote {got[:12]} != local {want[:12]}); outcome was {outcome}")
    return outcome


# --------------------------------------------------------------------------- offload
def offload_pending(bdir: Path, store, max_attempts: int, now: datetime) -> dict:
    """Upload every pending snapshot (+sidecar), verify by download, and only then delete the local files. Returns a report; never raises
    for a per-file failure (those are recorded and retained)."""
    bdir = Path(bdir)
    rep = {"offloaded": [], "failed": [], "stuck": [], "skipped_no_sidecar": []}
    if not bdir.exists():
        return rep
    with _Lock(bdir):
        st = _load_state(bdir)
        for snap in pending_snapshots(bdir):
            side = snap.with_name(snap.name + ".sha256")
            ent = st.get(snap.name, {"attempts": 0})
            if not side.exists():
                rep["skipped_no_sidecar"].append(snap.name)
                continue
            if ent["attempts"] >= max_attempts:
                rep["stuck"].append({"snapshot": snap.name, "attempts": ent["attempts"], "last_error": ent.get("last_error")})
                continue
            try:
                recorded = side.read_text().split()[0]
                if sha_file(snap) != recorded:
                    raise OffloadError("local snapshot does not match its sidecar checksum; refusing to upload a corrupt backup")
                key = snapshot_key(snap.name)
                tmp = Path(tempfile.mkdtemp(prefix="kimrec-offload-", dir=bdir))
                try:
                    o1 = _verified_upload(store, key, snap, tmp)
                    o2 = _verified_upload(store, key + ".sha256", side, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                snap.unlink()                                   # remote bytes were just proven identical
                side.unlink()
                st.pop(snap.name, None)
                rep["offloaded"].append({"snapshot": snap.name, "key": key, "sha256": recorded, "remote": o1, "uploaded_utc": now.isoformat()})
            except Exception as e:  # noqa: BLE001  keep the local copy, count the attempt, surface it
                ent["attempts"] = ent["attempts"] + 1
                ent["last_error"] = repr(e)[:300]
                ent["last_attempt_utc"] = now.isoformat()
                ent.setdefault("first_failed_utc", now.isoformat())
                st[snap.name] = ent
                rep["failed"].append({"snapshot": snap.name, "attempts": ent["attempts"], "error": ent["last_error"]})
        _save_state(bdir, st)
    return rep


# --------------------------------------------------------------------------- three-month archive cycle
def month_index(y: int, m: int) -> int:
    return y * 12 + m


def eligible_months(keys: list[str], today: datetime, keep_months: int) -> list[tuple[int, int]]:
    """A month group is eligible once the whole month ended at least `keep_months` calendar months ago, i.e. the current month index is
    at least month+keep+1 (October snapshots become eligible on 1 February)."""
    cur = month_index(today.year, today.month)
    months = set()
    for k in keys:
        m = re.match(r"^daily/(\d{4})/(\d{2})/", k)
        if m:
            months.add((int(m.group(1)), int(m.group(2))))
    return sorted(ym for ym in months if cur >= month_index(*ym) + keep_months + 1)


def consolidate(bdir: Path, store, today: datetime, keep_months: int = 3, stage_cap_mb: int = 256) -> dict:
    """Consolidate eligible months into archives/kim_state_snapshots_YYYY-MM.tar with a checksum manifest. Order of operations is the safety:
    download+verify members -> build tar -> upload archive+manifest+sha -> download archive and verify tar sha AND every member -> only then delete exactly the
    verified individual daily objects. Bounded staging; no recompression; archives are never deleted."""
    bdir = Path(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    rep = {"archives": [], "refused": [], "deleted_daily_objects": 0}
    with _Lock(bdir):
        objs = store.list("daily/")
        keys = [o["key"] for o in objs]
        sizes = {o["key"]: o["size"] for o in objs}
        for (y, m) in eligible_months(keys, today, keep_months):
            prefix = f"daily/{y:04d}/{m:02d}/"
            members = [k for k in keys if k.startswith(prefix) and not k.endswith(".sha256") and k.endswith(".tar.gz")]
            if not members:
                continue
            total_mb = sum(sizes[k] + sizes.get(k + ".sha256", 0) for k in members) / (1 << 20)
            if total_mb > stage_cap_mb:
                rep["refused"].append({"month": f"{y}-{m:02d}", "reason": f"{total_mb:.1f} MB exceeds the {stage_cap_mb} MB staging cap; nothing deleted"})
                continue
            if any(k + ".sha256" not in sizes for k in members):
                rep["refused"].append({"month": f"{y}-{m:02d}", "reason": "a snapshot has no checksum sidecar in S3; nothing deleted"})
                continue
            name = f"kim_state_snapshots_{y:04d}-{m:02d}"
            stage = Path(tempfile.mkdtemp(prefix=f"kimrec-consolidate-{y}{m:02d}-", dir=bdir))
            try:
                man = {"schema": "kim-snapshot-archive-v1", "month": f"{y:04d}-{m:02d}", "created_utc": today.isoformat(), "members": {}}
                tar_path = stage / f"{name}.tar"
                with tarfile.open(tar_path, "w:") as tf:                          # uncompressed: members are already compressed
                    for k in members:
                        base = k.rsplit("/", 1)[1]
                        p, side = stage / ("m_" + base), stage / ("m_" + base + ".sha256")
                        store.get(k, p)
                        store.get(k + ".sha256", side)
                        if sha_file(p) != side.read_text().split()[0]:
                            raise OffloadError(f"downloaded {k} does not match its sidecar; archive aborted, nothing deleted")
                        man["members"][base] = {"sha256": sha_file(p), "size": p.stat().st_size, "source_key": k}
                        tf.add(p, arcname=base)
                        tf.add(side, arcname=base + ".sha256")
                        p.unlink()
                        side.unlink()
                    mj = stage / "MANIFEST.json"
                    mj.write_text(json.dumps(man, indent=1, sort_keys=True))
                    tf.add(mj, arcname="MANIFEST.json")
                tar_sha = sha_file(tar_path)
                size = tar_path.stat().st_size
                (stage / f"{name}.tar.sha256").write_text(f"{tar_sha}  {name}.tar\n")
                (stage / f"{name}.manifest.json").write_text(json.dumps({**man, "archive_sha256": tar_sha, "archive_size": tar_path.stat().st_size}, indent=1, sort_keys=True))
                for fn in (f"{name}.tar", f"{name}.tar.sha256", f"{name}.manifest.json"):
                    _verified_upload(store, f"archives/{fn}", stage / fn, stage)     # exists-with-identical-bytes is fine; different bytes raises
                # independent post-upload verification of the archive AND its members before ANY deletion
                chk = stage / "check.tar"
                store.get(f"archives/{name}.tar", chk)
                if sha_file(chk) != tar_sha:
                    raise OffloadError("uploaded archive failed checksum verification; nothing deleted")
                with tarfile.open(chk) as tf:
                    got = {}
                    for mem in tf.getmembers():
                        if mem.name.endswith(".tar.gz"):
                            got[mem.name] = hashlib.sha256(tf.extractfile(mem).read()).hexdigest()
                if got != {n: v["sha256"] for n, v in man["members"].items()}:
                    raise OffloadError("archive members do not match the manifest; nothing deleted")
                chk.unlink()
                deleted = 0
                for k in members:
                    store.delete(k)
                    store.delete(k + ".sha256")
                    deleted += 2
                rep["deleted_daily_objects"] += deleted
                rep["archives"].append({"archive": f"archives/{name}.tar", "sha256": tar_sha, "size": size,
                                        "members": len(members), "deleted_daily_objects": deleted})
            except Exception as e:  # noqa: BLE001
                rep["refused"].append({"month": f"{y}-{m:02d}", "reason": repr(e)[:300]})
            finally:
                shutil.rmtree(stage, ignore_errors=True)
    return rep


# --------------------------------------------------------------------------- restore from S3 (isolated; never over live state)
def fetch_snapshot(store, key_or_latest: str, dest_dir: Path) -> Path:
    """Download a snapshot (+sidecar) from daily/ into dest_dir, verifying the sidecar. 'latest' = newest daily snapshot."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if key_or_latest == "latest":
        snaps = sorted(o["key"] for o in store.list("daily/") if o["key"].endswith(".tar.gz"))
        if not snaps:
            raise OffloadError("no daily snapshots in S3")
        key = snaps[-1]
    else:
        key = key_or_latest
    name = key.rsplit("/", 1)[1]
    store.get(key, dest_dir / name)
    store.get(key + ".sha256", dest_dir / (name + ".sha256"))
    if sha_file(dest_dir / name) != (dest_dir / (name + ".sha256")).read_text().split()[0]:
        raise OffloadError("downloaded snapshot does not match its sidecar checksum")
    return dest_dir / name
