# MAOP Database Schema

> Auto-generated from source code CREATE TABLE statements.
> ⚠️ 更正 2026-08-26：代码库实际含 **101 张 distinct 表**（`grep -ri "CREATE TABLE" py/maop` 统计，排除 `_subagents_new` 等临时迁移表）。本文档当前仅记录其中 53 张，其余约 48 张（enterprise / monitoring / evolution 等模块）待补全。

---

## 1. Authentication — `core/security/auth.py`

### api_keys

Stores API key hashes for inter-service authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key_hash | TEXT | PRIMARY KEY | SHA-256 hash of the API key |
| name | TEXT | NOT NULL | Human-readable key name |
| roles | TEXT | NOT NULL DEFAULT '[]' | JSON array of role names |
| created_at | REAL | NOT NULL | Unix timestamp of creation |
| expires_at | REAL | | Unix timestamp of expiration (NULL = never) |
| enabled | INTEGER | NOT NULL DEFAULT 1 | 1 = active, 0 = disabled |
| rate_limit | INTEGER | NOT NULL DEFAULT 0 | Requests per minute (0 = unlimited) |

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    expires_at REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    rate_limit INTEGER NOT NULL DEFAULT 0
);
```

---

## 2. Core Data — `core/backends/data.py`

### delegations

Agent delegation/invocation history with routing and execution results.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-increment ID |
| timestamp | TEXT | | ISO-8601 timestamp |
| agent | TEXT | | Agent name |
| task | TEXT | | Task description |
| routing_key | TEXT | | Routing key for dispatcher |
| exit_code | INT | | Process exit code |
| stdout | TEXT | | Standard output |
| stderr | TEXT | | Standard error |
| duration_ms | INT | | Execution duration in milliseconds |
| trace_id | TEXT | | Distributed trace ID |

```sql
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
```

### metrics

Metric data points categorized by agent and metric name.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-increment ID |
| timestamp | TEXT | | ISO-8601 timestamp |
| agent | TEXT | | Agent name |
| metric_name | TEXT | | Metric identifier |
| metric_value | REAL | | Numeric value |
| tags | TEXT | | JSON tags for filtering |

```sql
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT,
  agent TEXT,
  metric_name TEXT,
  metric_value REAL,
  tags TEXT
);
```

### checkpoints

Agent task checkpoint state for task resumption.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Checkpoint UUID |
| agent | TEXT | | Agent name |
| task | TEXT | | Task description |
| phase | TEXT | | Current phase |
| state_json | TEXT | | Serialized state |
| created | TEXT | | Creation timestamp |
| updated | TEXT | | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  agent TEXT,
  task TEXT,
  phase TEXT,
  state_json TEXT,
  created TEXT,
  updated TEXT
);
```

### circuit_breaker

Circuit breaker state per agent (legacy table, superseded by circuit_breaker_state).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| agent | TEXT | PRIMARY KEY | Agent name |
| state | TEXT | DEFAULT 'closed' | closed/open/half-open |
| failures | INT | DEFAULT 0 | Consecutive failure count |
| threshold | INT | DEFAULT 3 | Failure threshold for tripping |
| last_failure | TEXT | | Timestamp of last failure |
| cooldown_s | INT | DEFAULT 60 | Cooldown period in seconds |
| updated | TEXT | | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker (
  agent TEXT PRIMARY KEY,
  state TEXT DEFAULT 'closed',
  failures INT DEFAULT 0,
  threshold INT DEFAULT 3,
  last_failure TEXT,
  cooldown_s INT DEFAULT 60,
  updated TEXT
);
```

### error_log

Agent execution error log.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-increment ID |
| timestamp | TEXT | | ISO-8601 timestamp |
| agent | TEXT | | Agent name |
| task | TEXT | | Task description |
| exit_code | INT | | Process exit code |
| error | TEXT | | Error message |
| trace_id | TEXT | | Distributed trace ID |
| duration_ms | INT | | Execution duration |

```sql
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
```

---

## 3. Session — `core/security/session.py`

### sessions

Persistent conversation sessions with token budgets and metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Session UUID |
| agent | TEXT | NOT NULL DEFAULT '' | Associated agent |
| workdir | TEXT | NOT NULL DEFAULT '' | Working directory |
| status | TEXT | NOT NULL DEFAULT 'active' | active/paused/completed/archived |
| tags | TEXT | NOT NULL DEFAULT '[]' | JSON array of tags |
| metadata | TEXT | NOT NULL DEFAULT '{}' | JSON metadata |
| token_count | INTEGER | NOT NULL DEFAULT 0 | Tokens consumed |
| token_budget | INTEGER | NOT NULL DEFAULT 0 | Token budget limit |
| message_count | INTEGER | NOT NULL DEFAULT 0 | Number of messages |
| created_at | TEXT | NOT NULL | Creation timestamp |
| updated_at | TEXT | NOT NULL | Last update timestamp |
| last_active_at | TEXT | NOT NULL | Last activity timestamp |

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL DEFAULT '',
    workdir TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    token_count INTEGER NOT NULL DEFAULT 0,
    token_budget INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);
```

---

## 4. Circuit Breaker — `core/reliability/circuit_breaker.py`

### circuit_breaker_state

Circuit breaker state machine per agent.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| agent | TEXT | PRIMARY KEY | Agent name |
| state | TEXT | NOT NULL DEFAULT 'closed' | closed/open/half-open |
| failures | INTEGER | NOT NULL DEFAULT 0 | Consecutive failure count |
| threshold | INTEGER | NOT NULL DEFAULT 3 | Trip threshold |
| last_failure | REAL | | Unix timestamp of last failure |
| cooldown_s | INTEGER | NOT NULL DEFAULT 60 | Cooldown in seconds |
| updated | REAL | NOT NULL DEFAULT 0.0 | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
  agent TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'closed',
  failures INTEGER NOT NULL DEFAULT 0,
  threshold INTEGER NOT NULL DEFAULT 3,
  last_failure REAL,
  cooldown_s INTEGER NOT NULL DEFAULT 60,
  updated REAL NOT NULL DEFAULT 0.0
);
```

### failover_chains

Agent failover chain configuration (primary → backup → tertiary).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Chain name |
| agents | TEXT | NOT NULL DEFAULT '[]' | JSON array of agent names |
| current_index | INTEGER | NOT NULL DEFAULT 0 | Current active agent index |
| updated | REAL | NOT NULL DEFAULT 0.0 | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS failover_chains (
  name TEXT PRIMARY KEY,
  agents TEXT NOT NULL DEFAULT '[]',
  current_index INTEGER NOT NULL DEFAULT 0,
  updated REAL NOT NULL DEFAULT 0.0
);
```

### breaker_events

Circuit breaker state transition event log.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-increment ID |
| agent | TEXT | NOT NULL | Agent name |
| old_state | TEXT | NOT NULL DEFAULT '' | Previous state |
| new_state | TEXT | NOT NULL DEFAULT '' | New state |
| failures | INTEGER | NOT NULL DEFAULT 0 | Failure count at transition |
| timestamp | REAL | NOT NULL DEFAULT 0.0 | Unix timestamp |

```sql
CREATE TABLE IF NOT EXISTS breaker_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  old_state TEXT NOT NULL DEFAULT '',
  new_state TEXT NOT NULL DEFAULT '',
  failures INTEGER NOT NULL DEFAULT 0,
  timestamp REAL NOT NULL DEFAULT 0.0
);
```

---

## 5. API Key Vault — `core/security/api_key_vault.py`

### api_keys

Encrypted LLM provider API keys (Fernet symmetric encryption).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| provider | TEXT | PRIMARY KEY | LLM provider name |
| encrypted_key | TEXT | NOT NULL | Fernet-encrypted API key |
| created_at | TEXT | NOT NULL | Creation timestamp |
| updated_at | TEXT | DEFAULT '' | Last update timestamp |

> **Note**: This table shares the name `api_keys` with `core/security/auth.py`. They are in separate SQLite databases.

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    provider TEXT PRIMARY KEY,
    encrypted_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT DEFAULT ''
);
```

---

## 6. Time Series — `core/monitoring/timeseries.py`

### ts_raw

Raw time-series data points (1-minute granularity, 24-hour retention).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| timestamp | REAL | NOT NULL | Unix timestamp |
| metric | TEXT | NOT NULL | Metric identifier |
| value | REAL | NOT NULL | Data point value |
| tags | TEXT | NOT NULL DEFAULT '{}' | JSON tags |

> Composite PK: `(metric, timestamp)`, WITHOUT ROWID for performance.

```sql
CREATE TABLE IF NOT EXISTS ts_raw (
    timestamp REAL NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    tags TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (metric, timestamp)
) WITHOUT ROWID;
```

### ts_5min

5-minute aggregated time-series (7-day retention).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| timestamp | REAL | NOT NULL | Bucket start time |
| metric | TEXT | NOT NULL | Metric identifier |
| avg_value | REAL | NOT NULL | Average value |
| min_value | REAL | NOT NULL | Minimum value |
| max_value | REAL | NOT NULL | Maximum value |
| sum_value | REAL | NOT NULL | Sum of values |
| count | INTEGER | NOT NULL DEFAULT 1 | Number of data points |
| tags | TEXT | NOT NULL DEFAULT '{}' | JSON tags |

```sql
CREATE TABLE IF NOT EXISTS ts_5min (
    timestamp REAL NOT NULL,
    metric TEXT NOT NULL,
    avg_value REAL NOT NULL,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    sum_value REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (metric, timestamp)
) WITHOUT ROWID;
```

### ts_1hour

1-hour aggregated time-series (90-day retention).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| timestamp | REAL | NOT NULL | Bucket start time |
| metric | TEXT | NOT NULL | Metric identifier |
| avg_value | REAL | NOT NULL | Average value |
| min_value | REAL | NOT NULL | Minimum value |
| max_value | REAL | NOT NULL | Maximum value |
| sum_value | REAL | NOT NULL | Sum of values |
| count | INTEGER | NOT NULL DEFAULT 1 | Number of data points |
| tags | TEXT | NOT NULL DEFAULT '{}' | JSON tags |

```sql
CREATE TABLE IF NOT EXISTS ts_1hour (
    timestamp REAL NOT NULL,
    metric TEXT NOT NULL,
    avg_value REAL NOT NULL,
    min_value REAL NOT NULL,
    sum_value REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (metric, timestamp)
) WITHOUT ROWID;
```

---

## 7. KV Store — `core/backends/kv_store.py`

### kv_store

Lightweight SQLite key-value store with namespaces, TTL, and CAS.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key | TEXT | NOT NULL | Key name |
| namespace | TEXT | NOT NULL DEFAULT 'default' | Namespace partition |
| value | TEXT | NOT NULL | Stored value |
| ttl_expires | REAL | | Unix timestamp for TTL expiration |
| created_at | REAL | NOT NULL | Creation timestamp |
| updated_at | REAL | NOT NULL | Last update timestamp |
| version | INTEGER | NOT NULL DEFAULT 1 | Version counter for CAS |

> Composite PK: `(key, namespace)`

```sql
CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    ttl_expires REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (key, namespace)
);
```

---

## 8. Vector Search — `core/memory/vector_store.py`

### vector_entries

Vector index entries for cosine similarity semantic search.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Entry UUID |
| text | TEXT | NOT NULL DEFAULT '' | Source text |
| vector | TEXT | NOT NULL DEFAULT '[]' | JSON-encoded float array |
| metadata | TEXT | NOT NULL DEFAULT '{}' | JSON metadata |
| created_at | REAL | NOT NULL DEFAULT 0.0 | Unix timestamp |

```sql
CREATE TABLE IF NOT EXISTS vector_entries (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL DEFAULT '',
  vector TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL DEFAULT 0.0
);
```

---

## 9. Worktree — `core/agent/memory_ctx/worktree.py`

### worktrees

Git worktree isolated execution workspace metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Worktree UUID |
| path | TEXT | NOT NULL | Filesystem path |
| branch | TEXT | DEFAULT '' | Git branch name |
| task_id | TEXT | DEFAULT '' | Associated task ID |
| status | TEXT | DEFAULT 'active' | active/finished/cleaned |
| created_at | TEXT | NOT NULL | Creation timestamp |
| finished_at | TEXT | DEFAULT '' | Completion timestamp |

```sql
CREATE TABLE IF NOT EXISTS worktrees (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    branch TEXT DEFAULT '',
    task_id TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    finished_at TEXT DEFAULT ''
);
```

---

## 10. Tool Manager — `core/agent/tools/tool_manager.py`

### tools

Registered external CLI tools with categories and invocation stats.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Tool UUID |
| name | TEXT | NOT NULL | Display name |
| description | TEXT | DEFAULT '' | Tool description |
| command | TEXT | NOT NULL | Executable command |
| category | TEXT | DEFAULT 'general' | Tool category |
| params | TEXT | DEFAULT '{}' | JSON parameter schema |
| enabled | INTEGER | DEFAULT 1 | 1 = enabled, 0 = disabled |
| created | TEXT | NOT NULL | Registration timestamp |
| last_called | TEXT | | Last invocation timestamp |
| call_count | INTEGER | DEFAULT 0 | Total invocation count |

```sql
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    command TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    params TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    created TEXT NOT NULL,
    last_called TEXT,
    call_count INTEGER DEFAULT 0
);
```

---

## 11. Subagent — `core/agent/delegation/subagent_db.py`

### subagents

Hierarchical agent delegation relationships (parent → child).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Delegation UUID |
| parent_agent | TEXT | NOT NULL | Parent agent name |
| child_agent | TEXT | NOT NULL | Child agent name |
| task | TEXT | DEFAULT '' | Task description |
| status | TEXT | DEFAULT 'spawned' | spawned/running/done/failed |
| created_at | TEXT | NOT NULL | Creation timestamp |
| finished_at | TEXT | DEFAULT '' | Completion timestamp |
| exit_code | INTEGER | | Process exit code |
| depth | INTEGER | DEFAULT 0 | Nesting depth |

```sql
CREATE TABLE IF NOT EXISTS subagents (
    id TEXT PRIMARY KEY,
    parent_agent TEXT NOT NULL,
    child_agent TEXT NOT NULL,
    task TEXT DEFAULT '',
    status TEXT DEFAULT 'spawned',
    created_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    exit_code INTEGER,
    depth INTEGER DEFAULT 0
);
```

### agent_messages

Inter-agent communication messages.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| sender | TEXT | NOT NULL | Sending agent |
| recipient | TEXT | NOT NULL | Receiving agent |
| msg_type | TEXT | DEFAULT 'info' | Message type |
| payload | TEXT | DEFAULT '{}' | JSON payload |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    msg_type TEXT DEFAULT 'info',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

---

## 12. Sandbox — `core/security/sandbox.py`

### sandboxes

Isolated execution sandbox metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Sandbox UUID |
| created | TEXT | NOT NULL | Creation timestamp |
| status | TEXT | DEFAULT 'active' | active/finished/failed |
| path | TEXT | NOT NULL | Sandbox directory path |
| command | TEXT | DEFAULT '' | Executed command |
| exit_code | INTEGER | DEFAULT 0 | Process exit code |
| duration_ms | INTEGER | DEFAULT 0 | Execution duration |
| output_lines | INTEGER | DEFAULT 0 | Output line count |

```sql
CREATE TABLE IF NOT EXISTS sandboxes (
    id TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    path TEXT NOT NULL,
    command TEXT DEFAULT '',
    exit_code INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    output_lines INTEGER DEFAULT 0
);
```

---

## 13. Protocol — `core/agent/plugins_hooks/protocol.py`

### protocols

Dynamic agent communication protocol definitions with JSON Schema.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | NOT NULL | Protocol name |
| version | TEXT | NOT NULL DEFAULT '1.0' | Protocol version |
| schema_def | TEXT | DEFAULT '{}' | JSON Schema definition |
| participants | TEXT | DEFAULT '[]' | JSON array of participant agents |
| description | TEXT | DEFAULT '' | Protocol description |
| created_at | TEXT | NOT NULL | Creation timestamp |
| updated_at | TEXT | DEFAULT '' | Last update timestamp |

> Composite PK: `(name, version)`

```sql
CREATE TABLE IF NOT EXISTS protocols (
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0',
    schema_def TEXT DEFAULT '{}',
    participants TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT DEFAULT '',
    PRIMARY KEY (name, version)
);
```

### protocol_messages

Protocol-validated inter-agent messages.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| protocol | TEXT | NOT NULL | Protocol name |
| version | TEXT | DEFAULT '1.0' | Protocol version |
| sender | TEXT | NOT NULL | Sending agent |
| recipient | TEXT | NOT NULL | Receiving agent |
| payload | TEXT | DEFAULT '{}' | JSON payload |
| valid | INTEGER | DEFAULT 1 | Schema validation result |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS protocol_messages (
    id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    version TEXT DEFAULT '1.0',
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    valid INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
```

---

## 14. Plugin — `core/agent/plugins_hooks/plugin_manager.py`

### plugins

Plugin lifecycle state persistence.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Plugin UUID |
| name | TEXT | NOT NULL | Plugin name |
| version | TEXT | DEFAULT '0.1.0' | Plugin version |
| description | TEXT | DEFAULT '' | Description |
| author | TEXT | DEFAULT '' | Author |
| state | TEXT | NOT NULL DEFAULT 'discovered' | discovered/loaded/started/stopped/error |
| path | TEXT | DEFAULT '' | Plugin directory path |
| error | TEXT | DEFAULT '' | Last error message |
| loaded_at | TEXT | DEFAULT '' | Load timestamp |
| started_at | TEXT | DEFAULT '' | Start timestamp |
| config | TEXT | DEFAULT '{}' | JSON configuration |

```sql
CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT DEFAULT '0.1.0',
    description TEXT DEFAULT '',
    author TEXT DEFAULT '',
    state TEXT NOT NULL DEFAULT 'discovered',
    path TEXT DEFAULT '',
    error TEXT DEFAULT '',
    loaded_at TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    config TEXT DEFAULT '{}'
);
```

---

## 15. Permission — `core/security/permission.py`

### permission_rules

Agent operation permission rules (allow/ask/deny) with wildcard matching.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Rule UUID |
| agent | TEXT | NOT NULL DEFAULT '*' | Agent name pattern |
| action | TEXT | NOT NULL DEFAULT '*' | Action pattern |
| decision | TEXT | NOT NULL DEFAULT 'ask' | allow/ask/deny |
| reason | TEXT | DEFAULT '' | Reason for the rule |
| created | TEXT | NOT NULL | Creation timestamp |
| priority | INTEGER | DEFAULT 0 | Higher = evaluated first |

```sql
CREATE TABLE IF NOT EXISTS permission_rules (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL DEFAULT '*',
    action TEXT NOT NULL DEFAULT '*',
    decision TEXT NOT NULL DEFAULT 'ask',
    reason TEXT DEFAULT '',
    created TEXT NOT NULL,
    priority INTEGER DEFAULT 0
);
```

---

## 16. Message Queue — `core/reliability/message_queue.py`

### queue_messages

Persistent message queue with priority, ACK, consumer groups, and delayed delivery.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| topic | TEXT | NOT NULL | Topic name |
| payload | TEXT | NOT NULL DEFAULT '{}' | JSON payload |
| priority | INTEGER | NOT NULL DEFAULT 5 | Priority (1-10) |
| status | TEXT | NOT NULL DEFAULT 'pending' | pending/dequeued/acked |
| retries | INTEGER | NOT NULL DEFAULT 0 | Current retry count |
| max_retries | INTEGER | NOT NULL DEFAULT 3 | Maximum retries |
| ack_timeout_s | REAL | NOT NULL DEFAULT 30.0 | ACK timeout in seconds |
| enqueued_at | REAL | NOT NULL DEFAULT 0.0 | Enqueue timestamp |
| visible_at | REAL | NOT NULL DEFAULT 0.0 | Visibility timestamp (delayed) |
| dequeued_at | REAL | NOT NULL DEFAULT 0.0 | Dequeue timestamp |
| acked_at | REAL | NOT NULL DEFAULT 0.0 | ACK timestamp |
| consumer_group | TEXT | NOT NULL DEFAULT '' | Consumer group name |
| consumer_id | TEXT | NOT NULL DEFAULT '' | Consumer instance ID |

```sql
CREATE TABLE IF NOT EXISTS queue_messages (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'pending',
  retries INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 3,
  ack_timeout_s REAL NOT NULL DEFAULT 30.0,
  enqueued_at REAL NOT NULL DEFAULT 0.0,
  visible_at REAL NOT NULL DEFAULT 0.0,
  dequeued_at REAL NOT NULL DEFAULT 0.0,
  acked_at REAL NOT NULL DEFAULT 0.0,
  consumer_group TEXT NOT NULL DEFAULT '',
  consumer_id TEXT NOT NULL DEFAULT ''
);
```

### queue_dead_letters

Dead letter messages that exceeded max retries.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| topic | TEXT | NOT NULL | Original topic |
| payload | TEXT | NOT NULL DEFAULT '{}' | JSON payload |
| priority | INTEGER | NOT NULL DEFAULT 5 | Original priority |
| retries | INTEGER | NOT NULL DEFAULT 0 | Final retry count |
| error | TEXT | NOT NULL DEFAULT '' | Last error message |
| consumer_group | TEXT | NOT NULL DEFAULT '' | Consumer group |
| dead_at | REAL | NOT NULL DEFAULT 0.0 | Dead-letter timestamp |

```sql
CREATE TABLE IF NOT EXISTS queue_dead_letters (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 5,
  retries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  consumer_group TEXT NOT NULL DEFAULT '',
  dead_at REAL NOT NULL DEFAULT 0.0
);
```

### queue_idempotent

Idempotent consumption tracker to prevent duplicate processing.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| msg_id | TEXT | PRIMARY KEY | Original message ID |
| consumer_id | TEXT | NOT NULL | Consumer that processed it |
| processed_at | REAL | NOT NULL DEFAULT 0.0 | Processing timestamp |

```sql
CREATE TABLE IF NOT EXISTS queue_idempotent (
  msg_id TEXT PRIMARY KEY,
  consumer_id TEXT NOT NULL,
  processed_at REAL NOT NULL DEFAULT 0.0
);
```

---

## 17. MCP Registry — `core/mcp/mcp_hub.py`

### mcp_servers

MCP (Model Context Protocol) server connection configurations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Server name |
| config | TEXT | NOT NULL | JSON connection config |
| created | TEXT | NOT NULL | Registration timestamp |
| updated | TEXT | | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    name TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT
);
```

---

## 18. Knowledge Extractor — `core/memory/knowledge_extractor.py`

### facts

Structured knowledge triples (subject-predicate-object) extracted from conversations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Fact UUID |
| subject | TEXT | NOT NULL | Subject entity |
| predicate | TEXT | NOT NULL | Relationship type |
| object_value | TEXT | NOT NULL | Object entity |
| source_exchange | TEXT | DEFAULT '' | Source conversation reference |
| topic | TEXT | DEFAULT '' | Topic category |
| confidence | REAL | DEFAULT 1.0 | Extraction confidence (0-1) |
| created_at | TEXT | NOT NULL | Creation timestamp |
| access_count | INTEGER | DEFAULT 0 | Access frequency |

```sql
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_value TEXT NOT NULL,
    source_exchange TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    access_count INTEGER DEFAULT 0
);
```

### entities

Named entities extracted from text (class names, file names, function names, config keys).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Entity name |
| entity_type | TEXT | DEFAULT 'concept' | Entity type |
| attributes | TEXT | DEFAULT '{}' | JSON attributes |
| confidence | REAL | DEFAULT 1.0 | Extraction confidence |

```sql
CREATE TABLE IF NOT EXISTS entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT DEFAULT 'concept',
    attributes TEXT DEFAULT '{}',
    confidence REAL DEFAULT 1.0
);
```

### relations

Entity relationships (uses/depends_on/extends/calls etc.).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Relation UUID |
| source | TEXT | NOT NULL | Source entity |
| target | TEXT | NOT NULL | Target entity |
| relation_type | TEXT | NOT NULL | Relationship type |
| context | TEXT | DEFAULT '' | Context description |
| confidence | REAL | DEFAULT 1.0 | Extraction confidence |

```sql
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    context TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0
);
```

---

## 19. Image Store — `core/backends/image_store.py`

### images

Multi-modal chat image metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Image UUID |
| session_id | TEXT | NOT NULL | Associated session |
| filename | TEXT | DEFAULT '' | Original filename |
| content_type | TEXT | DEFAULT '' | MIME type |
| size_bytes | INTEGER | DEFAULT 0 | File size |
| width | INTEGER | DEFAULT 0 | Image width |
| height | INTEGER | DEFAULT 0 | Image height |
| checksum | TEXT | DEFAULT '' | SHA-256 checksum |
| created_at | TEXT | NOT NULL | Upload timestamp |
| file_path | TEXT | NOT NULL | Storage path |

```sql
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    filename TEXT DEFAULT '',
    content_type TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    checksum TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    file_path TEXT NOT NULL
);
```

---

## 20. Human Proxy — `core/agent/delegation/human_proxy.py`

### approval_requests

Human-in-the-loop approval requests.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Request UUID |
| task | TEXT | NOT NULL | Task description |
| agent | TEXT | DEFAULT '' | Requesting agent |
| requester | TEXT | DEFAULT 'system' | Requester identity |
| priority | TEXT | DEFAULT 'medium' | low/medium/high/critical |
| reason | TEXT | DEFAULT '' | Reason for approval |
| status | TEXT | DEFAULT 'pending' | pending/approved/rejected/expired |
| created | TEXT | NOT NULL | Creation timestamp |
| resolved | TEXT | | Resolution timestamp |
| metadata | TEXT | DEFAULT '{}' | JSON metadata |

```sql
CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    agent TEXT DEFAULT '',
    requester TEXT DEFAULT 'system',
    priority TEXT DEFAULT 'medium',
    reason TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created TEXT NOT NULL,
    resolved TEXT,
    metadata TEXT DEFAULT '{}'
);
```

---

## 21. Hook Manager — `core/agent/plugins_hooks/hook_manager.py`

### hooks

Lifecycle hook registrations (callback/webhook).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Hook UUID |
| event | TEXT | NOT NULL | Event name |
| hook_type | TEXT | NOT NULL DEFAULT 'callback' | callback/webhook |
| callback | TEXT | DEFAULT '' | Python callable path |
| url | TEXT | DEFAULT '' | Webhook URL |
| enabled | INTEGER | DEFAULT 1 | 1 = active |
| priority | INTEGER | DEFAULT 0 | Execution priority |
| description | TEXT | DEFAULT '' | Description |
| created_at | TEXT | NOT NULL | Registration timestamp |
| source | TEXT | DEFAULT 'api' | Registration source |

```sql
CREATE TABLE IF NOT EXISTS hooks (
    id TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    hook_type TEXT NOT NULL DEFAULT 'callback',
    callback TEXT DEFAULT '',
    url TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    source TEXT DEFAULT 'api'
);
```

### hook_logs

Hook execution logs.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Log UUID |
| hook_id | TEXT | NOT NULL | Associated hook |
| event | TEXT | NOT NULL | Triggered event |
| success | INTEGER | DEFAULT 1 | 1 = success, 0 = failure |
| error | TEXT | DEFAULT '' | Error message |
| duration_ms | INTEGER | DEFAULT 0 | Execution duration |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS hook_logs (
    id TEXT PRIMARY KEY,
    hook_id TEXT NOT NULL,
    event TEXT NOT NULL,
    success INTEGER DEFAULT 1,
    error TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
```

---

## 22. Cost Tracker — `core/cost_tracker.py`

### cost_entries

LLM call token usage and cost tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Entry UUID |
| session_id | TEXT | DEFAULT '' | Associated session |
| agent | TEXT | DEFAULT '' | Agent name |
| model | TEXT | DEFAULT '' | LLM model name |
| prompt_tokens | INTEGER | DEFAULT 0 | Input tokens |
| completion_tokens | INTEGER | DEFAULT 0 | Output tokens |
| total_tokens | INTEGER | DEFAULT 0 | Total tokens |
| cost_usd | REAL | DEFAULT 0.0 | Cost in USD |
| latency_ms | INTEGER | DEFAULT 0 | Response latency |
| metadata | TEXT | DEFAULT '{}' | JSON metadata |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS cost_entries (
    id TEXT PRIMARY KEY,
    session_id TEXT DEFAULT '',
    agent TEXT DEFAULT '',
    model TEXT DEFAULT '',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    latency_ms INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

---

## 23. Conversation — `core/agent/llm_chat/conversation.py`

### messages

Multi-turn conversation messages.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| session_id | TEXT | NOT NULL | Associated session |
| role | TEXT | NOT NULL | user/assistant/system/tool |
| content | TEXT | NOT NULL | Message content |
| metadata | TEXT | NOT NULL DEFAULT '{}' | JSON metadata |
| token_count | INTEGER | NOT NULL DEFAULT 0 | Token count |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
```

---

## 24. Change Tracker — `core/reliability/change_tracker.py`

### snapshots

File system snapshot metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Snapshot UUID |
| workdir | TEXT | NOT NULL | Working directory |
| label | TEXT | NOT NULL DEFAULT '' | Snapshot label |
| file_count | INTEGER | NOT NULL DEFAULT 0 | Number of files |
| total_size | INTEGER | NOT NULL DEFAULT 0 | Total size in bytes |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    workdir TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
```

### file_states

Per-file hash/size/mtime within a snapshot for change detection.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| snapshot_id | TEXT | NOT NULL | Parent snapshot |
| path | TEXT | NOT NULL | File path |
| hash | TEXT | NOT NULL DEFAULT '' | SHA-256 hash |
| size | INTEGER | NOT NULL DEFAULT 0 | File size |
| modified | TEXT | NOT NULL DEFAULT '' | Modification time |

> Composite PK: `(snapshot_id, path)`

```sql
CREATE TABLE IF NOT EXISTS file_states (
    snapshot_id TEXT NOT NULL,
    path TEXT NOT NULL,
    hash TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    modified TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (snapshot_id, path)
);
```

### change_log

File change log (added/modified/deleted).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Auto-increment ID |
| workdir | TEXT | NOT NULL | Working directory |
| path | TEXT | NOT NULL | File path |
| change_type | TEXT | NOT NULL | added/modified/deleted |
| old_hash | TEXT | DEFAULT '' | Previous hash |
| new_hash | TEXT | DEFAULT '' | New hash |
| snapshot_from | TEXT | DEFAULT '' | Source snapshot |
| snapshot_to | TEXT | DEFAULT '' | Target snapshot |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workdir TEXT NOT NULL,
    path TEXT NOT NULL,
    change_type TEXT NOT NULL,
    old_hash TEXT DEFAULT '',
    new_hash TEXT DEFAULT '',
    snapshot_from TEXT DEFAULT '',
    snapshot_to TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
```

---

## 25. Artifact Store — `core/backends/artifact_store.py`

### artifacts

Artifact metadata with version tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Artifact name |
| latest_version | INTEGER | NOT NULL DEFAULT 0 | Latest version number |
| total_versions | INTEGER | NOT NULL DEFAULT 0 | Total version count |
| total_size_bytes | INTEGER | NOT NULL DEFAULT 0 | Cumulative size |
| created_at | TEXT | NOT NULL | Creation timestamp |
| updated_at | TEXT | NOT NULL | Last update timestamp |

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    name TEXT PRIMARY KEY,
    latest_version INTEGER NOT NULL DEFAULT 0,
    total_versions INTEGER NOT NULL DEFAULT 0,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### artifact_versions

Per-version artifact data with content hash and blob storage.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Version UUID |
| artifact_name | TEXT | NOT NULL | Parent artifact |
| version | INTEGER | NOT NULL | Version number |
| content_hash | TEXT | NOT NULL DEFAULT '' | SHA-256 content hash |
| size_bytes | INTEGER | NOT NULL DEFAULT 0 | Content size |
| tag | TEXT | NOT NULL DEFAULT '' | Version tag |
| metadata | TEXT | NOT NULL DEFAULT '{}' | JSON metadata |
| blob_path | TEXT | NOT NULL DEFAULT '' | Blob storage path |
| created_at | TEXT | NOT NULL | Timestamp |

```sql
CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT PRIMARY KEY,
    artifact_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    tag TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    blob_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

---

## 26. Agent Scanner — `core/agent/lifecycle/agent_scanner.py`

### scanned_agents

Auto-discovered agent CLI information.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Agent name |
| cli_path | TEXT | DEFAULT '' | CLI executable path |
| version | TEXT | DEFAULT '' | Agent version |
| source | TEXT | DEFAULT 'scanned' | Discovery source |
| status | TEXT | DEFAULT 'unknown' | known/unknown/error |
| capabilities | TEXT | DEFAULT '[]' | JSON capability list |
| provider | TEXT | DEFAULT '' | LLM provider |
| description | TEXT | DEFAULT '' | Description |
| model | TEXT | DEFAULT '' | Default model |
| timeout_s | INTEGER | DEFAULT 120 | Default timeout |
| driver | TEXT | DEFAULT 'cli' | Driver type |
| cli_args | TEXT | DEFAULT '' | Additional CLI arguments |
| last_checked | TEXT | DEFAULT '' | Last scan timestamp |
| error | TEXT | DEFAULT '' | Last error |

```sql
CREATE TABLE IF NOT EXISTS scanned_agents (
    name TEXT PRIMARY KEY,
    cli_path TEXT DEFAULT '',
    version TEXT DEFAULT '',
    source TEXT DEFAULT 'scanned',
    status TEXT DEFAULT 'unknown',
    capabilities TEXT DEFAULT '[]',
    provider TEXT DEFAULT '',
    description TEXT DEFAULT '',
    model TEXT DEFAULT '',
    timeout_s INTEGER DEFAULT 120,
    driver TEXT DEFAULT 'cli',
    cli_args TEXT DEFAULT '',
    last_checked TEXT DEFAULT '',
    error TEXT DEFAULT ''
);
```

---

## 27. Agent Registry — `core/agent/lifecycle/agent_registry.py`

### registered_agents

Unified agent registry with health status and failover support.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Agent name |
| cli_path | TEXT | DEFAULT '' | CLI executable path |
| version | TEXT | DEFAULT '' | Agent version |
| provider | TEXT | DEFAULT '' | LLM provider |
| capabilities | TEXT | DEFAULT '[]' | JSON capability list |
| description | TEXT | DEFAULT '' | Description |
| model | TEXT | DEFAULT '' | Default model |
| driver | TEXT | DEFAULT 'cli' | Driver type |
| cli_args | TEXT | DEFAULT '' | Additional CLI arguments |
| timeout_s | INTEGER | DEFAULT 120 | Default timeout |
| enabled | INTEGER | DEFAULT 1 | 1 = enabled |
| health | TEXT | DEFAULT 'unknown' | healthy/unhealthy/unknown |
| last_health_check | TEXT | DEFAULT '' | Last check timestamp |
| last_latency_ms | INTEGER | DEFAULT 0 | Last response latency |
| consecutive_failures | INTEGER | DEFAULT 0 | Current failure streak |
| registered_at | TEXT | DEFAULT '' | Registration timestamp |
| source | TEXT | DEFAULT 'scanned' | Registration source |

```sql
CREATE TABLE IF NOT EXISTS registered_agents (
    name TEXT PRIMARY KEY,
    cli_path TEXT DEFAULT '',
    version TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    capabilities TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    model TEXT DEFAULT '',
    driver TEXT DEFAULT 'cli',
    cli_args TEXT DEFAULT '',
    timeout_s INTEGER DEFAULT 120,
    enabled INTEGER DEFAULT 1,
    health TEXT DEFAULT 'unknown',
    last_health_check TEXT DEFAULT '',
    last_latency_ms INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    registered_at TEXT DEFAULT '',
    source TEXT DEFAULT 'scanned'
);
```

### health_log

Agent health check result history.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Check UUID |
| agent_name | TEXT | NOT NULL | Agent name |
| healthy | INTEGER | DEFAULT 0 | 1 = healthy, 0 = unhealthy |
| latency_ms | INTEGER | DEFAULT 0 | Response latency |
| version | TEXT | DEFAULT '' | Agent version |
| error | TEXT | DEFAULT '' | Error message |
| checked_at | TEXT | NOT NULL | Check timestamp |

```sql
CREATE TABLE IF NOT EXISTS health_log (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    healthy INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    version TEXT DEFAULT '',
    error TEXT DEFAULT '',
    checked_at TEXT NOT NULL
);
```

---

## 28. Migration — `core/backends/migration.py`

### _migrations

Database migration version tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| version | INTEGER | PRIMARY KEY | Migration version number |
| name | TEXT | NOT NULL | Migration name |
| applied_at | TEXT | NOT NULL | Application timestamp |
| checksum | TEXT | NOT NULL DEFAULT '' | Content checksum |
| execution_ms | REAL | NOT NULL DEFAULT 0.0 | Execution time |

```sql
CREATE TABLE IF NOT EXISTS _migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL DEFAULT '',
    execution_ms REAL NOT NULL DEFAULT 0.0
);
```

---

## 29. Memory Manager — `memory/manager.py`

### consolidation_log

L2→L3 memory consolidation (DreamConsolidator) execution log.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Log UUID |
| started_at | TEXT | NOT NULL | Start timestamp |
| finished_at | TEXT | DEFAULT '' | Completion timestamp |
| entries_scanned | INTEGER | DEFAULT 0 | Entries examined |
| entries_pruned | INTEGER | DEFAULT 0 | Entries pruned |
| success | INTEGER | DEFAULT 0 | 1 = success |

```sql
CREATE TABLE IF NOT EXISTS consolidation_log (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    entries_scanned INTEGER DEFAULT 0,
    entries_pruned INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0
);
```

---

## 30. Memory Models — `memory/models.py`

### memory_entries

Short-term memory entries (agent interaction records).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Entry UUID |
| agent | TEXT | NOT NULL DEFAULT '' | Agent name |
| task | TEXT | NOT NULL DEFAULT '' | Task description |
| content | TEXT | NOT NULL DEFAULT '' | Memory content |
| tags | TEXT | NOT NULL DEFAULT '' | Comma-separated tags |
| topic | TEXT | NOT NULL DEFAULT 'general' | Topic category |
| trace_id | TEXT | NOT NULL DEFAULT '' | Trace association |
| session_id | TEXT | NOT NULL DEFAULT '' | Session association |
| exit_code | INTEGER | NOT NULL DEFAULT 0 | Process exit code |
| duration_ms | INTEGER | NOT NULL DEFAULT 0 | Duration |
| timestamp | TEXT | NOT NULL DEFAULT '' | Timestamp |

```sql
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
```

### memory_traces

Session trace records linking trace_id, session_id, participating agents and status.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| trace_id | TEXT | PRIMARY KEY | Trace UUID |
| parent_trace_id | TEXT | NOT NULL DEFAULT '' | Parent trace |
| session_id | TEXT | NOT NULL DEFAULT '' | Session association |
| task | TEXT | NOT NULL DEFAULT '' | Task description |
| agents | TEXT | NOT NULL DEFAULT '' | Participating agents |
| created | TEXT | NOT NULL DEFAULT '' | Creation timestamp |
| last_active | TEXT | NOT NULL DEFAULT '' | Last activity |
| status | TEXT | NOT NULL DEFAULT 'active' | active/completed/failed |

```sql
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
```

### memory_trajectory

Agent execution trajectory steps (tool calls, I/O, timing).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Step UUID |
| trace_id | TEXT | NOT NULL DEFAULT '' | Parent trace |
| agent | TEXT | NOT NULL DEFAULT '' | Agent name |
| task | TEXT | NOT NULL DEFAULT '' | Task description |
| tool_name | TEXT | NOT NULL DEFAULT '' | Tool invoked |
| tool_input | TEXT | NOT NULL DEFAULT '' | Tool input |
| tool_output | TEXT | NOT NULL DEFAULT '' | Tool output |
| duration_ms | INTEGER | NOT NULL DEFAULT 0 | Step duration |
| exit_code | INTEGER | NOT NULL DEFAULT 0 | Exit code |
| timestamp | TEXT | NOT NULL DEFAULT '' | Timestamp |

```sql
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
```

### memory_fts (FTS5 Virtual Table)

Full-text search index for memory_entries with auto-sync triggers.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  id UNINDEXED,
  agent,
  task,
  content,
  tags,
  topic,
  content='memory_entries',
  content_rowid='rowid'
);
```

---

## 31. Dashboard Auth — `dashboard/routers/auth.py`

### users

Dashboard user accounts with PBKDF2 password hashing.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| username | TEXT | PRIMARY KEY | Username |
| password_hash | TEXT | NOT NULL | PBKDF2-SHA256 hash |
| roles | TEXT | NOT NULL DEFAULT '["admin"]' | JSON role array |
| created_at | REAL | NOT NULL | Unix timestamp |
| enabled | INTEGER | NOT NULL DEFAULT 1 | 1 = active |

```sql
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '["admin"]',
    created_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
```

---

## 32. Prompt Manager — `prompt_manager.py`

### prompt_templates

Prompt template metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | Template UUID |
| name | TEXT | NOT NULL | Template name |
| category | TEXT | DEFAULT 'general' | Category |
| tags | TEXT | DEFAULT '[]' | JSON tag array |
| current_version | TEXT | DEFAULT '1.0' | Active version |

```sql
CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    tags TEXT DEFAULT '[]',
    current_version TEXT DEFAULT '1.0'
);
```

### prompt_versions

Per-version prompt template content with variable definitions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| template_id | TEXT | NOT NULL | FK → prompt_templates.id |
| version | TEXT | NOT NULL | Version string |
| content | TEXT | NOT NULL | Template content |
| variables | TEXT | DEFAULT '{}' | JSON variable definitions |
| created | TEXT | NOT NULL | Creation timestamp |

> Composite PK: `(template_id, version)`  
> FK: `template_id → prompt_templates(id)`

```sql
CREATE TABLE IF NOT EXISTS prompt_versions (
    template_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    variables TEXT DEFAULT '{}',
    created TEXT NOT NULL,
    PRIMARY KEY (template_id, version),
    FOREIGN KEY (template_id) REFERENCES prompt_templates(id)
);
```

---

## Summary

| # | Subsystem | Source File | Tables |
|---|-----------|-------------|--------|
| 1 | Authentication | core/security/auth.py | api_keys |
| 2 | Core Data | core/backends/data.py | delegations, metrics, checkpoints, circuit_breaker, error_log |
| 3 | Session | core/security/session.py | sessions |
| 4 | Circuit Breaker | core/reliability/circuit_breaker.py | circuit_breaker_state, failover_chains, breaker_events |
| 5 | API Key Vault | core/security/api_key_vault.py | api_keys |
| 6 | Time Series | core/monitoring/timeseries.py | ts_raw, ts_5min, ts_1hour |
| 7 | KV Store | core/backends/kv_store.py | kv_store |
| 8 | Vector Search | core/memory/vector_store.py | vector_entries |
| 9 | Worktree | core/agent/memory_ctx/worktree.py | worktrees |
| 10 | Tool Manager | core/agent/tools/tool_manager.py | tools |
| 11 | Subagent | core/agent/delegation/subagent_db.py | subagents, agent_messages |
| 12 | Sandbox | core/security/sandbox.py | sandboxes |
| 13 | Protocol | core/agent/plugins_hooks/protocol.py | protocols, protocol_messages |
| 14 | Plugin | core/agent/plugins_hooks/plugin_manager.py | plugins |
| 15 | Permission | core/security/permission.py | permission_rules |
| 16 | Message Queue | core/reliability/message_queue.py | queue_messages, queue_dead_letters, queue_idempotent |
| 17 | MCP Registry | core/mcp/mcp_hub.py | mcp_servers |
| 18 | Knowledge Extractor | core/memory/knowledge_extractor.py | facts, entities, relations |
| 19 | Image Store | core/backends/image_store.py | images |
| 20 | Human Proxy | core/agent/delegation/human_proxy.py | approval_requests |
| 21 | Hook Manager | core/agent/plugins_hooks/hook_manager.py | hooks, hook_logs |
| 22 | Cost Tracker | core/cost_tracker.py | cost_entries |
| 23 | Conversation | core/agent/llm_chat/conversation.py | messages |
| 24 | Change Tracker | core/reliability/change_tracker.py | snapshots, file_states, change_log |
| 25 | Artifact Store | core/backends/artifact_store.py | artifacts, artifact_versions |
| 26 | Agent Scanner | core/agent/lifecycle/agent_scanner.py | scanned_agents |
| 27 | Agent Registry | core/agent/lifecycle/agent_registry.py | registered_agents, health_log |
| 28 | Migration | core/backends/migration.py | _migrations |
| 29 | Memory Manager | memory/manager.py | consolidation_log |
| 30 | Memory Models | memory/models.py | memory_entries, memory_traces, memory_trajectory, memory_fts |
| 31 | Dashboard Auth | dashboard/routers/auth.py | users |
| 32 | Prompt Manager | prompt_manager.py | prompt_templates, prompt_versions |

**Total: 101 tables（本文档已记录 53 张，其余 48 张待补全）**