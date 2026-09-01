"""Fail-closed loader for the single authorized S5D FINAL RESERVE bundle."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from lib.ids import canonical_json
from lib.replay import DatasetBundle, DatasetVerificationError
from s5d_config import (
    ACCEPTED_CHAMPION_ID,
    DATASET_REVISION,
    EPISODE_HASH,
    FINAL_RESERVE_END,
    FINAL_RESERVE_START,
    OUTCOME_HASH,
    ROOT,
    load_preparation_lock,
)

BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FinalReserveExposureAudit:
    def __init__(self):
        self.counts = {
            "warmup_observations_exposed": 0,
            "final_reserve_observations_exposed": 0,
            "post_reserve_observations_exposed": 0,
        }

    def record(self, timestamp: str, count: int = 1) -> None:
        if timestamp < FINAL_RESERVE_START:
            key = "warmup_observations_exposed"
        elif timestamp <= FINAL_RESERVE_END:
            key = "final_reserve_observations_exposed"
        else:
            key = "post_reserve_observations_exposed"
        self.counts[key] += count

    def snapshot(self) -> dict:
        return dict(self.counts)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_bundle_directory(
    bundle_dir: Path, expected_revision: str, expected_manifest_hash: str
) -> DatasetBundle:
    bundle_dir = Path(bundle_dir)
    path = bundle_dir / f"manifest_{expected_revision}.json"
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetVerificationError("authorized final reserve bundle is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise DatasetVerificationError("invalid final reserve bundle manifest envelope")
    content = envelope["content"]
    actual_manifest = "s5d_reserve_manifest_" + _sha256(
        canonical_json(content).encode()
    )
    if (
        envelope["manifest_hash"] != expected_manifest_hash
        or actual_manifest != expected_manifest_hash
    ):
        raise DatasetVerificationError("final reserve bundle manifest hash mismatch")
    basis = content.get("revision_basis")
    if not isinstance(basis, dict):
        raise DatasetVerificationError("final reserve revision basis missing")
    actual_revision = "s5dreserve_" + _sha256(canonical_json(basis).encode())
    if (
        actual_revision != expected_revision
        or content.get("derived_bundle_revision") != expected_revision
        or content.get("source_dataset_revision") != DATASET_REVISION
        or content.get("champion_id") != ACCEPTED_CHAMPION_ID
        or content.get("episode_protocol_hash") != EPISODE_HASH
        or content.get("outcome_protocol_hash") != OUTCOME_HASH
        or basis.get("lane_start") != FINAL_RESERVE_START
        or basis.get("lane_end") != FINAL_RESERVE_END
        or basis.get("champion_id") != ACCEPTED_CHAMPION_ID
    ):
        raise DatasetVerificationError("final reserve bundle identity or bounds changed")
    isolation = content.get("isolation_contract", {})
    if isolation != {
        "evaluator_has_no_normalized_source_path": True,
        "only_fixed_bundle_path_is_supported": True,
        "observations_after_2026_08_25": 0,
        "historical_s5a_performance_payloads": 0,
        "historical_s5b_performance_payloads": 0,
        "historical_s5c_performance_payloads": 0,
        "evolutionary_feedback_outputs": 0,
        "competing_genomes": 0,
    }:
        raise DatasetVerificationError("final reserve isolation contract changed")

    asset_set = basis.get("asset_set")
    warmup_policy = basis.get("warmup_policy", {})
    warmup_limit = warmup_policy.get("pre_reserve_bars_per_asset")
    if (
        not isinstance(asset_set, list)
        or not asset_set
        or not isinstance(warmup_limit, int)
        or warmup_limit < 0
    ):
        raise DatasetVerificationError("final reserve asset set or warmup is invalid")

    per_symbol_rows = {}
    for symbol in asset_set:
        artifact_path = bundle_dir / f"{symbol}.csv"
        try:
            artifact = artifact_path.read_bytes()
        except OSError as exc:
            raise DatasetVerificationError(
                f"missing final reserve artifact: {symbol}"
            ) from exc
        if _sha256(artifact) != basis["artifact_hashes"].get(symbol):
            raise DatasetVerificationError(f"changed final reserve artifact: {symbol}")
        reader = csv.DictReader(io.StringIO(artifact.decode()))
        if tuple(reader.fieldnames or ()) != BUNDLE_FIELDS:
            raise DatasetVerificationError(f"invalid final reserve fields: {symbol}")
        rows = []
        for row in reader:
            timestamp = row.get("timestamp", "")
            if not ISO_DATE_RE.match(timestamp) or timestamp > FINAL_RESERVE_END:
                raise DatasetVerificationError(
                    "post-reserve or malformed final reserve timestamp"
                )
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    float(row[field])
                except (KeyError, ValueError):
                    raise DatasetVerificationError(
                        "invalid final reserve numeric field"
                    )
            if row["adjusted_close"]:
                try:
                    float(row["adjusted_close"])
                except ValueError:
                    raise DatasetVerificationError(
                        "invalid final reserve adjusted close"
                    )
            rows.append({field: row[field] for field in BUNDLE_FIELDS})
        rows.sort(key=lambda row: row["timestamp"])
        warmup_count = sum(row["timestamp"] < FINAL_RESERVE_START for row in rows)
        metadata = content["asset_metadata"][symbol]
        if (
            warmup_count > warmup_limit
            or not rows
            or len(rows) != metadata["total_rows"]
            or rows[0]["timestamp"] != metadata["earliest_included"]
            or rows[-1]["timestamp"] != metadata["latest_included"]
            or rows[-1]["timestamp"] != FINAL_RESERVE_END
        ):
            raise DatasetVerificationError("final reserve bundle coverage changed")
        per_symbol_rows[symbol] = rows

    calendar = sorted({
        row["timestamp"] for rows in per_symbol_rows.values() for row in rows
    })
    if not calendar or calendar[-1] != FINAL_RESERVE_END:
        raise DatasetVerificationError("final reserve evaluator maximum changed")
    return DatasetBundle(
        DATASET_REVISION,
        asset_set,
        per_symbol_rows,
        calendar,
        exposure_audit=FinalReserveExposureAudit(),
        bundle_revision=expected_revision,
        bundle_manifest_hash=expected_manifest_hash,
    )


def load_authorized_final_reserve_bundle() -> DatasetBundle:
    """The evaluator accepts no caller-selected source path or date range."""
    lock = load_preparation_lock()
    revision = lock["final_reserve_bundle_revision"]
    expected = (ROOT / lock["final_reserve_bundle_path"]).resolve()
    fixed = (ROOT / "data" / "final_reserve_bundles" / revision).resolve()
    if expected != fixed:
        raise DatasetVerificationError("final reserve bundle path is not fixed")
    return _load_bundle_directory(
        fixed, revision, lock["final_reserve_bundle_manifest_hash"]
    )


def isolation_snapshot(bundle: DatasetBundle) -> dict:
    if not isinstance(bundle.exposure_audit, FinalReserveExposureAudit):
        raise DatasetVerificationError("final reserve exposure instrumentation missing")
    counts = bundle.exposure_audit.snapshot()
    counts.update({
        "evaluator_visible_min_date": bundle.calendar[0],
        "evaluator_visible_max_date": bundle.calendar[-1],
        "observations_after_2026_08_25_available_to_evaluator": sum(
            row["timestamp"] > FINAL_RESERVE_END
            for rows in bundle.per_symbol_rows.values() for row in rows
        ),
        "historical_s5a_performance_inputs_accepted": 0,
        "historical_s5b_performance_inputs_accepted": 0,
        "historical_s5c_performance_inputs_accepted": 0,
        "bundle_revision": bundle.bundle_revision,
        "bundle_manifest_hash": bundle.bundle_manifest_hash,
    })
    return counts


def assert_isolated(bundle: DatasetBundle) -> dict:
    result = isolation_snapshot(bundle)
    forbidden = (
        "post_reserve_observations_exposed",
        "observations_after_2026_08_25_available_to_evaluator",
        "historical_s5a_performance_inputs_accepted",
        "historical_s5b_performance_inputs_accepted",
        "historical_s5c_performance_inputs_accepted",
    )
    if any(result[name] != 0 for name in forbidden):
        raise DatasetVerificationError(f"S5D final reserve isolation failed: {result}")
    if result["evaluator_visible_max_date"] != FINAL_RESERVE_END:
        raise DatasetVerificationError("S5D evaluator maximum changed")
    return result
