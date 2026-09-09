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

import contextlib
import logging
import os
from typing import Any

from maop.core.backends.backends import StorageBackend

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


class _PgTransaction:
    """P1-8 fix: PostgreSQL 显式事务句柄。

    绑定到单个连接（``autocommit=False``），在 :meth:`transaction` 上下文
    内使用。退出上下文时由外层根据有无异常统一 commit/rollback。
    """

    def __init__(self, conn: Any, cur: Any) -> None:
        self._conn = conn
        self._cur = cur

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self._cur.execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        self._cur.execute(sql, params or ())
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self._cur.description or []]
        return dict(zip(cols, row))

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        self._cur.execute(sql, params or ())
        cols = [desc[0] for desc in self._cur.description or []]
        return [dict(zip(cols, row)) for row in self._cur.fetchall()]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


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
        # P1-8 fix: 不再静默 no-op。pool 在 autocommit=True 模式下，每次
        # execute 已自动提交，顶层 commit() 无未提交事务可提交。记录 debug
        # 日志使调用行为可观测；需要真正的事务提交/回滚请使用 transaction()。
        logger.debug(
            "[pg] commit() invoked in autocommit mode (no pending txn); "
            "use transaction() for explicit transactional control"
        )

    def rollback(self) -> None:
        # P1-8 fix: 不再静默 no-op。autocommit 模式下已执行的语句无法回滚；
        # 发出 warning 让调用方意识到问题，并改用 transaction() 获得回滚能力。
        logger.warning(
            "[pg] rollback() invoked in autocommit mode — already-committed "
            "statements cannot be rolled back; use transaction() for explicit txn control"
        )

    @contextlib.contextmanager
    def transaction(self) -> Any:
        """P1-8 fix: 显式事务上下文管理器。

        从连接池获取一个连接并切换到 ``autocommit=False``，在上下文内
        通过返回的 :class:`_PgTransaction` 句柄执行 SQL；退出时根据有无
        异常自动 commit 或 rollback，确保多步操作的原子性。

        用法::

            with backend.transaction() as tx:
                tx.execute("INSERT INTO maop_kv(key,value) VALUES (%s,%s)", ("k","v"))
                tx.execute("UPDATE maop_meta SET value=%s WHERE key=%s", (v,"k"))
            # 异常时自动 rollback，正常退出时自动 commit

        注意：上下文内的句柄与顶层 execute/fetchone/fetchall 相互独立
        （后者仍走 autocommit 模式），不要在同一事务内混用顶层方法。
        """
        with self._pool.connection() as conn:
            conn.autocommit = False
            cur = conn.cursor()
            try:
                yield _PgTransaction(conn, cur)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

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
