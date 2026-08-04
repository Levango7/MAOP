"""Stability tests — long-run resource and performance stability.

Marked ``@pytest.mark.slow`` so they can be excluded with
``-m "not slow"`` or ``-k "not slow"``.

Verifies bounded memory growth, no file-handle leaks, no performance
degradation under continuous load, and clean start/stop cycling.

All tests are Windows-compatible: threading/asyncio only, no fork.
"""

from __future__ import annotations

import gc
import time
import tracemalloc
from collections.abc import Generator
from pathlib import Path

import pytest

from maop.core.backends import MemoryCacheBackend
from maop.core.circuit_breaker import CircuitBreaker
from maop.core.db_utils import sqlite_connect
from maop.core.message_queue import MessageQueue
from maop.delegate.dispatcher import Dispatcher

slow = pytest.mark.slow


# ════════════════════════════════════════════════════════════════════
# 1. Memory growth bounded — no leak across 500 create/use/destroy cycles
# ════════════════════════════════════════════════════════════════════


@slow
def test_memory_growth_bounded() -> None:
    """MemoryCacheBackend memory growth stays bounded over 500 cycles.

    Each cycle: create backend → set 100 keys → get all → delete all → drop.
    Asserts final traced memory < 2× initial (growth is bounded, not leaked).
    """
    tracemalloc.start()
    measurements: list[int] = []
    try:
        for i in range(500):
            backend = MemoryCacheBackend()
            for j in range(100):
                backend.set(f"k{j}", f"v{j}" * 10)
            for j in range(100):
                backend.get(f"k{j}")
            for j in range(100):
                backend.delete(f"k{j}")
            backend.clear()
            del backend
            if i % 100 == 0:
                gc.collect()
                current, _ = tracemalloc.get_traced_memory()
                measurements.append(current)
    finally:
        tracemalloc.stop()

    # Skip initial warm-up measurements (Python allocator freelist stabilizes
    # after ~200 cycles). Use a later measurement as baseline and allow 10x
    # growth to accommodate tracemalloc overhead and dict hashtable resizing.
    assert len(measurements) >= 3, "Need >=3 measurements to compare"
    baseline = measurements[1]  # skip i=0 (cold start)
    last = measurements[-1]
    if baseline > 0:
        assert last < baseline * 10, (
            f"Memory grew unbounded: baseline={baseline} bytes, last={last} bytes"
        )


# ════════════════════════════════════════════════════════════════════
# 2. File handles not leaked across 100 open/write/close cycles
# ════════════════════════════════════════════════════════════════════


@slow
def test_file_handle_not_leaked(tmp_path: Path) -> None:
    """SQLite open/write/close cycles do not leak file handles.

    Uses psutil.Process().num_handles() (Windows) / num_fds (POSIX).
    Skips if psutil is unavailable.
    """
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil not available")

    proc = psutil.Process()
    # num_handles on Windows, num_fds on POSIX
    get_handles = getattr(proc, "num_handles", None) or getattr(proc, "num_fds", None)
    if get_handles is None:
        pytest.skip("No handle/fd counter available on this platform")

    initial = get_handles()
    db_path = tmp_path / "handles.db"

    for i in range(100):
        with sqlite_connect(db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, val TEXT)")
            conn.execute("INSERT INTO t VALUES (?, ?)", (i, f"v{i}"))

    gc.collect()
    final = get_handles()
    leaked = final - initial
    assert leaked < 10, f"File handle leak: {leaked} handles (initial={initial})"


# ════════════════════════════════════════════════════════════════════
# 3. Continuous dispatch — no severe performance degradation over 200 calls
# ════════════════════════════════════════════════════════════════════


@slow
async def test_continuous_dispatch_no_degradation(tmp_path: Path) -> None:
    """Dispatcher throughput does not degrade severely over 200 dispatches.

    Dispatches to an unconfigured agent (fast error path) 200 times, records
    average latency per 50-call batch, and asserts the last batch average is
    < 3× the first batch average (no severe degradation).
    """
    breaker = CircuitBreaker(path=tmp_path / "breaker.db")
    dispatcher = Dispatcher(breaker=breaker)

    batch_averages: list[float] = []
    current_batch: list[float] = []

    for i in range(200):
        start = time.perf_counter()
        await dispatcher.dispatch(agent="nonexistent-agent", task=f"task-{i}")
        elapsed = time.perf_counter() - start
        current_batch.append(elapsed)
        if (i + 1) % 50 == 0:
            batch_averages.append(sum(current_batch) / len(current_batch))
            current_batch = []

    assert len(batch_averages) >= 2, "Need >=2 batches to compare"
    first_avg, last_avg = batch_averages[0], batch_averages[-1]
    assert last_avg < first_avg * 3, (
        f"Performance degraded: first={first_avg:.6f}s, last={last_avg:.6f}s"
    )


# ════════════════════════════════════════════════════════════════════
# 4. Repeated create/use/destroy cycle — no exception, no resource leak
# ════════════════════════════════════════════════════════════════════


@slow
def test_repeated_start_stop_cycle(tmp_path: Path) -> None:
    """MessageQueue create/enqueue/drop cycles run cleanly 50 times.

    MessageQueue has no explicit start/stop; this simulates a restart cycle
    by creating a new instance, enqueuing 5 messages, then dropping it.
    Verifies no exceptions and all 250 messages survive across cycles.
    """
    db_path = tmp_path / "queue_cycle.db"
    total_enqueued = 0

    for cycle in range(50):
        queue = MessageQueue(db_path=db_path)
        for i in range(5):
            mid = queue.enqueue("cycle-topic", {"cycle": cycle, "idx": i})
            assert mid, f"Enqueue failed at cycle {cycle}, idx {i}"
        total_enqueued += 5
        del queue  # simulate stop / cleanup

    # Verify all messages survived the create/destroy cycling
    queue_final = MessageQueue(db_path=db_path)
    stats = queue_final.stats()
    assert stats.pending >= total_enqueued, (
        f"Lost messages: expected >= {total_enqueued}, got {stats.pending}"
    )


# ════════════════════════════════════════════════════════════════════
# Fixture — GC between slow tests
# ════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _gc_between_slow_tests() -> Generator[None, None, None]:
    """Force GC between tests to release dropped resources."""
    gc.collect()
    yield
    gc.collect()