"""Fail-closed Sam identity/schema manifest check.

Read-only against the isolated Sam worktree: loads the exact genome,
recomputes its content-addressed id via the repo's own lib.ids.genome_id,
asserts it against the expected constant, validates schema/feature
compatibility via the repo's own s6a_runtime.validate_genome, and writes
a manifest JSON. Never falls back to the frozen champion on any failure;
never calls decide_D, never executes a backtest, never mutates the
genome or any accepted file.
"""
import hashlib
import json
import sys
from pathlib import Path

WORKTREE = Path("/home/ec2-user/sam_dev_staging_20261001/tbots-sam-worktree")
SCRIPTS = WORKTREE / "scripts"
sys.path.insert(0, str(SCRIPTS))

EXPECTED_GENOME_ID = "gen_8e7a622b48276bc68b7b70773bb9e2a5250dbb82584c53f6c67416bd6ffdf0cf"
OLD_CHAMPION_ID = "gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155"
SOURCE_FILE = (
    WORKTREE
    / "evolution/s6b_runs/primary/D_s6a_d_b986e97007ea1d059d3a55d8dbec42f17a53fda7a805db13849e112e273f74ca"
    / "development_top_eight.json"
)
MANIFEST_OUT = Path("/home/ec2-user/sam_dev_staging_20261001/SAM_MANIFEST_20261001.json")


def fail_closed(reason: str) -> None:
    manifest = {
        "status": "MISSING_FAIL_CLOSED",
        "reason": reason,
        "expected_genome_id": EXPECTED_GENOME_ID,
        "fallback_to_champion": False,
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"FAIL_CLOSED: {reason}")
    sys.exit(1)


def main() -> None:
    if not SOURCE_FILE.exists():
        fail_closed(f"source file missing: {SOURCE_FILE}")

    source_bytes = SOURCE_FILE.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    data = json.loads(source_bytes)
    frozen = data.get("frozen", [])
    entry = next((e for e in frozen if e.get("genome_id") == EXPECTED_GENOME_ID), None)
    if entry is None:
        fail_closed(f"genome id {EXPECTED_GENOME_ID} not found in {SOURCE_FILE}")

    from lib.ids import genome_id  # the repo's own canonical derivation

    genome = entry["genome"]
    recomputed = genome_id(genome)
    if recomputed != EXPECTED_GENOME_ID:
        fail_closed(
            f"genome_id mismatch: stored={entry['genome_id']} recomputed={recomputed}"
        )
    if recomputed == OLD_CHAMPION_ID:
        fail_closed("recomputed id equals the old frozen champion -- refusing substitution")

    import s6a_runtime as rt

    try:
        validated_id = rt.validate_genome("D", genome)
    except Exception as exc:  # noqa: BLE001 - fail closed on ANY validation error
        fail_closed(f"schema/feature validation raised: {exc!r}")

    if validated_id != EXPECTED_GENOME_ID:
        fail_closed(f"validate_genome returned unexpected id: {validated_id}")

    warmup_sessions = rt.warmup("D", genome)

    manifest = {
        "status": "DEVELOPMENT_CANDIDATE",
        "admission_status": "NOT_ADMITTED",
        "broker_status": "NO_BROKER",
        "label": "Sam (Rick's name; not used in-repo) -- D-primary lineage rank 1",
        "expected_genome_id": EXPECTED_GENOME_ID,
        "observed_genome_id": recomputed,
        "genome_id_match": True,
        "old_champion_id_for_comparison_only": OLD_CHAMPION_ID,
        "schema_validation": "PASSED (s6a_runtime.validate_genome('D', genome))",
        "warmup_sessions_required": warmup_sessions,
        "source_file": str(SOURCE_FILE),
        "source_file_sha256": source_sha256,
        "source_commit": "6ac72a290e4bd55c89165ad98214e5deea63ac4f",
        "source_commit_message": "Preserve S6B historical run evidence into tbots",
        "staged_worktree_head": None,  # filled in by the shell wrapper from git rev-parse
        "development_rank": entry.get("development_rank"),
        "generation": entry.get("generation"),
        "fitness": entry.get("fitness"),
        "lineage": entry.get("lineage"),
        "verified_flag_in_source": entry.get("verified"),
        "genome_definition": genome,
        "never_executed_decide_D": True,
        "never_ran_reserve_evaluation": True,
        "never_mutated_genome": True,
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print("OK: manifest written, genome identity and schema verified, no evaluation performed")
    print(json.dumps({k: manifest[k] for k in (
        "status", "admission_status", "broker_status", "expected_genome_id",
        "observed_genome_id", "genome_id_match", "schema_validation",
        "warmup_sessions_required", "source_file_sha256",
    )}, indent=2))


if __name__ == "__main__":
    main()
