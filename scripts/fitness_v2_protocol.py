"""TBOTS Fitness V2 pre-result protocol completion (Rick's GO, 2026-09-26, §3).

Implements the thirteen frozen conventions (§3 #0-#12) resolving the
"Unresolved" rows of FITNESS_V2_SCIENTIFIC_REVIEW_STOP_20260926.md: world
descriptors, DEVELOPMENT reference calibration, distance admission, and the
Distributional/Sequence/Shock world generators. Execution needs no price
generator here -- it matches its historical anchor exactly (warm-up
amendment) and is distance-exempt (fitness_v2_admission.market_distance_passes).

Reuses, never duplicates: fitness_v2.py's contract algebra,
fitness_v2_formula_definitions.py's trend/OC1/source-guard (already resolved,
not reopened here), and fitness_v2_admission.py's market-distance predicate.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

from fitness_v2 import FitnessV2Error, PARAMETER_FREEZE_ID, content_identity
from fitness_v2_formula_definitions import (
    DEFINITIONS_ID, trend_strength, validate_development_source,
)
from fitness_v2_warmup import AMENDMENT_ID as WARMUP_AMENDMENT_ID
from fitness_v2_admission import market_distance_passes

EXECUTION_DIVERSITY_AMENDMENT_ID = (
    "fitness_v2_execution_diversity_amendment_"
    "4ef367db44be987a59e41547b485decbfb8c7d9d0b75477490f9221a59b76fef"
)

ASSET_UNIVERSE = ("DBC", "EEM", "EFA", "GLD", "IEF", "SPY", "TLT", "VNQ")

# Own scoped boundary per the GO -- distinct from fitness_v2.DEVELOPMENT_START
# (2007-01-01), which remains load-bearing for the frozen parameter-freeze
# and world-bank validators and must not be touched.
DEVELOPMENT_START = "2007-02-07"
DEVELOPMENT_END = "2018-12-31"

DESCRIPTOR_WINDOW = 252
CORRELATION_WINDOW = 63
TURNOVER_LOOKBACK = 63
GAP_PERCENTILE = 95.0

H_SHAPE_STARTS = {"H1": "2007-02-07", "H2": "2011-02-07", "H3": "2015-02-07"}

COMPONENT_NAMES = tuple(sorted(
    ["volatility_aggregate", "dispersion", "correlation", "drawdown_depth",
     "drawdown_duration", "turnover", "gap_frequency", "gap_magnitude"]
    + [f"volatility_{a}" for a in ASSET_UNIVERSE]
    + [f"trend_{a}" for a in ASSET_UNIVERSE]
    + [f"autocorrelation_{a}" for a in ASSET_UNIVERSE]
))
assert len(COMPONENT_NAMES) == 32


# ---------------------------------------------------------------------------
# §3 #1 -- sample standard deviation, n-1, reject below 2 observations
# ---------------------------------------------------------------------------

def sample_stdev(values: Sequence[float]) -> float:
    values = [float(v) for v in values]
    if len(values) < 2:
        raise FitnessV2Error("sample standard deviation requires at least 2 observations")
    return statistics.stdev(values)


# ---------------------------------------------------------------------------
# §3 #2 -- adjusted-close simple returns
# ---------------------------------------------------------------------------

def adjusted_simple_returns(adjusted_closes: Sequence[float]) -> list[float]:
    prices = [float(v) for v in adjusted_closes]
    if len(prices) < 2:
        raise FitnessV2Error("returns require at least two adjusted-close prices")
    for price in prices:
        if not math.isfinite(price) or price <= 0:
            raise FitnessV2Error("nonfinite or nonpositive adjusted-close price")
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


# ---------------------------------------------------------------------------
# §3 #3 -- equal-weight, daily-rebalanced descriptor portfolio
# ---------------------------------------------------------------------------

def equal_weight_portfolio_returns(returns_matrix: Sequence[Sequence[float]]) -> list[float]:
    result = []
    for row in returns_matrix:
        if len(row) != len(ASSET_UNIVERSE):
            raise FitnessV2Error("equal-weight portfolio requires all eight assets every session")
        result.append(statistics.mean(row))
    return result


def equal_weight_portfolio_equity(returns_matrix: Sequence[Sequence[float]]) -> list[float]:
    equity = [1.0]
    for r in equal_weight_portfolio_returns(returns_matrix):
        equity.append(equity[-1] * (1.0 + r))
    return equity


# ---------------------------------------------------------------------------
# §3 #3b -- cross-sectional dispersion (per-session sample std across assets)
# ---------------------------------------------------------------------------

def cross_sectional_dispersion_series(returns_matrix: Sequence[Sequence[float]]) -> list[float]:
    result = []
    for row in returns_matrix:
        if len(row) != len(ASSET_UNIVERSE):
            raise FitnessV2Error("cross-sectional dispersion requires all eight assets every session")
        result.append(sample_stdev(row))
    return result


# ---------------------------------------------------------------------------
# §3 #4 -- cross-asset correlation, 63-session windows, nested median
# ---------------------------------------------------------------------------

def _pearson_or_none(x: Sequence[float], y: Sequence[float]) -> float | None:
    mx, my = statistics.mean(x), statistics.mean(y)
    cx = [v - mx for v in x]
    cy = [v - my for v in y]
    sx = math.fsum(v * v for v in cx)
    sy = math.fsum(v * v for v in cy)
    if sx == 0 or sy == 0:
        return None
    return math.fsum(a * b for a, b in zip(cx, cy)) / math.sqrt(sx * sy)


def correlation_component(returns_window: Sequence[Sequence[float]]) -> float:
    n_assets = len(ASSET_UNIVERSE)
    window_medians = []
    for start in range(len(returns_window) - CORRELATION_WINDOW + 1):
        block = returns_window[start:start + CORRELATION_WINDOW]
        columns = [[row[i] for row in block] for i in range(n_assets)]
        pairs = []
        for i in range(n_assets):
            for j in range(i + 1, n_assets):
                corr = _pearson_or_none(columns[i], columns[j])
                if corr is None:
                    raise FitnessV2Error("zero-variance asset in a 63-session correlation window")
                pairs.append(corr)
        window_medians.append(statistics.median(pairs))
    if not window_medians:
        raise FitnessV2Error("insufficient sessions for one complete 63-session correlation window")
    return statistics.median(window_medians)


# ---------------------------------------------------------------------------
# §3 #5 -- drawdown depth and normalized duration on the §3 equity curve
# ---------------------------------------------------------------------------

def drawdown_depth_duration(equity_series: Sequence[float]) -> tuple[float, float]:
    if len(equity_series) < 2:
        raise FitnessV2Error("drawdown requires at least one return in the equity curve")
    peak = equity_series[0]
    depth = 0.0
    max_run = 0
    current_run = 0
    for value in equity_series[1:]:
        if value > peak:
            peak = value
        depth = max(depth, 1.0 - value / peak)
        if value < peak:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0
    n_returns = len(equity_series) - 1
    return depth, max_run / n_returns


# ---------------------------------------------------------------------------
# §3 #6 -- turnover opportunity: M63 leader momentum, lexicographic tie-break
# ---------------------------------------------------------------------------

def turnover_component(adjusted_close_window: Sequence[Sequence[float]]) -> float:
    """adjusted_close_window: rows=sessions in this window, cols=ASSET_UNIVERSE order."""
    n = len(adjusted_close_window)
    leaders: list[str | None] = [None] * n
    for i in range(TURNOVER_LOOKBACK, n):
        momenta = []
        for a_idx, symbol in enumerate(ASSET_UNIVERSE):
            p_t = adjusted_close_window[i][a_idx]
            p_prior = adjusted_close_window[i - TURNOVER_LOOKBACK][a_idx]
            momenta.append((p_t / p_prior - 1.0, symbol))
        momenta.sort(key=lambda pair: (-pair[0], pair[1]))
        leaders[i] = momenta[0][1]
    changes = 0
    pairs = 0
    for i in range(TURNOVER_LOOKBACK + 1, n):
        if leaders[i] is not None and leaders[i - 1] is not None:
            pairs += 1
            if leaders[i] != leaders[i - 1]:
                changes += 1
    if pairs == 0:
        raise FitnessV2Error("insufficient sessions for one turnover-opportunity window")
    return changes / pairs


# ---------------------------------------------------------------------------
# §3 #7 -- return autocorrelation, lag-1, per asset, reject on zero variance
# ---------------------------------------------------------------------------

def autocorrelation_component(returns_window: Sequence[float]) -> float:
    if len(returns_window) < 3:
        raise FitnessV2Error("insufficient returns for one lag-1 autocorrelation window")
    corr = _pearson_or_none(list(returns_window[:-1]), list(returns_window[1:]))
    if corr is None:
        raise FitnessV2Error("zero-variance asset returns in an autocorrelation window")
    return corr


# ---------------------------------------------------------------------------
# §3 #8 -- gap descriptor: pooled DEVELOPMENT threshold, frequency, magnitude
# ---------------------------------------------------------------------------

def valid_gaps(gap_rows: Sequence[Mapping[str, object]]) -> list[float]:
    """Each row: {'open', 'prev_close', 'corporate_action'} for one asset-session."""
    gaps = []
    for row in gap_rows:
        if row.get("corporate_action"):
            continue
        open_price = row["open"]
        prev_close = row["prev_close"]
        if (not isinstance(open_price, (int, float)) or isinstance(open_price, bool)
                or not isinstance(prev_close, (int, float)) or isinstance(prev_close, bool)):
            raise FitnessV2Error("gap prices must be numeric")
        if not math.isfinite(open_price) or not math.isfinite(prev_close):
            continue
        if open_price <= 0 or prev_close <= 0:
            continue
        gaps.append(open_price / prev_close - 1.0)
    return gaps


def _percentile_linear(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile, numpy's default ('type 7')."""
    if not sorted_values:
        raise FitnessV2Error("percentile requires at least one observation")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_values[int(lo)])
    frac = rank - lo
    return sorted_values[int(lo)] + (sorted_values[int(hi)] - sorted_values[int(lo)]) * frac


def gap_threshold_tau(pooled_development_gaps: Sequence[float]) -> float:
    abs_gaps = sorted(abs(g) for g in pooled_development_gaps)
    return _percentile_linear(abs_gaps, GAP_PERCENTILE)


def gap_component(gap_rows: Sequence[Mapping[str, object]], tau: float) -> tuple[float, float]:
    gaps = valid_gaps(gap_rows)
    if not gaps:
        raise FitnessV2Error("no valid gap observations in this window")
    abs_gaps = [abs(g) for g in gaps]
    exceedances = [g for g in abs_gaps if g > tau]
    if not exceedances:
        raise FitnessV2Error("zero gap exceedances in this window")
    frequency = len(exceedances) / len(gaps)
    magnitude = statistics.median(exceedances)
    return frequency, magnitude


# ---------------------------------------------------------------------------
# §3 #0 -- volatility (§3 #1's convention) and the uniform 252-window horizon
# ---------------------------------------------------------------------------

def volatility_component(returns_window: Sequence[float]) -> float:
    return sample_stdev(returns_window)


def validate_world_shape(world: Mapping[str, Mapping[str, Sequence]]) -> int:
    assets = world.get("assets")
    if not isinstance(assets, Mapping) or set(assets) != set(ASSET_UNIVERSE):
        raise FitnessV2Error("world must cover exactly the eight-asset universe")
    lengths = set()
    for symbol in ASSET_UNIVERSE:
        row = assets[symbol]
        for field in ("adjusted_close", "raw_open", "raw_close", "corporate_action"):
            if field not in row:
                raise FitnessV2Error(f"asset {symbol} is missing required field {field}")
        lengths.add(len(row["adjusted_close"]))
    if len(lengths) != 1:
        raise FitnessV2Error("all eight assets must share one synchronized scored session index")
    n_sessions = lengths.pop()
    if n_sessions - 1 < DESCRIPTOR_WINDOW:
        raise FitnessV2Error("world is shorter than one complete 252-return descriptor window")
    return n_sessions


def _component_window_values(world: Mapping[str, Mapping[str, Sequence]], tau: float) -> dict:
    """Rule #0, corrected boundary: windows are 252 consecutive derived
    observations (returns/gaps), never 252 consecutive sessions -- a return
    or a gap at session t needs session t-1 as its base, so the first
    derived observation is dated at the SECOND DEVELOPMENT session (base =
    the first DEVELOPMENT session's own close). No row before the world's
    own first session is ever read: adjusted_simple_returns/gap rows are
    built only from prices already inside `assets[...]`, which the caller
    populates from DEVELOPMENT_START onward and no earlier. A 252-window of
    prices spans 253 prices (252 returns); trend and turnover use that
    253-price span directly, since both need raw prices, not returns.
    Same code path for candidates, historical anchors, and the reference."""
    assets = world["assets"]
    n_sessions = validate_world_shape(world)
    n_returns = n_sessions - 1
    asset_returns = {
        symbol: adjusted_simple_returns(assets[symbol]["adjusted_close"])
        for symbol in ASSET_UNIVERSE
    }
    returns_matrix = [
        [asset_returns[symbol][i] for symbol in ASSET_UNIVERSE]
        for i in range(n_returns)
    ]
    gap_rows_by_asset = {
        symbol: [
            {
                "open": assets[symbol]["raw_open"][i],
                "prev_close": assets[symbol]["raw_close"][i - 1],
                "corporate_action": assets[symbol]["corporate_action"][i],
            }
            for i in range(1, n_sessions)
        ]
        for symbol in ASSET_UNIVERSE
    }

    per_component: dict[str, list[float]] = {name: [] for name in COMPONENT_NAMES}
    for r_start in range(0, n_returns - DESCRIPTOR_WINDOW + 1):
        r_end = r_start + DESCRIPTOR_WINDOW  # 252 consecutive returns [r_start, r_end)
        price_end = r_end + 1                # the 253 prices spanning those returns
        window_returns = returns_matrix[r_start:r_end]
        equity = equal_weight_portfolio_equity(window_returns)

        per_component["volatility_aggregate"].append(
            volatility_component(equal_weight_portfolio_returns(window_returns))
        )
        for a_idx, symbol in enumerate(ASSET_UNIVERSE):
            asset_window_returns = [row[a_idx] for row in window_returns]
            per_component[f"volatility_{symbol}"].append(
                volatility_component(asset_window_returns)
            )
            per_component[f"trend_{symbol}"].append(
                trend_strength(assets[symbol]["adjusted_close"][r_start:price_end])
            )
            per_component[f"autocorrelation_{symbol}"].append(
                autocorrelation_component(asset_window_returns)
            )
        per_component["dispersion"].append(
            statistics.median(cross_sectional_dispersion_series(window_returns))
        )
        per_component["correlation"].append(correlation_component(window_returns))
        depth, duration = drawdown_depth_duration(equity)
        per_component["drawdown_depth"].append(depth)
        per_component["drawdown_duration"].append(duration)
        per_component["turnover"].append(turnover_component([
            [assets[symbol]["adjusted_close"][idx] for symbol in ASSET_UNIVERSE]
            for idx in range(r_start, price_end)
        ]))
        window_gap_rows = [
            gap_rows_by_asset[symbol][r_start:r_end]
            for symbol in ASSET_UNIVERSE
        ]
        window_gap_rows = [row for rows in window_gap_rows for row in rows]
        frequency, magnitude = gap_component(window_gap_rows, tau)
        per_component["gap_frequency"].append(frequency)
        per_component["gap_magnitude"].append(magnitude)

    if not per_component[COMPONENT_NAMES[0]]:
        raise FitnessV2Error("world produced zero complete 252-return descriptor windows")
    return per_component


def world_descriptor_vector(world: Mapping[str, Mapping[str, Sequence]], tau: float) -> dict:
    windows = _component_window_values(world, tau)
    return {name: statistics.median(values) for name, values in windows.items()}


def development_reference_calibration(
    development_worlds: Sequence[Mapping[str, Mapping[str, Sequence]]], tau: float
) -> dict:
    """§3 #12: any zero-MAD reference component aborts world-bank construction."""
    if not development_worlds:
        raise FitnessV2Error("reference calibration requires at least one DEVELOPMENT world")
    pooled: dict[str, list[float]] = {name: [] for name in COMPONENT_NAMES}
    for world in development_worlds:
        windows = _component_window_values(world, tau)
        for name, values in windows.items():
            pooled[name].extend(values)
    median = {name: statistics.median(values) for name, values in pooled.items()}
    mad = {}
    for name, values in pooled.items():
        deviation = statistics.median(abs(v - median[name]) for v in values)
        if deviation == 0:
            raise FitnessV2Error(
                f"zero-MAD DEVELOPMENT reference component (§3 #12 abort): {name}"
            )
        mad[name] = deviation
    return {"median": median, "mad": mad}


def normalized_descriptor_vector(vector: Mapping[str, float], reference: Mapping[str, dict]) -> list[float]:
    median, mad = reference["median"], reference["mad"]
    return [(vector[name] - median[name]) / mad[name] for name in COMPONENT_NAMES]


def world_distance_admission(
    family: str, candidate_vector: Mapping[str, float],
    reference_vectors: Sequence[Mapping[str, float]], reference: Mapping[str, dict],
) -> bool:
    candidate_z = normalized_descriptor_vector(candidate_vector, reference)
    reference_z = [normalized_descriptor_vector(v, reference) for v in reference_vectors]
    return market_distance_passes(family, candidate_z, reference_z)


OHLC_FIELDS = ("raw_open", "raw_high", "raw_low", "raw_close")


def _valid_ohlc_geometry(row: Mapping[str, float]) -> bool:
    return row["raw_low"] <= row["raw_open"] <= row["raw_high"] and \
        row["raw_low"] <= row["raw_close"] <= row["raw_high"]


# ---------------------------------------------------------------------------
# §3 #9 -- Distributional (and Sequence's first join) reconstruction level
# ---------------------------------------------------------------------------

def reconstruct_distributional_path(
    anchor_start_row: Mapping[str, float], sampled_rows: Sequence[Mapping[str, float]]
) -> list[dict]:
    """anchor_start_row: real raw/adjusted close on the matching H-shape's first
    DEVELOPMENT session (generated warm-up bar 0). sampled_rows: ordered
    original DEVELOPMENT rows drawn by the family's bootstrap/segment process,
    each carrying its OWN prior-day raw/adjusted close and dividend yield --
    used only to derive that day's own ratios, never its absolute level."""
    reconstructed = [{
        "raw_open": float(anchor_start_row["raw_open"]),
        "raw_high": float(anchor_start_row["raw_high"]),
        "raw_low": float(anchor_start_row["raw_low"]),
        "raw_close": float(anchor_start_row["raw_close"]),
        "adjusted_close": float(anchor_start_row["adjusted_close"]),
        "dividend_cents": 0.0,
    }]
    for sample in sampled_rows:
        prior_raw_close = reconstructed[-1]["raw_close"]
        prior_adjusted_close = reconstructed[-1]["adjusted_close"]
        prev_raw_close = sample["prev_raw_close"]
        prev_adjusted_close = sample["prev_adjusted_close"]
        if not (math.isfinite(prev_raw_close) and prev_raw_close > 0
                and math.isfinite(prev_adjusted_close) and prev_adjusted_close > 0):
            raise FitnessV2Error("sampled observation has an invalid prior price")
        adjusted_return = sample["adjusted_close"] / prev_adjusted_close - 1.0
        new_row = {
            field: prior_raw_close * (sample[field] / prev_raw_close)
            for field in OHLC_FIELDS
        }
        new_row["adjusted_close"] = prior_adjusted_close * (1.0 + adjusted_return)
        new_row["dividend_cents"] = float(sample.get("dividend_yield") or 0.0) * prior_raw_close
        if not _valid_ohlc_geometry(new_row):
            raise FitnessV2Error("reconstructed OHLC geometry is invalid")
        reconstructed.append(new_row)
    return reconstructed


def stationary_bootstrap_indices(
    rng, source_length: int, target_length: int, mean_block_length: float = 21.0
) -> list[int]:
    """Common index across all eight assets (caller reuses the same draw for
    each asset). Boundary redraw, no wrap: a block never reads the row before
    DEVELOPMENT_START -- the pool excludes source index 0 (it has no prior
    DEVELOPMENT session for the sampled day's own ratio) and never continues
    past source_length-1. Final truncation to exactly target_length."""
    if source_length < 2 or target_length < 1:
        raise FitnessV2Error("stationary bootstrap requires source_length>=2 and target_length>=1")
    continue_probability = 1.0 - 1.0 / mean_block_length
    indices: list[int] = []
    while len(indices) < target_length:
        idx = rng.randrange(1, source_length)
        indices.append(idx)
        while len(indices) < target_length and rng.random() < continue_probability:
            if idx + 1 >= source_length:
                break
            idx += 1
            indices.append(idx)
    return indices[:target_length]


# ---------------------------------------------------------------------------
# §3 #10 -- Sequence: intact-segment copy, join scaling, start-0 eligible
# ---------------------------------------------------------------------------

def scale_segment(segment_rows: Sequence[Mapping[str, float]], scale_factor: float) -> list[dict]:
    scaled = []
    for row in segment_rows:
        scaled_row = {field: row[field] * scale_factor for field in OHLC_FIELDS}
        scaled_row["adjusted_close"] = row["adjusted_close"] * scale_factor
        scaled_row["dividend_cents"] = row.get("dividend_cents", 0.0) * scale_factor
        scaled.append(scaled_row)
    return scaled


def join_sequence_segments(
    segments: Sequence[Sequence[Mapping[str, float]]], anchor_start_row: Mapping[str, float]
) -> list[dict]:
    """The anchor (§9) stands in as "segment 0": every segment, including the
    first, is scaled so its own first raw_open continues from whatever the
    running series' last raw_close is -- a zero join gap at every boundary."""
    if not segments:
        raise FitnessV2Error("sequence world requires at least one segment")
    joined = [{
        "raw_open": float(anchor_start_row["raw_open"]),
        "raw_high": float(anchor_start_row["raw_high"]),
        "raw_low": float(anchor_start_row["raw_low"]),
        "raw_close": float(anchor_start_row["raw_close"]),
        "adjusted_close": float(anchor_start_row["adjusted_close"]),
        "dividend_cents": 0.0,
    }]
    for segment in segments:
        if not segment:
            raise FitnessV2Error("sequence segment must be non-empty")
        previous_close = joined[-1]["raw_close"]
        first_open = segment[0]["raw_open"]
        if not math.isfinite(first_open) or first_open <= 0:
            raise FitnessV2Error("sequence segment has an invalid first raw open")
        scale = previous_close / first_open
        joined.extend(scale_segment(segment, scale))
    return joined


def draw_sequence_segment_starts(
    rng, source_length: int, segment_length: int, n_segments: int
) -> list[int]:
    """Sorted, distinct starts without replacement; overlap between segments
    is allowed; start index 0 is eligible (no unauthorized restriction)."""
    max_start = source_length - segment_length
    if max_start < 0:
        raise FitnessV2Error("source is shorter than one intact segment")
    if n_segments > max_start + 1:
        raise FitnessV2Error("more segments requested than distinct starts exist")
    starts: set[int] = set()
    while len(starts) < n_segments:
        starts.add(rng.randrange(max_start + 1))
    return sorted(starts)


# ---------------------------------------------------------------------------
# §3 #11 -- Shock: exactly three donor-gap relocations, no same-asset restriction
# ---------------------------------------------------------------------------

def apply_shock_event(asset_rows: list[dict], destination_index: int, donor_gap: float) -> float:
    """Mutates asset_rows in place from destination_index through the end.
    Recomputes the live (possibly already-shocked) gap at destination_index
    immediately before scaling, so composed same-asset events remain correct
    regardless of application order. Returns the multiplier applied."""
    if destination_index <= 0 or destination_index >= len(asset_rows):
        raise FitnessV2Error("shock destination must be a scored, non-first session")
    live_open = asset_rows[destination_index]["raw_open"]
    live_prev_close = asset_rows[destination_index - 1]["raw_close"]
    if not (math.isfinite(live_open) and live_open > 0
            and math.isfinite(live_prev_close) and live_prev_close > 0):
        raise FitnessV2Error("shock destination has an invalid price")
    original_gap = live_open / live_prev_close - 1.0
    multiplier = (1.0 + donor_gap) / (1.0 + original_gap)
    for row in asset_rows[destination_index:]:
        for field in OHLC_FIELDS:
            row[field] *= multiplier
        row["adjusted_close"] *= multiplier
        row["dividend_cents"] = row.get("dividend_cents", 0.0) * multiplier
    return multiplier


def draw_shock_events(rng, donor_pool: Sequence[tuple], eligible_destinations: Sequence[tuple]):
    """donor_pool / eligible_destinations: pre-sorted canonically (asset, session)
    by the caller. Draws exactly three distinct donors (no replacement) and
    three distinct destinations."""
    if len(donor_pool) < 3 or len(eligible_destinations) < 3:
        raise FitnessV2Error("insufficient donors or eligible destinations for a shock world")
    donor_idx = rng.sample(range(len(donor_pool)), 3)
    dest_idx = rng.sample(range(len(eligible_destinations)), 3)
    return (
        [donor_pool[i] for i in donor_idx],
        [eligible_destinations[i] for i in dest_idx],
    )


def gap_extrema(pooled_development_gaps: Sequence[float]) -> tuple[float, float]:
    return min(pooled_development_gaps), max(pooled_development_gaps)


def assert_within_development_extrema(gap: float, extrema: tuple[float, float]) -> None:
    low, high = extrema
    if not low <= gap <= high:
        raise FitnessV2Error("relocated shock gap exceeds DEVELOPMENT-observed extrema")


# ---------------------------------------------------------------------------
# Execution: no price transformation -- matches its historical anchor exactly
# ---------------------------------------------------------------------------

def execution_skip_mask(rng, n_sessions: int, skip_probability: float = 0.05) -> list[bool]:
    """Deterministic seeded skipped-fill stress: at most one skipped session
    per affected fill is enforced by the caller applying this mask to fills,
    not by this draw (a single True here marks one candidate skip decision
    per scored session)."""
    return [rng.random() < skip_probability for _ in range(n_sessions)]


# ---------------------------------------------------------------------------
# §4.3 -- canonical, content-addressed complete-protocol manifest
# ---------------------------------------------------------------------------

DEVELOPMENT_BOUNDARY_RULE = (
    "No row before the world's own first DEVELOPMENT session is read, for any "
    "purpose, ever, in calibration or descriptor computation. The Execution/"
    "Shock real-warm-up amendment governs WORLD CONSTRUCTION only, not "
    "calibration or descriptors -- it grants no exception here. The return "
    "series for asset i begins at the world's second session (first return's "
    "base = the world's own first-session close); an N-session world yields "
    "exactly N-1 returns. A 252-window means 252 consecutive RETURNS (253 "
    "consecutive prices), never 252 consecutive sessions -- an N-session "
    "world yields exactly N-1-252+1 = N-252 complete 252-return windows "
    "(H1: 1008 sessions -> 1007 returns -> 756 windows). Gaps need "
    "close[t-1], so the first gap is also at the world's second session. "
    "M63 turnover's first defined value is at the 64th session within a "
    "window; leader-change denominators count only consecutive pairs where "
    "both leaders are defined (§3 #6)."
)

RULE_SUMMARY = {
    "0_candidate_descriptor_horizon": (
        "Component-wise median over every complete 252-return window (stride 1) "
        "of the world's own scored sessions; historical anchors and the "
        "DEVELOPMENT reference use the identical procedure over their own shape. "
        "See development_boundary_rule for the exact returns-vs-sessions boundary."
    ),
    "1_sample_standard_deviation": "Sample stdev, n-1; reject below 2 observations.",
    "2_returns": "Adjusted-close simple returns: adjusted[t]/adjusted[t-1]-1.",
    "3_equal_weight_portfolio": "Daily-rebalanced equal-weight mean return across the eight assets; E0=1.0.",
    "3b_cross_sectional_dispersion": "Per-session sample stdev across the eight assets; component = median over the window.",
    "4_cross_asset_correlation": "63-session windows, median of the 28 off-diagonal Pearson correlations per window, nested median across windows; reject zero-variance asset.",
    "5_drawdown_structure": "Depth/normalized-duration on the §3 equity curve; peak includes E0; a run underwater at window end counts through the end.",
    "6_turnover_opportunity": "M63 leader momentum, lexicographic tie-break; leader-change frequency over defined consecutive pairs.",
    "7_return_autocorrelation": "Per-asset lag-1 Pearson; reject zero variance.",
    "8_gap_descriptor": (
        "Raw open/prior-close gap, corporate-action and invalid-price exclusion; "
        "tau = pooled eight-asset DEVELOPMENT 95th percentile of |gap| (linear "
        "interpolation, numpy default); reject zero exceedances."
    ),
    "9_distributional_reconstruction_level": (
        "Generated warm-up bar 0 = real raw/adjusted close on the matching "
        "H-shape's first DEVELOPMENT trading session on or after its declared "
        "start; subsequent bars evolve by the sampled observation's own ratios."
    ),
    "10_sequence_joins": (
        "Intact 63-session segments, one scale constant per segment; the anchor "
        "stands in as segment 0 so every segment (including the first) is "
        "scaled to zero join gap; start index 0 eligible; overlap allowed."
    ),
    "11_shock_transformation": (
        "Exactly three donor-gap relocations from the DEVELOPMENT exceedance "
        "pool into three distinct eligible destinations; multiplies one "
        "asset's OHLC/adjusted/dividend from the destination through the end "
        "of the world; no same-asset donor/destination restriction."
    ),
    "12_zero_mad_reference_component": (
        "Any of the 32 DEVELOPMENT reference components with MAD==0 aborts "
        "world-bank construction; no sentinel substitution."
    ),
}


def build_complete_protocol(
    *, tau: float, gap_extrema_bounds: tuple[float, float], reference: Mapping[str, dict],
    h_shape_dates: Mapping[str, Mapping[str, object]],
    h_shape_anchor_rows: Mapping[str, Mapping[str, Mapping[str, float]]],
    stream_counts: Mapping[str, Mapping[str, object]],
    boundary_proof: Mapping[str, Mapping[str, object]],
) -> dict:
    return {
        "schema_version": 2,
        "authority": (
            "Rick: GO — Claude Code on NODE: TBOTS Fitness V2 pre-result "
            "protocol completion, 2026-09-26, and GO ADDENDUM A — Unattended "
            "operation, 2026-09-26 (§2's blocking development-boundary "
            "correction and §3's positive post-2018/pre-2007-02-07 filter proof)"
        ),
        "depends_on": {
            "parameter_freeze": PARAMETER_FREEZE_ID,
            "formula_definitions": DEFINITIONS_ID,
            "real_warmup_amendment": WARMUP_AMENDMENT_ID,
            "execution_diversity_amendment": EXECUTION_DIVERSITY_AMENDMENT_ID,
        },
        "development_boundary": {"start": DEVELOPMENT_START, "end": DEVELOPMENT_END},
        "development_boundary_rule": DEVELOPMENT_BOUNDARY_RULE,
        "h_shapes": {
            "declared_starts": H_SHAPE_STARTS,
            "dates": dict(h_shape_dates),
            "anchor_rows": dict(h_shape_anchor_rows),
            "stream_counts": dict(stream_counts),
        },
        "post_development_filter_proof": dict(boundary_proof),
        "asset_universe": list(ASSET_UNIVERSE),
        "component_names": list(COMPONENT_NAMES),
        "rules": RULE_SUMMARY,
        "gap_threshold_tau": tau,
        "gap_extrema": list(gap_extrema_bounds),
        "reference_median": dict(reference["median"]),
        "reference_mad": dict(reference["mad"]),
        "complete_protocol_ready": True,
    }


def complete_protocol_manifest(**kwargs) -> dict:
    content = build_complete_protocol(**kwargs)
    manifest_id = content_identity("fitness_v2_complete_protocol_", content)
    return {"manifest_id": manifest_id, "content": content}


def validate_complete_protocol(envelope: Mapping[str, object]) -> str:
    content = envelope.get("content")
    declared = envelope.get("manifest_id")
    if not isinstance(content, Mapping):
        raise FitnessV2Error("complete-protocol envelope requires a content object")
    actual = content_identity("fitness_v2_complete_protocol_", content)
    if declared != actual:
        raise FitnessV2Error("complete-protocol manifest identity mismatch")
    if content.get("complete_protocol_ready") is not True:
        raise FitnessV2Error("complete-protocol manifest is not marked ready")
    return actual
