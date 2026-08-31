-- PEV Initial Schema Migration v1
-- Creates all core tables for the PEV orchestration framework.
-- This migration consolidates the DDL previously embedded in:
--   core/data.py (SCHEMA_DDL), memory/store.py (_MEMORY_DDL),
--   core/circuit_breaker.py (_BREAKER_DDL), core/message_queue.py,
--   core/timeseries.py, core/human_proxy.py, core/kv_store.py,
--   core/sandbox.py, core/tool_manager.py, core/auth.py, prompt_manager.py

-- ── Schema version tracking ───────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_migrations (version, name) VALUES ('001', 'init');

-- ── Core operational tables ──────────────────────────────────

CREATE TABLE IF NOT EXISTS delegations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  task TEXT,
  routing_key TEXT,
  exit_code INT,
  stdout TEXT,
  stderr TEXT,
  duration_ms INT,
  trace_id TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  metric_name TEXT,
  metric_value REAL,
  tags TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  agent TEXT,
  task TEXT,
  phase TEXT,
  state_json TEXT,
  created TEXT,
  updated TEXT
);

CREATE TABLE IF NOT EXISTS error_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  task TEXT,
  exit_code INT,
  error TEXT,
  trace_id TEXT,
  duration_ms INT
);

-- ── Circuit breaker ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
  agent TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'closed',
  failures INTEGER NOT NULL DEFAULT 0,
  threshold INTEGER NOT NULL DEFAULT 3,
  last_failure REAL,
  cooldown_s INTEGER NOT NULL DEFAULT 60,
  updated REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS failover_chains (
  name TEXT PRIMARY KEY,
  agents TEXT NOT NULL DEFAULT '[]',
  current_index INTEGER NOT NULL DEFAULT 0,
  updated REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS breaker_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  old_state TEXT NOT NULL DEFAULT '',
  new_state TEXT NOT NULL DEFAULT '',
  failures INTEGER NOT NULL DEFAULT 0,
  timestamp REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_be_agent_ts ON breaker_events(agent, timestamp);
CREATE INDEX IF NOT EXISTS idx_be_ts ON breaker_events(timestamp);

-- ── Memory system ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS memory_entries (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT 'general',
  trace_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  exit_code INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  timestamp TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_mem_agent ON memory_entries(agent);
CREATE INDEX IF NOT EXISTS idx_mem_topic ON memory_entries(topic);
CREATE INDEX IF NOT EXISTS idx_mem_trace_id ON memory_entries(trace_id);
CREATE INDEX IF NOT EXISTS idx_mem_timestamp ON memory_entries(timestamp);

CREATE TABLE IF NOT EXISTS memory_traces (
  trace_id TEXT PRIMARY KEY,
  parent_trace_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  agents TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL DEFAULT '',
  last_active TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS memory_trajectory (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  tool_name TEXT NOT NULL DEFAULT '',
  tool_input TEXT NOT NULL DEFAULT '',
  tool_output TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  exit_code INTEGER NOT NULL DEFAULT 0,
  timestamp TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_traj_trace_id ON memory_trajectory(trace_id);

-- ── Message queue ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS queue_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  processed_at TEXT,
  consumer_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_qm_topic_status ON queue_messages(topic, status);

CREATE TABLE IF NOT EXISTS queue_dead_letters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_id INTEGER NOT NULL,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_idempotent (
  key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- ── Time-series ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ts_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ts_raw_metric_ts ON ts_raw(metric, timestamp);

CREATE TABLE IF NOT EXISTS ts_5min (
  bucket_start REAL NOT NULL,
  metric TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  sum REAL NOT NULL DEFAULT 0.0,
  min_val REAL NOT NULL DEFAULT 0.0,
  max_val REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (bucket_start, metric)
);

CREATE TABLE IF NOT EXISTS ts_1hour (
  bucket_start REAL NOT NULL,
  metric TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  sum REAL NOT NULL DEFAULT 0.0,
  min_val REAL NOT NULL DEFAULT 0.0,
  max_val REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (bucket_start, metric)
);

-- ── Human proxy / approval requests ──────────────────────────

CREATE TABLE IF NOT EXISTS approval_requests (
  id TEXT PRIMARY KEY,
  requester TEXT NOT NULL DEFAULT 'system',
  agent TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  priority TEXT NOT NULL DEFAULT 'normal',
  resolved TEXT,
  created TEXT NOT NULL DEFAULT ''
);

-- ── KV store ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kv_store (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  ttl INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT ''
);

-- ── Prompt templates ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS prompt_templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT 'general',
  content TEXT NOT NULL DEFAULT '',
  variables TEXT NOT NULL DEFAULT '[]',
  created TEXT NOT NULL DEFAULT '',
  updated TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id TEXT PRIMARY KEY,
  template_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  content TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL DEFAULT ''
);

-- ── Sandbox ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sandboxes (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL DEFAULT '',
  workdir TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created TEXT NOT NULL DEFAULT '',
  expires TEXT
);

-- ── Tools ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tools (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT 'general',
  description TEXT NOT NULL DEFAULT '',
  config TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL DEFAULT '',
  updated TEXT NOT NULL DEFAULT ''
);

-- ── Auth / API keys ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_keys (
  key_id TEXT PRIMARY KEY,
  key_hash TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL DEFAULT '[]',
  created TEXT NOT NULL DEFAULT '',
  expires TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  -- 3f335fb: align with the ONLY runtime consumer (dashboard/routers/auth.py
  -- _ensure_default_user/_db_login_user), which uses the plural `roles`
  -- JSON column + created_at + enabled. The previous singular `role`/`created`
  -- columns made every default-admin bootstrap fail with "table users has
  -- no column named roles" inside the container (compose-smoke log).
  roles TEXT NOT NULL DEFAULT '["read"]',
  created_at REAL NOT NULL DEFAULT 0.0,
  enabled INTEGER NOT NULL DEFAULT 1
);

-- ── Vector entries ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vector_entries (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  embedding TEXT NOT NULL DEFAULT '',
  dimension INTEGER NOT NULL DEFAULT 0,
  timestamp TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_vec_agent ON vector_entries(agent);

-- DOWN:
-- Drop all tables in reverse dependency order.
DROP TABLE IF EXISTS vector_entries;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS api_keys;
DROP TABLE IF EXISTS tools;
DROP TABLE IF EXISTS sandboxes;
DROP TABLE IF EXISTS prompt_versions;
DROP TABLE IF EXISTS prompt_templates;
DROP TABLE IF EXISTS kv_store;
DROP TABLE IF EXISTS approval_requests;
DROP TABLE IF EXISTS ts_1hour;
DROP TABLE IF EXISTS ts_5min;
DROP TABLE IF EXISTS ts_raw;
DROP TABLE IF EXISTS queue_idempotent;
DROP TABLE IF EXISTS queue_dead_letters;
DROP TABLE IF EXISTS queue_messages;
DROP TABLE IF EXISTS memory_trajectory;
DROP TABLE IF EXISTS memory_traces;
DROP TABLE IF EXISTS memory_entries;
DROP TABLE IF EXISTS breaker_events;
DROP TABLE IF EXISTS failover_chains;
DROP TABLE IF EXISTS circuit_breaker_state;
DROP TABLE IF EXISTS error_log;
DROP TABLE IF EXISTS checkpoints;
DROP TABLE IF EXISTS metrics;
DROP TABLE IF EXISTS delegations;
