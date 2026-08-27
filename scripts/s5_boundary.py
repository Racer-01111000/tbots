#!/usr/bin/env python3
"""S5 contamination and lane boundary.

This module authorizes evaluation plans; it does not implement population
creation, mutation, ranking, qualification scheduling, or any sealed-lane
operation. The only executable anchor is a fresh S4 control-genome replay.
"""
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from genome_control import CONTROL_GENOME
from lib.ids import genome_id
from run_control_episode import run_control_episode

ROOT = Path(__file__).resolve().parents[1]
LANE_DIR = ROOT / "data" / "lane_manifests"
DATASET_REVISION = "ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131"
DATASET_END = "2026-08-25"
CONTROL_GENOME_ID = genome_id(CONTROL_GENOME)

DEVELOPMENT_HASH = "lane_a02f9a435d21bd21722356da8280a45b9454e653987174b897244ec9746d1dc2"
QUALIFICATION_HASH = "lane_c226205dfa6ff0f5ccd96f26c49a5bb080853fcca5e7d46ef94099fba30da539"

AUTHORIZED_PATHS = {
    DEVELOPMENT_HASH: LANE_DIR / f"development_{DEVELOPMENT_HASH}.json",
    QUALIFICATION_HASH: LANE_DIR / f"qualification_{QUALIFICATION_HASH}.json",
}
AUTHORIZED_CONTENT = {
    DEVELOPMENT_HASH: {
        "schema_version": 1,
        "lane": "DEVELOPMENT",
        "dataset_revision": DATASET_REVISION,
        "start_date": "2007-02-07",
        "end_date": "2018-12-31",
        "sealed": False,
        "purpose": "S5 control-anchor and evolutionary fitness evaluation",
        "feedback_policy": "eligible_for_evolutionary_fitness",
    },
    QUALIFICATION_HASH: {
        "schema_version": 1,
        "lane": "QUALIFICATION",
        "dataset_revision": DATASET_REVISION,
        "start_date": "2019-01-01",
        "end_date": "2022-12-31",
        "sealed": False,
        "purpose": "S5 out-of-development qualification evaluation",
        "feedback_policy": "evaluation_only_no_mutation_or_selection_feedback",
    },
}
SEALED_INTERVALS = {
    "CHAMPIONSHIP": ("2023-01-01", "2025-12-31"),
    "FINAL_RESERVE": ("2026-01-01", DATASET_END),
}
IDENTIFIER_RE = re.compile(r"^(?:exp|epi|result|report)[_:.-]", re.IGNORECASE)


class LaneBoundaryError(ValueError):
    pass


class ManifestIntegrityError(LaneBoundaryError):
    pass


class SealedLaneError(LaneBoundaryError):
    pass


class ContaminatedFitnessInput(LaneBoundaryError):
    pass


@dataclass(frozen=True)
class LaneManifest:
    manifest_hash: str
    lane: str
    dataset_revision: str
    start_date: str
    end_date: str
    feedback_policy: str


@dataclass(frozen=True)
class EvaluationPlan:
    manifest: LaneManifest
    genome_id: str
    genome_source: str
    fresh_evaluation: bool
    retention_end_date: str
    evolutionary_feedback_allowed: bool


@dataclass(frozen=True)
class LaneEvaluationResult:
    plan: EvaluationPlan
    fresh_result: dict


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def content_hash(content: dict) -> str:
    return "lane_" + hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def _overlaps(start: str, end: str, sealed_start: str, sealed_end: str) -> bool:
    return start <= sealed_end and end >= sealed_start


def _resolve_manifest_reference(manifest_ref) -> Path:
    if isinstance(manifest_ref, str) and manifest_ref in AUTHORIZED_PATHS:
        return AUTHORIZED_PATHS[manifest_ref]
    return Path(manifest_ref)


def load_authorized_manifest(manifest_ref) -> LaneManifest:
    path = _resolve_manifest_reference(manifest_ref)
    try:
        envelope = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestIntegrityError(f"lane manifest is unreadable: {path}") from exc
    if set(envelope) != {"content", "manifest_hash"} or not isinstance(envelope["content"], dict):
        raise ManifestIntegrityError("lane manifest envelope must contain only content and manifest_hash")

    content = envelope["content"]
    claimed_hash = envelope["manifest_hash"]
    actual_hash = content_hash(content)
    if claimed_hash != actual_hash:
        raise ManifestIntegrityError(
            f"lane manifest content hash mismatch: claimed {claimed_hash}, actual {actual_hash}"
        )

    start, end = content.get("start_date"), content.get("end_date")
    if not isinstance(start, str) or not isinstance(end, str) or start > end:
        raise ManifestIntegrityError("lane manifest has an invalid interval")
    for sealed_name, (sealed_start, sealed_end) in SEALED_INTERVALS.items():
        if _overlaps(start, end, sealed_start, sealed_end):
            raise SealedLaneError(f"{sealed_name} interval is sealed from S5 evaluation")

    if actual_hash not in AUTHORIZED_CONTENT or content != AUTHORIZED_CONTENT[actual_hash]:
        raise ManifestIntegrityError("lane manifest is not an authorized immutable manifest")
    expected_path = AUTHORIZED_PATHS[actual_hash]
    if expected_path.name != path.name and path.resolve() != expected_path.resolve():
        raise ManifestIntegrityError("authorized manifest content must use its content-hashed filename")

    return LaneManifest(
        manifest_hash=actual_hash,
        lane=content["lane"],
        dataset_revision=content["dataset_revision"],
        start_date=start,
        end_date=end,
        feedback_policy=content["feedback_policy"],
    )


def build_control_evaluation_plan(manifest_ref, *, requested_interval=None, fitness_input=None,
                                  prior_result_identifiers=None,
                                  qualification_feedback=None) -> EvaluationPlan:
    """Build a plan solely from an authorized manifest and the S4 genome definition."""
    if requested_interval is not None:
        raise LaneBoundaryError("arbitrary evaluation intervals are prohibited")
    if fitness_input is not None:
        raise ContaminatedFitnessInput(
            "external performance, curves, decisions, orders, fills, or metrics are prohibited"
        )
    if prior_result_identifiers:
        identifiers = list(prior_result_identifiers)
        if any(not isinstance(value, str) or not IDENTIFIER_RE.match(value)
               for value in identifiers):
            raise ContaminatedFitnessInput("unrecognized prior result identifier input is prohibited")
        raise ContaminatedFitnessInput(
            "prior experiment, episode, result, and report identifiers are prohibited"
        )
    if qualification_feedback is not None:
        raise ContaminatedFitnessInput(
            "qualification results cannot feed evolutionary mutation or selection"
        )

    manifest = load_authorized_manifest(manifest_ref)
    return EvaluationPlan(
        manifest=manifest,
        genome_id=CONTROL_GENOME_ID,
        genome_source="S4_CONTROL_GENOME_DEFINITION",
        fresh_evaluation=True,
        retention_end_date=manifest.end_date,
        evolutionary_feedback_allowed=manifest.lane == "DEVELOPMENT",
    )


def evaluate_control_lane(manifest_ref, **untrusted_inputs) -> LaneEvaluationResult:
    """Freshly replay the S4 control genome within one authorized lane."""
    plan = build_control_evaluation_plan(manifest_ref, **untrusted_inputs)
    result = run_control_episode(
        ROOT,
        plan.manifest.dataset_revision,
        plan.manifest.start_date,
        plan.manifest.end_date,
        masked_time=True,
        write_equity_curve=False,
        verify_every_rebalance=True,
        data_access_end=plan.retention_end_date,
    )
    return LaneEvaluationResult(plan=plan, fresh_result=result)


def extract_evolutionary_fitness(evaluation: LaneEvaluationResult) -> dict:
    """Whitelist fresh DEVELOPMENT metrics; reject qualification feedback."""
    if (evaluation.plan.manifest.lane != "DEVELOPMENT"
            or not evaluation.plan.evolutionary_feedback_allowed):
        raise ContaminatedFitnessInput(
            "only fresh DEVELOPMENT evaluation may produce evolutionary fitness"
        )
    result = evaluation.fresh_result
    return {
        "final_equity_cents": result["final_equity_cents"],
        "total_return": result["total_return"],
        "max_drawdown": result["max_drawdown"],
    }
