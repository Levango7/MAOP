"""MAOP PostgreSQL Storage Backend — psycopg3 with connection pooling.

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

Uses psycopg_pool.ConnectionPool for high-concurrency throughput.
Falls back to SQLite with a degradation warning if psycopg / psycopg_pool
is not installed (ImportError propagates to the caller — see backends.py
get_storage_backend() and enterprise.pg_persist._get_pg_backend()).
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
    """PostgreSQL storage backend using psycopg3 + connection pool."""

    def __init__(self, dsn: str = "") -> None:
        import psycopg  # noqa: F401 — guard: ImportError if psycopg missing
        from psycopg_pool import ConnectionPool

        self._dsn = dsn or _build_dsn()
        self._pool: Any = ConnectionPool(
            conninfo=self._dsn,
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True},
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
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
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description or []]
            return dict(zip(cols, row))

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [desc[0] for desc in cur.description or []]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def commit(self) -> None:
        # Pool manages connections per-operation with autocommit=True;
        # each execute() is its own transaction, so explicit commit is a no-op.
        pass

    def rollback(self) -> None:
        # With autocommit=True, each statement commits immediately;
        # rollback is a no-op (matches SQLiteStorageBackend behavior).
        pass

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def table_exists(self, name: str) -> bool:
        row = self.fetchone(
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s LIMIT 1",
            (name,),
        )
        return row is not None
