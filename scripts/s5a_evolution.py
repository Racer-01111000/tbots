#!/usr/bin/env python3
"""S5A deterministic DEVELOPMENT-only population evolution."""
import argparse
import hashlib
import json
import random
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from genome_control import CONTROL_GENOME
from lib import models
from lib.db import DB_PATH, init_db
from lib.gitrev import get_code_revision
from lib.ids import canonical_json, genome_id
from s5_boundary import DEVELOPMENT_HASH, load_authorized_manifest
from s5a_config import (
    CONTROL_GENOME_ID,
    DATASET_REVISION,
    DEVELOPMENT_LANE_HASH,
    EPISODE_HASH,
    EPISODE_PROTOCOL,
    EVOLUTION_SEED,
    FINAL_GENERATION,
    FITNESS_HASH,
    MUTATION_HASH,
    MUTATION_PROTOCOL,
    POPULATION_HASH,
    POPULATION_PROTOCOL,
    POPULATION_SIZE,
    ROOT,
    derive_seed,
    mutate_genome,
    population_diversity,
    random_genome,
    validate_genome,
)
from s5a_development_bundle import (
    assert_isolated,
    load_authorized_development_bundle,
)
from s5a_evaluator import IndependentVerifierFailure, evaluate_genome


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_id(prefix: str, *parts) -> str:
    digest = hashlib.sha256(canonical_json(list(parts)).encode()).hexdigest()
    return f"{prefix}_{digest}"


def result_comparison_hash(result: dict) -> str:
    episodes = []
    for metrics in result["episode_metrics"]:
        cleaned = dict(metrics)
        cleaned.pop("verifier_checks", None)
        episodes.append(cleaned)
    return hashlib.sha256(
        canonical_json({"episodes": episodes, "aggregate": result["aggregate"]}).encode()
    ).hexdigest()


def evolution_run_id(code_revision: str, bundle_revision: str) -> str:
    return deterministic_id(
        "evo",
        "S5A",
        code_revision,
        DATASET_REVISION,
        DEVELOPMENT_LANE_HASH,
        bundle_revision,
        EVOLUTION_SEED,
        EPISODE_HASH,
        FITNESS_HASH,
        MUTATION_HASH,
        POPULATION_HASH,
    )


def agent_id_for(run_id: str, generation: int, role: str, gid: str,
                 parent_agent_id: str | None, creation_seed: int) -> str:
    return deterministic_id(
        "agt", run_id, generation, role, gid, parent_agent_id, creation_seed
    )


def _register_organism(conn, run_id: str, generation: int, role: str, genome: dict, *,
                       parent=None, mutation=None, magnitude: float = 0.0,
                       creation_seed: int, is_control: bool = False) -> dict:
    validate_genome(genome)
    gid = models.create_genome(conn, genome)
    parent_agent_id = parent["agent_id"] if parent else None
    parent_genome_id = parent["genome_id"] if parent else None
    agent_id = agent_id_for(
        run_id, generation, role, gid, parent_agent_id, creation_seed
    )
    created_at = now()
    conn.execute(
        "INSERT INTO agents "
        "(agent_id, genome_id, parent_agent_id, generation, status, created_at) "
        "VALUES (?, ?, ?, ?, 'active', ?)",
        (agent_id, gid, parent_agent_id, generation, created_at),
    )
    conn.execute(
        "INSERT INTO evolution_organisms "
        "(agent_id, run_id, genome_id, generation, parent_agent_id, parent_genome_id, "
        "creation_role, mutation_json, mutation_magnitude, creation_seed, "
        "is_control_anchor, state, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (
            agent_id,
            run_id,
            gid,
            generation,
            parent_agent_id,
            parent_genome_id,
            role,
            canonical_json(mutation or {}),
            magnitude,
            creation_seed,
            int(is_control),
            created_at,
        ),
    )
    return {
        "agent_id": agent_id,
        "genome_id": gid,
        "genome": genome,
        "creation_generation": generation,
        "role": role,
        "parent_agent_id": parent_agent_id,
        "parent_genome_id": parent_genome_id,
        "creation_seed": creation_seed,
        "is_control": is_control,
    }


def _record_collision(conn, run_id: str, generation: int, role: str, parent,
                      candidate_gid: str, creation_seed: int, attempt: int) -> None:
    collision_id = deterministic_id(
        "col", run_id, generation, role,
        parent["agent_id"] if parent else None, candidate_gid, creation_seed, attempt,
    )
    conn.execute(
        "INSERT INTO evolution_duplicate_collisions "
        "(collision_id, run_id, generation, slot_role, parent_agent_id, "
        "candidate_genome_id, creation_seed, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            collision_id,
            run_id,
            generation,
            role,
            parent["agent_id"] if parent else None,
            candidate_gid,
            creation_seed,
            "genome_id already used in this run",
        ),
    )


def _unique_immigrant(conn, run_id: str, generation: int, slot: int,
                      globally_used: set[str]) -> dict:
    limit = MUTATION_PROTOCOL["duplicate_retry_limit_per_slot"]
    for attempt in range(limit):
        seed = derive_seed("immigrant", generation, slot, attempt)
        genome = random_genome(random.Random(seed))
        gid = genome_id(genome)
        if gid in globally_used:
            _record_collision(
                conn, run_id, generation, "immigrant", None, gid, seed, attempt
            )
            continue
        globally_used.add(gid)
        return _register_organism(
            conn,
            run_id,
            generation,
            "immigrant",
            genome,
            creation_seed=seed,
        )
    raise RuntimeError("duplicate retry limit exhausted for immigrant")


def _unique_child(conn, run_id: str, generation: int, slot: int, survivors: list[dict],
                  globally_used: set[str]) -> dict:
    limit = MUTATION_PROTOCOL["duplicate_retry_limit_per_slot"]
    for attempt in range(limit):
        seed = derive_seed("child", generation, slot, attempt)
        rng = random.Random(seed)
        parent = survivors[rng.randrange(len(survivors))]
        genome, mutation, magnitude = mutate_genome(parent["genome"], rng)
        gid = genome_id(genome)
        if gid in globally_used:
            _record_collision(
                conn, run_id, generation, "child", parent, gid, seed, attempt
            )
            continue
        globally_used.add(gid)
        return _register_organism(
            conn,
            run_id,
            generation,
            "child",
            genome,
            parent=parent,
            mutation=mutation,
            magnitude=magnitude,
            creation_seed=seed,
        )
    raise RuntimeError("duplicate retry limit exhausted for child")


def initial_population(conn, run_id: str) -> tuple[list[dict], set[str]]:
    anchor_seed = derive_seed("control_anchor")
    anchor = _register_organism(
        conn,
        run_id,
        0,
        "control_anchor",
        dict(CONTROL_GENOME),
        creation_seed=anchor_seed,
        is_control=True,
    )
    if anchor["genome_id"] != CONTROL_GENOME_ID:
        raise RuntimeError("control anchor identity changed")
    population = [anchor]
    used = {anchor["genome_id"]}
    for slot in range(1, POPULATION_SIZE):
        population.append(_unique_immigrant(conn, run_id, 0, slot, used))
    return population, used


def next_population(conn, run_id: str, generation: int, survivors: list[dict],
                    globally_used: set[str]) -> list[dict]:
    rules = POPULATION_PROTOCOL["subsequent_generation"]
    if len(survivors) != rules["elites"]:
        raise RuntimeError("survivor count does not match frozen elite count")
    population = []
    for survivor in survivors:
        role = "control_anchor" if survivor["is_control"] else "elite"
        population.append({**survivor, "membership_role": role})
    for slot in range(rules["mutated_children"]):
        population.append(
            _unique_child(conn, run_id, generation, slot, survivors, globally_used)
        )
    for slot in range(rules["random_immigrants"]):
        population.append(
            _unique_immigrant(conn, run_id, generation, slot, globally_used)
        )
    if len(population) != POPULATION_SIZE:
        raise RuntimeError("population size drifted from 50")
    gids = [member["genome_id"] for member in population]
    if len(set(gids)) != POPULATION_SIZE:
        raise RuntimeError("population contains duplicate genome slots")
    return population


def _insert_population(conn, run_id: str, generation: int, population: list[dict]) -> None:
    for slot, member in enumerate(population):
        role = member.get("membership_role", member["role"])
        conn.execute(
            "INSERT INTO evolution_population "
            "(run_id, generation, slot_index, agent_id, genome_id, membership_role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, generation, slot, member["agent_id"], member["genome_id"], role),
        )


def _start_evaluation(conn, run_id: str, generation: int, member: dict, code_revision: str,
                      kind: str, verification_role: str | None) -> tuple[str, str, list[tuple]]:
    evaluation_id = deterministic_id(
        "eval", run_id, generation, member["agent_id"], kind, verification_role
    )
    experiment_id = deterministic_id("exp", evaluation_id)
    created_at = now()
    conn.execute(
        "INSERT INTO experiments "
        "(experiment_id, code_revision, code_dirty, dataset_revision, random_seed, "
        "agent_id, genome_id, start_state_json, replay_window_start, replay_window_end, "
        "execution_assumptions_json, status, created_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            experiment_id,
            code_revision,
            DATASET_REVISION,
            member["creation_seed"],
            member["agent_id"],
            member["genome_id"],
            canonical_json({
                "starting_cash_cents": EPISODE_PROTOCOL["starting_cash_cents"],
                "fresh_state_per_episode": True,
            }),
            EPISODE_PROTOCOL["lane_start"],
            EPISODE_PROTOCOL["lane_end"],
            canonical_json({
                "lane_manifest_hash": DEVELOPMENT_LANE_HASH,
                "episode_manifest_hash": EPISODE_HASH,
                "fitness_formula_hash": FITNESS_HASH,
                "commission_bps": 5,
                "slippage_bps": 5,
                "fill_price": "next-session raw open",
                "verification": kind == "verification",
            }),
            created_at,
        ),
    )
    models.mark_experiment_running(conn, experiment_id)
    conn.execute(
        "INSERT INTO evolution_evaluations "
        "(evaluation_id, run_id, generation, agent_id, experiment_id, evaluation_kind, "
        "verification_role, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)",
        (
            evaluation_id,
            run_id,
            generation,
            member["agent_id"],
            experiment_id,
            kind,
            verification_role,
            created_at,
        ),
    )
    episode_rows = []
    for episode in EPISODE_PROTOCOL["episodes"]:
        episode_id = deterministic_id("epi", evaluation_id, episode["episode_index"])
        conn.execute(
            "INSERT INTO episodes "
            "(episode_id, experiment_id, agent_id, dataset_revision, label, start_ts, end_ts, "
            "current_ts, masked_time, random_seed, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'RUNNING', ?)",
            (
                episode_id,
                experiment_id,
                member["agent_id"],
                DATASET_REVISION,
                f"S5A generation {generation} {kind} episode {episode['episode_index']}",
                episode["start_date"],
                episode["end_date"],
                episode["start_date"],
                member["creation_seed"],
                created_at,
            ),
        )
        episode_rows.append((episode, episode_id))
    conn.commit()
    return evaluation_id, experiment_id, episode_rows


def persist_evaluation(conn, bundle, run_id: str, generation: int, member: dict,
                       code_revision: str, *, kind: str = "fitness",
                       verification_role: str | None = None) -> dict:
    evaluation_id, experiment_id, episode_rows = _start_evaluation(
        conn, run_id, generation, member, code_revision, kind, verification_role
    )
    try:
        result = evaluate_genome(bundle, member["genome"], verify=kind == "verification")
        for (episode, episode_id), metrics in zip(
            episode_rows, result["episode_metrics"], strict=True
        ):
            metrics_json = canonical_json(metrics)
            metrics_hash = hashlib.sha256(metrics_json.encode()).hexdigest()
            conn.execute(
                "UPDATE episodes SET current_ts = ?, status = 'COMPLETED' WHERE episode_id = ?",
                (episode["end_date"], episode_id),
            )
            conn.execute(
                "INSERT INTO evolution_episode_results "
                "(result_id, evaluation_id, episode_id, episode_index, metrics_json, metrics_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    deterministic_id("eres", evaluation_id, episode["episode_index"]),
                    evaluation_id,
                    episode_id,
                    episode["episode_index"],
                    metrics_json,
                    metrics_hash,
                ),
            )
        models.complete_experiment(
            conn,
            experiment_id,
            {
                "status": "completed",
                "evaluation_id": evaluation_id,
                "evaluation_kind": kind,
                "metrics_hash": result["metrics_hash"],
                "aggregate": result["aggregate"],
            },
        )
        conn.execute(
            "UPDATE evolution_evaluations SET status = 'completed', fitness = ?, "
            "metrics_json = ?, metrics_hash = ?, completed_at = ? WHERE evaluation_id = ?",
            (
                result["aggregate"]["fitness"],
                canonical_json(result),
                result["metrics_hash"],
                now(),
                evaluation_id,
            ),
        )
        conn.commit()
        return {**result, "evaluation_id": evaluation_id, "experiment_id": experiment_id}
    except Exception as exc:
        conn.rollback()
        for _, episode_id in episode_rows:
            models.fail_incomplete_episode(conn, episode_id)
        models.fail_experiment(
            conn,
            experiment_id,
            {
                "status": "failed",
                "evaluation_id": evaluation_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "verifier_outcome": getattr(exc, "outcome", None),
            },
        )
        conn.execute(
            "UPDATE evolution_evaluations SET status = 'failed', metrics_json = ?, "
            "completed_at = ? WHERE evaluation_id = ?",
            (
                canonical_json({
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "verifier_outcome": getattr(exc, "outcome", None),
                }),
                now(),
                evaluation_id,
            ),
        )
        conn.commit()
        raise


def _rank_population(population: list[dict]) -> list[dict]:
    return sorted(population, key=lambda member: (-member["fitness"], member["genome_id"]))


def _select_survivors(ranked: list[dict]) -> list[dict]:
    anchor = next(member for member in ranked if member["is_control"])
    non_control = [member for member in ranked if not member["is_control"]]
    survivors = [anchor, *non_control[:9]]
    return sorted(survivors, key=lambda member: (-member["fitness"], member["genome_id"]))


def _retire(conn, members: list[dict]) -> None:
    retired_at = now()
    for member in members:
        conn.execute(
            "UPDATE evolution_organisms SET state = 'retired', retired_at = ? "
            "WHERE agent_id = ? AND state = 'active'",
            (retired_at, member["agent_id"]),
        )
        conn.execute(
            "UPDATE agents SET status = 'graveyard' WHERE agent_id = ? AND status = 'active'",
            (member["agent_id"],),
        )


def _verify_generation(conn, bundle, run_id: str, generation: int, ranked: list[dict],
                       survivors: list[dict], code_revision: str) -> list[dict]:
    champion = ranked[0]
    worst_survivor = survivors[-1]
    excluded = {champion["agent_id"], worst_survivor["agent_id"]}
    candidates = [member for member in ranked if member["agent_id"] not in excluded]
    random_member = candidates[
        random.Random(derive_seed("verification_random", generation)).randrange(len(candidates))
    ]
    roles = [
        ("champion", champion),
        ("worst_survivor", worst_survivor),
        ("deterministic_random", random_member),
    ]
    outcomes = []
    for role, member in roles:
        verification = persist_evaluation(
            conn,
            bundle,
            run_id,
            generation,
            member,
            code_revision,
            kind="verification",
            verification_role=role,
        )
        equivalent = (
            result_comparison_hash(verification)
            == result_comparison_hash(member["evaluation_result"])
        )
        outcome = {
            "role": role,
            "agent_id": member["agent_id"],
            "genome_id": member["genome_id"],
            "status": "agreed" if equivalent else "disagreed",
            "primary_comparison_hash": result_comparison_hash(member["evaluation_result"]),
            "verification_comparison_hash": result_comparison_hash(verification),
            "verifier_checks": sum(
                row["verifier_checks"] for row in verification["episode_metrics"]
            ),
        }
        verification_id = deterministic_id("ver", run_id, generation, role)
        conn.execute(
            "INSERT INTO evolution_verifications "
            "(verification_id, run_id, generation, agent_id, role, evaluation_id, "
            "status, outcome_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verification_id,
                run_id,
                generation,
                member["agent_id"],
                role,
                verification["evaluation_id"],
                outcome["status"],
                canonical_json(outcome),
            ),
        )
        conn.commit()
        if not equivalent:
            raise IndependentVerifierFailure(
                -1, generation, "aggregate", ["primary/verification metric mismatch"]
            )
        outcomes.append(outcome)
    return outcomes


def _generation_report(conn, run_id: str, generation: int, ranked: list[dict],
                       diversity: float, retired_count: int,
                       verification_outcomes: list[dict]) -> dict:
    fitnesses = [member["fitness"] for member in ranked]
    collisions = conn.execute(
        "SELECT slot_role, COUNT(*) FROM evolution_duplicate_collisions "
        "WHERE run_id = ? AND generation = ? GROUP BY slot_role",
        (run_id, generation),
    ).fetchall()
    collision_counts = {row[0]: row[1] for row in collisions}
    rules = POPULATION_PROTOCOL["subsequent_generation"]
    report = {
        "generation": generation,
        "best_fitness": ranked[0]["fitness"],
        "median_fitness": statistics.median(fitnesses),
        "worst_fitness": ranked[-1]["fitness"],
        "champion_agent_id": ranked[0]["agent_id"],
        "champion_genome_id": ranked[0]["genome_id"],
        "unique_genomes": len({member["genome_id"] for member in ranked}),
        "diversity_measure": diversity,
        "drawdown_halts": sum(
            member["evaluation_result"]["aggregate"]["drawdown_halt_count"]
            for member in ranked
        ),
        "retired_count": retired_count,
        "elites_retained": 0 if generation == 0 else rules["elites"],
        "children_produced": 0 if generation == 0 else rules["mutated_children"],
        "immigrants_introduced": (
            POPULATION_PROTOCOL["generation_zero"]["random_immigrants"]
            if generation == 0 else rules["random_immigrants"]
        ),
        "duplicate_mutations_rejected": collision_counts.get("child", 0),
        "duplicate_immigrants_rejected": collision_counts.get("immigrant", 0),
        "control_anchor_fitness": next(
            member["fitness"] for member in ranked if member["is_control"]
        ),
        "verification": verification_outcomes,
        "champion_episode_metrics": ranked[0]["evaluation_result"]["episode_metrics"],
    }
    report_json = canonical_json(report)
    conn.execute(
        "INSERT INTO evolution_generation_reports "
        "(run_id, generation, report_json, report_hash) VALUES (?, ?, ?, ?)",
        (run_id, generation, report_json, hashlib.sha256(report_json.encode()).hexdigest()),
    )
    return report


def _genealogy(conn, agent_id: str) -> list[dict]:
    chain = []
    current = agent_id
    while current is not None:
        row = conn.execute(
            "SELECT agent_id, genome_id, generation, parent_agent_id, parent_genome_id, "
            "creation_role, mutation_json, mutation_magnitude, creation_seed "
            "FROM evolution_organisms WHERE agent_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"missing genealogy row: {current}")
        chain.append(dict(row))
        current = row["parent_agent_id"]
    return chain


def _freeze_top10(conn, run_id: str, ranked: list[dict]) -> list[dict]:
    top10 = ranked[:10]
    top_ids = {member["agent_id"] for member in top10}
    active = conn.execute(
        "SELECT agent_id FROM evolution_organisms WHERE run_id = ? AND state = 'active'",
        (run_id,),
    ).fetchall()
    _retire(conn, [
        {"agent_id": row["agent_id"]} for row in active if row["agent_id"] not in top_ids
    ])
    frozen = []
    for rank, member in enumerate(top10, 1):
        genealogy = _genealogy(conn, member["agent_id"])
        conn.execute(
            "UPDATE evolution_organisms SET state = 'frozen' WHERE agent_id = ?",
            (member["agent_id"],),
        )
        conn.execute(
            "UPDATE agents SET status = 'champion' WHERE agent_id = ?",
            (member["agent_id"],),
        )
        conn.execute(
            "INSERT INTO evolution_frozen_top10 "
            "(run_id, rank, agent_id, genome_id, fitness, genealogy_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                rank,
                member["agent_id"],
                member["genome_id"],
                member["fitness"],
                canonical_json(genealogy),
            ),
        )
        frozen.append({
            "rank": rank,
            "agent_id": member["agent_id"],
            "genome_id": member["genome_id"],
            "fitness": member["fitness"],
            "genome": member["genome"],
            "genealogy": genealogy,
        })
    return frozen


def deterministic_snapshot(conn, run_id: str) -> dict:
    queries = {
        "organisms": (
            "SELECT agent_id, genome_id, generation, parent_agent_id, parent_genome_id, "
            "creation_role, mutation_json, mutation_magnitude, creation_seed, "
            "is_control_anchor, fitness, fitness_json, state FROM evolution_organisms "
            "WHERE run_id = ? ORDER BY agent_id"
        ),
        "population": (
            "SELECT generation, slot_index, agent_id, genome_id, membership_role, rank, fitness "
            "FROM evolution_population WHERE run_id = ? ORDER BY generation, slot_index"
        ),
        "generation_reports": (
            "SELECT generation, report_json, report_hash FROM evolution_generation_reports "
            "WHERE run_id = ? ORDER BY generation"
        ),
        "verifications": (
            "SELECT generation, agent_id, role, status, outcome_json FROM evolution_verifications "
            "WHERE run_id = ? ORDER BY generation, role"
        ),
        "collisions": (
            "SELECT generation, slot_role, parent_agent_id, candidate_genome_id, creation_seed, reason "
            "FROM evolution_duplicate_collisions WHERE run_id = ? ORDER BY collision_id"
        ),
        "top10": (
            "SELECT rank, agent_id, genome_id, fitness, genealogy_json "
            "FROM evolution_frozen_top10 WHERE run_id = ? ORDER BY rank"
        ),
    }
    return {
        name: [dict(row) for row in conn.execute(query, (run_id,)).fetchall()]
        for name, query in queries.items()
    }


def run_evolution(db_path: Path = DB_PATH, *, write_report: bool = True,
                  expected_code_revision: str | None = None) -> dict:
    manifest = load_authorized_manifest(DEVELOPMENT_HASH)
    if manifest.manifest_hash != DEVELOPMENT_LANE_HASH or manifest.lane != "DEVELOPMENT":
        raise RuntimeError("S5A requires the authorized DEVELOPMENT manifest")
    code_revision, code_dirty = get_code_revision(str(ROOT))
    if code_dirty:
        raise RuntimeError("S5A execution requires a clean committed code revision")
    if expected_code_revision is not None and code_revision != expected_code_revision:
        raise RuntimeError("runtime code revision does not match reproduction target")

    bundle = load_authorized_development_bundle()
    isolation = assert_isolated(bundle)

    conn = init_db(db_path=db_path)
    conn.row_factory = sqlite3.Row
    run_id = evolution_run_id(code_revision, bundle.bundle_revision)
    if conn.execute("SELECT 1 FROM evolution_runs WHERE run_id = ?", (run_id,)).fetchone():
        conn.close()
        raise RuntimeError(f"evolution run already exists: {run_id}")
    created_at = now()
    conn.execute(
        "INSERT INTO evolution_runs "
        "(run_id, code_revision, code_dirty, dataset_revision, lane_manifest_hash, "
        "development_bundle_revision, bundle_manifest_hash, "
        "evolution_seed, population_size, final_generation, episode_manifest_hash, "
        "fitness_formula_hash, mutation_bounds_hash, population_rules_hash, status, created_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
        (
            run_id,
            code_revision,
            DATASET_REVISION,
            DEVELOPMENT_LANE_HASH,
            bundle.bundle_revision,
            bundle.bundle_manifest_hash,
            EVOLUTION_SEED,
            POPULATION_SIZE,
            FINAL_GENERATION,
            EPISODE_HASH,
            FITNESS_HASH,
            MUTATION_HASH,
            POPULATION_HASH,
            created_at,
        ),
    )
    conn.commit()
    try:
        population, globally_used = initial_population(conn, run_id)
        generation_reports = []
        control_fitness = None
        final_ranked = None

        for generation in range(FINAL_GENERATION + 1):
            _insert_population(conn, run_id, generation, population)
            conn.commit()
            for index, member in enumerate(population, 1):
                result = persist_evaluation(
                    conn, bundle, run_id, generation, member, code_revision
                )
                member["evaluation_result"] = result
                member["fitness"] = result["aggregate"]["fitness"]
                conn.execute(
                    "UPDATE evolution_organisms SET fitness = ?, fitness_json = ? "
                    "WHERE agent_id = ?",
                    (
                        member["fitness"],
                        canonical_json(result["aggregate"]),
                        member["agent_id"],
                    ),
                )
                if index % 10 == 0:
                    print(
                        f"generation {generation}: evaluated {index}/{POPULATION_SIZE}",
                        flush=True,
                    )

            ranked = _rank_population(population)
            for rank, member in enumerate(ranked, 1):
                conn.execute(
                    "UPDATE evolution_population SET rank = ?, fitness = ? "
                    "WHERE run_id = ? AND generation = ? AND agent_id = ?",
                    (rank, member["fitness"], run_id, generation, member["agent_id"]),
                )
            survivors = _select_survivors(ranked)
            verification_outcomes = _verify_generation(
                conn, bundle, run_id, generation, ranked, survivors, code_revision
            )
            isolation = assert_isolated(bundle)
            diversity = population_diversity([member["genome"] for member in population])
            if generation < FINAL_GENERATION:
                survivor_ids = {member["agent_id"] for member in survivors}
                retired = [
                    member for member in population if member["agent_id"] not in survivor_ids
                ]
                _retire(conn, retired)
                retired_count = len(retired)
            else:
                retired_count = POPULATION_SIZE - 10
            report = _generation_report(
                conn,
                run_id,
                generation,
                ranked,
                diversity,
                retired_count,
                verification_outcomes,
            )
            generation_reports.append(report)
            conn.commit()
            print(
                f"generation {generation} complete: best={report['best_fitness']:.12f} "
                f"median={report['median_fitness']:.12f} "
                f"worst={report['worst_fitness']:.12f} "
                f"diversity={report['diversity_measure']:.12f}",
                flush=True,
            )
            if generation == 0:
                control_fitness = report["control_anchor_fitness"]
            if generation < FINAL_GENERATION:
                population = next_population(
                    conn, run_id, generation + 1, survivors, globally_used
                )
                conn.commit()
            else:
                final_ranked = ranked

        frozen_top10 = _freeze_top10(conn, run_id, final_ranked)
        conn.commit()
        snapshot = deterministic_snapshot(conn, run_id)
        digest = hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
        result = {
            "run_id": run_id,
            "code_revision": code_revision,
            "evolution_seed": EVOLUTION_SEED,
            "episode_manifest_hash": EPISODE_HASH,
            "fitness_formula_hash": FITNESS_HASH,
            "mutation_bounds_hash": MUTATION_HASH,
            "population_rules_hash": POPULATION_HASH,
            "development_bundle_revision": bundle.bundle_revision,
            "bundle_manifest_hash": bundle.bundle_manifest_hash,
            "isolation_instrumentation": isolation,
            "control_anchor_development_fitness": control_fitness,
            "generation_reports": generation_reports,
            "frozen_top10": frozen_top10,
            "deterministic_digest": digest,
        }
        conn.execute(
            "UPDATE evolution_runs SET status = 'completed', deterministic_digest = ?, "
            "isolation_json = ?, result_json = ?, completed_at = ? WHERE run_id = ?",
            (digest, canonical_json(isolation), canonical_json(result), now(), run_id),
        )
        conn.commit()
        if write_report:
            report_path = ROOT / "reports" / f"{run_id}_result.json"
            report_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
            result["report_path"] = str(report_path)
        return result
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE evolution_runs SET status = 'failed', failure_json = ?, completed_at = ? "
            "WHERE run_id = ?",
            (
                canonical_json({
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "verifier_outcome": getattr(exc, "outcome", None),
                }),
                now(),
                run_id,
            ),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def reproduce(canonical_result: dict, reproduction_db: Path) -> dict:
    reproduced = run_evolution(
        reproduction_db,
        write_report=False,
        expected_code_revision=canonical_result["code_revision"],
    )
    agreed = reproduced["deterministic_digest"] == canonical_result["deterministic_digest"]
    isolation_agreed = (
        reproduced["isolation_instrumentation"]
        == canonical_result["isolation_instrumentation"]
    )
    return {
        "status": "agreed" if agreed and isolation_agreed else "disagreed",
        "canonical_digest": canonical_result["deterministic_digest"],
        "reproduction_digest": reproduced["deterministic_digest"],
        "reproduction_db": str(reproduction_db),
        "isolation_agreed": isolation_agreed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--reproduction-db", type=Path)
    args = parser.parse_args()
    result = run_evolution(args.db)
    print(json.dumps({
        "run_id": result["run_id"],
        "code_revision": result["code_revision"],
        "deterministic_digest": result["deterministic_digest"],
        "champion": result["frozen_top10"][0],
    }, sort_keys=True))
    if args.reproduction_db:
        reproduction = reproduce(result, args.reproduction_db)
        print(json.dumps({"reproduction": reproduction}, sort_keys=True))
        if reproduction["status"] != "agreed":
            raise SystemExit("deterministic reproduction disagreed")


if __name__ == "__main__":
    main()
