"""S3: attacks the S2 boundary. Adds no trading intelligence and no new
production code path beyond the hardening fixes this exercise justified
(bars type-checking, timestamp/numeric format validation at load time).

Reuses ReplayTestBase from test_replay.py rather than re-deriving the
fixture, so the synthetic dataset (AAA/BBB full window, CCC late
inception, one AAA dividend) is identical to S2's.

Two categories are deliberately NOT the same thing, and this file keeps
them separate throughout:
  - supported-interface isolation: what AgentView.observe()/history()
    can be made to return. This must be airtight.
  - same-process malicious-Python sandboxing: what raw Python
    introspection (vars(), __dict__, walking a name-mangled attribute)
    can reach. Out of scope per the accepted S2 boundary. Tests in
    OutOfScopeIntrospectionTestCase DEMONSTRATE this gap exists and
    stays open on purpose -- they are not failures to fix.
"""
import copy
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

from test_replay import ReplayTestBase  # noqa: E402 (sibling test module)

from hashing import sha256_bytes  # noqa: E402
from replay import (  # noqa: E402
    AgentView, DatasetVerificationError, EpisodeCompletedError, ReplayEngine,
    canonical_json, observation_hash,
)


class DirectFutureAccessTestCase(ReplayTestBase):
    def test_no_way_to_pass_a_date_to_observe_or_history(self):
        import inspect
        eng = self.engine()
        view = AgentView(eng)
        obs_params = inspect.signature(view.observe).parameters
        hist_params = inspect.signature(view.history).parameters
        self.assertEqual(list(obs_params.keys()), [])
        self.assertEqual(list(hist_params.keys()), ["symbol", "bars"])

    def test_repeated_observe_at_same_clock_is_stable_not_advancing(self):
        eng = self.engine()
        view = AgentView(eng)
        o1, o2, o3 = view.observe(), view.observe(), view.observe()
        self.assertEqual(o1, o2)
        self.assertEqual(o2, o3)
        self.assertEqual(eng.current_index, 0)  # observe() itself never advances


class OversizedNegativeZeroHistoryTestCase(ReplayTestBase):
    def test_zero_bars(self):
        view = AgentView(self.engine())
        self.assertEqual(view.history("AAA", 0), [])

    def test_negative_bars(self):
        view = AgentView(self.engine())
        self.assertEqual(view.history("AAA", -1), [])
        self.assertEqual(view.history("AAA", -10 ** 9), [])

    def test_non_int_bars_rejected_not_silently_coerced(self):
        view = AgentView(self.engine())
        for bad in (3.7, "5", None, [5], True):
            with self.assertRaises(TypeError):
                view.history("AAA", bad)

    def test_extremely_large_bars_terminates_fast_and_clamped(self):
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        t0 = time.time()
        hist = view.history("AAA", 10 ** 12)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(len(hist), 6)  # 6 legitimate bars exist by day 6


class SymbolBoundaryTestCase(ReplayTestBase):
    def test_case_sensitivity_not_silently_matched(self):
        view = AgentView(self.engine())
        with self.assertRaises(ValueError):
            view.history("aaa", 5)

    def test_whitespace_padded_symbol_rejected(self):
        view = AgentView(self.engine())
        with self.assertRaises(ValueError):
            view.history(" AAA", 5)
        with self.assertRaises(ValueError):
            view.history("AAA ", 5)

    def test_empty_and_none_symbol_rejected(self):
        view = AgentView(self.engine())
        with self.assertRaises(ValueError):
            view.history("", 5)
        with self.assertRaises(ValueError):
            view.history(None, 5)

    def test_non_string_symbol_rejected(self):
        view = AgentView(self.engine())
        with self.assertRaises(ValueError):
            view.history(12345, 5)


class CorporateActionLeakageTestCase(ReplayTestBase):
    def test_history_bars_spanning_dividend_from_before_shows_it_only_at_its_date(self):
        eng = self.engine()
        view = AgentView(eng)
        while eng.current_timestamp < self.d[5]:
            eng.advance()
        hist = view.history("AAA", 6)  # spans d0..d5, dividend is on d3
        dated = {h.get("timestamp", h.get("day")): h["corporate_action"] for h in hist}
        non_empty = [k for k, v in dated.items() if v]
        self.assertEqual(len(non_empty), 1)

    def test_dividend_absent_from_bbb_which_never_pays_one(self):
        eng = self.engine()
        view = AgentView(eng)
        while eng.status == "RUNNING" and eng.current_index < eng.step_count - 1:
            eng.advance()
        # now at the LAST valid (still-RUNNING) step; history() is
        # blocked once COMPLETED, matching S2's episode-end enforcement
        hist = view.history("BBB", 100)
        self.assertTrue(all(h["corporate_action"] == "" for h in hist))


class FutureInceptionLeakageTestCase(ReplayTestBase):
    def test_bars_value_does_not_change_pre_inception_empty_result(self):
        view = AgentView(self.engine())
        for bars in (1, 5, 1000, 10 ** 6):
            self.assertEqual(view.history("CCC", bars), [])

    def test_availability_flag_carries_no_countdown_information(self):
        """{'available': False} and nothing else -- confirms no 'days
        until available' or similar hint field exists."""
        eng = self.engine()
        view = AgentView(eng)
        obs = view.observe()
        self.assertEqual(obs["assets"]["CCC"], {"available": False})


class MaskedMetadataHashAuditLeakageTestCase(ReplayTestBase):
    def test_dataset_revision_field_is_constant_across_days_not_a_date_proxy(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        revisions = set()
        while True:
            revisions.add(view.observe()["dataset_revision"])
            if not eng.advance():
                break
        self.assertEqual(revisions, {self.dataset_revision})

    def test_observation_hash_cannot_be_correlated_to_real_dates(self):
        """The hash is computed over the already-masked payload -- it
        cannot carry more information about the real date than the
        payload itself already doesn't have."""
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        hashes, real_dates = [], []
        idx = 0
        while True:
            obs = view.observe()
            hashes.append(observation_hash(obs))
            real_dates.append(self.d[idx])
            idx += 1
            if not eng.advance():
                break
        # sanity: hashes differ per day (not degenerate), but nothing about
        # the masked payload used to build them contains the real date
        self.assertEqual(len(set(hashes)), len(hashes))
        for h in hashes:
            self.assertNotIn(h, real_dates)

    def test_agent_view_has_no_db_or_audit_access(self):
        view = AgentView(self.engine())
        for forbidden in ("db", "conn", "connection", "audit", "cursor"):
            self.assertFalse(hasattr(view, forbidden), f"AgentView exposes {forbidden}")

    def test_exception_messages_never_contain_a_real_date(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        for _ in range(3):
            eng.advance()
        attempts = []
        try:
            view.history("ZZZ", 5)
        except ValueError as e:
            attempts.append(str(e))
        try:
            view.history("AAA", "bad")
        except TypeError as e:
            attempts.append(str(e))
        for msg in attempts:
            for real_date in self.d:
                self.assertNotIn(real_date, msg)

    def test_asset_key_order_is_stable_across_days_not_a_date_signal(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        orders = set()
        while True:
            orders.add(tuple(view.observe()["assets"].keys()))
            if not eng.advance():
                break
        self.assertEqual(len(orders), 1)  # identical key order every single day

    def test_serialization_forms_of_returned_payload_leak_nothing_extra(self):
        eng = self.engine(masked_time=True)
        view = AgentView(eng)
        for _ in range(4):
            eng.advance()
        obs = view.observe()
        for form in (str(obs), repr(obs), json.dumps(obs)):
            for real_date in self.d:
                self.assertNotIn(real_date, form)


class CrossAssetTemporalLeakageTestCase(ReplayTestBase):
    def test_querying_one_symbol_does_not_affect_another(self):
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        before = view.history("BBB", 10)
        view.history("AAA", 10)  # unrelated call in between
        view.history("CCC", 10)
        after = view.history("BBB", 10)
        self.assertEqual(before, after)

    def test_repeated_calls_produce_no_shared_mutable_state_interference(self):
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(5):
            eng.advance()
        h1 = view.history("AAA", 5)
        h1[0]["close"] = "TAMPERED"
        h1[0]["available"] = False
        h2 = view.history("AAA", 5)
        self.assertNotEqual(h2[0]["close"], "TAMPERED")
        self.assertTrue(h2[0]["available"])


class EpisodeBoundaryAbuseTestCase(ReplayTestBase):
    def test_start_end_on_non_trading_days_still_bounds_correctly(self):
        # 2021-01-09/10 is a weekend; 2021-01-16/17 is a weekend
        eng = self.engine(start="2021-01-09", end="2021-01-17")
        self.assertEqual(eng.episode_dates[0], self.d[5])   # first Mon in range
        self.assertEqual(eng.episode_dates[-1], self.d[9])  # last Fri in range

    def test_range_with_zero_trading_days_refused_at_construction(self):
        with self.assertRaises(ValueError):
            self.engine(start="2021-01-09", end="2021-01-10")  # pure weekend

    def test_single_day_episode(self):
        eng = self.engine(start=self.d[3], end=self.d[3])
        view = AgentView(eng)
        self.assertEqual(eng.step_count, 1)
        view.observe()
        self.assertFalse(eng.advance())
        self.assertEqual(eng.status, "COMPLETED")


class StaleCachedObservationTestCase(ReplayTestBase):
    def test_previously_returned_observation_does_not_mutate_on_advance(self):
        eng = self.engine()
        view = AgentView(eng)
        obs1 = view.observe()
        snapshot = copy.deepcopy(obs1)
        eng.advance()
        _ = view.observe()
        self.assertEqual(obs1, snapshot)  # unchanged by the advance

    def test_new_observation_after_advance_is_genuinely_different(self):
        eng = self.engine()
        view = AgentView(eng)
        obs1 = view.observe()
        eng.advance()
        obs2 = view.observe()
        self.assertNotEqual(obs1, obs2)


class MutableReturnedObjectTestCase(ReplayTestBase):
    def test_mutating_returned_observation_does_not_corrupt_engine(self):
        eng = self.engine()
        view = AgentView(eng)
        obs = view.observe()
        obs["assets"]["AAA"]["close"] = "HACKED"
        obs["assets"]["NEWFAKE"] = {"available": True, "close": "999"}
        obs2 = view.observe()
        self.assertNotEqual(obs2["assets"]["AAA"]["close"], "HACKED")
        self.assertNotIn("NEWFAKE", obs2["assets"])

    def test_mutating_returned_history_list_does_not_corrupt_engine(self):
        eng = self.engine()
        view = AgentView(eng)
        for _ in range(3):
            eng.advance()
        hist = view.history("AAA", 3)
        hist.append({"available": True, "close": "FAKE", "open": "", "high": "", "low": "",
                     "adjusted_close": "", "volume": "", "corporate_action": "", "timestamp": "2099-01-01"})
        hist2 = view.history("AAA", 3)
        self.assertEqual(len(hist2), 3)
        self.assertNotIn("2099-01-01", [h.get("timestamp") for h in hist2])


class ReachingEngineThroughSupportedInterfaceTestCase(ReplayTestBase):
    def test_no_public_attribute_reaches_engine_or_dataset(self):
        view = AgentView(self.engine())
        for name in dir(view):
            if name.startswith("_"):
                continue
            self.assertIn(name, ("observe", "history"), f"unexpected public attribute: {name}")


class OutOfScopeIntrospectionTestCase(ReplayTestBase):
    """Documents, rather than fixes, the accepted non-goal: raw Python
    introspection of a same-process object is not blocked. This is
    expected to PASS by demonstrating the reach exists -- per the S2
    carry-forward condition, that boundary is not silently broadened."""

    def test_name_mangled_attribute_does_reach_full_dataset_and_this_is_expected(self):
        eng = self.engine()
        view = AgentView(eng)
        reached = getattr(view, "_AgentView__engine")
        self.assertIs(reached, eng)
        self.assertIn("AAA", reached.bundle.per_symbol_rows)
        self.assertEqual(len(reached.bundle.per_symbol_rows["AAA"]), len(self.d))


class MalformedDatasetInputTestCase(ReplayTestBase):
    """Content-hash verification runs before row parsing, so a raw byte
    edit alone is always caught as 'changed normalized artifact' -- that
    path is already covered in test_replay.py. To actually exercise the
    format validators added in S3 (timestamp shape, numeric parseability)
    rather than re-testing the hash check, each test here also patches
    the manifest's recorded hash to match the corrupted file, so hash
    verification passes and format validation is what has to catch it."""

    def _corrupt_fully_self_consistent(self, mutate) -> str:
        """Simulates a 'perfectly forged' dataset: the CSV is corrupted,
        then BOTH hash layers (per-file, and the manifest-level revision
        that's built from it) are correctly recomputed and the manifest
        is renamed to match, exactly as build_dataset_revision.py would.
        This proves format validation is a real second gate, not one
        that only ever gets a chance to run because a forgery was
        sloppy about hash-consistency."""
        csv_path = self.norm_dir / "AAA.csv"
        lines = csv_path.read_text().splitlines()
        mutate(lines)
        new_text = "\n".join(lines) + "\n"
        csv_path.write_text(new_text)

        old_manifest_path = next(self.norm_dir.glob("manifest_*.json"))
        manifest = json.loads(old_manifest_path.read_text())
        manifest["normalized_content_hashes"]["AAA"] = sha256_bytes(new_text.encode("utf-8"))

        manifest_for_hash = {
            "asset_set": manifest["asset_set"],
            "source_identity": manifest["source_identity"],
            "source_artifact_hashes": manifest["source_artifact_hashes"],
            "normalization_version": manifest["normalization_version"],
            "normalized_content_hashes": manifest["normalized_content_hashes"],
            "coverage_metadata": manifest["coverage_metadata"],
        }
        new_revision = "ds_" + sha256_bytes(canonical_json(manifest_for_hash).encode("utf-8"))
        manifest["dataset_revision"] = new_revision

        old_manifest_path.unlink()
        (self.norm_dir / f"manifest_{new_revision}.json").write_text(json.dumps(manifest))
        return new_revision

    def test_malformed_timestamp_format_refused(self):
        new_rev = self._corrupt_fully_self_consistent(
            lambda lines: lines.__setitem__(1, lines[1].replace(self.d[0], "01/04/2021", 1))
        )
        with self.assertRaises(DatasetVerificationError) as ctx:
            ReplayEngine(self.root, new_rev, self.d[0], self.d[-1])
        self.assertIn("timestamp", str(ctx.exception))

    def test_non_numeric_price_field_refused(self):
        def mutate(lines):
            cols = lines[1].split(",")
            cols[2] = "not_a_number"  # open column
            lines[1] = ",".join(cols)
        new_rev = self._corrupt_fully_self_consistent(mutate)
        with self.assertRaises(DatasetVerificationError) as ctx:
            ReplayEngine(self.root, new_rev, self.d[0], self.d[-1])
        self.assertIn("non-numeric", str(ctx.exception))

    def test_non_numeric_adjusted_close_refused(self):
        def mutate(lines):
            cols = lines[1].split(",")
            cols[6] = "garbage"  # adjusted_close column
            lines[1] = ",".join(cols)
        new_rev = self._corrupt_fully_self_consistent(mutate)
        with self.assertRaises(DatasetVerificationError) as ctx:
            ReplayEngine(self.root, new_rev, self.d[0], self.d[-1])
        self.assertIn("adjusted_close", str(ctx.exception))

    def test_hash_mismatch_alone_still_caught_first_when_manifest_not_patched(self):
        """Regression guard for the ordering itself: an unpatched
        corruption must still be refused (by the hash check), proving
        the hash gate is not accidentally bypassed by having a format
        validator at all."""
        csv_path = self.norm_dir / "AAA.csv"
        csv_path.write_text(csv_path.read_text() + "\n")
        with self.assertRaises(DatasetVerificationError) as ctx:
            self.engine()
        self.assertIn("changed normalized artifact", str(ctx.exception))


class DeterministicReplayAfterAdversarialRequestsTestCase(ReplayTestBase):
    def test_legitimate_hash_sequence_unaffected_by_interleaved_attacks(self):
        def clean_run():
            eng = self.engine()
            view = AgentView(eng)
            out = []
            while True:
                out.append(observation_hash(view.observe()))
                if not eng.advance():
                    break
            return out

        def attacked_run():
            eng = self.engine()
            view = AgentView(eng)
            out = []
            while True:
                obs = view.observe()
                out.append(observation_hash(obs))

                # a battery of adversarial noise between every legitimate step
                try:
                    view.history("ZZZ", 5)
                except ValueError:
                    pass
                try:
                    view.history("AAA", -1)
                except Exception:
                    pass
                try:
                    view.history("AAA", "bad")
                except TypeError:
                    pass
                obs["assets"]["AAA"]["close"] = "TAMPERED"
                json.dumps(obs)

                if not eng.advance():
                    break
            return out

        self.assertEqual(clean_run(), attacked_run())


if __name__ == "__main__":
    import unittest
    unittest.main()
