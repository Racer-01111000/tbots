#!/usr/bin/env python3
"""Trusted, deterministic preprocessing for the S5A DEVELOPMENT bundle.

This program is infrastructure.  It is never imported by the evolutionary
evaluator.  It may verify the complete accepted source dataset, but its output
contains only the frozen DEVELOPMENT interval and the bounded per-asset warmup.
"""
import argparse
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
from lib.replay import load_and_verify_dataset
from s5_boundary import AUTHORIZED_PATHS, DEVELOPMENT_HASH, load_authorized_manifest
from s5a_config import (
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv_bytes(rows: list[dict]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=BUNDLE_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in BUNDLE_FIELDS})
    return out.getvalue().encode("utf-8")


def select_authorized_rows(rows: list[dict]) -> tuple[list[dict], int]:
    """Return at most 378 per-asset bars plus DEVELOPMENT observations."""
    lane_start = EPISODE_PROTOCOL["lane_start"]
    lane_end = EPISODE_PROTOCOL["lane_end"]
    ordered = sorted(rows, key=lambda row: row["timestamp"])
    warmup = [row for row in ordered if row["timestamp"] < lane_start]
    warmup = warmup[-PREDEVELOPMENT_WARMUP_BARS:]
    development = [
        row for row in ordered if lane_start <= row["timestamp"] <= lane_end
    ]
    return [*warmup, *development], len(warmup)


def evaluation_content_revision(asset_set: list[str], artifact_hashes: dict) -> tuple[str, dict]:
    revision_basis = {
        "schema_version": 1,
        "asset_set": asset_set,
        "authorized_lane_manifest_hash": DEVELOPMENT_LANE_HASH,
        "lane_start": EPISODE_PROTOCOL["lane_start"],
        "lane_end": EPISODE_PROTOCOL["lane_end"],
        "warmup_policy": WARMUP_POLICY,
        "artifact_hashes": artifact_hashes,
    }
    return "s5adev_" + _sha256(canonical_json(revision_basis).encode()), revision_basis


def derive_development_bundle(source_root: Path, construction_revision: str) -> dict:
    source_root = Path(source_root)
    lane = load_authorized_manifest(DEVELOPMENT_HASH)
    if lane.manifest_hash != DEVELOPMENT_LANE_HASH or lane.lane != "DEVELOPMENT":
        raise RuntimeError("trusted builder requires the frozen DEVELOPMENT manifest")

    # Trusted infrastructure verifies the complete accepted source.  Only the
    # selected bounded rows below are serialized into the evaluator bundle.
    source_bundle = load_and_verify_dataset(source_root, DATASET_REVISION)
    source_manifest_path = (
        source_root / "data" / "normalized" / f"manifest_{DATASET_REVISION}.json"
    )
    source_manifest_bytes = source_manifest_path.read_bytes()
    source_manifest = json.loads(source_manifest_bytes)
    lane_manifest_path = AUTHORIZED_PATHS[DEVELOPMENT_HASH]
    lane_manifest_bytes = lane_manifest_path.read_bytes()

    artifacts = {}
    artifact_hashes = {}
    asset_metadata = {}
    for symbol in source_bundle.asset_set:
        source_rows = source_bundle.per_symbol_rows[symbol]
        selected, warmup_count = select_authorized_rows(source_rows)
        if not selected:
            raise RuntimeError(f"authorized bundle would contain no rows for {symbol}")
        if any(row["timestamp"] > lane.end_date for row in selected):
            raise RuntimeError("post-DEVELOPMENT row reached bundle serialization")
        artifact = _csv_bytes(selected)
        artifacts[f"{symbol}.csv"] = artifact
        artifact_hashes[symbol] = _sha256(artifact)
        development_count = sum(row["timestamp"] >= lane.start_date for row in selected)
        asset_metadata[symbol] = {
            "source_earliest": source_rows[0]["timestamp"],
            "source_latest": source_rows[-1]["timestamp"],
            "earliest_included": selected[0]["timestamp"],
            "latest_included": selected[-1]["timestamp"],
            "warmup_rows_requested": PREDEVELOPMENT_WARMUP_BARS,
            "warmup_rows_included": warmup_count,
            "development_rows_included": development_count,
            "total_rows": len(selected),
            "artifact": f"{symbol}.csv",
            "artifact_sha256": artifact_hashes[symbol],
        }

    derived_revision, revision_basis = evaluation_content_revision(
        source_bundle.asset_set, artifact_hashes
    )
    content = {
        "schema_version": 1,
        "bundle_kind": "S5A_AUTHORIZED_DEVELOPMENT",
        "derived_bundle_revision": derived_revision,
        "revision_basis": revision_basis,
        "source_dataset_revision": DATASET_REVISION,
        "source_manifest_sha256": _sha256(source_manifest_bytes),
        "source_normalized_content_hashes": source_manifest["normalized_content_hashes"],
        "authorized_lane_manifest_hash": lane.manifest_hash,
        "authorized_lane_manifest_sha256": _sha256(lane_manifest_bytes),
        "warmup_policy": WARMUP_POLICY,
        "construction_code_revision": construction_revision,
        "asset_metadata": asset_metadata,
        "isolation_contract": {
            "only_fixed_bundle_path_is_supported_by_evaluator": True,
            "observations_after_development_end": 0,
            "historical_performance_payloads": 0,
        },
    }
    manifest_hash = "s5a_dev_manifest_" + _sha256(canonical_json(content).encode())
    envelope = {"content": content, "manifest_hash": manifest_hash}
    return {
        "derived_bundle_revision": derived_revision,
        "manifest_hash": manifest_hash,
        "manifest_bytes": (json.dumps(envelope, sort_keys=True, indent=2) + "\n").encode(),
        "artifacts": artifacts,
        "content": content,
    }


def write_development_bundle(derived: dict, bundle_base: Path = BUNDLE_BASE) -> Path:
    target = Path(bundle_base) / derived["derived_bundle_revision"]
    target.mkdir(parents=True, exist_ok=True)
    expected = {
        **derived["artifacts"],
        f"manifest_{derived['derived_bundle_revision']}.json": derived["manifest_bytes"],
    }
    existing = {path.name for path in target.iterdir() if path.is_file()}
    unexpected = existing - set(expected)
    if unexpected:
        raise RuntimeError(f"unexpected files in immutable bundle directory: {sorted(unexpected)}")
    for name, payload in expected.items():
        path = target / name
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"refusing to overwrite changed immutable bundle artifact: {path}")
        if not path.exists():
            path.write_bytes(payload)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()
    revision, dirty = get_code_revision(str(ROOT))
    if dirty and not args.print_only:
        raise SystemExit("production bundle construction requires a clean committed revision")
    derived = derive_development_bundle(ROOT, revision if not dirty else "UNCOMMITTED_DRY_RUN")
    if not args.print_only:
        path = write_development_bundle(derived)
    else:
        path = None
    print(json.dumps({
        "derived_bundle_revision": derived["derived_bundle_revision"],
        "manifest_hash": derived["manifest_hash"],
        "path": str(path) if path else None,
        "construction_code_revision": revision if not dirty else "UNCOMMITTED_DRY_RUN",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
