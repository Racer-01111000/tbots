"""Fail-closed loader for the single authorized S5B qualification bundle."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from lib.ids import canonical_json
from lib.replay import DatasetBundle, DatasetVerificationError
from s5b_config import (
    CHAMPIONSHIP_END,
    CHAMPIONSHIP_START,
    DATASET_REVISION,
    EPISODE_HASH,
    FINAL_RESERVE_START,
    QUALIFICATION_END,
    QUALIFICATION_LANE_HASH,
    QUALIFICATION_START,
    ROOT,
    SCORE_HASH,
    load_preparation_lock,
)

BUNDLE_FIELDS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume",
    "corporate_action",
)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class QualificationExposureAudit:
    def __init__(self):
        self.counts = {
            "prequalification_warmup_observations_exposed": 0,
            "qualification_observations_exposed": 0,
            "championship_observations_exposed": 0,
            "final_reserve_observations_exposed": 0,
            "other_observations_exposed": 0,
        }

    def record(self, timestamp: str, count: int = 1) -> None:
        if timestamp < QUALIFICATION_START:
            key = "prequalification_warmup_observations_exposed"
        elif timestamp <= QUALIFICATION_END:
            key = "qualification_observations_exposed"
        elif CHAMPIONSHIP_START <= timestamp <= CHAMPIONSHIP_END:
            key = "championship_observations_exposed"
        elif timestamp >= FINAL_RESERVE_START:
            key = "final_reserve_observations_exposed"
        else:
            key = "other_observations_exposed"
        self.counts[key] += count

    def snapshot(self) -> dict:
        return dict(self.counts)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_bundle_directory(bundle_dir: Path, expected_revision: str,
                           expected_manifest_hash: str) -> DatasetBundle:
    bundle_dir = Path(bundle_dir)
    path = bundle_dir / f"manifest_{expected_revision}.json"
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetVerificationError("authorized qualification bundle is unreadable") from exc
    if set(envelope) != {"content", "manifest_hash"}:
        raise DatasetVerificationError("invalid qualification bundle manifest envelope")
    content = envelope["content"]
    actual_manifest = "s5b_qual_manifest_" + _sha256(canonical_json(content).encode())
    if envelope["manifest_hash"] != expected_manifest_hash or actual_manifest != expected_manifest_hash:
        raise DatasetVerificationError("qualification bundle manifest hash mismatch")
    basis = content.get("revision_basis")
    actual_revision = "s5bqual_" + _sha256(canonical_json(basis).encode())
    if (
        actual_revision != expected_revision
        or content.get("derived_bundle_revision") != expected_revision
        or content.get("source_dataset_revision") != DATASET_REVISION
        or content.get("authorized_lane_manifest_hash") != QUALIFICATION_LANE_HASH
        or content.get("episode_protocol_hash") != EPISODE_HASH
        or content.get("score_protocol_hash") != SCORE_HASH
        or basis.get("lane_start") != QUALIFICATION_START
        or basis.get("lane_end") != QUALIFICATION_END
    ):
        raise DatasetVerificationError("qualification bundle identity or bounds changed")
    isolation = content.get("isolation_contract", {})
    if isolation != {
        "evaluator_has_no_normalized_source_path": True,
        "only_fixed_bundle_path_is_supported": True,
        "observations_after_2022_12_31": 0,
        "championship_observations": 0,
        "final_reserve_observations": 0,
        "historical_s5a_performance_payloads": 0,
    }:
        raise DatasetVerificationError("qualification isolation contract changed")

    asset_set = basis.get("asset_set")
    if not isinstance(asset_set, list) or not asset_set:
        raise DatasetVerificationError("qualification asset set is invalid")
    per_symbol_rows = {}
    shared_warmup = basis["warmup_policy"]["shared_bundle_prequalification_bars_per_asset"]
    for symbol in asset_set:
        artifact_path = bundle_dir / f"{symbol}.csv"
        try:
            artifact = artifact_path.read_bytes()
        except OSError as exc:
            raise DatasetVerificationError(f"missing qualification artifact: {symbol}") from exc
        if _sha256(artifact) != basis["artifact_hashes"].get(symbol):
            raise DatasetVerificationError(f"changed qualification artifact: {symbol}")
        reader = csv.DictReader(io.StringIO(artifact.decode()))
        if tuple(reader.fieldnames or ()) != BUNDLE_FIELDS:
            raise DatasetVerificationError(f"invalid qualification fields: {symbol}")
        rows = []
        for row in reader:
            timestamp = row.get("timestamp", "")
            if not ISO_DATE_RE.match(timestamp) or timestamp > QUALIFICATION_END:
                raise DatasetVerificationError("post-2022 or malformed qualification timestamp")
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    float(row[field])
                except (KeyError, ValueError):
                    raise DatasetVerificationError("invalid qualification numeric field")
            if row["adjusted_close"]:
                try:
                    float(row["adjusted_close"])
                except ValueError:
                    raise DatasetVerificationError("invalid qualification adjusted close")
            rows.append({field: row[field] for field in BUNDLE_FIELDS})
        rows.sort(key=lambda row: row["timestamp"])
        prequalification = sum(row["timestamp"] < QUALIFICATION_START for row in rows)
        if prequalification > shared_warmup:
            raise DatasetVerificationError("qualification bundle exceeds frozen warmup")
        metadata = content["asset_metadata"][symbol]
        if (
            len(rows) != metadata["total_rows"]
            or not rows
            or rows[0]["timestamp"] != metadata["earliest_included"]
            or rows[-1]["timestamp"] != metadata["latest_included"]
        ):
            raise DatasetVerificationError("qualification bundle coverage changed")
        per_symbol_rows[symbol] = rows

    calendar = sorted({row["timestamp"] for rows in per_symbol_rows.values() for row in rows})
    if not calendar or calendar[-1] > QUALIFICATION_END:
        raise DatasetVerificationError("qualification evaluator calendar exceeds 2022")
    return DatasetBundle(
        DATASET_REVISION,
        asset_set,
        per_symbol_rows,
        calendar,
        exposure_audit=QualificationExposureAudit(),
        bundle_revision=expected_revision,
        bundle_manifest_hash=expected_manifest_hash,
    )


def load_authorized_qualification_bundle() -> DatasetBundle:
    """The evaluator accepts no caller-supplied path, dates, or source dataset."""
    lock = load_preparation_lock()
    expected_revision = lock["qualification_bundle_revision"]
    expected = (ROOT / lock["qualification_bundle_path"]).resolve()
    fixed = (ROOT / "data" / "qualification_bundles" / expected_revision).resolve()
    if expected != fixed:
        raise DatasetVerificationError("qualification bundle path is not fixed")
    return _load_bundle_directory(
        fixed, expected_revision, lock["qualification_bundle_manifest_hash"]
    )


def isolation_snapshot(bundle: DatasetBundle) -> dict:
    if not isinstance(bundle.exposure_audit, QualificationExposureAudit):
        raise DatasetVerificationError("qualification exposure instrumentation missing")
    counts = bundle.exposure_audit.snapshot()
    counts.update({
        "evaluator_visible_min_date": bundle.calendar[0],
        "evaluator_visible_max_date": bundle.calendar[-1],
        "observations_after_2022_12_31_available_to_evaluator": sum(
            row["timestamp"] > QUALIFICATION_END
            for rows in bundle.per_symbol_rows.values() for row in rows
        ),
        "historical_s5a_performance_inputs_accepted": 0,
        "bundle_revision": bundle.bundle_revision,
        "bundle_manifest_hash": bundle.bundle_manifest_hash,
    })
    return counts


def assert_isolated(bundle: DatasetBundle) -> dict:
    result = isolation_snapshot(bundle)
    forbidden = (
        "championship_observations_exposed",
        "final_reserve_observations_exposed",
        "other_observations_exposed",
        "observations_after_2022_12_31_available_to_evaluator",
        "historical_s5a_performance_inputs_accepted",
    )
    if any(result[name] != 0 for name in forbidden):
        raise DatasetVerificationError(f"S5B qualification isolation failed: {result}")
    if result["evaluator_visible_max_date"] > QUALIFICATION_END:
        raise DatasetVerificationError("S5B qualification evaluator can see post-2022 data")
    return result
