import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

from hashing import sha256_bytes
from normalize import normalize_yahoo_chart, rows_to_csv


def _epoch(y, m, d, hour=12):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def build_fixture() -> bytes:
    """Nine synthetic bars exercising every edge case the S1 spec calls out:
    a clean run, a duplicate date, a row missing 'close', a row with
    high < low, a dividend day, and a split day."""
    days = [
        _epoch(2020, 1, 2),   # 0: clean
        _epoch(2020, 1, 3),   # 1: clean
        _epoch(2020, 1, 6),   # 2: duplicate (paired with index 3)
        _epoch(2020, 1, 6),   # 3: duplicate of index 2
        _epoch(2020, 1, 7),   # 4: missing close
        _epoch(2020, 1, 8),   # 5: invalid (high < low)
        _epoch(2020, 1, 9),   # 6: dividend day
        _epoch(2020, 1, 10),  # 7: split day
        _epoch(2020, 1, 13),  # 8: clean, no adjclose provided at this index handled globally
    ]
    quote = {
        "open":   [100.0, 101.5, 102.0, 102.1, 103.0, 104.0, 105.0, 106.0, 107.25],
        "high":   [100.5, 102.0, 102.5, 102.6, 103.5, 104.5, 105.5, 106.5, 107.75],
        "low":    [99.5,  101.0, 101.5, 101.6, 102.5, 105.0, 104.5, 105.5, 106.75],
        "close":  [100.25, 101.75, 102.25, 102.35, None, 104.25, 105.25, 106.25, 107.5],
        "volume": [1000, 1100, 1200, 1210, 1300, 1400, 1500, 1600, 1700],
    }
    # index 5: high(104.5) < low(105.0) -> invalid numeric
    adjclose = [v - 0.1 if v is not None else None for v in quote["close"]]

    raw = {
        "chart": {
            "result": [{
                "meta": {"symbol": "TEST"},
                "timestamp": days,
                "indicators": {
                    "quote": [quote],
                    "adjclose": [{"adjclose": adjclose}],
                },
                "events": {
                    "dividends": {str(days[6]): {"amount": 1.58, "date": days[6]}},
                    "splits": {str(days[7]): {
                        "numerator": 4, "denominator": 1, "splitRatio": "4:1", "date": days[7],
                    }},
                },
            }],
            "error": None,
        }
    }
    return json.dumps(raw).encode("utf-8")


class NormalizeTestCase(unittest.TestCase):
    def setUp(self):
        self.raw_bytes = build_fixture()
        self.rows, self.report = normalize_yahoo_chart(
            self.raw_bytes, "TEST", "yahoo-finance-chart-api-v8",
            source_url="file:fixture", ingested_at="2026-08-26T00:00:00+00:00",
        )

    def test_raw_file_hash_stability(self):
        h1 = sha256_bytes(self.raw_bytes)
        h2 = sha256_bytes(self.raw_bytes)
        self.assertEqual(h1, h2)

    def test_normalized_determinism(self):
        rows2, report2 = normalize_yahoo_chart(
            self.raw_bytes, "TEST", "yahoo-finance-chart-api-v8",
            source_url="file:fixture", ingested_at="2026-08-26T00:00:00+00:00",
        )
        self.assertEqual(rows_to_csv(self.rows), rows_to_csv(rows2))
        self.assertEqual(self.report, report2)

    def test_timestamp_ordering(self):
        timestamps = [r["timestamp"] for r in self.rows]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(timestamps), len(set(timestamps)))

    def test_duplicate_detection(self):
        self.assertIn("2020-01-06", self.report["duplicate_timestamps"])
        output_dates = {r["timestamp"] for r in self.rows}
        self.assertNotIn("2020-01-06", output_dates)

    def test_required_field_validation(self):
        missing = self.report["rejected_missing_field"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["date"], "2020-01-07")
        self.assertIn("close", missing[0]["missing"])
        output_dates = {r["timestamp"] for r in self.rows}
        self.assertNotIn("2020-01-07", output_dates)

    def test_invalid_numeric_rejection(self):
        invalid = self.report["rejected_invalid_numeric"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["date"], "2020-01-08")
        output_dates = {r["timestamp"] for r in self.rows}
        self.assertNotIn("2020-01-08", output_dates)

    def test_asset_identity_preservation(self):
        for r in self.rows:
            self.assertEqual(r["asset"], "TEST")
        other_rows, _ = normalize_yahoo_chart(
            self.raw_bytes, "OTHER", "yahoo-finance-chart-api-v8",
            source_url="file:fixture", ingested_at="2026-08-26T00:00:00+00:00",
        )
        for r in other_rows:
            self.assertEqual(r["asset"], "OTHER")

    def test_source_provenance_preservation(self):
        for r in self.rows:
            self.assertEqual(r["source"], "yahoo-finance-chart-api-v8")
            self.assertEqual(r["ingested_at"], "2026-08-26T00:00:00+00:00")
            # source_timestamp must round-trip and its UTC date must match
            # the row's own timestamp date derived from the same source bar
            parsed = datetime.fromisoformat(r["source_timestamp"])
            self.assertEqual(parsed.astimezone(timezone.utc).strftime("%Y-%m-%d"), r["timestamp"])

    def test_no_fabricated_timestamps(self):
        raw = json.loads(self.raw_bytes)
        raw_dates = {
            datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y-%m-%d")
            for e in raw["chart"]["result"][0]["timestamp"]
        }
        output_dates = {r["timestamp"] for r in self.rows}
        self.assertTrue(output_dates.issubset(raw_dates))

    def test_corporate_action_and_adjusted_close_distinct(self):
        by_date = {r["timestamp"]: r for r in self.rows}

        dividend_row = by_date["2020-01-09"]
        self.assertIsNotNone(dividend_row["corporate_action"])
        self.assertEqual(json.loads(dividend_row["corporate_action"])["dividend_amount"], 1.58)

        split_row = by_date["2020-01-10"]
        self.assertIsNotNone(split_row["corporate_action"])
        self.assertEqual(json.loads(split_row["corporate_action"])["split"]["split_ratio"], "4:1")

        clean_row = by_date["2020-01-02"]
        self.assertIsNone(clean_row["corporate_action"])
        self.assertNotEqual(clean_row["close"], clean_row["adjusted_close"])

    def test_missing_adjclose_series_is_explicit_none_not_fabricated(self):
        raw = json.loads(self.raw_bytes)
        del raw["chart"]["result"][0]["indicators"]["adjclose"]
        rows, _ = normalize_yahoo_chart(
            json.dumps(raw).encode("utf-8"), "TEST", "yahoo-finance-chart-api-v8",
            source_url="file:fixture", ingested_at="2026-08-26T00:00:00+00:00",
        )
        for r in rows:
            self.assertIsNone(r["adjusted_close"])

    def test_dataset_revision_hash_is_deterministic_and_sensitive_to_change(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from build_dataset_revision import canonical_json

        manifest_a = {"asset_set": ["SPY", "EFA"], "hashes": {"SPY": "aaa", "EFA": "bbb"}}
        manifest_b = {"hashes": {"EFA": "bbb", "SPY": "aaa"}, "asset_set": ["SPY", "EFA"]}
        self.assertEqual(
            sha256_bytes(canonical_json(manifest_a).encode()),
            sha256_bytes(canonical_json(manifest_b).encode()),
        )

        manifest_c = dict(manifest_a, hashes={"SPY": "aaa", "EFA": "ccc"})
        self.assertNotEqual(
            sha256_bytes(canonical_json(manifest_a).encode()),
            sha256_bytes(canonical_json(manifest_c).encode()),
        )


if __name__ == "__main__":
    unittest.main()
