#!/usr/bin/env python3
"""Future S5C execution entrypoint. Requires an explicit championship token."""
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
from s5c_championship_bundle import (
    assert_isolated,
    load_authorized_championship_bundle,
)
from s5c_config import (
    ADVANCEMENT_MANIFEST_HASH,
    DATASET_REVISION,
    EPISODE_HASH,
    ROOT,
    SCORE_HASH,
    load_preparation_lock,
)
from s5c_evaluator import evaluate_finalist, select_winner
from s5c_finalists import load_frozen_finalists

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE s5c_runs (
    run_id TEXT PRIMARY KEY,
    code_revision TEXT NOT NULL,
    code_dirty INTEGER NOT NULL CHECK(code_dirty IN (0,1)),
    dataset_revision TEXT NOT NULL,
    advancement_manifest_hash TEXT NOT NULL,
    episode_protocol_hash TEXT NOT NULL,
    score_protocol_hash TEXT NOT NULL,
    bundle_revision TEXT NOT NULL,
    bundle_manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    winner_genome_id TEXT,
    deterministic_digest TEXT,
    failure_json TEXT
);
CREATE TABLE s5c_episode_results (
    run_id TEXT NOT NULL REFERENCES s5c_runs(run_id),
    genome_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    selection_order INTEGER NOT NULL,
    episode_index INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id, episode_index)
);
CREATE TABLE s5c_aggregate_results (
    run_id TEXT NOT NULL REFERENCES s5c_runs(run_id),
    genome_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    selection_order INTEGER NOT NULL,
    championship_rank INTEGER,
    championship_score REAL NOT NULL,
    aggregate_json TEXT NOT NULL,
    metrics_hash TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
CREATE TABLE s5c_verifications (
    run_id TEXT NOT NULL REFERENCES s5c_runs(run_id),
    genome_id TEXT NOT NULL,
    status TEXT NOT NULL,
    main_result_digest TEXT NOT NULL,
    verifier_result_digest TEXT NOT NULL,
    PRIMARY KEY(run_id, genome_id)
);
"""


def championship_result_digest(result: dict) -> str:
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
        "advancement_manifest_hash": ADVANCEMENT_MANIFEST_HASH,
        "episode_protocol_hash": EPISODE_HASH,
        "score_protocol_hash": SCORE_HASH,
        "bundle_revision": lock["championship_bundle_revision"],
        "bundle_manifest_hash": lock["championship_bundle_manifest_hash"],
    }
    return "s5c_" + hashlib.sha256(canonical_json(basis).encode()).hexdigest()


def _persist_unranked(conn, run_id: str, result: dict) -> None:
    for episode in result["episode_metrics"]:
        conn.execute(
            "INSERT INTO s5c_episode_results VALUES (?,?,?,?,?,?)",
            (
                run_id,
                result["genome_id"],
                result["agent_id"],
                result["selection_order"],
                episode["episode_index"],
                canonical_json(episode),
            ),
        )
    conn.execute(
        "INSERT INTO s5c_aggregate_results "
        "(run_id,genome_id,agent_id,selection_order,championship_score,"
        "aggregate_json,metrics_hash) VALUES (?,?,?,?,?,?,?)",
        (
            run_id,
            result["genome_id"],
            result["agent_id"],
            result["selection_order"],
            result["aggregate"]["championship_score"],
            canonical_json(result["aggregate"]),
            result["metrics_hash"],
        ),
    )


def _final_digest(ranked: list[dict], winner: dict, lock: dict) -> str:
    payload = {
        "ranked_results": [
            {
                "championship_rank": row["championship_rank"],
                "selection_order": row["selection_order"],
                "agent_id": row["agent_id"],
                "genome_id": row["genome_id"],
                "episode_metrics": row["episode_metrics"],
                "aggregate": row["aggregate"],
            }
            for row in ranked
        ],
        "winner_genome_id": winner["genome_id"],
        "preparation_lock": lock,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def run_championship(db_path: Path, report_path: Path) -> dict:
    """Execute all three only under a future explicit S5C championship GO."""
    revision, dirty = get_code_revision(str(ROOT))
    if dirty:
        raise RuntimeError("championship requires a clean committed revision")
    lock = load_preparation_lock()
    finalists = load_frozen_finalists()
    if len(finalists) != 3:
        raise RuntimeError("championship requires the accepted frozen three")
    bundle = load_authorized_championship_bundle()
    assert_isolated(bundle)
    run_id = deterministic_run_id(revision, lock)
    db_path = Path(db_path)
    report_path = Path(report_path)
    if db_path.exists() or report_path.exists():
        raise RuntimeError("refusing to overwrite a prior championship output")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO s5c_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                run_id,
                revision,
                0,
                DATASET_REVISION,
                ADVANCEMENT_MANIFEST_HASH,
                EPISODE_HASH,
                SCORE_HASH,
                lock["championship_bundle_revision"],
                lock["championship_bundle_manifest_hash"],
                "running",
                None,
                None,
            ),
        )
        conn.commit()

        results = []
        for finalist in finalists:
            result = evaluate_finalist(bundle, finalist, verify=False)
            _persist_unranked(conn, run_id, result)
            results.append(result)
            conn.commit()

        verifier_rows = []
        for finalist, main_result in zip(finalists, results):
            verifier_bundle = load_authorized_championship_bundle()
            verified = evaluate_finalist(verifier_bundle, finalist, verify=True)
            main_digest = championship_result_digest(main_result)
            verifier_digest = championship_result_digest(verified)
            status = "agreed" if main_digest == verifier_digest else "disagreed"
            conn.execute(
                "INSERT INTO s5c_verifications VALUES (?,?,?,?,?)",
                (
                    run_id,
                    finalist.genome_id,
                    status,
                    main_digest,
                    verifier_digest,
                ),
            )
            verifier_rows.append({
                "genome_id": finalist.genome_id,
                "status": status,
                "main_result_digest": main_digest,
                "verifier_result_digest": verifier_digest,
            })
        if any(row["status"] != "agreed" for row in verifier_rows):
            raise RuntimeError("independent championship verifier disagreed")

        winner, ranked = select_winner(results)
        for row in ranked:
            conn.execute(
                "UPDATE s5c_aggregate_results SET championship_rank=? "
                "WHERE run_id=? AND genome_id=?",
                (row["championship_rank"], run_id, row["genome_id"]),
            )
        digest = _final_digest(ranked, winner, lock)
        conn.execute(
            "UPDATE s5c_runs SET status='completed',winner_genome_id=?,"
            "deterministic_digest=? WHERE run_id=?",
            (winner["genome_id"], digest, run_id),
        )
        conn.commit()
        report = {
            "run_id": run_id,
            "code_revision": revision,
            "preparation_lock": lock,
            "ranked_results": ranked,
            "winner": {
                "championship_rank": 1,
                "genome_id": winner["genome_id"],
                "rule": "rank_1_no_discretionary_override",
            },
            "independent_verifier": verifier_rows,
            "deterministic_digest": digest,
            "conservation": {
                "genome_mutations": 0,
                "breeding_or_retraining": 0,
                "evolutionary_feedback": 0,
                "broker_connections": 0,
                "real_orders": 0,
                "live_market_feeds": 0,
                "public_endpoints": 0,
                "final_reserve_access": 0,
            },
        }
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        return report
    except Exception as exc:
        conn.execute(
            "UPDATE s5c_runs SET status='failed',failure_json=? WHERE run_id=?",
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
    parser.add_argument("--execute-s5c-championship", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--report")
    args = parser.parse_args()
    if not args.execute_s5c_championship:
        raise SystemExit(
            "refusing to execute CHAMPIONSHIP without "
            "--execute-s5c-championship"
        )
    if not args.db or not args.report:
        raise SystemExit("--db and --report are required")
    result = run_championship(Path(args.db), Path(args.report))
    print(json.dumps({
        "run_id": result["run_id"],
        "winner_genome_id": result["winner"]["genome_id"],
        "deterministic_digest": result["deterministic_digest"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
