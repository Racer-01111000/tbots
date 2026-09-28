"""Rick's exact trend/OC1 definitions and current source boundary only.

Not a complete executable protocol, descriptor builder, world generator, or
readiness gate. Earlier content-addressed receipts remain historical evidence.
"""
from __future__ import annotations

from datetime import date
import math
import statistics

from fitness_v2 import FitnessV2Error, content_identity

DEVELOPMENT_START = "2007-02-07"
DEVELOPMENT_END = "2018-12-31"
DEFINITIONS = {
    "schema_version": 1,
    "authority": "Rick: CONTINUING GO FITNESS V2 FINAL FORMULA DEFINITIONS and subsequent scientific review instruction",
    "checkpoint": "02bc2ad0e3624bad28096c7b9541dd1a520fc246",
    "trend": {
        "window_price_sessions": 63,
        "x": "0,1,...,62",
        "y": "ln(adjusted_close)",
        "strength": "abs(PearsonCorrelation(x,y))",
        "constant_y": 0.0,
        "invalid_price": "Any nonfinite or nonpositive price rejects candidate world",
        "incomplete_windows": "Do not contribute; no complete windows produces no descriptor value",
        "per_asset": "Median of strengths from every complete 63-session window in that world",
        "other_normalizations": "Prohibited: no t-statistic, raw slope or annualized slope",
    },
    "oc1": {
        "nonzero_development_mad": "(passive_sharpe-reference_median)/(1.4826*MAD)",
        "zero_development_mad": {"above_median": 4.0, "equal_median": 0.0, "below_median": -4.0},
        "zero_mad_per_world_anomaly": "passive_sharpe != reference_median",
        "family_aggregation": "Ordinary median of numeric z values, including signed zero-MAD sentinels",
        "family_anomaly": "family_median_z > +3.0; negative deviations cannot trigger favorable-passive OC1",
        "candidate_inputs": "None; supplied Sharpes must be the already-frozen candidate-independent passive comparator",
    },
    "source_boundary": {
        "generation_and_calibration": [DEVELOPMENT_START, DEVELOPMENT_END],
        "earlier_observations": "Only real historical-anchor warm-up under frozen S5A eligibility, including Execution/Shock exceptions; never Distributional/Sequence source, donors, or calibration",
        "precedence": "Later executable GO and explicit scientific-review clarification supersede earlier 2007-01-01 source metadata without rewriting it",
        "protected_post2018": "Prohibited",
    },
    "resolution_status": {
        "prior_trend_and_oc1_stop": "Resolved by Rick; old receipt retained",
        "execution_distance_and_execution_shock_warmup": "Remain resolved, unchanged",
        "complete_protocol_ready": False,
        "scope": "Exact formula/source amendment only; remaining descriptor/transformation conventions require authority",
    },
}
DEFINITIONS_ID = content_identity("fitness_v2_formula_definitions_", DEFINITIONS)


def validate_definitions(envelope):
    if envelope != {"manifest_id": DEFINITIONS_ID, "content": DEFINITIONS}:
        raise FitnessV2Error("formula/source amendment mismatch")
    return DEFINITIONS_ID


def validate_development_source(dates):
    """Fail closed on mixed warm-up/generation sources; never silently trim."""
    if not dates or list(dates) != sorted(set(dates)):
        raise FitnessV2Error("source dates must be nonempty, sorted, unique")
    for value in dates:
        try:
            canonical = date.fromisoformat(value).isoformat()
        except (TypeError, ValueError) as exc:
            raise FitnessV2Error("source date must be an ISO date") from exc
        if value != canonical or not DEVELOPMENT_START <= value <= DEVELOPMENT_END:
            raise FitnessV2Error("generation/calibration source outside 2007-02-07..2018-12-31")


def trend_window_strengths(prices):
    """One asset's adjusted closes; no incomplete-window contribution."""
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
        # math.fsum(...)/len(...) instead of statistics.mean(): this runs
        # inside a tens-of-millions-of-calls-per-candidate hot path (perf
        # incident 2026-09-28); no accuracy benefit from exact-Fraction
        # summation on ordinary float log-price data here.
        mean = math.fsum(y) / len(y)
        centered = [value - mean for value in y]
        yy = math.fsum(value * value for value in centered)
        numerator = math.fsum(a * b for a, b in zip(x, centered))
        # Pearson's exact range; clip only floating-point roundoff at endpoints.
        result.append(min(1.0, abs(numerator / math.sqrt(xx * yy))))
    return result


def trend_strength(prices):
    strengths = trend_window_strengths(prices)
    return statistics.median(strengths) if strengths else None


def passive_robust_z(sharpe, reference_median, mad):
    if not all(math.isfinite(value) for value in (sharpe, reference_median, mad)) or mad < 0:
        raise FitnessV2Error("invalid passive reference statistic")
    if mad:
        return (sharpe - reference_median) / (1.4826 * mad)
    return 4.0 if sharpe > reference_median else -4.0 if sharpe < reference_median else 0.0


def family_oc1(world_passive_sharpes, reference_passive_sharpes):
    if not world_passive_sharpes or not reference_passive_sharpes:
        raise FitnessV2Error("passive worlds and DEVELOPMENT reference required")
    if not all(math.isfinite(value) for value in [*world_passive_sharpes, *reference_passive_sharpes]):
        raise FitnessV2Error("nonfinite passive Sharpe")
    median = statistics.median(reference_passive_sharpes)
    mad = statistics.median(abs(value - median) for value in reference_passive_sharpes)
    z = [passive_robust_z(value, median, mad) for value in world_passive_sharpes]
    family_median = statistics.median(z)
    return {
        "reference_median": median, "reference_mad": mad, "world_z": z,
        "zero_mad_world_anomalous": [value != median for value in world_passive_sharpes] if mad == 0 else None,
        "family_median_z": family_median,
        "passive_comparator_anomalous": family_median > 3.0,
    }
