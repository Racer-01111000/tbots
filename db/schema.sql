PRAGMA foreign_keys = ON;

-- A genome is content-addressed: genome_id is the sha256 of its canonical
-- JSON, so identical genomes always collide to the same id and the id
-- itself proves the content hasn't changed.
CREATE TABLE IF NOT EXISTS genomes (
    genome_id   TEXT PRIMARY KEY,
    genome_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id        TEXT PRIMARY KEY,
    genome_id       TEXT NOT NULL REFERENCES genomes(genome_id),
    parent_agent_id TEXT REFERENCES agents(agent_id),
    generation      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'champion', 'graveyard')),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id             TEXT PRIMARY KEY,
    code_revision             TEXT NOT NULL,
    code_dirty                INTEGER NOT NULL DEFAULT 0,
    dataset_revision          TEXT NOT NULL,
    random_seed               INTEGER NOT NULL,
    agent_id                  TEXT NOT NULL REFERENCES agents(agent_id),
    genome_id                 TEXT NOT NULL REFERENCES genomes(genome_id),
    start_state_json          TEXT NOT NULL,
    replay_window_start       TEXT NOT NULL,
    replay_window_end         TEXT NOT NULL,
    execution_assumptions_json TEXT NOT NULL,
    final_result_json         TEXT,
    status                    TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    created_at                TEXT NOT NULL,
    completed_at              TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id       TEXT PRIMARY KEY,
    experiment_id    TEXT NOT NULL REFERENCES experiments(experiment_id),
    agent_id         TEXT NOT NULL REFERENCES agents(agent_id),
    dataset_revision TEXT NOT NULL,
    label            TEXT,
    start_ts         TEXT NOT NULL,
    end_ts           TEXT,
    current_ts       TEXT,
    masked_time      INTEGER NOT NULL DEFAULT 0,
    random_seed      INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'CREATED'
                         CHECK (status IN ('CREATED', 'RUNNING', 'COMPLETED', 'FAILED')),
    created_at       TEXT NOT NULL
);

-- One row per delivered observation. Deliberately does not duplicate the
-- market dataset: observation_hash + dataset_revision + true_ts is
-- enough to regenerate and re-verify the exact observation on demand
-- from the frozen, hash-verified normalized artifacts.
CREATE TABLE IF NOT EXISTS replay_audit (
    audit_id         TEXT PRIMARY KEY,
    episode_id       TEXT NOT NULL REFERENCES episodes(episode_id),
    step_index       INTEGER NOT NULL,
    true_ts          TEXT NOT NULL,
    masked_day       INTEGER,
    symbols_visible_json TEXT NOT NULL,
    observation_hash TEXT NOT NULL,
    dataset_revision TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id  TEXT PRIMARY KEY,
    episode_id   TEXT NOT NULL REFERENCES episodes(episode_id),
    agent_id     TEXT NOT NULL REFERENCES agents(agent_id),
    simulated_ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         TEXT PRIMARY KEY,
    decision_id      TEXT NOT NULL REFERENCES decisions(decision_id),
    episode_id       TEXT NOT NULL REFERENCES episodes(episode_id),
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity         INTEGER NOT NULL,
    order_type       TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
    limit_price_cents INTEGER,
    submitted_ts     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'filled', 'rejected', 'cancelled')),
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id          TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES orders(order_id),
    fill_ts          TEXT NOT NULL,
    fill_price_cents INTEGER NOT NULL,
    fill_quantity    INTEGER NOT NULL,
    commission_cents INTEGER NOT NULL DEFAULT 0,
    slippage_cents   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_runs (
    run_id                  TEXT PRIMARY KEY,
    code_revision           TEXT NOT NULL,
    code_dirty              INTEGER NOT NULL,
    dataset_revision        TEXT NOT NULL,
    lane_manifest_hash      TEXT NOT NULL,
    development_bundle_revision TEXT NOT NULL,
    bundle_manifest_hash    TEXT NOT NULL,
    evolution_seed          INTEGER NOT NULL,
    population_size         INTEGER NOT NULL,
    final_generation        INTEGER NOT NULL,
    episode_manifest_hash   TEXT NOT NULL,
    fitness_formula_hash    TEXT NOT NULL,
    mutation_bounds_hash    TEXT NOT NULL,
    population_rules_hash   TEXT NOT NULL,
    status                  TEXT NOT NULL
                                CHECK (status IN ('running', 'completed', 'failed')),
    failure_json            TEXT,
    deterministic_digest    TEXT,
    isolation_json          TEXT,
    result_json             TEXT,
    created_at              TEXT NOT NULL,
    completed_at            TEXT
);

CREATE TABLE IF NOT EXISTS evolution_organisms (
    agent_id          TEXT PRIMARY KEY REFERENCES agents(agent_id),
    run_id            TEXT NOT NULL REFERENCES evolution_runs(run_id),
    genome_id         TEXT NOT NULL REFERENCES genomes(genome_id),
    generation        INTEGER NOT NULL,
    parent_agent_id   TEXT REFERENCES evolution_organisms(agent_id),
    parent_genome_id  TEXT REFERENCES genomes(genome_id),
    creation_role     TEXT NOT NULL
                          CHECK (creation_role IN ('control_anchor', 'child', 'immigrant')),
    mutation_json     TEXT NOT NULL,
    mutation_magnitude REAL NOT NULL,
    creation_seed     INTEGER NOT NULL,
    is_control_anchor INTEGER NOT NULL DEFAULT 0,
    fitness           REAL,
    fitness_json      TEXT,
    state             TEXT NOT NULL
                          CHECK (state IN ('active', 'retired', 'frozen')),
    created_at        TEXT NOT NULL,
    retired_at        TEXT
);

CREATE TABLE IF NOT EXISTS evolution_evaluations (
    evaluation_id    TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES evolution_runs(run_id),
    generation       INTEGER NOT NULL,
    agent_id         TEXT NOT NULL REFERENCES evolution_organisms(agent_id),
    experiment_id    TEXT NOT NULL UNIQUE REFERENCES experiments(experiment_id),
    evaluation_kind  TEXT NOT NULL CHECK (evaluation_kind IN ('fitness', 'verification')),
    verification_role TEXT,
    status           TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    fitness           REAL,
    metrics_json     TEXT,
    metrics_hash     TEXT,
    created_at       TEXT NOT NULL,
    completed_at     TEXT
);

CREATE TABLE IF NOT EXISTS evolution_episode_results (
    result_id        TEXT PRIMARY KEY,
    evaluation_id   TEXT NOT NULL REFERENCES evolution_evaluations(evaluation_id),
    episode_id       TEXT NOT NULL UNIQUE REFERENCES episodes(episode_id),
    episode_index    INTEGER NOT NULL,
    metrics_json     TEXT NOT NULL,
    metrics_hash     TEXT NOT NULL,
    UNIQUE (evaluation_id, episode_index)
);

CREATE TABLE IF NOT EXISTS evolution_population (
    run_id           TEXT NOT NULL REFERENCES evolution_runs(run_id),
    generation       INTEGER NOT NULL,
    slot_index       INTEGER NOT NULL,
    agent_id         TEXT NOT NULL REFERENCES evolution_organisms(agent_id),
    genome_id        TEXT NOT NULL REFERENCES genomes(genome_id),
    membership_role  TEXT NOT NULL
                         CHECK (membership_role IN ('control_anchor', 'elite', 'child', 'immigrant')),
    rank             INTEGER,
    fitness          REAL,
    PRIMARY KEY (run_id, generation, slot_index),
    UNIQUE (run_id, generation, agent_id),
    UNIQUE (run_id, generation, genome_id)
);

CREATE TABLE IF NOT EXISTS evolution_generation_reports (
    run_id          TEXT NOT NULL REFERENCES evolution_runs(run_id),
    generation      INTEGER NOT NULL,
    report_json     TEXT NOT NULL,
    report_hash     TEXT NOT NULL,
    PRIMARY KEY (run_id, generation)
);

CREATE TABLE IF NOT EXISTS evolution_verifications (
    verification_id TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES evolution_runs(run_id),
    generation       INTEGER NOT NULL,
    agent_id         TEXT NOT NULL REFERENCES evolution_organisms(agent_id),
    role             TEXT NOT NULL,
    evaluation_id    TEXT NOT NULL REFERENCES evolution_evaluations(evaluation_id),
    status           TEXT NOT NULL CHECK (status IN ('agreed', 'disagreed')),
    outcome_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_duplicate_collisions (
    collision_id       TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES evolution_runs(run_id),
    generation          INTEGER NOT NULL,
    slot_role           TEXT NOT NULL,
    parent_agent_id     TEXT REFERENCES evolution_organisms(agent_id),
    candidate_genome_id TEXT NOT NULL,
    creation_seed       INTEGER NOT NULL,
    reason              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_frozen_top10 (
    run_id          TEXT NOT NULL REFERENCES evolution_runs(run_id),
    rank            INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 10),
    agent_id        TEXT NOT NULL REFERENCES evolution_organisms(agent_id),
    genome_id       TEXT NOT NULL REFERENCES genomes(genome_id),
    fitness         REAL NOT NULL,
    genealogy_json  TEXT NOT NULL,
    PRIMARY KEY (run_id, rank),
    UNIQUE (run_id, genome_id)
);

CREATE INDEX IF NOT EXISTS idx_agents_genome ON agents(genome_id);
CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_agent_id);
CREATE INDEX IF NOT EXISTS idx_experiments_agent ON experiments(agent_id);
CREATE INDEX IF NOT EXISTS idx_episodes_experiment ON episodes(experiment_id);
CREATE INDEX IF NOT EXISTS idx_replay_audit_episode ON replay_audit(episode_id);
CREATE INDEX IF NOT EXISTS idx_decisions_episode ON decisions(episode_id);
CREATE INDEX IF NOT EXISTS idx_orders_episode ON orders(episode_id);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_evolution_organisms_run_generation
    ON evolution_organisms(run_id, generation);
CREATE INDEX IF NOT EXISTS idx_evolution_evaluations_run_generation
    ON evolution_evaluations(run_id, generation);
CREATE INDEX IF NOT EXISTS idx_evolution_population_run_generation
    ON evolution_population(run_id, generation);
CREATE INDEX IF NOT EXISTS idx_evolution_episode_results_evaluation
    ON evolution_episode_results(evaluation_id);
