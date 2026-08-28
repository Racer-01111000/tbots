import hashlib
import inspect
import json
import shutil
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
from s5b_build_qualification_bundle import (
    csv_bytes,
    select_authorized_rows,
)
import s5b_config as config
import s5b_evaluator as evaluator
from s5b_frozen import load_frozen_top10
from s5b_qualification import qualification_result_digest
from s5b_qualification_bundle import (
    _load_bundle_directory,
    assert_isolated,
    load_authorized_qualification_bundle,
)


def episode(index, total_return=0.01, sharpe=0.2, max_drawdown=-0.03,
            halted=False, turnover=1.0, cost_rate=0.001):
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


class S5BFrozenProtocolTestCase(unittest.TestCase):
    def test_four_calendar_year_episodes_and_fresh_state_are_frozen(self):
        episodes = config.EPISODE_PROTOCOL["episodes"]
        self.assertEqual(episodes, [
            {"episode_index": 0, "start_date": "2019-01-01", "end_date": "2019-12-31"},
            {"episode_index": 1, "start_date": "2020-01-01", "end_date": "2020-12-31"},
            {"episode_index": 2, "start_date": "2021-01-01", "end_date": "2021-12-31"},
            {"episode_index": 3, "start_date": "2022-01-01", "end_date": "2022-12-31"},
        ])
        self.assertEqual(config.EPISODE_PROTOCOL["starting_cash_cents"], 100_000_000)
        self.assertEqual(
            set(config.EPISODE_PROTOCOL["fresh_state_each_episode"]),
            {"cash", "portfolio", "peak_equity", "drawdown", "halt"},
        )
        self.assertEqual(config.EPISODE_PROTOCOL["drawdown_halt_scope"], "episode_only")
        self.assertFalse(config.EPISODE_PROTOCOL["evolutionary_feedback_allowed"])

    def test_score_is_exactly_the_frozen_s5a_equation_on_four_episodes(self):
        rows = [
            episode(0, -0.03, -0.2, -0.12, True, 1.2, 0.0014),
            episode(1, 0.01, 0.1, -0.05, False, 0.8, 0.0008),
            episode(2, 0.02, 0.3, -0.04, False, 1.0, 0.0010),
            episode(3, 0.04, 0.5, -0.03, False, 0.6, 0.0006),
        ]
        result = evaluator.compute_qualification_score(rows)
        weights = config.SCORE_PROTOCOL["weights"]
        expected = (
            weights["median_episode_return"] * result["median_episode_return"]
            + weights["median_episode_sharpe"] * result["median_episode_sharpe"]
            + weights["absolute_worst_drawdown"] * abs(result["worst_drawdown"])
            + weights["consistency_score"] * result["consistency_score"]
            + weights["halt_rate"] * result["halt_rate"]
            + weights["median_episode_turnover"] * result["median_episode_turnover"]
            + weights["median_transaction_cost_rate"] * result["median_transaction_cost_rate"]
            + weights["performance_concentration"] * result["performance_concentration"]
        )
        self.assertEqual(result["qualification_score"], round(expected, 12))
        self.assertEqual(config.SCORE_PROTOCOL["score_round_decimal_places"], 12)

    def test_historical_performance_and_feedback_are_rejected(self):
        rows = [episode(index) for index in range(4)]
        with self.assertRaises(evaluator.ContaminatedQualificationInput):
            evaluator.compute_qualification_score(
                rows, historical_s5a_performance={"fitness": 999}
            )
        with self.assertRaises(evaluator.ContaminatedQualificationInput):
            evaluator.compute_qualification_score(
                rows, evolutionary_feedback={"winner": "forged"}
            )
        with self.assertRaises(evaluator.ContaminatedQualificationInput):
            evaluator.qualification_as_evolutionary_fitness({"score": 1})

    def test_rank_waits_for_ten_and_uses_genome_id_for_ties(self):
        def result(index):
            return {
                "genome_id": f"gen_{10-index:02d}",
                "episode_metrics": [episode(i) for i in range(4)],
                "aggregate": {"qualification_score": 1.0},
            }
        with self.assertRaises(ValueError):
            evaluator.rank_completed_results([result(0)])
        ranked = evaluator.rank_completed_results([result(i) for i in range(10)])
        self.assertEqual(
            [row["genome_id"] for row in ranked],
            sorted(row["genome_id"] for row in ranked),
        )

    def test_every_episode_gets_independent_fresh_state(self):
        first = evaluator.fresh_episode_state()
        first.portfolio.cash_cents = 1
        first.portfolio.positions["SPY"] = {"shares": 1, "cost_basis_cents": 1}
        first.portfolio.halted = True
        second = evaluator.fresh_episode_state()
        self.assertEqual(second.portfolio.cash_cents, 100_000_000)
        self.assertEqual(second.portfolio.positions, {})
        self.assertEqual(second.portfolio.peak_equity_cents, 100_000_000)
        self.assertFalse(second.portfolio.halted)
        self.assertIsNone(second.pending)

    def test_trusted_selector_never_serializes_post_2022_rows(self):
        base = [row("2018-12-31", "99"), row("2019-01-02"), row("2022-12-30")]
        selected_a, _ = select_authorized_rows([*base, row("2023-01-03", "500")], 1)
        selected_b, _ = select_authorized_rows([*base, row("2026-01-02", "SENTINEL")], 1)
        self.assertEqual(csv_bytes(selected_a), csv_bytes(selected_b))
        self.assertLessEqual(max(r["timestamp"] for r in selected_a), "2022-12-31")

    def test_verifier_digest_ignores_only_verifier_counter(self):
        base = {
            "genome_id": "gen_test",
            "episode_metrics": [dict(episode(i), verifier_checks=0) for i in range(4)],
            "aggregate": {"qualification_score": 0.1},
        }
        verified = json.loads(json.dumps(base))
        for row_ in verified["episode_metrics"]:
            row_["verifier_checks"] = 99
        self.assertEqual(
            qualification_result_digest(base),
            qualification_result_digest(verified),
        )


class S5BPreparedArtifactTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = config.load_preparation_lock()
        cls.bundle = load_authorized_qualification_bundle()
        cls.frozen = load_frozen_top10()

    def test_protocol_and_artifact_hashes_are_content_addressed(self):
        config.validate_protocols_on_disk()
        self.assertIn(config.EPISODE_HASH, config.EPISODE_PATH.name)
        self.assertIn(config.SCORE_HASH, config.SCORE_PATH.name)
        self.assertEqual(len(self.frozen), 10)

    def test_frozen_genomes_cannot_be_mutated_through_snapshot(self):
        original = self.frozen[0].genome
        changed = self.frozen[0].genome
        changed["target_max_exposure"] = 0.01
        self.assertEqual(self.frozen[0].genome, original)
        self.assertNotEqual(self.frozen[0].genome, changed)

    def test_exact_warmup_is_derived_from_actual_frozen_ten(self):
        expected = {
            "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155": 295,
            "gen_2a6d4d628da2ecb2b3aa84faf53d8fc19ad257d016087f1400c31a68dee9b23f": 295,
            "gen_3e06a2be7814b193c9da1725ea559f8154bbce006a6ab227a72f18aa5c7eb30f": 305,
            "gen_bbe33d2b6208f6a28660b5a3750bf52863379e28c51d3b8301cf6a235854a1be": 305,
            "gen_6452b74749c53e80b7bb6d0a5506e41760c4d17ead60b80c0c9b09c4bbddb422": 317,
            "gen_908f66b505b9965160443a410c4538d81a7cb54ddf10c2c6cbe04e2ef5e8bd1c": 317,
            "gen_9f8874becbe730e95d4dc2fa02333999f6dad00a87b85514305feae3aeb64137": 317,
            "gen_a8a5c9f1078a8e446bd3c55b6dc5d201b016c78d7f67ac2e047c76355936bf70": 317,
            "gen_c139ea058d3691b6385b21be93a644ed2e28223fb09125f589378de4e23fc891": 317,
            "gen_cb089956d968ecaa7b5e7f8c0e113af28b00b042e13c23028cd72159e27cfe57": 317,
        }
        actual = {
            row.genome_id: config.required_price_bars(row.genome) - 1
            for row in self.frozen
        }
        self.assertEqual(actual, expected)

    def test_evaluator_visible_bundle_has_no_post_2022_observations(self):
        self.assertEqual(len(inspect.signature(load_authorized_qualification_bundle).parameters), 0)
        self.assertLessEqual(self.bundle.calendar[-1], "2022-12-31")
        for rows in self.bundle.per_symbol_rows.values():
            self.assertTrue(all(row_["timestamp"] <= "2022-12-31" for row_ in rows))
        isolation = assert_isolated(self.bundle)
        self.assertEqual(
            isolation["observations_after_2022_12_31_available_to_evaluator"], 0
        )

    def test_sealed_exposure_instrumentation_fails_closed_without_opening_data(self):
        self.bundle.exposure_audit.record("2023-01-03")
        with self.assertRaises(DatasetVerificationError):
            assert_isolated(self.bundle)
        self.bundle.exposure_audit.counts["championship_observations_exposed"] = 0
        self.bundle.exposure_audit.record("2026-01-02")
        with self.assertRaises(DatasetVerificationError):
            assert_isolated(self.bundle)
        self.bundle.exposure_audit.counts["final_reserve_observations_exposed"] = 0

    def test_forged_bundle_and_lane_identity_are_rejected(self):
        source = ROOT / self.lock["qualification_bundle_path"]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / self.lock["qualification_bundle_revision"]
            shutil.copytree(source, target)
            manifest = target / f"manifest_{self.lock['qualification_bundle_revision']}.json"
            envelope = json.loads(manifest.read_text())
            envelope["content"]["authorized_lane_manifest_hash"] = "lane_forged"
            forged_hash = "s5b_qual_manifest_" + hashlib.sha256(
                canonical_json(envelope["content"]).encode()
            ).hexdigest()
            envelope["manifest_hash"] = forged_hash
            manifest.write_text(json.dumps(envelope))
            with self.assertRaises(DatasetVerificationError):
                _load_bundle_directory(
                    target,
                    self.lock["qualification_bundle_revision"],
                    forged_hash,
                )


if __name__ == "__main__":
    unittest.main()
