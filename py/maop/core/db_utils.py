"""MAOP DB Utilities — Shared SQLite connection management and project root detection.

Eliminates duplicated _connect() and _find_project_root() implementations
across 9+ modules.

Unified DB routing:
  By default, all modules share ``maop.db`` with table-name prefixes for
  isolation (e.g. ``kv_entries``, ``queue_messages``).  Set
  ``MAOP_DB_PER_MODULE=1`` to restore per-module .db files (legacy mode).
  Enterprise edition with PostgreSQL ignores this entirely.
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

logger = logging.getLogger(__name__)

_VALID_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


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
    conn.execute("PRAGMA busy_timeout=5000")
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
    containing 'config/agents.yaml' or 'py/MAOP/__init__.py'.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "config" / "agents.yaml").exists():
            return parent
        if (parent / "py" / "MAOP" / "__init__.py").exists():
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
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
                conn.row_factory = sqlite3.Row
                return conn
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
