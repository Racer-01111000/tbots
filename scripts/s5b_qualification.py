#!/usr/bin/env python3
"""Future S5B execution entrypoint.  Requires an explicit execution token."""
import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

from lib.gitrev import get_code_revision
from lib.ids import canonical_json
from s5b_config import (
    DATASET_REVISION,
    EPISODE_HASH,
    QUALIFICATION_LANE_HASH,
    ROOT,
    SCORE_HASH,
    load_preparation_lock,
)
from s5b_evaluator import evaluate_frozen_genome, rank_completed_results
from s5b_frozen import load_frozen_top10
from s5b_qualification_bundle import (
    assert_isolated,
    load_authorized_qualification_bundle,
)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS s5b_runs (
    run_id TEXT PRIMARY KEY,
    code_revision TEXT NOT NULL,
    code_dirty INTEGER NOT NULL CHECK(code_dirty IN (0,1)),
    dataset_revision TEXT NOT NULL,
    qualification_lane_hash TEXT NOT NULL,
    episode_protocol_hash TEXT NOT NULL,
    score_protocol_hash TEXT NOT NULL,
    bundle_revision TEXT NOT NULL,
    bundle_manifest_hash TEXT NOT NULL,
    frozen_top10_snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    deterministic_digest TEXT,
    failure_json TEXT
);
CREATE TABLE IF NOT EXISTS s5b_episode_results (
    run_id TEXT NOT NULL REFERENCES s5b_runs(run_id),
    genome_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    development_rank INTEGER NOT NULL,
    episode_index INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id, episode_index)
);
CREATE TABLE IF NOT EXISTS s5b_aggregate_results (
    run_id TEXT NOT NULL REFERENCES s5b_runs(run_id),
    genome_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    development_rank INTEGER NOT NULL,
    qualification_rank INTEGER,
    qualification_score REAL NOT NULL,
    aggregate_json TEXT NOT NULL,
    metrics_hash TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
CREATE TABLE IF NOT EXISTS s5b_verifications (
    run_id TEXT NOT NULL REFERENCES s5b_runs(run_id),
    genome_id TEXT NOT NULL,
    status TEXT NOT NULL,
    main_result_digest TEXT NOT NULL,
    verifier_result_digest TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
"""


def qualification_result_digest(result: dict) -> str:
    payload = copy.deepcopy({
        "genome_id": result["genome_id"],
        "episode_metrics": result["episode_metrics"],
        "aggregate": result["aggregate"],
    })
    for episode in payload["episode_metrics"]:
        episode.pop("verifier_checks", None)
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def deterministic_run_id(revision: str, lock: dict) -> str:
    basis = {
        "code_revision": revision,
        "dataset_revision": DATASET_REVISION,
        "qualification_lane_hash": QUALIFICATION_LANE_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "bundle_revision": lock["qualification_bundle_revision"],
        "bundle_manifest_hash": lock["qualification_bundle_manifest_hash"],
        "frozen_top10_snapshot_hash": lock["frozen_top10_snapshot_hash"],
    }
    return "s5b_" + hashlib.sha256(canonical_json(basis).encode()).hexdigest()


def _persist_unranked(conn, run_id: str, result: dict) -> None:
    for episode in result["episode_metrics"]:
        conn.execute(
            "INSERT INTO s5b_episode_results "
            "(run_id,genome_id,agent_id,development_rank,episode_index,metrics_json) "
            "VALUES (?,?,?,?,?,?)",
            (
                run_id, result["genome_id"], result["agent_id"],
                result["development_rank"], episode["episode_index"],
                canonical_json(episode),
            ),
        )
    conn.execute(
        "INSERT INTO s5b_aggregate_results "
        "(run_id,genome_id,agent_id,development_rank,qualification_score,"
        "aggregate_json,metrics_hash) VALUES (?,?,?,?,?,?,?)",
        (
            run_id, result["genome_id"], result["agent_id"],
            result["development_rank"], result["aggregate"]["qualification_score"],
            canonical_json(result["aggregate"]), result["metrics_hash"],
        ),
    )


def _final_digest(ranked: list[dict], lock: dict) -> str:
    payload = {
        "ranked_results": [
            {
                "qualification_rank": row["qualification_rank"],
                "development_rank": row["development_rank"],
                "agent_id": row["agent_id"],
                "genome_id": row["genome_id"],
                "episode_metrics": row["episode_metrics"],
                "aggregate": row["aggregate"],
            }
            for row in ranked
        ],
        "preparation_lock": lock,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def run_qualification(db_path: Path, report_path: Path) -> dict:
    """Execute all ten only under a future explicit S5B execution GO."""
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise RuntimeError("qualification requires a clean committed revision")
    lock = load_preparation_lock()
    frozen = load_frozen_top10()
    if len(frozen) != 10:
        raise RuntimeError("qualification requires the accepted frozen ten")
    bundle = load_authorized_qualification_bundle()
    assert_isolated(bundle)
    run_id = deterministic_run_id(revision, lock)
    db_path = Path(db_path)
    report_path = Path(report_path)
    if db_path.exists() or report_path.exists():
        raise RuntimeError("refusing to overwrite a prior qualification output")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO s5b_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                run_id, revision, 0, DATASET_REVISION, QUALIFICATION_LANE_HASH,
                EPISODE_HASH, SCORE_HASH, lock["qualification_bundle_revision"],
                lock["qualification_bundle_manifest_hash"],
                lock["frozen_top10_snapshot_hash"], "running", None,
            ),
        )
        conn.commit()

        # Complete all ten examinations before ranking or selection.
        results = []
        for frozen_genome in frozen:
            result = evaluate_frozen_genome(bundle, frozen_genome, verify=False)
            _persist_unranked(conn, run_id, result)
            results.append(result)
            conn.commit()

        # Independent verifier replay begins only after all ten main examinations.
        verifier_rows = []
        for frozen_genome, main_result in zip(frozen, results):
            verifier_bundle = load_authorized_qualification_bundle()
            verified = evaluate_frozen_genome(
                verifier_bundle, frozen_genome, verify=True
            )
            main_digest = qualification_result_digest(main_result)
            verifier_digest = qualification_result_digest(verified)
            status = "agreed" if main_digest == verifier_digest else "disagreed"
            conn.execute(
                "INSERT INTO s5b_verifications VALUES (?,?,?,?,?)",
                (run_id, frozen_genome.genome_id, status, main_digest, verifier_digest),
            )
            verifier_rows.append({
                "genome_id": frozen_genome.genome_id,
                "status": status,
                "main_result_digest": main_digest,
                "verifier_result_digest": verifier_digest,
            })
        if any(row["status"] != "agreed" for row in verifier_rows):
            raise RuntimeError("independent qualification verifier disagreed")

        ranked = rank_completed_results(results)
        for row in ranked:
            conn.execute(
                "UPDATE s5b_aggregate_results SET qualification_rank=? "
                "WHERE run_id=? AND genome_id=?",
                (row["qualification_rank"], run_id, row["genome_id"]),
            )
        digest = _final_digest(ranked, lock)
        conn.execute(
            "UPDATE s5b_runs SET status='completed', deterministic_digest=? "
            "WHERE run_id=?",
            (digest, run_id),
        )
        conn.commit()
        report = {
            "run_id": run_id,
            "code_revision": revision,
            "preparation_lock": lock,
            "ranked_results": ranked,
            "independent_verifier": verifier_rows,
            "deterministic_digest": digest,
            "conservation": {
                "genome_mutations": 0,
                "breeding_or_retraining": 0,
                "broker_connections": 0,
                "real_orders": 0,
                "live_market_feeds": 0,
                "public_endpoints": 0,
                "championship_access": 0,
                "final_reserve_access": 0,
            },
        }
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        return report
    except Exception as exc:
        conn.execute(
            "UPDATE s5b_runs SET status='failed', failure_json=? WHERE run_id=?",
            (canonical_json({"type": type(exc).__name__, "message": str(exc)}), run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def reproduce_qualification(expected_digest: str, db_path: Path,
                            report_path: Path) -> dict:
    result = run_qualification(db_path, report_path)
    if result["deterministic_digest"] != expected_digest:
        raise RuntimeError("deterministic qualification reproduction mismatch")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-s5b", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--report")
    args = parser.parse_args()
    if not args.execute_s5b:
        raise SystemExit("refusing to execute S5B without --execute-s5b")
    if not args.db or not args.report:
        raise SystemExit("--db and --report are required")
    result = run_qualification(Path(args.db), Path(args.report))
    print(json.dumps({
        "run_id": result["run_id"],
        "deterministic_digest": result["deterministic_digest"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
