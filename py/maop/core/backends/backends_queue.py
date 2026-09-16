"""MAOP Queue & KV Backend Abstractions.

Extracted from ``backends.py`` to keep each module under 500 lines.
Contains the queue and key-value store backend ABCs, their default
SQLite implementations, the environment-driven factory functions, and
the convenience helpers.

Backends provided:
  - QueueBackend  (SQLite / Redis / RabbitMQ)
  - KVBackend     (SQLite / etcd / Consul)

Selection via env vars::

    MAOP_QUEUE_BACKEND=sqlite|redis|rabbitmq
    MAOP_KV_BACKEND=sqlite|etcd|consul
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
        from maop.core.reliability.message_queue import MessageQueue
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
        from maop.core.backends.kv_store import KVStore
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
        # C-2 fix: the old code passed a hard-coded version 0 and discarded
        # ``expected`` entirely — any concurrent writer's value would be
        # blindly overwritten (or the CAS would always fail once version>0).
        # KVStore.cas is version-based, so implement value-CAS on top of it:
        # read current value+version, compare value to ``expected``, then
        # swap against that exact version (still atomic — a concurrent
        # update bumps the version and our cas fails, as it should).
        # Probe with version 0: KVStore versions start at 1, so this never
        # writes — it just returns the current value+version atomically.
        result = self._store.cas(key, 0, new_value)
        current_value = getattr(result, "current_value", None)
        current_version = getattr(result, "current_version", 0)
        if current_value is None and current_version == 0:
            return False  # key does not exist
        if str(current_value) != str(expected):
            return False  # value mismatch — CAS must fail
        retry = self._store.cas(key, current_version, new_value)
        return bool(getattr(retry, "success", retry))


# ═══════════════════════════════════════════════════════════════════════
# Factory functions — environment variable driven
# ═══════════════════════════════════════════════════════════════════════

_queue: QueueBackend | None = None
_kv: KVBackend | None = None

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
    with _factory_lock:
        if _queue is not None:
            return _queue
        defaults = _edition_defaults()
        backend_type = os.getenv("MAOP_QUEUE_BACKEND", defaults["queue"]).lower()
        if backend_type == "rabbitmq":
            # backends_rabbitmq.py 已实现（需可选依赖 pika）。
            # FeatureFlag.RABBITMQ 未加入 _ENTERPRISE_FEATURES，因 pika 为可选安装；
            # 缺失时 ImportError 触发降级到 Redis，再降级到 SQLite。
            try:
                from maop.core.backends.backends_rabbitmq import RabbitMQQueueBackend
                _queue = RabbitMQQueueBackend()
                logger.info("[backends] Queue: RabbitMQ (edition=%s)", get_edition().value)
                return _queue
            except Exception as exc:
                # 后端不可用的全部真实形态：pika 缺失(ImportError)、依赖版本冲突
                # (TypeError, 如 protobuf 与 pika 不兼容)、broker 不可达
                # (AMQPError→RuntimeError/OSError)。只捕 ImportError 会让降级链
                # 在后两者（生产最常见故障）下断裂。
                # fail-fast 防线由 MAOP_QUEUE_ALLOW_FALLBACK（默认关闭）把守：
                # 未显式允许降级时，任何失败都向上抛出，不会静默降级。
                if os.getenv("MAOP_QUEUE_ALLOW_FALLBACK", "0") == "1":
                    logger.warning("[backends] RabbitMQ unavailable (pika/connect: %s), trying Redis fallback", exc)
                    record_degradation("queue", "rabbitmq", "redis", "unavailable_rabbitmq")
                else:
                    raise RuntimeError(
                        f"RabbitMQ queue backend was requested (MAOP_QUEUE_BACKEND={backend_type}) "
                        f"but is unavailable — check pika installation and MAOP_RABBITMQ_URL: {exc}. "
                        "Set MAOP_QUEUE_ALLOW_FALLBACK=1 to allow degrading to Redis/SQLite."
                    ) from exc
                try:
                    from maop.core.backends.backends_redis import RedisQueueBackend
                    _queue = RedisQueueBackend()
                    logger.info("[backends] Queue: Redis (RabbitMQ fallback)")
                    return _queue
                except Exception as exc:
                    # redis 缺失(ImportError)或 Redis 服务不可达(ping 抛
                    # redis.exceptions.ConnectionError)都应落到 SQLite。
                    logger.warning("[backends] Redis queue backend not available (%s), falling back to SQLite", exc)
                    record_degradation("queue", "redis", "sqlite", "redis_unavailable")
        elif backend_type == "redis":
            try:
                from maop.core.backends.backends_redis import RedisQueueBackend
                _queue = RedisQueueBackend()
                logger.info("[backends] Queue: Redis")
                return _queue
            except Exception as exc:
                # redis 缺失(ImportError)或 Redis 服务不可达(ping 抛
                # redis.exceptions.ConnectionError)。fail-fast 防线由
                # MAOP_QUEUE_ALLOW_FALLBACK（默认关闭）把守。
                if os.getenv("MAOP_QUEUE_ALLOW_FALLBACK", "0") == "1":
                    logger.warning("[backends] Redis queue not available (%s), falling back to SQLite", exc)
                    record_degradation("queue", "redis", "sqlite")
                else:
                    raise RuntimeError(
                        f"Redis queue backend was requested (MAOP_QUEUE_BACKEND={backend_type}) "
                        f"but is not importable or not reachable: {exc}. Install redis "
                        "deps and check MAOP_REDIS_URL, or set MAOP_QUEUE_ALLOW_FALLBACK=1 "
                        "to allow degrading to SQLite."
                    ) from exc
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
    with _factory_lock:
        if _kv is not None:
            return _kv
        defaults = _edition_defaults()
        backend_type = os.getenv("MAOP_KV_BACKEND", defaults["kv"]).lower()
        if backend_type in ("etcd", "consul"):
            # backends_distributed.py 已实现（需可选依赖 etcd3）。
            # FeatureFlag.ETCD 未加入 _ENTERPRISE_FEATURES，因 etcd3 为可选安装；
            # 缺失时 ImportError 触发降级到 SQLite。
            try:
                from maop.core.backends.backends_distributed import EtcdKVBackend
                _kv = EtcdKVBackend()
                logger.info("[backends] KV: %s (edition=%s)", backend_type, get_edition().value)
                return _kv
            except Exception as exc:
                # 后端不可用的全部真实形态：etcd3 缺失(ImportError)、依赖版本冲突
                # (TypeError, 如 protobuf 与 etcd3 不兼容)、etcd 集群不可达
                # (RuntimeError/OSError, 构造期连接失败)。只捕 ImportError 会让
                # 降级链在后两者（生产最常见）下断裂。
                # fail-fast 防线由 MAOP_KV_ALLOW_FALLBACK（默认关闭）把守：
                # 未显式允许降级时，任何失败都向上抛出，不会静默降级。
                if os.getenv("MAOP_KV_ALLOW_FALLBACK", "0") == "1":
                    logger.warning(
                        "[backends] %s KV backend unavailable (%s), falling back to SQLite",
                        backend_type, exc,
                    )
                    record_degradation("kv", backend_type, "sqlite", "unavailable_etcd")
                else:
                    raise RuntimeError(
                        f"{backend_type} KV backend was requested (MAOP_KV_BACKEND={backend_type}) "
                        f"but is unavailable (check etcd3 installation and MAOP_ETCD_HOST/PORT): {exc}. "
                        "Set MAOP_KV_ALLOW_FALLBACK=1 to allow degrading to SQLite."
                    ) from exc
        _kv = SQLiteKVBackend(db_path=db_path)
        logger.debug("[backends] KV: SQLite")
        return _kv


def _reset_queue_kv() -> None:
    """Reset cached queue & KV backend instances (useful for testing)."""
    global _queue, _kv
    _queue = None
    _kv = None


# ═══════════════════════════════════════════════════════════════════════
# Convenience helpers — drop-in replacements for direct module access
# ═══════════════════════════════════════════════════════════════════════

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