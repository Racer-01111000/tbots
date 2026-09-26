import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from world_seed_stream import (
    FROZEN_INDICES,
    STREAM_NAMESPACE,
    SeedStreamError,
    derive_seed,
    draw_next,
    next_stream_index,
)


def _reference_seed(payload: str) -> int:
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:4], "big")


class DeriveSeed(unittest.TestCase):
    def test_matches_the_exact_frozen_formula_for_index_5(self):
        expected_payload = f"{STREAM_NAMESPACE}|world|distributional|5"
        expected = _reference_seed(expected_payload)
        result = derive_seed("distributional", 5)
        self.assertEqual(result["seed"], expected)
        self.assertEqual(result["attempts"], [
            {"payload": expected_payload, "seed": expected, "rejected_reason": None}
        ])

    def test_is_deterministic_across_repeated_calls(self):
        first = derive_seed("shock", 7)
        second = derive_seed("shock", 7)
        self.assertEqual(first, second)

    def test_different_families_at_the_same_index_differ(self):
        seeds = {family: derive_seed(family, 5)["seed"] for family in
                 ("distributional", "execution", "sequence", "shock")}
        self.assertEqual(len(set(seeds.values())), 4)

    def test_different_indices_in_the_same_family_differ(self):
        seeds = {index: derive_seed("sequence", index)["seed"] for index in range(5, 12)}
        self.assertEqual(len(set(seeds.values())), 7)

    def test_rejects_unknown_family(self):
        with self.assertRaises(SeedStreamError):
            derive_seed("momentum", 5)

    def test_rejects_capitalized_family_names(self):
        for bad_family in ("Distributional", "EXECUTION", "Sequence", "Shock", "SHOCK"):
            with self.assertRaises(SeedStreamError):
                derive_seed(bad_family, 5)

    def test_rejects_index_below_five(self):
        for bad_index in (0, 1, 2, 3, 4):
            with self.assertRaises(SeedStreamError):
                derive_seed("shock", bad_index)

    def test_rejects_non_integer_index(self):
        with self.assertRaises(SeedStreamError):
            derive_seed("shock", 5.0)

    def test_rejects_bool_as_index(self):
        with self.assertRaises(SeedStreamError):
            derive_seed("shock", True)

    def test_retries_with_a_suffix_when_the_seed_is_already_used(self):
        base_payload = f"{STREAM_NAMESPACE}|world|execution|9"
        base_seed = _reference_seed(base_payload)
        result = derive_seed("execution", 9, used_seeds=[base_seed])

        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["attempts"][0]["payload"], base_payload)
        self.assertEqual(result["attempts"][0]["rejected_reason"], "duplicate_seed")
        self.assertEqual(result["attempts"][1]["payload"], base_payload + "|retry|1")
        self.assertNotEqual(result["seed"], base_seed)
        self.assertEqual(result["seed"], result["attempts"][1]["seed"])

    def test_retries_repeatedly_until_an_unused_seed_is_found(self):
        base_payload = f"{STREAM_NAMESPACE}|world|execution|9"
        base_seed = _reference_seed(base_payload)
        retry1_seed = _reference_seed(base_payload + "|retry|1")
        result = derive_seed("execution", 9, used_seeds=[base_seed, retry1_seed])

        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(
            [a["rejected_reason"] for a in result["attempts"]],
            ["duplicate_seed", "duplicate_seed", None],
        )
        self.assertEqual(result["attempts"][2]["payload"], base_payload + "|retry|2")

    def test_never_returns_a_seed_that_was_marked_used(self):
        base_payload = f"{STREAM_NAMESPACE}|world|distributional|6"
        base_seed = _reference_seed(base_payload)
        result = derive_seed("distributional", 6, used_seeds=[base_seed])
        self.assertNotIn(result["seed"], [base_seed])

    def test_rejected_attempts_all_carry_a_reason_and_final_does_not(self):
        result = derive_seed("sequence", 10)
        for attempt in result["attempts"][:-1]:
            self.assertIsNotNone(attempt["rejected_reason"])
        self.assertIsNone(result["attempts"][-1]["rejected_reason"])


class NextStreamIndex(unittest.TestCase):
    def test_returns_five_when_only_the_frozen_four_are_consumed(self):
        self.assertEqual(next_stream_index([1, 2, 3, 4]), 5)

    def test_returns_one_past_the_highest_consumed_index(self):
        self.assertEqual(next_stream_index([1, 2, 3, 4, 5, 6]), 7)

    def test_order_of_consumed_indices_does_not_matter(self):
        self.assertEqual(next_stream_index([4, 6, 1, 3, 2, 5]), 7)

    def test_a_rejected_index_still_counts_as_consumed(self):
        # index 5 was drawn and rejected (never accepted into the bank) --
        # the next draw must still be 6, never a re-draw of 5.
        self.assertEqual(next_stream_index([1, 2, 3, 4, 5]), 6)

    def test_raises_if_the_frozen_four_are_not_all_present(self):
        with self.assertRaises(SeedStreamError):
            next_stream_index([1, 2, 4])

    def test_raises_on_an_empty_history(self):
        with self.assertRaises(SeedStreamError):
            next_stream_index([])


class DrawNext(unittest.TestCase):
    def test_draws_index_five_first_and_matches_derive_seed(self):
        result = draw_next("shock", [1, 2, 3, 4], used_seeds=[])
        self.assertEqual(result["stream_index"], 5)
        self.assertEqual(result["seed"], derive_seed("shock", 5)["seed"])

    def test_advances_past_prior_rejections(self):
        result = draw_next("shock", [1, 2, 3, 4, 5, 6], used_seeds=[])
        self.assertEqual(result["stream_index"], 7)

    def test_passes_used_seeds_through_to_force_a_retry(self):
        base_payload = f"{STREAM_NAMESPACE}|world|shock|5"
        base_seed = _reference_seed(base_payload)
        result = draw_next("shock", [1, 2, 3, 4], used_seeds=[base_seed])
        self.assertEqual(result["stream_index"], 5)
        self.assertNotEqual(result["seed"], base_seed)
        self.assertEqual(len(result["attempts"]), 2)


class SimulatedExpansionSequence(unittest.TestCase):
    """Exercises the exact caller pattern the future world-bank generator
    will use: maintain consumed_indices/used_seeds, draw, record, repeat --
    proving indices are never reused or skipped across a run of draws that
    mixes accepted and rejected outcomes."""

    def test_a_full_run_of_draws_never_reuses_or_skips_an_index(self):
        consumed = [1, 2, 3, 4]
        used_seeds = []
        drawn_indices = []
        for _ in range(5):
            result = draw_next("distributional", consumed, used_seeds)
            drawn_indices.append(result["stream_index"])
            consumed.append(result["stream_index"])
            used_seeds.append(result["seed"])
        self.assertEqual(drawn_indices, [5, 6, 7, 8, 9])
        self.assertEqual(len(set(used_seeds)), 5)


class PinnedIndexFiveValues(unittest.TestCase):
    """Rick's decision, 2026-09-26 (FITNESS_V2_SEED_STREAM_CASING_DECISION_
    20260926.md): the lowercase family identifiers already used throughout
    fitness_v2_parameter_freeze are the ones this formula uses -- not a
    recovered interpretation of Sable handoff section 48, Rick's own call
    since section 48 does not specify a casing. These four literal values
    were supplied by Rick as pinned evidence and independently verified
    against this module before being committed here. Hardcoded (not
    recomputed via _reference_seed) so a future accidental change to the
    namespace string, the digest truncation, or the retry rule is caught
    immediately rather than silently re-deriving a new "expected" value."""

    PINNED = {
        "distributional": 3728510749,
        "execution": 2005964006,
        "sequence": 687330419,
        "shock": 3037866416,
    }

    def test_every_family_index_five_matches_ricks_pinned_value(self):
        for family, expected_seed in self.PINNED.items():
            with self.subTest(family=family):
                self.assertEqual(derive_seed(family, 5)["seed"], expected_seed)

    def test_pinned_values_are_pairwise_distinct(self):
        self.assertEqual(len(set(self.PINNED.values())), 4)


if __name__ == "__main__":
    unittest.main()
