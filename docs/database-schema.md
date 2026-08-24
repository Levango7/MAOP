# MAOP Database Schema

> Auto-generated from source code CREATE TABLE statements.
> ✅ 更新 2026-08-23：本文档已补全至 **105 张表**（过滤临时表 `_subagents_new` 与误识别项）。涵盖 enterprise / monitoring / evolution / tenant / mcp / marketplace / reliability 等全部模块。

---

## 1. Authentication — `core/auth.py`

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

## 2. Core Data — `core/data.py`

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

## 3. Session — `core/session.py`

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

## 4. Circuit Breaker — `core/circuit_breaker.py`

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

## 5. API Key Vault — `core/api_key_vault.py`

### api_keys

Encrypted LLM provider API keys (Fernet symmetric encryption).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| provider | TEXT | PRIMARY KEY | LLM provider name |
| encrypted_key | TEXT | NOT NULL | Fernet-encrypted API key |
| created_at | TEXT | NOT NULL | Creation timestamp |
| updated_at | TEXT | DEFAULT '' | Last update timestamp |

> **Note**: This table shares the name `api_keys` with `core/auth.py`. They are in separate SQLite databases.

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    provider TEXT PRIMARY KEY,
    encrypted_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT DEFAULT ''
);
```

---

## 6. Time Series — `core/timeseries.py`

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

## 7. KV Store — `core/kv_store.py`

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

## 8. Vector Search — `core/vector.py`

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

## 9. Worktree — `core/worktree.py`

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

## 10. Tool Manager — `core/tool_manager.py`

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

## 11. Subagent — `core/subagent.py`

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

## 12. Sandbox — `core/sandbox.py`

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

## 13. Protocol — `core/protocol.py`

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

## 14. Plugin — `core/plugin.py`

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

## 15. Permission — `core/permission.py`

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

## 16. Message Queue — `core/message_queue.py`

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

## 17. MCP Registry — `core/mcp_registry.py`

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

## 18. Knowledge Extractor — `core/knowledge_extractor.py`

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

## 19. Image Store — `core/image_store.py`

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

## 20. Human Proxy — `core/human_proxy.py`

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

## 21. Hook Manager — `core/hook_manager.py`

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

## 23. Conversation — `core/conversation.py`

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

## 24. Change Tracker — `core/change_tracker.py`

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

## 25. Artifact Store — `core/artifact_store.py`

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

## 26. Agent Scanner — `core/agent_scanner.py`

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

## 27. Agent Registry — `core/agent_registry.py`

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

## 28. Migration — `core/migration.py`

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

## 33. Schema Migrations — `data/migrations/001_init.sql`

### schema_migrations

版本追踪表，记录已应用的 schema 迁移版本。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| version | TEXT | PRIMARY KEY | 迁移版本号 |
| name | TEXT | NOT NULL | 迁移名称 |
| applied_at | TEXT | NOT NULL DEFAULT (datetime('now')) | 应用时间戳 |

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 34. A2A Delegation — `core/agent/delegation/a2a.py`

### a2a_cards

Agent-to-Agent 协议的 Agent Card 存储，记录对外暴露的 agent 能力描述。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | Agent card 名称 |
| card_json | TEXT | NOT NULL | Agent card JSON 序列化内容 |

```sql
CREATE TABLE IF NOT EXISTS a2a_cards (
    name TEXT PRIMARY KEY,
    card_json TEXT NOT NULL
);
```

### a2a_tasks

A2A 协议任务存储，记录跨 agent 派发的任务状态。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| task_id | TEXT | PRIMARY KEY | 任务 ID |
| task_json | TEXT | NOT NULL | 任务 JSON 序列化内容 |

```sql
CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id TEXT PRIMARY KEY,
    task_json TEXT NOT NULL
);
```

---

## 35. Subagent Transcripts — `core/agent/delegation/subagent_db.py`

### subagent_transcripts

Subagent 生命周期事件流（对话/状态变更记录），与 `subagents` / `agent_messages` 共存。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| agent_id | TEXT | NOT NULL | Subagent ID |
| timestamp | REAL | NOT NULL | 事件时间戳 |
| event | TEXT | DEFAULT '' | 事件类型 |
| data | TEXT | DEFAULT '{}' | 事件负载 JSON |

> 索引：`idx_st_agent ON subagent_transcripts(agent_id)`

```sql
CREATE TABLE IF NOT EXISTS subagent_transcripts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event TEXT DEFAULT '',
    data TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_st_agent ON subagent_transcripts(agent_id);
```

---

## 36. Agent Proxy — `core/agent/delegation/agent_proxy.py`

### agent_proxy_state

外部 agent 适配器（如 MCP/A2A 桥接）的运行状态与调用统计。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| adapter_name | TEXT | PRIMARY KEY | 适配器名称 |
| adapter_type | TEXT | DEFAULT '' | 适配器类型 |
| connected | INTEGER | DEFAULT 0 | 1 = 已连接，0 = 未连接 |
| config | TEXT | DEFAULT '{}' | 配置 JSON |
| last_call_at | REAL | DEFAULT 0.0 | 最近调用时间戳 |
| call_count | INTEGER | DEFAULT 0 | 累计调用次数 |
| error_count | INTEGER | DEFAULT 0 | 累计错误次数 |
| created_at | REAL | NOT NULL | 创建时间戳 |
| updated_at | REAL | DEFAULT 0.0 | 最近更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS agent_proxy_state (
    adapter_name TEXT PRIMARY KEY,
    adapter_type TEXT DEFAULT '',
    connected INTEGER DEFAULT 0,
    config TEXT DEFAULT '{}',
    last_call_at REAL DEFAULT 0.0,
    call_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL DEFAULT 0.0
);
```

---

## 37. AB Test — `core/evolution/ab_test.py`

### ab_experiments

A/B 实验元数据，记录变体集合与统计参数。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | 实验名称 |
| variants | TEXT | NOT NULL | JSON 变体数组 |
| min_samples | INTEGER | DEFAULT 30 | 最小样本数 |
| confidence_level | REAL | DEFAULT 0.95 | 置信水平 |
| created_at | REAL | NOT NULL | 创建时间戳 |

```sql
CREATE TABLE IF NOT EXISTS ab_experiments (
    name TEXT PRIMARY KEY,
    variants TEXT NOT NULL,
    min_samples INTEGER DEFAULT 30,
    confidence_level REAL DEFAULT 0.95,
    created_at REAL NOT NULL
);
```

### ab_assignments

实验变体分配记录，决定每个实体（agent/route）落入哪个变体。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| experiment | TEXT | NOT NULL | 实验名称 |
| entity_id | TEXT | NOT NULL | 实体 ID |
| variant | TEXT | NOT NULL | 分配到的变体 |
| assigned_at | REAL | NOT NULL | 分配时间戳 |

> 复合主键：`(experiment, entity_id)`

```sql
CREATE TABLE IF NOT EXISTS ab_assignments (
    experiment TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    assigned_at REAL NOT NULL,
    PRIMARY KEY (experiment, entity_id)
);
```

### ab_metrics

实验指标流水，记录每次尝试的成功/失败用于统计检验。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| experiment | TEXT | NOT NULL | 实验名称 |
| variant | TEXT | NOT NULL | 变体名称 |
| entity_id | TEXT | NOT NULL | 实体 ID |
| success | INTEGER | NOT NULL | 1 = 成功，0 = 失败 |
| recorded_at | REAL | NOT NULL | 记录时间戳 |

> 索引：`idx_ab_metrics_exp_var ON ab_metrics(experiment, variant)`

```sql
CREATE TABLE IF NOT EXISTS ab_metrics (
    experiment TEXT NOT NULL,
    variant TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ab_metrics_exp_var ON ab_metrics(experiment, variant);
```

---

## 38. Agent Memory — `core/agent/memory_ctx/agent_memory.py`

### agent_memory

Agent 长期记忆条目，按 agent + memory_type 分类存储。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| agent_name | TEXT | NOT NULL | Agent 名称 |
| memory_type | TEXT | NOT NULL | 记忆类型 |
| content | TEXT | NOT NULL | 记忆内容 |
| metadata | TEXT | DEFAULT '{}' | 元数据 JSON |
| importance | REAL | DEFAULT 0.5 | 重要性评分 |
| created_at | TEXT | NOT NULL | 创建时间戳 |
| expires_at | TEXT | DEFAULT '' | 过期时间戳 |

> 索引：`idx_memory_agent_type ON agent_memory(agent_name, memory_type)`、`idx_memory_created ON agent_memory(created_at DESC)`

```sql
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    importance REAL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    expires_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memory_agent_type ON agent_memory(agent_name, memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_created ON agent_memory(created_at DESC);
```

### agent_evolution_history

Agent 进化历史，记录每次自我改进的变更与结果。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| agent_name | TEXT | NOT NULL | Agent 名称 |
| evolution_type | TEXT | NOT NULL | 进化类型 |
| description | TEXT | NOT NULL | 变更描述 |
| changes | TEXT | NOT NULL | 变更内容 JSON |
| success | BOOLEAN | DEFAULT 0 | 1 = 成功，0 = 失败 |
| created_at | TEXT | NOT NULL | 创建时间戳 |

> 索引：`idx_evolution_agent ON agent_evolution_history(agent_name, created_at DESC)`

```sql
CREATE TABLE IF NOT EXISTS agent_evolution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    evolution_type TEXT NOT NULL,
    description TEXT NOT NULL,
    changes TEXT NOT NULL,
    success BOOLEAN DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evolution_agent ON agent_evolution_history(agent_name, created_at DESC);
```

---

## 39. Agent Performance — `core/agent/lifecycle/agent_performance.py`

### agent_performance

Agent 调用性能流水，记录每次路由的代价与延迟。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| agent | TEXT | NOT NULL | Agent 名称 |
| routing_key | TEXT | DEFAULT '' | 路由键 |
| outcome | TEXT | DEFAULT '' | 结果状态 |
| cost_usd | REAL | DEFAULT 0.0 | 调用成本（美元） |
| latency_ms | REAL | DEFAULT 0.0 | 延迟（毫秒） |
| created_at | REAL | NOT NULL | 创建时间戳 |

> 索引：`idx_ap_agent`、`idx_ap_rk`、`idx_ap_outcome`、`idx_ap_created`
>
> 注：`data/migrations/002_schema_sync.sql` 中存在同名表的替代定义（以 agent 为主键的聚合视图），运行时以本表为准。

```sql
CREATE TABLE IF NOT EXISTS agent_performance (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    routing_key TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0.0,
    latency_ms REAL DEFAULT 0.0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ap_agent ON agent_performance(agent);
CREATE INDEX IF NOT EXISTS idx_ap_rk ON agent_performance(routing_key);
CREATE INDEX IF NOT EXISTS idx_ap_outcome ON agent_performance(outcome);
CREATE INDEX IF NOT EXISTS idx_ap_created ON agent_performance(created_at DESC);
```

---

## 40. API Key Usage — `core/security/api_key_manager.py`

### api_key_usage

API key 调用流水，用于配额统计与异常检测。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| key_id | TEXT | NOT NULL | 关联 api_keys.key_hash |
| timestamp | REAL | NOT NULL | 调用时间戳 |
| endpoint | TEXT | NOT NULL DEFAULT '' | 调用端点 |
| method | TEXT | NOT NULL DEFAULT '' | HTTP 方法 |
| ip_address | TEXT | NOT NULL DEFAULT '' | 客户端 IP |
| status_code | INTEGER | NOT NULL DEFAULT 0 | HTTP 状态码 |
| latency_ms | REAL | NOT NULL DEFAULT 0 | 延迟（毫秒） |

> 索引：`idx_usage_key_ts ON api_key_usage(key_id, timestamp)`

```sql
CREATE TABLE IF NOT EXISTS api_key_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id      TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    endpoint    TEXT NOT NULL DEFAULT '',
    method      TEXT NOT NULL DEFAULT '',
    ip_address  TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL DEFAULT 0,
    latency_ms  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON api_key_usage(key_id, timestamp);
```

---

## 41. Budget Guard — `core/budget_guard.py`

### budget_daily

每日预算消耗计数器，按日期聚合 token 与成本。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| date | TEXT | PRIMARY KEY | 日期（YYYY-MM-DD） |
| tokens_used | INTEGER | DEFAULT 0 | 当日消耗 token 数 |
| cost_used | REAL | DEFAULT 0.0 | 当日消耗成本 |
| calls_count | INTEGER | DEFAULT 0 | 当日调用次数 |

```sql
CREATE TABLE IF NOT EXISTS budget_daily (
    date TEXT PRIMARY KEY,
    tokens_used INTEGER DEFAULT 0,
    cost_used REAL DEFAULT 0.0,
    calls_count INTEGER DEFAULT 0
);
```

### budget_config

预算配置键值对，存储每日上限、告警阈值等参数。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key | TEXT | PRIMARY KEY | 配置键 |
| value | TEXT | NOT NULL | 配置值 |

```sql
CREATE TABLE IF NOT EXISTS budget_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

---

## 42. Budget Ledger — `data/migrations/002_schema_sync.sql`

### budget_ledger

BudgetGuard SQLite 变体的逐条调用流水，用于审计与对账。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| tenant_id | TEXT | NOT NULL DEFAULT 'default' | 租户 ID |
| agent | TEXT | NOT NULL DEFAULT '' | Agent 名称 |
| estimated_cost | REAL | NOT NULL DEFAULT 0.0 | 预估成本 |
| actual_cost | REAL | NOT NULL DEFAULT 0.0 | 实际成本 |
| tokens_in | INTEGER | NOT NULL DEFAULT 0 | 输入 token |
| tokens_out | INTEGER | NOT NULL DEFAULT 0 | 输出 token |
| model | TEXT | NOT NULL DEFAULT '' | 模型名称 |
| trace_id | TEXT | NOT NULL DEFAULT '' | 分布式 trace ID |
| timestamp | TEXT | NOT NULL | 时间戳 |

> 索引：`idx_bl_tenant_ts ON budget_ledger(tenant_id, timestamp)`、`idx_bl_agent ON budget_ledger(agent)`

```sql
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
```

---

## 43. Config History — `core/config/config_history.py`

### config_snapshots

配置版本快照，支持配置回滚与审计。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| version | INTEGER | UNIQUE NOT NULL | 配置版本号 |
| snapshot_json | TEXT | NOT NULL | 配置 JSON 快照 |
| changed_by | TEXT | NOT NULL | 变更者 |
| changed_at | TEXT | NOT NULL | 变更时间戳 |

> 索引：`idx_config_snapshots_version ON config_snapshots(version DESC)`

```sql
CREATE TABLE IF NOT EXISTS config_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    version       INTEGER UNIQUE NOT NULL,
    snapshot_json TEXT    NOT NULL,
    changed_by    TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_version ON config_snapshots(version DESC);
```

---

## 44. Episodic Memory — `core/memory/episodic_store.py`

### episodic_memory

情景记忆条目，存储任务执行经验与教训。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记忆 ID |
| task | TEXT | NOT NULL | 任务描述 |
| agent | TEXT | DEFAULT '' | Agent 名称 |
| outcome | TEXT | DEFAULT '' | 结果状态 |
| score | REAL | DEFAULT 0.0 | 质量评分 |
| lessons | TEXT | DEFAULT '[]' | 教训 JSON 数组 |
| user_feedback | TEXT | DEFAULT '' | 用户反馈 |
| quality_dimensions | TEXT | DEFAULT '{}' | 质量维度 JSON |
| summary | TEXT | DEFAULT '' | 摘要 |
| key_decisions | TEXT | DEFAULT '[]' | 关键决策 JSON |
| files_touched | TEXT | DEFAULT '[]' | 涉及文件 JSON |
| metadata | TEXT | DEFAULT '{}' | 元数据 JSON |
| created_at | REAL | NOT NULL | 创建时间戳 |
| consolidated | INTEGER | DEFAULT 0 | 1 = 已合并 |
| access_count | INTEGER | DEFAULT 0 | 访问计数 |

> 索引：`idx_episodic_agent`、`idx_episodic_outcome`、`idx_episodic_score`、`idx_episodic_created`、`idx_episodic_consolidated`、`idx_episodic_access`
> FTS5 虚拟表：`episodic_memory_fts(task, agent, summary, user_feedback)`

```sql
CREATE TABLE IF NOT EXISTS episodic_memory (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    agent TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    score REAL DEFAULT 0.0,
    lessons TEXT DEFAULT '[]',
    user_feedback TEXT DEFAULT '',
    quality_dimensions TEXT DEFAULT '{}',
    summary TEXT DEFAULT '',
    key_decisions TEXT DEFAULT '[]',
    files_touched TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    consolidated INTEGER DEFAULT 0,
    access_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_episodic_agent ON episodic_memory(agent);
CREATE INDEX IF NOT EXISTS idx_episodic_outcome ON episodic_memory(outcome);
CREATE INDEX IF NOT EXISTS idx_episodic_score ON episodic_memory(score DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_consolidated ON episodic_memory(consolidated);
CREATE INDEX IF NOT EXISTS idx_episodic_access ON episodic_memory(access_count DESC);
```

---

## 45. Error Ledger — `core/reliability/error_ledger.py`

### error_ledger

错误账本，记录错误模式与根因分析结果，用于自愈规则生成。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| error_type | TEXT | NOT NULL | 错误类型 |
| context | TEXT | DEFAULT '' | 上下文 |
| trigger | TEXT | DEFAULT '{}' | 触发条件 JSON |
| output | TEXT | DEFAULT '' | 实际输出 |
| expected | TEXT | DEFAULT '' | 期望输出 |
| root_cause | TEXT | DEFAULT '' | 根因分析 |
| pattern | TEXT | DEFAULT '' | 错误模式 |
| rule | TEXT | DEFAULT '' | 自愈规则 |
| action | TEXT | DEFAULT '' | 修复动作 |
| recurrence | INTEGER | DEFAULT 1 | 复现次数 |
| created_at | REAL | NOT NULL | 创建时间戳 |

> 索引：`idx_el_type`、`idx_el_pattern`、`idx_el_created`

```sql
CREATE TABLE IF NOT EXISTS error_ledger (
    id TEXT PRIMARY KEY,
    error_type TEXT NOT NULL,
    context TEXT DEFAULT '',
    trigger TEXT DEFAULT '{}',
    output TEXT DEFAULT '',
    expected TEXT DEFAULT '',
    root_cause TEXT DEFAULT '',
    pattern TEXT DEFAULT '',
    rule TEXT DEFAULT '',
    action TEXT DEFAULT '',
    recurrence INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_el_type ON error_ledger(error_type);
CREATE INDEX IF NOT EXISTS idx_el_pattern ON error_ledger(pattern);
CREATE INDEX IF NOT EXISTS idx_el_created ON error_ledger(created_at DESC);
```

### promoted_rules

已晋升为正式规则的模式，从 error_ledger 中高频模式固化而来。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 规则 ID |
| pattern | TEXT | NOT NULL | 错误模式 |
| rule | TEXT | NOT NULL | 自愈规则 |
| count | INTEGER | DEFAULT 0 | 命中次数 |
| promoted_at | REAL | NOT NULL | 晋升时间戳 |

```sql
CREATE TABLE IF NOT EXISTS promoted_rules (
    id TEXT PRIMARY KEY,
    pattern TEXT NOT NULL,
    rule TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    promoted_at REAL NOT NULL
);
```

---

## 46. Evolution Loop — `core/evolution/evolution_loop.py`

### evolution_cycles

进化循环执行记录，跟踪每轮自进化的统计指标。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 循环 ID |
| started_at | REAL | NOT NULL | 开始时间戳 |
| finished_at | REAL | DEFAULT 0 | 结束时间戳 |
| total_duration_s | REAL | DEFAULT 0 | 总耗时（秒） |
| errors_observed | INTEGER | DEFAULT 0 | 观察到的错误数 |
| heal_attempts | INTEGER | DEFAULT 0 | 自愈尝试次数 |
| heal_successes | INTEGER | DEFAULT 0 | 自愈成功次数 |
| suggestions_generated | INTEGER | DEFAULT 0 | 生成建议数 |
| suggestions_applied | INTEGER | DEFAULT 0 | 应用建议数 |
| validation_improved | INTEGER | DEFAULT 0 | 验证改进数 |
| consolidated | INTEGER | DEFAULT 0 | 1 = 已合并 |
| report_json | TEXT | DEFAULT '{}' | 报告 JSON |

> 索引：`idx_evo_cycles_started ON evolution_cycles(started_at DESC)`

```sql
CREATE TABLE IF NOT EXISTS evolution_cycles (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    total_duration_s REAL DEFAULT 0,
    errors_observed INTEGER DEFAULT 0,
    heal_attempts INTEGER DEFAULT 0,
    heal_successes INTEGER DEFAULT 0,
    suggestions_generated INTEGER DEFAULT 0,
    suggestions_applied INTEGER DEFAULT 0,
    validation_improved INTEGER DEFAULT 0,
    consolidated INTEGER DEFAULT 0,
    report_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evo_cycles_started ON evolution_cycles(started_at DESC);
```

---

## 47. Evolution Deployments — `core/evolution/auto_deployer.py`

### evolution_deployments

进化部署记录，跟踪 promote/rollback 动作的结果。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 部署 ID |
| experiment | TEXT | NOT NULL | 关联实验名称 |
| action | TEXT | NOT NULL | 动作类型（promote / rollback） |
| winner | TEXT | DEFAULT '' | 胜出变体 |
| snapshot_id | TEXT | DEFAULT '' | 快照 ID |
| success | INTEGER | NOT NULL | 1 = 成功，0 = 失败 |
| detail | TEXT | DEFAULT '' | 详情 |
| config_json | TEXT | DEFAULT '{}' | 配置 JSON |
| created_at | REAL | NOT NULL | 创建时间戳 |

```sql
CREATE TABLE IF NOT EXISTS evolution_deployments (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    action TEXT NOT NULL,
    winner TEXT DEFAULT '',
    snapshot_id TEXT DEFAULT '',
    success INTEGER NOT NULL,
    detail TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
```

---

## 48. Evolution Perf Loop — `core/evolution/evolution_perf_loop.py`

### evolution_perf_cycles

性能进化循环记录，跟踪 SPRT 决策与晋升/回滚状态。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 循环 ID |
| experiment | TEXT | NOT NULL | 关联实验 |
| started_at | REAL | NOT NULL | 开始时间戳 |
| finished_at | REAL | DEFAULT 0 | 结束时间戳 |
| duration_s | REAL | DEFAULT 0 | 耗时（秒） |
| suggestions_count | INTEGER | DEFAULT 0 | 建议数 |
| sprt_decision | TEXT | DEFAULT 'continue' | SPRT 决策 |
| winner | TEXT | DEFAULT '' | 胜出变体 |
| promoted | INTEGER | DEFAULT 0 | 1 = 已晋升 |
| rolled_back | INTEGER | DEFAULT 0 | 1 = 已回滚 |
| pending_approval | INTEGER | DEFAULT 0 | 1 = 待审批 |
| detail | TEXT | DEFAULT '' | 详情 |
| error | TEXT | DEFAULT '' | 错误信息 |
| report_json | TEXT | DEFAULT '{}' | 报告 JSON |

```sql
CREATE TABLE IF NOT EXISTS evolution_perf_cycles (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    duration_s REAL DEFAULT 0,
    suggestions_count INTEGER DEFAULT 0,
    sprt_decision TEXT DEFAULT 'continue',
    winner TEXT DEFAULT '',
    promoted INTEGER DEFAULT 0,
    rolled_back INTEGER DEFAULT 0,
    pending_approval INTEGER DEFAULT 0,
    detail TEXT DEFAULT '',
    error TEXT DEFAULT '',
    report_json TEXT DEFAULT '{}'
);
```

---

## 49. GDPR Manager — `core/tenant/gdpr_manager.py`

### gdpr_dsr

GDPR 数据主体请求（DSR）记录，跟踪访问/删除/可携带性等请求处理。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| request_id | TEXT | PRIMARY KEY | 请求 ID |
| user_id | TEXT | NOT NULL | 用户 ID |
| tenant_id | TEXT | NOT NULL DEFAULT '' | 租户 ID |
| request_type | TEXT | NOT NULL | 请求类型 |
| status | TEXT | NOT NULL DEFAULT 'pending' | 处理状态 |
| created_at | TEXT | NOT NULL DEFAULT '' | 创建时间戳 |
| completed_at | TEXT | NOT NULL DEFAULT '' | 完成时间戳 |
| detail | TEXT | NOT NULL DEFAULT '{}' | 详情 JSON |
| result_summary | TEXT | NOT NULL DEFAULT '{}' | 结果摘要 JSON |
| due_at | TEXT | NOT NULL DEFAULT '' | 截止时间 |

> 索引：`idx_dsr_user ON gdpr_dsr(user_id)`、`idx_dsr_status ON gdpr_dsr(status)`

```sql
CREATE TABLE IF NOT EXISTS gdpr_dsr (
    request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    request_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    result_summary TEXT NOT NULL DEFAULT '{}',
    due_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dsr_user ON gdpr_dsr (user_id);
CREATE INDEX IF NOT EXISTS idx_dsr_status ON gdpr_dsr (status);
```

### gdpr_dpa

GDPR 数据处理协议（DPA）记录，存储控制者/处理者合约元数据。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| dpa_id | TEXT | PRIMARY KEY | DPA ID |
| controller_name | TEXT | NOT NULL | 控制者名称 |
| processor_name | TEXT | NOT NULL | 处理者名称 |
| tenant_id | TEXT | NOT NULL DEFAULT '' | 租户 ID |
| purpose | TEXT | NOT NULL DEFAULT '' | 处理目的 |
| data_categories | TEXT | NOT NULL DEFAULT '[]' | 数据类别 JSON |
| sub_processors | TEXT | NOT NULL DEFAULT '[]' | 子处理者 JSON |
| security_measures | TEXT | NOT NULL DEFAULT '[]' | 安全措施 JSON |
| effective_date | TEXT | NOT NULL DEFAULT '' | 生效日期 |
| termination_date | TEXT | NOT NULL DEFAULT '' | 终止日期 |
| status | TEXT | NOT NULL DEFAULT 'active' | 状态 |
| created_at | TEXT | NOT NULL DEFAULT '' | 创建时间戳 |
| updated_at | TEXT | NOT NULL DEFAULT '' | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS gdpr_dpa (
    dpa_id TEXT PRIMARY KEY,
    controller_name TEXT NOT NULL,
    processor_name TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    data_categories TEXT NOT NULL DEFAULT '[]',
    sub_processors TEXT NOT NULL DEFAULT '[]',
    security_measures TEXT NOT NULL DEFAULT '[]',
    effective_date TEXT NOT NULL DEFAULT '',
    termination_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
```

### gdpr_processing_records

GDPR 处理活动记录（ROPA），用于合规审计。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| record_id | TEXT | PRIMARY KEY | 记录 ID |
| tenant_id | TEXT | NOT NULL DEFAULT '' | 租户 ID |
| activity_name | TEXT | NOT NULL | 活动名称 |
| purpose | TEXT | NOT NULL DEFAULT '' | 处理目的 |
| data_categories | TEXT | NOT NULL DEFAULT '[]' | 数据类别 JSON |
| data_subject_categories | TEXT | NOT NULL DEFAULT '[]' | 数据主体类别 JSON |
| recipients | TEXT | NOT NULL DEFAULT '[]' | 接收者 JSON |
| cross_border_transfers | TEXT | NOT NULL DEFAULT '[]' | 跨境传输 JSON |
| retention_period_days | INTEGER | NOT NULL DEFAULT 0 | 保留期（天） |
| security_measures | TEXT | NOT NULL DEFAULT '[]' | 安全措施 JSON |
| legal_basis | TEXT | NOT NULL DEFAULT '' | 法律依据 |
| created_at | TEXT | NOT NULL DEFAULT '' | 创建时间戳 |
| updated_at | TEXT | NOT NULL DEFAULT '' | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS gdpr_processing_records (
    record_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT '',
    activity_name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    data_categories TEXT NOT NULL DEFAULT '[]',
    data_subject_categories TEXT NOT NULL DEFAULT '[]',
    recipients TEXT NOT NULL DEFAULT '[]',
    cross_border_transfers TEXT NOT NULL DEFAULT '[]',
    retention_period_days INTEGER NOT NULL DEFAULT 0,
    security_measures TEXT NOT NULL DEFAULT '[]',
    legal_basis TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
```

---

## 50. Self Heal — `core/reliability/self_heal.py`

### heal_rules

自愈规则定义，匹配错误模式并执行修复动作。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| name | TEXT | PRIMARY KEY | 规则名称 |
| condition | TEXT | NOT NULL | 匹配条件 |
| action | TEXT | NOT NULL | 修复动作 |
| verify | TEXT | DEFAULT '' | 验证表达式 |
| priority | INTEGER | DEFAULT 0 | 优先级 |
| enabled | INTEGER | DEFAULT 1 | 1 = 启用 |
| max_retries | INTEGER | DEFAULT 1 | 最大重试次数 |

```sql
CREATE TABLE IF NOT EXISTS heal_rules (
    name TEXT PRIMARY KEY,
    condition TEXT NOT NULL,
    action TEXT NOT NULL,
    verify TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    max_retries INTEGER DEFAULT 1
);
```

### heal_history

自愈执行历史，记录每次规则触发的结果。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| rule_name | TEXT | NOT NULL | 规则名称 |
| status | TEXT | NOT NULL | 执行状态 |
| message | TEXT | DEFAULT '' | 消息 |
| duration_s | REAL | DEFAULT 0.0 | 耗时（秒） |
| executed_at | REAL | NOT NULL | 执行时间戳 |

```sql
CREATE TABLE IF NOT EXISTS heal_history (
    id TEXT PRIMARY KEY,
    rule_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT DEFAULT '',
    duration_s REAL DEFAULT 0.0,
    executed_at REAL NOT NULL
);
```

---

## 51. Vector Search — `memory/vector_search.py`

### vectors

向量索引条目，存储 embedding 与文本哈希用于去重。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 向量 ID |
| embedding | BLOB | NOT NULL | 向量二进制 |
| text_hash | TEXT | NOT NULL | 文本哈希 |
| model_name | TEXT | NOT NULL | 模型名称 |
| indexed_at | TEXT | NOT NULL | 索引时间戳 |

> 索引：`idx_vectors_hash ON vectors(text_hash)`

```sql
CREATE TABLE IF NOT EXISTS vectors (
    id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    text_hash TEXT NOT NULL,
    model_name TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vectors_hash ON vectors(text_hash);
```

### index_log

向量索引操作日志，记录每次重建的统计信息。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| model_name | TEXT | NOT NULL | 模型名称 |
| entries_indexed | INTEGER | DEFAULT 0 | 索引条目数 |
| indexed_at | TEXT | NOT NULL | 索引时间戳 |

```sql
CREATE TABLE IF NOT EXISTS index_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    entries_indexed INTEGER DEFAULT 0,
    indexed_at TEXT NOT NULL
);
```

---

## 52. PostgreSQL Backends — `core/backends/backends_pg.py`

> 注：以下两张表为 PostgreSQL 专用（使用 `TIMESTAMPTZ` / `JSONB` 类型），由 `PgBackend` 在连接时创建。

### maop_kv

PostgreSQL 键值存储表，作为 SQLite `kv_store` 的 PG 后端。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key | TEXT | PRIMARY KEY | 键 |
| value | TEXT | NOT NULL | 值 |
| updated_at | TIMESTAMPTZ | DEFAULT now() | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS maop_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### maop_meta

PostgreSQL 元数据表，存储 JSONB 格式的平台元信息。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key | TEXT | PRIMARY KEY | 键 |
| value | JSONB | DEFAULT '{}' | 值（JSONB） |
| updated_at | TIMESTAMPTZ | DEFAULT now() | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS maop_meta (
    key TEXT PRIMARY KEY,
    value JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

---

## 53. Marketplace Key Management — `core/marketplace/key_management.py`

### marketplace_keys

插件市场签发密钥，用于工具调用的双向认证。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key_id | TEXT | PRIMARY KEY | 密钥 ID |
| tool_id | TEXT | NOT NULL | 工具 ID |
| public_key | TEXT | NOT NULL | 公钥 |
| created_at | TEXT | NOT NULL | 创建时间戳 |
| expires_at | TEXT | | 过期时间戳 |
| status | TEXT | NOT NULL DEFAULT 'active' | 状态 |

> 索引：`idx_mk_tool ON marketplace_keys(tool_id)`

```sql
CREATE TABLE IF NOT EXISTS marketplace_keys (
    key_id      TEXT PRIMARY KEY,
    tool_id     TEXT NOT NULL,
    public_key  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_mk_tool ON marketplace_keys(tool_id);
```

### marketplace_key_blacklist

已吊销的密钥黑名单，阻止过期/泄露密钥继续使用。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key_id | TEXT | NOT NULL | 密钥 ID |
| tool_id | TEXT | NOT NULL | 工具 ID |
| reason | TEXT | NOT NULL | 吊销原因 |
| blacklisted_at | TEXT | NOT NULL | 吊销时间戳 |

> 复合主键：`(key_id, tool_id)`  
> 索引：`idx_mkb_tool ON marketplace_key_blacklist(tool_id)`

```sql
CREATE TABLE IF NOT EXISTS marketplace_key_blacklist (
    key_id          TEXT NOT NULL,
    tool_id         TEXT NOT NULL,
    reason          TEXT NOT NULL,
    blacklisted_at  TEXT NOT NULL,
    PRIMARY KEY (key_id, tool_id)
);
CREATE INDEX IF NOT EXISTS idx_mkb_tool ON marketplace_key_blacklist(tool_id);
```

---

## 54. MCP Audit — `core/mcp/mcp_audit.py`

### mcp_audit

MCP 工具调用审计流水，记录权限决策与执行结果。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| timestamp | REAL | NOT NULL | 时间戳 |
| server_name | TEXT | NOT NULL | MCP 服务器名称 |
| tool_name | TEXT | NOT NULL | 工具名称 |
| user_id | TEXT | | 用户 ID |
| arguments_hash | TEXT | | 参数哈希 |
| allowed | INTEGER | NOT NULL | 1 = 允许，0 = 拒绝 |
| decision_reason | TEXT | | 决策原因 |
| success | INTEGER | NOT NULL | 1 = 成功，0 = 失败 |
| duration_ms | REAL | | 耗时（毫秒） |
| error | TEXT | | 错误信息 |

> 索引：`idx_mcp_audit_ts`、`idx_mcp_audit_server`、`idx_mcp_audit_user`、`idx_mcp_audit_allowed`

```sql
CREATE TABLE IF NOT EXISTS mcp_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    server_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    user_id TEXT,
    arguments_hash TEXT,
    allowed INTEGER NOT NULL,
    decision_reason TEXT,
    success INTEGER NOT NULL,
    duration_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_server ON mcp_audit(server_name);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_user ON mcp_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_allowed ON mcp_audit(allowed);
```

---

## 55. MCP Hub — `core/mcp/mcp_hub.py`

### mcp_tools

MCP 服务器暴露的工具清单。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 工具 ID |
| server_id | TEXT | NOT NULL | 服务器 ID |
| server_name | TEXT | NOT NULL | 服务器名称 |
| name | TEXT | NOT NULL | 工具名称 |
| description | TEXT | DEFAULT '' | 工具描述 |
| input_schema | TEXT | DEFAULT '{}' | 输入 schema JSON |

> 唯一约束：`UNIQUE(server_id, name)`

```sql
CREATE TABLE IF NOT EXISTS mcp_tools (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    server_name TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    input_schema TEXT DEFAULT '{}',
    UNIQUE(server_id, name)
);
```

### mcp_resources

MCP 服务器暴露的资源清单。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 资源 ID |
| server_id | TEXT | NOT NULL | 服务器 ID |
| server_name | TEXT | NOT NULL | 服务器名称 |
| uri | TEXT | NOT NULL | 资源 URI |
| name | TEXT | DEFAULT '' | 资源名称 |
| description | TEXT | DEFAULT '' | 资源描述 |
| mime_type | TEXT | DEFAULT '' | MIME 类型 |

> 唯一约束：`UNIQUE(server_id, uri)`

```sql
CREATE TABLE IF NOT EXISTS mcp_resources (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    server_name TEXT NOT NULL,
    uri TEXT NOT NULL,
    name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    mime_type TEXT DEFAULT '',
    UNIQUE(server_id, uri)
);
```

---

## 56. Pipeline Checkpoint — `core/reliability/pipeline_checkpoint.py`

### pipeline_runs

流水线运行实例，跟踪工作流执行状态。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| run_id | TEXT | PRIMARY KEY | 运行 ID |
| workflow_name | TEXT | NOT NULL | 工作流名称 |
| status | TEXT | DEFAULT 'running' | 运行状态 |
| variables | TEXT | DEFAULT '{}' | 变量 JSON |
| created_at | REAL | NOT NULL | 创建时间戳 |
| updated_at | REAL | DEFAULT 0.0 | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    variables TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL DEFAULT 0.0
);
```

### pipeline_step_checkpoints

流水线步骤级检查点，支持断点续跑。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| run_id | TEXT | NOT NULL | 运行 ID |
| step_name | TEXT | NOT NULL | 步骤名称 |
| status | TEXT | DEFAULT 'pending' | 步骤状态 |
| output | TEXT | DEFAULT '' | 输出 |
| started_at | REAL | DEFAULT 0.0 | 开始时间戳 |
| completed_at | REAL | DEFAULT 0.0 | 完成时间戳 |
| metadata | TEXT | DEFAULT '{}' | 元数据 JSON |
| attempts | INTEGER | DEFAULT 0 | 尝试次数 |
| error | TEXT | DEFAULT '' | 错误信息 |

> 复合主键：`(run_id, step_name)`  
> 索引：`idx_ckpt_run ON pipeline_step_checkpoints(run_id)`、`idx_ckpt_status ON pipeline_step_checkpoints(status)`

```sql
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
```

---

## 57. Routing Decision — `core/routing/routing_decision.py`

### routing_decisions

路由决策流水，记录每次路由选择的输入、输出与解释。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| trace_id | TEXT | NOT NULL | 分布式 trace ID |
| span_id | TEXT | NOT NULL | Span ID |
| parent_span_id | TEXT | | 父 Span ID |
| timestamp | REAL | NOT NULL | 时间戳 |
| stage | TEXT | NOT NULL | 路由阶段 |
| input_summary | TEXT | | 输入摘要 |
| output_summary | TEXT | | 输出摘要 |
| explanation | TEXT | | 决策解释 |
| duration_ms | REAL | | 耗时（毫秒） |
| attributes | TEXT | | 属性 JSON |

> 索引：`idx_rd_trace`、`idx_rd_ts`、`idx_rd_stage`

```sql
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    timestamp REAL NOT NULL,
    stage TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    explanation TEXT,
    duration_ms REAL,
    attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_rd_trace ON routing_decisions(trace_id);
CREATE INDEX IF NOT EXISTS idx_rd_ts ON routing_decisions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_rd_stage ON routing_decisions(stage);
```

---

## 58. Tenant Audit — `core/tenant/audit.py`

### tenant_audit_log

租户操作审计日志，支持哈希链式校验保证完整性。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增 ID |
| tenant_id | TEXT | NOT NULL | 租户 ID |
| timestamp | TEXT | NOT NULL | 时间戳 |
| action | TEXT | NOT NULL | 操作类型 |
| resource | TEXT | NOT NULL DEFAULT '' | 资源类型 |
| resource_id | TEXT | NOT NULL DEFAULT '' | 资源 ID |
| actor | TEXT | NOT NULL DEFAULT '' | 操作者 |
| result | TEXT | NOT NULL DEFAULT 'ok' | 结果 |
| detail | TEXT | NOT NULL DEFAULT '{}' | 详情 JSON |
| seq | INTEGER | NOT NULL DEFAULT 0 | 序列号 |
| prev_hash | TEXT | NOT NULL DEFAULT '' | 前一条哈希 |
| hash | TEXT | NOT NULL DEFAULT '' | 本条哈希 |

> 索引：`idx_audit_tenant_ts ON tenant_audit_log(tenant_id, timestamp)`

```sql
CREATE TABLE IF NOT EXISTS tenant_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT 'ok',
    detail TEXT NOT NULL DEFAULT '{}',
    seq INTEGER NOT NULL DEFAULT 0,
    prev_hash TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON tenant_audit_log (tenant_id, timestamp);
```

---

## 59. Tenant Hierarchy — `core/tenant/hierarchy.py`

### tenant_organizations

租户组织树节点，支持多级组织继承。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| org_id | TEXT | PRIMARY KEY | 组织 ID |
| name | TEXT | NOT NULL DEFAULT '' | 组织名称 |
| parent_id | TEXT | NOT NULL DEFAULT '' | 父组织 ID |
| tenant_id | TEXT | NOT NULL DEFAULT '' | 租户 ID |
| metadata | TEXT | NOT NULL DEFAULT '{}' | 元数据 JSON |
| block_inherit | INTEGER | NOT NULL DEFAULT 0 | 1 = 阻止权限继承 |
| created_at | TEXT | NOT NULL DEFAULT '' | 创建时间戳 |
| updated_at | TEXT | NOT NULL DEFAULT '' | 更新时间戳 |

> 索引：`idx_org_tenant ON tenant_organizations(tenant_id)`、`idx_org_parent ON tenant_organizations(parent_id)`

```sql
CREATE TABLE IF NOT EXISTS tenant_organizations (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    parent_id TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    block_inherit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_org_tenant ON tenant_organizations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_org_parent ON tenant_organizations (parent_id);
```

### tenant_org_closure

组织闭包表，预计算的祖先-后代关系，加速权限继承查询。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| ancestor | TEXT | NOT NULL | 祖先组织 ID |
| descendant | TEXT | NOT NULL | 后代组织 ID |
| distance | INTEGER | NOT NULL DEFAULT 0 | 距离（0 = 自引用） |

> 复合主键：`(ancestor, descendant)`

```sql
CREATE TABLE IF NOT EXISTS tenant_org_closure (
    ancestor TEXT NOT NULL,
    descendant TEXT NOT NULL,
    distance INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ancestor, descendant)
);
```

### tenant_org_permissions

组织级权限覆盖，支持显式 allow/deny 列表。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| org_id | TEXT | PRIMARY KEY | 组织 ID |
| permissions | TEXT | NOT NULL DEFAULT '[]' | 允许权限 JSON |
| denied | TEXT | NOT NULL DEFAULT '[]' | 拒绝权限 JSON |
| updated_at | TEXT | NOT NULL DEFAULT '' | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS tenant_org_permissions (
    org_id TEXT PRIMARY KEY,
    permissions TEXT NOT NULL DEFAULT '[]',
    denied TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT ''
);
```

---

## 60. Tenant Quota — `core/tenant/quota.py`

### tenant_resource_quota

租户资源配额定义，按资源 + 周期设置上限。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| tenant_id | TEXT | NOT NULL | 租户 ID |
| resource | TEXT | NOT NULL | 资源名称 |
| limit_val | INTEGER | NOT NULL DEFAULT 0 | 配额上限 |
| period | TEXT | NOT NULL DEFAULT 'total' | 周期 |

> 复合主键：`(tenant_id, resource)`

```sql
CREATE TABLE IF NOT EXISTS tenant_resource_quota (
    tenant_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    limit_val INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL DEFAULT 'total',
    PRIMARY KEY (tenant_id, resource)
);
```

### tenant_resource_usage

租户资源使用计数器，按周期键聚合。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| tenant_id | TEXT | NOT NULL | 租户 ID |
| resource | TEXT | NOT NULL | 资源名称 |
| period_key | TEXT | NOT NULL | 周期键 |
| used | INTEGER | NOT NULL DEFAULT 0 | 已用量 |

> 复合主键：`(tenant_id, resource, period_key)`

```sql
CREATE TABLE IF NOT EXISTS tenant_resource_usage (
    tenant_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    period_key TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, resource, period_key)
);
```

---

## 61. Tenant Manager — `core/tenant/manager.py`

### tenants

租户主表，存储租户配置与配额。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| tenant_id | TEXT | PRIMARY KEY | 租户 ID |
| display_name | TEXT | NOT NULL DEFAULT '' | 显示名称 |
| enabled | INTEGER | NOT NULL DEFAULT 1 | 1 = 启用 |
| quota_tokens | INTEGER | NOT NULL DEFAULT 0 | Token 配额 |
| quota_requests | INTEGER | NOT NULL DEFAULT 0 | 请求配额 |
| allowed_agents | TEXT | NOT NULL DEFAULT '[]' | 允许 agent JSON |
| allowed_models | TEXT | NOT NULL DEFAULT '[]' | 允许模型 JSON |
| metadata | TEXT | NOT NULL DEFAULT '{}' | 元数据 JSON |
| created_at | TEXT | NOT NULL DEFAULT '' | 创建时间戳 |
| updated_at | TEXT | NOT NULL DEFAULT '' | 更新时间戳 |

```sql
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    quota_tokens INTEGER NOT NULL DEFAULT 0,
    quota_requests INTEGER NOT NULL DEFAULT 0,
    allowed_agents TEXT NOT NULL DEFAULT '[]',
    allowed_models TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
```

### tenant_usage

租户每日使用量计数器。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| tenant_id | TEXT | NOT NULL | 租户 ID |
| date | TEXT | NOT NULL | 日期 |
| tokens_used | INTEGER | NOT NULL DEFAULT 0 | Token 用量 |
| requests_used | INTEGER | NOT NULL DEFAULT 0 | 请求用量 |

> 复合主键：`(tenant_id, date)`

```sql
CREATE TABLE IF NOT EXISTS tenant_usage (
    tenant_id TEXT NOT NULL,
    date TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    requests_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, date)
);
```

---

## 62. Tool Audit — `core/agent/tools/tool_audit.py`

### tool_audit_log

工具调用审计流水，记录每次工具调用的输入输出与耗时。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| tool_name | TEXT | NOT NULL | 工具名称 |
| agent | TEXT | DEFAULT '' | 调用 agent |
| inputs | TEXT | DEFAULT '{}' | 输入 JSON |
| output | TEXT | DEFAULT '' | 输出 |
| duration_ms | INTEGER | DEFAULT 0 | 耗时（毫秒） |
| success | INTEGER | DEFAULT 1 | 1 = 成功，0 = 失败 |
| error_message | TEXT | DEFAULT '' | 错误信息 |
| created_at | REAL | NOT NULL | 创建时间戳 |

> 索引：`idx_audit_tool`、`idx_audit_agent`、`idx_audit_success`、`idx_audit_created`

```sql
CREATE TABLE IF NOT EXISTS tool_audit_log (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    agent TEXT DEFAULT '',
    inputs TEXT DEFAULT '{}',
    output TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,
    error_message TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_audit_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON tool_audit_log(agent);
CREATE INDEX IF NOT EXISTS idx_audit_success ON tool_audit_log(success);
CREATE INDEX IF NOT EXISTS idx_audit_created ON tool_audit_log(created_at DESC);
```

---

## 63. Worktree (Memory Context) — `core/agent/memory_ctx/worktree.py`

### worktree_nodes

工作树节点，支持 agent 任务分解的树状结构。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 节点 ID |
| parent_id | TEXT | DEFAULT '' | 父节点 ID |
| root_id | TEXT | DEFAULT '' | 根节点 ID |
| name | TEXT | NOT NULL | 节点名称 |
| description | TEXT | DEFAULT '' | 描述 |
| status | TEXT | DEFAULT 'active' | 状态 |
| result | TEXT | DEFAULT '' | 结果 |
| metadata | TEXT | DEFAULT '{}' | 元数据 JSON |
| created_at | REAL | NOT NULL | 创建时间戳 |
| updated_at | REAL | NOT NULL | 更新时间戳 |

> 索引：`idx_wt_parent`、`idx_wt_root`、`idx_wt_status`

```sql
CREATE TABLE IF NOT EXISTS worktree_nodes (
    id TEXT PRIMARY KEY,
    parent_id TEXT DEFAULT '',
    root_id TEXT DEFAULT '',
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    result TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wt_parent ON worktree_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_wt_root ON worktree_nodes(root_id);
CREATE INDEX IF NOT EXISTS idx_wt_status ON worktree_nodes(status);
```

### worktree_checkpoints

工作树节点检查点，支持任务状态回滚。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 检查点 ID |
| node_id | TEXT | NOT NULL | 节点 ID |
| label | TEXT | NOT NULL | 标签 |
| snapshot | TEXT | DEFAULT '{}' | 快照 JSON |
| created_at | REAL | NOT NULL | 创建时间戳 |

> 索引：`idx_cp_node ON worktree_checkpoints(node_id)`

```sql
CREATE TABLE IF NOT EXISTS worktree_checkpoints (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    label TEXT NOT NULL,
    snapshot TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cp_node ON worktree_checkpoints(node_id);
```

---

## 64. JWT Revoked — `data/migrations/002_schema_sync.sql`

### jwt_revoked

已吊销的 JWT 黑名单，支持持久化 token 失效。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| jti | TEXT | PRIMARY KEY | JWT ID |
| expires_at | REAL | NOT NULL | JWT 过期时间戳 |
| revoked_at | REAL | NOT NULL | 吊销时间戳 |

```sql
CREATE TABLE IF NOT EXISTS jwt_revoked (
  jti TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  revoked_at REAL NOT NULL
);
```

---

## 65. Evolve History — `data/migrations/002_schema_sync.sql`

### evolve_history

进化动作历史，记录每次建议应用的结果。

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | TEXT | PRIMARY KEY | 记录 ID |
| action | TEXT | NOT NULL | 动作类型 |
| agent | TEXT | NOT NULL DEFAULT '' | Agent 名称 |
| suggestion_type | TEXT | NOT NULL DEFAULT '' | 建议类型 |
| details | TEXT | NOT NULL DEFAULT '{}' | 详情 JSON |
| applied_at | TEXT | NOT NULL | 应用时间戳 |
| success | INTEGER | NOT NULL DEFAULT 1 | 1 = 成功，0 = 失败 |

> 索引：`idx_eh_agent ON evolve_history(agent)`、`idx_eh_applied_at ON evolve_history(applied_at)`

```sql
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
```

---

## Summary

| # | Subsystem | Source File | Tables |
|---|-----------|-------------|--------|
| 1 | Authentication | core/auth.py | api_keys |
| 2 | Core Data | core/data.py | delegations, metrics, checkpoints, circuit_breaker, error_log |
| 3 | Session | core/session.py | sessions |
| 4 | Circuit Breaker | core/circuit_breaker.py | circuit_breaker_state, failover_chains, breaker_events |
| 5 | API Key Vault | core/api_key_vault.py | api_keys |
| 6 | Time Series | core/timeseries.py | ts_raw, ts_5min, ts_1hour |
| 7 | KV Store | core/kv_store.py | kv_store |
| 8 | Vector Search | core/vector.py | vector_entries |
| 9 | Worktree | core/worktree.py | worktrees |
| 10 | Tool Manager | core/tool_manager.py | tools |
| 11 | Subagent | core/subagent.py | subagents, agent_messages |
| 12 | Sandbox | core/sandbox.py | sandboxes |
| 13 | Protocol | core/protocol.py | protocols, protocol_messages |
| 14 | Plugin | core/plugin.py | plugins |
| 15 | Permission | core/permission.py | permission_rules |
| 16 | Message Queue | core/message_queue.py | queue_messages, queue_dead_letters, queue_idempotent |
| 17 | MCP Registry | core/mcp_registry.py | mcp_servers |
| 18 | Knowledge Extractor | core/knowledge_extractor.py | facts, entities, relations |
| 19 | Image Store | core/image_store.py | images |
| 20 | Human Proxy | core/human_proxy.py | approval_requests |
| 21 | Hook Manager | core/hook_manager.py | hooks, hook_logs |
| 22 | Cost Tracker | core/cost_tracker.py | cost_entries |
| 23 | Conversation | core/conversation.py | messages |
| 24 | Change Tracker | core/change_tracker.py | snapshots, file_states, change_log |
| 25 | Artifact Store | core/artifact_store.py | artifacts, artifact_versions |
| 26 | Agent Scanner | core/agent_scanner.py | scanned_agents |
| 27 | Agent Registry | core/agent_registry.py | registered_agents, health_log |
| 28 | Migration | core/migration.py | _migrations |
| 29 | Memory Manager | memory/manager.py | consolidation_log |
| 30 | Memory Models | memory/models.py | memory_entries, memory_traces, memory_trajectory, memory_fts |
| 31 | Dashboard Auth | dashboard/routers/auth.py | users |
| 32 | Prompt Manager | prompt_manager.py | prompt_templates, prompt_versions |
| 33 | Schema Migrations | data/migrations/001_init.sql | schema_migrations |
| 34 | A2A Delegation | core/agent/delegation/a2a.py | a2a_cards, a2a_tasks |
| 35 | Subagent Transcripts | core/agent/delegation/subagent_db.py | subagent_transcripts |
| 36 | Agent Proxy | core/agent/delegation/agent_proxy.py | agent_proxy_state |
| 37 | AB Test | core/evolution/ab_test.py | ab_experiments, ab_assignments, ab_metrics |
| 38 | Agent Memory | core/agent/memory_ctx/agent_memory.py | agent_memory, agent_evolution_history |
| 39 | Agent Performance | core/agent/lifecycle/agent_performance.py | agent_performance |
| 40 | API Key Usage | core/security/api_key_manager.py | api_key_usage |
| 41 | Budget Guard | core/budget_guard.py | budget_daily, budget_config |
| 42 | Budget Ledger | data/migrations/002_schema_sync.sql | budget_ledger |
| 43 | Config History | core/config/config_history.py | config_snapshots |
| 44 | Episodic Memory | core/memory/episodic_store.py | episodic_memory |
| 45 | Error Ledger | core/reliability/error_ledger.py | error_ledger, promoted_rules |
| 46 | Evolution Loop | core/evolution/evolution_loop.py | evolution_cycles |
| 47 | Evolution Deployments | core/evolution/auto_deployer.py | evolution_deployments |
| 48 | Evolution Perf Loop | core/evolution/evolution_perf_loop.py | evolution_perf_cycles |
| 49 | GDPR Manager | core/tenant/gdpr_manager.py | gdpr_dsr, gdpr_dpa, gdpr_processing_records |
| 50 | Self Heal | core/reliability/self_heal.py | heal_rules, heal_history |
| 51 | Vector Search (memory) | memory/vector_search.py | vectors, index_log |
| 52 | PostgreSQL Backends | core/backends/backends_pg.py | maop_kv, maop_meta |
| 53 | Marketplace Key Mgmt | core/marketplace/key_management.py | marketplace_keys, marketplace_key_blacklist |
| 54 | MCP Audit | core/mcp/mcp_audit.py | mcp_audit |
| 55 | MCP Hub | core/mcp/mcp_hub.py | mcp_tools, mcp_resources |
| 56 | Pipeline Checkpoint | core/reliability/pipeline_checkpoint.py | pipeline_runs, pipeline_step_checkpoints |
| 57 | Routing Decision | core/routing/routing_decision.py | routing_decisions |
| 58 | Tenant Audit | core/tenant/audit.py | tenant_audit_log |
| 59 | Tenant Hierarchy | core/tenant/hierarchy.py | tenant_organizations, tenant_org_closure, tenant_org_permissions |
| 60 | Tenant Quota | core/tenant/quota.py | tenant_resource_quota, tenant_resource_usage |
| 61 | Tenant Manager | core/tenant/manager.py | tenants, tenant_usage |
| 62 | Tool Audit | core/agent/tools/tool_audit.py | tool_audit_log |
| 63 | Worktree (Memory Ctx) | core/agent/memory_ctx/worktree.py | worktree_nodes, worktree_checkpoints |
| 64 | JWT Revoked | data/migrations/002_schema_sync.sql | jwt_revoked |
| 65 | Evolve History | data/migrations/002_schema_sync.sql | evolve_history |

**Total: 105 tables（本文档已全部记录，覆盖所有运行时 CREATE TABLE 语句；过滤临时表 `_subagents_new` 与误识别项）**