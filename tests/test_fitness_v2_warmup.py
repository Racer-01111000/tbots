from __future__ import annotations

import copy
import math
from pathlib import Path
import statistics
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from fitness_v2 import FitnessV2Error
from fitness_v2_warmup import (AMENDMENT_ID, WARMUP_AMENDMENT, validate_amendment,
                               validate_real_warmup, warmup_policy)
from fitness_v2_admission import market_distance_passes
from s5a_development_bundle import load_authorized_development_bundle


class WarmupAmendmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_authorized_development_bundle()

    def test_content_address_and_no_readiness_claim(self):
        import json
        envelope = json.loads((ROOT / "evolution/protocol" / (AMENDMENT_ID + ".json")).read_text())
        self.assertEqual(validate_amendment(envelope), AMENDMENT_ID)
        self.assertFalse(envelope["content"]["resolution_status"]["complete_executable_protocol_ready"])
        envelope["content"]["shock"]["market_distance_exempt"] = True
        with self.assertRaises(FitnessV2Error):
            validate_amendment(envelope)

    def test_all_shapes_exact_real_anchor_and_no_mutation(self):
        for start in ("2007-02-07", "2011-02-07", "2015-02-07"):
            anchor = {s: [r for r in rows if r["timestamp"] < start][-378:]
                      for s, rows in self.bundle.per_symbol_rows.items()}
            before = copy.deepcopy(anchor)
            for family in ("execution", "shock"):
                self.assertTrue(validate_real_warmup(family, copy.deepcopy(anchor), anchor, scoring_start=start))
            self.assertEqual(anchor, before)
            if start == "2007-02-07":
                self.assertEqual(len(anchor["DBC"]), 252)

    def test_generated_families_cannot_take_exception(self):
        for family in ("distributional", "sequence"):
            self.assertEqual(warmup_policy(family), "EXACT_378_SAME_TRANSFORMATION_STREAM")
            with self.assertRaises(FitnessV2Error):
                validate_real_warmup(family, {}, {}, scoring_start="2007-02-07")

    def test_fabricated_missing_or_future_rows_fail(self):
        anchor = {s: [r for r in rows if r["timestamp"] < "2007-02-07"][-378:]
                  for s, rows in self.bundle.per_symbol_rows.items()}
        for family in ("execution", "shock"):
            for mutation in ("missing", "price", "future"):
                changed = copy.deepcopy(anchor)
                if mutation == "missing":
                    changed["DBC"].pop()
                elif mutation == "price":
                    changed["DBC"][0]["close"] = "999999"
                else:
                    changed["DBC"][-1]["timestamp"] = "2019-01-02"
                with self.assertRaises(FitnessV2Error):
                    validate_real_warmup(family, changed, anchor, scoring_start="2007-02-07")
            changed = copy.deepcopy(anchor)
            changed["DBC"][-1]["timestamp"] = "2019-01-02"
            with self.assertRaises(FitnessV2Error):
                validate_real_warmup(family, changed, changed, scoring_start="2007-02-07")

    def test_shock_warmup_exception_now_also_carries_a_distance_exemption(self):
        # Superseded 2026-09-29 (Rick): Shock's warm-up exception previously
        # did NOT imply a distance exemption (both were False here). Rick has
        # since separately exempted Shock from the >=0.75 threshold itself
        # (see fitness_v2_admission.py); this warm-up module's own declarative
        # WARMUP_AMENDMENT["shock"]["market_distance_exempt"] field is left at
        # False deliberately, since editing it would change the content-hashed
        # AMENDMENT_ID embedded in the frozen complete-protocol manifest chain.
        # The live predicate in fitness_v2_admission.py is the source of truth.
        self.assertTrue(market_distance_passes("execution", [0.0], [[0.0]]))
        self.assertTrue(market_distance_passes("shock", [0.0], [[0.0]]))
        for family in ("sequence", "distributional"):
            self.assertFalse(market_distance_passes(family, [0.0], [[0.0]]))

    def test_unknown_family_is_not_an_exception(self):
        with self.assertRaises(FitnessV2Error):
            warmup_policy("historical")


class DescriptorDefinitionProofTests(unittest.TestCase):
    def test_unspecified_trend_normalization_changes_frozen_gate(self):
        # Descriptor-level algebra only: no price series, world, or genome.
        # These alternatives illustrate the unresolved definition; neither is
        # implemented as an admission descriptor.
        reference = [.1, .2, .3, .4, .5]
        def distance(transform):
            values = [transform(x) for x in reference]
            median = statistics.median(values)
            mad = statistics.median(abs(x-median) for x in values)
            z = (transform(.449)-median)/mad
            return math.sqrt(8*z*z/32)
        correlation = distance(lambda r: r)
        t_statistic = distance(lambda r: r*math.sqrt(61/(1-r*r)))
        self.assertAlmostEqual(correlation, .745)
        self.assertAlmostEqual(t_statistic, .770866618310756)
        self.assertLess(correlation, .75)
        self.assertGreater(t_statistic, .75)

    def test_zero_mad_does_not_define_numeric_family_z(self):
        reference = [0.0]*5
        median = statistics.median(reference)
        mad = statistics.median(abs(x-median) for x in reference)
        self.assertEqual(mad, 0)
        self.assertTrue(-1.0 != median)  # Given per-world fallback is true.
        with self.assertRaises(ZeroDivisionError):
            _ = (-1.0-median)/(1.4826*mad)


if __name__ == "__main__":
    unittest.main()
