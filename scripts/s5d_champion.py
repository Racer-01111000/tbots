"""Performance-free loader for the single accepted S5D champion."""
from dataclasses import dataclass
import json
from pathlib import Path

from lib.ids import canonical_json, genome_id
from s5d_config import (
    ACCEPTED_CHAMPION_ID,
    ACCEPTED_S5C_DIGEST,
    ACCEPTED_S5C_REPORT_SHA256,
    ACCEPTED_S5C_RUN_ID,
    PROTOCOL_DIR,
    ROOT,
    S5DProtocolError,
    assert_frozen_execution_semantics,
    content_hash,
    load_preparation_lock,
    required_price_bars,
)


@dataclass(frozen=True)
class FrozenChampion:
    agent_id: str
    genome_id: str
    _genome_json: str

    @property
    def genome(self) -> dict:
        return json.loads(self._genome_json)


def load_frozen_champion() -> FrozenChampion:
    lock = load_preparation_lock()
    relative = Path(lock["champion_snapshot_path"])
    path = (ROOT / relative).resolve()
    claimed = lock["champion_snapshot_hash"]
    if path.parent != PROTOCOL_DIR.resolve() or claimed not in path.name:
        raise S5DProtocolError("frozen champion snapshot path is not authorized")
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5DProtocolError("frozen champion snapshot is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5DProtocolError("invalid champion snapshot envelope")
    content = envelope["content"]
    if (
        envelope["manifest_hash"] != claimed
        or content_hash("s5d_champion_", content) != claimed
        or content.get("source_s5c_run_id") != ACCEPTED_S5C_RUN_ID
        or content.get("source_s5c_deterministic_digest") != ACCEPTED_S5C_DIGEST
        or content.get("source_s5c_result_sha256") != ACCEPTED_S5C_REPORT_SHA256
        or content.get("performance_fields_included") != []
    ):
        raise S5DProtocolError("frozen champion snapshot identity changed")
    row = content.get("champion")
    if not isinstance(row, dict) or row.get("genome_id") != ACCEPTED_CHAMPION_ID:
        raise S5DProtocolError("frozen champion identity changed")
    genome = row.get("genome")
    assert_frozen_execution_semantics(genome)
    required = required_price_bars(genome)
    if (
        genome_id(genome) != ACCEPTED_CHAMPION_ID
        or row.get("required_price_bars_including_current") != required
        or row.get("required_pre_reserve_bars") != required - 1
    ):
        raise S5DProtocolError("frozen champion genome or warmup changed")
    return FrozenChampion(
        row["agent_id"],
        ACCEPTED_CHAMPION_ID,
        canonical_json(genome),
    )
