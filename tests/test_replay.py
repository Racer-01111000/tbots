import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import build_dataset_revision as bdr
from replay import (
    ALLOWED_ASSET_FIELDS, AgentView, DatasetVerificationError, EpisodeCompletedError,
    ReplayEngine, canonical_json, observation_hash,
)

WEEKDAYS = [
    (2021, 1, 4), (2021, 1, 5), (2021, 1, 6), (2021, 1, 7), (2021, 1, 8),
    (2021, 1, 11), (2021, 1, 12), (2021, 1, 13), (2021, 1, 14), (2021, 1, 15),
    (2021, 1, 19), (2021, 1, 20), (2021, 1, 21), (2021, 1, 22),
]
ISO = lambda t: f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"


def _epoch(y, m, d, hour=16):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def _fixture_raw_for(ticker, dates, base_price=100.0, dividend_on=None):
    days = [_epoch(*d) for d in dates]
    n = len(days)
    quote = {
        "open": [base_price + i for i in range(n)],
        "high": [base_price + i + 0.5 for i in range(n)],
        "low": [base_price + i - 0.5 for i in range(n)],
        "close": [base_price + i + 0.2 for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    }
    adjclose = [v - 1.0 for v in quote["close"]]
    events = {}
    if dividend_on is not None:
        div_epoch = _epoch(*dividend_on)
        events["dividends"] = {str(div_epoch): {"amount": 0.5, "date": div_epoch}}
    raw = {
        "chart": {
            "result": [{
                "meta": {"symbol": ticker},
                "timestamp": days,
                "indicators": {"quote": [quote], "adjclose": [{"adjclose": adjclose}]},
                "events": events,
            }],
            "error": None,
        }
    }
    return json.dumps(raw).encode("utf-8")


class ReplayTestBase(unittest.TestCase):
    """Builds a real, self-consistent synthetic dataset through the actual
    S1 pipeline (not hand-authored JSON), so hash verification and content
    addressing are exercised for real. AAA/BBB run the full window; CCC
    has a late inception (like DBC); AAA pays one dividend mid-window."""

    LATE_START_IDX = 6

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        raw_dir = self.root / "data" / "raw"
        norm_dir = self.root / "data" / "normalized"
        raw_dir.mkdir(parents=True)

        self.all_dates = WEEKDAYS
        self.late_dates = WEEKDAYS[self.LATE_START_IDX:]
        self.dividend_date = WEEKDAYS[3]

        manifest_entries = {}
        specs = [
            ("AAA", self.all_dates, {"dividend_on": self.dividend_date}),
            ("BBB", self.all_dates, {}),
            ("CCC", self.late_dates, {}),
        ]
        for ticker, dates, kwargs in specs:
            data = _fixture_raw_for(ticker, dates, **kwargs)
            (raw_dir / f"{ticker}.json").write_bytes(data)
            manifest_entries[ticker] = {"ticker": ticker, "fetched_at": "2026-01-01T00:00:00+00:00"}
        (raw_dir / "manifest.json").write_text(
            json.dumps({"source": "test", "entries": manifest_entries})
        )

        orig = (bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE)
        bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = self.root, raw_dir, norm_dir, ["AAA", "BBB", "CCC"]
        try:
            result = bdr.build()
        finally:
            bdr.ROOT, bdr.RAW_DIR, bdr.NORM_DIR, bdr.UNIVERSE = orig

        self.dataset_revision = result["dataset_revision"]
        self.norm_dir = norm_dir
        self.d = [ISO(t) for t in self.all_dates]        # d[0]..d[13]
        self.late_d = [ISO(t) for t in self.late_dates]
        self.dividend_d = ISO(self.dividend_date)

    def tearDown(self):
        self.tmpdir.cleanup()

    def engine(self, start=None, end=None, **kw):
        return ReplayEngine(self.root, self.dataset_revision,
                             start or self.d[0], end or self.d[-1], **kw)


class DatasetVerificationTestCase(ReplayTestBase):
    def test_valid_dataset_loads(self):
        eng = self.engine()
        self.assertEqual(eng.step_count, len(self.d))

    def test_unknown_dataset_revision_refused(self):
        with self.assertRaises(DatasetVerificationError):
            ReplayEngine(self.root, "ds_" + "0" * 64, self.d[0], self.d[-1])

    def test_missing_manifest_refused(self):
        for f in self.norm_dir.glob("manifest_*.json"):
            f.unlink()
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_changed_normalized_csv_refused(self):
        csv_path = self.norm_dir / "AAA.csv"
        original = csv_path.read_text()
        csv_path.write_text(original + "\n")  # append a byte -> hash no longer matches
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_test_fixture_cannot_masquerade_as_accepted_historical_data(self):
        csv_path = self.norm_dir / "AAA.csv"
        fixture_bytes = csv_path.read_bytes() + b"TEST_FIXTURE_ONLY\n"
        csv_path.write_bytes(fixture_bytes)
        manifest_path = next(self.norm_dir.glob("manifest_*.json"))
        manifest = json.loads(manifest_path.read_text())
        manifest["normalized_content_hashes"]["AAA"] = hashlib.sha256(
            fixture_bytes
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_flipped_manifest_dataset_revision_field_refused(self):
        """Editing ONLY the manifest's self-reported dataset_revision string
        (content otherwise untouched) must still be caught -- proves we
        recompute the hash rather than trusting that field."""
        manifest_path = next(self.norm_dir.glob("manifest_*.json"))
        manifest = json.loads(manifest_path.read_text())
        real = manifest["dataset_revision"]
        manifest["dataset_revision"] = real[:-1] + ("0" if real[-1] != "0" else "1")
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_tampered_content_hash_entry_refused(self):
        """Editing a normalized_content_hashes entry to lie about a CSV's
        hash must be caught by the independent per-file re-hash, even
        though it would make the manifest-level recompute self-consistent
        with the (also edited) claim."""
        manifest_path = next(self.norm_dir.glob("manifest_*.json"))
        manifest = json.loads(manifest_path.read_text())
        manifest["normalized_content_hashes"]["AAA"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_missing_normalized_artifact_refused(self):
        (self.norm_dir / "BBB.csv").unlink()
        with self.assertRaises(DatasetVerificationError):
            self.engine()

    def test_does_not_modify_the_dataset_it_verifies(self):
        before = {f.name: f.read_bytes() for f in self.norm_dir.glob("*")}
        self.engine()
        after = {f.name: f.read_bytes() for f in self.norm_dir.glob("*")}
        self.assertEqual(before, after)


class ClockAndEpisodeLifecycleTestCase(ReplayTestBase):
    def test_legal_advancement_walks_every_calendar_date_in_order(self):
        eng = self.engine()
        seen = [eng.current_timestamp]
        while eng.advance():
            seen.append(eng.current_timestamp)
        self.assertEqual(seen, self.d)
        self.assertEqual(eng.status, "COMPLETED")

    def test_step_count_is_dates_in_range_inclusive(self):
        eng = self.engine(start=self.d[2], end=self.d[8])
        self.assertEqual(eng.step_count, 7)  # indices 2..8 inclusive

    def test_no_reversal_or_seek_method_exists(self):
        eng = self.engine()
        for forbidden in ("reverse", "seek", "set_clock", "set_index", "rewind", "goto"):
            self.assertFalse(hasattr(eng, forbidden), f"ReplayEngine exposes {forbidden}")

    def test_episode_end_enforcement(self):
        eng = self.engine()
        while eng.advance():
            pass
        with self.assertRaises(EpisodeCompletedError):
            eng.observe()
        with self.assertRaises(EpisodeCompletedError):
            eng.advance()
        with self.assertRaises(EpisodeCompletedError):
            eng.history("AAA", 5)

    def test_agent_view_has_no_advance_or_engine_surface(self):
        eng = self.engine()
        view = AgentView(eng)
        for forbidden in ("advance", "engine", "bundle", "dataset", "rows", "_engine",
                           "reset", "seek", "reverse"):
            self.assertFalse(hasattr(view, forbidden), f"AgentView exposes {forbidden}")
        with self.assertRaises(AttributeError):
            view.advance()


class RetentionBoundaryTestCase(ReplayTestBase):
    def test_bundle_retains_no_rows_after_authorized_lane_end(self):
        eng = self.engine(end=self.d[5], retention_end_date=self.d[5])
        for rows in eng.bundle.per_symbol_rows.values():
            self.assertTrue(all(row["timestamp"] <= self.d[5] for row in rows))
        self.assertEqual(eng.bundle.calendar[-1], self.d[5])

    def test_episode_end_cannot_exceed_retention_end(self):
        with self.assertRaisesRegex(ValueError, "retention boundary"):
            self.engine(end=self.d[8], retention_end_date=self.d[5])


class ObservationBoundaryTestCase(ReplayTestBase):
    def test_future_ohlcv_never_appears_in_observation(self):
        eng = self.engine()
        view = AgentView(eng)
        # step to a mid-window date and grab ground truth for the NEXT date
        # directly from the verified bundle (test oracle, not the agent path)
        for _ in range(3):
            eng.advance()
        clock = eng.current_timestamp
        future_row = eng.bundle.per_symbol_rows["AAA"][eng._symbol_timestamps["AAA"].index(clock) + 1]

        obs = view.observe()
        self.assertEqual(obs["timestamp"], clock)
        self.assertNotEqual(obs["assets"]["AAA"]["close"], future_row["close"])
        serialized = json.dumps(obs)
        self.assertNotIn(future_row["close"], serialized)
        # the future date itself must not appear anywhere either
        future_date = future_row["timestamp"]
        self.assertNotIn(future_date, serialized)

    def test_history_never_extends_past_clock(self):
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        clock = eng.current_timestamp
        hist = view.history("AAA", 1000)
        self.assertTrue(all(h["timestamp"] <= clock for h in hist))
        self.assertEqual(hist[-1]["timestamp"], clock)

    def test_history_huge_request_terminates_and_clamps(self):
        eng = self.engine()
        view = AgentView(eng)
        hist = view.history("AAA", 10 ** 9)
        self.assertEqual(len(hist), 1)  # only day 0's own bar is available so far

    def test_history_unknown_symbol_raises(self):
        eng = self.engine()
        view = AgentView(eng)
        with self.assertRaises(ValueError):
            view.history("ZZZ", 5)

    def test_history_pre_inception_returns_empty(self):
        eng = self.engine()  # starts at d[0], before CCC's late_d[0]
        view = AgentView(eng)
        self.assertEqual(view.history("CCC", 5), [])

    def test_only_whitelisted_fields_ever_appear(self):
        """Backs the acceptance receipt's 'future indicators exposed: 0'
        line: proves the payload's per-asset keys are exactly the allowed
        set, nothing more (no indicator field could leak if none exists)."""
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(3):
            eng.advance()
        obs = view.observe()
        allowed = set(ALLOWED_ASSET_FIELDS) | {"available"}
        for symbol, fields in obs["assets"].items():
            if fields["available"]:
                self.assertEqual(set(fields.keys()) - {"available"}, set(ALLOWED_ASSET_FIELDS))
            else:
                self.assertEqual(set(fields.keys()), {"available"})
        for row in view.history("AAA", 10):
            self.assertEqual(set(row.keys()) - {"available", "timestamp", "day"}, set(ALLOWED_ASSET_FIELDS))


class AssetInceptionTestCase(ReplayTestBase):
    def test_asset_unavailable_before_inception(self):
        eng = self.engine()
        view = AgentView(eng)
        obs = view.observe()
        self.assertEqual(obs["assets"]["CCC"], {"available": False})

    def test_asset_available_from_inception_onward(self):
        eng = self.engine()
        view = AgentView(eng)
        while eng.current_timestamp < self.late_d[0]:
            eng.advance()
        obs = view.observe()
        self.assertTrue(obs["assets"]["CCC"]["available"])

    def test_episode_not_dropped_by_one_asset_missing(self):
        eng = self.engine()
        view = AgentView(eng)
        obs = view.observe()  # CCC absent, AAA/BBB present -- must not raise
        self.assertTrue(obs["assets"]["AAA"]["available"])
        self.assertTrue(obs["assets"]["BBB"]["available"])
        self.assertFalse(obs["assets"]["CCC"]["available"])


class CorporateActionTestCase(ReplayTestBase):
    def test_future_corporate_action_not_exposed(self):
        eng = self.engine()
        view = AgentView(eng)
        obs = view.observe()  # well before dividend_date
        self.assertEqual(obs["assets"]["AAA"]["corporate_action"], "")
        serialized = json.dumps(obs)
        self.assertNotIn("dividend_amount", serialized)

    def test_corporate_action_exposed_exactly_on_its_date(self):
        eng = self.engine()
        view = AgentView(eng)
        while eng.current_timestamp < self.dividend_d:
            eng.advance()
        obs = view.observe()
        self.assertIn("dividend_amount", obs["assets"]["AAA"]["corporate_action"])

    def test_corporate_action_boundary_via_history(self):
        eng = self.engine()
        view = AgentView(eng)
        while eng.current_timestamp < self.dividend_d:
            eng.advance()
        hist = view.history("AAA", 100)
        actions = [h["corporate_action"] for h in hist]
        self.assertEqual(sum(1 for a in actions if a), 1)  # exactly the one dividend, no more


class MaskedTimeTestCase(ReplayTestBase):
    DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

    def _leaked_dates(self, obj, real_dates):
        """Recursively serializes obj and scans for ISO-date patterns AND
        the specific real dates used to build this episode -- not just a
        top-level key check."""
        serialized = json.dumps(obj)
        regex_hits = self.DATE_RE.findall(serialized)
        exact_hits = [d for d in real_dates if d in serialized]
        return regex_hits, exact_hits

    def test_masked_observation_has_no_real_date(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        obs = view.observe()
        self.assertNotIn("timestamp", obs)
        self.assertEqual(obs["day"], "DAY 0001")
        regex_hits, exact_hits = self._leaked_dates(obs, self.d)
        self.assertEqual(regex_hits, [])
        self.assertEqual(exact_hits, [])

    def test_masked_history_has_no_real_dates_full_payload_scan(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        combined = {"observe": view.observe(), "history": view.history("AAA", 5)}
        regex_hits, exact_hits = self._leaked_dates(combined, self.d)
        self.assertEqual(regex_hits, [])
        self.assertEqual(exact_hits, [])
        for row in combined["history"]:
            self.assertIn("day", row)
            self.assertNotIn("timestamp", row)

    def test_no_epoch_magnitude_integers_anywhere_in_payload(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        combined = {"observe": view.observe(), "history": view.history("AAA", 5)}

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    yield from walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from walk(v)
            else:
                yield o

        for leaf in walk(combined):
            if isinstance(leaf, int) and not isinstance(leaf, bool):
                self.assertLess(abs(leaf), 10 ** 6, f"suspicious large int leaked: {leaf}")

    def test_history_day_numbers_can_precede_episode_start(self):
        eng = self.engine(start=self.d[5], end=self.d[-1], masked_time=True)
        view = AgentView(eng)
        hist = view.history("AAA", 4)  # 4 bars ending at day 1 -> days -2,-1,0,1
        self.assertEqual([h["day"] for h in hist], ["DAY -002", "DAY -001", "DAY 0000", "DAY 0001"])

    def test_unmasked_mode_does_carry_real_timestamp(self):
        eng = self.engine(masked_time=False)
        view = AgentView(eng)
        obs = view.observe()
        self.assertEqual(obs["timestamp"], self.d[0])
        self.assertNotIn("day", obs)


class DeterminismTestCase(ReplayTestBase):
    def _walk_all_hashes(self, **kw):
        eng = self.engine(**kw)
        view = AgentView(eng)
        hashes = []
        while True:
            hashes.append(observation_hash(view.observe()))
            if not eng.advance():
                break
        return hashes, eng.status, eng.current_timestamp

    def test_identical_construction_produces_identical_hash_sequence(self):
        h1, status1, end1 = self._walk_all_hashes()
        h2, status2, end2 = self._walk_all_hashes()
        self.assertEqual(h1, h2)
        self.assertEqual((status1, end1), (status2, end2))
        self.assertEqual(len(h1), len(self.d))

    def test_masked_and_unmasked_hash_sequences_differ(self):
        h_unmasked, _, _ = self._walk_all_hashes(masked_time=False)
        h_masked, _, _ = self._walk_all_hashes(masked_time=True)
        self.assertNotEqual(h_unmasked, h_masked)

    def test_full_episode_rerun_matches_including_history(self):
        def run():
            eng = self.engine()
            view = AgentView(eng)
            record = []
            while True:
                record.append((view.observe(), view.history("AAA", 5)))
                if not eng.advance():
                    break
            return record

        r1, r2 = run(), run()
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
