"""MAOP Redis Backends — Cache, Queue, and Distributed Lock.

Implements:
  - RedisCacheBackend  (CacheBackend ABC)
  - RedisQueueBackend  (QueueBackend ABC, using Redis Streams)
  - RedisDistributedLock (lease-based lock with fencing tokens)

Connection config via environment variables:
  - MAOP_REDIS_URL       — full URL (takes priority)
  - MAOP_REDIS_HOST      — default localhost
  - MAOP_REDIS_PORT      — default 6379
  - MAOP_REDIS_PASSWORD  — default empty
  - MAOP_REDIS_DB        — default 0

Falls back to local backends with a degradation warning if redis is not installed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from maop.core.backends.backends import CacheBackend, QueueBackend

logger = logging.getLogger(__name__)


def _build_redis_kwargs() -> dict[str, Any]:
    """Build redis-py connection kwargs from environment variables."""
    url = os.getenv("MAOP_REDIS_URL", "").strip()
    if url:
        return {"url": url}
    return {
        "host": os.getenv("MAOP_REDIS_HOST", "localhost"),
        "port": int(os.getenv("MAOP_REDIS_PORT", "6379")),
        "password": os.getenv("MAOP_REDIS_PASSWORD", "") or None,
        "db": int(os.getenv("MAOP_REDIS_DB", "0")),
    }


class RedisCacheBackend(CacheBackend):
    """Redis-backed cache with JSON serialization."""

    def __init__(self) -> None:
        import redis
        kwargs = _build_redis_kwargs()
        if "url" in kwargs:
            self._client = redis.from_url(kwargs["url"])
        else:
            self._client = redis.Redis(**kwargs)
        self._client.ping()  # Verify connection

    def get(self, key: str) -> Any | None:
        data = self._client.get(key)
        if data is None:
            return None
        return json.loads(data.decode() if isinstance(data, bytes) else data)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        data = json.dumps(value, default=str).encode()
        if ttl:
            self._client.setex(key, int(ttl), data)
        else:
            self._client.set(key, data)

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(key))

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))

    def clear(self) -> None:
        self._client.flushdb()


class RedisQueueBackend(QueueBackend):
    """Redis Streams-backed message queue with consumer groups."""

    def __init__(self) -> None:
        import redis
        kwargs = _build_redis_kwargs()
        if "url" in kwargs:
            self._client = redis.from_url(kwargs["url"])
        else:
            self._client = redis.Redis(**kwargs)
        self._client.ping()
        self._consumer_name = f"consumer-{uuid.uuid4().hex[:8]}"

    def _stream_key(self, topic: str) -> str:
        return f"maop:queue:{topic}"

    def _ensure_group(self, topic: str) -> None:
        stream = self._stream_key(topic)
        try:
            self._client.xgroup_create(stream, "maop_group", id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP means group already exists
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, topic: str, message: dict[str, Any], *, delay: float = 0) -> str:
        stream = self._stream_key(topic)
        # Redis Streams doesn't natively support delay; for delay>0 we store scheduled time
        msg: dict[str, Any] = {"data": json.dumps(message, default=str).encode().hex()}
        if delay > 0:
            msg["scheduled_at"] = str(time.time() + delay)
        msg_id = self._client.xadd(stream, msg)  # type: ignore  # redis stub 对 dict[str,Any] 的 key 类型过严
        return msg_id.decode() if isinstance(msg_id, bytes) else msg_id

    def consume(self, topic: str, consumer_group: str = "", limit: int = 1) -> list[dict[str, Any]]:
        self._ensure_group(topic)
        stream = self._stream_key(topic)
        group = consumer_group or "maop_group"
        consumer = self._consumer_name
        results = self._client.xreadgroup(group, consumer, {stream: ">"}, count=limit)
        messages = []
        # redis stub 对 xreadgroup 返回类型标注不完整（entries 被推断为 str）
        for _stream, entries in results:  # type: ignore
            for msg_id, fields in entries:  # type: ignore
                data_hex = fields.get(b"data", b"").decode()  # type: ignore
                if data_hex:
                    msg = json.loads(bytes.fromhex(data_hex).decode())
                    msg["_msg_id"] = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                    messages.append(msg)
        return messages

    def ack(self, topic: str, message_id: str) -> bool:
        stream = self._stream_key(topic)
        return bool(self._client.xack(stream, "maop_group", message_id))

    def nack(self, topic: str, message_id: str) -> bool:
        # Redis Streams: nack is implicit (don't ack). Claim back for retry.
        stream = self._stream_key(topic)
        try:
            self._client.xclaim(stream, "maop_group", self._consumer_name, min_idle_time=0, message_ids=[message_id])
            return True
        except Exception:
            logger.debug("Silent exception in core/backends_redis.py:140", exc_info=True)
            return False

    def topic_stats(self, topic: str) -> dict[str, Any]:
        stream = self._stream_key(topic)
        try:
            length = self._client.xlen(stream)
            pending = self._client.xpending(stream, "maop_group")
            return {
                "length": length,
                "pending": pending.get("pending", 0) if isinstance(pending, dict) else 0,
            }
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)
            return {"length": 0, "pending": 0}


# Lua script for atomic release with fencing token verification
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisDistributedLock:
    """Distributed lock using Redis SET NX EX with fencing tokens.

    Usage:
        lock = RedisDistributedLock("my_lock", ttl=30)
        if lock.acquire():
            try:
                token = lock.fencing_token
                # do work with token
            finally:
                lock.release()
    """

    def __init__(self, name: str, ttl: float = 30.0, client: Any = None) -> None:
        if client is not None:
            self._client = client
        else:
            import redis
            kwargs = _build_redis_kwargs()
            if "url" in kwargs:
                self._client = redis.from_url(kwargs["url"])
            else:
                self._client = redis.Redis(**kwargs)
        self._name = f"maop:lock:{name}"
        self._ttl = int(ttl)
        self._token: str | None = None
        self._fencing_counter_key = f"maop:fencing:{name}"
        self._fencing_token: int = 0
        self._release_script = self._client.register_script(_RELEASE_LOCK_SCRIPT)

    def acquire(self, blocking: bool = False, timeout: float = 0.0) -> bool:
        self._token = uuid.uuid4().hex
        deadline = time.time() + timeout if timeout > 0 else None
        while True:
            result = self._client.set(self._name, self._token, nx=True, ex=self._ttl)
            if result:
                # Increment fencing token (monotonic)
                self._fencing_token = self._client.incr(self._fencing_counter_key)
                return True
            if not blocking:
                return False
            if deadline and time.time() >= deadline:
                return False
            # P0-§4.2: 保留 time.sleep — acquire() 是同步阻塞接口（调用方期望
            # 同步等待），不能改为 asyncio.sleep。
            time.sleep(0.1)

    def release(self) -> bool:
        if not self._token:
            return False
        result = self._release_script(keys=[self._name], args=[self._token])
        released = bool(result)
        if released:
            self._token = None
        return released

    @property
    def fencing_token(self) -> int:
        return self._fencing_token

    def refresh(self) -> bool:
        """Extend the lock's TTL (for long-running operations)."""
        if not self._token:
            return False
        return bool(self._client.expire(self._name, self._ttl))

    def __enter__(self):
        self.acquire(blocking=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
