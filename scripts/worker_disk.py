"""Disk-space gate (GO Addendum A: '<10GB free -> stop_code R, delete nothing')."""
import shutil

MIN_FREE_BYTES = 10 * 1024 ** 3


def disk_snapshot(path) -> dict:
    usage = shutil.disk_usage(str(path))
    return {"disk_used_bytes": usage.used, "disk_free_bytes": usage.free}


def disk_gate(path) -> dict:
    """Never deletes anything, regardless of outcome -- that decision is
    Rick's, per the original GO's stop-condition discipline."""
    snapshot = disk_snapshot(path)
    if snapshot["disk_free_bytes"] < MIN_FREE_BYTES:
        return {
            **snapshot, "outcome": "stopped", "stop_code": "R",
            "detail": f"free disk {snapshot['disk_free_bytes']} < {MIN_FREE_BYTES} minimum",
        }
    return {**snapshot, "outcome": "ok", "stop_code": None, "detail": None}
