"""Atomic, integrity-checked checkpoints. A checkpoint is `gen_NNN.json` plus `gen_NNN.sha256`; the payload also embeds its own
hash. Loading verifies both. A corrupt or truncated LATEST checkpoint is REFUSED (CheckpointCorrupt) -- the run does not silently
fall back to an older one or start over, because that could hide lost work."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class CheckpointCorrupt(RuntimeError):
    pass


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def save(dir_: Path, gen: int, state: dict) -> Path:
    body = dict(state); body.pop("payload_sha256", None)
    digest = hashlib.sha256(_canon(body)).hexdigest()
    body["payload_sha256"] = digest
    raw = _canon(body)
    path = Path(dir_) / f"gen_{gen:03d}.json"
    write_atomic(path, raw)                                    # payload first ...
    write_atomic(path.with_suffix(".sha256"), (hashlib.sha256(raw).hexdigest() + "\n").encode())   # ... then its file hash
    return path


def list_generations(dir_: Path) -> list[int]:
    return sorted(int(p.stem.split("_")[1]) for p in Path(dir_).glob("gen_*.json")) if Path(dir_).exists() else []


def load(dir_: Path, gen: int) -> dict:
    path = Path(dir_) / f"gen_{gen:03d}.json"
    side = path.with_suffix(".sha256")
    if not path.exists() or not side.exists():
        raise CheckpointCorrupt(f"{path.name}: payload or sidecar missing")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != side.read_text().strip():
        raise CheckpointCorrupt(f"{path.name}: file hash does not match its sidecar")
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CheckpointCorrupt(f"{path.name}: unparseable ({e})") from e
    body = dict(state); claimed = body.pop("payload_sha256", None)
    if claimed != hashlib.sha256(_canon(body)).hexdigest():
        raise CheckpointCorrupt(f"{path.name}: embedded payload hash mismatch")
    return state


def load_latest(dir_: Path):
    gens = list_generations(dir_)
    if not gens:
        return None
    return load(dir_, gens[-1])          # raises CheckpointCorrupt; no fallback
