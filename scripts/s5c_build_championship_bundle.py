#!/usr/bin/env python3
"""Trusted one-way construction of the isolated S5C CHAMPIONSHIP bundle."""
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib.gitrev import get_code_revision
from lib.ids import canonical_json
from s5c_config import (
    ADVANCEMENT_MANIFEST_HASH,
    ADVANCEMENT_PROTOCOL_HASH,
    CHAMPIONSHIP_END,
    CHAMPIONSHIP_START,
    DATASET_REVISION,
    EPISODE_HASH,
    EPISODE_PATH,
    EPISODE_PROTOCOL,
    EXPECTED_FINAL_TRADING_SESSION,
    FINALIST_IDS,
    LOCK_PATH,
    ROOT,
    SCORE_HASH,
    SCORE_PATH,
    SCORE_PROTOCOL,
    content_hash,
    envelope_bytes,
    load_advancement_manifest,
    required_price_bars,
    validate_protocols_on_disk,
)
from s5c_finalists import load_frozen_finalists

BUNDLE_BASE = ROOT / "data" / "championship_bundles"
BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)
SOURCE_FIELDS = (
    "asset", *BUNDLE_FIELDS, "source", "source_timestamp", "ingested_at",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError(f"refusing to overwrite immutable artifact: {path}")
    if not path.exists():
        path.write_bytes(payload)


def freeze_protocols_without_data_access() -> dict:
    write_immutable(EPISODE_PATH, envelope_bytes(EPISODE_PROTOCOL, EPISODE_HASH))
    write_immutable(SCORE_PATH, envelope_bytes(SCORE_PROTOCOL, SCORE_HASH))
    return {
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
    }


def _parse_bounded_source_row(raw: bytes, symbol: str) -> dict:
    try:
        values = next(csv.reader([raw.decode("utf-8")]))
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise RuntimeError(f"invalid bounded canonical row for {symbol}") from exc
    if len(values) != len(SOURCE_FIELDS):
        raise RuntimeError(f"invalid bounded canonical field count for {symbol}")
    source_row = dict(zip(SOURCE_FIELDS, values))
    if source_row["asset"] != symbol:
        raise RuntimeError(f"canonical source asset identity changed: {symbol}")
    row = {field: source_row[field] for field in BUNDLE_FIELDS}
    timestamp = row["timestamp"]
    if len(timestamp) != 10 or timestamp[4:5] != "-" or timestamp[7:8] != "-":
        raise RuntimeError(f"invalid bounded canonical timestamp for {symbol}")
    for field in ("open", "high", "low", "close", "volume"):
        try:
            float(row[field])
        except ValueError as exc:
            raise RuntimeError(
                f"invalid bounded canonical numeric field for {symbol}"
            ) from exc
    if row["adjusted_close"]:
        try:
            float(row["adjusted_close"])
        except ValueError as exc:
            raise RuntimeError(
                f"invalid bounded adjusted close for {symbol}"
            ) from exc
    return row


def load_canonical_source_through_final_2025() -> tuple[list[str], dict, bytes]:
    """Read canonical rows unbuffered and stop on the final 2025 session.

    No read is issued after the accepted final-session row, so FINAL RESERVE
    observation bytes never cross the trusted construction boundary.
    """
    normalized = ROOT / "data" / "normalized"
    manifest_path = normalized / f"manifest_{DATASET_REVISION}.json"
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise RuntimeError("canonical source manifest is unreadable") from exc
    asset_set = manifest.get("asset_set")
    if (
        manifest.get("dataset_revision") != DATASET_REVISION
        or not isinstance(asset_set, list)
        or asset_set != list(sorted(asset_set))
        or set(manifest.get("normalized_content_hashes", {})) != set(asset_set)
    ):
        raise RuntimeError("canonical source manifest identity changed")

    per_symbol_rows = {}
    expected_header = ",".join(SOURCE_FIELDS).encode() + b"\n"
    for symbol in asset_set:
        path = normalized / f"{symbol}.csv"
        rows = []
        prior_timestamp = ""
        reached_final_session = False
        with path.open("rb", buffering=0) as source_file:
            if source_file.readline() != expected_header:
                raise RuntimeError(f"canonical source header changed: {symbol}")
            while not reached_final_session:
                raw = source_file.readline()
                if not raw:
                    raise RuntimeError(
                        f"canonical source ended before final 2025 session: {symbol}"
                    )
                row = _parse_bounded_source_row(raw, symbol)
                timestamp = row["timestamp"]
                if timestamp <= prior_timestamp or timestamp > EXPECTED_FINAL_TRADING_SESSION:
                    raise RuntimeError(
                        f"canonical source ordering or 2025 boundary changed: {symbol}"
                    )
                rows.append(row)
                prior_timestamp = timestamp
                reached_final_session = timestamp == EXPECTED_FINAL_TRADING_SESSION
        per_symbol_rows[symbol] = rows
    return asset_set, per_symbol_rows, manifest_bytes


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
        row for row in ordered if row["timestamp"] < CHAMPIONSHIP_START
    ][-warmup_bars:]
    championship = [
        row
        for row in ordered
        if CHAMPIONSHIP_START <= row["timestamp"] <= CHAMPIONSHIP_END
    ]
    return [*warmup, *championship], len(warmup)


def derive_championship_bundle(
    finalists, construction_revision: str
) -> dict:
    # All identities and protocols are authenticated before this sole source opening.
    validate_protocols_on_disk()
    load_advancement_manifest()
    if tuple(row.genome_id for row in finalists) != FINALIST_IDS:
        raise RuntimeError("trusted builder requires the accepted frozen finalists")

    source_asset_set, source_per_symbol_rows, source_manifest_bytes = (
        load_canonical_source_through_final_2025()
    )

    per_genome = {
        row.genome_id: required_price_bars(row.genome) - 1
        for row in finalists
    }
    shared_warmup_bars = max(per_genome.values())
    warmup_policy = {
        "basis": "actual accepted frozen S5C finalists only",
        "per_genome_required_prechampionship_bars": per_genome,
        "shared_bundle_prechampionship_bars_per_asset": shared_warmup_bars,
        "selection": "last trading bars strictly before 2023-01-01",
        "missing_history_policy": "INSUFFICIENT_HISTORY_NOT_ELIGIBLE",
        "fabrication_backfill_interpolation": "PROHIBITED",
        "shortened_indicators": "PROHIBITED",
    }

    artifacts = {}
    artifact_hashes = {}
    metadata = {}
    for symbol in source_asset_set:
        source_rows = source_per_symbol_rows[symbol]
        selected, warmup_count = select_authorized_rows(
            source_rows, shared_warmup_bars
        )
        if (
            not selected
            or any(row["timestamp"] > CHAMPIONSHIP_END for row in selected)
            or selected[-1]["timestamp"] != EXPECTED_FINAL_TRADING_SESSION
        ):
            raise RuntimeError(
                "post-2025, incomplete, or empty data reached championship serialization"
            )
        artifact = csv_bytes(selected)
        artifacts[f"{symbol}.csv"] = artifact
        artifact_hashes[symbol] = sha256_bytes(artifact)
        championship_count = sum(
            CHAMPIONSHIP_START <= row["timestamp"] <= CHAMPIONSHIP_END
            for row in selected
        )
        metadata[symbol] = {
            "artifact": f"{symbol}.csv",
            "artifact_sha256": artifact_hashes[symbol],
            "earliest_included": selected[0]["timestamp"],
            "latest_included": selected[-1]["timestamp"],
            "warmup_rows_requested": shared_warmup_bars,
            "warmup_rows_included": warmup_count,
            "championship_rows_included": championship_count,
            "total_rows": len(selected),
        }

    revision_basis = {
        "schema_version": 1,
        "asset_set": source_asset_set,
        "dataset_revision": DATASET_REVISION,
        "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
        "finalist_ids": list(FINALIST_IDS),
        "lane_start": CHAMPIONSHIP_START,
        "lane_end": CHAMPIONSHIP_END,
        "final_trading_session": EXPECTED_FINAL_TRADING_SESSION,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "warmup_policy": warmup_policy,
        "artifact_hashes": artifact_hashes,
    }
    revision = content_hash("s5cchamp_", revision_basis)
    content = {
        "schema_version": 1,
        "bundle_kind": "S5C_AUTHORIZED_CHAMPIONSHIP",
        "derived_bundle_revision": revision,
        "revision_basis": revision_basis,
        "source_dataset_revision": DATASET_REVISION,
        "source_manifest_sha256": sha256_bytes(source_manifest_bytes),
        "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
        "advancement_protocol_hash": ADVANCEMENT_PROTOCOL_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "finalist_ids": list(FINALIST_IDS),
        "warmup_policy": warmup_policy,
        "construction_code_revision": construction_revision,
        "asset_metadata": metadata,
        "isolation_contract": {
            "evaluator_has_no_normalized_source_path": True,
            "only_fixed_bundle_path_is_supported": True,
            "observations_after_2025_12_31": 0,
            "final_reserve_observations": 0,
            "historical_s5a_performance_payloads": 0,
            "historical_s5b_performance_payloads": 0,
            "evolutionary_feedback_outputs": 0,
        },
    }
    manifest_hash = content_hash("s5c_champ_manifest_", content)
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
            f"unexpected files in immutable championship bundle: {sorted(unexpected)}"
        )
    for name, payload in expected.items():
        write_immutable(target / name, payload)
    return target


def write_lock(derived: dict, construction_revision: str) -> dict:
    content = {
        "schema_version": 1,
        "dataset_revision": DATASET_REVISION,
        "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
        "advancement_protocol_hash": ADVANCEMENT_PROTOCOL_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "finalist_ids": list(FINALIST_IDS),
        "championship_bundle_revision": derived["revision"],
        "championship_bundle_manifest_hash": derived["manifest_hash"],
        "championship_bundle_path": str(
            (BUNDLE_BASE / derived["revision"]).relative_to(ROOT)
        ),
        "construction_code_revision": construction_revision,
        "warmup_policy": derived["content"]["warmup_policy"],
        "evaluator_visible_min_date": min(
            row["earliest_included"]
            for row in derived["content"]["asset_metadata"].values()
        ),
        "evaluator_visible_max_date": EXPECTED_FINAL_TRADING_SESSION,
        "championship_organisms_executed_during_preparation": 0,
        "championship_result_rows_during_preparation": 0,
        "final_reserve_observations_accessed_or_exposed": 0,
        "genome_mutations": 0,
        "breeding_or_retraining": 0,
        "broker_connections": 0,
        "real_orders": 0,
        "live_market_feeds": 0,
        "public_endpoints": 0,
        "alpaca_access_configuration_authentication": 0,
        "external_system_access_or_mutation": 0,
    }
    lock_hash = content_hash("s5c_prep_lock_", content)
    write_immutable(LOCK_PATH, envelope_bytes(content, lock_hash))
    return {"content": content, "manifest_hash": lock_hash}


def main() -> None:
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise SystemExit(
            "S5C bundle construction requires a clean committed preparation revision"
        )
    validate_protocols_on_disk()
    finalists = load_frozen_finalists()
    derived = derive_championship_bundle(finalists, revision)
    path = write_bundle(derived)
    lock = write_lock(derived, revision)
    print(json.dumps({
        "bundle_path": str(path),
        "bundle_revision": derived["revision"],
        "bundle_manifest_hash": derived["manifest_hash"],
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "preparation_lock_hash": lock["manifest_hash"],
        "construction_code_revision": revision,
        "evaluator_visible_min_date": lock["content"]["evaluator_visible_min_date"],
        "evaluator_visible_max_date": EXPECTED_FINAL_TRADING_SESSION,
        "championship_organisms_executed": 0,
        "championship_result_rows": 0,
        "final_reserve_observations_accessed_or_exposed": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
