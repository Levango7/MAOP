"""MAOP Memory Models — Pydantic models, helpers, and DDL for the memory store.

Extracted from store.py for single-responsibility separation.
All symbols are re-exported from store.py for backward compatibility.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _new_id() -> str:
    """Generate a memory entry ID: YYYYMMDD-HHmmss-<rand6>."""
    import random
    import string
    rand = "".join(random.choices(string.ascii_letters, k=6))
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{rand}"


# ── Models ────────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    """A single memory entry."""
    id: str = Field(default_factory=lambda: _new_id())
    agent: str = ""
    task: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    topic: str = "general"
    trace_id: str = ""
    session_id: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class TraceEntry(BaseModel):
    """A session trace for correlation."""
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_trace_id: str = ""
    session_id: str = ""
    task: str = ""
    agents: list[str] = Field(default_factory=list)
    created: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "active"


class TrajectoryStep(BaseModel):
    """A single step in an agent's execution trajectory."""
    id: str = Field(default_factory=lambda: _new_id())
    trace_id: str = ""
    agent: str = ""
    task: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_output: str = ""
    duration_ms: int = 0
    exit_code: int = 0
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SearchResult(BaseModel):
    """A scored search result with optional highlighted snippet."""
    id: str
    agent: str
    task: str
    tags: str = ""
    topic: str = ""
    trace_id: str = ""
    timestamp: str = ""
    score: float = 0.0
    snippet: str = ""
    highlighted: str = ""  # FTS5 snippet with <b> markup


class FacetResult(BaseModel):
    """Aggregation result for a facet (e.g. topic distribution)."""
    facet: str
    value: str
    count: int


class MemoryStats(BaseModel):
    """Memory store statistics."""
    total_entries: int = 0
    total_traces: int = 0
    total_trajectory_steps: int = 0
    by_agent: dict[str, int] = Field(default_factory=dict)
    by_topic: dict[str, int] = Field(default_factory=dict)
    oldest: str = ""
    newest: str = ""


# ── Synonym map (mirrors memory.ps1) ─────────────────────────

SYNONYM_MAP: dict[str, list[str]] = {
    "登录": ["login", "signin", "认证", "auth"],
    "超时": ["timeout", "超时", "hang", "卡住"],
    "错误": ["error", "异常", "exception", "bug", "故障"],
    "慢": ["slow", "延迟", "latency", "性能", "performance"],
    "配置": ["config", "设置", "setting", "setup"],
    "安装": ["install", "setup", "部署", "deploy"],
    "更新": ["update", "upgrade", "升级", "版本"],
    "删除": ["delete", "remove", "清理", "clean"],
    "搜索": ["search", "查找", "find", "query", "查询"],
    "认证": ["auth", "login", "token", "凭据", "credential", "keyring"],
}


def expand_keywords(text: str) -> list[str]:
    """Expand query text with synonyms for better search hit rate."""
    results = [text]
    for key, synonyms in SYNONYM_MAP.items():
        if key in text:
            results.extend(synonyms)
        for syn in synonyms:
            if syn in text:
                results.append(key)
                break
    return list(dict.fromkeys(results))  # deduplicate preserving order


# ── ID validation ─────────────────────────────────────────────

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_id(entry_id: str) -> bool:
    """Reject IDs containing path-traversal characters."""
    return bool(_ID_RE.match(entry_id))


# ── SQLite DDL ────────────────────────────────────────────────

_MEMORY_DDL = """
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
"""

# FTS5 virtual table for full-text search (replaces regex O(N) scan)
_FTS5_DDL = """
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

-- Triggers to keep FTS5 in sync with memory_entries
CREATE TRIGGER IF NOT EXISTS mem_fts_insert AFTER INSERT ON memory_entries BEGIN
  INSERT INTO memory_fts(rowid, id, agent, task, content, tags, topic)
  VALUES (new.rowid, new.id, new.agent, new.task, new.content, new.tags, new.topic);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_delete AFTER DELETE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, id, agent, task, content, tags, topic)
  VALUES ('delete', old.rowid, old.id, old.agent, old.task, old.content, old.tags, old.topic);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_update AFTER UPDATE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, id, agent, task, content, tags, topic)
  VALUES ('delete', old.rowid, old.id, old.agent, old.task, old.content, old.tags, old.topic);
  INSERT INTO memory_fts(rowid, id, agent, task, content, tags, topic)
  VALUES (new.rowid, new.id, new.agent, new.task, new.content, new.tags, new.topic);
END;
"""

# JSON1 extension queries for semi-structured data (P2-2)
_JSON1_DDL = """
-- JSON1 is a compile-time extension in Python's sqlite3 module.
-- No DDL needed; just use json_extract() in queries.
-- Example: SELECT * FROM memory_entries WHERE json_extract(content, '$.key') = 'val'
"""
