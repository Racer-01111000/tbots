import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import build_dataset_revision as bdr


def _fixture_raw(ticker: str) -> bytes:
    day = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    raw = {
        "chart": {
            "result": [{
                "meta": {"symbol": ticker},
                "timestamp": [day],
                "indicators": {
                    "quote": [{"open": [10.0], "high": [11.0], "low": [9.0],
                               "close": [10.5], "volume": [1000]}],
                    "adjclose": [{"adjclose": [10.4]}],
                },
                "events": {},
            }],
            "error": None,
        }
    }
    return json.dumps(raw).encode("utf-8")


class BuildDeterminismTestCase(unittest.TestCase):
    """Regression test: build() previously stamped a fresh datetime.now()
    into every normalized row's ingested_at on each invocation, which
    silently changed normalized_content_hashes (and therefore
    dataset_revision) between two runs over IDENTICAL raw input. Fixed by
    sourcing ingested_at from data/raw/manifest.json's recorded
    fetched_at instead of generating it at normalization time."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        raw_dir = root / "data" / "raw"
        norm_dir = root / "data" / "normalized"
        raw_dir.mkdir(parents=True)

        self.tickers = ["T1", "T2"]
        manifest_entries = {}
        for t in self.tickers:
            data = _fixture_raw(t)
            (raw_dir / f"{t}.json").write_bytes(data)
            manifest_entries[t] = {
                "ticker": t,
                "fetched_at": "2026-01-01T00:00:00+00:00",
            }
        (raw_dir / "manifest.json").write_text(
            json.dumps({"source": "test", "entries": manifest_entries})
        )

        self._orig = (bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE)
        bdr.ROOT = root
        bdr.RAW_DIR = raw_dir
        bdr.NORM_DIR = norm_dir
        bdr.UNIVERSE = self.tickers

    def tearDown(self):
        bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = self._orig
        self.tmpdir.cleanup()

    def test_dataset_revision_identical_across_reruns_on_unchanged_raw_input(self):
        result1 = bdr.build()
        csv_after_run1 = {t: (bdr.NORM_DIR / f"{t}.csv").read_text() for t in self.tickers}

        time.sleep(1.1)  # force a different wall-clock second between runs
        result2 = bdr.build()
        csv_after_run2 = {t: (bdr.NORM_DIR / f"{t}.csv").read_text() for t in self.tickers}

        self.assertEqual(result1["dataset_revision"], result2["dataset_revision"])
        self.assertEqual(
            result1["manifest"]["normalized_content_hashes"],
            result2["manifest"]["normalized_content_hashes"],
        )
        self.assertEqual(csv_after_run1, csv_after_run2)
        # created_at is run metadata, expected to differ
        self.assertNotEqual(result1["manifest"]["created_at"], result2["manifest"]["created_at"])


if __name__ == "__main__":
    unittest.main()
