"""Regression tests for the 2026-09-28 performance fix: statistics.mean()
(exact-Fraction, tens of millions of calls per candidate world -- see
incident receipt TBOTS-FITNESS-V2-STATISTICS-MEAN-PERF-20260928) replaced by
math.fsum(values) / len(values) at its two hot call sites:

  - fitness_v2_protocol._pearson_or_none
  - fitness_v2_formula_definitions.trend_window_strengths (via trend_strength)

Compares the current (fixed) implementation against a frozen copy of the
pre-fix statistics.mean()-based implementation over a broad, deterministic,
synthetic fixture set built with this suite's own flat_world() generator
(never real market data, matching this repo's existing test convention).
Verifies: numerically negligible deltas (float-rounding only), identical
descriptor-vector structure, and identical 0.75 distance-admission decisions.
"""
import math
import statistics
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fitness_v2_protocol as proto
import fitness_v2_formula_definitions as fd
from fitness_v2 import FitnessV2Error

from test_fitness_v2_protocol import flat_world, pooled_tau


# ---------------------------------------------------------------------------
# Frozen pre-fix reference implementations (statistics.mean()-based), copied
# verbatim from the code as it stood immediately before this fix.
# ---------------------------------------------------------------------------

def _old_pearson_or_none(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    cx = [v - mx for v in x]
    cy = [v - my for v in y]
    sx = math.fsum(v * v for v in cx)
    sy = math.fsum(v * v for v in cy)
    if sx == 0 or sy == 0:
        return None
    return math.fsum(a * b for a, b in zip(cx, cy)) / math.sqrt(sx * sy)


def _old_trend_window_strengths(prices):
    for value in prices:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise FitnessV2Error("nonfinite/nonpositive or nonnumeric adjusted-close price")
    logs = [math.log(value) for value in prices]
    x = [i - 31 for i in range(63)]
    xx = sum(value * value for value in x)
    result = []
    for start in range(len(logs) - 62):
        y = logs[start:start + 63]
        if all(value == y[0] for value in y):
            result.append(0.0)
            continue
        mean = statistics.mean(y)
        centered = [value - mean for value in y]
        yy = math.fsum(value * value for value in centered)
        numerator = math.fsum(a * b for a, b in zip(x, centered))
        result.append(min(1.0, abs(numerator / math.sqrt(xx * yy))))
    return result


class PearsonOrNoneMatchesPreFixImplementation(unittest.TestCase):
    def test_matches_across_many_windows_and_seeds(self):
        symbols = proto.ASSET_UNIVERSE
        checked = 0
        for seed in range(20):
            world = flat_world(400, seed=seed)
            returns = {
                sym: proto.adjusted_simple_returns(world["assets"][sym]["adjusted_close"])
                for sym in symbols
            }
            for start in range(0, len(returns[symbols[0]]) - proto.CORRELATION_WINDOW, 40):
                for i in range(len(symbols)):
                    for j in range(i + 1, len(symbols)):
                        x = returns[symbols[i]][start:start + proto.CORRELATION_WINDOW]
                        y = returns[symbols[j]][start:start + proto.CORRELATION_WINDOW]
                        old = _old_pearson_or_none(x, y)
                        new = proto._pearson_or_none(x, y)
                        self.assertIsNotNone(old)
                        self.assertIsNotNone(new)
                        self.assertAlmostEqual(old, new, places=9)
                        checked += 1
        self.assertGreater(checked, 500, "fixture sweep too small to be a meaningful regression check")

    def test_zero_variance_still_returns_none_identically(self):
        x = [0.01] * 63
        y = [0.02, -0.01] * 31 + [0.0]
        self.assertIsNone(_old_pearson_or_none(x, y))
        self.assertIsNone(proto._pearson_or_none(x, y))

    def test_perfectly_correlated_case_unchanged(self):
        base = [0.01 * math.sin(i) for i in range(proto.CORRELATION_WINDOW)]
        self.assertAlmostEqual(_old_pearson_or_none(base, base), 1.0)
        self.assertAlmostEqual(proto._pearson_or_none(base, base), 1.0)


class TrendWindowStrengthsMatchesPreFixImplementation(unittest.TestCase):
    def test_matches_across_many_price_series_and_seeds(self):
        checked = 0
        for seed in range(20):
            world = flat_world(300, seed=seed)
            for symbol in proto.ASSET_UNIVERSE:
                prices = world["assets"][symbol]["adjusted_close"]
                old = _old_trend_window_strengths(prices)
                new = fd.trend_window_strengths(prices)
                self.assertEqual(len(old), len(new))
                for a, b in zip(old, new):
                    self.assertAlmostEqual(a, b, places=9)
                checked += len(old)
        self.assertGreater(checked, 500, "fixture sweep too small to be a meaningful regression check")

    def test_flat_window_still_zero_identically(self):
        prices = [100.0] * 70
        self.assertEqual(_old_trend_window_strengths(prices), fd.trend_window_strengths(prices))


class WorldDescriptorVectorAndAdmissionUnaffected(unittest.TestCase):
    """End-to-end: with the two hot call sites monkeypatched back to their
    pre-fix statistics.mean() form, world_descriptor_vector() and the frozen
    0.75 world_distance_admission() gate must reproduce the same structure
    and the same accept/reject decisions as the fixed (current) code."""

    def _descriptor_vectors(self, worlds, tau):
        return [proto.world_descriptor_vector(w, tau) for w in worlds]

    def test_descriptor_structure_and_admission_decisions_match(self):
        n = proto.DESCRIPTOR_WINDOW + 20
        worlds = [flat_world(n, seed=s) for s in range(1, 7)]
        tau = pooled_tau(worlds)

        new_reference = proto.development_reference_calibration(worlds[:3], tau)
        new_admitted = self._descriptor_vectors(worlds[:3], tau)
        new_candidates = self._descriptor_vectors(worlds[3:], tau)

        with patch.object(proto, "_pearson_or_none", _old_pearson_or_none), \
             patch.object(fd, "trend_window_strengths", _old_trend_window_strengths):
            old_reference = proto.development_reference_calibration(worlds[:3], tau)
            old_admitted = self._descriptor_vectors(worlds[:3], tau)
            old_candidates = self._descriptor_vectors(worlds[3:], tau)

        self.assertEqual(set(new_reference["median"]), set(old_reference["median"]))
        self.assertEqual(len(new_candidates), len(old_candidates))

        for new_vec, old_vec in zip(new_candidates, old_candidates):
            self.assertEqual(set(new_vec), set(old_vec), "descriptor structure changed")
            for name in new_vec:
                self.assertAlmostEqual(
                    new_vec[name], old_vec[name], places=6,
                    msg=f"component {name!r} diverged beyond float rounding",
                )

            for family in ("distributional", "sequence", "shock"):
                new_decision = proto.world_distance_admission(
                    family, new_vec, new_admitted, new_reference
                )
                old_decision = proto.world_distance_admission(
                    family, old_vec, old_admitted, old_reference
                )
                self.assertEqual(
                    new_decision, old_decision,
                    f"0.75 admission gate outcome changed after the perf fix (family={family})",
                )


if __name__ == "__main__":
    unittest.main()
