#!/usr/bin/env python3
"""Trusted one-way construction of the isolated S5B QUALIFICATION bundle."""
import csv
import hashlib
import io
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib.gitrev import get_code_revision
from lib.ids import canonical_json, genome_id
from lib.replay import load_and_verify_dataset
from s5_boundary import AUTHORIZED_PATHS, QUALIFICATION_HASH, load_authorized_manifest
from s5b_config import (
    ACCEPTED_DB_PATH,
    ACCEPTED_DB_SHA256,
    ACCEPTED_RESULT_PATH,
    ACCEPTED_RESULT_SHA256,
    ACCEPTED_RUN_DIGEST,
    ACCEPTED_RUN_ID,
    ACCEPTED_S5A_REVISION,
    DATASET_REVISION,
    EPISODE_HASH,
    EPISODE_PATH,
    EPISODE_PROTOCOL,
    LOCK_PATH,
    QUALIFICATION_END,
    QUALIFICATION_LANE_HASH,
    QUALIFICATION_START,
    ROOT,
    SCORE_HASH,
    SCORE_PATH,
    SCORE_PROTOCOL,
    assert_frozen_execution_semantics,
    content_hash,
    envelope_bytes,
    required_price_bars,
)

BUNDLE_BASE = ROOT / "data" / "qualification_bundles"
BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError(f"refusing to overwrite immutable artifact: {path}")
    if not path.exists():
        path.write_bytes(payload)


def write_frozen_protocols() -> None:
    # main() calls this before the trusted source dataset is opened.
    write_immutable(EPISODE_PATH, envelope_bytes(EPISODE_PROTOCOL, EPISODE_HASH))
    write_immutable(SCORE_PATH, envelope_bytes(SCORE_PROTOCOL, SCORE_HASH))


def authenticate_frozen_top10() -> dict:
    if sha256_file(ACCEPTED_RESULT_PATH) != ACCEPTED_RESULT_SHA256:
        raise RuntimeError("accepted S5A result hash mismatch")
    if sha256_file(ACCEPTED_DB_PATH) != ACCEPTED_DB_SHA256:
        raise RuntimeError("accepted S5A reproduction DB hash mismatch")
    result = json.loads(ACCEPTED_RESULT_PATH.read_text())
    if (
        result.get("run_id") != ACCEPTED_RUN_ID
        or result.get("code_revision") != ACCEPTED_S5A_REVISION
        or result.get("deterministic_digest") != ACCEPTED_RUN_DIGEST
    ):
        raise RuntimeError("accepted S5A result identity mismatch")
    rows = result.get("frozen_top10")
    if not isinstance(rows, list) or len(rows) != 10:
        raise RuntimeError("accepted S5A result does not contain exactly ten frozen genomes")

    conn = sqlite3.connect(f"file:{ACCEPTED_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT code_revision, code_dirty, dataset_revision, lane_manifest_hash, "
            "development_bundle_revision, status, deterministic_digest "
            "FROM evolution_runs WHERE run_id = ?",
            (ACCEPTED_RUN_ID,),
        ).fetchone()
        if run is None or dict(run) != {
            "code_revision": ACCEPTED_S5A_REVISION,
            "code_dirty": 0,
            "dataset_revision": DATASET_REVISION,
            "lane_manifest_hash": "lane_a02f9a435d21bd21722356da8280a45b9454e653987174b897244ec9746d1dc2",
            "development_bundle_revision": "s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a",
            "status": "completed",
            "deterministic_digest": ACCEPTED_RUN_DIGEST,
        }:
            raise RuntimeError("accepted S5A DB run identity mismatch")
        db_rows = conn.execute(
            "SELECT f.rank, f.agent_id, f.genome_id, g.genome_json "
            "FROM evolution_frozen_top10 f JOIN genomes g ON g.genome_id=f.genome_id "
            "WHERE f.run_id=? ORDER BY f.rank",
            (ACCEPTED_RUN_ID,),
        ).fetchall()
    finally:
        conn.close()
    if len(db_rows) != 10:
        raise RuntimeError("accepted S5A DB does not contain exactly ten frozen genomes")

    frozen = []
    for expected_rank, (json_row, db_row) in enumerate(zip(rows, db_rows), 1):
        genome = json_row.get("genome")
        identity = (json_row.get("rank"), json_row.get("agent_id"), json_row.get("genome_id"))
        db_identity = (db_row["rank"], db_row["agent_id"], db_row["genome_id"])
        if identity != db_identity or identity[0] != expected_rank:
            raise RuntimeError("frozen top-ten JSON/DB identity mismatch")
        if canonical_json(genome) != canonical_json(json.loads(db_row["genome_json"])):
            raise RuntimeError("frozen genome JSON differs between accepted artifacts")
        if genome_id(genome) != json_row["genome_id"]:
            raise RuntimeError("frozen genome content hash mismatch")
        assert_frozen_execution_semantics(genome)
        bars = required_price_bars(genome)
        frozen.append({
            "development_rank": expected_rank,
            "agent_id": json_row["agent_id"],
            "genome_id": json_row["genome_id"],
            "genome": genome,
            "required_price_bars_including_current": bars,
            "required_prequalification_bars": bars - 1,
        })

    content = {
        "schema_version": 1,
        "kind": "S5B_ACCEPTED_FROZEN_TOP10_WITHOUT_DEVELOPMENT_PERFORMANCE",
        "source_run_id": ACCEPTED_RUN_ID,
        "source_code_revision": ACCEPTED_S5A_REVISION,
        "source_result_sha256": ACCEPTED_RESULT_SHA256,
        "source_reproduction_db_sha256": ACCEPTED_DB_SHA256,
        "performance_fields_included": [],
        "genomes": frozen,
    }
    snapshot_hash = content_hash("s5b_top10_", content)
    return {
        "content": content,
        "snapshot_hash": snapshot_hash,
        "snapshot_path": ROOT / "evolution" / "protocol" / f"frozen_top10_{snapshot_hash}.json",
        "snapshot_bytes": envelope_bytes(content, snapshot_hash),
    }


def csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=BUNDLE_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in BUNDLE_FIELDS})
    return output.getvalue().encode()


def select_authorized_rows(rows: list[dict], warmup_bars: int) -> tuple[list[dict], int]:
    ordered = sorted(rows, key=lambda row: row["timestamp"])
    warmup = [row for row in ordered if row["timestamp"] < QUALIFICATION_START]
    warmup = warmup[-warmup_bars:]
    qualification = [
        row for row in ordered
        if QUALIFICATION_START <= row["timestamp"] <= QUALIFICATION_END
    ]
    return [*warmup, *qualification], len(warmup)


def derive_qualification_bundle(snapshot: dict, construction_revision: str) -> dict:
    lane = load_authorized_manifest(QUALIFICATION_HASH)
    if (
        lane.manifest_hash != QUALIFICATION_LANE_HASH
        or lane.lane != "QUALIFICATION"
        or lane.start_date != QUALIFICATION_START
        or lane.end_date != QUALIFICATION_END
    ):
        raise RuntimeError("trusted builder requires the frozen QUALIFICATION manifest")

    # This trusted boundary alone may open the complete accepted source.
    source = load_and_verify_dataset(ROOT, DATASET_REVISION)
    source_manifest_path = (
        ROOT / "data" / "normalized" / f"manifest_{DATASET_REVISION}.json"
    )
    source_manifest_bytes = source_manifest_path.read_bytes()
    source_manifest = json.loads(source_manifest_bytes)
    lane_manifest_bytes = AUTHORIZED_PATHS[QUALIFICATION_HASH].read_bytes()
    per_genome = {
        row["genome_id"]: row["required_prequalification_bars"]
        for row in snapshot["content"]["genomes"]
    }
    shared_warmup_bars = max(per_genome.values())
    warmup_policy = {
        "basis": "actual accepted frozen top-ten genomes only",
        "per_genome_required_prequalification_bars": per_genome,
        "shared_bundle_prequalification_bars_per_asset": shared_warmup_bars,
        "selection": "last trading bars strictly before 2019-01-01",
        "missing_history_policy": "INSUFFICIENT_HISTORY_NOT_ELIGIBLE",
        "fabrication_backfill_interpolation": "PROHIBITED",
        "shortened_indicators": "PROHIBITED",
    }

    artifacts = {}
    artifact_hashes = {}
    metadata = {}
    for symbol in source.asset_set:
        source_rows = source.per_symbol_rows[symbol]
        selected, warmup_count = select_authorized_rows(source_rows, shared_warmup_bars)
        if not selected or any(row["timestamp"] > QUALIFICATION_END for row in selected):
            raise RuntimeError("post-2022 or empty data reached bundle serialization")
        artifact = csv_bytes(selected)
        artifacts[f"{symbol}.csv"] = artifact
        artifact_hashes[symbol] = sha256_bytes(artifact)
        qualification_count = sum(
            QUALIFICATION_START <= row["timestamp"] <= QUALIFICATION_END
            for row in selected
        )
        metadata[symbol] = {
            "artifact": f"{symbol}.csv",
            "artifact_sha256": artifact_hashes[symbol],
            "source_earliest": source_rows[0]["timestamp"],
            "source_latest": source_rows[-1]["timestamp"],
            "earliest_included": selected[0]["timestamp"],
            "latest_included": selected[-1]["timestamp"],
            "warmup_rows_requested": shared_warmup_bars,
            "warmup_rows_included": warmup_count,
            "qualification_rows_included": qualification_count,
            "total_rows": len(selected),
        }

    revision_basis = {
        "schema_version": 1,
        "asset_set": source.asset_set,
        "dataset_revision": DATASET_REVISION,
        "qualification_lane_hash": QUALIFICATION_LANE_HASH,
        "lane_start": QUALIFICATION_START,
        "lane_end": QUALIFICATION_END,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "frozen_top10_snapshot_hash": snapshot["snapshot_hash"],
        "warmup_policy": warmup_policy,
        "artifact_hashes": artifact_hashes,
    }
    revision = content_hash("s5bqual_", revision_basis)
    content = {
        "schema_version": 1,
        "bundle_kind": "S5B_AUTHORIZED_QUALIFICATION",
        "derived_bundle_revision": revision,
        "revision_basis": revision_basis,
        "source_dataset_revision": DATASET_REVISION,
        "source_manifest_sha256": sha256_bytes(source_manifest_bytes),
        "source_normalized_content_hashes": source_manifest["normalized_content_hashes"],
        "authorized_lane_manifest_hash": QUALIFICATION_LANE_HASH,
        "authorized_lane_manifest_sha256": sha256_bytes(lane_manifest_bytes),
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "frozen_top10_snapshot_hash": snapshot["snapshot_hash"],
        "warmup_policy": warmup_policy,
        "construction_code_revision": construction_revision,
        "asset_metadata": metadata,
        "isolation_contract": {
            "evaluator_has_no_normalized_source_path": True,
            "only_fixed_bundle_path_is_supported": True,
            "observations_after_2022_12_31": 0,
            "championship_observations": 0,
            "final_reserve_observations": 0,
            "historical_s5a_performance_payloads": 0,
        },
    }
    manifest_hash = content_hash("s5b_qual_manifest_", content)
    return {
        "revision": revision,
        "manifest_hash": manifest_hash,
        "manifest_bytes": envelope_bytes(content, manifest_hash),
        "content": content,
        "artifacts": artifacts,
    }


def write_bundle(derived: dict) -> Path:
    target = BUNDLE_BASE / derived["revision"]
    target.mkdir(parents=True, exist_ok=True)
    expected = {
        **derived["artifacts"],
        f"manifest_{derived['revision']}.json": derived["manifest_bytes"],
    }
    unexpected = {p.name for p in target.iterdir() if p.is_file()} - set(expected)
    if unexpected:
        raise RuntimeError(f"unexpected files in immutable bundle: {sorted(unexpected)}")
    for name, payload in expected.items():
        write_immutable(target / name, payload)
    return target


def write_lock(snapshot: dict, derived: dict, construction_revision: str) -> dict:
    content = {
        "schema_version": 1,
        "dataset_revision": DATASET_REVISION,
        "qualification_lane_hash": QUALIFICATION_LANE_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "frozen_top10_snapshot_hash": snapshot["snapshot_hash"],
        "frozen_top10_snapshot_path": str(snapshot["snapshot_path"].relative_to(ROOT)),
        "qualification_bundle_revision": derived["revision"],
        "qualification_bundle_manifest_hash": derived["manifest_hash"],
        "qualification_bundle_path": str(
            (BUNDLE_BASE / derived["revision"]).relative_to(ROOT)
        ),
        "accepted_run_id": ACCEPTED_RUN_ID,
        "accepted_result_sha256": ACCEPTED_RESULT_SHA256,
        "accepted_reproduction_db_sha256": ACCEPTED_DB_SHA256,
        "construction_code_revision": construction_revision,
        "qualification_organisms_executed_during_preparation": 0,
    }
    lock_hash = content_hash("s5b_prep_lock_", content)
    write_immutable(LOCK_PATH, envelope_bytes(content, lock_hash))
    return {"content": content, "manifest_hash": lock_hash}


def main() -> None:
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise SystemExit("S5B bundle construction requires a clean committed revision")
    write_frozen_protocols()
    snapshot = authenticate_frozen_top10()
    write_immutable(snapshot["snapshot_path"], snapshot["snapshot_bytes"])
    derived = derive_qualification_bundle(snapshot, revision)
    path = write_bundle(derived)
    lock = write_lock(snapshot, derived, revision)
    print(json.dumps({
        "bundle_path": str(path),
        "bundle_revision": derived["revision"],
        "bundle_manifest_hash": derived["manifest_hash"],
        "frozen_top10_snapshot_hash": snapshot["snapshot_hash"],
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "preparation_lock_hash": lock["manifest_hash"],
        "construction_code_revision": revision,
        "qualification_organisms_executed": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
