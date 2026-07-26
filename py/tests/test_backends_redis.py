"""Tests for maop.core.backends_redis — Redis cache/queue/lock backends.

Uses a lightweight dict-backed FakeRedis mock (fakeredis is not installed).
The FakeRedis simulates the subset of Redis operations used by the backends:
  - Key/String: get/set/setex/delete/exists/flushdb/incr/expire/ping
  - Streams:    xadd/xreadgroup/xack/xlen/xpending/xgroup_create/xclaim
  - Scripts:    register_script (atomic check-and-delete for lock release)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# FakeRedis — dict-backed Redis mock
# ═══════════════════════════════════════════════════════════════════════

class _FakeScript:
    """Mock Lua script: simulates atomic check-and-delete for lock release."""

    def __init__(self, store: dict) -> None:
        self._store = store

    def __call__(self, keys=None, args=None):
        key = keys[0] if keys else None
        token = args[0] if args else None
        if self._store.get(key) == token and key in self._store:
            del self._store[key]
            return 1
        return 0


class FakeRedis:
    """Lightweight dict-backed Redis mock for testing backends."""

    def __init__(self, **kwargs) -> None:
        self._data: dict = {}           # key -> value
        self._streams: dict = {}        # stream -> [(msg_id, {field: value})]
        self._groups: set = set()       # (stream, group) tuples
        self._pending: dict = {}        # (stream, group) -> {msg_id: idle}
        self._counters: dict = {}       # key -> int
        self._msg_counter = 0

    @classmethod
    def from_url(cls, url: str = "") -> FakeRedis:
        return cls()

    def ping(self) -> None:
        pass

    # ── Key/String operations ──────────────────────────────────────
    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self._data:
            return None
        self._data[key] = value
        return True

    def setex(self, key, ttl, value):
        self._data[key] = value

    def delete(self, *keys):
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
        return count

    def exists(self, key):
        return 1 if key in self._data else 0

    def flushdb(self):
        self._data.clear()
        self._streams.clear()
        self._groups = set()
        self._pending.clear()
        self._counters.clear()

    def incr(self, key):
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def expire(self, key, ttl):
        return 1 if key in self._data else 0

    # ── Stream operations ──────────────────────────────────────────
    def xadd(self, stream, fields):
        if stream not in self._streams:
            self._streams[stream] = []
        self._msg_counter += 1
        msg_id = f"{self._msg_counter}-0"
        byte_fields = {}
        for k, v in fields.items():
            bk = k.encode() if isinstance(k, str) else k
            bv = v.encode() if isinstance(v, str) else v
            byte_fields[bk] = bv
        self._streams[stream].append((msg_id, byte_fields))
        return msg_id.encode()

    def xgroup_create(self, stream, group, id="0", mkstream=False):
        if mkstream and stream not in self._streams:
            self._streams[stream] = []
        key = (stream, group)
        if key in self._groups:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self._groups.add(key)
        self._pending[key] = {}

    def xreadgroup(self, group, consumer, streams, count=1):
        results = []
        for stream in streams:
            key = (stream, group)
            if key not in self._groups:
                self._groups.add(key)
                self._pending[key] = {}
            stream_msgs = self._streams.get(stream, [])
            pending = self._pending.get(key, {})
            new_msgs = [(mid, f) for mid, f in stream_msgs if mid not in pending]
            entries = []
            for mid, f in new_msgs[:count]:
                pending[mid] = 0
                entries.append((mid.encode(), f))
            if entries:
                s = stream.encode() if isinstance(stream, str) else stream
                results.append((s, entries))
        return results

    def xack(self, stream, group, *msg_ids):
        key = (stream, group)
        count = 0
        for mid in msg_ids:
            mid_str = mid.decode() if isinstance(mid, bytes) else mid
            if key in self._pending and mid_str in self._pending[key]:
                del self._pending[key][mid_str]
                count += 1
        return count

    def xlen(self, stream):
        return len(self._streams.get(stream, []))

    def xpending(self, stream, group):
        key = (stream, group)
        return {"pending": len(self._pending.get(key, {}))}

    def xclaim(self, stream, group, consumer, min_idle_time=0, message_ids=None):
        key = (stream, group)
        claimed = []
        for mid in (message_ids or []):
            mid_str = mid.decode() if isinstance(mid, bytes) else mid
            if key in self._pending and mid_str in self._pending[key]:
                claimed.append((mid.encode(), {}))
        return claimed

    # ── Script operations ──────────────────────────────────────────
    def register_script(self, script):
        return _FakeScript(self._data)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_redis_module():
    """Inject FakeRedis as the 'redis' module for the duration of the test."""
    fake_module = MagicMock()
    fake_module.Redis = FakeRedis
    fake_module.from_url = lambda url: FakeRedis()
    with patch.dict(sys.modules, {"redis": fake_module}):
        yield fake_module


@pytest.fixture
def cache_backend(fake_redis_module):
    from maop.core.backends_redis import RedisCacheBackend
    return RedisCacheBackend()


@pytest.fixture
def queue_backend(fake_redis_module):
    from maop.core.backends_redis import RedisQueueBackend
    return RedisQueueBackend()


@pytest.fixture
def lock(fake_redis_module):
    from maop.core.backends_redis import RedisDistributedLock
    return RedisDistributedLock("test_lock", ttl=30)


# ═══════════════════════════════════════════════════════════════════════
# Cache tests
# ═══════════════════════════════════════════════════════════════════════

class TestRedisCacheBackend:
    def test_redis_cache_get_set(self, cache_backend):
        cache_backend.set("k1", {"v": 1})
        assert cache_backend.get("k1") == {"v": 1}

    def test_redis_cache_ttl(self, cache_backend):
        # set with TTL — FakeRedis doesn't expire, but setex should not raise
        cache_backend.set("k1", "v1", ttl=60)
        assert cache_backend.get("k1") == "v1"

    def test_redis_cache_delete(self, cache_backend):
        cache_backend.set("k1", "v1")
        assert cache_backend.delete("k1") is True
        assert cache_backend.get("k1") is None

    def test_redis_cache_exists(self, cache_backend):
        assert cache_backend.exists("k1") is False
        cache_backend.set("k1", "v1")
        assert cache_backend.exists("k1") is True

    def test_redis_cache_clear(self, cache_backend):
        cache_backend.set("k1", "v1")
        cache_backend.set("k2", "v2")
        cache_backend.clear()
        assert cache_backend.get("k1") is None
        assert cache_backend.get("k2") is None


# ═══════════════════════════════════════════════════════════════════════
# Queue tests
# ═══════════════════════════════════════════════════════════════════════

class TestRedisQueueBackend:
    def test_redis_queue_publish_consume(self, queue_backend):
        msg_id = queue_backend.publish("topic1", {"task": "run"})
        assert isinstance(msg_id, str)
        msgs = queue_backend.consume("topic1", limit=10)
        assert len(msgs) == 1
        assert msgs[0]["task"] == "run"
        assert msgs[0]["_msg_id"] == msg_id

    def test_redis_queue_ack(self, queue_backend):
        msg_id = queue_backend.publish("topic1", {"task": "run"})
        # Must consume before acking (Redis Streams semantics)
        msgs = queue_backend.consume("topic1", limit=1)
        assert len(msgs) == 1
        assert queue_backend.ack("topic1", msg_id) is True
        # Acking again returns False (already acked)
        assert queue_backend.ack("topic1", msg_id) is False

    def test_redis_queue_topic_stats(self, queue_backend):
        queue_backend.publish("topic1", {"task": "run"})
        queue_backend.publish("topic1", {"task": "walk"})
        queue_backend.consume("topic1", limit=1)  # one consumed, not acked
        stats = queue_backend.topic_stats("topic1")
        assert stats["length"] == 2
        assert stats["pending"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Lock tests
# ═══════════════════════════════════════════════════════════════════════

class TestRedisDistributedLock:
    def test_redis_lock_acquire_release(self, lock):
        assert lock.acquire() is True
        assert lock.release() is True

    def test_redis_lock_fencing_token(self, fake_redis_module):
        # Two locks sharing the same Redis client → fencing token monotonic
        from maop.core.backends_redis import RedisDistributedLock
        shared = FakeRedis()
        lock1 = RedisDistributedLock("mylock", ttl=30, client=shared)
        lock1.acquire()
        token1 = lock1.fencing_token
        assert token1 > 0
        lock1.release()
        lock2 = RedisDistributedLock("mylock", ttl=30, client=shared)
        lock2.acquire()
        token2 = lock2.fencing_token
        assert token2 > token1
        lock2.release()

    def test_redis_lock_non_blocking(self, fake_redis_module):
        # Two locks sharing the same Redis client → second acquire fails
        from maop.core.backends_redis import RedisDistributedLock
        shared = FakeRedis()
        lock1 = RedisDistributedLock("nb_lock", ttl=30, client=shared)
        lock2 = RedisDistributedLock("nb_lock", ttl=30, client=shared)
        assert lock1.acquire(blocking=False) is True
        assert lock2.acquire(blocking=False) is False
        lock1.release()

    def test_redis_lock_context_manager(self, fake_redis_module):
        from maop.core.backends_redis import RedisDistributedLock
        lock = RedisDistributedLock("ctx_lock", ttl=30)
        with lock:
            assert lock.fencing_token > 0
        # After context exit, lock is released
        assert lock._token is None

    def test_redis_lock_refresh(self, lock):
        lock.acquire()
        assert lock.refresh() is True
        lock.release()


# ═══════════════════════════════════════════════════════════════════════
# Degradation test
# ═══════════════════════════════════════════════════════════════════════

def test_redis_backend_degrades(monkeypatch):
    """When redis is not installed, ImportError propagates to caller.

    backends.py get_cache_backend() catches ImportError and falls back to
    MemoryCacheBackend — this test verifies the ImportError is raised.
    """
    # Setting sys.modules['redis'] = None makes `import redis` raise ImportError
    monkeypatch.setitem(sys.modules, "redis", None)
    from maop.core.backends_redis import RedisCacheBackend
    with pytest.raises(ImportError):
        RedisCacheBackend()
