"""MAOP initial PostgreSQL schema.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-08-07

Translates the SQLite schema (data/maop.db, see data/migrations/001_init.sql)
onto idiomatic PostgreSQL types:

  - ``INTEGER PRIMARY KEY AUTOINCREMENT`` -> ``BIGSERIAL PRIMARY KEY``
  - ``TEXT`` retained; ``VARCHAR(n)`` where a length cap is meaningful
  - ``DATETIME`` / ``TEXT``-storing-ISO8601 -> ``TIMESTAMPTZ`` where the
    column is a *created_at/updated_at/checked_at* style timestamp; the
    rest stay ``TEXT`` to avoid coercing historical string values.
  - ``JSON``-encoded TEXT columns (``metadata``, ``payload``, ``tags``,
    ``roles``, ``capabilities``, ``changes``, ``lessons``, ...) -> ``JSONB``
    with GIN index where the column is queried by containment.
  - FTS5 virtual tables (``memory_fts``, ``episodic_memory_fts``) -> a
    generated ``tsvector`` column + ``GIN`` index on the parent table.
  - sqlite-vec virtual table (``vec_vectors``, runtime-created by
    ``core/memory/vector.py``) -> ``vector(dim)`` column on
    ``vector_entries`` + ``ivfflat`` ANN index.

The migration is idempotent: each object uses ``IF NOT EXISTS`` so
``alembic downgrade base && alembic upgrade head`` is safe in CI.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ────────────────────────────────────────────────────────────────────────────
# DDL fragments. Kept as raw SQL strings (executed via op.execute) rather than
# op.create_table() so the migration reads as a literal transcript of the
# SQLite schema — easier to audit and to diff against 001_init.sql.
# ────────────────────────────────────────────────────────────────────────────

_TABLES_DDL = """
-- ── Migration bookkeeping ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _migrations (
    version        BIGINT PRIMARY KEY,
    name           TEXT NOT NULL,
    applied_at     TIMESTAMPTZ NOT NULL,
    checksum       TEXT NOT NULL DEFAULT '',
    execution_ms   DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version        TEXT PRIMARY KEY,
    applied_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Auth / users ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    roles          JSONB NOT NULL DEFAULT '["admin"]'::jsonb,
    created_at     DOUBLE PRECISION NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    roles          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     DOUBLE PRECISION NOT NULL,
    expires_at     DOUBLE PRECISION,
    enabled        INTEGER NOT NULL DEFAULT 1,
    rate_limit     INTEGER NOT NULL DEFAULT 0
);

-- ── Sessions / messages ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    agent          TEXT NOT NULL DEFAULT '',
    workdir        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    tags           JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count    INTEGER NOT NULL DEFAULT 0,
    token_budget   INTEGER NOT NULL DEFAULT 0,
    message_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- ── Agent memory / messaging / performance / evolution ─────────────────
CREATE TABLE IF NOT EXISTS agent_memory (
    id            BIGSERIAL PRIMARY KEY,
    agent_name    TEXT NOT NULL,
    memory_type   TEXT NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}'::jsonb,
    importance    DOUBLE PRECISION DEFAULT 0.5,
    created_at    TEXT NOT NULL,
    expires_at    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id            TEXT PRIMARY KEY,
    sender        TEXT NOT NULL,
    recipient     TEXT NOT NULL,
    msg_type      TEXT DEFAULT 'info',
    payload       JSONB DEFAULT '{}'::jsonb,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_performance (
    id            TEXT PRIMARY KEY,
    agent         TEXT NOT NULL,
    routing_key   TEXT DEFAULT '',
    outcome       TEXT DEFAULT '',
    cost_usd      DOUBLE PRECISION DEFAULT 0.0,
    latency_ms    DOUBLE PRECISION DEFAULT 0.0,
    created_at    DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_evolution_history (
    id            BIGSERIAL PRIMARY KEY,
    agent_name    TEXT NOT NULL,
    evolution_type TEXT NOT NULL,
    description   TEXT NOT NULL,
    changes       JSONB NOT NULL,
    success       INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- ── A2A ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS a2a_cards (
    name      TEXT PRIMARY KEY,
    card_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id   TEXT PRIMARY KEY,
    task_json JSONB NOT NULL
);

-- ── Circuit breaker ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS breaker_events (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,
    old_state  TEXT NOT NULL DEFAULT '',
    new_state  TEXT NOT NULL DEFAULT '',
    failures   INTEGER NOT NULL DEFAULT 0,
    timestamp  DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    agent        TEXT PRIMARY KEY,
    state        TEXT DEFAULT 'closed',
    failures     INTEGER DEFAULT 0,
    threshold    INTEGER DEFAULT 3,
    last_failure TEXT,
    cooldown_s   INTEGER DEFAULT 60,
    updated      TEXT
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    agent        TEXT PRIMARY KEY,
    state        TEXT NOT NULL DEFAULT 'closed',
    failures     INTEGER NOT NULL DEFAULT 0,
    threshold    INTEGER NOT NULL DEFAULT 3,
    last_failure DOUBLE PRECISION,
    cooldown_s   INTEGER NOT NULL DEFAULT 60,
    updated      DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

-- ── Checkpoints ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkpoints (
    id          TEXT PRIMARY KEY,
    agent       TEXT,
    task        TEXT,
    phase       TEXT,
    state_json  JSONB,
    created     TEXT,
    updated     TEXT
);

-- ── Consolidation ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS consolidation_log (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT DEFAULT '',
    entries_scanned INTEGER DEFAULT 0,
    entries_pruned  INTEGER DEFAULT 0,
    success         INTEGER DEFAULT 0
);

-- ── Cost ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cost_entries (
    id                TEXT PRIMARY KEY,
    session_id        TEXT DEFAULT '',
    agent             TEXT DEFAULT '',
    model             TEXT DEFAULT '',
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    cost_usd          DOUBLE PRECISION DEFAULT 0.0,
    latency_ms        INTEGER DEFAULT 0,
    metadata          JSONB DEFAULT '{}'::jsonb,
    created_at        TEXT NOT NULL
);

-- ── Delegations / error log ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS delegations (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TEXT,
    agent       TEXT,
    task        TEXT,
    routing_key TEXT,
    exit_code   INTEGER,
    stdout      TEXT,
    stderr      TEXT,
    duration_ms INTEGER,
    trace_id    TEXT
);

CREATE TABLE IF NOT EXISTS error_log (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TEXT,
    agent       TEXT,
    task        TEXT,
    exit_code   INTEGER,
    error       TEXT,
    trace_id    TEXT,
    duration_ms INTEGER
);

-- ── Knowledge graph: entities / relations / facts ──────────────────────
CREATE TABLE IF NOT EXISTS entities (
    name        TEXT PRIMARY KEY,
    entity_type TEXT DEFAULT 'concept',
    attributes  JSONB DEFAULT '{}'::jsonb,
    confidence  DOUBLE PRECISION DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS relations (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    target        TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    context       TEXT DEFAULT '',
    confidence    DOUBLE PRECISION DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object_value    TEXT NOT NULL,
    source_exchange TEXT DEFAULT '',
    topic           TEXT DEFAULT '',
    confidence      DOUBLE PRECISION DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    access_count    INTEGER DEFAULT 0
);

-- ── Episodic memory (with tsvector + GIN replacing FTS5) ───────────────
CREATE TABLE IF NOT EXISTS episodic_memory (
    id                 TEXT PRIMARY KEY,
    task               TEXT NOT NULL,
    agent              TEXT DEFAULT '',
    outcome            TEXT DEFAULT '',
    score              DOUBLE PRECISION DEFAULT 0.0,
    lessons            JSONB DEFAULT '[]'::jsonb,
    user_feedback      TEXT DEFAULT '',
    quality_dimensions JSONB DEFAULT '{}'::jsonb,
    summary            TEXT DEFAULT '',
    key_decisions      JSONB DEFAULT '[]'::jsonb,
    files_touched      JSONB DEFAULT '[]'::jsonb,
    metadata           JSONB DEFAULT '{}'::jsonb,
    created_at         DOUBLE PRECISION NOT NULL,
    consolidated       INTEGER DEFAULT 0,
    access_count       INTEGER DEFAULT 0,
    -- Generated tsvector column replaces the FTS5 virtual table.
    fts_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(task, '') || ' ' ||
                                    coalesce(agent, '') || ' ' ||
                                    coalesce(summary, '') || ' ' ||
                                    coalesce(user_feedback, ''))
        ) STORED
);

-- ── Error ledger / evolution cycles / promoted rules ───────────────────
CREATE TABLE IF NOT EXISTS error_ledger (
    id          TEXT PRIMARY KEY,
    error_type  TEXT NOT NULL,
    context     TEXT DEFAULT '',
    trigger     JSONB DEFAULT '{}'::jsonb,
    output      TEXT DEFAULT '',
    expected    TEXT DEFAULT '',
    root_cause  TEXT DEFAULT '',
    pattern     TEXT DEFAULT '',
    rule        TEXT DEFAULT '',
    action      TEXT DEFAULT '',
    recurrence  INTEGER DEFAULT 1,
    created_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_cycles (
    id                   TEXT PRIMARY KEY,
    started_at           DOUBLE PRECISION NOT NULL,
    finished_at          DOUBLE PRECISION DEFAULT 0,
    total_duration_s     DOUBLE PRECISION DEFAULT 0,
    errors_observed      INTEGER DEFAULT 0,
    heal_attempts        INTEGER DEFAULT 0,
    heal_successes       INTEGER DEFAULT 0,
    suggestions_generated INTEGER DEFAULT 0,
    suggestions_applied  INTEGER DEFAULT 0,
    validation_improved  INTEGER DEFAULT 0,
    consolidated         INTEGER DEFAULT 0,
    report_json          JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS promoted_rules (
    id          TEXT PRIMARY KEY,
    pattern     TEXT NOT NULL,
    rule        TEXT NOT NULL,
    count       INTEGER DEFAULT 0,
    promoted_at DOUBLE PRECISION NOT NULL
);

-- ── Failover / health / hooks ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS failover_chains (
    name          TEXT PRIMARY KEY,
    agents        JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_index INTEGER NOT NULL DEFAULT 0,
    updated       DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS health_log (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    healthy     INTEGER DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    version     TEXT DEFAULT '',
    error       TEXT DEFAULT '',
    checked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hooks (
    id            TEXT PRIMARY KEY,
    event         TEXT NOT NULL,
    hook_type     TEXT NOT NULL DEFAULT 'callback',
    callback      TEXT DEFAULT '',
    callback_path TEXT DEFAULT '',
    url           TEXT DEFAULT '',
    enabled       INTEGER DEFAULT 1,
    priority      INTEGER DEFAULT 0,
    description   TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    source        TEXT DEFAULT 'api'
);

CREATE TABLE IF NOT EXISTS hook_logs (
    id          TEXT PRIMARY KEY,
    hook_id     TEXT NOT NULL,
    event       TEXT NOT NULL,
    success     INTEGER DEFAULT 1,
    error       TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- ── KV store (composite PK) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT NOT NULL,
    namespace   TEXT NOT NULL DEFAULT 'default',
    value       TEXT NOT NULL,
    ttl_expires DOUBLE PRECISION,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (key, namespace)
);

-- ── Memory entries (with tsvector + GIN replacing FTS5) ────────────────
CREATE TABLE IF NOT EXISTS memory_entries (
    id         TEXT PRIMARY KEY,
    agent      TEXT NOT NULL DEFAULT '',
    task       TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    topic      TEXT NOT NULL DEFAULT 'general',
    trace_id   TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    exit_code  INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    timestamp  TEXT NOT NULL DEFAULT '',
    fts_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(agent, '') || ' ' ||
                                    coalesce(task, '') || ' ' ||
                                    coalesce(content, '') || ' ' ||
                                    coalesce(tags, '') || ' ' ||
                                    coalesce(topic, ''))
        ) STORED
);

CREATE TABLE IF NOT EXISTS memory_traces (
    trace_id         TEXT PRIMARY KEY,
    parent_trace_id  TEXT NOT NULL DEFAULT '',
    session_id       TEXT NOT NULL DEFAULT '',
    task             TEXT NOT NULL DEFAULT '',
    agents           TEXT NOT NULL DEFAULT '',
    created          TEXT NOT NULL DEFAULT '',
    last_active      TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS memory_trajectory (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    tool_name   TEXT NOT NULL DEFAULT '',
    tool_input  TEXT NOT NULL DEFAULT '',
    tool_output TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    exit_code   INTEGER NOT NULL DEFAULT 0,
    timestamp   TEXT NOT NULL DEFAULT ''
);

-- ── Metrics / timeseries ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metrics (
    id           BIGSERIAL PRIMARY KEY,
    timestamp    TEXT,
    agent        TEXT,
    metric_name  TEXT,
    metric_value DOUBLE PRECISION,
    tags         JSONB
);

CREATE TABLE IF NOT EXISTS ts_raw (
    timestamp DOUBLE PRECISION NOT NULL,
    metric    TEXT NOT NULL,
    value     DOUBLE PRECISION NOT NULL,
    tags      JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (metric, timestamp)
);

CREATE TABLE IF NOT EXISTS ts_5min (
    timestamp DOUBLE PRECISION NOT NULL,
    metric    TEXT NOT NULL,
    avg_value DOUBLE PRECISION NOT NULL,
    min_value DOUBLE PRECISION NOT NULL,
    max_value DOUBLE PRECISION NOT NULL,
    sum_value DOUBLE PRECISION NOT NULL,
    count     INTEGER NOT NULL DEFAULT 1,
    tags      JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (metric, timestamp)
);

CREATE TABLE IF NOT EXISTS ts_1hour (
    timestamp DOUBLE PRECISION NOT NULL,
    metric    TEXT NOT NULL,
    avg_value DOUBLE PRECISION NOT NULL,
    min_value DOUBLE PRECISION NOT NULL,
    max_value DOUBLE PRECISION NOT NULL,
    sum_value DOUBLE PRECISION NOT NULL,
    count     INTEGER NOT NULL DEFAULT 1,
    tags      JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (metric, timestamp)
);

-- ── Prompt templates / versions (FK) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS prompt_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT 'general',
    tags            JSONB DEFAULT '[]'::jsonb,
    current_version TEXT DEFAULT '1.0'
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    template_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    content     TEXT NOT NULL,
    variables   JSONB DEFAULT '{}'::jsonb,
    created     TEXT NOT NULL,
    PRIMARY KEY (template_id, version),
    FOREIGN KEY (template_id) REFERENCES prompt_templates(id)
);

-- ── Queue ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS queue_messages (
    id             TEXT PRIMARY KEY,
    topic          TEXT NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority       INTEGER NOT NULL DEFAULT 5,
    status         TEXT NOT NULL DEFAULT 'pending',
    retries        INTEGER NOT NULL DEFAULT 0,
    max_retries    INTEGER NOT NULL DEFAULT 3,
    ack_timeout_s  DOUBLE PRECISION NOT NULL DEFAULT 30.0,
    enqueued_at    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    visible_at     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    dequeued_at    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    acked_at       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    consumer_group TEXT NOT NULL DEFAULT '',
    consumer_id    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS queue_dead_letters (
    id             TEXT PRIMARY KEY,
    topic          TEXT NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority       INTEGER NOT NULL DEFAULT 5,
    retries        INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    consumer_group TEXT NOT NULL DEFAULT '',
    dead_at        DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS queue_idempotent (
    msg_id       TEXT PRIMARY KEY,
    consumer_id  TEXT NOT NULL,
    processed_at DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

-- ── Registered / scanned agents ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS registered_agents (
    name                    TEXT PRIMARY KEY,
    cli_path                TEXT DEFAULT '',
    version                 TEXT DEFAULT '',
    provider                TEXT DEFAULT '',
    capabilities            JSONB DEFAULT '[]'::jsonb,
    description             TEXT DEFAULT '',
    model                   TEXT DEFAULT '',
    driver                  TEXT DEFAULT 'cli',
    cli_args                TEXT DEFAULT '',
    timeout_s               INTEGER DEFAULT 120,
    enabled                 INTEGER DEFAULT 1,
    health                  TEXT DEFAULT 'unknown',
    last_health_check       TEXT DEFAULT '',
    last_latency_ms         INTEGER DEFAULT 0,
    consecutive_failures    INTEGER DEFAULT 0,
    registered_at           TEXT DEFAULT '',
    source                  TEXT DEFAULT 'scanned',
    extracts_queries        INTEGER DEFAULT 0,
    supports_regeneration   INTEGER DEFAULT 0,
    results_merge           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scanned_agents (
    name         TEXT PRIMARY KEY,
    cli_path     TEXT DEFAULT '',
    version      TEXT DEFAULT '',
    source       TEXT DEFAULT 'scanned',
    status       TEXT DEFAULT 'unknown',
    capabilities JSONB DEFAULT '[]'::jsonb,
    provider     TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    model        TEXT DEFAULT '',
    timeout_s    INTEGER DEFAULT 120,
    driver       TEXT DEFAULT 'cli',
    cli_args     TEXT DEFAULT '',
    last_checked TEXT DEFAULT '',
    error        TEXT DEFAULT ''
);

-- ── Routing decisions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS routing_decisions (
    id             BIGSERIAL PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    parent_span_id TEXT,
    timestamp      DOUBLE PRECISION NOT NULL,
    stage          TEXT NOT NULL,
    input_summary  TEXT,
    output_summary TEXT,
    explanation    TEXT,
    duration_ms    DOUBLE PRECISION,
    attributes     JSONB
);

-- ── Subagents / transcripts ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subagents (
    id            TEXT PRIMARY KEY,
    task          TEXT DEFAULT '',
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    finished_at   TEXT DEFAULT '',
    updated_at    TEXT DEFAULT '',
    parent_agent  TEXT DEFAULT '',
    child_agent   TEXT DEFAULT '',
    exit_code     INTEGER,
    depth         INTEGER DEFAULT 0,
    message       TEXT,
    name          TEXT DEFAULT '',
    role          TEXT DEFAULT 'leaf',
    model         TEXT DEFAULT '',
    context       JSONB DEFAULT '{}'::jsonb,
    output        TEXT DEFAULT '',
    tool_calls    JSONB DEFAULT '[]'::jsonb,
    tokens_used   INTEGER DEFAULT 0,
    duration_ms   INTEGER DEFAULT 0,
    error         TEXT DEFAULT '',
    config        JSONB DEFAULT '{}'::jsonb,
    started_at    DOUBLE PRECISION DEFAULT 0,
    transcript    TEXT,
    result        TEXT,
    metadata      JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS subagent_transcripts (
    id        TEXT PRIMARY KEY,
    agent_id  TEXT NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    event     TEXT DEFAULT '',
    data      JSONB DEFAULT '{}'::jsonb
);

-- ── Tools ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tools (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT DEFAULT '',
    command             TEXT NOT NULL,
    category            TEXT DEFAULT 'general',
    params              JSONB DEFAULT '{}'::jsonb,
    enabled             INTEGER DEFAULT 1,
    created             TEXT NOT NULL,
    last_called         TEXT,
    call_count          INTEGER DEFAULT 0,
    version             TEXT DEFAULT '1.0',
    min_platform_version TEXT DEFAULT ''
);

-- ── Vector entries (pgvector replaces sqlite-vec BLOB) ─────────────────
-- The embedding column uses pgvector's ``vector`` type. The dimension is
-- fixed at 1536 (the OpenAI text-embedding-3-small default); if your model
-- emits a different dim, ALTER the column type after running this migration.
-- The ivfflat ANN index requires pgvector and ``lists = sqrt(rows)``;
-- 100 lists is a reasonable default for ≤10K vectors.
CREATE TABLE IF NOT EXISTS vector_entries (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL DEFAULT '',
    vector     JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding  vector(1536),
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

-- ── Worktree ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS worktree_nodes (
    id          TEXT PRIMARY KEY,
    parent_id   TEXT DEFAULT '',
    root_id     TEXT DEFAULT '',
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    status      TEXT DEFAULT 'active',
    result      TEXT DEFAULT '',
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS worktree_checkpoints (
    id         TEXT PRIMARY KEY,
    node_id    TEXT NOT NULL,
    label      TEXT NOT NULL,
    snapshot   JSONB DEFAULT '{}'::jsonb,
    created_at DOUBLE PRECISION NOT NULL
);
"""

_INDEXES_DDL = """
-- ── B-tree indexes (mirror SQLite schema) ──────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ap_agent  ON agent_performance(agent);
CREATE INDEX IF NOT EXISTS idx_ap_created ON agent_performance(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ap_outcome ON agent_performance(outcome);
CREATE INDEX IF NOT EXISTS idx_ap_rk     ON agent_performance(routing_key);

CREATE INDEX IF NOT EXISTS idx_be_agent_ts ON breaker_events(agent, timestamp);
CREATE INDEX IF NOT EXISTS idx_be_ts       ON breaker_events(timestamp);

CREATE INDEX IF NOT EXISTS idx_cost_agent  ON cost_entries(agent, created_at);
CREATE INDEX IF NOT EXISTS idx_cost_model   ON cost_entries(model, created_at);
CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_entries(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cost_time    ON cost_entries(created_at);

CREATE INDEX IF NOT EXISTS idx_cp_node ON worktree_checkpoints(node_id);

CREATE INDEX IF NOT EXISTS idx_el_created ON error_ledger(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_el_pattern ON error_ledger(pattern);
CREATE INDEX IF NOT EXISTS idx_el_type    ON error_ledger(error_type);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

CREATE INDEX IF NOT EXISTS idx_episodic_access      ON episodic_memory(access_count DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_agent       ON episodic_memory(agent);
CREATE INDEX IF NOT EXISTS idx_episodic_consolidated ON episodic_memory(consolidated);
CREATE INDEX IF NOT EXISTS idx_episodic_created     ON episodic_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_outcome     ON episodic_memory(outcome);
CREATE INDEX IF NOT EXISTS idx_episodic_score       ON episodic_memory(score DESC);

CREATE INDEX IF NOT EXISTS idx_evo_cycles_started ON evolution_cycles(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_evolution_agent ON agent_evolution_history(agent_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_facts_subject   ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_topic     ON facts(topic);

CREATE INDEX IF NOT EXISTS idx_health_log_agent ON health_log(agent_name, checked_at);

CREATE INDEX IF NOT EXISTS idx_hook_logs_event ON hook_logs(event, created_at);
CREATE INDEX IF NOT EXISTS idx_hooks_event     ON hooks(event, enabled);

-- Partial index: PG supports WHERE on CREATE INDEX natively (no need for
-- the SQLite partial-index syntax).
CREATE INDEX IF NOT EXISTS idx_kv_expires   ON kv_store(ttl_expires) WHERE ttl_expires IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kv_namespace ON kv_store(namespace);

CREATE INDEX IF NOT EXISTS idx_mem_agent     ON memory_entries(agent);
CREATE INDEX IF NOT EXISTS idx_mem_timestamp ON memory_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_mem_topic     ON memory_entries(topic);
CREATE INDEX IF NOT EXISTS idx_mem_trace_id  ON memory_entries(trace_id);

CREATE INDEX IF NOT EXISTS idx_memory_agent_type ON agent_memory(agent_name, memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_created    ON agent_memory(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_msg_recipient ON agent_messages(recipient, created_at);

CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompt_templates(category);

CREATE INDEX IF NOT EXISTS idx_qdl_topic     ON queue_dead_letters(topic);
CREATE INDEX IF NOT EXISTS idx_qi_consumer   ON queue_idempotent(consumer_id);
CREATE INDEX IF NOT EXISTS idx_qm_consumer   ON queue_messages(consumer_group, consumer_id);
CREATE INDEX IF NOT EXISTS idx_qm_status_dequeued ON queue_messages(status, dequeued_at);
CREATE INDEX IF NOT EXISTS idx_qm_topic_status   ON queue_messages(topic, status);
CREATE INDEX IF NOT EXISTS idx_qm_visible    ON queue_messages(topic, status, visible_at);

CREATE INDEX IF NOT EXISTS idx_rd_stage ON routing_decisions(stage);
CREATE INDEX IF NOT EXISTS idx_rd_trace ON routing_decisions(trace_id);
CREATE INDEX IF NOT EXISTS idx_rd_ts    ON routing_decisions(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
CREATE INDEX IF NOT EXISTS idx_relations_type   ON relations(relation_type);

CREATE INDEX IF NOT EXISTS idx_sessions_agent  ON sessions(agent);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE INDEX IF NOT EXISTS idx_st_agent ON subagent_transcripts(agent_id);

CREATE INDEX IF NOT EXISTS idx_subagents_name   ON subagents(name);
CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagents(parent_agent);
CREATE INDEX IF NOT EXISTS idx_subagents_status ON subagents(status);

CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category);

CREATE INDEX IF NOT EXISTS idx_traj_trace_id ON memory_trajectory(trace_id);

CREATE INDEX IF NOT EXISTS idx_ts_1hour_ts ON ts_1hour(timestamp);
CREATE INDEX IF NOT EXISTS idx_ts_5min_ts  ON ts_5min(timestamp);
CREATE INDEX IF NOT EXISTS idx_ts_raw_ts   ON ts_raw(timestamp);

CREATE INDEX IF NOT EXISTS idx_ve_created ON vector_entries(created_at);

CREATE INDEX IF NOT EXISTS idx_wt_parent ON worktree_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_wt_root   ON worktree_nodes(root_id);
CREATE INDEX IF NOT EXISTS idx_wt_status ON worktree_nodes(status);

-- ── GIN indexes on JSONB columns (containment queries) ─────────────────
CREATE INDEX IF NOT EXISTS idx_sessions_tags_gin     ON sessions USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_sessions_metadata_gin ON sessions USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_messages_metadata_gin ON messages USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_kv_value_gin          ON kv_store USING GIN (value jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_episodic_metadata_gin ON episodic_memory USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_queue_payload_gin     ON queue_messages USING GIN (payload);

-- ── GIN indexes on tsvector columns (replacing FTS5) ───────────────────
CREATE INDEX IF NOT EXISTS idx_episodic_fts_gin ON episodic_memory USING GIN (fts_tsv);
CREATE INDEX IF NOT EXISTS idx_memory_fts_gin   ON memory_entries   USING GIN (fts_tsv);

-- ── ivfflat ANN index on vector_entries.embedding ──────────────────────
-- Requires pgvector. ``lists=100`` is a reasonable default for ≤10K rows;
-- for larger datasets, drop & rebuild with lists ≈ sqrt(rows). The index
-- is built with cosine distance (matches the SQLite cosine_similarity
-- fallback in core/memory/vector.py).
CREATE INDEX IF NOT EXISTS idx_vector_embedding_ivfflat
    ON vector_entries USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""

# Tables dropped on downgrade, in reverse dependency order.
_DROP_ORDER = [
    "worktree_checkpoints",
    "worktree_nodes",
    "vector_entries",
    "tools",
    "subagent_transcripts",
    "subagents",
    "routing_decisions",
    "scanned_agents",
    "registered_agents",
    "queue_idempotent",
    "queue_dead_letters",
    "queue_messages",
    "prompt_versions",
    "prompt_templates",
    "ts_1hour",
    "ts_5min",
    "ts_raw",
    "metrics",
    "memory_trajectory",
    "memory_traces",
    "memory_entries",
    "kv_store",
    "hook_logs",
    "hooks",
    "health_log",
    "failover_chains",
    "promoted_rules",
    "evolution_cycles",
    "error_ledger",
    "episodic_memory",
    "facts",
    "relations",
    "entities",
    "error_log",
    "delegations",
    "cost_entries",
    "consolidation_log",
    "checkpoints",
    "circuit_breaker_state",
    "circuit_breaker",
    "breaker_events",
    "a2a_tasks",
    "a2a_cards",
    "agent_evolution_history",
    "agent_performance",
    "agent_messages",
    "agent_memory",
    "messages",
    "sessions",
    "api_keys",
    "users",
    "schema_migrations",
    "_migrations",
]


def _require_destructive_ack(revision_name: str) -> None:
    """Guard destructive downgrades (mirrors 001_init.py / 003_pg_enterprise.py).

    ``alembic downgrade base`` for this revision DROPS every MAOP table and
    ALL data. Allowed only in dev/test or when explicitly overridden.
    """
    env = os.environ.get("MAOP_ENV", "").strip().lower()
    if env in ("dev", "development", "local", "test", "ci"):
        return
    if os.environ.get("MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE", "") == "1":
        return
    raise RuntimeError(
        f"SAFETY: downgrade of {revision_name} DROPS all MAOP tables and "
        "permanently deletes data. Refusing outside dev/test environments. "
        "Set MAOP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 to override (back up first)."
    )


def _exec_block(sql: str) -> None:
    """Execute a multi-statement SQL block, splitting on ';' and skipping comments."""
    bind = op.get_bind()
    for stmt in sql.split(";"):
        lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            bind.execute(sa.text(cleaned))


def upgrade() -> None:
    """Create the full MAOP PostgreSQL schema (idempotent)."""
    # pgvector extension is enabled by env.py at connection time; assert it here
    # too so a direct ``alembic upgrade`` (without env.py's _enable_extensions)
    # fails loudly rather than producing a half-applied schema.
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "001_initial_schema is PostgreSQL-only. Point alembic at a PG "
            "database (MAOP_DATABASE_URL=postgresql+psycopg2://...) and retry."
        )
    try:
        bind.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "vector"'))
    except Exception as exc:  # noqa: BLE001 — surface a clear error
        raise RuntimeError(
            "pgvector extension is required but could not be created. "
            "Install pgvector (https://github.com/pgvector/pgvector) and "
            f"retry. Original error: {exc}"
        ) from exc

    _exec_block(_TABLES_DDL)
    _exec_block(_INDEXES_DDL)


def downgrade() -> None:
    """Drop all tables created by upgrade()."""
    _require_destructive_ack("001_initial_schema (all MAOP tables)")
    # Drop indexes first (they live on the tables we're about to drop, but
    # explicit drops make the migration log auditable and avoid CASCADE).
    _exec_block(
        """
        DROP INDEX IF EXISTS idx_vector_embedding_ivfflat;
        DROP INDEX IF EXISTS idx_memory_fts_gin;
        DROP INDEX IF EXISTS idx_episodic_fts_gin;
        DROP INDEX IF EXISTS idx_queue_payload_gin;
        DROP INDEX IF EXISTS idx_episodic_metadata_gin;
        DROP INDEX IF EXISTS idx_kv_value_gin;
        DROP INDEX IF EXISTS idx_messages_metadata_gin;
        DROP INDEX IF EXISTS idx_sessions_metadata_gin;
        DROP INDEX IF EXISTS idx_sessions_tags_gin;
        """
    )
    for table in _DROP_ORDER:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')