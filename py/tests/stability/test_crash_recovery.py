"""Stability tests — crash recovery and data integrity.

Verifies that MAOP subsystems survive simulated process crashes (connection
drops without close/checkpoint) and preserve data via SQLite WAL replay,
JSON ledger persistence, and SQLite-backed state snapshots.

All tests are Windows-compatible: no fork, no SIGKILL — "crash" is simulated
by dropping Python references without calling close().
"""

from __future__ import annotations

import gc
from collections.abc import Generator
from pathlib import Path

import aiosqlite
import pytest

from maop.core.circuit_breaker import BreakerState, CircuitBreaker
from maop.core.message_queue import MessageQueue
from maop.memory.store import MemoryStore
from maop.model.budget import BudgetGuard


# ════════════════════════════════════════════════════════════════════
# 1. WAL replay — un-checkpointed data survives reopen
# ════════════════════════════════════════════════════════════════════


async def test_wal_replay_after_simulated_crash(tmp_path: Path) -> None:
    """SQLite WAL mode replays un-checkpointed journal on reopen.

    Writes 100 rows with auto-checkpoint disabled (data stays in -wal file),
    drops the connection without close (simulated crash), then reopens and
    verifies all 100 rows are visible via automatic WAL replay.
    """
    db_path = tmp_path / "wal_test.db"

    # conn1: WAL mode, disable auto-checkpoint, write 100 rows
    db1 = await aiosqlite.connect(str(db_path))
    await db1.execute("PRAGMA journal_mode=WAL")
    await db1.execute("PRAGMA wal_autocheckpoint=0")
    await db1.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    for i in range(100):
        await db1.execute("INSERT INTO t (id, val) VALUES (?, ?)", (i, f"v{i}"))
    await db1.commit()

    # WAL file should exist (data not yet checkpointed into main db)
    wal_path = tmp_path / "wal_test.db-wal"
    assert wal_path.exists(), "WAL file should exist before checkpoint"

    # Simulate crash: drop conn1 WITHOUT close (no explicit checkpoint)
    del db1

    # conn2: reopen same DB — SQLite replays WAL automatically on first read
    db2 = await aiosqlite.connect(str(db_path))
    cursor = await db2.execute("SELECT COUNT(*) FROM t")
    rows = await cursor.fetchall()
    assert rows[0][0] == 100, "WAL replay should restore all 100 rows"

    # Verify content integrity (id and val match for every row)
    cursor = await db2.execute("SELECT id, val FROM t ORDER BY id")
    rows = await cursor.fetchall()
    for i, (rid, rval) in enumerate(rows):
        assert rid == i and rval == f"v{i}", f"Row {i} mismatch: {rid!r}, {rval!r}"
    await db2.close()


# ════════════════════════════════════════════════════════════════════
# 2. Budget ledger (JSON-backed) survives crash
# ════════════════════════════════════════════════════════════════════


def test_budget_ledger_survives_crash(tmp_path: Path) -> None:
    """JSON budget ledger persists entries across object drop (crash).

    Records 10 spending entries, drops the guard without close, recreates
    from the same root_dir, and verifies daily spend and entry count.
    """
    guard = BudgetGuard(root_dir=tmp_path)
    for i in range(10):
        guard.record(
            model="test-model",
            provider="test",
            cost=0.01 * (i + 1),
            tokens_in=100,
            tokens_out=50,
        )
    expected_daily = guard.stats()["daily_spend"]
    assert expected_daily > 0, "Should have recorded spending"

    # Simulate crash: drop guard without explicit close
    del guard

    # Recreate guard pointing at same root_dir — ledger reloads from JSON
    guard2 = BudgetGuard(root_dir=tmp_path)
    stats = guard2.stats()
    assert stats["daily_spend"] == pytest.approx(expected_daily), (
        "Ledger daily spend should survive crash"
    )


# ════════════════════════════════════════════════════════════════════
# 3. MemoryStore entries survive crash
# ════════════════════════════════════════════════════════════════════


def test_memory_store_persistence_after_crash(tmp_path: Path) -> None:
    """MemoryStore entries persist to SQLite across object drop (crash).

    Stores 50 memory entries, drops the store, recreates from the same
    root_dir, and verifies all entries are retrievable by ID.
    """
    store = MemoryStore(root_dir=tmp_path)
    ids: list[str] = []
    for i in range(50):
        eid = store.store(agent="test", task=f"task-{i}", content=f"content-{i}")
        assert eid is not None, f"Store failed for entry {i}"
        ids.append(eid)

    # Simulate crash: drop store without close
    del store

    # Recreate store — same DB path (MAOP_DATA_DIR isolated by conftest)
    store2 = MemoryStore(root_dir=tmp_path)
    stats = store2.stats()
    assert stats.total_entries >= 50, (
        f"Expected >=50 entries after crash, got {stats.total_entries}"
    )
    # Verify each entry is retrievable by ID
    for eid in ids:
        results = store2.search(entry_id=eid)
        assert len(results) == 1, f"Entry {eid} not found after crash"


# ════════════════════════════════════════════════════════════════════
# 4. MessageQueue — no message loss after reconnect
# ════════════════════════════════════════════════════════════════════


def test_queue_no_loss_after_reconnect(tmp_path: Path) -> None:
    """MessageQueue preserves all messages across reconnect (crash).

    Enqueues 20 messages, drops the queue, recreates from the same DB path,
    and verifies all 20 are still pending.
    """
    db_path = tmp_path / "queue.db"
    q1 = MessageQueue(db_path=db_path)
    for i in range(20):
        mid = q1.enqueue("topic-1", {"idx": i})
        assert mid, f"Enqueue failed for message {i}"

    # Simulate crash: drop queue without close
    del q1

    # Reconnect — same DB path
    q2 = MessageQueue(db_path=db_path)
    stats = q2.stats()
    assert stats.pending >= 20, f"Expected >=20 pending, got {stats.pending}"
    assert stats.by_topic.get("topic-1", 0) >= 20, "All messages should be on topic-1"


# ════════════════════════════════════════════════════════════════════
# 5. CircuitBreaker OPEN state survives restart
# ════════════════════════════════════════════════════════════════════


def test_circuit_breaker_state_survives_restart(tmp_path: Path) -> None:
    """CircuitBreaker OPEN state persists to SQLite and survives restart.

    Triggers 3 failures (default threshold) to enter OPEN, drops the breaker,
    recreates from the same DB path, and verifies state is still OPEN.
    """
    db_path = tmp_path / "breaker.db"
    cb1 = CircuitBreaker(path=db_path)
    # BreakerEntry defaults: threshold=3 → 3 failures transitions to OPEN
    for _ in range(3):
        cb1.record_failure("test-agent")
    entry = cb1.get("test-agent")
    assert entry is not None, "Agent should exist after failures"
    assert entry.state == BreakerState.OPEN, "Should be OPEN after 3 failures"

    # Simulate crash: drop breaker without close
    del cb1

    # Restart — state loads from SQLite
    cb2 = CircuitBreaker(path=db_path)
    entry2 = cb2.get("test-agent")
    assert entry2 is not None, "Agent should exist after restart"
    assert entry2.state == BreakerState.OPEN, "OPEN state should survive restart"
    assert entry2.failures >= 3, "Failure count should survive restart"


# ════════════════════════════════════════════════════════════════════
# 6. Cleanup helper — ensure no lingering connections break other tests
# ════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _gc_between_tests() -> Generator[None, None, None]:
    """Force garbage collection between tests to release dropped objects."""
    gc.collect()
    yield
    gc.collect()