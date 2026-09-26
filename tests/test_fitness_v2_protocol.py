"""Fixtures-and-algebra tests for scripts/fitness_v2_protocol.py (GO §4.2).

Never reads data/normalized/*.csv or any market data. Every world below is a
small, hand-constructed fixture built only to exercise §3's rules and their
reject paths deterministically.
"""
import math
import random
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fitness_v2_protocol as proto
from fitness_v2 import FitnessV2Error


def flat_world(n_sessions, base=100.0, seed=1, vol=0.01, drift=0.0002):
    """A minimal valid eight-asset synchronized world: independent per-asset
    geometric random walks, no corporate actions, strictly positive prices."""
    rng = random.Random(seed)
    world = {"assets": {}}
    for offset, symbol in enumerate(proto.ASSET_UNIVERSE):
        local_rng = random.Random(f"{seed}-{offset}")
        price = base
        adjusted = [price]
        raw_open = [price]
        raw_close = [price]
        for _ in range(n_sessions - 1):
            r = local_rng.gauss(drift, vol)
            price = price * (1 + r)
            adjusted.append(price)
            raw_open.append(price * (1 - 0.0003))
            raw_close.append(price)
        world["assets"][symbol] = {
            "adjusted_close": adjusted,
            "raw_open": raw_open,
            "raw_close": raw_close,
            "corporate_action": [None] * n_sessions,
        }
    return world


def pooled_tau(worlds):
    gaps = []
    for world in worlds:
        for symbol in proto.ASSET_UNIVERSE:
            rows = world["assets"][symbol]
            n = len(rows["raw_close"])
            for i in range(1, n):
                gaps.append(rows["raw_open"][i] / rows["raw_close"][i - 1] - 1.0)
    return proto.gap_threshold_tau(gaps)


class SampleStdev(unittest.TestCase):
    def test_matches_known_value(self):
        self.assertAlmostEqual(proto.sample_stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]), 2.138089935)

    def test_rejects_fewer_than_two_observations(self):
        with self.assertRaises(FitnessV2Error):
            proto.sample_stdev([1.0])
        with self.assertRaises(FitnessV2Error):
            proto.sample_stdev([])


class AdjustedSimpleReturns(unittest.TestCase):
    def test_matches_known_ratios(self):
        result = proto.adjusted_simple_returns([100.0, 110.0, 99.0])
        self.assertAlmostEqual(result[0], 0.10)
        self.assertAlmostEqual(result[1], -0.10)

    def test_rejects_nonpositive_price(self):
        with self.assertRaises(FitnessV2Error):
            proto.adjusted_simple_returns([100.0, 0.0, 99.0])

    def test_rejects_nonfinite_price(self):
        with self.assertRaises(FitnessV2Error):
            proto.adjusted_simple_returns([100.0, math.inf])


class EqualWeightPortfolio(unittest.TestCase):
    def test_daily_rebalanced_compounding(self):
        matrix = [[0.10, 0.10, -0.02, -0.02, 0.0, 0.0, 0.0, 0.0]]
        returns = proto.equal_weight_portfolio_returns(matrix)
        self.assertAlmostEqual(returns[0], 0.02)
        equity = proto.equal_weight_portfolio_equity(matrix)
        self.assertEqual(equity, [1.0, 1.02])

    def test_rejects_incomplete_row(self):
        with self.assertRaises(FitnessV2Error):
            proto.equal_weight_portfolio_returns([[0.01, 0.02]])


class CrossSectionalDispersion(unittest.TestCase):
    def test_matches_sample_stdev_per_session(self):
        matrix = [[0.0] * 8, [0.01, -0.01, 0.02, -0.02, 0.0, 0.0, 0.0, 0.0]]
        series = proto.cross_sectional_dispersion_series(matrix)
        self.assertEqual(series[0], 0.0)
        self.assertAlmostEqual(series[1], statistics.stdev(matrix[1]))


class CorrelationComponent(unittest.TestCase):
    def test_perfectly_correlated_assets_give_one(self):
        base = [0.01 * math.sin(i) for i in range(70)]
        matrix = [[v] * 8 for v in base]
        self.assertAlmostEqual(proto.correlation_component(matrix), 1.0)

    def test_rejects_zero_variance_asset(self):
        base = [0.01 * math.sin(i) for i in range(70)]
        matrix = [[v] * 7 + [0.0] for v in base]
        with self.assertRaises(FitnessV2Error):
            proto.correlation_component(matrix)

    def test_rejects_insufficient_sessions(self):
        with self.assertRaises(FitnessV2Error):
            proto.correlation_component([[0.01] * 8] * 10)


class DrawdownDepthDuration(unittest.TestCase):
    def test_known_path(self):
        equity = [1.0, 1.1, 1.0, 0.9, 0.95, 1.05]
        depth, duration = proto.drawdown_depth_duration(equity)
        self.assertAlmostEqual(depth, 1 - 0.9 / 1.1)
        # peak (1.1) is never re-exceeded, so every session after it is
        # underwater: 1.0, 0.9, 0.95, 1.05 -- 4 of the 5 returns.
        self.assertAlmostEqual(duration, 4 / 5)

    def test_run_still_underwater_at_window_end_counts_through_end(self):
        equity = [1.0, 1.2, 1.0, 0.9]
        _, duration = proto.drawdown_depth_duration(equity)
        self.assertAlmostEqual(duration, 2 / 3)

    def test_peak_includes_e0(self):
        equity = [1.0, 0.9, 0.8]
        depth, _ = proto.drawdown_depth_duration(equity)
        self.assertAlmostEqual(depth, 0.2)


class TurnoverComponent(unittest.TestCase):
    def test_lexicographic_tie_break_no_spurious_change(self):
        n = proto.TURNOVER_LOOKBACK + 3
        window = []
        for i in range(n):
            window.append([100.0 + i] * len(proto.ASSET_UNIVERSE))
        # every asset moves identically every session -> momentum always tied
        # across all eight assets -> leader is always the lexicographically
        # smallest symbol -> zero leader changes.
        self.assertEqual(proto.turnover_component(window), 0.0)

    def test_detects_a_real_leader_change(self):
        n = proto.TURNOVER_LOOKBACK + 2
        window = [[100.0] * len(proto.ASSET_UNIVERSE) for _ in range(n)]
        # All eight assets are tied every session except the last, so the
        # lexicographic tie-break makes DBC (alphabetically first) the
        # leader at every post-lookback session but the final one. VNQ
        # (alphabetically last) rallies only at the final session, breaking
        # the tie outright and becoming the new leader -- one real change.
        vnq_index = proto.ASSET_UNIVERSE.index("VNQ")
        window[-1][vnq_index] = 200.0
        turnover = proto.turnover_component(window)
        self.assertGreater(turnover, 0.0)

    def test_rejects_insufficient_sessions(self):
        with self.assertRaises(FitnessV2Error):
            proto.turnover_component([[100.0] * 8] * 10)


class AutocorrelationComponent(unittest.TestCase):
    def test_matches_known_lag1_pearson(self):
        series = [0.01, -0.01, 0.02, -0.02, 0.015, -0.005]
        expected = statistics.correlation(series[:-1], series[1:]) if hasattr(statistics, "correlation") else None
        value = proto.autocorrelation_component(series)
        self.assertTrue(-1.0 <= value <= 1.0)
        if expected is not None:
            self.assertAlmostEqual(value, expected)

    def test_rejects_zero_variance(self):
        with self.assertRaises(FitnessV2Error):
            proto.autocorrelation_component([0.01, 0.01, 0.01, 0.01])


class GapDescriptor(unittest.TestCase):
    def test_percentile_matches_audit_worked_example(self):
        # FITNESS_V2_SCIENTIFIC_REVIEW_STOP_20260926.md row "Gap ... pooled
        # threshold, linear quantile interpolation": values 1..20,
        # nearest-rank 95th = 19, linear interpolation = 19.05.
        values = list(range(1, 21))
        self.assertAlmostEqual(proto._percentile_linear(values, 95.0), 19.05)

    def test_gap_threshold_uses_absolute_values(self):
        gaps = [-0.05, 0.01, 0.02, -0.10, 0.03]
        tau = proto.gap_threshold_tau(gaps)
        self.assertGreaterEqual(tau, 0.0)

    def test_corporate_action_session_excluded(self):
        rows = [
            {"open": 101.0, "prev_close": 100.0, "corporate_action": None},
            {"open": 200.0, "prev_close": 100.0, "corporate_action": {"dividend_amount": 1.0}},
        ]
        gaps = proto.valid_gaps(rows)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0], 0.01)

    def test_nonpositive_price_excluded(self):
        rows = [{"open": -5.0, "prev_close": 100.0, "corporate_action": None}]
        self.assertEqual(proto.valid_gaps(rows), [])

    def test_rejects_zero_exceedances(self):
        rows = [{"open": 100.1, "prev_close": 100.0, "corporate_action": None}] * 5
        with self.assertRaises(FitnessV2Error):
            proto.gap_component(rows, tau=1.0)

    def test_frequency_and_magnitude(self):
        rows = [
            {"open": 105.0, "prev_close": 100.0, "corporate_action": None},  # gap .05, exceeds
            {"open": 100.1, "prev_close": 100.0, "corporate_action": None},  # gap .001, does not
            {"open": 110.0, "prev_close": 100.0, "corporate_action": None},  # gap .10, exceeds
        ]
        frequency, magnitude = proto.gap_component(rows, tau=0.02)
        self.assertAlmostEqual(frequency, 2 / 3)
        self.assertAlmostEqual(magnitude, statistics.median([0.05, 0.10]))


class WorldDescriptorVector(unittest.TestCase):
    def test_produces_all_32_components_and_is_deterministic(self):
        n = proto.DESCRIPTOR_WINDOW + 8
        world = flat_world(n, seed=42)
        tau = pooled_tau([world])
        vector_a = proto.world_descriptor_vector(world, tau)
        vector_b = proto.world_descriptor_vector(world, tau)
        self.assertEqual(set(vector_a), set(proto.COMPONENT_NAMES))
        self.assertEqual(vector_a, vector_b)

    def test_rejects_world_shorter_than_one_window(self):
        world = flat_world(proto.DESCRIPTOR_WINDOW - 1, seed=1)
        with self.assertRaises(FitnessV2Error):
            proto.world_descriptor_vector(world, tau=0.02)

    def test_rejects_desynchronized_assets(self):
        world = flat_world(proto.DESCRIPTOR_WINDOW + 5, seed=1)
        world["assets"][proto.ASSET_UNIVERSE[0]]["adjusted_close"].pop()
        with self.assertRaises(FitnessV2Error):
            proto.world_descriptor_vector(world, tau=0.02)

    def test_rejects_missing_asset(self):
        world = flat_world(proto.DESCRIPTOR_WINDOW + 5, seed=1)
        del world["assets"][proto.ASSET_UNIVERSE[0]]
        with self.assertRaises(FitnessV2Error):
            proto.world_descriptor_vector(world, tau=0.02)


class ReferenceCalibration(unittest.TestCase):
    def test_zero_mad_component_aborts_bank_construction(self):
        n = proto.DESCRIPTOR_WINDOW + 1  # exactly one window: MAD of one value is 0
        world = flat_world(n, seed=7)
        tau = pooled_tau([world])
        with self.assertRaises(FitnessV2Error) as ctx:
            proto.development_reference_calibration([world], tau)
        self.assertIn("zero-MAD", str(ctx.exception))

    def test_pooling_distinct_worlds_yields_median_and_mad(self):
        n = proto.DESCRIPTOR_WINDOW + 6
        world_a = flat_world(n, seed=101, vol=0.01, drift=0.0003)
        world_b = flat_world(n, seed=202, vol=0.011, drift=-0.0002)
        tau = pooled_tau([world_a, world_b])
        reference = proto.development_reference_calibration([world_a, world_b], tau)
        self.assertEqual(set(reference["median"]), set(proto.COMPONENT_NAMES))
        self.assertTrue(all(v > 0 for v in reference["mad"].values()))

    def test_requires_at_least_one_world(self):
        with self.assertRaises(FitnessV2Error):
            proto.development_reference_calibration([], tau=0.02)


class DistanceAdmission(unittest.TestCase):
    def _reference(self):
        n = proto.DESCRIPTOR_WINDOW + 6
        world_a = flat_world(n, seed=301)
        world_b = flat_world(n, seed=302)
        tau = pooled_tau([world_a, world_b])
        reference = proto.development_reference_calibration([world_a, world_b], tau)
        return reference, tau, world_a

    def test_execution_family_is_distance_exempt(self):
        reference, tau, anchor = self._reference()
        anchor_vector = proto.world_descriptor_vector(anchor, tau)
        self.assertTrue(
            proto.world_distance_admission("execution", anchor_vector, [anchor_vector], reference)
        )

    def test_identical_vector_fails_distance_from_itself(self):
        reference, tau, anchor = self._reference()
        anchor_vector = proto.world_descriptor_vector(anchor, tau)
        self.assertFalse(
            proto.world_distance_admission("distributional", anchor_vector, [anchor_vector], reference)
        )


class DistributionalReconstruction(unittest.TestCase):
    def _anchor(self):
        return {
            "raw_open": 99.5, "raw_high": 101.0, "raw_low": 99.0,
            "raw_close": 100.0, "adjusted_close": 50.0,
        }

    def test_bar_zero_matches_anchor_exactly(self):
        result = proto.reconstruct_distributional_path(self._anchor(), [])
        self.assertEqual(result[0]["raw_close"], 100.0)
        self.assertEqual(result[0]["adjusted_close"], 50.0)
        self.assertEqual(result[0]["dividend_cents"], 0.0)

    def test_ratios_are_reapplied_not_absolute_levels(self):
        sample = {
            "raw_open": 202.0, "raw_high": 206.0, "raw_low": 199.0, "raw_close": 204.0,
            "adjusted_close": 51.0, "prev_raw_close": 200.0, "prev_adjusted_close": 50.0,
            "dividend_yield": 0.0,
        }
        result = proto.reconstruct_distributional_path(self._anchor(), [sample])
        self.assertAlmostEqual(result[1]["raw_close"], 100.0 * (204.0 / 200.0))
        self.assertAlmostEqual(result[1]["adjusted_close"], 50.0 * (51.0 / 50.0))

    def test_dividend_uses_yield_times_reconstructed_prior_close(self):
        sample = {
            "raw_open": 200.0, "raw_high": 201.0, "raw_low": 199.0, "raw_close": 200.0,
            "adjusted_close": 50.0, "prev_raw_close": 200.0, "prev_adjusted_close": 50.0,
            "dividend_yield": 0.01,
        }
        result = proto.reconstruct_distributional_path(self._anchor(), [sample])
        self.assertAlmostEqual(result[1]["dividend_cents"], 0.01 * 100.0)

    def test_rejects_invalid_prior_price(self):
        sample = {
            "raw_open": 1.0, "raw_high": 1.0, "raw_low": 1.0, "raw_close": 1.0,
            "adjusted_close": 1.0, "prev_raw_close": 0.0, "prev_adjusted_close": 1.0,
        }
        with self.assertRaises(FitnessV2Error):
            proto.reconstruct_distributional_path(self._anchor(), [sample])

    def test_rejects_invalid_geometry(self):
        sample = {
            "raw_open": 300.0, "raw_high": 100.0, "raw_low": 50.0, "raw_close": 200.0,
            "adjusted_close": 51.0, "prev_raw_close": 200.0, "prev_adjusted_close": 50.0,
        }
        with self.assertRaises(FitnessV2Error):
            proto.reconstruct_distributional_path(self._anchor(), [sample])


class StationaryBootstrap(unittest.TestCase):
    def test_deterministic_given_seed(self):
        a = proto.stationary_bootstrap_indices(random.Random(5), 500, 400)
        b = proto.stationary_bootstrap_indices(random.Random(5), 500, 400)
        self.assertEqual(a, b)

    def test_exact_target_length(self):
        indices = proto.stationary_bootstrap_indices(random.Random(1), 500, 378)
        self.assertEqual(len(indices), 378)

    def test_never_draws_source_index_zero(self):
        indices = proto.stationary_bootstrap_indices(random.Random(3), 100, 500)
        self.assertNotIn(0, indices)

    def test_never_exceeds_source_bound(self):
        indices = proto.stationary_bootstrap_indices(random.Random(9), 50, 500)
        self.assertTrue(all(1 <= i < 50 for i in indices))

    def test_mean_block_length_near_target_statistically(self):
        rng = random.Random(123)
        indices = proto.stationary_bootstrap_indices(rng, 5000, 20000, mean_block_length=21.0)
        blocks = 1
        for i in range(1, len(indices)):
            if indices[i] != indices[i - 1] + 1:
                blocks += 1
        mean_block_length = len(indices) / blocks
        self.assertTrue(15.0 <= mean_block_length <= 28.0)


class SequenceJoins(unittest.TestCase):
    def _rows(self, start_price, n):
        rows = []
        price = start_price
        for i in range(n):
            rows.append({
                "raw_open": price * 0.999, "raw_high": price * 1.01, "raw_low": price * 0.99,
                "raw_close": price, "adjusted_close": price * 0.5, "dividend_cents": 0.0,
            })
            price *= 1.001
        return rows

    def test_zero_join_gap_between_segments(self):
        anchor = {"raw_open": 100.0, "raw_high": 100.5, "raw_low": 99.5,
                   "raw_close": 100.0, "adjusted_close": 50.0}
        seg1 = self._rows(500.0, 63)
        seg2 = self._rows(700.0, 63)
        joined = proto.join_sequence_segments([seg1, seg2], anchor)
        self.assertAlmostEqual(joined[1]["raw_open"], joined[0]["raw_close"])
        self.assertAlmostEqual(joined[64]["raw_open"], joined[63]["raw_close"])

    def test_within_segment_ratios_bit_for_bit_preserved(self):
        anchor = {"raw_open": 100.0, "raw_high": 100.5, "raw_low": 99.5,
                   "raw_close": 100.0, "adjusted_close": 50.0}
        seg = self._rows(500.0, 63)
        joined = proto.join_sequence_segments([seg], anchor)
        original_ratio = seg[5]["raw_close"] / seg[4]["raw_close"]
        joined_ratio = joined[1 + 5]["raw_close"] / joined[1 + 4]["raw_close"]
        self.assertAlmostEqual(original_ratio, joined_ratio)

    def test_start_index_zero_is_eligible(self):
        rng = random.Random(1)
        seen_zero = False
        for _ in range(200):
            starts = proto.draw_sequence_segment_starts(rng, source_length=64, segment_length=63, n_segments=1)
            if starts == [0]:
                seen_zero = True
                break
        self.assertTrue(seen_zero)

    def test_sorted_distinct_no_replacement(self):
        starts = proto.draw_sequence_segment_starts(random.Random(2), source_length=100, segment_length=63, n_segments=5)
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(starts), len(set(starts)))

    def test_rejects_more_segments_than_distinct_starts(self):
        with self.assertRaises(FitnessV2Error):
            proto.draw_sequence_segment_starts(random.Random(1), source_length=63, segment_length=63, n_segments=2)


class ShockEvents(unittest.TestCase):
    def _asset_rows(self, n=10):
        rows = []
        price = 100.0
        for i in range(n):
            rows.append({
                "raw_open": price, "raw_high": price * 1.01, "raw_low": price * 0.99,
                "raw_close": price, "adjusted_close": price * 0.5, "dividend_cents": 0.0,
            })
            price *= 1.002
        return rows

    def test_relocated_gap_matches_donor_exactly(self):
        rows = self._asset_rows()
        donor_gap = 0.08
        proto.apply_shock_event(rows, destination_index=4, donor_gap=donor_gap)
        new_gap = rows[4]["raw_open"] / rows[3]["raw_close"] - 1.0
        self.assertAlmostEqual(new_gap, donor_gap)

    def test_later_gaps_and_returns_unchanged(self):
        rows = self._asset_rows()
        original_gap_5 = rows[5]["raw_open"] / rows[4]["raw_close"] - 1.0
        proto.apply_shock_event(rows, destination_index=4, donor_gap=0.08)
        new_gap_5 = rows[5]["raw_open"] / rows[4]["raw_close"] - 1.0
        self.assertAlmostEqual(original_gap_5, new_gap_5)

    def test_exactly_three_gaps_differ_across_three_events(self):
        rows = self._asset_rows(n=10)
        original_gaps = [rows[i]["raw_open"] / rows[i - 1]["raw_close"] - 1.0 for i in range(1, 10)]
        for destination, donor in ((2, 0.05), (5, -0.06), (8, 0.09)):
            proto.apply_shock_event(rows, destination, donor)
        new_gaps = [rows[i]["raw_open"] / rows[i - 1]["raw_close"] - 1.0 for i in range(1, 10)]
        differing = sum(
            1 for old, new in zip(original_gaps, new_gaps) if abs(old - new) > 1e-9
        )
        self.assertEqual(differing, 3)

    def test_same_asset_composed_events_each_hit_their_own_donor_exactly(self):
        rows = self._asset_rows(n=10)
        proto.apply_shock_event(rows, destination_index=2, donor_gap=0.05)
        proto.apply_shock_event(rows, destination_index=7, donor_gap=-0.06)
        gap_2 = rows[2]["raw_open"] / rows[1]["raw_close"] - 1.0
        gap_7 = rows[7]["raw_open"] / rows[6]["raw_close"] - 1.0
        self.assertAlmostEqual(gap_2, 0.05)
        self.assertAlmostEqual(gap_7, -0.06)

    def test_composition_order_independent_for_overlapping_tails(self):
        rows_a = self._asset_rows(n=10)
        rows_b = self._asset_rows(n=10)
        proto.apply_shock_event(rows_a, 2, 0.05)
        proto.apply_shock_event(rows_a, 7, -0.06)
        proto.apply_shock_event(rows_b, 7, -0.06)
        proto.apply_shock_event(rows_b, 2, 0.05)
        for a, b in zip(rows_a, rows_b):
            self.assertAlmostEqual(a["raw_close"], b["raw_close"])

    def test_rejects_first_session_destination(self):
        rows = self._asset_rows()
        with self.assertRaises(FitnessV2Error):
            proto.apply_shock_event(rows, destination_index=0, donor_gap=0.05)

    def test_draw_shock_events_distinct(self):
        donors = [("SPY", i, 0.05) for i in range(10)]
        destinations = [("GLD", i) for i in range(10)]
        chosen_donors, chosen_dest = proto.draw_shock_events(random.Random(1), donors, destinations)
        self.assertEqual(len(chosen_donors), 3)
        self.assertEqual(len(chosen_dest), 3)
        self.assertEqual(len(set(chosen_dest)), 3)

    def test_extrema_assertion(self):
        proto.assert_within_development_extrema(0.05, (-0.20, 0.20))
        with self.assertRaises(FitnessV2Error):
            proto.assert_within_development_extrema(0.25, (-0.20, 0.20))


class ExecutionSkipMask(unittest.TestCase):
    def test_deterministic_given_seed(self):
        a = proto.execution_skip_mask(random.Random(4), 100)
        b = proto.execution_skip_mask(random.Random(4), 100)
        self.assertEqual(a, b)

    def test_length_matches_sessions(self):
        mask = proto.execution_skip_mask(random.Random(1), 50)
        self.assertEqual(len(mask), 50)


class SourceGuard(unittest.TestCase):
    def test_rejects_pre_development_start(self):
        with self.assertRaises(FitnessV2Error):
            proto.validate_development_source(["2007-02-06", "2007-02-08"])

    def test_rejects_post_development_end(self):
        with self.assertRaises(FitnessV2Error):
            proto.validate_development_source(["2018-12-31", "2019-01-01"])

    def test_accepts_exact_boundary_dates(self):
        proto.validate_development_source([proto.DEVELOPMENT_START, proto.DEVELOPMENT_END])

    def test_module_boundary_matches_go_scope(self):
        self.assertEqual(proto.DEVELOPMENT_START, "2007-02-07")
        self.assertEqual(proto.DEVELOPMENT_END, "2018-12-31")


class CompleteProtocolManifest(unittest.TestCase):
    def _kwargs(self):
        return dict(
            tau=0.02,
            gap_extrema_bounds=(-0.1, 0.1),
            reference={
                "median": {name: 0.1 for name in proto.COMPONENT_NAMES},
                "mad": {name: 0.01 for name in proto.COMPONENT_NAMES},
            },
            h_shape_dates={
                "H1": {"start": "2007-02-07", "end": "2011-02-04", "n_sessions": 1008},
                "H2": {"start": "2011-02-07", "end": "2015-02-06", "n_sessions": 1007},
                "H3": {"start": "2015-02-09", "end": "2018-12-31", "n_sessions": 981},
            },
            h_shape_anchor_rows={"H1": {}, "H2": {}, "H3": {}},
        )

    def test_round_trips_through_validation(self):
        envelope = proto.complete_protocol_manifest(**self._kwargs())
        self.assertEqual(proto.validate_complete_protocol(envelope), envelope["manifest_id"])

    def test_detects_content_tampering(self):
        envelope = proto.complete_protocol_manifest(**self._kwargs())
        tampered = {
            "manifest_id": envelope["manifest_id"],
            "content": {**envelope["content"], "gap_threshold_tau": 0.999},
        }
        with self.assertRaises(FitnessV2Error):
            proto.validate_complete_protocol(tampered)

    def test_requires_ready_flag(self):
        from fitness_v2 import content_identity
        content = proto.build_complete_protocol(**self._kwargs())
        content["complete_protocol_ready"] = False
        manifest_id = content_identity("fitness_v2_complete_protocol_", content)
        with self.assertRaises(FitnessV2Error):
            proto.validate_complete_protocol({"manifest_id": manifest_id, "content": content})

    def test_lists_every_rule_and_dependency_hash(self):
        envelope = proto.complete_protocol_manifest(**self._kwargs())
        content = envelope["content"]
        # GO §3 has 14 numbered rows: 0,1,2,3,3b,4,5,6,7,8,9,10,11,12.
        self.assertEqual(len(content["rules"]), 14)
        self.assertEqual(
            set(content["depends_on"]),
            {"parameter_freeze", "formula_definitions", "real_warmup_amendment",
             "execution_diversity_amendment"},
        )
        self.assertEqual(len(content["component_names"]), 32)


class ComponentNamesCanonical(unittest.TestCase):
    def test_exactly_32_with_nine_volatility(self):
        volatility = [name for name in proto.COMPONENT_NAMES if name.startswith("volatility_")]
        self.assertEqual(len(proto.COMPONENT_NAMES), 32)
        self.assertEqual(len(volatility), 9)

    def test_sorted_and_unique(self):
        self.assertEqual(list(proto.COMPONENT_NAMES), sorted(proto.COMPONENT_NAMES))
        self.assertEqual(len(set(proto.COMPONENT_NAMES)), 32)


if __name__ == "__main__":
    unittest.main()
