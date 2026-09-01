import copy
import hashlib
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib.ids import canonical_json
from lib.replay import DatasetVerificationError
from s5d_build_final_reserve_bundle import (
    authenticate_champion_without_performance,
    csv_bytes,
    select_authorized_rows,
)
from s5d_final_reserve import final_reserve_result_digest
from s5d_final_reserve_bundle import (
    _load_bundle_directory,
    assert_isolated,
    load_authorized_final_reserve_bundle,
)
import s5d_final_reserve as runner
import s5d_config as config
import s5d_evaluator as evaluator
from s5d_champion import load_frozen_champion


def synthetic_episode(total_return=-0.01):
    return {
        "episode_index": 0,
        "start_date": "2026-01-01",
        "end_date": "2026-08-25",
        "initial_state": {
            "cash_cents": 100_000_000,
            "positions": {},
            "peak_equity_cents": 100_000_000,
            "drawdown": 0.0,
            "halted": False,
            "pending_orders": 0,
        },
        "initial_state_hash": "fixture",
        "starting_cash_cents": 100_000_000,
        "final_equity_cents": 99_000_000,
        "total_return": total_return,
        "sharpe": -0.2,
        "sortino": -0.3,
        "max_drawdown": -0.04,
        "halted": False,
        "turnover": 0.8,
        "commission_cents": 40_000,
        "slippage_cents": 40_000,
        "transaction_cost_cents": 80_000,
        "transaction_cost_rate": 0.0008,
        "order_count": 3,
        "fill_count": 3,
        "step_count": 100,
        "verifier_checks": 0,
    }


def source_row(timestamp, close="100"):
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


class S5DProtocolTestCase(unittest.TestCase):
    def test_one_exact_contiguous_reserve_episode_and_fresh_state_are_frozen(self):
        self.assertEqual(config.EPISODE_PROTOCOL["episodes"], [{
            "episode_index": 0,
            "start_date": "2026-01-01",
            "end_date": "2026-08-25",
        }])
        self.assertEqual(config.EPISODE_PROTOCOL["starting_cash_cents"], 100_000_000)
        self.assertEqual(
            set(config.EPISODE_PROTOCOL["fresh_state"]),
            {"cash", "portfolio", "peak_equity", "drawdown", "halt"},
        )
        self.assertFalse(config.EPISODE_PROTOCOL["champion_mutation_allowed"])
        self.assertFalse(config.EPISODE_PROTOCOL["competing_genomes_allowed"])
        self.assertFalse(config.EPISODE_PROTOCOL["selection_or_optimization_allowed"])

    def test_reporting_has_no_score_threshold_selection_or_replacement(self):
        protocol = config.OUTCOME_PROTOCOL
        self.assertEqual(protocol["fitness_score"], "NOT_APPLICABLE")
        self.assertEqual(protocol["minimum_return_hurdle"], "NONE")
        self.assertEqual(protocol["ranking"], "NOT_APPLICABLE_ONE_FROZEN_CHAMPION")
        self.assertEqual(
            protocol["replacement_or_retraining_on_negative_return"],
            "PROHIBITED",
        )
        outcome = evaluator.summarize_final_reserve(
            synthetic_episode(total_return=-0.25)
        )
        self.assertEqual(outcome["total_return"], -0.25)
        self.assertIsNone(outcome["selection_or_replacement"])
        self.assertIsNone(outcome["acceptance_threshold"])
        self.assertEqual(outcome["performance_concentration"], 0.0)

    def test_all_required_outcome_metrics_are_recorded(self):
        outcome = evaluator.summarize_final_reserve(synthetic_episode())
        self.assertEqual(
            set(config.OUTCOME_PROTOCOL["required_outcome_fields"]),
            {
                "total_return", "sharpe", "sortino", "max_drawdown", "halted",
                "turnover", "commission_cents", "slippage_cents",
                "transaction_cost_cents", "transaction_cost_rate",
                "performance_concentration", "final_equity_cents",
            },
        )
        self.assertTrue(
            set(config.OUTCOME_PROTOCOL["required_outcome_fields"]) <= set(outcome)
        )

    def test_prior_performance_feedback_and_competition_are_rejected(self):
        episode = synthetic_episode()
        forbidden = (
            {"historical_s5a_performance": {"fitness": 1}},
            {"historical_s5b_performance": {"score": 1}},
            {"historical_s5c_performance": {"score": 1}},
            {"evolutionary_feedback": {"winner": "changed"}},
            {"competing_genomes": ["gen_forged"]},
            {"acceptance_threshold": 0.1},
        )
        for kwargs in forbidden:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(evaluator.ContaminatedFinalReserveInput):
                    evaluator.summarize_final_reserve(episode, **kwargs)
        with self.assertRaises(evaluator.ContaminatedFinalReserveInput):
            evaluator.final_reserve_as_evolutionary_fitness({"return": 1})

    def test_fresh_state_initialization_is_independent(self):
        first = evaluator.fresh_episode_state()
        first.portfolio.cash_cents = 1
        first.portfolio.positions["SPY"] = {
            "shares": 1, "cost_basis_cents": 1
        }
        first.portfolio.peak_equity_cents = 2
        first.portfolio.halted = True
        second = evaluator.fresh_episode_state()
        self.assertEqual(second.portfolio.cash_cents, 100_000_000)
        self.assertEqual(second.portfolio.positions, {})
        self.assertEqual(second.portfolio.peak_equity_cents, 100_000_000)
        self.assertFalse(second.portfolio.halted)
        self.assertIsNone(second.pending)

    def test_selector_contains_only_exact_warmup_and_reserve(self):
        rows = [
            source_row("2024-12-30", "1"),
            source_row("2025-12-29", "2"),
            source_row("2025-12-30", "3"),
            source_row("2025-12-31", "4"),
            source_row("2026-01-02", "5"),
            source_row("2026-08-25", "6"),
            source_row("2026-08-26", "SENTINEL"),
        ]
        selected, warmup = select_authorized_rows(rows, 2)
        self.assertEqual(warmup, 2)
        self.assertEqual(
            [row["timestamp"] for row in selected],
            ["2025-12-30", "2025-12-31", "2026-01-02", "2026-08-25"],
        )
        self.assertNotIn(b"SENTINEL", csv_bytes(selected))

    def test_performance_free_champion_snapshot_is_exact(self):
        snapshot = authenticate_champion_without_performance()
        content = snapshot["content"]
        self.assertEqual(content["performance_fields_included"], [])
        self.assertEqual(
            content["champion"]["genome_id"], config.ACCEPTED_CHAMPION_ID
        )
        self.assertEqual(
            content["champion"]["required_pre_reserve_bars"], 295
        )
        self.assertEqual(
            snapshot["snapshot_hash"],
            config.content_hash("s5d_champion_", content),
        )

    def test_forged_protocol_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / config.EPISODE_PATH.name
            forged = {
                "content": dict(
                    config.EPISODE_PROTOCOL, lane_end="2026-08-26"
                ),
                "manifest_hash": config.EPISODE_HASH,
            }
            path.write_text(json.dumps(forged))
            with self.assertRaises(config.S5DProtocolError):
                config.validate_protocol_file(
                    path,
                    config.EPISODE_PROTOCOL,
                    config.EPISODE_HASH,
                    "s5d_final_reserve_episode_",
                )

    def test_deterministic_result_digest_ignores_only_verifier_counter(self):
        base = {
            "genome_id": config.ACCEPTED_CHAMPION_ID,
            "episode_metric": synthetic_episode(),
            "outcome": evaluator.summarize_final_reserve(synthetic_episode()),
        }
        verified = copy.deepcopy(base)
        verified["episode_metric"]["verifier_checks"] = 99
        self.assertEqual(
            final_reserve_result_digest(base),
            final_reserve_result_digest(verified),
        )
        changed = copy.deepcopy(base)
        changed["episode_metric"]["total_return"] = 0.5
        self.assertNotEqual(
            final_reserve_result_digest(base),
            final_reserve_result_digest(changed),
        )

    def test_fixture_runner_persists_verification_and_deterministic_artifact(self):
        episode = synthetic_episode()
        outcome = evaluator.summarize_final_reserve(episode)
        main = {
            "agent_id": "agent_fixture",
            "genome_id": config.ACCEPTED_CHAMPION_ID,
            "episode_metric": episode,
            "outcome": outcome,
            "metrics_hash": "fixture_metrics_hash",
        }
        verified = copy.deepcopy(main)
        verified["episode_metric"]["verifier_checks"] = 9
        lock = {
            "final_reserve_bundle_revision": "fixture_bundle",
            "final_reserve_bundle_manifest_hash": "fixture_manifest",
            "champion_snapshot_hash": "fixture_snapshot",
        }
        champion = SimpleNamespace(
            agent_id="agent_fixture",
            genome_id=config.ACCEPTED_CHAMPION_ID,
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fixture.db"
            report_path = Path(tmp) / "fixture.json"
            with (
                mock.patch.object(
                    runner, "get_code_revision",
                    return_value=("0" * 40, False),
                ),
                mock.patch.object(
                    runner, "load_preparation_lock", return_value=lock,
                ),
                mock.patch.object(
                    runner, "load_frozen_champion", return_value=champion,
                ),
                mock.patch.object(
                    runner, "load_authorized_final_reserve_bundle",
                    side_effect=[object(), object()],
                ),
                mock.patch.object(runner, "assert_isolated", return_value={}),
                mock.patch.object(
                    runner, "evaluate_champion",
                    side_effect=[main, verified],
                ),
            ):
                report = runner.run_final_reserve(db_path, report_path)
            self.assertEqual(
                report["independent_verifier"]["status"], "agreed"
            )
            self.assertTrue(db_path.exists())
            self.assertEqual(json.loads(report_path.read_text()), report)
            self.assertEqual(
                report["champion_result"]["genome_id"],
                config.ACCEPTED_CHAMPION_ID,
            )

    def test_execution_entrypoint_refuses_without_future_token(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "s5d_final_reserve.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing to execute FINAL RESERVE", completed.stderr)


class S5DPreparedArtifactTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not config.LOCK_PATH.exists():
            raise unittest.SkipTest("S5D final reserve bundle not constructed yet")
        cls.lock = config.load_preparation_lock()
        cls.bundle = load_authorized_final_reserve_bundle()
        cls.champion = load_frozen_champion()

    def test_protocol_bundle_snapshot_and_manifest_are_content_addressed(self):
        config.validate_protocols_on_disk()
        self.assertIn(config.EPISODE_HASH, config.EPISODE_PATH.name)
        self.assertIn(config.OUTCOME_HASH, config.OUTCOME_PATH.name)
        self.assertIn(
            self.lock["champion_snapshot_hash"],
            self.lock["champion_snapshot_path"],
        )
        self.assertIn(
            self.lock["final_reserve_bundle_revision"],
            self.lock["final_reserve_bundle_path"],
        )

    def test_champion_genome_is_exact_and_immutable(self):
        self.assertEqual(self.champion.genome_id, config.ACCEPTED_CHAMPION_ID)
        self.assertEqual(
            self.champion.genome_id,
            "gen_" + hashlib.sha256(
                canonical_json(self.champion.genome).encode()
            ).hexdigest(),
        )
        original = self.champion.genome
        changed = self.champion.genome
        changed["target_max_exposure"] = 0.01
        self.assertEqual(self.champion.genome, original)
        self.assertNotEqual(self.champion.genome, changed)

    def test_warmup_is_minimum_mechanical_champion_requirement(self):
        policy = self.lock["warmup_policy"]
        self.assertEqual(policy["champion_id"], config.ACCEPTED_CHAMPION_ID)
        self.assertEqual(policy["required_price_bars_including_current"], 296)
        self.assertEqual(policy["pre_reserve_bars_per_asset"], 295)
        self.assertEqual(policy["fabrication_backfill_interpolation"], "PROHIBITED")
        self.assertEqual(policy["shortened_indicators"], "PROHIBITED")

    def test_evaluator_has_fixed_path_and_exact_date_boundaries(self):
        self.assertEqual(
            len(inspect.signature(
                load_authorized_final_reserve_bundle
            ).parameters),
            0,
        )
        isolation = assert_isolated(self.bundle)
        self.assertEqual(
            isolation["evaluator_visible_max_date"], "2026-08-25"
        )
        self.assertEqual(
            isolation["observations_after_2026_08_25_available_to_evaluator"],
            0,
        )
        for rows in self.bundle.per_symbol_rows.values():
            self.assertTrue(all(row["timestamp"] <= "2026-08-25" for row in rows))
            self.assertEqual(
                sum(row["timestamp"] < "2026-01-01" for row in rows), 295
            )

    def test_post_reserve_exposure_instrumentation_fails_closed(self):
        self.bundle.exposure_audit.record("2026-08-26")
        with self.assertRaises(DatasetVerificationError):
            assert_isolated(self.bundle)
        self.bundle.exposure_audit.counts["post_reserve_observations_exposed"] = 0

    def test_forged_bundle_and_champion_identities_are_rejected(self):
        source = ROOT / self.lock["final_reserve_bundle_path"]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / self.lock["final_reserve_bundle_revision"]
            shutil.copytree(source, target)
            manifest = target / (
                f"manifest_{self.lock['final_reserve_bundle_revision']}.json"
            )
            envelope = json.loads(manifest.read_text())
            envelope["content"]["champion_id"] = "gen_forged"
            forged_hash = "s5d_reserve_manifest_" + hashlib.sha256(
                canonical_json(envelope["content"]).encode()
            ).hexdigest()
            envelope["manifest_hash"] = forged_hash
            manifest.write_text(json.dumps(envelope))
            with self.assertRaises(DatasetVerificationError):
                _load_bundle_directory(
                    target,
                    self.lock["final_reserve_bundle_revision"],
                    forged_hash,
                )

    def test_preparation_has_no_real_execution_or_result_artifact(self):
        keys = (
            "final_reserve_champion_executions_during_preparation",
            "real_final_reserve_result_rows",
            "genome_mutations",
            "breeding_or_retraining",
            "evolutionary_feedback",
            "broker_connections",
            "real_orders",
            "live_market_feeds",
            "public_endpoints",
            "alpaca_access_configuration_authentication",
            "external_system_access_or_mutation",
        )
        self.assertTrue(all(self.lock[key] == 0 for key in keys))
        self.assertEqual(
            list((ROOT / "reports").glob("s5d_final_reserve_*_result.json")),
            [],
        )
        self.assertEqual(
            list((ROOT / "db").glob("s5d_final_reserve_*.db")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
