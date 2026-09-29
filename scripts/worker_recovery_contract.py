"""Frozen runtime-recovery constants (Rick, 2026-09-29: "TBOTS CLOUD V1
STATE SURVIVABILITY FIX").

Three STATUS.json fields -- audit_window_ends_utc, head_commit_parent,
freeze_commit -- have never had any durable home except STATUS.json itself
(confirmed: no other file in the repo, including the complete-protocol
manifest and the parameter freeze, records any of them; they have been
write-once-at-freeze constants for the whole life of this branch, never
touched by any with_updates() call in fitness_v2_worker.py). That made full
STATUS.json reconstruction impossible whenever the file is missing entirely
-- worker_state_reconstruction.py needs *some* durable source for them, or
a fresh clone with no STATUS.json history can never pass the audit gate.

This module is that durable source: their exact, already-observed values,
content-addressed like every other frozen protocol artifact in this repo,
persisted at evolution/protocol/<RECOVERY_CONTRACT_ID>.json. It documents
the recovery contract; it does not change what these values mean or how
the audit gate uses them.
"""
from __future__ import annotations

from fitness_v2 import FitnessV2Error, content_identity

RECOVERY_CONTRACT = {
    "schema_version": 1,
    "authority": "Rick",
    "purpose": (
        "Durable home for the STATUS.json fields that have no other durable "
        "record, so worker_state_reconstruction.py can rebuild STATUS.json "
        "from evolution/state/*.json alone when the file is missing, stale, "
        "or inconsistent -- including on a fresh clone with no prior "
        "STATUS.json history at all."
    ),
    "audit_window_ends_utc": "2026-09-26T12:29:05Z",
    "head_commit_parent": "bb7cbf9e1e8969426a6f78bdc1022ada4eabea12",
    "freeze_commit": "1c3b6b013c4a47dbaf74a8a9ef5cda09f43afcd2",
}
RECOVERY_CONTRACT_ID = content_identity(
    "fitness_v2_runtime_recovery_contract_", RECOVERY_CONTRACT
)


def validate_recovery_contract(envelope):
    if envelope != {"manifest_id": RECOVERY_CONTRACT_ID, "content": RECOVERY_CONTRACT}:
        raise FitnessV2Error("runtime recovery contract mismatch")
    return RECOVERY_CONTRACT_ID
