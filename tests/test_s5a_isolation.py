import hashlib
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import indicators
from genome_control import CONTROL_GENOME
from lib.replay import AgentView, DatasetVerificationError, ReplayEngine
import s5a_config as config
from s5a_build_development_bundle import (
    _csv_bytes,
    derive_development_bundle,
    evaluation_content_revision,
    select_authorized_rows,
    write_development_bundle,
)
from s5a_development_bundle import (
    _load_bundle_directory,
    assert_isolated,
    load_authorized_development_bundle,
)
import s5a_evaluator
import s5a_evolution


def row(timestamp, close="100"):
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adjusted_close": close,
        "volume": "1000",
        "corporate_action": "",
    }


class DevelopmentBundleIsolationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.derived = derive_development_bundle(ROOT, "TEST_CONSTRUCTION_REVISION")
        cls.bundle_path = write_development_bundle(cls.derived, Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fresh_bundle(self):
        return _load_bundle_directory(
            self.bundle_path, self.derived["derived_bundle_revision"]
        )

    def test_malformed_locked_row_cannot_affect_selected_evaluation_content(self):
        base = [row("2007-02-07"), row("2018-12-31")]
        selected_a, _ = select_authorized_rows(base)
        malformed_locked = {"timestamp": "2019-01-02", "close": "SENTINEL"}
        selected_b, _ = select_authorized_rows([*base, malformed_locked])
        self.assertEqual(_csv_bytes(selected_a), _csv_bytes(selected_b))

    def test_locked_rows_are_unavailable_and_instrumented_fail_closed(self):
        bundle = self.fresh_bundle()
        self.assertTrue(all(
            row_["timestamp"] <= "2018-12-31"
            for rows in bundle.per_symbol_rows.values() for row_ in rows
        ))
        bundle.exposure_audit.record("2019-01-02")
        with self.assertRaises(DatasetVerificationError):
            assert_isolated(bundle)

    def test_evaluator_accepts_no_source_path_date_range_or_historical_payload(self):
        self.assertEqual(len(inspect.signature(load_authorized_development_bundle).parameters), 0)
        with self.assertRaises(TypeError):
            load_authorized_development_bundle(Path("/tmp/arbitrary"))
        with self.assertRaises(TypeError):
            s5a_evolution.run_evolution(fitness_input={"historical_s4": True})
        with self.assertRaises(TypeError):
            s5a_evaluator.simulate_episode(
                self.fresh_bundle(), CONTROL_GENOME,
                {"start_date": "2019-01-01", "end_date": "2022-12-31"},
            )

    def test_excessive_history_request_is_clamped_to_legitimate_bundle_rows(self):
        bundle = self.fresh_bundle()
        engine = ReplayEngine(
            ROOT, config.DATASET_REVISION, "2007-02-07", "2007-02-07",
            masked_time=True, retention_end_date="2018-12-31", _verified_bundle=bundle,
        )
        rows = AgentView(engine).history("SPY", 10**9)
        predevelopment = [r for r in rows if r["day"] <= "DAY 0000"]
        self.assertLessEqual(len(predevelopment), config.PREDEVELOPMENT_WARMUP_BARS)
        self.assertEqual(len(rows), config.PREDEVELOPMENT_WARMUP_BARS + 1)

    def test_late_inception_asset_is_not_eligible_when_history_is_insufficient(self):
        bundle = self.fresh_bundle()
        engine = ReplayEngine(
            ROOT, config.DATASET_REVISION, "2007-02-07", "2007-02-07",
            masked_time=True, retention_end_date="2018-12-31", _verified_bundle=bundle,
        )
        genome = dict(CONTROL_GENOME)
        genome.update({
            "momentum_lookbacks": [126, 252, 378],
            "trend_filter_window": 300,
            "volatility_window": 126,
        })
        config.validate_genome(genome)
        result = indicators.evaluate_symbol(AgentView(engine), "DBC", genome)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "insufficient_history_momentum")

    def test_locked_row_change_does_not_change_evaluation_content_revision(self):
        base = [row("2007-02-07", "100"), row("2018-12-31", "101")]
        a, _ = select_authorized_rows([*base, row("2019-01-02", "500")])
        b, _ = select_authorized_rows([*base, row("2019-01-02", "SENTINEL")])
        hashes_a = {"TEST": hashlib.sha256(_csv_bytes(a)).hexdigest()}
        hashes_b = {"TEST": hashlib.sha256(_csv_bytes(b)).hexdigest()}
        self.assertEqual(
            evaluation_content_revision(["TEST"], hashes_a)[0],
            evaluation_content_revision(["TEST"], hashes_b)[0],
        )

    def test_authorized_or_warmup_change_changes_bundle_revision(self):
        base = [row("2007-02-06", "99"), row("2007-02-07", "100")]
        changed_warmup = [row("2007-02-06", "98"), row("2007-02-07", "100")]
        changed_development = [row("2007-02-06", "99"), row("2007-02-07", "101")]
        revisions = []
        for rows in (base, changed_warmup, changed_development):
            selected, _ = select_authorized_rows(rows)
            hashes = {"TEST": hashlib.sha256(_csv_bytes(selected)).hexdigest()}
            revisions.append(evaluation_content_revision(["TEST"], hashes)[0])
        self.assertEqual(len(set(revisions)), 3)

    def test_real_bundle_exposes_only_warmup_and_development(self):
        bundle = self.fresh_bundle()
        s5a_evaluator.simulate_episode(bundle, CONTROL_GENOME, 0)
        result = assert_isolated(bundle)
        self.assertGreater(result["warmup_observations_exposed"], 0)
        self.assertGreater(result["development_observations_exposed"], 0)
        self.assertEqual(result["qualification_observations_exposed"], 0)
        self.assertEqual(result["championship_observations_exposed"], 0)
        self.assertEqual(result["final_reserve_observations_exposed"], 0)
        self.assertEqual(result["observations_after_2018_12_31_available_to_evaluator"], 0)


if __name__ == "__main__":
    unittest.main()
