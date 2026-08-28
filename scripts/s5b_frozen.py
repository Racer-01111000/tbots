"""Performance-free loader for the accepted frozen S5A top ten."""
from dataclasses import dataclass
import json
from pathlib import Path

from lib.ids import canonical_json, genome_id
from s5b_config import (
    ACCEPTED_DB_SHA256,
    ACCEPTED_RESULT_SHA256,
    ACCEPTED_RUN_ID,
    ACCEPTED_S5A_REVISION,
    PROTOCOL_DIR,
    ROOT,
    S5BProtocolError,
    assert_frozen_execution_semantics,
    content_hash,
    load_preparation_lock,
    required_price_bars,
)


@dataclass(frozen=True)
class FrozenGenome:
    development_rank: int
    agent_id: str
    genome_id: str
    _genome_json: str

    @property
    def genome(self) -> dict:
        return json.loads(self._genome_json)


def load_frozen_top10() -> tuple[FrozenGenome, ...]:
    lock = load_preparation_lock()
    relative = Path(lock["frozen_top10_snapshot_path"])
    path = (ROOT / relative).resolve()
    if path.parent != PROTOCOL_DIR.resolve() or lock["frozen_top10_snapshot_hash"] not in path.name:
        raise S5BProtocolError("frozen top-ten snapshot path is not authorized")
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise S5BProtocolError("frozen top-ten snapshot is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise S5BProtocolError("invalid frozen top-ten snapshot envelope")
    content = envelope["content"]
    claimed = lock["frozen_top10_snapshot_hash"]
    if (
        envelope["manifest_hash"] != claimed
        or content_hash("s5b_top10_", content) != claimed
        or content.get("source_run_id") != ACCEPTED_RUN_ID
        or content.get("source_code_revision") != ACCEPTED_S5A_REVISION
        or content.get("source_result_sha256") != ACCEPTED_RESULT_SHA256
        or content.get("source_reproduction_db_sha256") != ACCEPTED_DB_SHA256
        or content.get("performance_fields_included") != []
    ):
        raise S5BProtocolError("frozen top-ten snapshot identity changed")
    rows = content.get("genomes")
    if not isinstance(rows, list) or len(rows) != 10:
        raise S5BProtocolError("frozen top-ten snapshot must contain exactly ten genomes")

    frozen = []
    for expected_rank, row in enumerate(rows, 1):
        if row.get("development_rank") != expected_rank:
            raise S5BProtocolError("frozen top-ten rank sequence changed")
        genome = row.get("genome")
        assert_frozen_execution_semantics(genome)
        if genome_id(genome) != row.get("genome_id"):
            raise S5BProtocolError("frozen genome content hash changed")
        required = required_price_bars(genome)
        if (
            row.get("required_price_bars_including_current") != required
            or row.get("required_prequalification_bars") != required - 1
        ):
            raise S5BProtocolError("frozen genome warmup requirement changed")
        frozen.append(FrozenGenome(
            expected_rank,
            row["agent_id"],
            row["genome_id"],
            canonical_json(genome),
        ))
    if len({row.agent_id for row in frozen}) != 10 or len({row.genome_id for row in frozen}) != 10:
        raise S5BProtocolError("frozen top ten contains duplicate identities")
    return tuple(frozen)
