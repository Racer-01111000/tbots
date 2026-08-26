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

CREATE INDEX IF NOT EXISTS idx_agents_genome ON agents(genome_id);
CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_agent_id);
CREATE INDEX IF NOT EXISTS idx_experiments_agent ON experiments(agent_id);
CREATE INDEX IF NOT EXISTS idx_episodes_experiment ON episodes(experiment_id);
CREATE INDEX IF NOT EXISTS idx_replay_audit_episode ON replay_audit(episode_id);
CREATE INDEX IF NOT EXISTS idx_decisions_episode ON decisions(episode_id);
CREATE INDEX IF NOT EXISTS idx_orders_episode ON orders(episode_id);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
