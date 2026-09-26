"""Fitness V2 world-bank construction: 3 real historical worlds (H1/H2/H3)
plus 16 synthetic worlds (4 per family: distributional/execution/sequence/
shock), wiring only already-frozen primitives -- this module invents no new
world-construction mathematics, only the orchestration connecting them.

Every function here takes its DEVELOPMENT source data as a plain argument
rather than loading it internally, so this module's own tests exercise every
code path (bootstrap sampling, segment joins, shock relocation, admission,
seed-stream replacement) against small synthetic fixtures, never real market
data. The one function that touches real data
(load_real_development_pool) is a thin, obviously-real-data-only wrapper
around s5a_development_bundle.load_authorized_development_bundle -- the same
already hash-verified bundle S5A itself uses -- and is exercised only by
integration code that actually runs after the audit gate opens, never by
this module's unit tests.
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from collections.abc import Mapping, Sequence

from fitness_v2 import FitnessV2Error, content_identity, validate_world_manifest
from fitness_v2_admission import market_distance_passes
from fitness_v2_protocol import (
    ASSET_UNIVERSE, OHLC_FIELDS, H_SHAPE_STARTS,
    apply_shock_event, assert_within_development_extrema, draw_sequence_segment_starts,
    draw_shock_events, gap_extrema, join_sequence_segments, reconstruct_distributional_path,
    sequence_warmup_and_scored, stationary_bootstrap_indices, validate_complete_protocol,
    world_descriptor_vector, world_distance_admission,
)
from s5a_config import PREDEVELOPMENT_WARMUP_BARS
from world_seed_stream import FROZEN_INDICES, draw_next

SYNTHETIC_FAMILIES = ("distributional", "execution", "sequence", "shock")
INITIAL_WORLDS_PER_FAMILY = 4
WITHHELD_SLOT = 4
SEQUENCE_SEGMENT_LENGTH = 63
SHOCK_EVENT_COUNT = 3
MAX_TOTAL_SYNTHETIC_WORLDS = 32  # handoff §50
MAX_WORLDS_PER_FAMILY = MAX_TOTAL_SYNTHETIC_WORLDS // len(SYNTHETIC_FAMILIES)

COMPLETE_PROTOCOL_PATH = (
    Path(__file__).resolve().parents[1] / "evolution" / "protocol"
    / "fitness_v2_complete_protocol_"
    "49c71da11d46f56838a560481350cb3436af3177bab6cb13254f9c84860c6e89.json"
)


class WorldBankError(FitnessV2Error):
    pass


def load_complete_protocol() -> dict:
    envelope = json.loads(COMPLETE_PROTOCOL_PATH.read_text())
    validate_complete_protocol(envelope)
    return envelope["content"]


# ---------------------------------------------------------------------------
# Real-data source loading (never exercised by this module's own tests)
# ---------------------------------------------------------------------------

def load_real_development_pool() -> dict[str, list[dict]]:
    """The one real-data entry point in this module. Returns, per asset, the
    authorized DEVELOPMENT bundle's own rows (string-typed fields exactly as
    s5a_development_bundle verifies them), sorted by timestamp. Callers slice
    this into H-shapes / bootstrap pools; nothing here constructs a world."""
    from s5a_development_bundle import assert_isolated, load_authorized_development_bundle

    bundle = load_authorized_development_bundle()
    assert_isolated(bundle)
    return bundle.per_symbol_rows


# ---------------------------------------------------------------------------
# Row <-> array-world conversion
# ---------------------------------------------------------------------------

def _row_price_fields(row: Mapping[str, object]) -> dict:
    if not row.get("adjusted_close"):
        raise WorldBankError(f"missing adjusted_close at {row.get('timestamp')}")
    return {
        "raw_open": float(row["open"]), "raw_high": float(row["high"]),
        "raw_low": float(row["low"]), "raw_close": float(row["close"]),
        "adjusted_close": float(row["adjusted_close"]),
    }


def _rows_to_asset_arrays(rows_by_symbol: Mapping[str, Sequence[Mapping[str, object]]]) -> dict:
    """rows_by_symbol values may contain None entries at the front (a short
    real warmup, handoff §18 -- e.g. DBC's 252-not-378 bars before H1) --
    passed straight through as unavailable per fitness_v2_simulator.WorldView."""
    assets = {}
    lengths = {len(rows) for rows in rows_by_symbol.values()}
    if len(lengths) != 1:
        raise WorldBankError("all eight assets must produce the same array length")
    for symbol in ASSET_UNIVERSE:
        rows = rows_by_symbol[symbol]
        adjusted_close, raw_open, raw_high, raw_low, raw_close, corporate_action = [], [], [], [], [], []
        for row in rows:
            if row is None:
                adjusted_close.append(None)
                raw_open.append(None)
                raw_high.append(None)
                raw_low.append(None)
                raw_close.append(None)
                corporate_action.append("")
            else:
                fields = _row_price_fields(row) if "raw_open" not in row else row
                adjusted_close.append(fields["adjusted_close"])
                raw_open.append(fields["raw_open"])
                raw_high.append(fields["raw_high"])
                raw_low.append(fields["raw_low"])
                raw_close.append(fields["raw_close"])
                corporate_action.append(row.get("corporate_action", "") or "")
        assets[symbol] = {
            "adjusted_close": adjusted_close, "raw_open": raw_open,
            "raw_high": raw_high, "raw_low": raw_low,
            "raw_close": raw_close, "corporate_action": corporate_action,
        }
    return {"assets": assets}


# ---------------------------------------------------------------------------
# Historical worlds (H1/H2/H3) -- real data, real warm-up, no transformation
# ---------------------------------------------------------------------------

def historical_world(
    development_pool: Mapping[str, Sequence[Mapping[str, object]]], shape_name: str,
    protocol: Mapping[str, object],
) -> dict:
    """Slices the real DEVELOPMENT pool into one H-shape's real warm-up
    (up to PREDEVELOPMENT_WARMUP_BARS=378 real prior bars per asset, front-
    padded with None for an asset with fewer -- handoff §18) followed by its
    real scored region exactly as frozen in the complete-protocol manifest's
    h_shapes.dates. This IS "the matching historical anchor": Execution and
    Shock both start from this world's own scored prices, never a copy with
    different real data."""
    shape = protocol["h_shapes"]["dates"][shape_name]
    start, end = shape["start"], shape["end"]
    rows_by_symbol = {}
    for symbol in ASSET_UNIVERSE:
        rows = development_pool[symbol]
        pre = [r for r in rows if r["timestamp"] < start]
        scored = [r for r in rows if start <= r["timestamp"] <= end]
        if len(scored) != shape["n_sessions"]:
            raise WorldBankError(
                f"{symbol} {shape_name}: expected {shape['n_sessions']} scored "
                f"sessions, found {len(scored)}"
            )
        warmup = pre[-PREDEVELOPMENT_WARMUP_BARS:]
        padding = [None] * (PREDEVELOPMENT_WARMUP_BARS - len(warmup))
        rows_by_symbol[symbol] = padding + warmup + scored
    world = _rows_to_asset_arrays(rows_by_symbol)
    world["scored_start_index"] = PREDEVELOPMENT_WARMUP_BARS
    world["family"] = "historical"
    world["shape"] = shape_name
    return world


def scored_only(world: Mapping[str, object]) -> dict:
    """The world's own scored region alone, with no warm-up prefix -- the
    shape descriptor computation (world_descriptor_vector) and diversity
    admission require: they must never see a row before the world's own
    first DEVELOPMENT session (DEVELOPMENT_BOUNDARY_RULE), a stricter rule
    than simulate_world's warm-up allowance."""
    start = world["scored_start_index"]
    assets = {
        symbol: {field: values[start:] for field, values in row.items()}
        for symbol, row in world["assets"].items()
    }
    return {"assets": assets}


# ---------------------------------------------------------------------------
# Distributional: synchronized stationary bootstrap + real-anchor reconstruction
# ---------------------------------------------------------------------------

def _development_pool_for_reconstruction(
    development_pool: Mapping[str, Sequence[Mapping[str, object]]],
    development_start: str, development_end: str,
) -> dict[str, list[dict]]:
    """Per asset, every DEVELOPMENT-window row (excluding the pool's own
    first date, which has no "prev" day) carrying its own day's OHLC/
    adjusted-close ratios and dividend yield -- reconstruct_distributional_path
    consumes only relative ratios from these, never an absolute source level."""
    prepared = {}
    for symbol in ASSET_UNIVERSE:
        rows = [
            r for r in development_pool[symbol]
            if development_start <= r["timestamp"] <= development_end
        ]
        pool = []
        for i in range(1, len(rows)):
            row, prev = rows[i], rows[i - 1]
            fields = _row_price_fields(row)
            prev_fields = _row_price_fields(prev)
            dividend_amount = None
            if row.get("corporate_action"):
                from execution import dividend_amount_dollars
                dividend_amount = dividend_amount_dollars(row["corporate_action"])
            pool.append({
                **fields,
                "prev_raw_close": prev_fields["raw_close"],
                "prev_adjusted_close": prev_fields["adjusted_close"],
                "dividend_yield": (dividend_amount / prev_fields["raw_close"]) if dividend_amount else 0.0,
            })
        prepared[symbol] = pool
    lengths = {len(v) for v in prepared.values()}
    if len(lengths) != 1:
        raise WorldBankError("DEVELOPMENT reconstruction pool is not synchronized across assets")
    return prepared


def distributional_world(
    pool: Mapping[str, Sequence[Mapping[str, object]]], anchor_world: Mapping[str, object],
    seed: int, target_length: int,
) -> dict:
    """One common bootstrap-index draw across all eight assets (frozen: "one
    common source-index stream", no per-asset independent resampling).
    target_length = PREDEVELOPMENT_WARMUP_BARS (378) + the matching H-shape's
    scored session count -- warm-up and scored region are drawn from the
    exact same transformation stream, never two different draws."""
    pool_length = len(next(iter(pool.values())))
    rng = random.Random(seed)
    # reconstruct_distributional_path prepends the anchor row itself as bar
    # 0, so only target_length-1 sampled rows are needed to reach exactly
    # target_length total bars.
    indices = stationary_bootstrap_indices(rng, pool_length, target_length - 1)
    scored_start = anchor_world["scored_start_index"]
    rows_by_symbol = {}
    for symbol in ASSET_UNIVERSE:
        anchor_start_row = {
            field: anchor_world["assets"][symbol][field][scored_start]
            for field in ("raw_open", "raw_high", "raw_low", "raw_close", "adjusted_close")
        }
        sampled = [pool[symbol][i] for i in indices]
        reconstructed = reconstruct_distributional_path(anchor_start_row, sampled)
        rows_by_symbol[symbol] = reconstructed
    world = {
        "assets": {
            symbol: {
                "adjusted_close": [r["adjusted_close"] for r in rows],
                "raw_open": [r["raw_open"] for r in rows],
                "raw_high": [r["raw_high"] for r in rows],
                "raw_low": [r["raw_low"] for r in rows],
                "raw_close": [r["raw_close"] for r in rows],
                "corporate_action": ["" for _ in rows],
                "dividend_cents": [r["dividend_cents"] for r in rows],
            }
            for symbol, rows in rows_by_symbol.items()
        }
    }
    world["scored_start_index"] = PREDEVELOPMENT_WARMUP_BARS
    world["family"] = "distributional"
    world["seed"] = seed
    return world


# ---------------------------------------------------------------------------
# Sequence: intact 63-session segments, one shared draw across all assets
# ---------------------------------------------------------------------------

def sequence_world(
    pool: Mapping[str, Sequence[Mapping[str, object]]], anchor_world: Mapping[str, object],
    seed: int, target_scored_length: int,
) -> dict:
    pool_length = len(next(iter(pool.values())))
    # Joined length = 1 (anchor) + n_segments*63; must cover both the 378
    # required warm-up bars AND the requested scored length, since warm-up
    # and scored share the same joined stream (no separate draw).
    total_needed = PREDEVELOPMENT_WARMUP_BARS + target_scored_length - 1
    n_segments = -(-total_needed // SEQUENCE_SEGMENT_LENGTH)  # ceil
    rng = random.Random(seed)
    starts = draw_sequence_segment_starts(rng, pool_length, SEQUENCE_SEGMENT_LENGTH, n_segments)
    scored_start = anchor_world["scored_start_index"]
    rows_by_symbol = {}
    for symbol in ASSET_UNIVERSE:
        anchor_start_row = {
            field: anchor_world["assets"][symbol][field][scored_start]
            for field in ("raw_open", "raw_high", "raw_low", "raw_close", "adjusted_close")
        }
        segments = [
            [
                {
                    **{f: pool[symbol][i][f] for f in OHLC_FIELDS},
                    "adjusted_close": pool[symbol][i]["adjusted_close"],
                    "dividend_cents": (
                        (pool[symbol][i]["dividend_yield"] or 0.0) * pool[symbol][i]["prev_raw_close"]
                    ),
                }
                for i in range(start, start + SEQUENCE_SEGMENT_LENGTH)
            ]
            for start in starts
        ]
        joined = join_sequence_segments(segments, anchor_start_row)
        warmup, scored = sequence_warmup_and_scored(joined)
        combined = (warmup + scored)[:PREDEVELOPMENT_WARMUP_BARS + target_scored_length]
        rows_by_symbol[symbol] = combined
    world = {
        "assets": {
            symbol: {
                "adjusted_close": [r["adjusted_close"] for r in rows],
                "raw_open": [r["raw_open"] for r in rows],
                "raw_high": [r["raw_high"] for r in rows],
                "raw_low": [r["raw_low"] for r in rows],
                "raw_close": [r["raw_close"] for r in rows],
                "corporate_action": ["" for _ in rows],
                "dividend_cents": [r["dividend_cents"] for r in rows],
            }
            for symbol, rows in rows_by_symbol.items()
        }
    }
    world["scored_start_index"] = PREDEVELOPMENT_WARMUP_BARS
    world["family"] = "sequence"
    world["seed"] = seed
    return world


# ---------------------------------------------------------------------------
# Execution: identical to its historical anchor; only a skip-mask differs
# ---------------------------------------------------------------------------

def execution_world(anchor_world: Mapping[str, object]) -> dict:
    """Handoff §17: "Execution price path = matching historical anchor price
    path." No new price construction at all -- this is the historical
    world's own arrays, tagged, so it is trivially distance-exempt (its
    descriptor vector is identical to its anchor's by construction)."""
    return {**anchor_world, "family": "execution"}


# ---------------------------------------------------------------------------
# Shock: exactly 3 donor-gap relocations on the historical anchor's own path
# ---------------------------------------------------------------------------

def shock_world(
    anchor_world: Mapping[str, object], seed: int,
    donor_pool: Sequence[tuple], extrema: tuple[float, float],
) -> dict:
    """donor_pool: [(asset_index_in_ASSET_UNIVERSE, gap_value), ...] drawn
    from pooled DEVELOPMENT gap exceedances (the same pool tau is calibrated
    from). Destinations are eligible (asset, session) pairs within the
    anchor's OWN scored region, excluding session 0 (apply_shock_event
    refuses a non-scored, non-first destination)."""
    scored_start = anchor_world["scored_start_index"]
    n_sessions = len(anchor_world["assets"][ASSET_UNIVERSE[0]]["adjusted_close"])
    scored_length = n_sessions - scored_start
    eligible_destinations = [
        (a_index, session) for a_index in range(len(ASSET_UNIVERSE)) for session in range(1, scored_length)
    ]
    rng = random.Random(seed)
    donors, destinations = draw_shock_events(rng, donor_pool, eligible_destinations)

    asset_rows = {
        symbol: [
            {field: anchor_world["assets"][symbol][field][scored_start + i] for field in
             ("raw_open", "raw_high", "raw_low", "raw_close")}
            | {"adjusted_close": anchor_world["assets"][symbol]["adjusted_close"][scored_start + i],
               "dividend_cents": 0.0}
            for i in range(scored_length)
        ]
        for symbol in ASSET_UNIVERSE
    }
    for (donor_asset_index, gap), (dest_asset_index, dest_session) in zip(donors, destinations):
        assert_within_development_extrema(gap, extrema)
        dest_symbol = ASSET_UNIVERSE[dest_asset_index]
        apply_shock_event(asset_rows[dest_symbol], dest_session, gap)

    world = {
        "assets": {
            symbol: {
                "adjusted_close": (
                    anchor_world["assets"][symbol]["adjusted_close"][:scored_start]
                    + [r["adjusted_close"] for r in asset_rows[symbol]]
                ),
                "raw_open": (
                    anchor_world["assets"][symbol]["raw_open"][:scored_start]
                    + [r["raw_open"] for r in asset_rows[symbol]]
                ),
                "raw_high": (
                    anchor_world["assets"][symbol]["raw_high"][:scored_start]
                    + [r["raw_high"] for r in asset_rows[symbol]]
                ),
                "raw_low": (
                    anchor_world["assets"][symbol]["raw_low"][:scored_start]
                    + [r["raw_low"] for r in asset_rows[symbol]]
                ),
                "raw_close": (
                    anchor_world["assets"][symbol]["raw_close"][:scored_start]
                    + [r["raw_close"] for r in asset_rows[symbol]]
                ),
                "corporate_action": anchor_world["assets"][symbol]["corporate_action"],
            }
            for symbol in ASSET_UNIVERSE
        }
    }
    world["scored_start_index"] = scored_start
    world["family"] = "shock"
    world["seed"] = seed
    return world


# ---------------------------------------------------------------------------
# Admission: descriptor + distance gate, seed-stream rejection/replacement
# ---------------------------------------------------------------------------

def build_synthetic_family(
    family: str, *, slot_count: int, initial_seeds: Sequence[int],
    build_candidate, reference: Mapping[str, dict], admitted_reference_vectors: list[list[float]],
    tau: float,
    consumed_indices: Sequence[int] | None = None, used_seeds: Sequence[int] | None = None,
    accepted: Sequence[dict] | None = None, attempts: Sequence[dict] | None = None,
) -> dict:
    """Attempts each of the family's frozen initial seeds in order (indices
    1-4); on structural or diversity rejection, advances the deterministic
    replacement stream (world_seed_stream.draw_next) and retries -- never
    performance-driven, exactly the frozen replacement rule. Returns the
    accepted worlds plus a full attempt log (accepted and rejected) --
    rejected attempts are evidence, never deleted.

    Resumable for expansion (handoff §50): pass `consumed_indices`/
    `used_seeds`/`accepted` from a prior call's own return values (and
    `initial_seeds=[]`, since those are already consumed) with a larger
    `slot_count` to add more worlds continuing the SAME deterministic
    stream, rather than re-deriving the family from scratch."""
    accepted = list(accepted) if accepted is not None else []
    attempts = list(attempts) if attempts is not None else []
    if consumed_indices is None:
        consumed_indices = list(FROZEN_INDICES)
        used_seeds = list(initial_seeds)
        remaining_initial = list(enumerate(initial_seeds, start=1))
    else:
        consumed_indices = list(consumed_indices)
        used_seeds = list(used_seeds) if used_seeds is not None else []
        remaining_initial = []

    while len(accepted) < slot_count:
        if remaining_initial:
            stream_index, seed = remaining_initial.pop(0)
        else:
            draw = draw_next(family, consumed_indices, used_seeds)
            stream_index, seed = draw["stream_index"], draw["seed"]
            used_seeds.append(seed)
        consumed_indices = list(set(consumed_indices) | {stream_index})

        try:
            candidate = build_candidate(seed)
            candidate_scored = scored_only(candidate)
            vector = world_descriptor_vector(candidate_scored, tau)
            passes = world_distance_admission(
                family, vector, admitted_reference_vectors, reference
            )
        except FitnessV2Error as exc:
            attempts.append({
                "family": family, "stream_index": stream_index, "seed": seed,
                "accepted": False, "reason": str(exc),
            })
            continue

        if not passes:
            attempts.append({
                "family": family, "stream_index": stream_index, "seed": seed,
                "accepted": False, "reason": "failed_market_distance_admission",
            })
            continue

        attempts.append({
            "family": family, "stream_index": stream_index, "seed": seed, "accepted": True,
        })
        accepted.append(candidate)
        admitted_reference_vectors.append(vector)

    return {
        "family": family, "worlds": accepted, "attempts": attempts,
        "consumed_indices": consumed_indices, "used_seeds": used_seeds,
    }


class ExpansionHeld(WorldBankError):
    """Raised when a family has already reached the frozen expansion
    ceiling -- handoff §50: "Continue until: 32 synthetic worlds. Then hold
    further world-bank expansion pending later Rick GO." Not a structural or
    diversity rejection; the caller must not treat this as retryable."""


def expand_family(
    family: str, prior_state: Mapping[str, object], build_candidate,
    reference: Mapping[str, dict], admitted_reference_vectors: list[list[float]], tau: float,
) -> dict:
    """One expansion step (handoff §50): after a complete campaign batch,
    add exactly one more world to `family`, continuing its existing
    deterministic seed stream from `prior_state` (a previous build_synthetic_
    family() or expand_family() return value) rather than re-deriving the
    family from scratch. Never performance-driven -- expansion happens
    unconditionally after a batch completes, not because a family "needs"
    more worlds. Raises ExpansionHeld, not a structural rejection, once the
    family already holds MAX_WORLDS_PER_FAMILY (8, i.e. 32 total / 4
    families) -- world-bank growth then holds pending a new Rick GO."""
    existing = prior_state["worlds"]
    if len(existing) >= MAX_WORLDS_PER_FAMILY:
        raise ExpansionHeld(
            f"{family} already holds the frozen ceiling of {MAX_WORLDS_PER_FAMILY} "
            "synthetic worlds; expansion holds pending a new Rick GO"
        )
    return build_synthetic_family(
        family, slot_count=len(existing) + 1, initial_seeds=[],
        build_candidate=build_candidate, reference=reference,
        admitted_reference_vectors=admitted_reference_vectors, tau=tau,
        consumed_indices=prior_state["consumed_indices"], used_seeds=prior_state["used_seeds"],
        accepted=existing, attempts=prior_state["attempts"],
    )
