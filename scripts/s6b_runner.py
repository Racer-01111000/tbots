"""S6B deterministic orchestration and isolated persistence.

This module composes the frozen S6A primitives.  It contains no strategy,
mutation, fitness, execution, or advancement mathematics of its own.  Direct
CLI population execution stays disabled until a later exact operational gate.
"""
from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import s6a_final as p
import s6a_runtime as r
from lib.ids import canonical_json
from s5a_config import EPISODE_PROTOCOL


class S6BError(RuntimeError):
    pass


class IdentityMismatch(S6BError):
    pass


class PersistenceCollision(S6BError):
    pass


class EvaluatorVerifierDisagreement(S6BError):
    pass


class NondeterminismError(S6BError):
    pass


class PopulationExecutionLocked(S6BError):
    pass


EXPECTED_EVOLUTION = (
    "s6a_evolution_f11988439d95eeb66c8b7ab11e625a5441e22e8eb2f5dd9d0087d5f9bddd5088"
)
EXPECTED_PLAN = (
    "s6a_plan_f575e2b8617f8cad4d454d72079320744bb7b9514de0ab124e3b4e52ef26714d"
)
EXPECTED_COMPLETION_LOCK = (
    "s6a_completion_lock_0bd22a5df27a9f48141cb50b59df92ac88925a2b65c5dea77adb07c4ba1c7e81"
)
EXPECTED_RUN_IDS = {
    "B": "s6a_b_11cf933f3df42f14f360338c1a834fda684eb2d58705abfd90b73ca8afe75d79",
    "C": "s6a_c_99e51f2f688620f2bf46c8cc1d06cfefc5ea608d19aa378ebcb65d9ef8ac9c5a",
    "D": "s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca",
    "E": "s6a_e_569e97ec37fda9b1363d8bea6297a6134af160c37b03c94f5a0882ed40c9e91f",
    "F": "s6a_f_69e8deca71456bde6a661f59e91088c8a17cf7921e1b4ea59ffb5602776bdff9",
    "G": "s6a_g_1c923c4e2ec758e504723253be1dd482826832691de94f5a7045225411c969d5",
}
EXPECTED_SCHEMA_IDS = {
    "B": "s6a_schema_b_39619bd0c295340262805b765018540b84025a68312b8e5fec75bfb806ae9d30",
    "C": "s6a_schema_c_2c736dc756cd874815695858fb1489517c780fa847c14258c6f0451640d6447d",
    "D": "s6a_schema_d_e22ad90c115bbc09917e3257d29c5497ff95ced191aa4df47fb09c6771cb4211",
    "E": "s6a_schema_e_7a760b60b0a5427f15de6056b8d2d6066b0135b727890b6878d68df590f4bd81",
    "F": "s6a_schema_f_b2ab7c35baf5055a6760b9adcc23cb651b27942636415d162acd8af1a8ad9051",
    "G": "s6a_schema_g_aa7ea76288905b8d067677602baf97a6e6ac8af62ebbb8374850ebf96141e40e",
}
EXPECTED_PROTOCOL_IDS = {
    "advancement": "s6a_advancement_e07c320e6099f877ac487155649ebf7bde056e5801f1718a28b83dd97125a7f9",
    "diversity": "s6a_diversity_b7e1f38a13fff230756da3e30d1f35e110242bc8b6818cb65be95f111325ba11",
    "evolution": EXPECTED_EVOLUTION,
    "execution": "s6a_execution_972ceb538d8020eb461c700a89f756c35b75a3e70b530b968f14590e509f52c3",
    "fitness": "s6a_fitness_0c428673a0f037b0045f9cced6aaec9e5452a6acf590fc2ffe769d801948a3f6",
    "history": "s6a_history_47a5b532b597f95e87171b232c93193e17c729c288dbda7f83e75b7e42f47ad8",
    "operators": "s6a_operators_b5a9245b23520cec85028d7f2730af9f26ee874394350753e80bb1c8d2b2543b",
}
DEFAULT_OUTPUT_ROOT = p.ROOT / "evolution" / "s6b_runs"
Evaluator = Callable[[str, dict, r.CandidateAdmission], dict]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def assert_frozen_identities() -> dict:
    """Mechanically verify code-derived and committed immutable S6A identities."""
    if (
        p.HASHES != EXPECTED_PROTOCOL_IDS
        or p.HASHES["evolution"] != EXPECTED_EVOLUTION
        or p.PLAN_HASH != EXPECTED_PLAN
        or p.RUN_IDS != EXPECTED_RUN_IDS
        or p.SCHEMA_HASHES != EXPECTED_SCHEMA_IDS
    ):
        raise IdentityMismatch("frozen S6A code identity mismatch")
    for path, (content, identity) in p.artifact_map().items():
        if not path.is_file() or path.read_bytes() != p.envelope(content, identity):
            raise IdentityMismatch(f"frozen S6A artifact mismatch: {path}")
    lock_path = p.PROTOCOL_DIR / "s6a_executable_preparation_lock.json"
    try:
        lock = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityMismatch("S6A completion lock is unreadable") from exc
    if (
        set(lock) != {"content", "manifest_hash"}
        or lock["manifest_hash"] != EXPECTED_COMPLETION_LOCK
        or p.h("s6a_completion_lock_", lock["content"]) != EXPECTED_COMPLETION_LOCK
        or lock["content"].get("plan_hash") != EXPECTED_PLAN
        or lock["content"].get("protocol_hashes") != EXPECTED_PROTOCOL_IDS
        or lock["content"].get("run_ids") != EXPECTED_RUN_IDS
        or lock["content"].get("schema_hashes") != EXPECTED_SCHEMA_IDS
        or lock["content"].get("population_execution_authorized") is not False
    ):
        raise IdentityMismatch("frozen S6A completion lock mismatch")
    return {
        "evolution": EXPECTED_EVOLUTION,
        "plan": EXPECTED_PLAN,
        "completion_lock": EXPECTED_COMPLETION_LOCK,
        "run_ids": EXPECTED_RUN_IDS,
        "schema_ids": EXPECTED_SCHEMA_IDS,
        "protocol_ids": EXPECTED_PROTOCOL_IDS,
    }


def validate_development_availability(availability: dict) -> None:
    """Reject any feasibility source not exactly bounded to DEVELOPMENT."""
    lane = p.HISTORY["development"]
    episodes = availability.get("episodes")
    if (
        availability.get("dataset_revision") != p.DATASET
        or availability.get("development_start") != lane["start"]
        or availability.get("development_end") != lane["end"]
        or availability.get("episode_count") != lane["episodes"]
        or not isinstance(episodes, list)
        or len(episodes) != lane["episodes"]
        or availability.get("source_bundle_latest", "9999") > lane["end"]
        or availability.get("latest_observation_used_for_feasibility", "9999") > lane["end"]
    ):
        raise r.BoundaryError("S6B feasibility source escaped frozen DEVELOPMENT")
    expected_indexes = list(range(lane["episodes"]))
    if [row.get("episode_index") for row in episodes] != expected_indexes:
        raise r.BoundaryError("S6B DEVELOPMENT episode identity mismatch")
    for row in episodes:
        first = row.get("first_session", "")
        counts = row.get("available_price_bars")
        if (
            not lane["start"] <= first <= lane["end"]
            or first >= "2019-01-01"
            or not isinstance(counts, dict)
            or set(counts) != set(p.UNIVERSE)
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 1
                   for value in counts.values())
        ):
            raise r.BoundaryError("invalid or post-DEVELOPMENT physical availability")
    minimums = {
        symbol: min(row["available_price_bars"][symbol] for row in episodes)
        for symbol in p.UNIVERSE
    }
    if (
        availability.get("minimum_price_bars_by_asset") != minimums
        or availability.get("minimum_price_bars") != min(minimums.values())
    ):
        raise r.BoundaryError("inconsistent DEVELOPMENT physical availability")


def admit_candidate(code: str, genome: dict, availability: dict) -> r.CandidateAdmission:
    validate_development_availability(availability)
    return r.require_development_feasible(code, genome, availability=availability)


def validate_admission_token(code: str, genome: dict, admission,
                             availability: dict) -> r.CandidateAdmission:
    expected = admit_candidate(code, genome, availability)
    if not isinstance(admission, r.CandidateAdmission) or admission != expected:
        raise r.FeasibilityError("invalid S6B feasibility-admission token")
    return admission


def validate_evaluation_result(result: dict) -> dict:
    if not isinstance(result, dict) or set(result) != {
        "episode_metrics", "aggregate", "metrics_hash"
    }:
        raise S6BError("evaluator returned an invalid result envelope")
    rows = result["episode_metrics"]
    aggregate = result["aggregate"]
    frozen = EPISODE_PROTOCOL["episodes"]
    if not isinstance(rows, list) or len(rows) != len(frozen):
        raise r.BoundaryError("evaluator did not return the complete DEVELOPMENT lane")
    for index, (row, episode) in enumerate(zip(rows, frozen)):
        if (
            not isinstance(row, dict)
            or row.get("episode_index") != episode["episode_index"]
            or row.get("start_date") != episode["start_date"]
            or row.get("end_date") != episode["end_date"]
            or row["end_date"] >= "2019-01-01"
        ):
            raise r.BoundaryError(f"historical boundary violation in episode {index}")
    fitness = aggregate.get("fitness") if isinstance(aggregate, dict) else None
    if (
        not isinstance(fitness, (int, float))
        or isinstance(fitness, bool)
        or not math.isfinite(fitness)
    ):
        raise S6BError("evaluator returned invalid aggregate fitness")
    expected_hash = _digest({"episodes": rows, "aggregate": aggregate})
    if result["metrics_hash"] != expected_hash:
        raise S6BError("evaluator metrics hash mismatch")
    return result


def evaluate_candidate(code: str, genome: dict, availability: dict,
                       evaluator: Evaluator, *, admission=None) -> tuple:
    """Admission precedes the evaluator call; rejected candidates execute zero episodes."""
    token = admission or admit_candidate(code, genome, availability)
    token = validate_admission_token(code, genome, token, availability)
    result = validate_evaluation_result(evaluator(code, genome, token))
    return token, result


def assert_evaluator_verifier_agreement(primary: dict, verified: dict) -> None:
    validate_evaluation_result(primary)
    validate_evaluation_result(verified)
    if canonical_json(primary) != canonical_json(verified):
        raise EvaluatorVerifierDisagreement("independent verifier disagreed with evaluator")


def validate_population(code: str, members: list[dict], generation: int,
                        availability: dict, previous_ids: set[str] | None = None) -> None:
    if code not in p.NAMES or len(members) != 64:
        raise r.S6Error("population must contain exactly 64 valid lineage members")
    roles = [row.get("role") for row in members]
    if generation == 0:
        if roles != ["founder"] * 64 or len({row["genome_id"] for row in members}) != 64:
            raise r.S6Error("Gen0 must be 64 unique founders")
    elif (
        roles.count("elite") != 8
        or roles.count("child") != 48
        or roles.count("immigrant") != 8
        or previous_ids is None
    ):
        raise r.S6Error("generation composition changed from frozen S6A")
    for row in members:
        if row.get("lineage") != code:
            raise r.IsolationError("unexpected cross-lineage population provenance")
        gid = r.validate_genome(code, row["genome"])
        if gid != row.get("genome_id") or gid == p.TRADER_A:
            raise r.IsolationError("population genome identity or Trader A violation")
        validate_admission_token(
            code, row["genome"], admit_candidate(code, row["genome"], availability),
            availability,
        )
        parent = row.get("parent_genome_id")
        if generation == 0 or row["role"] == "immigrant":
            if parent is not None:
                raise r.IsolationError("founder/immigrant unexpectedly has a parent")
        else:
            r.assert_parent(code, code, parent)
            if parent not in previous_ids:
                raise r.IsolationError("parent is not in the prior same-lineage generation")
            if row["role"] == "elite" and parent != gid:
                raise r.IsolationError("elite provenance changed genome identity")
            if row["role"] == "child" and parent == gid:
                raise r.IsolationError("mutated child is unchanged")


class LineageStore:
    """Exclusive-create JSON persistence rooted in one lineage/run identity."""

    def __init__(self, output_root: Path, output_kind: str, code: str):
        if output_kind not in {"primary", "reproduction"}:
            raise ValueError("unknown S6B output kind")
        self.target = Path(output_root) / output_kind / f"{code}_{p.RUN_IDS[code]}"
        self.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.target.mkdir()
        except FileExistsError as exc:
            raise PersistenceCollision(f"S6B output already exists: {self.target}") from exc
        (self.target / "generations").mkdir()
        (self.target / "feasibility").mkdir()

    def write(self, relative: str, value: object) -> None:
        path = self.target / relative
        if path.parent not in {
            self.target, self.target / "generations", self.target / "feasibility"
        }:
            raise PersistenceCollision("persistence path escaped isolated lineage root")
        try:
            with path.open("xb") as handle:
                handle.write(_json_bytes(value))
        except FileExistsError as exc:
            raise PersistenceCollision(f"S6B artifact collision: {path}") from exc

    def tree_digest(self) -> str:
        manifest = []
        for path in sorted(item for item in self.target.rglob("*") if item.is_file()):
            manifest.append({
                "path": str(path.relative_to(self.target)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        return _digest(manifest)


def _member_record(slot: int, row: dict, admission: r.CandidateAdmission,
                   result: dict, generation: int, verified: bool) -> dict:
    return {
        "slot_index": slot,
        "generation": generation,
        "lineage": row["lineage"],
        "role": row["role"],
        "genome_id": row["genome_id"],
        "genome": row["genome"],
        "parent_genome_id": row.get("parent_genome_id"),
        "admission": asdict(admission),
        "episode_metrics": result["episode_metrics"],
        "aggregate": result["aggregate"],
        "metrics_hash": result["metrics_hash"],
        "verified": verified,
    }


def run_lineage(code: str, output_root: Path, availability: dict,
                evaluator: Evaluator, verifier: Evaluator, *,
                output_kind: str = "primary",
                execution_class: str = "synthetic",
                population_execution_authorized: bool = False) -> dict:
    """Run Gen0..Gen12 for one isolated lineage using only frozen S6A builders."""
    identities = assert_frozen_identities()
    validate_development_availability(availability)
    if code not in p.NAMES:
        raise r.IsolationError("only frozen B-G lineages are permitted")
    if execution_class not in {"synthetic", "population"}:
        raise ValueError("unknown S6B execution class")
    if execution_class == "population" and population_execution_authorized is not True:
        raise PopulationExecutionLocked(
            "real S6 population execution remains locked")
    if verifier is evaluator:
        raise S6BError("independent verifier hook must be a distinct callable")

    store = LineageStore(output_root, output_kind, code)
    store.write("run_metadata.json", {
        "schema_version": 1,
        "phase": "S6B_EXECUTION_SURFACE",
        "execution_class": execution_class,
        "lineage": code,
        "run_id": p.RUN_IDS[code],
        "generation_start": 0,
        "generation_end": 12,
        "population_size": 64,
        "frozen_identities": identities,
    })
    generation_summaries = []
    feasibility_audits = []
    verification_records = []
    audit = r.new_feasibility_audit()
    try:
        current = r.build_gen0(code, availability=availability, audit=audit)
        previous_ids = None
        final_rows = None
        for generation in range(13):
            validate_population(code, current, generation, availability, previous_ids)
            evaluated = []
            for slot, row in enumerate(current):
                admission, result = evaluate_candidate(
                    code, row["genome"], availability, evaluator,
                )
                verified = False
                if generation == 12:
                    verifier_result = validate_evaluation_result(
                        verifier(code, row["genome"], admission)
                    )
                    assert_evaluator_verifier_agreement(result, verifier_result)
                    verified = True
                    verification_records.append({
                        "slot_index": slot,
                        "genome_id": row["genome_id"],
                        "status": "agreed",
                        "evaluator_metrics_hash": result["metrics_hash"],
                        "verifier_metrics_hash": verifier_result["metrics_hash"],
                    })
                evaluated.append(
                    _member_record(slot, row, admission, result, generation, verified)
                )
            ranked = sorted(
                evaluated,
                key=lambda row: (-row["aggregate"]["fitness"], row["genome_id"],
                                 row["slot_index"]),
            )
            rank_by_slot = {
                row["slot_index"]: rank for rank, row in enumerate(ranked, 1)
            }
            for row in evaluated:
                row["fitness"] = row["aggregate"]["fitness"]
                row["rank"] = rank_by_slot[row["slot_index"]]
            generation_base = {
                "schema_version": 1,
                "lineage": code,
                "run_id": p.RUN_IDS[code],
                "generation": generation,
                "membership": evaluated,
            }
            generation_identity = "s6b_generation_" + _digest(generation_base)
            generation_record = {
                **generation_base, "generation_identity": generation_identity,
            }
            store.write(f"generations/gen_{generation:02d}.json", generation_record)
            store.write(f"feasibility/gen_{generation:02d}.json", audit)
            generation_summaries.append({
                "generation": generation,
                "generation_identity": generation_identity,
                "member_count": len(evaluated),
            })
            feasibility_audits.append({
                "generation": generation, "audit": audit,
            })
            if generation == 12:
                final_rows = evaluated
                break
            previous_ids = {row["genome_id"] for row in evaluated}
            next_audit = r.new_feasibility_audit()
            current = r.build_generation(
                code, evaluated, generation + 1,
                availability=availability, audit=next_audit,
            )
            audit = next_audit

        if final_rows is None:
            raise S6BError("Gen12 was not completed")
        top_eight = r.rank_development(final_rows)
        frozen = [{
            "development_rank": rank,
            "lineage": code,
            "genome_id": row["genome_id"],
            "genome": row["genome"],
            "fitness": row["fitness"],
            "generation": 12,
            "verified": True,
        } for rank, row in enumerate(top_eight, 1)]
        if len(frozen) != 8 or len({row["genome_id"] for row in frozen}) != 8:
            raise r.S6Error("development advancement must freeze eight unique genomes")
        store.write("gen12_verification.json", {
            "schema_version": 1,
            "lineage": code,
            "verified_slots": verification_records,
        })
        store.write("development_top_eight.json", {
            "schema_version": 1,
            "lineage": code,
            "run_id": p.RUN_IDS[code],
            "frozen": frozen,
        })
        deterministic_snapshot = {
            "lineage": code,
            "run_id": p.RUN_IDS[code],
            "generation_summaries": generation_summaries,
            "feasibility_audits": feasibility_audits,
            "gen12_verification": verification_records,
            "development_top_eight": frozen,
        }
        deterministic_digest = _digest(deterministic_snapshot)
        store.write("completion.json", {
            "schema_version": 1,
            "status": "completed",
            "lineage": code,
            "run_id": p.RUN_IDS[code],
            "generation_count": 13,
            "population_members_per_generation": 64,
            "deterministic_digest": deterministic_digest,
        })
        return {
            "lineage": code,
            "run_id": p.RUN_IDS[code],
            "output_path": str(store.target),
            "generation_summaries": generation_summaries,
            "feasibility_audits": feasibility_audits,
            "development_top_eight": frozen,
            "deterministic_digest": deterministic_digest,
            "tree_digest": store.tree_digest(),
        }
    except Exception as exc:
        try:
            store.write("failure.json", {
                "status": "failed_closed",
                "lineage": code,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
        except PersistenceCollision:
            pass
        raise


def run_and_reproduce_lineage(code: str, output_root: Path, availability: dict,
                              evaluator: Evaluator, verifier: Evaluator, *,
                              execution_class: str = "synthetic",
                              population_execution_authorized: bool = False) -> dict:
    primary = run_lineage(
        code, output_root, availability, evaluator, verifier,
        output_kind="primary", execution_class=execution_class,
        population_execution_authorized=population_execution_authorized,
    )
    reproduced = run_lineage(
        code, output_root, availability, evaluator, verifier,
        output_kind="reproduction", execution_class=execution_class,
        population_execution_authorized=population_execution_authorized,
    )
    if (
        primary["deterministic_digest"] != reproduced["deterministic_digest"]
        or primary["tree_digest"] != reproduced["tree_digest"]
    ):
        raise NondeterminismError("isolated deterministic reproduction disagreed")
    return {
        "status": "agreed",
        "primary": primary,
        "reproduction": reproduced,
    }


def run_lineages(codes: list[str], output_root: Path, availability: dict,
                 evaluator_factory: Callable[[str], Evaluator],
                 verifier_factory: Callable[[str], Evaluator], *,
                 max_concurrency: int = 2, output_kind: str = "primary",
                 execution_class: str = "synthetic",
                 population_execution_authorized: bool = False) -> dict:
    """Execute isolated lineages with a hard maximum of two active workers."""
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or not 1 <= max_concurrency <= 2
    ):
        raise S6BError("maximum lineage concurrency is exactly bounded to two")
    if len(codes) != len(set(codes)) or any(code not in p.NAMES for code in codes):
        raise r.IsolationError("lineage schedule must contain unique frozen B-G codes")
    results = {}
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {
            pool.submit(
                run_lineage, code, output_root, availability,
                evaluator_factory(code), verifier_factory(code),
                output_kind=output_kind, execution_class=execution_class,
                population_execution_authorized=population_execution_authorized,
            ): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            results[code] = future.result()
    return {code: results[code] for code in sorted(results)}


def historical_evaluator(bundle, availability: dict, *,
                         population_execution_authorized: bool = False) -> Evaluator:
    """Construct the real DEVELOPMENT adapter only under a later explicit gate."""
    if population_execution_authorized is not True:
        raise PopulationExecutionLocked("real S6 population execution remains locked")
    validate_development_availability(availability)
    from s6b_evaluator import evaluate_genome

    def evaluate(code: str, genome: dict, admission: r.CandidateAdmission) -> dict:
        return evaluate_genome(
            bundle, code, genome, admission=admission, availability=availability,
        )
    return evaluate


def main() -> None:
    raise SystemExit(
        "S6B execution surface is import-only; real Gen0 population execution remains locked"
    )


if __name__ == "__main__":
    main()
