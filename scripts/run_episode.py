#!/usr/bin/env python3
"""Runs one real episode against the accepted S1 dataset through the full
stack: DB-backed episode row, ReplayEngine + AgentView, replay_audit rows
per step. This is the S2.13 "trivial probe consumer" -- it receives
observations, requests legal history, and records observation hashes. No
trading behavior (S4) is implemented here.

Also used as acceptance evidence: run twice with identical parameters and
diff the two hash sequences to prove determinism through the DB-integrated
path, not just the in-memory engine tested by tests/test_replay.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from lib import models
from lib.db import connect, DB_PATH
from lib.replay import AgentView, ReplayEngine, observation_hash

ROOT = Path(__file__).resolve().parents[1]

PROBE_GENOME = {"kind": "s2_probe", "note": "not a trading strategy; observes and records hashes only"}


def ensure_probe_agent(conn) -> tuple[str, str]:
    """A minimal genome/agent so episode rows satisfy their FK
    constraints, without building S4's real control agent."""
    genome_id = models.create_genome(conn, PROBE_GENOME)
    row = conn.execute(
        "SELECT agent_id FROM agents WHERE genome_id = ? ORDER BY created_at LIMIT 1", (genome_id,)
    ).fetchone()
    if row:
        return row["agent_id"], genome_id
    agent_id = models.create_agent(conn, genome_id, generation=0)
    conn.commit()
    return agent_id, genome_id


def ensure_probe_experiment(conn, agent_id: str, genome_id: str, dataset_revision: str,
                             start_date: str, end_date: str) -> str:
    experiment_id = models.create_experiment(
        conn, code_revision="s2-probe", code_dirty=False, dataset_revision=dataset_revision,
        random_seed=0, agent_id=agent_id, genome_id_=genome_id,
        start_state={"note": "S2 replay-integrity probe, not a real portfolio"},
        replay_window_start=start_date, replay_window_end=end_date,
        execution_assumptions={"note": "no execution in S2"},
    )
    conn.commit()
    return experiment_id


def run_episode(dataset_root, dataset_revision: str, start_date: str, end_date: str, *,
                 masked_time: bool = False, random_seed: int = 0, history_bars: int = 20,
                 db_path=None) -> dict:
    conn = connect(db_path) if db_path else connect()
    agent_id, genome_id = ensure_probe_agent(conn)
    experiment_id = ensure_probe_experiment(conn, agent_id, genome_id, dataset_revision,
                                             start_date, end_date)

    engine = ReplayEngine(dataset_root, dataset_revision, start_date, end_date,
                           masked_time=masked_time, random_seed=random_seed)
    episode_id = models.create_episode(
        conn, experiment_id, agent_id, dataset_revision=dataset_revision,
        start_ts=start_date, end_ts=end_date, masked_time=masked_time, random_seed=random_seed,
        label="S2 probe episode",
    )
    conn.execute("UPDATE episodes SET status = 'RUNNING' WHERE episode_id = ?", (episode_id,))
    conn.commit()

    view = AgentView(engine)
    hashes = []
    illegal_attempts_blocked = 0

    step = 0
    while True:
        full = engine.observe()  # orchestration-side: has the true timestamp, for audit
        obs = view.observe()     # agent-side: whitelisted, possibly masked
        _ = view.history("SPY", history_bars)  # probe: request legal history, discard

        h = observation_hash(obs)
        hashes.append(h)
        symbols_visible = sorted(s for s, f in obs["assets"].items() if f["available"])

        models.create_replay_audit(
            conn, episode_id, step_index=step, true_ts=full["_true_timestamp"],
            masked_day=full["episode_day_index"] if masked_time else None,
            symbols_visible=symbols_visible, observation_hash=h, dataset_revision=dataset_revision,
        )

        # probe: attempt a couple of illegal accesses; each must fail
        try:
            view.advance()
            raise AssertionError("AgentView.advance should not exist")
        except AttributeError:
            illegal_attempts_blocked += 1

        more = engine.advance()
        models.update_episode_progress(
            conn, episode_id, current_ts=engine.current_timestamp if more else full["_true_timestamp"],
            status=engine.status,
        )
        conn.commit()
        step += 1
        if not more:
            break

    conn.close()
    return {
        "episode_id": episode_id,
        "experiment_id": experiment_id,
        "step_count": step,
        "hashes": hashes,
        "final_status": engine.status,
        "illegal_attempts_blocked": illegal_attempts_blocked,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-revision", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--masked", action="store_true")
    p.add_argument("--runs", type=int, default=2)
    args = p.parse_args()

    all_hashes = []
    for i in range(args.runs):
        result = run_episode(ROOT, args.dataset_revision, args.start, args.end,
                              masked_time=args.masked)
        all_hashes.append(result["hashes"])
        print(f"run {i+1}: episode={result['episode_id']} steps={result['step_count']} "
              f"status={result['final_status']} illegal_blocked={result['illegal_attempts_blocked']} "
              f"first_hash={result['hashes'][0][:16]} last_hash={result['hashes'][-1][:16]}")

    if args.runs > 1:
        identical = all(h == all_hashes[0] for h in all_hashes[1:])
        print(f"\nhash sequences identical across {args.runs} runs: {identical}")
