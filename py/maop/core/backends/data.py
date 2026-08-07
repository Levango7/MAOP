"""MAOP Data Layer — SQLite-backed persistence for delegations, metrics,
checkpoints, error_log, and circuit_breaker.

SQLite-backed data access layer. with Python sqlite3 (stdlib) + aiosqlite for async.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect, validate_identifier

logger = logging.getLogger(__name__)


# ── Schema DDL ────────────────────────────────────────────────

SCHEMA_DDL = """
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

CREATE TABLE IF NOT EXISTS circuit_breaker (
  agent TEXT PRIMARY KEY,
  state TEXT DEFAULT 'closed',
  failures INT DEFAULT 0,
  threshold INT DEFAULT 3,
  last_failure TEXT,
  cooldown_s INT DEFAULT 60,
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
"""


# ── Pydantic result models ────────────────────────────────────

class DelegationRecord(BaseModel):
    id: int | None = None
    timestamp: str = ""
    agent: str = ""
    task: str = ""
    routing_key: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    trace_id: str = ""


class CheckpointRecord(BaseModel):
    id: str = ""
    agent: str = ""
    task: str = ""
    phase: str = ""
    state_json: str = ""
    created: str = ""
    updated: str = ""


class ErrorLogRecord(BaseModel):
    id: int | None = None
    timestamp: str = ""
    agent: str = ""
    task: str = ""
    exit_code: int = 0
    error: str = ""
    trace_id: str = ""
    duration_ms: int = 0


# ── Database ──────────────────────────────────────────────────

def _default_db_path() -> Path:
    from maop.core.backends.db_utils import find_project_root
    return find_project_root() / "data" / "maop.db"


class MaopDatabase:
    """SQLite database for MAOP persistence.

    Usage::

        db = MaopDatabase()
        db.init()
        db.insert_delegation(agent="claude", task="fix bug", exit_code=0)
        rows = db._query("SELECT * FROM delegations LIMIT 10")
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = _default_db_path()
        self._path = Path(db_path)
        self._initialized = False

    # ── Connection management ─────────────────────────────────

    def _connect(self):
        return sqlite_connect(self._path)

    # ── Init ──────────────────────────────────────────────────

    def init(self) -> bool:
        """Create database and all tables if they don't exist."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(SCHEMA_DDL)
            self._initialized = True
            logger.info("Database initialized at %s", self._path)
            return True
        except Exception as exc:
            logger.warning("Failed to initialize database: %s", exc)
            return False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ── Generic query/execute ─────────────────────────────────

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | tuple | None = None,
    ) -> bool:
        """Execute a non-query SQL statement."""
        try:
            with self._connect() as conn:
                conn.execute(sql, params or ())
            return True
        except Exception as exc:
            logger.warning("Execute failed: %s", exc)
            return False

    def _query(
        self,
        sql: str,
        params: dict[str, Any] | tuple | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SELECT query and return rows as dicts.

        .. warning:: Internal API — not for external use. Accepts raw SQL.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params or ())
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("Query failed: %s", exc)
            return []

    # ── Delegations ───────────────────────────────────────────

    def insert_delegation(
        self,
        agent: str,
        task: str,
        *,
        routing_key: str = "",
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        trace_id: str = "",
    ) -> bool:
        """Insert a delegation record."""
        ts = datetime.now(timezone.utc).isoformat()
        return self.execute(
            """INSERT INTO delegations
               (timestamp, agent, task, routing_key, exit_code, stdout, stderr, duration_ms, trace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, agent, task, routing_key, exit_code, stdout, stderr, duration_ms, trace_id),
        )

    def get_recent_delegations(self, limit: int = 50) -> list[DelegationRecord]:
        """Get recent delegation records."""
        rows = self._query(
            "SELECT * FROM delegations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [DelegationRecord(**r) for r in rows]

    # ── Checkpoints ───────────────────────────────────────────

    def save_checkpoint(
        self,
        agent: str,
        task: str,
        phase: str,
        state: dict[str, Any],
    ) -> bool:
        """Save or update a checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        state_json = json.dumps(state, ensure_ascii=False)
        cp_id = f"{agent}_{task}_{phase}_{id(state) % 99999}"

        # Delete existing
        self.execute(
            "DELETE FROM checkpoints WHERE agent = ? AND task = ?",
            (agent, task),
        )
        return self.execute(
            """INSERT INTO checkpoints (id, agent, task, phase, state_json, created, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cp_id, agent, task, phase, state_json, now, now),
        )

    def get_checkpoint(self, agent: str, task: str) -> dict[str, Any] | None:
        """Retrieve checkpoint state for an agent+task."""
        rows = self._query(
            "SELECT state_json FROM checkpoints WHERE agent = ? AND task = ? LIMIT 1",
            (agent, task),
        )
        if not rows:
            return None
        try:
            return cast(dict[str, Any] | None, json.loads(rows[0]["state_json"]))
        except (json.JSONDecodeError, KeyError):
            return None

    def delete_checkpoint(self, agent: str, task: str) -> bool:
        """Delete checkpoint for an agent+task."""
        return self.execute(
            "DELETE FROM checkpoints WHERE agent = ? AND task = ?",
            (agent, task),
        )

    # ── Error log ─────────────────────────────────────────────

    def log_error(
        self,
        agent: str,
        task: str,
        exit_code: int,
        error: str,
        trace_id: str = "",
        duration_ms: int = 0,
    ) -> bool:
        """Log an execution error."""
        ts = datetime.now(timezone.utc).isoformat()
        return self.execute(
            """INSERT INTO error_log (timestamp, agent, task, exit_code, error, trace_id, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, agent, task, exit_code, error, trace_id, duration_ms),
        )

    # ── Metrics ───────────────────────────────────────────────

    def record_metric(
        self,
        agent: str,
        metric_name: str,
        metric_value: float,
        tags: dict[str, Any] | None = None,
    ) -> bool:
        """Record a metric data point."""
        ts = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(tags or {}, ensure_ascii=False)
        return self.execute(
            """INSERT INTO metrics (timestamp, agent, metric_name, metric_value, tags)
               VALUES (?, ?, ?, ?, ?)""",
            (ts, agent, metric_name, metric_value, tags_json),
        )

    # ── Circuit breaker sync ──────────────────────────────────

    def sync_breaker(self, agent: str, state: str, failures: int,
                     threshold: int, last_failure: str | None,
                     cooldown_s: int) -> bool:
        """Upsert circuit breaker state into DB."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute(
            """INSERT OR REPLACE INTO circuit_breaker
               (agent, state, failures, threshold, last_failure, cooldown_s, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (agent, state, failures, threshold, last_failure or "", cooldown_s, now),
        )

    # ── Semi-structured JSON1 queries (P2-2) ────────────────

    def json_query(
        self,
        table: str,
        json_column: str,
        json_path: str,
        value: Any = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query a JSON column using SQLite JSON1 functions.

        Parameters
        ----------
        table : str
            Table name (must exist).
        json_column : str
            Column containing JSON data.
        json_path : str
            JSON path expression (e.g. '$.agent', '$.tags[0]').
        value : Any
            If provided, filter WHERE json_extract = value.
            If None, just extract the path for all rows.
        limit : int
            Maximum rows.

        Returns
        -------
        list[dict]
            Rows with an extra 'json_value' key containing the extracted value.

        Example::

            # Find delegations where metadata.agent = "claude"
            rows = db.json_query("delegations", "stdout", "$.agent", "claude")

            # Extract all tags from metrics
            rows = db.json_query("metrics", "tags", "$.env")
        """
        # Security: validate identifiers to prevent SQL injection
        validate_identifier(table, "table")
        validate_identifier(json_column, "json_column")
        if value is not None:
            sql = f"""SELECT *, json_extract({json_column}, ?) AS json_value
                      FROM {table}
                      WHERE json_extract({json_column}, ?) = ?
                      LIMIT ?"""
            params: tuple[str, str, Any, int] | tuple[str, int] = (json_path, json_path, value, limit)
        else:
            sql = f"""SELECT *, json_extract({json_column}, ?) AS json_value
                      FROM {table}
                      LIMIT ?"""
            params = (json_path, limit)

        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params)
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("JSON1 query failed: %s", exc)
            return []

    def json_each(
        self,
        table: str,
        json_column: str,
        json_path: str = "$",
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Expand a JSON array using json_each() (JSON1 extension).

        Parameters
        ----------
        table : str
            Table name.
        json_column : str
            Column containing a JSON array.
        json_path : str
            Path to the array within the JSON document.
        limit : int
            Maximum rows.

        Returns
        -------
        list[dict]
            Each element of the array as a separate row.

        Example::

            # Expand tags array in metrics
            rows = db.json_each("metrics", "tags", "$")
        """
        # Security: validate identifiers
        validate_identifier(table, "table")
        validate_identifier(json_column, "json_column")
        sql = f"""SELECT je.key, je.value, je.type, je.atom
                  FROM {table}, json_each(json_extract({json_column}, ?)) AS je
                  LIMIT ?"""
        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, (json_path, limit))
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("json_each failed: %s", exc)
            return []

    # ── FTS5 full-text search (P2-2) ────────────────────────

    def fts_init(self, table: str, columns: list[str]) -> bool:
        """Create an FTS5 virtual table for full-text search.

        Parameters
        ----------
        table : str
            Base table name. Creates f"{table}_fts" virtual table.
        columns : list[str]
            Columns to index for full-text search.

        Example::

            db.fts_init("delegations", ["task", "stdout", "stderr"])
        """
        # Security: validate table name and columns
        validate_identifier(table, "table")
        for c in columns:
            validate_identifier(c, "column")
        fts_name = f"{table}_fts"
        cols = ", ".join(columns)
        ddl = f"""CREATE VIRTUAL TABLE IF NOT EXISTS {fts_name}
                  USING fts5({cols}, content={table}, content_rowid=id);"""
        try:
            with self._connect() as conn:
                conn.execute(ddl)
            return True
        except Exception as exc:
            logger.warning("FTS5 init failed: %s", exc)
            return False

    def fts_search(
        self,
        table: str,
        query: str,
        *,
        limit: int = 50,
        highlight: bool = True,
        highlight_tag: str = "mark",
    ) -> list[dict[str, Any]]:
        """Search using FTS5 full-text index.

        Parameters
        ----------
        table : str
            Base table name (uses f"{table}_fts" virtual table).
        query : str
            FTS5 query expression (supports AND, OR, NOT, *).
        limit : int
            Maximum results.
        highlight : bool
            If True, return highlighted snippets.
        highlight_tag : str
            HTML tag name for highlight markers.

        Returns
        -------
        list[dict]
            Search results with rank and optional highlights.
        """
        # Security: validate table name
        validate_identifier(table, "table")
        fts_name = f"{table}_fts"

        if highlight:
            # Get FTS columns from the virtual table schema
            try:
                with self._connect() as conn:
                    # Use bm25 rank + highlight
                    sql = f"""SELECT *, bm25({fts_name}) AS rank,
                              highlight({fts_name}, 0, '<{highlight_tag}>', '</{highlight_tag}>') AS snippet
                              FROM {fts_name}
                              WHERE {fts_name} MATCH ?
                              ORDER BY rank
                              LIMIT ?"""
                    cursor = conn.execute(sql, (query, limit))
                    columns = [desc[0] for desc in cursor.description]
                    return [dict(zip(columns, row)) for row in cursor.fetchall()]
            except Exception as exc:
                logger.warning("FTS5 highlight search failed, trying plain: %s", exc)

        # Plain search without highlight
        try:
            with self._connect() as conn:
                sql = f"""SELECT *, bm25({fts_name}) AS rank
                          FROM {fts_name}
                          WHERE {fts_name} MATCH ?
                          ORDER BY rank
                          LIMIT ?"""
                cursor = conn.execute(sql, (query, limit))
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("FTS5 search failed: %s", exc)
            return []

    def fts_rebuild(self, table: str) -> bool:
        """Rebuild FTS5 index from content table.

        Call after bulk inserts to the content table.
        """
        # Security: validate table name
        validate_identifier(table, "table")
        fts_name = f"{table}_fts"
        try:
            with self._connect() as conn:
                conn.execute(f"INSERT INTO {fts_name}({fts_name}) VALUES('rebuild')")
            return True
        except Exception as exc:
            logger.warning("FTS5 rebuild failed: %s", exc)
            return False
