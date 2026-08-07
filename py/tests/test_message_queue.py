"""Tests for MAOP.core.message_queue — SQLite-backed persistent message queue."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from maop.core.reliability.message_queue import (
    MessagePriority,
    MessageQueue,
)


@pytest.fixture
def mq() -> MessageQueue:
    """Create a MessageQueue with a temp DB."""
    tmp = tempfile.mkdtemp(prefix="MAOP_mq_")
    db_path = Path(tmp) / "queue.db"
    queue = MessageQueue(db_path=db_path)
    yield queue
    with contextlib.suppress(Exception):
        shutil.rmtree(tmp, ignore_errors=True)


class TestEnqueueDequeue:
    def test_enqueue_returns_id(self, mq: MessageQueue):
        msg_id = mq.enqueue("tasks", {"agent": "claude"})
        assert msg_id
        assert len(msg_id) == 16

    def test_dequeue_returns_message(self, mq: MessageQueue):
        mq.enqueue("tasks", {"agent": "claude", "cmd": "fix"})
        msg = mq.dequeue("tasks")
        assert msg is not None
        assert msg.topic == "tasks"
        assert msg.payload["agent"] == "claude"
        assert msg.status == "processing"

    def test_dequeue_empty_returns_none(self, mq: MessageQueue):
        msg = mq.dequeue("tasks")
        assert msg is None

    def test_priority_ordering(self, mq: MessageQueue):
        mq.enqueue("tasks", {"name": "low"}, priority=MessagePriority.LOW)
        mq.enqueue("tasks", {"name": "high"}, priority=MessagePriority.HIGH)
        mq.enqueue("tasks", {"name": "normal"}, priority=MessagePriority.NORMAL)

        msg1 = mq.dequeue("tasks")
        assert msg1.payload["name"] == "high"
        msg2 = mq.dequeue("tasks")
        assert msg2.payload["name"] == "normal"
        msg3 = mq.dequeue("tasks")
        assert msg3.payload["name"] == "low"

    def test_topic_isolation(self, mq: MessageQueue):
        mq.enqueue("tasks", {"name": "task1"})
        mq.enqueue("events", {"name": "event1"})

        msg = mq.dequeue("tasks")
        assert msg is not None
        assert msg.payload["name"] == "task1"

        # events topic still has message
        msg2 = mq.dequeue("events")
        assert msg2 is not None
        assert msg2.payload["name"] == "event1"


class TestAckNack:
    def test_ack_marks_acked(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1})
        msg = mq.dequeue("tasks")
        assert msg is not None

        result = mq.ack(msg.id)
        assert result is True

        stats = mq.stats()
        assert stats.acked == 1
        assert stats.processing == 0

    def test_ack_wrong_id_fails(self, mq: MessageQueue):
        result = mq.ack("nonexistent")
        assert result is False

    def test_nack_requeues(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1}, max_retries=2)
        msg = mq.dequeue("tasks")
        assert msg is not None

        # NACK → should re-queue
        result = mq.nack(msg.id, error="transient")
        assert result is True

        # Should be available again
        msg2 = mq.dequeue("tasks")
        assert msg2 is not None
        assert msg2.retries == 1

    def test_nack_exceeds_retries_dead_letter(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1}, max_retries=0)
        msg = mq.dequeue("tasks")
        assert msg is not None

        # NACK with max_retries=0 → dead letter
        result = mq.nack(msg.id, error="permanent")
        assert result is True

        dead = mq.get_dead_letters()
        assert len(dead) == 1
        assert dead[0].error == "permanent"


class TestUnackedReclaim:
    def test_unacked_message_reclaimed(self, mq: MessageQueue):
        """Messages not acked within timeout are reclaimed."""
        mq.enqueue("tasks", {"x": 1}, ack_timeout_s=0.1)
        msg = mq.dequeue("tasks")
        assert msg is not None

        # Wait for timeout
        time.sleep(0.2)

        # Dequeue should reclaim and return the same message
        msg2 = mq.dequeue("tasks")
        assert msg2 is not None
        assert msg2.retries == 1

    def test_unacked_exceeds_retries_dead_letter(self, mq: MessageQueue):
        """Unacked message that exceeds retries goes to dead letter."""
        mq.enqueue("tasks", {"x": 1}, max_retries=1, ack_timeout_s=0.1)
        msg = mq.dequeue("tasks")
        assert msg is not None

        # Wait for first timeout → requeue (retries=1)
        time.sleep(0.2)
        msg2 = mq.dequeue("tasks")
        assert msg2 is not None

        # Wait for second timeout → retries(2) > max_retries(1) → dead letter
        time.sleep(0.2)
        msg3 = mq.dequeue("tasks")
        assert msg3 is None

        dead = mq.get_dead_letters()
        assert len(dead) == 1


class TestDeadLetters:
    def test_get_dead_letters_by_topic(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1}, max_retries=0)
        mq.enqueue("events", {"x": 2}, max_retries=0)

        msg1 = mq.dequeue("tasks")
        mq.nack(msg1.id)
        msg2 = mq.dequeue("events")
        mq.nack(msg2.id)

        assert len(mq.get_dead_letters(topic="tasks")) == 1
        assert len(mq.get_dead_letters(topic="events")) == 1
        assert len(mq.get_dead_letters()) == 2


class TestStats:
    def test_stats_empty(self, mq: MessageQueue):
        stats = mq.stats()
        assert stats.pending == 0
        assert stats.processing == 0
        assert stats.acked == 0
        assert stats.dead_letters == 0

    def test_stats_with_data(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1})
        mq.enqueue("tasks", {"x": 2})
        mq.enqueue("events", {"x": 3})

        stats = mq.stats()
        # Messages may be in pending or delayed depending on timing
        total = stats.pending + stats.delayed
        assert total == 3
        assert stats.by_topic.get("tasks") == 2
        assert stats.by_topic.get("events") == 1

        # Dequeue one
        mq.dequeue("tasks")
        stats = mq.stats()
        total_after = stats.pending + stats.delayed
        assert total_after == 2
        assert stats.processing == 1


class TestPurge:
    def test_purge_acked(self, mq: MessageQueue):
        mq.enqueue("tasks", {"x": 1})
        msg = mq.dequeue("tasks")
        mq.ack(msg.id)

        # Purge acked messages older than 0 seconds
        count = mq.purge_acked(older_than_s=0)
        assert count == 1

        stats = mq.stats()
        assert stats.acked == 0


class TestRecoverUnackedDeadLetter:
    """Regression tests for D4 (MQ-3): exhausted messages must be moved to the
    dead-letter table safely, never dropped on a PK clash."""

    def test_exhausted_message_moved_to_dead_letter(self, mq: MessageQueue):
        msg_id = mq.enqueue("tasks", {"agent": "claude"})
        # Simulate an exhausted message: back to pending with retries >= max_retries.
        with mq._connect() as conn:
            conn.execute(
                "UPDATE queue_messages SET status='pending', retries=99 WHERE id=?",
                (msg_id,),
            )

        recovered = mq.recover_unacked()
        # No message was in 'processing', so nothing is "recovered"; the
        # exhausted one is moved to the dead-letter table instead.
        assert recovered == 0

        with mq._connect() as conn:
            in_queue = conn.execute(
                "SELECT 1 FROM queue_messages WHERE id=?", (msg_id,)
            ).fetchone()
            in_dlq = conn.execute(
                "SELECT 1 FROM queue_dead_letters WHERE id=?", (msg_id,)
            ).fetchone()

        assert in_queue is None, "exhausted message must be removed from the live queue"
        assert in_dlq is not None, "exhausted message must be recorded in the dead-letter table"

    def test_exhausted_message_safe_under_dlq_pk_clash(self, mq: MessageQueue):
        msg_id = mq.enqueue("tasks", {"agent": "claude"})
        with mq._connect() as conn:
            conn.execute(
                "UPDATE queue_messages SET status='pending', retries=99 WHERE id=?",
                (msg_id,),
            )
            # Pre-existing stale dead-letter row with the SAME id -> forces
            # INSERT OR IGNORE to be ignored (PK clash).
            conn.execute(
                "INSERT INTO queue_dead_letters "
                "(id, topic, payload, priority, retries, error, consumer_group, dead_at) "
                "VALUES (?, 'stale', '{}', 0, 0, 'stale', '', 0.0)",
                (msg_id,),
            )

        mq.recover_unacked()

        with mq._connect() as conn:
            in_dlq = conn.execute(
                "SELECT 1 FROM queue_dead_letters WHERE id=?", (msg_id,)
            ).fetchone()

        # D4 fix reuses _move_to_dead_letter: because a DLQ row exists for the
        # id, removing the source is safe (its record is preserved in the DLQ).
        assert in_dlq is not None
