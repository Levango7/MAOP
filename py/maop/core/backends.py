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

    from maop.core.backends import get_storage_backend

    backend = get_storage_backend()
    backend.execute("INSERT INTO delegations ...", params)
    rows = backend.fetchall("SELECT * FROM delegations WHERE ...")

Design principle: **all infrastructure access goes through these ABCs**.
Direct sqlite3 / file I/O in business logic is a code smell — route it
through the appropriate backend instead.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, cast

from maop.config.edition import get_edition, record_degradation
from maop.core.db_utils import sqlite_connect

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
    """Default local storage using SQLite."""

    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path

    @staticmethod
    def _default_path() -> str:
        from pathlib import Path
        return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "maop.db")

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        with sqlite_connect(self._db_path or self._default_path()) as conn:
            conn.execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        with sqlite_connect(self._db_path or self._default_path()) as conn:
            cur = conn.execute(sql, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        with sqlite_connect(self._db_path or self._default_path()) as conn:
            cur = conn.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

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
        from maop.core.cache import LRUCache
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
# QueueBackend — persistent message queue
# ═══════════════════════════════════════════════════════════════════════

class QueueBackend(ABC):
    """Abstract message queue backend.

    Default: SQLite-backed persistent queue.
    Cloud:   Redis streams / RabbitMQ.
    """

    @abstractmethod
    def publish(self, topic: str, message: dict[str, Any], *, delay: float = 0) -> str:
        ...

    @abstractmethod
    def consume(self, topic: str, consumer_group: str = "", limit: int = 1) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def ack(self, topic: str, message_id: str) -> bool:
        ...

    @abstractmethod
    def nack(self, topic: str, message_id: str) -> bool:
        ...

    @abstractmethod
    def topic_stats(self, topic: str) -> dict[str, Any]:
        ...


class SQLiteQueueBackend(QueueBackend):
    """Default SQLite-backed message queue."""

    def __init__(self, db_path: str = "") -> None:
        from maop.core.message_queue import MessageQueue
        self._queue = MessageQueue(db_path=db_path) if db_path else MessageQueue()

    def publish(self, topic: str, message: dict[str, Any], *, delay: float = 0) -> str:
        return self._queue.enqueue(topic, message, delay_s=delay)

    def consume(self, topic: str, consumer_group: str = "", limit: int = 1) -> list[dict[str, Any]]:
        results = []
        for _ in range(limit):
            msg = self._queue.dequeue(topic, consumer_group=consumer_group)
            if msg is None:
                break
            results.append(msg.payload if hasattr(msg, 'payload') else dict(msg))
        return results

    def ack(self, topic: str, message_id: str) -> bool:
        return self._queue.ack(topic, message_id)

    def nack(self, topic: str, message_id: str) -> bool:
        return self._queue.nack(topic, message_id)

    def topic_stats(self, topic: str) -> dict[str, Any]:
        stats = self._queue.stats()
        return stats.model_dump() if hasattr(stats, 'model_dump') else dict(stats)


# ═══════════════════════════════════════════════════════════════════════
# KVBackend — key-value store with watch/CAS
# ═══════════════════════════════════════════════════════════════════════

class KVBackend(ABC):
    """Abstract key-value store backend.

    Default: SQLite-backed KV store.
    Cloud:   etcd / Consul.
    """

    @abstractmethod
    def get(self, key: str) -> str | None:
        ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        ...

    @abstractmethod
    def cas(self, key: str, expected: str, new_value: str) -> bool:
        ...


class SQLiteKVBackend(KVBackend):
    """Default SQLite-backed KV store."""

    def __init__(self, db_path: str = "") -> None:
        from maop.core.kv_store import KVStore
        self._store = KVStore(db_path=db_path) if db_path else KVStore()

    def get(self, key: str) -> str | None:
        return cast(str | None, self._store.get(key))

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        self._store.set(key, value, ttl=ttl)

    def delete(self, key: str) -> bool:
        return self._store.delete(key)

    def list_keys(self, prefix: str = "") -> list[str]:
        return self._store.list_keys(prefix=prefix)

    def cas(self, key: str, expected: str, new_value: str) -> bool:
        result = self._store.cas(key, 0, new_value)
        return bool(result.success if hasattr(result, 'success') else result)


# ═══════════════════════════════════════════════════════════════════════
# SecretBackend — secrets / API key management
# ═══════════════════════════════════════════════════════════════════════

class SecretBackend(ABC):
    """Abstract secrets management backend.

    Default: Local Fernet-encrypted vault.
    Cloud:   HashiCorp Vault.
    """

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        ...

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        ...

    @abstractmethod
    def delete_secret(self, key: str) -> bool:
        ...

    @abstractmethod
    def list_secrets(self, prefix: str = "") -> list[str]:
        ...


class LocalSecretBackend(SecretBackend):
    """Default local encrypted vault (delegates to api_key_vault)."""

    def __init__(self, root_dir: str = "") -> None:
        from maop.core.api_key_vault import ApiKeyVault
        self._vault = ApiKeyVault(root_dir=root_dir) if root_dir else ApiKeyVault()

    def get_secret(self, key: str) -> str | None:
        return self._vault.retrieve(key)

    def set_secret(self, key: str, value: str) -> None:
        self._vault.store(key, value)

    def delete_secret(self, key: str) -> bool:
        return self._vault.delete(key)

    def list_secrets(self, prefix: str = "") -> list[str]:
        return [k for k in self._vault.list_providers() if k.startswith(prefix)]


# ═══════════════════════════════════════════════════════════════════════
# Factory functions — environment variable driven
# ═══════════════════════════════════════════════════════════════════════

_storage: StorageBackend | None = None
_cache: CacheBackend | None = None
_queue: QueueBackend | None = None
_kv: KVBackend | None = None
_secret: SecretBackend | None = None


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
    if _storage is not None:
        return _storage
    defaults = _edition_defaults()
    backend_type = os.getenv("MAOP_STORAGE_BACKEND", defaults["storage"]).lower()
    if backend_type == "postgresql":
        try:
            from maop.core.backends_pg import PostgreSQLStorageBackend
            _storage = PostgreSQLStorageBackend()
            logger.info("[backends] Storage: PostgreSQL (edition=%s)", get_edition().value)
            return _storage
        except ImportError:
            logger.warning("[backends] PostgreSQL backend not available, falling back to SQLite")
            record_degradation("storage", "postgresql", "sqlite")
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
    defaults = _edition_defaults()
    backend_type = os.getenv("MAOP_CACHE_BACKEND", defaults["cache"]).lower()
    if backend_type == "redis":
        try:
            from maop.core.backends_redis import RedisCacheBackend
            _cache = RedisCacheBackend()
            logger.info("[backends] Cache: Redis (edition=%s)", get_edition().value)
            return _cache
        except ImportError:
            logger.warning("[backends] Redis backend not available, falling back to memory")
            record_degradation("cache", "redis", "memory")
    _cache = MemoryCacheBackend()
    logger.debug("[backends] Cache: Memory")
    return _cache


def get_queue_backend(db_path: str = "") -> QueueBackend:
    """Get the configured queue backend.

    Selection priority:
      1. MAOP_QUEUE_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → Redis (RabbitMQ available via optional ``pika`` dep)
      3. Default → SQLite

    Note: ``backends_rabbitmq.py`` is implemented (requires optional ``pika``
    dependency).  ``FeatureFlag.RABBITMQ`` is intentionally NOT in
    ``_ENTERPRISE_FEATURES`` because the backend is an optional install —
    if ``pika`` is missing, the import fails and degrades to Redis, then
    SQLite.  This branch is also entered when the user explicitly sets
    ``MAOP_QUEUE_BACKEND=rabbitmq``.
    """
    global _queue
    if _queue is not None:
        return _queue
    defaults = _edition_defaults()
    backend_type = os.getenv("MAOP_QUEUE_BACKEND", defaults["queue"]).lower()
    if backend_type == "rabbitmq":
        # backends_rabbitmq.py 已实现（需可选依赖 pika）。
        # FeatureFlag.RABBITMQ 未加入 _ENTERPRISE_FEATURES，因 pika 为可选安装；
        # 缺失时 ImportError 触发降级到 Redis，再降级到 SQLite。
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend
            _queue = RabbitMQQueueBackend()
            logger.info("[backends] Queue: RabbitMQ (edition=%s)", get_edition().value)
            return _queue
        except ImportError:
            logger.warning("[backends] RabbitMQ backend not available, trying Redis fallback")
            record_degradation("queue", "rabbitmq", "redis", "import_error_rabbitmq")
            try:
                from maop.core.backends_redis import RedisQueueBackend
                _queue = RedisQueueBackend()
                logger.info("[backends] Queue: Redis (RabbitMQ fallback)")
                return _queue
            except ImportError:
                logger.warning("[backends] Redis queue backend not available, falling back to SQLite")
                record_degradation("queue", "redis", "sqlite", "import_error_redis")
    elif backend_type == "redis":
        try:
            from maop.core.backends_redis import RedisQueueBackend
            _queue = RedisQueueBackend()
            logger.info("[backends] Queue: Redis")
            return _queue
        except ImportError:
            logger.warning("[backends] Redis queue backend not available, falling back to SQLite")
            record_degradation("queue", "redis", "sqlite")
    _queue = SQLiteQueueBackend(db_path=db_path)
    logger.debug("[backends] Queue: SQLite")
    return _queue


def get_kv_backend(db_path: str = "") -> KVBackend:
    """Get the configured KV store backend.

    Selection priority:
      1. MAOP_KV_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → SQLite (etcd available via optional ``etcd3`` dep)
      3. Default → SQLite

    Note: ``backends_distributed.py`` is implemented (requires optional
    ``etcd3`` dependency).  ``FeatureFlag.ETCD`` is intentionally NOT in
    ``_ENTERPRISE_FEATURES`` because the backend is an optional install —
    if ``etcd3`` is missing, the import fails and degrades to SQLite.
    This branch is also entered when the user explicitly sets
    ``MAOP_KV_BACKEND=etcd``/``consul``.
    """
    global _kv
    if _kv is not None:
        return _kv
    defaults = _edition_defaults()
    backend_type = os.getenv("MAOP_KV_BACKEND", defaults["kv"]).lower()
    if backend_type in ("etcd", "consul"):
        # backends_distributed.py 已实现（需可选依赖 etcd3）。
        # FeatureFlag.ETCD 未加入 _ENTERPRISE_FEATURES，因 etcd3 为可选安装；
        # 缺失时 ImportError 触发降级到 SQLite。
        try:
            from maop.core.backends_distributed import EtcdKVBackend
            _kv = EtcdKVBackend()
            logger.info("[backends] KV: %s (edition=%s)", backend_type, get_edition().value)
            return _kv
        except ImportError:
            logger.warning("[backends] %s KV backend not available, falling back to SQLite", backend_type)
            record_degradation("kv", backend_type, "sqlite")
    _kv = SQLiteKVBackend(db_path=db_path)
    logger.debug("[backends] KV: SQLite")
    return _kv


def get_secret_backend(root_dir: str = "") -> SecretBackend:
    """Get the configured secrets backend.

    Selection priority:
      1. MAOP_SECRET_BACKEND env var (explicit override)
      2. MAOP_EDITION=enterprise → HashiCorp Vault
      3. Default → Local encrypted vault
    """
    global _secret
    if _secret is not None:
        return _secret
    defaults = _edition_defaults()
    backend_type = os.getenv("MAOP_SECRET_BACKEND", defaults["secret"]).lower()
    if backend_type == "vault":
        try:
            from maop.core.backends_vault import VaultSecretBackend
            _secret = VaultSecretBackend()
            logger.info("[backends] Secrets: HashiCorp Vault (edition=%s)", get_edition().value)
            return _secret
        except ImportError:
            logger.warning("[backends] Vault backend not available, falling back to local")
            record_degradation("secret", "vault", "local")
    _secret = LocalSecretBackend(root_dir=root_dir)
    logger.debug("[backends] Secrets: Local")
    return _secret


def reset_backends() -> None:
    """Reset all cached backend instances (useful for testing)."""
    global _storage, _cache, _queue, _kv, _secret
    _storage = None
    _cache = None
    _queue = None
    _kv = None
    _secret = None


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


def queue_publish(topic: str, message: dict[str, Any], *, delay: float = 0) -> str:
    """Shortcut: get_queue_backend().publish(...)."""
    return get_queue_backend().publish(topic, message, delay=delay)


def queue_consume(topic: str, consumer_group: str = "", limit: int = 1) -> list[dict[str, Any]]:
    """Shortcut: get_queue_backend().consume(...)."""
    return get_queue_backend().consume(topic, consumer_group=consumer_group, limit=limit)


def kv_get(key: str) -> str | None:
    """Shortcut: get_kv_backend().get(...)."""
    return get_kv_backend().get(key)


def kv_set(key: str, value: str, ttl: float | None = None) -> None:
    """Shortcut: get_kv_backend().set(...)."""
    return get_kv_backend().set(key, value, ttl=ttl)


def secret_get(key: str) -> str | None:
    """Shortcut: get_secret_backend().get_secret(...)."""
    return get_secret_backend().get_secret(key)


def secret_set(key: str, value: str) -> None:
    """Shortcut: get_secret_backend().set_secret(...)."""
    return get_secret_backend().set_secret(key, value)
