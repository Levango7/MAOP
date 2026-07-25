"""Subagent 共享数据库访问层。

统一 ``subagent_delegation.SubagentManager`` 和 ``subagent_lifecycle.SubAgentManager``
的 DB schema 与文件路径，消除双 DB 双 schema 问题。

背景
----
两套实现原本各自维护 ``subagents`` 表的 CREATE TABLE 语句，字段集合不同：

- ``SubagentManager`` (delegation): id / parent_agent / child_agent / task / status /
  created_at / finished_at / exit_code / depth + 独立的 ``agent_messages`` 表
- ``SubAgentManager`` (lifecycle): id / name / role / model / task / context / status /
  output / tool_calls / tokens_used / duration_ms / error / config / created_at /
  started_at / finished_at + 独立的 ``subagent_transcripts`` 表

在 unified DB 模式（默认）下两个 manager 写同一个 ``maop.db``，谁先初始化
就用谁的 schema，第二个 manager 的 INSERT 会因缺列直接失败。

解决方案
--------
两套实现共享同一个 DB 文件，``subagents`` 表为两套字段的超集；表名不冲突的
``agent_messages`` / ``subagent_transcripts`` 共存。``migrate_legacy_subagent_db``
负责旧表升级：

1. 旧表不存在 → 直接创建完整 schema。
2. 旧表存在但缺列 → ``ALTER TABLE ADD COLUMN`` 补齐。
3. 旧表的 NOT NULL 约束与新 schema 冲突（如旧 lifecycle 的 ``name NOT NULL``，
   或旧 delegation 的 ``parent_agent/child_agent NOT NULL``）→ 重建表以放宽
   约束，保留旧数据。
4. 索引和附属表幂等创建。

公共字段 ``task`` / ``status`` / ``created_at`` / ``finished_at`` 两套都用，
但写入格式不同（delegation 用 ISO 字符串，lifecycle 用 ``time.time()`` 浮点数）。
SQLite 列声明为 TEXT 即可同时容纳两种格式 —— 同一行不会被两个 manager 同时写入。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from maop.core.db_utils import get_db_path

# 统一 DB 模块名。
# - unified 模式（默认）：映射到 maop.db
# - per-module 模式 (MAOP_DB_PER_MODULE=1)：映射到 subagent.db
SUBAGENT_DB_NAME = "subagent"

# 统一 schema：两套实现的字段超集。
# 注意：parent_agent / child_agent / name 在新 schema 中都是 DEFAULT ''（允许 NULL），
# 因为两套 manager 各自只写自己关心的列，另一套的列必须允许 NULL/默认值。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS subagents (
    id TEXT PRIMARY KEY,
    -- 公共字段（两套实现都用）
    task TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',

    -- subagent_delegation.SubagentManager 特有字段
    parent_agent TEXT DEFAULT '',
    child_agent TEXT DEFAULT '',
    exit_code INTEGER,
    depth INTEGER DEFAULT 0,
    message TEXT,  -- 预留：send/receive 消息内容快照（实际消息走 agent_messages 表）

    -- subagent_lifecycle.SubAgentManager 特有字段
    name TEXT DEFAULT '',
    role TEXT DEFAULT 'leaf',
    model TEXT DEFAULT '',
    context TEXT DEFAULT '{}',
    output TEXT DEFAULT '',
    tool_calls TEXT DEFAULT '[]',
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    started_at REAL DEFAULT 0,
    transcript TEXT,  -- 预留：对话记录 JSON 快照（实际记录走 subagent_transcripts 表）
    result TEXT,      -- 预留：执行结果快照（实际结果走 output 字段）

    -- 元数据
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagents(parent_agent);
CREATE INDEX IF NOT EXISTS idx_subagents_status ON subagents(status);
CREATE INDEX IF NOT EXISTS idx_subagents_name ON subagents(name);

-- subagent_delegation 的消息传递表（表名与 lifecycle 不冲突，共存）
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    msg_type TEXT DEFAULT 'info',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON agent_messages(recipient, created_at);

-- subagent_lifecycle 的对话记录表（表名与 delegation 不冲突，共存）
CREATE TABLE IF NOT EXISTS subagent_transcripts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event TEXT DEFAULT '',
    data TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_st_agent ON subagent_transcripts(agent_id);
"""

# 索引 + 附属表 SQL（迁移旧表后用于补齐）
_ANCILLARY_SQL = """
CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagents(parent_agent);
CREATE INDEX IF NOT EXISTS idx_subagents_status ON subagents(status);
CREATE INDEX IF NOT EXISTS idx_subagents_name ON subagents(name);
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    msg_type TEXT DEFAULT 'info',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON agent_messages(recipient, created_at);
CREATE TABLE IF NOT EXISTS subagent_transcripts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event TEXT DEFAULT '',
    data TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_st_agent ON subagent_transcripts(agent_id);
"""

# 所有必需列：列名 → ADD COLUMN 子句用的类型定义。
# 用于迁移旧表：缺哪列就 ALTER TABLE ADD COLUMN 哪列。
# 注意：ADD COLUMN 不支持 PRIMARY KEY / NOT NULL 无默认值，因此 id 跳过迁移。
REQUIRED_COLUMNS: dict[str, str] = {
    "id": "TEXT PRIMARY KEY",
    "task": "TEXT DEFAULT ''",
    "status": "TEXT DEFAULT 'pending'",
    "created_at": "TEXT NOT NULL",
    "finished_at": "TEXT DEFAULT ''",
    "updated_at": "TEXT DEFAULT ''",
    "parent_agent": "TEXT DEFAULT ''",
    "child_agent": "TEXT DEFAULT ''",
    "exit_code": "INTEGER",
    "depth": "INTEGER DEFAULT 0",
    "message": "TEXT",
    "name": "TEXT DEFAULT ''",
    "role": "TEXT DEFAULT 'leaf'",
    "model": "TEXT DEFAULT ''",
    "context": "TEXT DEFAULT '{}'",
    "output": "TEXT DEFAULT ''",
    "tool_calls": "TEXT DEFAULT '[]'",
    "tokens_used": "INTEGER DEFAULT 0",
    "duration_ms": "INTEGER DEFAULT 0",
    "error": "TEXT DEFAULT ''",
    "config": "TEXT DEFAULT '{}'",
    "started_at": "REAL DEFAULT 0",
    "transcript": "TEXT",
    "result": "TEXT",
    "metadata": "TEXT DEFAULT '{}'",
}

# 迁移时跳过的列（PRIMARY KEY 列无法通过 ADD COLUMN 添加）。
_NON_MIGRATABLE_COLUMNS = {"id"}

# 新 schema 中允许 NULL/有默认值，但旧 schema 中可能被声明为 NOT NULL 的列。
# 这些列的 NOT NULL 约束会阻止另一套 manager 的 INSERT（不传该列），必须通过
# 重建表来放宽约束。
_NULLABLE_IN_NEW_SCHEMA = {"name", "parent_agent", "child_agent"}


def get_subagent_db_path() -> Path:
    """返回统一的 subagent DB 路径。

    - unified 模式（默认）：``<data_dir>/maop.db``
    - per-module 模式：``<data_dir>/subagent.db``
    """
    return get_db_path(SUBAGENT_DB_NAME)


def init_subagent_db(conn: sqlite3.Connection) -> None:
    """在已存在的连接上初始化 subagent 表结构（幂等）。

    执行所有 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 语句。
    不会修改已存在的表 schema —— 若需升级旧表请使用 :func:`migrate_legacy_subagent_db`。
    """
    conn.executescript(SCHEMA_SQL)


def _has_not_null_conflict(cols_info: list[sqlite3.Row]) -> bool:
    """检查旧表的 NOT NULL 约束是否与新 schema 冲突。

    冲突场景：
    - 旧 lifecycle schema: ``name TEXT NOT NULL``（SubagentManager.spawn 不传 name 会失败）
    - 旧 delegation schema: ``parent_agent/child_agent TEXT NOT NULL``
      （SubAgentManager.spawn 不传 parent_agent/child_agent 会失败）
    """
    for row in cols_info:
        if row["notnull"] and row["name"] in _NULLABLE_IN_NEW_SCHEMA:
            return True
    return False


def _rebuild_subagents_table(
    conn: sqlite3.Connection,
    old_cols_info: list[sqlite3.Row],
) -> None:
    """重建 subagents 表以放宽 NOT NULL 约束，保留旧数据。

    步骤：
    1. 创建 ``_subagents_new`` 表（完整新 schema，所有列允许 NULL 或有默认值）。
    2. 从旧表复制数据（仅复制两表都有的列；新表其他列用默认值）。
    3. 删除旧表。
    4. 重命名 ``_subagents_new`` 为 ``subagents``。
    5. 重建索引。

    ``created_at`` 在新 schema 中是 NOT NULL，必须存在于旧表（两套旧 schema 都有此列）。
    """
    old_col_names: set[str] = {row["name"] for row in old_cols_info}
    new_col_names: set[str] = set(REQUIRED_COLUMNS.keys())
    # 两表都有的列（用于 INSERT SELECT 复制）
    common_cols = old_col_names & new_col_names
    # created_at 是新表的 NOT NULL 列，必须复制；若旧表意外缺失则用当前时间兜底
    if "created_at" not in common_cols:
        common_cols.add("created_at")

    # 1. 创建临时新表（schema 与 subagents 完全一致，仅表名不同）
    conn.execute("DROP TABLE IF EXISTS _subagents_new")
    conn.execute("""
        CREATE TABLE _subagents_new (
            id TEXT PRIMARY KEY,
            task TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            finished_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            parent_agent TEXT DEFAULT '',
            child_agent TEXT DEFAULT '',
            exit_code INTEGER,
            depth INTEGER DEFAULT 0,
            message TEXT,
            name TEXT DEFAULT '',
            role TEXT DEFAULT 'leaf',
            model TEXT DEFAULT '',
            context TEXT DEFAULT '{}',
            output TEXT DEFAULT '',
            tool_calls TEXT DEFAULT '[]',
            tokens_used INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            config TEXT DEFAULT '{}',
            started_at REAL DEFAULT 0,
            transcript TEXT,
            result TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """)

    # 2. 复制数据（仅公共列，缺列让新表用 DEFAULT 值）
    col_list = ", ".join(sorted(common_cols))
    if col_list:
        conn.execute(
            f"INSERT INTO _subagents_new ({col_list}) SELECT {col_list} FROM subagents"
        )

    # 3. 删除旧表
    conn.execute("DROP TABLE subagents")

    # 4. 重命名新表
    conn.execute("ALTER TABLE _subagents_new RENAME TO subagents")

    # 5. 重建索引（DROP TABLE 会删掉旧索引）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagents(parent_agent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_subagents_status ON subagents(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_subagents_name ON subagents(name)")


def migrate_legacy_subagent_db() -> None:
    """迁移旧的 subagent DB 到统一 schema。

    行为：
    - 如果 ``subagents`` 表不存在 → 直接创建完整 schema（含索引和附属表）。
    - 如果表已存在但缺列（来自旧的 delegation-only 或 lifecycle-only schema）
      → 通过 ``ALTER TABLE ADD COLUMN`` 补齐所有缺失列。
    - 如果旧表的 NOT NULL 约束与新 schema 冲突（如旧 lifecycle 的 ``name NOT NULL``
      或旧 delegation 的 ``parent_agent/child_agent NOT NULL``）→ 重建表以放宽约束，
      保留旧数据。
    - 索引和 ``agent_messages`` / ``subagent_transcripts`` 附属表幂等创建。

    使用裸 ``sqlite3.connect`` 而非 ``sqlite_connect``，因为此函数在
    ``__init__`` 阶段执行一次，无需 WAL；后续业务访问通过 ``sqlite_connect``
    会自动启用 WAL 模式。
    """
    db_path = get_subagent_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # 检查 subagents 表是否存在
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='subagents'"
        ).fetchone()
        if not table_exists:
            # 全新数据库：直接创建完整 schema
            init_subagent_db(conn)
            return

        # 旧表存在：检查列信息和约束
        cols_info: list[sqlite3.Row] = list(
            conn.execute("PRAGMA table_info(subagents)")
        )

        # 优先检查 NOT NULL 约束冲突（需要重建表）
        if _has_not_null_conflict(cols_info):
            _rebuild_subagents_table(conn, cols_info)
        else:
            # 仅补齐缺失列（ALTER TABLE ADD COLUMN）
            existing_cols: set[str] = {row["name"] for row in cols_info}
            for col, col_def in REQUIRED_COLUMNS.items():
                if col in existing_cols or col in _NON_MIGRATABLE_COLUMNS:
                    continue
                # ALTER TABLE ADD COLUMN 接受简单类型 + DEFAULT，不接受 PRIMARY KEY/NOT NULL
                # col_def 形如 "TEXT DEFAULT ''" 已经是合法的 ADD COLUMN 子句
                conn.execute(f"ALTER TABLE subagents ADD COLUMN {col} {col_def}")

        # 确保索引和附属表也存在（旧库可能只有 subagents 表）
        conn.executescript(_ANCILLARY_SQL)
