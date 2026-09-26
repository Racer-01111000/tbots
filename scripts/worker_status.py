"""STATUS.json heartbeat schema and read/write helpers (GO Addendum A).

Exact field set as dictated in GO Addendum A. Writes are atomic
(tmp file + os.replace) so a reader never observes a half-written file.
"""
import json
from pathlib import Path

SCHEMA = "tbots-status-v1"

VALID_STATES = {
    "audit_window", "idle", "world_bank", "baseline", "campaign",
    "admission", "terminal_hold", "stopped",
}
VALID_STOP_CODES = {None, "A", "B", "C", "D", "E", "F", "G", "H", "R"}

FIELDS = (
    "schema", "updated_utc", "head_commit_parent", "state", "phase",
    "protocol_manifest", "freeze_commit", "audit_window_ends_utc",
    "world_bank_id", "synthetic_world_count", "campaign", "campaign_seed",
    "generation", "campaigns_complete", "rank1_qualifiers", "research_champion",
    "negative_result", "last_progress_utc", "stop_code", "stop_reason",
    "recoveries", "last_generation_seconds", "disk_used_bytes", "disk_free_bytes",
)


class StatusValidationError(Exception):
    pass


def build_status(**fields) -> dict:
    missing = set(FIELDS) - set(fields)
    if missing:
        raise StatusValidationError(f"STATUS.json missing required fields: {sorted(missing)}")
    extra = set(fields) - set(FIELDS)
    if extra:
        raise StatusValidationError(f"STATUS.json has unknown fields: {sorted(extra)}")
    if fields["schema"] != SCHEMA:
        raise StatusValidationError(f"unexpected schema: {fields['schema']!r}")
    if fields["state"] not in VALID_STATES:
        raise StatusValidationError(f"unknown state: {fields['state']!r}")
    if fields["stop_code"] not in VALID_STOP_CODES:
        raise StatusValidationError(f"unknown stop_code: {fields['stop_code']!r}")
    if fields["state"] == "stopped" and fields["stop_code"] is None:
        raise StatusValidationError("state 'stopped' requires a stop_code")
    if fields["state"] != "stopped" and fields["stop_code"] is not None:
        raise StatusValidationError("stop_code set without state 'stopped'")
    return {name: fields[name] for name in FIELDS}


def read_status(path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusValidationError(f"STATUS.json unreadable: {path}") from exc
    if not isinstance(data, dict):
        raise StatusValidationError(f"STATUS.json is not an object: {path}")
    return build_status(**data)


def write_status(path, status: dict) -> None:
    validated = build_status(**status)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(validated, indent=2) + "\n")
    tmp.replace(path)


def with_updates(status: dict, **updates) -> dict:
    """Returns a new, re-validated status dict; never mutates `status`."""
    merged = dict(status)
    merged.update(updates)
    return build_status(**merged)
