#!/usr/bin/env python3
"""Trusted construction of the isolated S5D FINAL RESERVE bundle."""
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib.gitrev import get_code_revision
from lib.ids import canonical_json, genome_id
from lib.replay import load_and_verify_dataset
from s5c_finalists import load_frozen_finalists
from s5d_config import (
    ACCEPTED_CHAMPION_ID,
    ACCEPTED_S5C_DIGEST,
    ACCEPTED_S5C_REPORT_PATH,
    ACCEPTED_S5C_REPORT_SHA256,
    ACCEPTED_S5C_RUN_ID,
    DATASET_REVISION,
    EPISODE_HASH,
    EPISODE_PATH,
    EPISODE_PROTOCOL,
    FINAL_RESERVE_END,
    FINAL_RESERVE_START,
    LOCK_PATH,
    OUTCOME_HASH,
    OUTCOME_PATH,
    OUTCOME_PROTOCOL,
    PROTOCOL_DIR,
    ROOT,
    assert_frozen_execution_semantics,
    content_hash,
    envelope_bytes,
    required_price_bars,
    validate_protocols_on_disk,
)

BUNDLE_BASE = ROOT / "data" / "final_reserve_bundles"
BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError(f"refusing to overwrite immutable artifact: {path}")
    if not path.exists():
        path.write_bytes(payload)


def authenticate_champion_without_performance() -> dict:
    try:
        report_bytes = ACCEPTED_S5C_REPORT_PATH.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("accepted S5C result is unreadable") from exc
    if (
        sha256_bytes(report_bytes) != ACCEPTED_S5C_REPORT_SHA256
        or report.get("run_id") != ACCEPTED_S5C_RUN_ID
        or report.get("deterministic_digest") != ACCEPTED_S5C_DIGEST
        or report.get("winner", {}).get("genome_id") != ACCEPTED_CHAMPION_ID
        or report.get("winner", {}).get("championship_rank") != 1
    ):
        raise RuntimeError("accepted S5C result or winner identity changed")
    ranked = report.get("ranked_results")
    verifier_rows = report.get("independent_verifier")
    if (
        not isinstance(ranked, list)
        or len(ranked) != 3
        or ranked[0].get("genome_id") != ACCEPTED_CHAMPION_ID
        or ranked[0].get("championship_rank") != 1
        or not isinstance(verifier_rows, list)
        or len(verifier_rows) != 3
        or any(
            row.get("status") != "agreed"
            or row.get("main_result_digest") != row.get("verifier_result_digest")
            for row in verifier_rows
        )
        or report.get("conservation", {}).get("final_reserve_access") != 0
    ):
        raise RuntimeError("accepted S5C completion evidence changed")

    finalists = {item.genome_id: item for item in load_frozen_finalists()}
    champion = finalists.get(ACCEPTED_CHAMPION_ID)
    if champion is None:
        raise RuntimeError("accepted champion is absent from frozen finalists")
    genome = champion.genome
    assert_frozen_execution_semantics(genome)
    required = required_price_bars(genome)
    if genome_id(genome) != ACCEPTED_CHAMPION_ID:
        raise RuntimeError("accepted champion genome content hash changed")

    content = {
        "schema_version": 1,
        "kind": "S5D_PERFORMANCE_FREE_FROZEN_CHAMPION",
        "source_s5c_run_id": ACCEPTED_S5C_RUN_ID,
        "source_s5c_deterministic_digest": ACCEPTED_S5C_DIGEST,
        "source_s5c_result_sha256": ACCEPTED_S5C_REPORT_SHA256,
        "winner_rule_verified": "championship_rank_1",
        "performance_fields_included": [],
        "champion": {
            "agent_id": champion.agent_id,
            "genome_id": champion.genome_id,
            "genome": genome,
            "required_price_bars_including_current": required,
            "required_pre_reserve_bars": required - 1,
        },
    }
    snapshot_hash = content_hash("s5d_champion_", content)
    return {
        "content": content,
        "snapshot_hash": snapshot_hash,
        "snapshot_path": PROTOCOL_DIR / f"frozen_champion_{snapshot_hash}.json",
        "snapshot_bytes": envelope_bytes(content, snapshot_hash),
    }


def freeze_pre_reserve_artifacts_without_data_access() -> dict:
    write_immutable(EPISODE_PATH, envelope_bytes(EPISODE_PROTOCOL, EPISODE_HASH))
    write_immutable(OUTCOME_PATH, envelope_bytes(OUTCOME_PROTOCOL, OUTCOME_HASH))
    snapshot = authenticate_champion_without_performance()
    write_immutable(snapshot["snapshot_path"], snapshot["snapshot_bytes"])
    return {
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "champion_snapshot_hash": snapshot["snapshot_hash"],
        "champion_snapshot_path": str(snapshot["snapshot_path"]),
        "final_reserve_observations_accessed": 0,
        "champion_executions": 0,
    }


def csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=BUNDLE_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in BUNDLE_FIELDS})
    return output.getvalue().encode()


def select_authorized_rows(
    rows: list[dict], warmup_bars: int
) -> tuple[list[dict], int]:
    ordered = sorted(rows, key=lambda row: row["timestamp"])
    warmup = [
        row for row in ordered if row["timestamp"] < FINAL_RESERVE_START
    ][-warmup_bars:]
    reserve = [
        row
        for row in ordered
        if FINAL_RESERVE_START <= row["timestamp"] <= FINAL_RESERVE_END
    ]
    return [*warmup, *reserve], len(warmup)


def derive_final_reserve_bundle(snapshot: dict, construction_revision: str) -> dict:
    validate_protocols_on_disk()
    if (
        snapshot["content"]["champion"]["genome_id"] != ACCEPTED_CHAMPION_ID
        or snapshot["content"]["performance_fields_included"] != []
    ):
        raise RuntimeError("trusted builder requires performance-free champion snapshot")

    # This trusted construction boundary alone may open the accepted source.
    source = load_and_verify_dataset(ROOT, DATASET_REVISION)
    source_manifest_path = (
        ROOT / "data" / "normalized" / f"manifest_{DATASET_REVISION}.json"
    )
    source_manifest_bytes = source_manifest_path.read_bytes()
    warmup_bars = snapshot["content"]["champion"]["required_pre_reserve_bars"]
    warmup_policy = {
        "basis": "actual accepted frozen champion only",
        "champion_id": ACCEPTED_CHAMPION_ID,
        "required_price_bars_including_current": warmup_bars + 1,
        "pre_reserve_bars_per_asset": warmup_bars,
        "selection": "last trading bars strictly before 2026-01-01",
        "missing_history_policy": "INSUFFICIENT_HISTORY_NOT_ELIGIBLE",
        "fabrication_backfill_interpolation": "PROHIBITED",
        "shortened_indicators": "PROHIBITED",
    }

    artifacts = {}
    artifact_hashes = {}
    metadata = {}
    for symbol in source.asset_set:
        selected, warmup_count = select_authorized_rows(
            source.per_symbol_rows[symbol], warmup_bars
        )
        if (
            not selected
            or any(row["timestamp"] > FINAL_RESERVE_END for row in selected)
            or selected[-1]["timestamp"] != FINAL_RESERVE_END
        ):
            raise RuntimeError(
                "post-reserve, incomplete, or empty data reached serialization"
            )
        artifact = csv_bytes(selected)
        artifacts[f"{symbol}.csv"] = artifact
        artifact_hashes[symbol] = sha256_bytes(artifact)
        reserve_count = sum(
            FINAL_RESERVE_START <= row["timestamp"] <= FINAL_RESERVE_END
            for row in selected
        )
        metadata[symbol] = {
            "artifact": f"{symbol}.csv",
            "artifact_sha256": artifact_hashes[symbol],
            "earliest_included": selected[0]["timestamp"],
            "latest_included": selected[-1]["timestamp"],
            "warmup_rows_requested": warmup_bars,
            "warmup_rows_included": warmup_count,
            "final_reserve_rows_included": reserve_count,
            "total_rows": len(selected),
        }

    revision_basis = {
        "schema_version": 1,
        "asset_set": source.asset_set,
        "dataset_revision": DATASET_REVISION,
        "champion_id": ACCEPTED_CHAMPION_ID,
        "champion_snapshot_hash": snapshot["snapshot_hash"],
        "lane_start": FINAL_RESERVE_START,
        "lane_end": FINAL_RESERVE_END,
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "warmup_policy": warmup_policy,
        "artifact_hashes": artifact_hashes,
    }
    revision = content_hash("s5dreserve_", revision_basis)
    content = {
        "schema_version": 1,
        "bundle_kind": "S5D_AUTHORIZED_FINAL_RESERVE",
        "derived_bundle_revision": revision,
        "revision_basis": revision_basis,
        "source_dataset_revision": DATASET_REVISION,
        "source_manifest_sha256": sha256_bytes(source_manifest_bytes),
        "champion_id": ACCEPTED_CHAMPION_ID,
        "champion_snapshot_hash": snapshot["snapshot_hash"],
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "warmup_policy": warmup_policy,
        "construction_code_revision": construction_revision,
        "asset_metadata": metadata,
        "isolation_contract": {
            "evaluator_has_no_normalized_source_path": True,
            "only_fixed_bundle_path_is_supported": True,
            "observations_after_2026_08_25": 0,
            "historical_s5a_performance_payloads": 0,
            "historical_s5b_performance_payloads": 0,
            "historical_s5c_performance_payloads": 0,
            "evolutionary_feedback_outputs": 0,
            "competing_genomes": 0,
        },
    }
    manifest_hash = content_hash("s5d_reserve_manifest_", content)
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
    unexpected = {path.name for path in target.iterdir() if path.is_file()} - set(
        expected
    )
    if unexpected:
        raise RuntimeError(
            f"unexpected files in immutable final reserve bundle: {sorted(unexpected)}"
        )
    for name, payload in expected.items():
        write_immutable(target / name, payload)
    return target


def write_lock(
    snapshot: dict, derived: dict, construction_revision: str
) -> dict:
    content = {
        "schema_version": 1,
        "dataset_revision": DATASET_REVISION,
        "champion_id": ACCEPTED_CHAMPION_ID,
        "accepted_s5c_run_id": ACCEPTED_S5C_RUN_ID,
        "accepted_s5c_deterministic_digest": ACCEPTED_S5C_DIGEST,
        "accepted_s5c_result_sha256": ACCEPTED_S5C_REPORT_SHA256,
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "champion_snapshot_hash": snapshot["snapshot_hash"],
        "champion_snapshot_path": str(
            snapshot["snapshot_path"].relative_to(ROOT)
        ),
        "final_reserve_bundle_revision": derived["revision"],
        "final_reserve_bundle_manifest_hash": derived["manifest_hash"],
        "final_reserve_bundle_path": str(
            (BUNDLE_BASE / derived["revision"]).relative_to(ROOT)
        ),
        "construction_code_revision": construction_revision,
        "warmup_policy": derived["content"]["warmup_policy"],
        "evaluator_visible_min_date": min(
            row["earliest_included"]
            for row in derived["content"]["asset_metadata"].values()
        ),
        "evaluator_visible_max_date": FINAL_RESERVE_END,
        "final_reserve_champion_executions_during_preparation": 0,
        "real_final_reserve_result_rows": 0,
        "genome_mutations": 0,
        "breeding_or_retraining": 0,
        "evolutionary_feedback": 0,
        "broker_connections": 0,
        "real_orders": 0,
        "live_market_feeds": 0,
        "public_endpoints": 0,
        "alpaca_access_configuration_authentication": 0,
        "external_system_access_or_mutation": 0,
    }
    lock_hash = content_hash("s5d_prep_lock_", content)
    write_immutable(LOCK_PATH, envelope_bytes(content, lock_hash))
    return {"content": content, "manifest_hash": lock_hash}


def main() -> None:
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise SystemExit(
            "S5D bundle construction requires a clean committed preparation revision"
        )
    validate_protocols_on_disk()
    snapshot = authenticate_champion_without_performance()
    if (
        not snapshot["snapshot_path"].exists()
        or snapshot["snapshot_path"].read_bytes() != snapshot["snapshot_bytes"]
    ):
        raise SystemExit("S5D frozen champion snapshot is absent or changed")
    derived = derive_final_reserve_bundle(snapshot, revision)
    path = write_bundle(derived)
    lock = write_lock(snapshot, derived, revision)
    print(json.dumps({
        "bundle_path": str(path),
        "bundle_revision": derived["revision"],
        "bundle_manifest_hash": derived["manifest_hash"],
        "champion_snapshot_hash": snapshot["snapshot_hash"],
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "preparation_lock_hash": lock["manifest_hash"],
        "construction_code_revision": revision,
        "evaluator_visible_min_date": lock["content"]["evaluator_visible_min_date"],
        "evaluator_visible_max_date": FINAL_RESERVE_END,
        "final_reserve_champion_executions": 0,
        "real_final_reserve_result_rows": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
