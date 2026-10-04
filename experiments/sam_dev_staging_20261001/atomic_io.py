"""Crash-safe JSON state writes: temp file in the same directory, fsync,
os.replace, then fsync the directory. A reader (or a restart after a
crash/power loss) sees either the complete old file or the complete new
one -- never a truncated write_text() result."""
from __future__ import annotations

import json
import os
from pathlib import Path


class StateCorrupt(RuntimeError):
    """A state file exists but cannot be parsed. Callers must stop and
    escalate -- silently starting from empty state would reset peak
    equity / halt latch / cadence."""


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    data = json.dumps(obj, indent=2, default=str).encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_json_strict(path: Path):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise StateCorrupt(f"{path} is unreadable ({e!r}); refusing to start from empty state") from e
