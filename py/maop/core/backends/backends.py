"""MAOP Pluggable Backend Abstractions — Strategy Pattern for cloud-native readiness.

Provides abstract base classes for five infrastructure backends:
  - StorageBackend  (SQLite / PostgreSQL)
  - CacheBackend    (Memory / Redis)
  - QueueBackend    (SQLite / Redis / RabbitMQ)
  - KVBackend       (SQLite / etcd / Consul)
  - SecretBackend   (Local / HashiCorp Vault)

Each backend has a default local implementation (zero external deps) and
can be swapped to a distributed implementation via environment variable:

    MAOP_STORAGE_BACKEND=sqlite|postgresql
    MAOP_CACHE_BACKEND=memory|redis
    MAOP_QUEUE_BACKEND=sqlite|redis|rabbitmq
    MAOP_KV_BACKEND=sqlite|etcd|consul
    MAOP_SECRET_BACKEND=local|vault

The factory functions ``get_*_backend()`` read the env var, instantiate the
chosen implementation, and cache it for the process lifetime.

Usage::

    from maop.core.backends.backends import get_storage_backend

    backend = get_storage_backend()
    backend.execute("INSERT INTO delegations ...", params)
    rows = backend.fetchall("SELECT * FROM delegations WHERE ...")

Design principle: **all infrastructure access goes through these ABCs**.
Direct sqlite3 / file I/O in business logic is a code smell — route it
through the appropriate backend instead.

Module layout note: the queue/KV backends (``QueueBackend``,
``SQLiteQueueBackend``, ``KVBackend``, ``SQLiteKVBackend`` and their
factory + helper functions) live in ``backends_queue.py``; the secret
backend (``SecretBackend``, ``LocalSecretBackend`` and its factory +
helpers) lives in ``backends_secret.py``.  This module re-exports every
public symbol from both so that ``from maop.core.backends.backends
import *`` remains fully backward compatible.
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import Any, cast

from maop.config.edition import get_edition, record_degradation

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# StorageBackend — relational / document storage
# ═══════════════════════════════════════════════════════════════════════

class StorageBackend(ABC):
    """Abstract storage backend for relational operations.

    Default: SQLite (local file).
    Cloud:   PostgreSQL (via asyncpg / psycopg).
    """

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        ...

    @abstractmethod
    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def commit(self) -> None:
        ...

    @abstractmethod
    def rollback(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def table_exists(self, name: str) -> bool:
        ...


class SQLiteStorageBackend(StorageBackend):
    """Default local storage using SQLite.

    C10 fix: previously every execute() opened a fresh connection that was
    committed and closed immediately, while commit()/rollback() were silent
    no-ops — multi-statement transactions were impossible and a caller's
    rollback() quietly did nothing. Now a persistent connection in
    autocommit mode is used: standalone statements still auto-commit
    (backwards compatible), and explicit ``BEGIN`` ... commit()/rollback()
    sequences work as real transactions.
    """

    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path
        self._conn: Any = None
        import threading
        self._lock = threading.RLock()

    @staticmethod
    def _default_path() -> str:
        from pathlib import Path
        return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "maop.db")

    def _get_conn(self):
        import sqlite3
        if self._conn is None:
            conn = sqlite3.connect(
                self._db_path or self._default_path(),
                timeout=10,
                check_same_thread=False,
                isolation_level=None,  # autocommit; explicit BEGIN starts a txn
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                from maop.core.backends.db_utils import _get_busy_timeout_ms
                conn.execute(f"PRAGMA busy_timeout={_get_busy_timeout_ms()}")
            except Exception:
                conn.execute("PRAGMA busy_timeout=10000")
            self._conn = conn
        return self._conn

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        with self._lock:
            self._get_conn().execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        with self._lock:
            cur = self._get_conn().execute(sql, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._get_conn().execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]

    def commit(self) -> None:
        # autocommit 模式语义说明：
        # 连接以 isolation_level=None（autocommit）打开，独立语句自动提交。
        # 只有调用方显式执行 "BEGIN" 后，commit() 才会真正提交事务；
        # 否则 commit() 是空操作（无事务进行中）。
        with self._lock:
            if self._conn is not None and self._conn.in_transaction:
                self._conn.commit()
            elif self._conn is not None:
                logger.debug(
                    "[backends] commit() called in autocommit mode with no "
                    "active transaction; this is a no-op. Use explicit BEGIN "
                    "to start a transaction."
                )

    def rollback(self) -> None:
        # autocommit 模式语义说明：
        # 同 commit()，只有显式 BEGIN 后 rollback() 才真正回滚事务。
        # 在 autocommit 模式下对独立语句调用 rollback() 是空操作，
        # 因为语句已自动提交无法回滚。
        with self._lock:
            if self._conn is not None and self._conn.in_transaction:
                self._conn.rollback()
            elif self._conn is not None:
                logger.debug(
                    "[backends] rollback() called in autocommit mode with no "
                    "active transaction; this is a no-op. Use explicit BEGIN "
                    "to start a transaction."
                )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def table_exists(self, name: str) -> bool:
        row = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return row is not None


# ═══════════════════════════════════════════════════════════════════════
# CacheBackend — key-value cache with TTL
# ═══════════════════════════════════════════════════════════════════════

class CacheBackend(ABC):
    """Abstract cache backend.

    Default: In-memory LRU with TTL.
    Cloud:   Redis.
    """

    @abstractmethod
    def get(self, key: str) -> Any | None:
        ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...


class MemoryCacheBackend(CacheBackend):
    """Default in-memory cache (delegates to maop.core.cache)."""

    def __init__(self) -> None:
        from maop.core.reliability.cache import LRUCache
        self._cache = LRUCache(max_size=1024, default_ttl_s=300.0)

    def get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._cache.put(key, value, ttl_s=ttl)

    def delete(self, key: str) -> bool:
        return self._cache.delete(key)

    def exists(self, key: str) -> bool:
        return self._cache.contains(key)

    def clear(self) -> None:
        self._cache.clear()


# ═══════════════════════════════════════════════════════════════════════
# Factory functions — environment variable driven
# ═══════════════════════════════════════════════════════════════════════

_storage: StorageBackend | None = None
_cache: CacheBackend | None = None

# 工厂单例锁：保护 get_*_backend() 中的 check-then-set 临界区，
# 避免多线程并发首次调用时重复创建后端实例（来源：并发安全审计经验）。
_factory_lock = threading.Lock()


def _edition_defaults() -> dict[str, str]:
    """Return default backend types based on MAOP edition.

    Delegates to ``config.edition.backend_defaults()`` — the single source
    of truth.  Individual MAOP_*_BACKEND env vars always override edition
    defaults.
    """
    from maop.config.edition import backend_defaults
    return backend_defaults()


def get_storage_backend(db_path: str = "") -> StorageBackend:
    """Get the configured storage backend.

    Selection priority:
      1. MAOP_STORAGE_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → PostgreSQL
      3. Default → SQLite
    """
    global _storage
    # 快路径：已初始化则直接返回（无锁，避免热路径开销）。
    if _storage is not None:
        return _storage
    with _factory_lock:
        # 双重检查：持锁后再确认一次，避免并发重复创建。
        if _storage is not None:
            return _storage
        defaults = _edition_defaults()
        backend_type = os.getenv("MAOP_STORAGE_BACKEND", defaults["storage"]).lower()
        if backend_type == "postgresql":
            try:
                from maop.core.backends.backends_pg import PostgreSQLStorageBackend
                _storage = PostgreSQLStorageBackend()
                logger.info("[backends] Storage: PostgreSQL (edition=%s)", get_edition().value)
                return _storage
            except Exception as exc:
                # C9 fix: silently degrading an explicitly-requested PostgreSQL
                # backend to SQLite causes split-brain data (writes land in a
                # local file while the rest of the fleet uses PG). Default is
                # now fail-fast; set MAOP_STORAGE_ALLOW_FALLBACK=1 to opt in to
                # the old degrade-with-warning behaviour.
                # 捕获范围覆盖全部不可用形态：psycopg 缺失(ImportError)、
                # 连接池连不上 PG(psycopg.OperationalError)等。fail-fast 防线
                # 由 MAOP_STORAGE_ALLOW_FALLBACK（默认关闭）把守。
                if os.getenv("MAOP_STORAGE_ALLOW_FALLBACK", "0") == "1":
                    logger.warning(
                        "[backends] PostgreSQL backend not available (%s); "
                        "MAOP_STORAGE_ALLOW_FALLBACK=1 → degrading to SQLite", exc,
                    )
                    record_degradation("storage", "postgresql", "sqlite")
                else:
                    raise RuntimeError(
                        "PostgreSQL storage backend was requested "
                        "(MAOP_STORAGE_BACKEND/edition) but is not importable or "
                        f"not reachable: {exc}. Install psycopg/backend_pg deps and "
                        "check MAOP_PG_DSN, or set MAOP_STORAGE_ALLOW_FALLBACK=1 to "
                        "explicitly allow degrading to SQLite."
                    ) from exc
        _storage = SQLiteStorageBackend(db_path=db_path)
        logger.debug("[backends] Storage: SQLite")
        return _storage



def get_cache_backend() -> CacheBackend:
    """Get the configured cache backend.

    Selection priority:
      1. MAOP_CACHE_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → Redis
      3. Default → In-memory LRU
    """
    global _cache
    if _cache is not None:
        return _cache
    with _factory_lock:
        if _cache is not None:
            return _cache
        defaults = _edition_defaults()
        backend_type = os.getenv("MAOP_CACHE_BACKEND", defaults["cache"]).lower()
        if backend_type == "redis":
            try:
                from maop.core.backends.backends_redis import RedisCacheBackend
                _cache = RedisCacheBackend()
                logger.info("[backends] Cache: Redis (edition=%s)", get_edition().value)
                return _cache
            except Exception as exc:
                # 后端不可用的全部真实形态：redis 包缺失(ImportError)、
                # Redis 服务不可达(ping 抛 redis.exceptions.ConnectionError)等。
                # fail-fast 防线由 MAOP_CACHE_ALLOW_FALLBACK（默认关闭）把守。
                if os.getenv("MAOP_CACHE_ALLOW_FALLBACK", "0") == "1":
                    logger.warning("[backends] Redis cache not available (%s), falling back to memory", exc)
                    record_degradation("cache", "redis", "memory")
                else:
                    raise RuntimeError(
                        f"Redis cache backend was requested (MAOP_CACHE_BACKEND={backend_type}) "
                        f"but is not importable or not reachable: {exc}. Install "
                        "redis/backends_redis deps and check MAOP_REDIS_URL, or set "
                        "MAOP_CACHE_ALLOW_FALLBACK=1 to allow degrading to memory."
                    ) from exc
        if backend_type not in ("memory", ""):
            logger.warning(
                "[backends] Unknown cache backend %r (MAOP_CACHE_BACKEND=%s); "
                "falling back to MemoryCacheBackend. Valid values: memory, redis.",
                backend_type, backend_type,
            )
        _cache = MemoryCacheBackend()
        logger.debug("[backends] Cache: Memory")
        return _cache


def reset_backends() -> None:
    """Reset all cached backend instances (useful for testing).

    Storage and cache singletons live in this module; the queue/KV
    singletons live in ``backends_queue`` and the secret singleton in
    ``backends_secret``.  All are reset so callers see a uniform reset
    regardless of which module owns the state.
    """
    global _storage, _cache
    _storage = None
    _cache = None
    # 惰性导入避免循环依赖，并确保各子模块的私有单例也被清空。
    from maop.core.backends.backends_queue import _reset_queue_kv
    from maop.core.backends.backends_secret import _reset_secret
    _reset_queue_kv()
    _reset_secret()


# ═══════════════════════════════════════════════════════════════════════
# Convenience helpers — drop-in replacements for direct module access
# ═══════════════════════════════════════════════════════════════════════

def storage_execute(sql: str, params: tuple[Any, ...] | None = None) -> None:
    """Shortcut: get_storage_backend().execute(...)."""
    return get_storage_backend().execute(sql, params)


def storage_fetchone(sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    """Shortcut: get_storage_backend().fetchone(...)."""
    return get_storage_backend().fetchone(sql, params)


def storage_fetchall(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    """Shortcut: get_storage_backend().fetchall(...)."""
    return get_storage_backend().fetchall(sql, params)


def cache_get(key: str) -> Any | None:
    """Shortcut: get_cache_backend().get(...)."""
    return get_cache_backend().get(key)


def cache_set(key: str, value: Any, ttl: float | None = None) -> None:
    """Shortcut: get_cache_backend().set(...)."""
    return get_cache_backend().set(key, value, ttl=ttl)


def cache_delete(key: str) -> bool:
    """Shortcut: get_cache_backend().delete(...)."""
    return get_cache_backend().delete(key)


# ═══════════════════════════════════════════════════════════════════════
# Re-exports — backward compatibility for queue / KV / secret backends
# ═══════════════════════════════════════════════════════════════════════
#
# The queue/KV and secret backends were extracted into ``backends_queue``
# and ``backends_secret`` to keep this module under 500 lines.  Every
# public symbol is re-exported here so legacy ``from maop.core.backends
# .backends import X`` / ``import *`` keeps working unchanged.

from maop.core.backends.backends_queue import (  # noqa: E402,F401
    KVBackend,
    SQLiteKVBackend,
    SQLiteQueueBackend,
    QueueBackend,
    get_kv_backend,
    get_queue_backend,
    kv_get,
    kv_set,
    queue_consume,
    queue_publish,
)
from maop.core.backends.backends_secret import (  # noqa: E402,F401
    LocalSecretBackend,
    SecretBackend,
    get_secret_backend,
    secret_get,
    secret_set,
)

