#!/usr/bin/env python3
"""Future S5D execution entrypoint. Requires an explicit FINAL RESERVE token."""
import argparse
import copy
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from lib.gitrev import get_code_revision
from lib.ids import canonical_json
from s5d_champion import load_frozen_champion
from s5d_config import (
    ACCEPTED_CHAMPION_ID,
    ACCEPTED_S5C_RUN_ID,
    DATASET_REVISION,
    EPISODE_HASH,
    OUTCOME_HASH,
    ROOT,
    load_preparation_lock,
)
from s5d_evaluator import evaluate_champion
from s5d_final_reserve_bundle import (
    assert_isolated,
    load_authorized_final_reserve_bundle,
)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE s5d_runs (
    run_id TEXT PRIMARY KEY,
    code_revision TEXT NOT NULL,
    code_dirty INTEGER NOT NULL CHECK(code_dirty IN (0,1)),
    dataset_revision TEXT NOT NULL,
    champion_id TEXT NOT NULL,
    accepted_s5c_run_id TEXT NOT NULL,
    episode_protocol_hash TEXT NOT NULL,
    outcome_protocol_hash TEXT NOT NULL,
    bundle_revision TEXT NOT NULL,
    bundle_manifest_hash TEXT NOT NULL,
    champion_snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    deterministic_digest TEXT,
    failure_json TEXT
);
CREATE TABLE s5d_episode_results (
    run_id TEXT NOT NULL REFERENCES s5d_runs(run_id),
    genome_id TEXT NOT NULL,
    episode_index INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id, episode_index)
);
CREATE TABLE s5d_outcomes (
    run_id TEXT NOT NULL REFERENCES s5d_runs(run_id),
    genome_id TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    metrics_hash TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
CREATE TABLE s5d_verifications (
    run_id TEXT NOT NULL REFERENCES s5d_runs(run_id),
    genome_id TEXT NOT NULL,
    status TEXT NOT NULL,
    main_result_digest TEXT NOT NULL,
    verifier_result_digest TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
"""


def final_reserve_result_digest(result: dict) -> str:
    payload = copy.deepcopy({
        "genome_id": result["genome_id"],
        "episode_metric": result["episode_metric"],
        "outcome": result["outcome"],
    })
    payload["episode_metric"].pop("verifier_checks", None)
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def deterministic_run_id(revision: str, lock: dict) -> str:
    basis = {
        "code_revision": revision,
        "dataset_revision": DATASET_REVISION,
        "champion_id": ACCEPTED_CHAMPION_ID,
        "accepted_s5c_run_id": ACCEPTED_S5C_RUN_ID,
        "episode_protocol_hash": EPISODE_HASH,
        "outcome_protocol_hash": OUTCOME_HASH,
        "bundle_revision": lock["final_reserve_bundle_revision"],
        "bundle_manifest_hash": lock["final_reserve_bundle_manifest_hash"],
        "champion_snapshot_hash": lock["champion_snapshot_hash"],
    }
    return "s5d_" + hashlib.sha256(canonical_json(basis).encode()).hexdigest()


def _final_digest(result: dict, verification: dict, lock: dict) -> str:
    payload = {
        "champion_result": {
            "agent_id": result["agent_id"],
            "genome_id": result["genome_id"],
            "episode_metric": result["episode_metric"],
            "outcome": result["outcome"],
            "metrics_hash": result["metrics_hash"],
        },
        "verification": verification,
        "preparation_lock": lock,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def run_final_reserve(db_path: Path, report_path: Path) -> dict:
    """Execute only under a future explicit S5D FINAL RESERVE GO."""
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise RuntimeError("final reserve execution requires a clean committed revision")
    lock = load_preparation_lock()
    champion = load_frozen_champion()
    bundle = load_authorized_final_reserve_bundle()
    assert_isolated(bundle)
    run_id = deterministic_run_id(revision, lock)
    db_path = Path(db_path)
    report_path = Path(report_path)
    if db_path.exists() or report_path.exists():
        raise RuntimeError("refusing to overwrite a prior final reserve output")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO s5d_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                run_id,
                revision,
                0,
                DATASET_REVISION,
                ACCEPTED_CHAMPION_ID,
                ACCEPTED_S5C_RUN_ID,
                EPISODE_HASH,
                OUTCOME_HASH,
                lock["final_reserve_bundle_revision"],
                lock["final_reserve_bundle_manifest_hash"],
                lock["champion_snapshot_hash"],
                "running",
                None,
            ),
        )
        conn.commit()

        result = evaluate_champion(bundle, champion, verify=False)
        conn.execute(
            "INSERT INTO s5d_episode_results VALUES (?,?,?,?)",
            (
                run_id,
                champion.genome_id,
                0,
                canonical_json(result["episode_metric"]),
            ),
        )
        conn.execute(
            "INSERT INTO s5d_outcomes VALUES (?,?,?,?)",
            (
                run_id,
                champion.genome_id,
                canonical_json(result["outcome"]),
                result["metrics_hash"],
            ),
        )
        conn.commit()

        verifier_bundle = load_authorized_final_reserve_bundle()
        verified = evaluate_champion(verifier_bundle, champion, verify=True)
        main_digest = final_reserve_result_digest(result)
        verifier_digest = final_reserve_result_digest(verified)
        status = "agreed" if main_digest == verifier_digest else "disagreed"
        verification = {
            "genome_id": champion.genome_id,
            "status": status,
            "main_result_digest": main_digest,
            "verifier_result_digest": verifier_digest,
        }
        conn.execute(
            "INSERT INTO s5d_verifications VALUES (?,?,?,?,?)",
            (
                run_id,
                champion.genome_id,
                status,
                main_digest,
                verifier_digest,
            ),
        )
        if status != "agreed":
            raise RuntimeError("independent final reserve verifier disagreed")

        digest = _final_digest(result, verification, lock)
        conn.execute(
            "UPDATE s5d_runs SET status='completed',deterministic_digest=? "
            "WHERE run_id=?",
            (digest, run_id),
        )
        conn.commit()
        report = {
            "run_id": run_id,
            "code_revision": revision,
            "preparation_lock": lock,
            "champion_result": result,
            "independent_verifier": verification,
            "deterministic_digest": digest,
            "interpretation": {
                "purpose": "reporting_and_validation_only",
                "selection_or_replacement": None,
                "acceptance_threshold": None,
                "negative_return_policy": "record_without_replacement_or_retraining",
            },
            "conservation": {
                "genome_mutations": 0,
                "breeding_or_retraining": 0,
                "evolutionary_feedback": 0,
                "broker_connections": 0,
                "real_orders": 0,
                "live_market_feeds": 0,
                "public_endpoints": 0,
            },
        }
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        return report
    except Exception as exc:
        conn.execute(
            "UPDATE s5d_runs SET status='failed',failure_json=? WHERE run_id=?",
            (
                canonical_json(
                    {"type": type(exc).__name__, "message": str(exc)}
                ),
                run_id,
            ),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-s5d-final-reserve", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--report")
    args = parser.parse_args()
    if not args.execute_s5d_final_reserve:
        raise SystemExit(
            "refusing to execute FINAL RESERVE without "
            "--execute-s5d-final-reserve"
        )
    if not args.db or not args.report:
        raise SystemExit("--db and --report are required")
    result = run_final_reserve(Path(args.db), Path(args.report))
    print(json.dumps({
        "run_id": result["run_id"],
        "genome_id": result["champion_result"]["genome_id"],
        "deterministic_digest": result["deterministic_digest"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
