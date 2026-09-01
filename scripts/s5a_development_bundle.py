"""Fail-closed loader for the one authorized S5A DEVELOPMENT bundle."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from lib.ids import canonical_json
from lib.replay import DatasetBundle, DatasetVerificationError, EvaluationExposureAudit
from s5a_config import (
    AUTHORIZED_DEVELOPMENT_BUNDLE_REVISION,
    DATASET_REVISION,
    DEVELOPMENT_LANE_HASH,
    EPISODE_PROTOCOL,
    PREDEVELOPMENT_WARMUP_BARS,
    ROOT,
    WARMUP_POLICY,
)

BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)
BUNDLE_BASE = ROOT / "data" / "development_bundles"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_bundle_directory(bundle_dir: Path, expected_revision: str) -> DatasetBundle:
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / f"manifest_{expected_revision}.json"
    try:
        envelope = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetVerificationError("authorized DEVELOPMENT bundle manifest is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise DatasetVerificationError("invalid DEVELOPMENT bundle manifest envelope")
    content = envelope["content"]
    actual_manifest_hash = "s5a_dev_manifest_" + _sha256(canonical_json(content).encode())
    if envelope["manifest_hash"] != actual_manifest_hash:
        raise DatasetVerificationError("DEVELOPMENT bundle manifest hash mismatch")
    if content.get("derived_bundle_revision") != expected_revision:
        raise DatasetVerificationError("DEVELOPMENT bundle revision mismatch")
    if content.get("source_dataset_revision") != DATASET_REVISION:
        raise DatasetVerificationError("DEVELOPMENT bundle source dataset changed")
    if content.get("authorized_lane_manifest_hash") != DEVELOPMENT_LANE_HASH:
        raise DatasetVerificationError("DEVELOPMENT bundle lane manifest changed")
    if content.get("warmup_policy") != WARMUP_POLICY:
        raise DatasetVerificationError("DEVELOPMENT bundle warmup policy changed")

    basis = content.get("revision_basis")
    actual_revision = "s5adev_" + _sha256(canonical_json(basis).encode())
    if actual_revision != expected_revision:
        raise DatasetVerificationError("DEVELOPMENT evaluation-content revision mismatch")
    if basis.get("lane_start") != EPISODE_PROTOCOL["lane_start"]:
        raise DatasetVerificationError("DEVELOPMENT bundle start changed")
    if basis.get("lane_end") != EPISODE_PROTOCOL["lane_end"]:
        raise DatasetVerificationError("DEVELOPMENT bundle end changed")

    asset_set = basis.get("asset_set")
    if not isinstance(asset_set, list) or not asset_set:
        raise DatasetVerificationError("DEVELOPMENT bundle asset set is invalid")
    per_symbol_rows = {}
    for symbol in asset_set:
        artifact_path = bundle_dir / f"{symbol}.csv"
        try:
            artifact = artifact_path.read_bytes()
        except OSError as exc:
            raise DatasetVerificationError(f"missing DEVELOPMENT artifact for {symbol}") from exc
        expected_hash = basis["artifact_hashes"].get(symbol)
        if _sha256(artifact) != expected_hash:
            raise DatasetVerificationError(f"changed DEVELOPMENT artifact: {symbol}")
        reader = csv.DictReader(io.StringIO(artifact.decode("utf-8")))
        if tuple(reader.fieldnames or ()) != BUNDLE_FIELDS:
            raise DatasetVerificationError(f"invalid DEVELOPMENT fields for {symbol}")
        rows = []
        for row in reader:
            timestamp = row.get("timestamp", "")
            if not ISO_DATE_RE.match(timestamp):
                raise DatasetVerificationError(f"invalid DEVELOPMENT timestamp for {symbol}")
            if timestamp > EPISODE_PROTOCOL["lane_end"]:
                raise DatasetVerificationError("post-DEVELOPMENT observation in evaluator bundle")
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    float(row[field])
                except (KeyError, ValueError):
                    raise DatasetVerificationError(
                        f"invalid DEVELOPMENT numeric field for {symbol}"
                    )
            if row["adjusted_close"]:
                try:
                    float(row["adjusted_close"])
                except ValueError:
                    raise DatasetVerificationError(
                        f"invalid DEVELOPMENT adjusted close for {symbol}"
                    )
            rows.append({field: row[field] for field in BUNDLE_FIELDS})
        rows.sort(key=lambda row: row["timestamp"])
        predevelopment = sum(
            row["timestamp"] < EPISODE_PROTOCOL["lane_start"] for row in rows
        )
        if predevelopment > PREDEVELOPMENT_WARMUP_BARS:
            raise DatasetVerificationError("DEVELOPMENT bundle exceeds per-asset warmup limit")
        metadata = content["asset_metadata"][symbol]
        if len(rows) != metadata["total_rows"]:
            raise DatasetVerificationError("DEVELOPMENT bundle row count changed")
        if rows and (
            rows[0]["timestamp"] != metadata["earliest_included"]
            or rows[-1]["timestamp"] != metadata["latest_included"]
        ):
            raise DatasetVerificationError("DEVELOPMENT bundle coverage changed")
        per_symbol_rows[symbol] = rows

    calendar = sorted({row["timestamp"] for rows in per_symbol_rows.values() for row in rows})
    if not calendar or calendar[-1] > EPISODE_PROTOCOL["lane_end"]:
        raise DatasetVerificationError("DEVELOPMENT evaluator calendar is invalid")
    audit = EvaluationExposureAudit(
        EPISODE_PROTOCOL["lane_start"], EPISODE_PROTOCOL["lane_end"]
    )
    return DatasetBundle(
        DATASET_REVISION,
        asset_set,
        per_symbol_rows,
        calendar,
        exposure_audit=audit,
        bundle_revision=expected_revision,
        bundle_manifest_hash=envelope["manifest_hash"],
    )


def load_authorized_development_bundle() -> DatasetBundle:
    """No path or date parameters are accepted by the evaluator boundary."""
    revision = AUTHORIZED_DEVELOPMENT_BUNDLE_REVISION
    bundle_dir = BUNDLE_BASE / revision
    return _load_bundle_directory(bundle_dir, revision)


def isolation_snapshot(bundle: DatasetBundle) -> dict:
    if bundle.exposure_audit is None:
        raise DatasetVerificationError("DEVELOPMENT exposure instrumentation is missing")
    counts = bundle.exposure_audit.snapshot()
    counts.update({
        "observations_after_2018_12_31_available_to_evaluator": sum(
            len([row for row in rows if row["timestamp"] > EPISODE_PROTOCOL["lane_end"]])
            for rows in bundle.per_symbol_rows.values()
        ),
        "historical_s4_performance_inputs_accepted": 0,
        "bundle_revision": bundle.bundle_revision,
        "bundle_manifest_hash": bundle.bundle_manifest_hash,
    })
    return counts


def assert_isolated(bundle: DatasetBundle) -> dict:
    result = isolation_snapshot(bundle)
    forbidden = (
        "qualification_observations_exposed",
        "championship_observations_exposed",
        "final_reserve_observations_exposed",
        "other_post_development_observations_exposed",
        "observations_after_2018_12_31_available_to_evaluator",
        "historical_s4_performance_inputs_accepted",
    )
    if any(result[name] != 0 for name in forbidden):
        raise DatasetVerificationError(f"S5A DEVELOPMENT isolation failed: {result}")
    return result
