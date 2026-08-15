"""MAOP DB Utilities — Shared SQLite connection management and project root detection.

Eliminates duplicated _connect() and _find_project_root() implementations
across 9+ modules.

Unified DB routing:
  By default, all modules share ``maop.db`` with table-name prefixes for
  isolation (e.g. ``kv_entries``, ``queue_messages``).  Set
  ``MAOP_DB_PER_MODULE=1`` to restore per-module .db files (legacy mode).
  Enterprise edition with PostgreSQL ignores this entirely.

Backend selection (P1-6):
  ``get_db_engine()`` returns a SQLAlchemy engine for either SQLite (default)
  or PostgreSQL, selected by ``MAOP_DB_BACKEND`` (``sqlite`` | ``postgresql``).
  The URL can be overridden with ``MAOP_DATABASE_URL`` (preferred) or
  ``MAOP_DB_URL`` (legacy). SQLite remains the default so existing deployments
  keep working without any configuration change.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _get_busy_timeout_ms() -> int:
    """Read MAOP_SQLITE_BUSY_TIMEOUT_MS with fault tolerance (C5 fix).

    A malformed value (e.g. "abc") previously raised ValueError inside
    every DB connect, killing all database access process-wide. Fall back
    to the 10s default and log a warning instead.
    """
    raw = os.environ.get("MAOP_SQLITE_BUSY_TIMEOUT_MS", "10000")
    try:
        value = int(raw)
        if value < 0:
            raise ValueError("negative timeout")
        return value
    except (ValueError, TypeError):
        logger.warning(
            "Invalid MAOP_SQLITE_BUSY_TIMEOUT_MS=%r; falling back to 10000ms", raw,
        )
        return 10000


def validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL table/column name to prevent injection.

    Only allows alphanumeric + underscore, starting with a letter or underscore.
    """
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(f"Invalid SQL {context}: {name!r}")
    return name


@contextmanager
def sqlite_connect(
    db_path: str | Path,
    *,
    timeout: float = 10.0,
    wal: bool = True,
    foreign_keys: bool = True,
    row_factory: type | None = sqlite3.Row,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections with WAL mode and proper cleanup.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file.
    timeout : float
        Connection timeout in seconds.
    wal : bool
        Enable WAL journal mode.
    foreign_keys : bool
        Enable foreign key constraints.
    row_factory : type | None
        Row factory class (default: sqlite3.Row).
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    if row_factory:
        conn.row_factory = row_factory
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    # T2-10: Multi-container SQLite coordination — WAL allows 1 writer + N readers.
    # busy_timeout increased to 10s (env-override: MAOP_SQLITE_BUSY_TIMEOUT_MS).
    conn.execute(f"PRAGMA busy_timeout={_get_busy_timeout_ms()}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find_project_root() -> Path:
    """Find the MAOP project root directory.

    Walks up from this file's location until finding a directory
    containing 'config/agents.yaml' or 'py/maop/__init__.py'.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "config" / "agents.yaml").exists():
            return parent
        if (parent / "py" / "maop" / "__init__.py").exists():
            return parent
    return current.parents[3]


class ConnectionPool:
    """Simple thread-safe SQLite connection pool."""

    def __init__(self, db_path: str | Path, max_size: int = 5) -> None:
        self._db_path = str(db_path)
        self._max_size = max_size
        self._pool: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def acquire(self) -> sqlite3.Connection:
        # C5 fix: pooled connections may be stale/broken (e.g. underlying
        # file handle invalidated). Health-check with SELECT 1 before
        # handing them out; discard broken ones instead of returning them.
        while True:
            with self._lock:
                if not self._pool:
                    break
                conn = self._pool.pop()
            try:
                conn.execute("SELECT 1")
                conn.row_factory = sqlite3.Row
                return conn
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("ignored: %s", e, exc_info=True)
                logger.warning("Discarded broken pooled connection for %s", self._db_path)
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # T2-10: Multi-container SQLite coordination — WAL allows 1 writer + N readers.
        # busy_timeout increased to 10s (env-override: MAOP_SQLITE_BUSY_TIMEOUT_MS).
        conn.execute(f"PRAGMA busy_timeout={_get_busy_timeout_ms()}")
        return conn

    def release(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            if len(self._pool) < self._max_size:
                conn.rollback()
                self._pool.append(conn)
            else:
                conn.close()

    def close_all(self) -> None:
        with self._lock:
            for c in self._pool:
                c.close()
            self._pool.clear()


_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def get_pool(db_path: str | Path, max_size: int = 5) -> ConnectionPool:
    """Get or create a ConnectionPool for the given db_path."""
    key = str(db_path)
    with _pools_lock:
        if key not in _pools:
            _pools[key] = ConnectionPool(key, max_size=max_size)
        return _pools[key]


def close_all_pools() -> None:
    """Close and drop every cached ConnectionPool.

    测试/进程收尾时调用：池连接默认 ``release()`` 回池不关闭，跨测试存活。
    每个测试用独立 ``MAOP_DATA_DIR`` 会产生大量池，连接句柄累积触发
    ``ResourceWarning: unclosed database`` 并在 xdist 全量下耗尽 worker 句柄。
    """
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        try:
            pool.close_all()
        except Exception:
            logger.debug("ignored pool close error", exc_info=True)


_UNIFIED_DB_NAME = "maop.db"


def unified_db_path() -> Path:
    """Return the path to the unified MAOP database.

    Location: ``<data_dir>/maop.db``  (data_dir from settings or auto-detected).
    """
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _UNIFIED_DB_NAME


def get_db_path(module_name: str = "", *, legacy_fallback: str = "") -> Path:
    """Get the DB path for a given module.

    In unified mode (default): all modules share ``maop.db``.
    In per-module mode (``MAOP_DB_PER_MODULE=1``): each module gets its
    own ``<module_name>.db`` file, falling back to *legacy_fallback* if
    *module_name* is empty.

    Parameters
    ----------
    module_name : str
        Logical module name (e.g. "kv_store", "queue", "memory").
        Used as table-name prefix in unified mode and as .db filename
        in per-module mode.
    legacy_fallback : str
        Filename to use in per-module mode when *module_name* is empty.
    """
    if os.getenv("MAOP_DB_PER_MODULE", "0") == "1":
        data_dir = _resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        name = module_name or legacy_fallback or "misc"
        return data_dir / f"{name}.db"
    return unified_db_path()


def _resolve_data_dir() -> Path:
    """Resolve the data directory from settings or project root."""
    data_dir = os.getenv("MAOP_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir)
    root = find_project_root()
    return root / "data"


# ────────────────────────────────────────────────────────────────────────────
# P1-6: SQLAlchemy engine factory with backend selection
# ────────────────────────────────────────────────────────────────────────────

# Module-level engine cache so repeated get_db_engine() calls in the same
# process reuse the underlying pool. Keyed by (backend, url).
_engine_cache: dict[tuple[str, str], Any] = {}
_engine_cache_lock = threading.Lock()


def _resolve_backend(backend: str | None) -> str:
    """Resolve the backend name from arg or env, with validation."""
    backend = (backend or os.environ.get("MAOP_DB_BACKEND", "sqlite")).strip().lower()
    if backend not in ("sqlite", "postgresql", "postgres", "pg"):
        raise ValueError(
            f"Unsupported MAOP_DB_BACKEND={backend!r}; expected 'sqlite' or 'postgresql'"
        )
    # Normalise aliases.
    if backend in ("postgres", "pg"):
        backend = "postgresql"
    return backend


def _default_sqlite_url() -> str:
    """Default SQLite URL pointing at the unified maop.db."""
    return f"sqlite:///{unified_db_path().as_posix()}"


def _default_pg_url() -> str:
    """Default PostgreSQL URL (local, no auth — override via env in prod)."""
    return "postgresql+psycopg2://localhost:5432/maop"


def _resolve_url(backend: str, url: str | None) -> str:
    """Resolve the DB URL for the given backend.

    Priority: explicit *url* arg > ``MAOP_DATABASE_URL`` > ``MAOP_DB_URL`` >
    backend default.
    """
    if url:
        return url
    env_url = os.environ.get("MAOP_DATABASE_URL") or os.environ.get("MAOP_DB_URL")
    if env_url:
        return env_url
    return _default_sqlite_url() if backend == "sqlite" else _default_pg_url()


def get_db_engine(
    backend: str | None = None,
    *,
    url: str | None = None,
    pool_size: int = 10,
    max_overflow: int = 20,
    echo: bool = False,
    cache: bool = True,
) -> Any:
    """Return a SQLAlchemy Engine for the selected backend.

    Parameters
    ----------
    backend : str | None
        ``"sqlite"`` or ``"postgresql"`` (aliases: ``"postgres"``, ``"pg"``).
        If ``None``, reads ``MAOP_DB_BACKEND`` (default ``"sqlite"``).
    url : str | None
        Explicit SQLAlchemy URL. If ``None``, falls back to
        ``MAOP_DATABASE_URL`` / ``MAOP_DB_URL`` / backend default.
    pool_size, max_overflow : int
        Connection-pool sizing. Only applied for PostgreSQL; SQLite uses
        the default SingletonThreadPool/NullPool.
    echo : bool
        Enable SQLAlchemy echo logging.
    cache : bool
        If True (default), reuse a process-wide cached engine for the same
        (backend, url) pair. Pass ``cache=False`` to force a fresh engine
        (e.g. for tests).

    Returns
    -------
    sqlalchemy.engine.Engine
        A sync SQLAlchemy 2.0 Engine. For async use, call
        ``sqlalchemy.ext.asyncio.create_async_engine`` directly with the
        asyncpg URL (``postgresql+asyncpg://...``).
    """
    backend = _resolve_backend(backend)
    resolved_url = _resolve_url(backend, url)
    cache_key = (backend, resolved_url)

    if cache:
        with _engine_cache_lock:
            cached = _engine_cache.get(cache_key)
            if cached is not None:
                return cached

    # Lazy import so maop[postgresql] is optional — SQLite-only deployments
    # don't pay the SQLAlchemy import cost if they never call this function.
    from sqlalchemy import create_engine

    if backend == "sqlite":
        engine = create_engine(resolved_url, echo=echo, future=True)
    else:
        engine = create_engine(
            resolved_url,
            echo=echo,
            future=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
        )

    if cache:
        with _engine_cache_lock:
            _engine_cache[cache_key] = engine
    return engine


def reset_db_engine_cache() -> None:
    """Clear the process-wide engine cache.

    Primarily for tests that swap ``MAOP_DB_BACKEND`` between cases.
    """
    with _engine_cache_lock:
        _engine_cache.clear()


def get_db_backend() -> str:
    """Return the currently configured backend name (``"sqlite"`` | ``"postgresql"``)."""
    return _resolve_backend(None)
