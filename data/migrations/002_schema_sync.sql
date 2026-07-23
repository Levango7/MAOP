-- MAOP Schema Sync Migration v2
-- Syncs 001_init.sql with runtime DDL changes:
--   - queue_messages: add missing columns (retries, max_retries, etc.)
--   - pipeline_runs + pipeline_step_checkpoints: new tables
--   - schema_migrations: version tracking table
--
-- NOTE: SQLite ALTER TABLE ADD COLUMN does not support IF NOT EXISTS.
--   If a column already exists (e.g. created by runtime DDL), ALTER TABLE
--   raises "duplicate column name" error. Since 001_init.sql creates
--   queue_messages without these columns, this migration is safe for fresh
--   databases. For databases where runtime DDL already added these columns,
--   the migration runner (migrations.py) catches "duplicate column name"
--   and marks the migration as applied. For manual execution, comment out
--   the offending ALTER statements as needed.

-- ── Version tracking ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Record this migration
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES ('002', 'schema_sync');

-- ── queue_messages: add missing columns ───────────────────────
-- Use ALTER TABLE ADD COLUMN (SQLite supports this, columns are added to the end)
-- IF NOT EXISTS is not supported in ALTER TABLE, so we use a safe pattern

-- Column: retries
ALTER TABLE queue_messages ADD COLUMN retries INTEGER NOT NULL DEFAULT 0;
-- Column: max_retries  
ALTER TABLE queue_messages ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3;
-- Column: ack_timeout_s
ALTER TABLE queue_messages ADD COLUMN ack_timeout_s REAL NOT NULL DEFAULT 30.0;
-- Column: enqueued_at
ALTER TABLE queue_messages ADD COLUMN enqueued_at REAL NOT NULL DEFAULT 0.0;
-- Column: visible_at
ALTER TABLE queue_messages ADD COLUMN visible_at REAL NOT NULL DEFAULT 0.0;
-- Column: dequeued_at
ALTER TABLE queue_messages ADD COLUMN dequeued_at REAL NOT NULL DEFAULT 0.0;
-- Column: acked_at
ALTER TABLE queue_messages ADD COLUMN acked_at REAL NOT NULL DEFAULT 0.0;
-- Column: consumer_group
ALTER TABLE queue_messages ADD COLUMN consumer_group TEXT NOT NULL DEFAULT '';

-- Add index for consumer group dequeue
CREATE INDEX IF NOT EXISTS idx_qm_status_dequeued ON queue_messages(status, dequeued_at);
CREATE INDEX IF NOT EXISTS idx_qm_cg_status_visible ON queue_messages(consumer_group, status, visible_at);

-- ── Pipeline checkpoint tables ────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY,
  workflow_name TEXT NOT NULL,
  status TEXT DEFAULT 'running',
  variables TEXT DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS pipeline_step_checkpoints (
  run_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  output TEXT DEFAULT '',
  started_at REAL DEFAULT 0.0,
  completed_at REAL DEFAULT 0.0,
  metadata TEXT DEFAULT '{}',
  attempts INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  PRIMARY KEY (run_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_ckpt_run ON pipeline_step_checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_ckpt_status ON pipeline_step_checkpoints(status);

-- ── jwt_revoked table (for persistent JWT blacklist, P2 fix) ───
CREATE TABLE IF NOT EXISTS jwt_revoked (
  jti TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  revoked_at REAL NOT NULL
);

-- ── budget ledger table (for BudgetGuard SQLite variant) ──────
CREATE TABLE IF NOT EXISTS budget_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  agent TEXT NOT NULL DEFAULT '',
  estimated_cost REAL NOT NULL DEFAULT 0.0,
  actual_cost REAL NOT NULL DEFAULT 0.0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bl_tenant_ts ON budget_ledger(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_bl_agent ON budget_ledger(agent);

-- ── agent_performance table ───────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_performance (
  agent TEXT PRIMARY KEY,
  total_calls INTEGER NOT NULL DEFAULT 0,
  successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  total_duration_ms INTEGER NOT NULL DEFAULT 0,
  last_call_at TEXT,
  avg_duration_ms REAL DEFAULT 0.0,
  updated_at REAL NOT NULL DEFAULT 0.0
);

-- ── evolve_history table ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS evolve_history (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  suggestion_type TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}',
  applied_at TEXT NOT NULL,
  success INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_eh_agent ON evolve_history(agent);
CREATE INDEX IF NOT EXISTS idx_eh_applied_at ON evolve_history(applied_at);