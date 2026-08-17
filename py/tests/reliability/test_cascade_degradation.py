"""Reliability tests — multi-backend cascade degradation.

Verifies that when distributed backends (Redis, etcd) are unavailable,
MAOP degrades gracefully to local backends (Memory, SQLite) without
data loss, and that every degradation event is recorded in the
degradation log for observability.

All tests are Windows-compatible and do not require any external
service (Redis / PostgreSQL / RabbitMQ / etcd) — unavailability is
simulated via ``unittest.mock.patch`` and ``sys.modules`` manipulation.
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from maop.config.edition import degradation_log, reset_edition
from maop.core.backends.backends import (
    MemoryCacheBackend,
    SQLiteKVBackend,
    SQLiteQueueBackend,
    get_cache_backend,
    get_kv_backend,
    get_queue_backend,
    reset_backends,
)
from maop.core.reliability.circuit_breaker import BreakerState, CircuitBreaker
from maop.model.budget import BudgetGuard
from maop.model.schema import BudgetConfig

# ── Helpers ────────────────────────────────────────────────────


@contextlib.contextmanager
def _module_unavailable(module_name: str):
    """Temporarily make *module_name* unimportable (raises ImportError).

    Setting ``sys.modules[name] = None`` causes ``import name`` and
    ``from name import ...`` to raise :class:`ImportError`, simulating
    a missing optional dependency without actually uninstalling anything.
    """
    sentinel = object()
    original = sys.modules.get(module_name, sentinel)
    sys.modules[module_name] = None  # import -> ImportError
    try:
        yield
    finally:
        if original is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original


@pytest.fixture(autouse=True)
def _reset_backend_state():
    """Reset backend singletons and degradation log before/after each test."""
    reset_backends()
    reset_edition()
    yield
    reset_backends()
    reset_edition()


# ── 1. Cache: Redis → Memory ───────────────────────────────────


def test_cache_redis_to_memory_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis cache requested but unavailable → degrade to MemoryCacheBackend.

    Sets ``MAOP_CACHE_BACKEND=redis`` and mocks ``RedisCacheBackend``
    to raise ``ImportError``.  With ``MAOP_CACHE_ALLOW_FALLBACK=1``,
    the factory falls back to ``MemoryCacheBackend``, records a
    degradation event, and the degraded backend remains functional.
    """
    monkeypatch.setenv("MAOP_CACHE_BACKEND", "redis")
    monkeypatch.setenv("MAOP_CACHE_ALLOW_FALLBACK", "1")

    with patch("maop.core.backends.backends_redis.RedisCacheBackend",
               side_effect=ImportError("mocked: redis unavailable")):
        backend = get_cache_backend()

    assert isinstance(backend, MemoryCacheBackend)

    # Degradation event recorded
    degs = degradation_log()
    assert any(
        d["backend"] == "cache" and d["requested"] == "redis" and d["fallback"] == "memory"
        for d in degs
    ), f"Expected cache redis→memory degradation, got {degs}"

    # Functional CRUD on degraded backend
    backend.set("k1", "v1")
    assert backend.get("k1") == "v1"
    assert backend.exists("k1")
    backend.delete("k1")
    assert backend.get("k1") is None


def test_cache_redis_fail_fast_without_allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without MAOP_CACHE_ALLOW_FALLBACK, Redis ImportError → RuntimeError."""
    monkeypatch.setenv("MAOP_CACHE_BACKEND", "redis")
    monkeypatch.delenv("MAOP_CACHE_ALLOW_FALLBACK", raising=False)

    with patch("maop.core.backends.backends_redis.RedisCacheBackend",  # noqa: SIM117
               side_effect=ImportError("mocked: redis unavailable")):
        with pytest.raises(RuntimeError, match="not importable"):
            get_cache_backend()


# ── 2. Queue: Redis → SQLite ───────────────────────────────────


def test_queue_redis_to_sqlite_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis queue requested but unavailable → degrade to SQLiteQueueBackend.

    With ``MAOP_QUEUE_ALLOW_FALLBACK=1``, the factory falls back to
    ``SQLiteQueueBackend``, records a degradation event, and the
    degraded backend supports publish/consume.
    """
    monkeypatch.setenv("MAOP_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("MAOP_QUEUE_ALLOW_FALLBACK", "1")

    with patch("maop.core.backends.backends_redis.RedisQueueBackend",
               side_effect=ImportError("mocked: redis unavailable")):
        backend = get_queue_backend()

    assert isinstance(backend, SQLiteQueueBackend)

    degs = degradation_log()
    assert any(
        d["backend"] == "queue" and d["requested"] == "redis" and d["fallback"] == "sqlite"
        for d in degs
    ), f"Expected queue redis→sqlite degradation, got {degs}"

    # Functional publish/consume
    msg_id = backend.publish("test-topic", {"hello": "world"})
    assert msg_id
    messages = backend.consume("test-topic", limit=10)
    assert len(messages) >= 1
    assert messages[0]["hello"] == "world"


# ── 3. KV: etcd → SQLite ───────────────────────────────────────


def test_kv_etcd_to_sqlite_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    """etcd KV requested but unavailable → degrade to SQLiteKVBackend.

    With ``MAOP_KV_ALLOW_FALLBACK=1``, the factory falls back to
    ``SQLiteKVBackend``, records a degradation event, and the
    degraded backend supports set/get.
    """
    monkeypatch.setenv("MAOP_KV_BACKEND", "etcd")
    monkeypatch.setenv("MAOP_KV_ALLOW_FALLBACK", "1")

    with _module_unavailable("maop.core.backends.backends_distributed"):
        backend = get_kv_backend()

    assert isinstance(backend, SQLiteKVBackend)

    degs = degradation_log()
    assert any(
        d["backend"] == "kv" and d["requested"] == "etcd" and d["fallback"] == "sqlite"
        for d in degs
    ), f"Expected kv etcd→sqlite degradation, got {degs}"

    # Functional set/get
    backend.set("key1", "value1")
    assert backend.get("key1") == "value1"
    assert backend.delete("key1")


# ── 4. Multiple backend cascade ────────────────────────────────


def test_multiple_backend_cascade(monkeypatch: pytest.MonkeyPatch) -> None:
    """All distributed backends unavailable simultaneously → all degrade.

    With ``MAOP_*_ALLOW_FALLBACK=1``, every backend degrades to its
    local fallback, each degradation is recorded, and CRUD works.
    """
    monkeypatch.setenv("MAOP_CACHE_BACKEND", "redis")
    monkeypatch.setenv("MAOP_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("MAOP_KV_BACKEND", "etcd")
    monkeypatch.setenv("MAOP_CACHE_ALLOW_FALLBACK", "1")
    monkeypatch.setenv("MAOP_QUEUE_ALLOW_FALLBACK", "1")
    monkeypatch.setenv("MAOP_KV_ALLOW_FALLBACK", "1")

    with (
        patch("maop.core.backends.backends_redis.RedisCacheBackend",
              side_effect=ImportError("mocked: redis unavailable")),
        patch("maop.core.backends.backends_redis.RedisQueueBackend",
              side_effect=ImportError("mocked: redis unavailable")),
        _module_unavailable("maop.core.backends.backends_distributed"),
    ):
        cache = get_cache_backend()
        queue = get_queue_backend()
        kv = get_kv_backend()

    # All degraded to local backends
    assert isinstance(cache, MemoryCacheBackend)
    assert isinstance(queue, SQLiteQueueBackend)
    assert isinstance(kv, SQLiteKVBackend)

    # All three degradation events recorded
    degs = degradation_log()
    backends_seen = {(d["backend"], d["requested"], d["fallback"]) for d in degs}
    assert ("cache", "redis", "memory") in backends_seen
    assert ("queue", "redis", "sqlite") in backends_seen
    assert ("kv", "etcd", "sqlite") in backends_seen

    # CRUD on all degraded backends — data integrity preserved
    cache.set("cascade-key", "cascade-val")
    assert cache.get("cascade-key") == "cascade-val"

    queue.publish("cascade-topic", {"idx": 1})
    msgs = queue.consume("cascade-topic", limit=5)
    assert len(msgs) >= 1 and msgs[0]["idx"] == 1

    kv.set("cascade-kv", "cascade-kv-val")
    assert kv.get("cascade-kv") == "cascade-kv-val"


# ── 5. Data continuity across backend recreation ──────────────


def test_degradation_with_data_continuity(tmp_path: Path) -> None:
    """SQLite KV data survives backend object recreation (data continuity).

    Writes 50 key-value pairs to a ``SQLiteKVBackend``, drops the
    Python reference (simulating backend re-creation after degradation),
    then creates a new backend instance on the same DB file and verifies
    all 50 keys are still readable.  This proves degraded (local)
    backends provide durable storage across process restarts.
    """
    db_path = str(tmp_path / "continuity.db")
    kv1 = SQLiteKVBackend(db_path=db_path)
    for i in range(50):
        kv1.set(f"key:{i:03d}", f"value:{i:03d}")

    # Drop reference (simulate backend re-creation)
    del kv1

    # Re-create on same DB file
    kv2 = SQLiteKVBackend(db_path=db_path)
    for i in range(50):
        assert kv2.get(f"key:{i:03d}") == f"value:{i:03d}"

    keys = kv2.list_keys(prefix="key:")
    assert len(keys) == 50


# ── 6. CircuitBreaker state machine + persistence ─────────────


def test_circuit_breaker_degradation_integration(tmp_path: Path) -> None:
    """CircuitBreaker CLOSED→OPEN→HALF_OPEN→CLOSED with SQLite persistence.

    Creates a breaker with threshold=3 and cooldown=1s, trips it with
    3 failures, verifies OPEN blocks calls, waits for cooldown to
    transition to HALF_OPEN, records a success to return to CLOSED,
    then re-opens the DB with a new ``CircuitBreaker`` instance and
    verifies the CLOSED state is restored from SQLite.
    """
    db_path = tmp_path / "cb_integration.db"
    breaker = CircuitBreaker(path=db_path)

    # Configure: threshold=3, cooldown=1s for fast testing
    entry = breaker.set_state("test-agent", BreakerState.CLOSED, threshold=3)
    entry.cooldown_s = 1
    breaker._save_agent("test-agent", entry)

    # 3 failures → OPEN
    for _ in range(3):
        breaker.record_failure("test-agent")
    entry = breaker.get("test-agent")
    assert entry is not None
    assert entry.state == BreakerState.OPEN
    assert breaker.is_available("test-agent") is False

    # Wait for cooldown → HALF_OPEN (is_available auto-transitions)
    time.sleep(1.1)
    assert breaker.is_available("test-agent") is True
    entry = breaker.get("test-agent")
    assert entry is not None
    assert entry.state == BreakerState.HALF_OPEN

    # Success → CLOSED
    breaker.record_success("test-agent")
    entry = breaker.get("test-agent")
    assert entry is not None
    assert entry.state == BreakerState.CLOSED
    assert entry.failures == 0

    # Persistence: new instance with same DB restores state
    breaker2 = CircuitBreaker(path=db_path)
    entry2 = breaker2.get("test-agent")
    assert entry2 is not None
    assert entry2.state == BreakerState.CLOSED
    assert entry2.failures == 0


# ── 7. BudgetGuard graceful corruption handling ───────────────


def test_budget_guard_under_degradation(tmp_path: Path) -> None:
    """BudgetGuard (deprecated in-memory shim) remains functional.

    P2-1: JSON ``budget_ledger.json`` persistence has been removed.
    The guard now keeps in-memory accumulators only.  This test verifies
    the guard still enforces limits correctly without any on-disk ledger.
    """
    config = BudgetConfig(daily_limit=100.0, monthly_limit=1000.0, hard_stop=True)
    guard = BudgetGuard(root_dir=tmp_path, config=config)

    # Normal spending (in-memory only; no JSON file written)
    guard.record(model="test-model", provider="test", cost=80.0)
    assert guard._daily_spend == 80.0
    ledger_path = tmp_path / "data" / "budget_ledger.json"
    assert not ledger_path.exists()  # P2-1: no JSON persistence

    # Re-create — fresh in-memory state (no on-disk ledger to read)
    guard2 = BudgetGuard(root_dir=tmp_path, config=config)
    assert guard2._daily_spend == 0.0

    # Guard is still functional
    assert guard2.can_spend(estimated_cost=10.0) is True
    guard2.record(model="test", provider="test", cost=10.0)
    assert guard2._daily_spend == 10.0
    assert guard2.can_spend(estimated_cost=90.0) is True
    assert guard2.can_spend(estimated_cost=91.0) is False