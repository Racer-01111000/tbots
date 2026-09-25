"""Fitness V2 / World League contract algebra.

This module implements the frozen mathematical and admission contract only.
It deliberately does not invent a world bank.  A campaign may call these
functions only after supplying an exact, content-addressed world manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence


class FitnessV2Error(ValueError):
    pass


FAMILIES = ("historical", "distributional", "execution", "sequence", "shock")
SYNTHETIC_FAMILIES = FAMILIES[1:]
FAMILY_WEIGHTS = {
    "historical": 0.40,
    "distributional": 0.20,
    "execution": 0.15,
    "sequence": 0.15,
    "shock": 0.10,
}
DEVELOPMENT_START = "2007-01-01"
DEVELOPMENT_END = "2018-12-31"
RETURN_DRAWDOWN_K = 3.0
EPSILON = 0.001
MATERIALITY = 0.005
ACTIVITY_FLOOR = 0.50
OC2_ACTIVITY_FLOOR = 0.75
OC2_SIGNAL_DELTA_LIMIT = 0.05
OC2_ABSOLUTE_MARGIN = 0.10


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_identity(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FitnessV2Error(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise FitnessV2Error(f"{name} must be finite")
    return result


def per_world_score(metrics: Mapping[str, object]) -> dict:
    """Apply Level 1 exactly, including inactivity-first short-circuiting."""
    order_count = metrics.get("order_count")
    if isinstance(order_count, bool) or not isinstance(order_count, int) or order_count < 0:
        raise FitnessV2Error("order_count must be a non-negative integer")
    if order_count == 0:
        return {
            "score": 0.0,
            "scaling_factor": None,
            "return_term": 0.0,
            "sharpe_term": 0.0,
            "short_circuited_inactive": True,
        }

    median_return = _finite_number(metrics["median_return"], "median_return")
    worst_drawdown = _finite_number(metrics["worst_drawdown"], "worst_drawdown")
    median_sharpe = _finite_number(metrics["median_sharpe"], "median_sharpe")
    halt_rate = _finite_number(metrics["halt_rate"], "halt_rate")
    median_turnover = _finite_number(metrics["median_turnover"], "median_turnover")
    cost_rate = _finite_number(
        metrics["median_transaction_cost_rate"], "median_transaction_cost_rate"
    )
    if worst_drawdown > 0:
        raise FitnessV2Error("worst_drawdown must be non-positive")
    if not 0 <= halt_rate <= 1:
        raise FitnessV2Error("halt_rate must be in [0,1]")
    if median_turnover < 0 or cost_rate < 0:
        raise FitnessV2Error("turnover and transaction-cost rate must be non-negative")

    if median_return <= 0:
        scaling = 1.0
    else:
        raw = median_return / (RETURN_DRAWDOWN_K * abs(worst_drawdown) + EPSILON)
        scaling = max(0.0, min(1.0, raw))
    return_term = 3.0 * median_return * scaling
    sharpe_term = 0.20 * median_sharpe * min(1.0, abs(median_return) / MATERIALITY)
    score = (
        return_term
        + sharpe_term
        - 2.0 * abs(worst_drawdown)
        - 0.25 * halt_rate
        - 0.02 * median_turnover
        - 5.0 * cost_rate
    )
    return {
        "score": score,
        "scaling_factor": scaling,
        "return_term": return_term,
        "sharpe_term": sharpe_term,
        "short_circuited_inactive": False,
    }


def family_score(scores: Sequence[float]) -> float:
    if not scores:
        raise FitnessV2Error("a world family cannot be empty")
    checked = [_finite_number(value, "world score") for value in scores]
    return min(checked) + 0.5 * statistics.mean(checked)


def performance_concentration(values: Sequence[float]) -> float:
    absolute = [abs(_finite_number(value, "family score")) for value in values]
    if len(absolute) < 2 or sum(absolute) == 0:
        return 0.0
    total = sum(absolute)
    hhi = sum((value / total) ** 2 for value in absolute)
    floor = 1.0 / len(absolute)
    return max(0.0, min(1.0, (hhi - floor) / (1.0 - floor)))


def validate_calibration_source(source: Mapping[str, object]) -> None:
    if source.get("lane") != "DEVELOPMENT":
        raise FitnessV2Error("calibration source must be DEVELOPMENT")
    if source.get("start") != DEVELOPMENT_START or source.get("end") != DEVELOPMENT_END:
        raise FitnessV2Error("calibration must be exactly DEVELOPMENT 2007-01-01..2018-12-31")


def validate_world_manifest(manifest: Mapping[str, object]) -> str:
    """Validate an exact bank; refuse design ranges and implicit choices."""
    if manifest.get("schema_version") != 1:
        raise FitnessV2Error("unsupported world-manifest schema")
    validate_calibration_source(manifest.get("calibration_source", {}))
    worlds = manifest.get("worlds")
    if not isinstance(worlds, list) or not worlds:
        raise FitnessV2Error("world manifest must contain an exact non-empty world list")
    seen: set[str] = set()
    family_counts = {family: 0 for family in FAMILIES}
    technique_seeds: dict[str, set[int]] = {}
    for world in worlds:
        if not isinstance(world, dict):
            raise FitnessV2Error("each world manifest entry must be an object")
        world_id = world.get("world_id")
        family = world.get("family")
        if not isinstance(world_id, str) or not world_id or world_id in seen:
            raise FitnessV2Error("world identifiers must be non-empty and unique")
        if family not in family_counts:
            raise FitnessV2Error(f"unknown world family: {family}")
        seen.add(world_id)
        family_counts[family] += 1
        provenance = world.get("provenance_class")
        if family == "historical" and provenance != "REAL_DEVELOPMENT":
            raise FitnessV2Error("historical worlds must be REAL_DEVELOPMENT")
        if family != "historical" and provenance != "SYNTHETIC_DEVELOPMENT":
            raise FitnessV2Error("synthetic families must be SYNTHETIC_DEVELOPMENT")
        validate_calibration_source(world.get("calibration_source", {}))
        if family != "historical" and not isinstance(world.get("deterministic_seed"), int):
            raise FitnessV2Error("synthetic worlds require an explicit deterministic seed")
        if family != "historical":
            technique = world.get("technique")
            if not isinstance(technique, str) or not technique:
                raise FitnessV2Error("synthetic worlds require an explicit technique")
            technique_seeds.setdefault(technique, set()).add(world["deterministic_seed"])
    if family_counts["historical"] != 3:
        raise FitnessV2Error("the bank requires exactly three historical worlds")
    if any(family_counts[family] < 3 for family in SYNTHETIC_FAMILIES):
        raise FitnessV2Error("minimum viable bank requires at least three worlds per synthetic family")
    if any(len(seeds) < 2 for seeds in technique_seeds.values()):
        raise FitnessV2Error("every synthetic technique requires at least two deterministic seeds")
    if abs(sum(FAMILY_WEIGHTS.values()) - 1.0) > 1e-12:
        raise FitnessV2Error("family weights do not sum to one")
    unsigned = dict(manifest)
    declared = unsigned.pop("manifest_id", None)
    actual = content_identity("fitness_v2_world_bank_", unsigned)
    if declared != actual:
        raise FitnessV2Error("world manifest identity mismatch")
    return actual


def _oc1_eligible(family_deltas: Mapping[str, float], anomalies: Mapping[str, bool]) -> bool:
    drivers = []
    for family in SYNTHETIC_FAMILIES:
        remaining = [value for name, value in family_deltas.items() if name != family]
        if remaining and statistics.median(remaining) > 0:
            drivers.append(family)
    return len(drivers) == 1 and anomalies.get(drivers[0]) is True


def evaluate_candidate(world_rows: Sequence[Mapping[str, object]]) -> dict:
    if not world_rows:
        raise FitnessV2Error("candidate evaluation has no worlds")
    by_family: dict[str, list[dict]] = {family: [] for family in FAMILIES}
    seen: set[str] = set()
    for row in world_rows:
        family = row.get("family")
        world_id = row.get("world_id")
        if family not in by_family or not isinstance(world_id, str) or world_id in seen:
            raise FitnessV2Error("candidate world rows must have unique ids and known families")
        seen.add(world_id)
        score = per_world_score(row["metrics"])
        passive = _finite_number(row["exposure_matched_passive_sharpe"], "passive sharpe")
        genome_sharpe = _finite_number(row["metrics"].get("median_sharpe", 0.0), "genome sharpe")
        by_family[family].append({**row, "score": score["score"], "signal_delta": genome_sharpe - passive})
    if any(not by_family[family] for family in FAMILIES):
        raise FitnessV2Error("candidate must be evaluated on every active family")
    if len(by_family["historical"]) != 3:
        raise FitnessV2Error("candidate requires exactly three historical worlds")

    family_scores = {
        family: family_score([row["score"] for row in rows])
        for family, rows in by_family.items()
    }
    family_deltas = {
        family: family_score([row["signal_delta"] for row in rows])
        for family, rows in by_family.items()
    }
    family_values = [family_scores[family] for family in FAMILIES]
    dispersion = statistics.stdev(family_values) if len(family_values) > 1 else 0.0
    concentration = performance_concentration(family_values)
    cross_family_fitness = sum(
        FAMILY_WEIGHTS[family] * family_scores[family] for family in FAMILIES
    ) - 0.25 * dispersion - 0.25 * concentration

    historical_floor = all(row["score"] >= 0 for row in by_family["historical"])
    synthetic_floor = all(family_scores[family] >= 0 for family in SYNTHETIC_FAMILIES)
    all_rows = [row for family in FAMILIES for row in by_family[family]]
    active = [
        row for row in all_rows
        if row["metrics"]["order_count"] > 0
        and _finite_number(row["metrics"]["median_turnover"], "median_turnover") >= MATERIALITY
    ]
    activity_rate = len(active) / len(all_rows)
    historical_active = any(
        row["metrics"]["order_count"] > 0
        and _finite_number(row["metrics"]["median_turnover"], "median_turnover") >= MATERIALITY
        for row in by_family["historical"]
    )
    structurally_inactive = not historical_active or activity_rate < ACTIVITY_FLOOR

    median_signal_delta = statistics.median(family_deltas.values())
    passive_flag = median_signal_delta <= 0
    anomalies = {
        family: all(row.get("passive_comparator_anomalous") is True for row in rows)
        for family, rows in by_family.items()
    }
    oc1 = passive_flag and _oc1_eligible(family_deltas, anomalies)
    historical_margin = all(row["score"] >= OC2_ABSOLUTE_MARGIN for row in by_family["historical"])
    synthetic_margin = all(
        family_scores[family] >= OC2_ABSOLUTE_MARGIN for family in SYNTHETIC_FAMILIES
    )
    oc2 = (
        passive_flag
        and abs(median_signal_delta) < OC2_SIGNAL_DELTA_LIMIT
        and activity_rate >= OC2_ACTIVITY_FLOOR
        and historical_margin
        and synthetic_margin
    )
    eligible = (
        historical_floor
        and synthetic_floor
        and not structurally_inactive
        and (not passive_flag or oc1 or oc2)
    )
    return {
        "cross_family_fitness": cross_family_fitness,
        "family_scores": family_scores,
        "family_signal_deltas": family_deltas,
        "family_dispersion": dispersion,
        "family_concentration": concentration,
        "historical_floor": historical_floor,
        "synthetic_floor": synthetic_floor,
        "activity_rate": activity_rate,
        "structurally_inactive": structurally_inactive,
        "median_signal_delta": median_signal_delta,
        "passive_parity_or_worse": passive_flag,
        "oc1_eligible": oc1,
        "oc2_eligible": oc2,
        "eligible": eligible,
    }


def nominate_campaign(candidates: Sequence[Mapping[str, object]]) -> dict:
    """Nominate rank-1 first; never replace a rejected rank-1 with a sibling."""
    if not candidates:
        raise FitnessV2Error("campaign has no candidates")
    ranked = sorted(
        candidates,
        key=lambda row: (-_finite_number(row["evaluation"]["cross_family_fitness"], "fitness"), row["genome_id"]),
    )
    winner = ranked[0]
    return {
        "genome_id": winner["genome_id"],
        "evaluation": winner["evaluation"],
        "clears_all_gates": winner["evaluation"]["eligible"] is True,
        "rank": 1,
        "sibling_substitution_permitted": False,
    }


def decide_admission(nominees: Sequence[Mapping[str, object]], *, predeclared_n: int) -> dict:
    if isinstance(predeclared_n, bool) or not isinstance(predeclared_n, int) or predeclared_n < 1:
        raise FitnessV2Error("predeclared_n must be a positive integer")
    if len(nominees) != predeclared_n:
        raise FitnessV2Error("campaign count differs from the predeclared N")
    seeds = [row.get("evolution_seed") for row in nominees]
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise FitnessV2Error("every campaign nominee requires an integer EVOLUTION_SEED")
    if len(set(seeds)) != len(seeds):
        raise FitnessV2Error("campaign EVOLUTION_SEED values must be independent and unique")
    qualifiers = [row for row in nominees if row.get("clears_all_gates") is True]
    required = predeclared_n // 2 + 1
    admitted = len(qualifiers) >= required
    selected = min(qualifiers, key=lambda row: row["evolution_seed"]) if admitted else None
    return {
        "predeclared_n": predeclared_n,
        "qualifier_count": len(qualifiers),
        "required_count": required,
        "ratio": f"{len(qualifiers)}/{predeclared_n}",
        "admitted": admitted,
        "selected_genome_id": selected.get("genome_id") if selected else None,
        "selection_rule": "lowest_EVOLUTION_SEED_among_qualifiers",
    }
