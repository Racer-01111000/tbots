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

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib.ids import canonical_json
from lib.replay import DatasetVerificationError
from s5c_build_championship_bundle import (
    _parse_bounded_source_row, csv_bytes, select_authorized_rows,
)
from s5c_championship_bundle import (
    _load_bundle_directory,
    assert_isolated,
    load_authorized_championship_bundle,
)
import s5c_config as config
import s5c_evaluator as evaluator
from s5c_finalists import load_frozen_finalists


def episode(
    index,
    total_return=0.01,
    sharpe=0.2,
    max_drawdown=-0.03,
    halted=False,
    turnover=1.0,
    cost_rate=0.001,
):
    return {
        "episode_index": index,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "halted": halted,
        "turnover": turnover,
        "transaction_cost_rate": cost_rate,
        "commission_cents": 50_000,
        "slippage_cents": 50_000,
    }


def result(genome_id, score):
    return {
        "selection_order": 1,
        "agent_id": "agent_fixture",
        "genome_id": genome_id,
        "episode_metrics": [episode(index) for index in range(3)],
        "aggregate": {"championship_score": score},
    }


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


class S5CProtocolTestCase(unittest.TestCase):
    def test_three_independent_calendar_year_episodes_are_frozen(self):
        self.assertEqual(config.EPISODE_PROTOCOL["episodes"], [
            {"episode_index": 0, "start_date": "2023-01-01", "end_date": "2023-12-31"},
            {"episode_index": 1, "start_date": "2024-01-01", "end_date": "2024-12-31"},
            {"episode_index": 2, "start_date": "2025-01-01", "end_date": "2025-12-31"},
        ])
        self.assertEqual(config.EPISODE_PROTOCOL["starting_cash_cents"], 100_000_000)
        self.assertEqual(
            set(config.EPISODE_PROTOCOL["fresh_state_each_episode"]),
            {"cash", "portfolio", "peak_equity", "drawdown", "halt"},
        )
        self.assertEqual(config.EPISODE_PROTOCOL["drawdown_halt_scope"], "episode_only")
        self.assertFalse(config.EPISODE_PROTOCOL["genome_mutation_allowed"])
        self.assertFalse(config.EPISODE_PROTOCOL["evolutionary_feedback_allowed"])

    def test_score_is_exact_frozen_s5a_s5b_equation_rounded_12(self):
        rows = [
            episode(0, -0.03, -0.2, -0.12, True, 1.2, 0.0014),
            episode(1, 0.01, 0.1, -0.05, False, 0.8, 0.0008),
            episode(2, 0.04, 0.5, -0.03, False, 0.6, 0.0006),
        ]
        scored = evaluator.compute_championship_score(rows)
        weights = config.SCORE_PROTOCOL["weights"]
        expected = (
            weights["median_episode_return"] * scored["median_episode_return"]
            + weights["median_episode_sharpe"] * scored["median_episode_sharpe"]
            + weights["absolute_worst_drawdown"] * abs(scored["worst_drawdown"])
            + weights["consistency_score"] * scored["consistency_score"]
            + weights["halt_rate"] * scored["halt_rate"]
            + weights["median_episode_turnover"] * scored["median_episode_turnover"]
            + weights["median_transaction_cost_rate"]
            * scored["median_transaction_cost_rate"]
            + weights["performance_concentration"]
            * scored["performance_concentration"]
        )
        self.assertEqual(scored["championship_score"], round(expected, 12))
        self.assertEqual(config.SCORE_PROTOCOL["score_round_decimal_places"], 12)

    def test_prior_performance_reserve_and_feedback_are_rejected(self):
        rows = [episode(index) for index in range(3)]
        forbidden = (
            {"historical_s5a_performance": {"fitness": 1}},
            {"historical_s5b_performance": {"score": 1}},
            {"evolutionary_feedback": {"winner": "forged"}},
            {"final_reserve_information": {"return": 1}},
        )
        for kwargs in forbidden:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(evaluator.ContaminatedChampionshipInput):
                    evaluator.compute_championship_score(rows, **kwargs)
        with self.assertRaises(evaluator.ContaminatedChampionshipInput):
            evaluator.championship_as_evolutionary_fitness({"score": 1})

    def test_ranking_waits_for_all_three_and_tie_breaks_by_genome_id(self):
        ids = list(config.FINALIST_IDS)
        with self.assertRaises(ValueError):
            evaluator.rank_completed_results([result(ids[0], 1.0)])
        rows = [result(identity, 1.0) for identity in reversed(ids)]
        ranked = evaluator.rank_completed_results(rows)
        self.assertEqual(
            [row_["genome_id"] for row_ in ranked], sorted(ids)
        )

    def test_winner_rule_is_deterministic_rank_one_without_override(self):
        ids = list(config.FINALIST_IDS)
        rows = [
            result(ids[0], 0.1),
            result(ids[1], 0.3),
            result(ids[2], 0.2),
        ]
        original = copy.deepcopy(rows)
        winner_a, ranked_a = evaluator.select_winner(rows)
        winner_b, ranked_b = evaluator.select_winner(list(reversed(rows)))
        self.assertEqual(winner_a["genome_id"], ids[1])
        self.assertEqual(
            canonical_json(ranked_a), canonical_json(ranked_b)
        )
        self.assertEqual(rows, original)
        with self.assertRaises(evaluator.ContaminatedChampionshipInput):
            evaluator.select_winner(rows, discretionary_override=ids[0])
        with self.assertRaises(evaluator.ContaminatedChampionshipInput):
            evaluator.select_winner(rows, dropped_episode=0)
        with self.assertRaises(evaluator.ContaminatedChampionshipInput):
            evaluator.select_winner(rows, post_hoc_weights={"return": 99})
        with self.assertRaises(evaluator.ContaminatedChampionshipInput):
            evaluator.select_winner(rows, final_reserve_information={"x": 1})

    def test_every_episode_gets_independent_fresh_state(self):
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

    def test_trusted_selector_physically_excludes_2026_and_later(self):
        base = [
            row("2022-12-30", "99"),
            row("2023-01-03"),
            row("2025-12-31"),
        ]
        selected_a, _ = select_authorized_rows(
            [*base, row("2026-01-02", "SENTINEL")], 1
        )
        selected_b, _ = select_authorized_rows(
            [*base, row("2027-01-04", "DIFFERENT")], 1
        )
        self.assertEqual(csv_bytes(selected_a), csv_bytes(selected_b))
        self.assertEqual(
            max(item["timestamp"] for item in selected_a), "2025-12-31"
        )

    def test_constructor_never_uses_full_dataset_loader_or_reads_after_final_session(self):
        source = (SCRIPTS / "s5c_build_championship_bundle.py").read_text()
        self.assertNotIn("load_and_verify_dataset", source)
        self.assertIn('path.open("rb", buffering=0)', source)
        self.assertIn(
            "while not reached_final_session:",
            source,
        )
        self.assertIn("timestamp == EXPECTED_FINAL_TRADING_SESSION", source)

    def test_bounded_constructor_projects_canonical_provenance_schema(self):
        raw = (
            b"SPY,2025-12-31,100,101,99,100,100,1000,,"
            b"yahoo,2025-12-31,2026-08-26T00:00:00Z\n"
        )
        projected = _parse_bounded_source_row(raw, "SPY")
        self.assertEqual(projected["timestamp"], "2025-12-31")
        self.assertEqual(set(projected), {
            "timestamp", "open", "high", "low", "close",
            "adjusted_close", "volume", "corporate_action",
        })

    def test_forged_advancement_and_protocol_identities_are_rejected(self):
        envelope = json.loads(config.ADVANCEMENT_MANIFEST_PATH.read_text())
        forged = copy.deepcopy(envelope)
        forged["content"]["selected_finalists"][0]["genome_id"] = "gen_forged"
        forged["manifest_hash"] = config.content_hash(
            "s5c_advancement_manifest_", forged["content"]
        )
        with self.assertRaises(config.S5CProtocolError):
            config.validate_advancement_envelope(forged)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / config.EPISODE_PATH.name
            protocol = {
                "content": dict(config.EPISODE_PROTOCOL, lane_end="2026-12-31"),
                "manifest_hash": config.EPISODE_HASH,
            }
            path.write_text(json.dumps(protocol))
            with self.assertRaises(config.S5CProtocolError):
                config.validate_protocol_file(
                    path,
                    config.EPISODE_PROTOCOL,
                    config.EPISODE_HASH,
                    "s5c_championship_episode_",
                )

    def test_execution_entrypoint_refuses_without_future_token(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "s5c_championship.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing to execute CHAMPIONSHIP", completed.stderr)


class S5CPreparedArtifactTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not config.LOCK_PATH.exists():
            raise unittest.SkipTest("S5C prepared bundle not constructed yet")
        cls.lock = config.load_preparation_lock()
        cls.bundle = load_authorized_championship_bundle()
        cls.finalists = load_frozen_finalists()

    def test_protocol_bundle_and_manifest_are_content_addressed(self):
        config.validate_protocols_on_disk()
        self.assertIn(config.EPISODE_HASH, config.EPISODE_PATH.name)
        self.assertIn(config.SCORE_HASH, config.SCORE_PATH.name)
        self.assertIn(
            self.lock["championship_bundle_revision"],
            self.lock["championship_bundle_path"],
        )
        self.assertEqual(len(self.finalists), 3)

    def test_finalist_identities_and_genomes_are_reverified_immutable(self):
        self.assertEqual(
            tuple(row_.genome_id for row_ in self.finalists),
            config.FINALIST_IDS,
        )
        for finalist in self.finalists:
            self.assertEqual(
                finalist.genome_id,
                "gen_" + hashlib.sha256(
                    canonical_json(finalist.genome).encode()
                ).hexdigest(),
            )
        original = self.finalists[0].genome
        changed = self.finalists[0].genome
        changed["target_max_exposure"] = 0.01
        self.assertEqual(self.finalists[0].genome, original)
        self.assertNotEqual(self.finalists[0].genome, changed)

    def test_exact_warmup_is_derived_from_actual_three_finalists(self):
        expected = {
            config.FINALIST_IDS[0]: 305,
            config.FINALIST_IDS[1]: 295,
            config.FINALIST_IDS[2]: 317,
        }
        self.assertEqual(
            self.lock["warmup_policy"][
                "per_genome_required_prechampionship_bars"
            ],
            expected,
        )
        self.assertEqual(
            self.lock["warmup_policy"][
                "shared_bundle_prechampionship_bars_per_asset"
            ],
            317,
        )
        self.assertEqual(
            self.lock["warmup_policy"]["fabrication_backfill_interpolation"],
            "PROHIBITED",
        )
        self.assertEqual(
            self.lock["warmup_policy"]["shortened_indicators"], "PROHIBITED"
        )

    def test_evaluator_has_fixed_path_and_final_2025_maximum(self):
        self.assertEqual(
            len(inspect.signature(
                load_authorized_championship_bundle
            ).parameters),
            0,
        )
        isolation = assert_isolated(self.bundle)
        self.assertEqual(
            isolation["evaluator_visible_max_date"],
            config.EXPECTED_FINAL_TRADING_SESSION,
        )
        self.assertEqual(
            isolation[
                "observations_after_2025_12_31_available_to_evaluator"
            ],
            0,
        )
        self.assertEqual(
            isolation["final_reserve_observations_available_to_evaluator"], 0
        )
        for rows in self.bundle.per_symbol_rows.values():
            self.assertTrue(
                all(row_["timestamp"] < "2026-01-01" for row_ in rows)
            )

    def test_final_reserve_exposure_instrumentation_fails_closed(self):
        self.bundle.exposure_audit.record("2026-01-02")
        with self.assertRaises(DatasetVerificationError):
            assert_isolated(self.bundle)
        self.bundle.exposure_audit.counts[
            "final_reserve_observations_exposed"
        ] = 0

    def test_forged_bundle_identity_is_rejected(self):
        source = ROOT / self.lock["championship_bundle_path"]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / self.lock["championship_bundle_revision"]
            shutil.copytree(source, target)
            manifest = target / (
                f"manifest_{self.lock['championship_bundle_revision']}.json"
            )
            envelope = json.loads(manifest.read_text())
            envelope["content"]["advancement_manifest_hash"] = "forged"
            forged_hash = "s5c_champ_manifest_" + hashlib.sha256(
                canonical_json(envelope["content"]).encode()
            ).hexdigest()
            envelope["manifest_hash"] = forged_hash
            manifest.write_text(json.dumps(envelope))
            with self.assertRaises(DatasetVerificationError):
                _load_bundle_directory(
                    target,
                    self.lock["championship_bundle_revision"],
                    forged_hash,
                )

    def test_preparation_conservation_is_zero(self):
        keys = (
            "championship_organisms_executed_during_preparation",
            "championship_result_rows_during_preparation",
            "final_reserve_observations_accessed_or_exposed",
            "genome_mutations",
            "breeding_or_retraining",
            "broker_connections",
            "real_orders",
            "live_market_feeds",
            "public_endpoints",
            "alpaca_access_configuration_authentication",
            "external_system_access_or_mutation",
        )
        self.assertTrue(all(self.lock[key] == 0 for key in keys))


if __name__ == "__main__":
    unittest.main()
