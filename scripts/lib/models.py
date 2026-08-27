"""Thin record-creation helpers over the experiment-model schema.
Each function inserts one row and returns its id; callers own the
sqlite3.Connection and its commit/rollback."""
import sqlite3
from datetime import datetime, timezone

from .ids import canonical_json, genome_id, new_id


class InvalidExperimentTransition(RuntimeError):
    """Raised when an experiment lifecycle compare-and-set does not match."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_genome(conn: sqlite3.Connection, genome: dict) -> str:
    gid = genome_id(genome)
    conn.execute(
        "INSERT OR IGNORE INTO genomes (genome_id, genome_json, created_at) VALUES (?, ?, ?)",
        (gid, canonical_json(genome), _now()),
    )
    return gid


def create_agent(conn: sqlite3.Connection, genome_id_: str, generation: int,
                  parent_agent_id: str | None = None) -> str:
    aid = new_id("agt")
    conn.execute(
        "INSERT INTO agents (agent_id, genome_id, parent_agent_id, generation, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, genome_id_, parent_agent_id, generation, _now()),
    )
    return aid


def create_experiment(conn: sqlite3.Connection, *, code_revision: str, code_dirty: bool,
                       dataset_revision: str, random_seed: int, agent_id: str, genome_id_: str,
                       start_state: dict, replay_window_start: str, replay_window_end: str,
                       execution_assumptions: dict) -> str:
    eid = new_id("exp")
    conn.execute(
        "INSERT INTO experiments (experiment_id, code_revision, code_dirty, dataset_revision, "
        "random_seed, agent_id, genome_id, start_state_json, replay_window_start, "
        "replay_window_end, execution_assumptions_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (eid, code_revision, int(code_dirty), dataset_revision, random_seed, agent_id, genome_id_,
         canonical_json(start_state), replay_window_start, replay_window_end,
         canonical_json(execution_assumptions), _now()),
    )
    return eid


def mark_experiment_running(conn: sqlite3.Connection, experiment_id: str) -> None:
    """Atomically transition exactly pending -> running."""
    result = conn.execute(
        "UPDATE experiments SET status = 'running' "
        "WHERE experiment_id = ? AND status = 'pending'",
        (experiment_id,),
    )
    if result.rowcount != 1:
        raise InvalidExperimentTransition(
            f"experiment {experiment_id} is not pending; cannot mark running"
        )


def complete_experiment(conn: sqlite3.Connection, experiment_id: str, final_result: dict) -> None:
    """Atomically transition running -> completed only after all episodes complete."""
    result = conn.execute(
        "UPDATE experiments SET status = 'completed', final_result_json = ?, completed_at = ? "
        "WHERE experiment_id = ? AND status = 'running' "
        "AND EXISTS (SELECT 1 FROM episodes WHERE experiment_id = ?) "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM episodes WHERE experiment_id = ? AND status != 'COMPLETED'"
        ")",
        (canonical_json(final_result), _now(), experiment_id, experiment_id, experiment_id),
    )
    if result.rowcount != 1:
        raise InvalidExperimentTransition(
            f"experiment {experiment_id} is not running with only completed episodes"
        )


def fail_experiment(conn: sqlite3.Connection, experiment_id: str, failure_result: dict) -> None:
    """Atomically transition pending/running -> failed with auditable details."""
    result = conn.execute(
        "UPDATE experiments SET status = 'failed', final_result_json = ?, completed_at = ? "
        "WHERE experiment_id = ? AND status IN ('pending', 'running')",
        (canonical_json(failure_result), _now(), experiment_id),
    )
    if result.rowcount != 1:
        raise InvalidExperimentTransition(
            f"experiment {experiment_id} is not pending/running; cannot mark failed"
        )


def fail_incomplete_episode(conn: sqlite3.Connection, episode_id: str) -> None:
    """Mark an episode failed without rewriting an already terminal episode."""
    conn.execute(
        "UPDATE episodes SET status = 'FAILED' "
        "WHERE episode_id = ? AND status IN ('CREATED', 'RUNNING')",
        (episode_id,),
    )


def create_episode(conn: sqlite3.Connection, experiment_id: str, agent_id: str, *,
                    dataset_revision: str, start_ts: str, end_ts: str,
                    masked_time: bool = False, random_seed: int = 0,
                    label: str | None = None) -> str:
    epid = new_id("epi")
    conn.execute(
        "INSERT INTO episodes (episode_id, experiment_id, agent_id, dataset_revision, label, "
        "start_ts, end_ts, current_ts, masked_time, random_seed, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CREATED', ?)",
        (epid, experiment_id, agent_id, dataset_revision, label, start_ts, end_ts, start_ts,
         int(masked_time), random_seed, _now()),
    )
    return epid


def update_episode_progress(conn: sqlite3.Connection, episode_id: str, *,
                             current_ts: str, status: str) -> None:
    conn.execute(
        "UPDATE episodes SET current_ts = ?, status = ? WHERE episode_id = ?",
        (current_ts, status, episode_id),
    )


def create_replay_audit(conn: sqlite3.Connection, episode_id: str, *, step_index: int,
                         true_ts: str, masked_day: int | None, symbols_visible: list[str],
                         observation_hash: str, dataset_revision: str) -> str:
    aid = new_id("aud")
    conn.execute(
        "INSERT INTO replay_audit (audit_id, episode_id, step_index, true_ts, masked_day, "
        "symbols_visible_json, observation_hash, dataset_revision, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, episode_id, step_index, true_ts, masked_day, canonical_json(sorted(symbols_visible)),
         observation_hash, dataset_revision, _now()),
    )
    return aid


def create_decision(conn: sqlite3.Connection, episode_id: str, agent_id: str,
                     simulated_ts: str, payload: dict) -> str:
    did = new_id("dec")
    conn.execute(
        "INSERT INTO decisions (decision_id, episode_id, agent_id, simulated_ts, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (did, episode_id, agent_id, simulated_ts, canonical_json(payload), _now()),
    )
    return did


def create_order(conn: sqlite3.Connection, decision_id: str, episode_id: str, *, symbol: str,
                  side: str, quantity: int, order_type: str, submitted_ts: str,
                  limit_price_cents: int | None = None) -> str:
    oid = new_id("ord")
    conn.execute(
        "INSERT INTO orders (order_id, decision_id, episode_id, symbol, side, quantity, "
        "order_type, limit_price_cents, submitted_ts, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (oid, decision_id, episode_id, symbol, side, quantity, order_type,
         limit_price_cents, submitted_ts, _now()),
    )
    return oid


def create_fill(conn: sqlite3.Connection, order_id: str, *, fill_ts: str, fill_price_cents: int,
                 fill_quantity: int, commission_cents: int = 0, slippage_cents: int = 0) -> str:
    fid = new_id("fil")
    conn.execute(
        "INSERT INTO fills (fill_id, order_id, fill_ts, fill_price_cents, fill_quantity, "
        "commission_cents, slippage_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, order_id, fill_ts, fill_price_cents, fill_quantity,
         commission_cents, slippage_cents, _now()),
    )
    return fid
