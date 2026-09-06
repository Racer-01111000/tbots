"""Crash-safe deterministic resume executor for interrupted S6B lineages.

This module is intentionally narrower than ``s6b_runner.run_lineage``. It can
only continue a checkpoint that ``s6b_continuation.inspect_checkpoint`` has
already classified as INTERRUPTED. It never creates a new lineage, never
restarts Gen0, never overwrites a persisted artifact, and never changes the
frozen S6A/S6B evolution or trading methodology.

Persistence is generation-transactional. All artifacts are staged first and a
transaction manifest is fsync'd before any final path is created. Final files
are committed with hard links (atomic, no-overwrite). If the process dies
mid-commit, the manifest remains and the next invocation completes only the
same already-prepared transaction after verifying every staged/final SHA-256.
Generation JSON is the commit point for Gen0-11; completion.json is the final
commit point for Gen12.

No historical execution is authorized by this module itself. A preserved
``execution_class == 'population'`` checkpoint still requires the caller to
pass ``population_execution_authorized=True`` and to provide the already-
approved evaluator/verifier hooks under a separate operational GO.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import s6a_final as p
import s6a_runtime as r
import s6b_continuation as cont
import s6b_runner as runner


class ResumeExecutorError(cont.ContinuationError):
    pass


class ResumeExecutionLocked(ResumeExecutorError):
    pass


class ResumeTransactionError(ResumeExecutorError):
    pass


class ResumeArtifactCollision(ResumeTransactionError):
    pass


class ResumeCheckpointChanged(ResumeTransactionError):
    pass


TRANSACTION_FILE = ".s6b_resume_transaction.json"
STAGE_PREFIX = ".s6b_resume_stage_gen_"

Evaluator = Callable[[str, dict, r.CandidateAdmission], dict]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ResumeArtifactCollision(f"resume staging collision: {path}") from exc
    _fsync_dir(path.parent)


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ResumeTransactionError(f"unsafe transaction artifact path: {value!r}")
    allowed = {
        "generations", "feasibility", "gen12_verification.json",
        "development_top_eight.json", "completion.json",
    }
    if path.parts[0] not in allowed:
        raise ResumeTransactionError(f"transaction path escaped allowed resume surface: {value}")
    if path.parts[0] in {"generations", "feasibility"} and len(path.parts) != 2:
        raise ResumeTransactionError(f"invalid generation artifact path: {value}")
    if path.parts[0] not in {"generations", "feasibility"} and len(path.parts) != 1:
        raise ResumeTransactionError(f"invalid tail artifact path: {value}")
    return path


def _cleanup_orphan_stages(target: Path) -> None:
    """Remove only inert staging directories when no transaction exists.

    A stage is written before the transaction manifest and no final artifact is
    committed until after the manifest exists. Therefore a stage without a
    manifest can only be pre-commit debris from a crash and is safe to remove.
    """
    if (target / TRANSACTION_FILE).exists():
        return
    for path in target.glob(f"{STAGE_PREFIX}*"):
        if path.is_dir():
            shutil.rmtree(path)
    _fsync_dir(target)


def _read_metadata(state: cont.CheckpointState) -> dict:
    try:
        metadata = json.loads((state.target / "run_metadata.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeExecutorError("checkpoint metadata became unreadable") from exc
    if metadata.get("lineage") != state.code or metadata.get("run_id") != state.run_id:
        raise ResumeCheckpointChanged("checkpoint identity changed after inspection")
    return metadata


def _require_execution_gate(state: cont.CheckpointState,
                            population_execution_authorized: bool) -> str:
    metadata = _read_metadata(state)
    execution_class = metadata.get("execution_class")
    if execution_class not in {"synthetic", "population"}:
        raise ResumeExecutorError("checkpoint has an unknown execution_class")
    if execution_class == "population" and population_execution_authorized is not True:
        raise ResumeExecutionLocked(
            "historical population resume remains locked without explicit authorization"
        )
    return execution_class


def _previous_generation_identity(state: cont.CheckpointState) -> str | None:
    if state.highest_generation is None:
        return None
    path = state.target / "generations" / f"gen_{state.highest_generation:02d}.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeCheckpointChanged("predecessor generation became unreadable") from exc
    return record.get("generation_identity")


def _evaluate_generation(state: cont.CheckpointState, availability: dict,
                         evaluator: Evaluator, verifier: Evaluator) -> tuple[dict, dict, list]:
    generation = state.next_generation
    if generation is None:
        raise ResumeExecutorError("checkpoint has no next generation")
    if verifier is evaluator:
        raise runner.S6BError("independent verifier hook must be a distinct callable")

    audit = r.new_feasibility_audit()
    current = r.build_generation(
        state.code, state.last_generation_members, generation,
        availability=availability, audit=audit,
    )
    previous_ids = {
        row["genome_id"] for row in (state.last_generation_members or [])
    } or None
    runner.validate_population(
        state.code, current, generation, availability, previous_ids,
    )

    evaluated = []
    verification_records = []
    for slot, row in enumerate(current):
        admission, result = runner.evaluate_candidate(
            state.code, row["genome"], availability, evaluator,
        )
        verified = False
        if generation == 12:
            verifier_result = runner.validate_evaluation_result(
                verifier(state.code, row["genome"], admission)
            )
            runner.assert_evaluator_verifier_agreement(result, verifier_result)
            verified = True
            verification_records.append({
                "slot_index": slot,
                "genome_id": row["genome_id"],
                "status": "agreed",
                "evaluator_metrics_hash": result["metrics_hash"],
                "verifier_metrics_hash": verifier_result["metrics_hash"],
            })
        evaluated.append(runner._member_record(
            slot, row, admission, result, generation, verified,
        ))

    ranked = sorted(
        evaluated,
        key=lambda row: (-row["aggregate"]["fitness"], row["genome_id"], row["slot_index"]),
    )
    rank_by_slot = {row["slot_index"]: rank for rank, row in enumerate(ranked, 1)}
    for row in evaluated:
        row["fitness"] = row["aggregate"]["fitness"]
        row["rank"] = rank_by_slot[row["slot_index"]]

    generation_base = {
        "schema_version": 1,
        "lineage": state.code,
        "run_id": p.RUN_IDS[state.code],
        "generation": generation,
        "membership": evaluated,
    }
    generation_record = {
        **generation_base,
        "generation_identity": "s6b_generation_" + runner._digest(generation_base),
    }
    return generation_record, audit, verification_records


def _load_existing_history_for_completion(target: Path, through_generation: int) -> tuple[list, list]:
    summaries = []
    audits = []
    for generation in range(through_generation + 1):
        generation_path = target / "generations" / f"gen_{generation:02d}.json"
        feasibility_path = target / "feasibility" / f"gen_{generation:02d}.json"
        try:
            generation_record = json.loads(generation_path.read_text())
            audit = json.loads(feasibility_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ResumeExecutorError(
                f"cannot finalize resume: persisted generation {generation} history is incomplete"
            ) from exc
        membership = generation_record.get("membership")
        if not isinstance(membership, list) or len(membership) != 64:
            raise ResumeExecutorError(
                f"cannot finalize resume: generation {generation} membership is invalid"
            )
        summaries.append({
            "generation": generation,
            "generation_identity": generation_record.get("generation_identity"),
            "member_count": len(membership),
        })
        audits.append({"generation": generation, "audit": audit})
    return summaries, audits


def _gen12_tail(state: cont.CheckpointState, generation_record: dict,
                audit: dict, verification_records: list) -> dict[str, object]:
    final_rows = generation_record["membership"]
    top_eight = r.rank_development(final_rows)
    frozen = [{
        "development_rank": rank,
        "lineage": state.code,
        "genome_id": row["genome_id"],
        "genome": row["genome"],
        "fitness": row["fitness"],
        "generation": 12,
        "verified": True,
    } for rank, row in enumerate(top_eight, 1)]
    if len(frozen) != 8 or len({row["genome_id"] for row in frozen}) != 8:
        raise r.S6Error("development advancement must freeze eight unique genomes")

    generation_summaries, feasibility_audits = _load_existing_history_for_completion(
        state.target, 11,
    )
    generation_summaries.append({
        "generation": 12,
        "generation_identity": generation_record["generation_identity"],
        "member_count": 64,
    })
    feasibility_audits.append({"generation": 12, "audit": audit})
    deterministic_snapshot = {
        "lineage": state.code,
        "run_id": p.RUN_IDS[state.code],
        "generation_summaries": generation_summaries,
        "feasibility_audits": feasibility_audits,
        "gen12_verification": verification_records,
        "development_top_eight": frozen,
    }
    deterministic_digest = runner._digest(deterministic_snapshot)
    return {
        "gen12_verification.json": {
            "schema_version": 1,
            "lineage": state.code,
            "verified_slots": verification_records,
        },
        "development_top_eight.json": {
            "schema_version": 1,
            "lineage": state.code,
            "run_id": p.RUN_IDS[state.code],
            "frozen": frozen,
        },
        "completion.json": {
            "schema_version": 1,
            "status": "completed",
            "lineage": state.code,
            "run_id": p.RUN_IDS[state.code],
            "generation_count": 13,
            "population_members_per_generation": 64,
            "deterministic_digest": deterministic_digest,
        },
    }


def _prepare_transaction(state: cont.CheckpointState, artifacts: dict[str, object]) -> dict:
    generation = state.next_generation
    if generation is None:
        raise ResumeTransactionError("cannot prepare transaction without next generation")
    target = state.target
    transaction_path = target / TRANSACTION_FILE
    if transaction_path.exists():
        raise ResumeTransactionError("a resume transaction is already pending")

    stage = target / f"{STAGE_PREFIX}{generation:02d}"
    try:
        stage.mkdir()
    except FileExistsError as exc:
        raise ResumeTransactionError(f"resume stage already exists: {stage}") from exc

    manifest_artifacts = []
    try:
        for relative, value in artifacts.items():
            rel = _safe_relative_path(relative)
            data = _json_bytes(value)
            staged_name = relative.replace("/", "__")
            staged_path = stage / staged_name
            _write_new_bytes(staged_path, data)
            manifest_artifacts.append({
                "relative_path": str(rel),
                "staged_name": staged_name,
                "sha256": _sha256_bytes(data),
                "byte_size": len(data),
            })
        manifest = {
            "schema_version": 1,
            "lineage": state.code,
            "output_kind": state.output_kind,
            "run_id": state.run_id,
            "generation": generation,
            "predecessor_generation": state.highest_generation,
            "predecessor_generation_identity": _previous_generation_identity(state),
            "identity_profile": state.identity_profile,
            "stage_directory": stage.name,
            "artifacts": manifest_artifacts,
        }
        _write_new_bytes(transaction_path, _json_bytes(manifest))
        return manifest
    except Exception:
        if not transaction_path.exists() and stage.exists():
            shutil.rmtree(stage)
            _fsync_dir(target)
        raise


def _validate_transaction_target(target: Path, manifest: dict) -> None:
    if manifest.get("schema_version") != 1:
        raise ResumeTransactionError("unknown resume transaction schema")
    generation = manifest.get("generation")
    predecessor = manifest.get("predecessor_generation")
    if not isinstance(generation, int) or not 0 <= generation <= 12:
        raise ResumeTransactionError("invalid transaction generation")
    if predecessor != generation - 1:
        raise ResumeTransactionError("transaction predecessor is not immediately contiguous")
    predecessor_path = target / "generations" / f"gen_{predecessor:02d}.json"
    if predecessor >= 0:
        try:
            record = json.loads(predecessor_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ResumeCheckpointChanged("transaction predecessor is unreadable") from exc
        if record.get("generation_identity") != manifest.get("predecessor_generation_identity"):
            raise ResumeCheckpointChanged("checkpoint predecessor changed after transaction preparation")


def recover_pending_transaction(target: Path) -> dict | None:
    """Complete exactly one already-prepared generation transaction, if any."""
    target = Path(target)
    transaction_path = target / TRANSACTION_FILE
    if not transaction_path.exists():
        _cleanup_orphan_stages(target)
        return None
    try:
        manifest = json.loads(transaction_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeTransactionError("pending resume transaction manifest is unreadable") from exc
    _validate_transaction_target(target, manifest)

    stage = target / manifest.get("stage_directory", "")
    if not stage.is_dir() or stage.parent != target:
        raise ResumeTransactionError("pending resume transaction stage is missing")

    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise ResumeTransactionError("pending resume transaction has no artifacts")

    by_path = {entry.get("relative_path"): entry for entry in entries if isinstance(entry, dict)}
    generation = manifest["generation"]
    generation_rel = f"generations/gen_{generation:02d}.json"
    feasibility_rel = f"feasibility/gen_{generation:02d}.json"
    required = {generation_rel, feasibility_rel}
    if generation == 12:
        required |= {"gen12_verification.json", "development_top_eight.json", "completion.json"}
    if set(by_path) != required:
        raise ResumeTransactionError("pending transaction artifact set is not exactly bounded")

    order = [feasibility_rel]
    if generation == 12:
        order += ["gen12_verification.json", "development_top_eight.json"]
    order += [generation_rel]
    if generation == 12:
        order += ["completion.json"]

    for relative in order:
        entry = by_path[relative]
        rel = _safe_relative_path(relative)
        staged = stage / entry.get("staged_name", "")
        expected_hash = entry.get("sha256")
        if not staged.is_file() or _sha256_file(staged) != expected_hash:
            raise ResumeTransactionError(f"staged resume artifact failed hash check: {relative}")
        final = target / rel
        if final.exists():
            if not final.is_file() or _sha256_file(final) != expected_hash:
                raise ResumeArtifactCollision(
                    f"existing resume artifact differs from prepared transaction: {final}"
                )
            continue
        try:
            os.link(staged, final)
        except FileExistsError:
            if not final.is_file() or _sha256_file(final) != expected_hash:
                raise ResumeArtifactCollision(f"resume artifact collision: {final}")
        _fsync_dir(final.parent)

    transaction_path.unlink()
    _fsync_dir(target)
    shutil.rmtree(stage)
    _fsync_dir(target)
    return {
        "status": "recovered",
        "lineage": manifest.get("lineage"),
        "output_kind": manifest.get("output_kind"),
        "generation": generation,
    }


def resume_one_generation(output_root: Path, output_kind: str, code: str,
                          availability: dict, evaluator: Evaluator, verifier: Evaluator,
                          *, population_execution_authorized: bool = False) -> dict:
    """Resume exactly one missing generation, or finish a pending transaction.

    The function never advances two generations in a single call. This makes
    the operational boundary inspectable and allows a caller to stop after any
    newly persisted generation without losing deterministic restartability.
    """
    target = cont._target_path(output_root, output_kind, code)
    recovered = recover_pending_transaction(target) if target.is_dir() else None
    if recovered is not None:
        state = cont.inspect_checkpoint(output_root, output_kind, code, availability)
        return {
            **recovered,
            "checkpoint_status": state.status,
            "highest_generation": state.highest_generation,
            "next_generation": state.next_generation,
        }

    state = cont.inspect_checkpoint(output_root, output_kind, code, availability)
    if state.status == cont.STATUS_NO_EXISTING_RUN:
        raise cont.NoExistingRun(
            f"no preserved run exists for {output_kind}/{code}; resume cannot create one"
        )
    if state.status == cont.STATUS_COMPLETE:
        raise cont.CompleteRunProtected(
            f"{output_kind}/{code} is already complete; resume cannot overwrite it"
        )
    if state.status == cont.STATUS_FAILED:
        raise cont.FailedRunNeedsReview(
            f"{output_kind}/{code} has a failure marker and requires human review"
        )
    if state.status != cont.STATUS_INTERRUPTED:
        raise ResumeExecutorError("checkpoint is not resumable")

    _require_execution_gate(state, population_execution_authorized)
    runner.validate_development_availability(availability)
    generation_record, audit, verification_records = _evaluate_generation(
        state, availability, evaluator, verifier,
    )
    generation = state.next_generation
    artifacts: dict[str, object] = {
        f"feasibility/gen_{generation:02d}.json": audit,
        f"generations/gen_{generation:02d}.json": generation_record,
    }
    if generation == 12:
        artifacts.update(_gen12_tail(state, generation_record, audit, verification_records))

    _prepare_transaction(state, artifacts)
    committed = recover_pending_transaction(state.target)
    next_state = cont.inspect_checkpoint(output_root, output_kind, code, availability)
    return {
        "status": "committed",
        "lineage": code,
        "output_kind": output_kind,
        "generation": generation,
        "generation_identity": generation_record["generation_identity"],
        "transaction": committed,
        "checkpoint_status": next_state.status,
        "highest_generation": next_state.highest_generation,
        "next_generation": next_state.next_generation,
    }


def resume_to_completion(output_root: Path, output_kind: str, code: str,
                         availability: dict, evaluator: Evaluator, verifier: Evaluator,
                         *, population_execution_authorized: bool = False) -> dict:
    """Continue only an existing interrupted lineage until Gen12 is complete.

    Each generation is independently transactional. The function stops on the
    first exception, leaving either the last fully committed generation or a
    recoverable pending transaction. It can never create E/F/G or restart a
    completed B/D primary run because ``resume_one_generation`` refuses those
    checkpoint states.
    """
    committed = []
    while True:
        state = cont.inspect_checkpoint(output_root, output_kind, code, availability)
        if state.status == cont.STATUS_COMPLETE:
            return {
                "status": "completed",
                "lineage": code,
                "output_kind": output_kind,
                "highest_generation": 12,
                "generations_committed_this_invocation": committed,
            }
        result = resume_one_generation(
            output_root, output_kind, code, availability, evaluator, verifier,
            population_execution_authorized=population_execution_authorized,
        )
        if result.get("generation") is not None:
            committed.append(result["generation"])


def main() -> None:
    raise SystemExit(
        "S6B resume executor is import-only; historical execution requires a separate GO"
    )


if __name__ == "__main__":
    main()
