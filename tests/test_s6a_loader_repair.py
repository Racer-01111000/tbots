"""Regression coverage for the S6A loader defect repair: load_historical_bundle's
universe-identity guard was order-sensitive list equality, which rejected the
one authorized DEVELOPMENT/QUALIFICATION bundles solely because their stored
asset_set is alphabetical while s6a_final.UNIVERSE is not. The repair makes
the comparison order-insensitive but still duplicate/length-sensitive
(sorted-list equality). These tests exercise the real load_historical_bundle()
path for the success cases, and a monkeypatched loader (same pattern already
used by any test needing a controlled non-production bundle) for the
rejection cases -- production content cannot be corrupted to prove a
rejection path without producing exactly that kind of fixture.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]
import s6a_final as p
import s6a_runtime as r


class _FakeBundle:
    def __init__(self, dataset_revision, asset_set):
        self.dataset_revision = dataset_revision
        self.asset_set = asset_set
        self.per_symbol_rows = {}


class S6ALoaderRepairTests(unittest.TestCase):
    def test_development_bundle_loads_despite_symbol_ordering(self):
        bundle = r.load_historical_bundle("development")
        self.assertEqual(set(bundle.asset_set), set(p.UNIVERSE))
        self.assertEqual(bundle.dataset_revision, p.DATASET)

    def test_qualification_bundle_loads_despite_symbol_ordering(self):
        bundle = r.load_historical_bundle("qualification")
        self.assertEqual(set(bundle.asset_set), set(p.UNIVERSE))
        self.assertEqual(bundle.dataset_revision, p.DATASET)

    def test_missing_symbol_is_rejected(self):
        fake = _FakeBundle(p.DATASET, p.UNIVERSE[:-1])
        with patch("s5a_development_bundle.load_authorized_development_bundle", return_value=fake):
            with self.assertRaises(r.BoundaryError):
                r.load_historical_bundle("development")

    def test_extra_symbol_is_rejected(self):
        fake = _FakeBundle(p.DATASET, list(p.UNIVERSE) + ["QQQ"])
        with patch("s5a_development_bundle.load_authorized_development_bundle", return_value=fake):
            with self.assertRaises(r.BoundaryError):
                r.load_historical_bundle("development")

    def test_duplicated_symbol_is_rejected(self):
        fake = _FakeBundle(p.DATASET, p.UNIVERSE[:-1] + [p.UNIVERSE[0]])
        with patch("s5a_development_bundle.load_authorized_development_bundle", return_value=fake):
            with self.assertRaises(r.BoundaryError):
                r.load_historical_bundle("development")

    def test_different_universe_is_rejected(self):
        fake = _FakeBundle(p.DATASET, ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"])
        with patch("s5a_development_bundle.load_authorized_development_bundle", return_value=fake):
            with self.assertRaises(r.BoundaryError):
                r.load_historical_bundle("development")

    def test_reordered_but_otherwise_identical_set_is_accepted(self):
        fake = _FakeBundle(p.DATASET, sorted(p.UNIVERSE))
        with patch("s5a_development_bundle.load_authorized_development_bundle", return_value=fake):
            bundle = r.load_historical_bundle("development")
            self.assertEqual(bundle.asset_set, sorted(p.UNIVERSE))


if __name__ == "__main__":
    unittest.main()
