"""MAOP PostgreSQL Storage Backend — psycopg3 implementation.

Implements StorageBackend ABC for PostgreSQL, used when:
  - MAOP_STORAGE_BACKEND=postgresql
  - MAOP_EDITION=enterprise (and psycopg is installed)

Connection config via environment variables:
  - MAOP_PG_DSN      — full connection string (takes priority)
  - MAOP_PG_HOST     — default localhost
  - MAOP_PG_PORT     — default 5432
  - MAOP_PG_DATABASE — default maop
  - MAOP_PG_USER     — default maop
  - MAOP_PG_PASSWORD — default empty

Falls back to SQLite with a degradation warning if psycopg is not installed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from maop.core.backends import StorageBackend

logger = logging.getLogger(__name__)


def _build_dsn() -> str:
    dsn = os.getenv("MAOP_PG_DSN", "").strip()
    if dsn:
        return dsn
    host = os.getenv("MAOP_PG_HOST", "localhost")
    port = os.getenv("MAOP_PG_PORT", "5432")
    dbname = os.getenv("MAOP_PG_DATABASE", "maop")
    user = os.getenv("MAOP_PG_USER", "maop")
    password = os.getenv("MAOP_PG_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


class PostgreSQLStorageBackend(StorageBackend):
    """PostgreSQL storage backend using psycopg3."""

    def __init__(self, dsn: str = "") -> None:
        import psycopg
        self._dsn = dsn or _build_dsn()
        self._conn: psycopg.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> Any:
        import psycopg
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS maop_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS maop_meta (
                    key TEXT PRIMARY KEY,
                    value JSONB DEFAULT '{}',
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description or []]
            return dict(zip(cols, row))

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [desc[0] for desc in cur.description or []]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def commit(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.rollback()

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def table_exists(self, name: str) -> bool:
        row = self.fetchone(
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1",
            (name,),
        )
        return row is not None