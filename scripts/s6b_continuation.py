"""S6B deterministic continuation/resume surface.

Read-only inspection of an existing (possibly interrupted) isolated S6B
lineage checkpoint on disk, plus deterministic computation of the next
unbuilt generation's *candidate* population from frozen S6A primitives
alone.

This module never persists, never overwrites, and never evaluates a
genome. It composes ``s6a_runtime.build_generation`` with the exact
frozen-identity and population verification ``s6b_runner`` already
enforces for live execution, so a checkpoint that would fail
``s6b_runner``'s own fail-closed checks fails here too, before any
continuation is considered.

No historical organism ever executes here: computing the next generation
only derives genomes/roles/parent-provenance from frozen seeds, which is
disjoint from evaluating fitness against historical price data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import s6a_final as p
import s6a_runtime as r
import s6b_runner as runner


class ContinuationError(runner.S6BError):
    pass


class NoExistingRun(ContinuationError):
    pass


class CompleteRunProtected(ContinuationError):
    pass


class FailedRunNeedsReview(ContinuationError):
    pass


class CorruptedCheckpoint(ContinuationError):
    pass


class GenerationGap(ContinuationError):
    pass


class GenerationAlreadyExists(ContinuationError):
    pass


class WrongLineage(ContinuationError):
    pass


class WrongRunIdentity(ContinuationError):
    pass


STATUS_NO_EXISTING_RUN = "no_existing_run"
STATUS_INTERRUPTED = "interrupted"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_REQUIRED_MEMBER_KEYS = {
    "slot_index", "generation", "lineage", "role", "genome_id", "genome",
    "parent_genome_id", "admission", "episode_metrics", "aggregate",
    "metrics_hash", "verified", "fitness", "rank",
}


@dataclass
class CheckpointState:
    code: str
    output_kind: str
    target: Path
    status: str
    highest_generation: int | None
    next_generation: int | None
    last_generation_members: list | None
    run_id: str | None
    frozen_identities: dict | None


def _target_path(output_root, output_kind: str, code: str) -> Path:
    if output_kind not in {"primary", "reproduction"}:
        raise ValueError("unknown S6B output kind")
    if code not in p.NAMES:
        raise ValueError("unknown S6A lineage code")
    return Path(output_root) / output_kind / f"{code}_{p.RUN_IDS[code]}"


def _read_json(path: Path, error_cls=CorruptedCheckpoint):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise error_cls(f"unreadable or malformed checkpoint file: {path}") from exc


def _validate_generation_record(code: str, output_kind: str, generation: int,
                                 record: dict) -> list:
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 1
        or record.get("lineage") != code
        or record.get("run_id") != p.RUN_IDS[code]
        or record.get("generation") != generation
        or not isinstance(record.get("membership"), list)
    ):
        raise CorruptedCheckpoint(
            f"generation {generation} record shape mismatch ({output_kind}/{code})"
        )
    membership = record["membership"]
    if len(membership) != 64:
        raise CorruptedCheckpoint(
            f"generation {generation} does not contain exactly 64 population slots"
        )
    slots = {row.get("slot_index") for row in membership if isinstance(row, dict)}
    if slots != set(range(64)):
        raise CorruptedCheckpoint(
            f"generation {generation} population slots are not exactly 0..63"
        )
    if any(not _REQUIRED_MEMBER_KEYS.issubset(row) for row in membership):
        raise CorruptedCheckpoint(
            f"generation {generation} membership row missing required fields"
        )
    for row in membership:
        expected_metrics_hash = runner._digest({
            "episodes": row["episode_metrics"], "aggregate": row["aggregate"],
        })
        if row.get("metrics_hash") != expected_metrics_hash:
            raise CorruptedCheckpoint(
                f"generation {generation} slot {row.get('slot_index')} stored "
                "fitness/evaluation state does not match its own metrics_hash"
            )
    expected_identity = "s6b_generation_" + runner._digest({
        "schema_version": 1, "lineage": code, "run_id": p.RUN_IDS[code],
        "generation": generation, "membership": membership,
    })
    if record.get("generation_identity") != expected_identity:
        raise CorruptedCheckpoint(
            f"generation {generation} identity does not match its recomputed digest"
        )
    return membership


def inspect_checkpoint(output_root, output_kind: str, code: str,
                        availability: dict) -> CheckpointState:
    """Read-only mechanical inspection of one lineage's persisted checkpoint.

    Never writes. Never invokes an evaluator or verifier. Raises a typed
    ``ContinuationError`` subclass the instant any frozen invariant does
    not hold; never guesses past a discrepancy.
    """
    target = _target_path(output_root, output_kind, code)
    if not target.is_dir():
        return CheckpointState(
            code=code, output_kind=output_kind, target=target,
            status=STATUS_NO_EXISTING_RUN, highest_generation=None,
            next_generation=None, last_generation_members=None,
            run_id=None, frozen_identities=None,
        )

    metadata = _read_json(target / "run_metadata.json")
    if metadata.get("lineage") != code:
        raise WrongLineage(f"checkpoint at {target} is not lineage {code}")
    if metadata.get("run_id") != p.RUN_IDS[code]:
        raise WrongRunIdentity(f"checkpoint at {target} has an unexpected run_id")
    if (
        metadata.get("population_size") != 64
        or metadata.get("generation_start") != 0
        or metadata.get("generation_end") != 12
    ):
        raise CorruptedCheckpoint(f"checkpoint metadata shape mismatch at {target}")

    live_identities = runner.assert_frozen_identities()
    if metadata.get("frozen_identities") != live_identities:
        raise runner.IdentityMismatch(
            "checkpoint was produced under different frozen S6A identities "
            "than the currently committed protocol"
        )

    generations_dir = target / "generations"
    highest = -1
    last_members = None
    previous_ids = None
    for generation in range(13):
        path = generations_dir / f"gen_{generation:02d}.json"
        if not path.is_file():
            break
        record = _read_json(path)
        membership = _validate_generation_record(code, output_kind, generation, record)
        runner.validate_population(code, membership, generation, availability, previous_ids)
        previous_ids = {row["genome_id"] for row in membership}
        last_members = membership
        highest = generation

    for generation in range(highest + 1, 13):
        if (generations_dir / f"gen_{generation:02d}.json").is_file():
            raise GenerationGap(
                f"generation {generation} exists after a missing predecessor "
                f"(highest contiguous generation was {highest})"
            )

    failure_path = target / "failure.json"
    completion_path = target / "completion.json"

    if failure_path.is_file():
        _read_json(failure_path)
        return CheckpointState(
            code=code, output_kind=output_kind, target=target,
            status=STATUS_FAILED, highest_generation=highest if highest >= 0 else None,
            next_generation=None, last_generation_members=last_members,
            run_id=metadata.get("run_id"), frozen_identities=live_identities,
        )

    if highest == 12:
        completion = _read_json(completion_path)
        if (
            completion.get("status") != "completed"
            or completion.get("lineage") != code
            or completion.get("run_id") != p.RUN_IDS[code]
            or completion.get("generation_count") != 13
        ):
            raise CorruptedCheckpoint(
                f"generation 12 is persisted but completion.json is invalid at {target}"
            )
        return CheckpointState(
            code=code, output_kind=output_kind, target=target,
            status=STATUS_COMPLETE, highest_generation=12,
            next_generation=None, last_generation_members=last_members,
            run_id=metadata.get("run_id"), frozen_identities=live_identities,
        )

    if completion_path.is_file():
        raise CorruptedCheckpoint(
            f"completion.json present without a persisted generation 12 at {target}"
        )

    return CheckpointState(
        code=code, output_kind=output_kind, target=target,
        status=STATUS_INTERRUPTED,
        highest_generation=highest if highest >= 0 else None,
        next_generation=(highest + 1) if highest >= 0 else 0,
        last_generation_members=last_members,
        run_id=metadata.get("run_id"), frozen_identities=live_identities,
    )


def preview_next_generation(state: CheckpointState, availability: dict) -> list:
    """Deterministically construct the next generation's candidate population.

    Returns bare role/genome/genome_id/parent_genome_id rows only. Nothing
    is evaluated (no fitness, no episodes) and nothing is written to disk.
    Refuses outright unless ``state`` is a genuinely interrupted run whose
    next generation has not already been persisted.
    """
    if state.status == STATUS_NO_EXISTING_RUN:
        raise NoExistingRun(
            f"no preserved run exists for {state.output_kind}/{state.code}; "
            "first execution requires a separate GO, not continuation"
        )
    if state.status == STATUS_COMPLETE:
        raise CompleteRunProtected(
            f"{state.output_kind}/{state.code} is already complete through "
            "generation 12; refusing to resume or overwrite a completed run"
        )
    if state.status == STATUS_FAILED:
        raise FailedRunNeedsReview(
            f"{state.output_kind}/{state.code} stopped in a failed-closed "
            "state; it requires human review, not automatic continuation"
        )
    if state.status != STATUS_INTERRUPTED or state.next_generation is None:
        raise ContinuationError("checkpoint is not in a resumable state")

    next_path = (
        state.target / "generations" / f"gen_{state.next_generation:02d}.json"
    )
    if next_path.is_file():
        raise GenerationAlreadyExists(
            f"generation {state.next_generation} is already persisted at {next_path}"
        )

    audit = r.new_feasibility_audit()
    candidates = r.build_generation(
        state.code, state.last_generation_members, state.next_generation,
        availability=availability, audit=audit,
    )
    return [
        {
            "role": row["role"],
            "lineage": row["lineage"],
            "genome": row["genome"],
            "genome_id": row["genome_id"],
            "parent_genome_id": row["parent_genome_id"],
        }
        for row in candidates
    ]
