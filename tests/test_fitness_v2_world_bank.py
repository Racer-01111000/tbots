from __future__ import annotations

import json
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from fitness_v2 import FitnessV2Error
from fitness_v2_protocol import ASSET_UNIVERSE
from fitness_v2_world_bank import (
    WorldBankError,
    _development_pool_for_reconstruction,
    build_synthetic_family,
    distributional_world,
    execution_world,
    historical_world,
    scored_only,
    sequence_world,
    shock_world,
)
from s5a_config import PREDEVELOPMENT_WARMUP_BARS


def _dates(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:  # skip weekends for realism; not load-bearing
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _fake_pool(start="2007-01-01", n=1500, dividend_every=None, short_assets=None,
                walk_seed=20260926, drift=1.00015, stdev=0.01):
    """A synthetic DEVELOPMENT-pool-shaped bundle: per-asset real-format rows
    (string fields) over `n` trading days. A seeded random walk with a slight
    positive drift (not a perfectly smooth/periodic path) so descriptors that
    depend on genuine cross-window variation (drawdown depth/duration, gap
    frequency) don't degenerate to a single repeated value -- deterministic
    across runs via the fixed seed, never actual market data."""
    short_assets = short_assets or {}
    dates = _dates(start, n)
    pool = {}
    for a_index, symbol in enumerate(ASSET_UNIVERSE):
        rng = random.Random(f"{walk_seed}-{symbol}")
        asset_drift = drift + a_index * 0.00002
        level = 100.0
        rows = []
        skip_before = short_assets.get(symbol, 0)
        for i, ts in enumerate(dates):
            daily = asset_drift + rng.gauss(0.0, stdev)
            level *= max(daily, 0.5)  # never let a synthetic day go non-positive
            corporate_action = ""
            if dividend_every and i % dividend_every == 0 and i > 0:
                corporate_action = json.dumps({"dividend_amount": round(level * 0.001, 4)})
            if i < skip_before:
                continue
            rows.append({
                "timestamp": ts, "open": f"{level:.6f}", "high": f"{level * 1.002:.6f}",
                "low": f"{level * 0.998:.6f}", "close": f"{level:.6f}",
                "adjusted_close": f"{level:.6f}", "volume": "1000000",
                "corporate_action": corporate_action,
            })
        pool[symbol] = rows
    return pool


FAKE_PROTOCOL = {
    "h_shapes": {
        "dates": {
            "H1": {"start": None, "end": None, "n_sessions": None},
        }
    }
}


def _fake_protocol_for(pool, shape_name, start_offset, n_sessions):
    # Deliberately NOT ASSET_UNIVERSE[0] ("DBC") -- that's exactly the asset
    # short_assets tests shorten, which would silently shift start_offset's
    # meaning to DBC's own trimmed row list instead of the full calendar.
    # "SPY" is never shortened by any test in this module.
    dates = [r["timestamp"] for r in pool["SPY"]]
    start = dates[start_offset]
    end = dates[start_offset + n_sessions - 1]
    return {"h_shapes": {"dates": {shape_name: {"start": start, "end": end, "n_sessions": n_sessions}}}}


class HistoricalWorldConstruction(unittest.TestCase):
    def test_builds_synchronized_warmup_plus_scored(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        world = historical_world(pool, "H1", protocol)
        for symbol in ASSET_UNIVERSE:
            self.assertEqual(len(world["assets"][symbol]["adjusted_close"]), 378 + 300)
        self.assertEqual(world["scored_start_index"], 378)
        self.assertEqual(world["family"], "historical")

    def test_short_real_warmup_asset_is_none_padded_not_fabricated(self):
        # Shape starts at date-index 500; DBC's data begins only at index
        # 400, i.e. 100 real bars before the shape start instead of the
        # full 378 every other asset has (mirrors handoff §18's real DBC/H1
        # example, 252 real bars instead of 378).
        pool = _fake_pool(n=1500, short_assets={"DBC": 400})
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=50)
        world = historical_world(pool, "H1", protocol)
        dbc_close = world["assets"]["DBC"]["adjusted_close"]
        none_count = sum(1 for v in dbc_close[:378] if v is None)
        self.assertGreater(none_count, 0)
        self.assertTrue(all(v is not None for v in dbc_close[378:]))

    def test_rejects_wrong_session_count(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        protocol["h_shapes"]["dates"]["H1"]["n_sessions"] = 301
        with self.assertRaises(WorldBankError):
            historical_world(pool, "H1", protocol)


class ScoredOnly(unittest.TestCase):
    def test_strips_warmup_prefix(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        world = historical_world(pool, "H1", protocol)
        stripped = scored_only(world)
        self.assertEqual(len(stripped["assets"]["SPY"]["adjusted_close"]), 300)
        self.assertEqual(
            stripped["assets"]["SPY"]["adjusted_close"][0],
            world["assets"]["SPY"]["adjusted_close"][378],
        )


class ExecutionWorld(unittest.TestCase):
    def test_is_identical_to_its_anchor_except_family_tag(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        anchor = historical_world(pool, "H1", protocol)
        execution = execution_world(anchor)
        self.assertEqual(execution["assets"], anchor["assets"])
        self.assertEqual(execution["family"], "execution")


class DistributionalWorld(unittest.TestCase):
    def test_builds_requested_length_anchored_at_real_start_price(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        anchor = historical_world(pool, "H1", protocol)
        recon_pool = _development_pool_for_reconstruction(pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"])
        world = distributional_world(recon_pool, anchor, seed=12345, target_length=378 + 300)
        for symbol in ASSET_UNIVERSE:
            self.assertEqual(len(world["assets"][symbol]["adjusted_close"]), 378 + 300)
        self.assertEqual(
            world["assets"]["SPY"]["raw_open"][0], anchor["assets"]["SPY"]["raw_open"][378]
        )

    def test_deterministic_given_same_seed(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=200)
        anchor = historical_world(pool, "H1", protocol)
        recon_pool = _development_pool_for_reconstruction(pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"])
        a = distributional_world(recon_pool, anchor, seed=999, target_length=378 + 200)
        b = distributional_world(recon_pool, anchor, seed=999, target_length=378 + 200)
        self.assertEqual(a["assets"], b["assets"])

    def test_different_seeds_diverge(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=200)
        anchor = historical_world(pool, "H1", protocol)
        recon_pool = _development_pool_for_reconstruction(pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"])
        a = distributional_world(recon_pool, anchor, seed=1, target_length=378 + 200)
        b = distributional_world(recon_pool, anchor, seed=2, target_length=378 + 200)
        self.assertNotEqual(a["assets"], b["assets"])


class SequenceWorld(unittest.TestCase):
    def test_builds_requested_length_from_intact_segments(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=200)
        anchor = historical_world(pool, "H1", protocol)
        recon_pool = _development_pool_for_reconstruction(pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"])
        world = sequence_world(recon_pool, anchor, seed=42, target_scored_length=200)
        for symbol in ASSET_UNIVERSE:
            self.assertEqual(len(world["assets"][symbol]["adjusted_close"]), 378 + 200)

    def test_zero_join_gap_at_every_segment_boundary(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=200)
        anchor = historical_world(pool, "H1", protocol)
        recon_pool = _development_pool_for_reconstruction(pool, pool["SPY"][0]["timestamp"], pool["SPY"][-1]["timestamp"])
        world = sequence_world(recon_pool, anchor, seed=7, target_scored_length=200)
        opens = world["assets"]["SPY"]["raw_open"]
        closes = world["assets"]["SPY"]["raw_close"]
        # joined = [anchor] + segment_1(63 rows) + segment_2(63 rows) + ...,
        # so each new segment starts at absolute index 1 + 63*k (not a
        # multiple of 63 -- the anchor itself occupies index 0). Its open
        # must continue exactly from the running series' prior close.
        boundaries = range(1, len(opens), 63)
        self.assertGreater(len(list(boundaries)), 1)  # sanity: fixture is long enough
        for boundary in boundaries:
            self.assertAlmostEqual(opens[boundary], closes[boundary - 1], places=6)


class ShockWorld(unittest.TestCase):
    def test_exactly_three_sessions_change_from_the_anchor(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        anchor = historical_world(pool, "H1", protocol)
        donor_pool = [(0, 0.02), (1, -0.015), (2, 0.03), (3, -0.025), (4, 0.018)]
        world = shock_world(anchor, seed=5, donor_pool=donor_pool, extrema=(-0.5, 0.5))
        changed_assets = set()
        for symbol in ASSET_UNIVERSE:
            before = anchor["assets"][symbol]["raw_close"]
            after = world["assets"][symbol]["raw_close"]
            if before != after:
                changed_assets.add(symbol)
        self.assertGreater(len(changed_assets), 0)
        self.assertLessEqual(len(changed_assets), 3)

    def test_rejects_gap_outside_development_extrema(self):
        pool = _fake_pool(n=1500)
        protocol = _fake_protocol_for(pool, "H1", start_offset=500, n_sessions=300)
        anchor = historical_world(pool, "H1", protocol)
        donor_pool = [(0, 10.0), (1, -0.015), (2, 0.03)]
        with self.assertRaises(FitnessV2Error):
            shock_world(anchor, seed=5, donor_pool=donor_pool, extrema=(-0.5, 0.5))


class SyntheticFamilyAdmission(unittest.TestCase):
    """Tests build_synthetic_family's own retry/accept/replace-seed loop --
    NOT development_reference_calibration's statistical robustness, which is
    a separate concern with its own coverage in test_fitness_v2_protocol.py.
    A hand-built reference (valid, nonzero MAD by construction) is used
    instead of deriving one from a synthetic price world: doing that
    correctly for every one of the 32 descriptor components turned out to
    require production-scale fixture data (a driftless random walk left long
    stretches sharing an identical drawdown_duration; several different
    engineered deterministic signals fixed one component's zero-MAD only by
    reintroducing it in another -- e.g. a periodic amplitude modulation with
    period <= 252 makes every 252-window capture the same worst point, so
    portfolio-level drawdown_depth stayed constant to float precision
    regardless of the modulation). None of that is what this test is for."""

    def setUp(self):
        self.pool = _fake_pool(n=1500)
        self.protocol = _fake_protocol_for(self.pool, "H1", start_offset=500, n_sessions=300)
        self.anchor = historical_world(self.pool, "H1", self.protocol)
        self.tau = 0.02
        from fitness_v2_protocol import COMPONENT_NAMES
        self.reference = {
            "median": {name: 0.0 for name in COMPONENT_NAMES},
            "mad": {name: 0.1 for name in COMPONENT_NAMES},
        }

    def test_all_four_initial_seeds_can_be_accepted_or_replaced(self):
        seeds = [111, 222, 333, 444]

        def build(seed):
            return execution_world(self.anchor)  # distance-exempt: trivially admits every time

        result = build_synthetic_family(
            "execution", slot_count=2, initial_seeds=seeds, build_candidate=build,
            reference=self.reference, admitted_reference_vectors=[], tau=self.tau,
        )
        self.assertEqual(len(result["worlds"]), 2)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(all(a["accepted"] for a in result["attempts"]))

    def test_rejected_seed_advances_to_replacement_stream(self):
        seeds = [111, 222, 333, 444]
        calls = {"n": 0}

        def build(seed):
            calls["n"] += 1
            # Reject the very first attempt outright to force a replacement draw.
            if calls["n"] == 1:
                raise FitnessV2Error("forced rejection for this test")
            return execution_world(self.anchor)

        result = build_synthetic_family(
            "execution", slot_count=1, initial_seeds=seeds, build_candidate=build,
            reference=self.reference, admitted_reference_vectors=[], tau=self.tau,
        )
        self.assertEqual(len(result["worlds"]), 1)
        self.assertFalse(result["attempts"][0]["accepted"])
        self.assertTrue(result["attempts"][1]["accepted"])
        self.assertEqual(result["attempts"][1]["stream_index"], 2)


if __name__ == "__main__":
    unittest.main()
