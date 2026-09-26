"""Generation-transactional checkpoint persistence for the TBOTS Fitness V2
worker, mirroring scripts/s6b_resume_executor.py's stage-then-hardlink-commit
pattern (generalized: caller-supplied label and allowed-path prefixes instead
of S6B's hardcoded lineage/generation schema -- the world-league artifact
layout is a separate, not-yet-frozen design).

Durability shape, unchanged from S6B's proven design:
  1. every artifact is staged with an exclusive create ("xb") + fsync
  2. a transaction manifest (per-artifact sha256) is written and fsync'd
     LAST, after every artifact is staged -- so its mere existence means
     "safe to commit", and its absence means "nothing to resume"
  3. commit is a hard link per artifact: atomic, and refuses (raises) if a
     final path already exists with a DIFFERENT hash -- it never overwrites
  4. recover_pending_transaction() is idempotent: calling it when nothing
     is pending is a no-op; calling it twice on an already-committed
     transaction is a no-op (final files already match by hash)

This is what makes kill -9 mid-write and a real reboot mid-write both safe:
whatever stage the process was in, the next call finishes exactly that one
prepared transaction (via hash-verified hard link) or discards only inert
pre-manifest staging debris, and never reruns a completed unit of work.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

TRANSACTION_FILE = ".worker_checkpoint_transaction.json"
STAGE_PREFIX = ".worker_checkpoint_stage_"


class CheckpointError(Exception):
    pass


class ArtifactCollision(CheckpointError):
    pass


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ArtifactCollision(f"checkpoint staging collision: {path}") from exc
    _fsync_dir(path.parent)


def _safe_relative_path(value: str, allowed_prefixes: set) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise CheckpointError(f"unsafe checkpoint artifact path: {value!r}")
    if path.parts[0] not in allowed_prefixes:
        raise CheckpointError(f"checkpoint path escaped allowed surface: {value}")
    return path


def cleanup_orphan_stages(target) -> None:
    """A stage directory without a transaction manifest can only be
    pre-manifest debris from a crash (the manifest is always written last),
    so it is always safe to remove."""
    target = Path(target)
    if (target / TRANSACTION_FILE).exists():
        return
    for path in target.glob(f"{STAGE_PREFIX}*"):
        if path.is_dir():
            shutil.rmtree(path)
    if target.is_dir():
        _fsync_dir(target)


def recover_pending_transaction(target) -> dict | None:
    target = Path(target)
    transaction_path = target / TRANSACTION_FILE
    if not transaction_path.exists():
        cleanup_orphan_stages(target)
        return None
    try:
        manifest = json.loads(transaction_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError("pending checkpoint transaction manifest is unreadable") from exc

    stage = target / manifest.get("stage_directory", "")
    if not stage.is_dir() or stage.parent != target:
        raise CheckpointError("pending checkpoint transaction stage is missing")

    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise CheckpointError("pending checkpoint transaction has no artifacts")

    for entry in entries:
        rel = Path(entry["relative_path"])
        staged = stage / entry["staged_name"]
        expected_hash = entry["sha256"]
        if not staged.is_file() or _sha256_file(staged) != expected_hash:
            raise CheckpointError(f"staged checkpoint artifact failed hash check: {rel}")
        final = target / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            if not final.is_file() or _sha256_file(final) != expected_hash:
                raise ArtifactCollision(
                    f"existing checkpoint artifact differs from prepared transaction: {final}"
                )
        else:
            try:
                os.link(staged, final)
            except FileExistsError:
                if not final.is_file() or _sha256_file(final) != expected_hash:
                    raise ArtifactCollision(f"checkpoint artifact collision: {final}")
            _fsync_dir(final.parent)

    transaction_path.unlink()
    _fsync_dir(target)
    shutil.rmtree(stage)
    _fsync_dir(target)
    return {"status": "recovered", "label": manifest.get("label"), "artifact_count": len(entries)}


def prepare_transaction(target, label: str, artifacts: dict, allowed_prefixes: set) -> dict:
    """Stages every artifact and fsyncs a transaction manifest before any
    final path exists. Raises if a transaction is already pending -- the
    caller must recover_pending_transaction() first."""
    target = Path(target)
    transaction_path = target / TRANSACTION_FILE
    if transaction_path.exists():
        raise CheckpointError("a checkpoint transaction is already pending")

    target.mkdir(parents=True, exist_ok=True)
    stage = target / f"{STAGE_PREFIX}{label}"
    try:
        stage.mkdir()
    except FileExistsError as exc:
        raise CheckpointError(f"checkpoint stage already exists: {stage}") from exc

    manifest_artifacts = []
    try:
        for relative, value in artifacts.items():
            rel = _safe_relative_path(relative, allowed_prefixes)
            data = _json_bytes(value)
            staged_name = relative.replace("/", "__")
            _write_new_bytes(stage / staged_name, data)
            manifest_artifacts.append({
                "relative_path": str(rel), "staged_name": staged_name,
                "sha256": _sha256_bytes(data), "byte_size": len(data),
            })
        manifest = {
            "schema_version": 1, "label": label,
            "stage_directory": stage.name, "artifacts": manifest_artifacts,
        }
        _write_new_bytes(transaction_path, _json_bytes(manifest))
        return manifest
    except Exception:
        if not transaction_path.exists() and stage.exists():
            shutil.rmtree(stage)
            _fsync_dir(target)
        raise


def commit_checkpoint(target, label: str, artifacts: dict, allowed_prefixes: set) -> dict:
    """The normal (no crash in between) path: recover anything stale first,
    then prepare_transaction + recover_pending_transaction for this unit of
    work."""
    target = Path(target)
    if target.is_dir() and ((target / TRANSACTION_FILE).exists()
                             or list(target.glob(f"{STAGE_PREFIX}*"))):
        recover_pending_transaction(target)
    prepare_transaction(target, label, artifacts, allowed_prefixes)
    return recover_pending_transaction(target)
