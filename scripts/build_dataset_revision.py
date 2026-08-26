#!/usr/bin/env python3
"""Normalize the preserved raw files under data/raw/ into data/normalized/,
then compute and record a content-addressed dataset revision. Reads
data/raw/*.json — makes no network calls itself, so it is exactly
reproducible from a fixed set of raw inputs."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hashing import sha256_bytes, sha256_file
from normalize import NORMALIZATION_VERSION, normalize_yahoo_chart, rows_to_csv

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
NORM_DIR = ROOT / "data" / "normalized"
UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
SOURCE = "yahoo-finance-chart-api-v8"


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build() -> dict:
    """ingested_at is never freshly generated here: normalization is a
    pure re-derivation of already-fetched raw bytes, so each row's
    ingested_at comes from the per-ticker fetched_at already recorded in
    data/raw/manifest.json by fetch_raw.py. That's what makes re-running
    this script against unchanged raw files reproduce byte-identical
    normalized output and the same dataset_revision — a fresh
    datetime.now() here would silently break that on every re-run."""
    NORM_DIR.mkdir(parents=True, exist_ok=True)

    raw_manifest_path = RAW_DIR / "manifest.json"
    if not raw_manifest_path.exists():
        raise FileNotFoundError(f"missing {raw_manifest_path} (run fetch_raw.py first)")
    raw_manifest = json.loads(raw_manifest_path.read_text())

    source_artifact_hashes = {}
    normalized_content_hashes = {}
    coverage_metadata = {}
    per_ticker_reports = {}

    per_ticker_dates: dict[str, set] = {}

    for ticker in UNIVERSE:
        raw_path = RAW_DIR / f"{ticker}.json"
        if not raw_path.exists():
            raise FileNotFoundError(
                f"missing raw artifact for {ticker}: {raw_path} "
                "(run fetch_raw.py first; build_dataset_revision.py never fetches)"
            )
        raw_entry = raw_manifest["entries"].get(ticker)
        if not raw_entry or "fetched_at" not in raw_entry:
            raise ValueError(f"no fetched_at recorded for {ticker} in {raw_manifest_path}")
        ingested_at = raw_entry["fetched_at"]

        raw_bytes = raw_path.read_bytes()
        source_artifact_hashes[ticker] = sha256_bytes(raw_bytes)

        rows, report = normalize_yahoo_chart(
            raw_bytes, ticker, SOURCE,
            source_url=f"file:{raw_path}", ingested_at=ingested_at,
        )
        csv_text = rows_to_csv(rows)
        norm_path = NORM_DIR / f"{ticker}.csv"
        norm_path.write_text(csv_text)
        normalized_content_hashes[ticker] = sha256_file(norm_path)

        per_ticker_dates[ticker] = {r["timestamp"] for r in rows}
        per_ticker_reports[ticker] = report
        coverage_metadata[ticker] = {
            "start": report["start"],
            "end": report["end"],
            "rows": report["output_row_count"],
            "duplicate_timestamps": report["duplicate_timestamps"],
            "rejected_missing_field": report["rejected_missing_field"],
            "rejected_invalid_numeric": report["rejected_invalid_numeric"],
        }

    # Missing-trading-day report: reference set = union of all dates any
    # asset in the universe actually traded on. For each asset, "missing"
    # = reference dates that fall within ITS OWN observed [start, end]
    # window but aren't in its own date set. This never flags a pre-
    # inception gap as missing, and needs no external holiday calendar.
    all_dates = set()
    for dates in per_ticker_dates.values():
        all_dates |= dates
    for ticker in UNIVERSE:
        start, end = coverage_metadata[ticker]["start"], coverage_metadata[ticker]["end"]
        own_dates = per_ticker_dates[ticker]
        if start and end:
            missing = sorted(d for d in all_dates if start <= d <= end and d not in own_dates)
        else:
            missing = []
        coverage_metadata[ticker]["missing_trading_days"] = missing
        coverage_metadata[ticker]["missing_trading_days_count"] = len(missing)
        coverage_metadata[ticker]["missing_trading_days_note"] = (
            "reference set = union of trading dates observed across the asset "
            "universe within this asset's own coverage window; not an "
            "independently verified NYSE holiday calendar"
        )

    manifest_for_hash = {
        "asset_set": sorted(UNIVERSE),
        "source_identity": SOURCE,
        "source_artifact_hashes": source_artifact_hashes,
        "normalization_version": NORMALIZATION_VERSION,
        "normalized_content_hashes": normalized_content_hashes,
        "coverage_metadata": coverage_metadata,
    }
    dataset_revision = "ds_" + sha256_bytes(canonical_json(manifest_for_hash).encode("utf-8"))

    full_manifest = dict(manifest_for_hash)
    full_manifest["dataset_revision"] = dataset_revision
    # created_at records when THIS BUILD ran, not when the data was
    # ingested — deliberately excluded from manifest_for_hash above so
    # re-running the build at a later wall-clock time doesn't change the
    # dataset_revision.
    full_manifest["created_at"] = datetime.now(timezone.utc).isoformat()

    manifest_path = NORM_DIR / f"manifest_{dataset_revision}.json"
    manifest_path.write_text(json.dumps(full_manifest, indent=2, sort_keys=True))

    return {
        "dataset_revision": dataset_revision,
        "manifest_path": str(manifest_path.relative_to(ROOT)),
        "manifest": full_manifest,
        "per_ticker_reports": per_ticker_reports,
    }


if __name__ == "__main__":
    result = build()
    print(f"dataset_revision: {result['dataset_revision']}")
    print(f"manifest: {result['manifest_path']}")
    for ticker, cov in result["manifest"]["coverage_metadata"].items():
        print(f"  {ticker}: {cov['start']} .. {cov['end']}  rows={cov['rows']}  "
              f"missing={cov['missing_trading_days_count']}  "
              f"dup={len(cov['duplicate_timestamps'])}  "
              f"rejected={len(cov['rejected_missing_field']) + len(cov['rejected_invalid_numeric'])}")
