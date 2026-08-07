"""MAOP Message Queue — SQLite-backed persistent message queue with consumer groups,
delayed delivery, and idempotent consumption.

Provides durable, ordered message delivery with:
  - Priority-based dequeue (lower value = higher priority)
  - ACK confirmation (unacked messages re-enter queue after timeout)
  - Dead letter for exhausted retries
  - Consumer groups: multiple workers share a topic, each message delivered once
  - Delayed delivery: messages become visible after a delay
  - Idempotent consumption: duplicate msg_id silently deduped
  - Zero external dependencies (SQLite only)

Usage::

    mq = MessageQueue(db_path="data/queue.db")
    msg_id = mq.enqueue(topic="tasks", payload={"agent": "claude", "task": "fix"})
    msg = mq.dequeue(topic="tasks", consumer_group="worker-1")
    # ... process msg ...
    mq.ack(msg.id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from enum import IntEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class EnqueueError(RuntimeError):
    """MQ-1 fix: raised when a message cannot be persisted to the queue.

    Previously enqueue() returned "" on failure, silently losing the
    message; producers must now handle (or crash on) this exception.
    """

# ── DDL ───────────────────────────────────────────────────────

_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS queue_messages (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'pending',
  retries INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 3,
  ack_timeout_s REAL NOT NULL DEFAULT 30.0,
  enqueued_at REAL NOT NULL DEFAULT 0.0,
  visible_at REAL NOT NULL DEFAULT 0.0,
  dequeued_at REAL NOT NULL DEFAULT 0.0,
  acked_at REAL NOT NULL DEFAULT 0.0,
  consumer_group TEXT NOT NULL DEFAULT '',
  consumer_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_qm_topic_status ON queue_messages(topic, status);
CREATE INDEX IF NOT EXISTS idx_qm_status_dequeued ON queue_messages(status, dequeued_at);
CREATE INDEX IF NOT EXISTS idx_qm_visible ON queue_messages(topic, status, visible_at);
CREATE INDEX IF NOT EXISTS idx_qm_consumer ON queue_messages(consumer_group, consumer_id);

CREATE TABLE IF NOT EXISTS queue_dead_letters (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 5,
  retries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  consumer_group TEXT NOT NULL DEFAULT '',
  dead_at REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_qdl_topic ON queue_dead_letters(topic);

-- Idempotent consumption: track processed message IDs
CREATE TABLE IF NOT EXISTS queue_idempotent (
  msg_id TEXT PRIMARY KEY,
  consumer_id TEXT NOT NULL,
  processed_at REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_qi_consumer ON queue_idempotent(consumer_id);
"""


# ── Priority ──────────────────────────────────────────────────

class MessagePriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 5
    LOW = 10
    BACKGROUND = 20


# ── Models ────────────────────────────────────────────────────

class Message(BaseModel):
    """A message in the queue."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    topic: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = MessagePriority.NORMAL
    status: str = "pending"  # pending | processing | acked | dead
    retries: int = 0
    max_retries: int = 3
    ack_timeout_s: float = 30.0
    enqueued_at: float = Field(default_factory=time.time)
    visible_at: float = 0.0  # Delayed delivery: message not visible until this time
    dequeued_at: float = 0.0
    acked_at: float = 0.0
    consumer_group: str = ""
    consumer_id: str = ""


class DeadLetterMessage(BaseModel):
    """A message that exhausted retries."""
    id: str = ""
    topic: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = MessagePriority.NORMAL
    retries: int = 0
    error: str = ""
    consumer_group: str = ""
    dead_at: float = 0.0


class QueueStats(BaseModel):
    """Queue statistics."""
    pending: int = 0
    processing: int = 0
    acked: int = 0
    dead_letters: int = 0
    delayed: int = 0  # Messages not yet visible
    by_topic: dict[str, int] = Field(default_factory=dict)
    by_consumer_group: dict[str, int] = Field(default_factory=dict)


# ── MessageQueue ──────────────────────────────────────────────

class MessageQueue:
    """SQLite-backed persistent message queue with consumer groups,
    delayed delivery, and idempotent consumption.

    Parameters
    ----------
    db_path : Path | str | None
        Path to SQLite database file. Defaults to data/queue.db.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = get_db_path("queue")
        self._path = Path(db_path)
        self._initialized = False
        self._init_db()

    def _connect(self):
        return sqlite_connect(self._path)

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_QUEUE_DDL)
            self._initialized = True
        except Exception as exc:
            logger.warning("Failed to initialize message queue DB: %s", exc)

    # ── Enqueue ───────────────────────────────────────────────

    def enqueue(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        priority: int = MessagePriority.NORMAL,
        max_retries: int = 3,
        ack_timeout_s: float = 30.0,
        delay_s: float = 0.0,
        msg_id: str = "",
    ) -> str:
        """Add a message to the queue. Returns message ID.

        Parameters
        ----------
        topic : str
            Queue topic.
        payload : dict
            Message payload.
        priority : int
            Priority (lower = higher priority).
        max_retries : int
            Maximum retry count before dead letter.
        ack_timeout_s : float
            Seconds before unacked message is reclaimed.
        delay_s : float
            Delay before message becomes visible (delayed delivery).
        msg_id : str
            Custom message ID for idempotent dedup.
            If provided and already exists, silently returns existing ID.
        """
        # Idempotent: if msg_id already exists, return it
        if msg_id:
            existing = self._query(
                "SELECT id FROM queue_messages WHERE id = ? LIMIT 1",
                (msg_id,),
            )
            if existing:
                logger.debug("[mq] Idempotent: msg %s already exists, skipping", msg_id)
                return msg_id

        now = time.time()
        visible_at = now + delay_s if delay_s > 0 else now
        actual_id = msg_id or uuid.uuid4().hex[:16]

        msg = Message(
            id=actual_id,
            topic=topic,
            payload=payload,
            priority=priority,
            max_retries=max_retries,
            ack_timeout_s=ack_timeout_s,
            enqueued_at=now,
            visible_at=visible_at,
        )
        payload_json = json.dumps(msg.payload, ensure_ascii=False, default=str)
        # Retry on SQLite "database is locked" to avoid silently dropping
        # messages under concurrent producers. sqlite_connect already sets
        # busy_timeout=5000 (internal retry), but under high contention a
        # write can still fail after the busy_timeout expires. Without this
        # outer retry, enqueue returns "" and the producer never learns the
        # message was lost — manifesting as flaky 999/1000 in the stress test.
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                with self._connect() as conn:
                    conn.execute(
                        """INSERT INTO queue_messages
                           (id, topic, payload, priority, status, retries,
                            max_retries, ack_timeout_s, enqueued_at, visible_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (msg.id, msg.topic, payload_json, msg.priority, "pending",
                         msg.retries, msg.max_retries, msg.ack_timeout_s,
                         msg.enqueued_at, msg.visible_at),
                    )
                logger.info("[mq] Enqueued %s on %s (prio=%d, delay=%.1fs)",
                            msg.id, topic, priority, delay_s)
                return msg.id
            except sqlite3.IntegrityError as exc:
                # Idempotent race: with an explicit msg_id, concurrent
                # check-then-insert losers hit the UNIQUE constraint — the
                # message already exists, so this is idempotent success,
                # NOT a lost message. Return msg_id like the fast path.
                # (MQ-1's raise-on-failure only applies to real losses.)
                if msg_id and "unique" in str(exc).lower():
                    logger.debug(
                        "[mq] Idempotent race: msg %s already inserted by "
                        "concurrent producer, treating as success", msg_id)
                    return msg_id
                logger.error("[mq] Enqueue failed: %s", exc)
                raise EnqueueError(
                    f"enqueue to topic '{topic}' failed: {exc}") from exc
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < max_attempts - 1:
                    # P0-§4.2: 保留 time.sleep — enqueue() 是同步方法，
                    # 不能改为 asyncio.sleep。
                    time.sleep(0.05 * (attempt + 1))  # 50ms, 100ms, 150ms backoff
                    continue
                # MQ-1 fix: returning "" silently dropped the message — the
                # producer had no way to know it was lost. Raise so callers
                # can retry / surface the failure.
                logger.error("[mq] Enqueue failed after %d attempts: %s",
                             attempt + 1, exc)
                raise EnqueueError(
                    f"enqueue to topic '{topic}' failed after {attempt + 1} attempts: {exc}"
                ) from exc
            except Exception as exc:
                logger.error("[mq] Enqueue failed: %s", exc)
                raise EnqueueError(f"enqueue to topic '{topic}' failed: {exc}") from exc
        raise EnqueueError(f"enqueue to topic '{topic}' failed: retries exhausted")

    # ── Dequeue ───────────────────────────────────────────────

    def dequeue(
        self,
        topic: str,
        *,
        consumer_group: str = "",
        consumer_id: str = "",
        timeout_s: float = 0,
    ) -> Message | None:
        """Get the highest-priority visible pending message for a topic.

        Parameters
        ----------
        topic : str
            Topic to dequeue from.
        consumer_group : str
            Consumer group name. Messages are tracked per group.
        consumer_id : str
            Specific consumer within the group (for idempotent tracking).
        timeout_s : float
            If 0, return immediately. If > 0, poll until available or timeout.
        """
        deadline = time.time() + timeout_s if timeout_s > 0 else 0

        while True:
            self._reclaim_unacked(topic, consumer_group)

            msg = self._dequeue_one(topic, consumer_group, consumer_id)
            if msg is not None:
                # Idempotent check: has this consumer already processed this msg?
                if consumer_id:
                    already = self._query(
                        "SELECT 1 FROM queue_idempotent WHERE msg_id = ? AND consumer_id = ? LIMIT 1",
                        (msg.id, consumer_id),
                    )
                    if already:
                        logger.debug("[mq] Idempotent skip: %s already processed by %s",
                                     msg.id, consumer_id)
                        self.ack(msg.id)
                        continue  # Get next message
                return msg

            if timeout_s == 0 or time.time() >= deadline:
                return None

            # P0-§4.2: 保留 time.sleep — dequeue() 是同步方法，
            # 不能改为 asyncio.sleep。
            time.sleep(0.1)

    def _dequeue_one(
        self,
        topic: str,
        consumer_group: str = "",
        consumer_id: str = "",
    ) -> Message | None:
        """Atomically dequeue the next visible pending message."""
        now = time.time()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT * FROM queue_messages
                       WHERE topic = ? AND status = 'pending'
                       AND visible_at <= ?
                       ORDER BY priority ASC, enqueued_at ASC
                       LIMIT 1""",
                    (topic, now),
                ).fetchone()

                if row is None:
                    return None

                msg_id = row["id"]
                cursor = conn.execute(
                    """UPDATE queue_messages
                       SET status = 'processing', dequeued_at = ?,
                           consumer_group = ?, consumer_id = ?
                       WHERE id = ? AND status = 'pending'""",
                    (now, consumer_group, consumer_id, msg_id),
                )
                if cursor.rowcount == 0:
                    # Another consumer grabbed it between SELECT and UPDATE
                    return None

                return Message(
                    id=row["id"],
                    topic=row["topic"],
                    payload=json.loads(row["payload"]),
                    priority=row["priority"],
                    status="processing",
                    retries=row["retries"],
                    max_retries=row["max_retries"],
                    ack_timeout_s=row["ack_timeout_s"],
                    enqueued_at=row["enqueued_at"],
                    visible_at=row["visible_at"],
                    dequeued_at=now,
                    consumer_group=consumer_group,
                    consumer_id=consumer_id,
                )
        except Exception as exc:
            logger.warning("[mq] Dequeue failed: %s", exc)
            return None

    def _reclaim_unacked(self, topic: str, consumer_group: str = "") -> int:
        """Move timed-out 'processing' messages back to 'pending'."""
        now = time.time()
        try:
            with self._connect() as conn:
                if consumer_group:
                    cursor = conn.execute(
                        """SELECT id, retries, max_retries FROM queue_messages
                           WHERE topic = ? AND consumer_group = ? AND status = 'processing'
                           AND (? - dequeued_at) > ack_timeout_s""",
                        (topic, consumer_group, now),
                    )
                else:
                    cursor = conn.execute(
                        """SELECT id, retries, max_retries FROM queue_messages
                           WHERE topic = ? AND status = 'processing'
                           AND (? - dequeued_at) > ack_timeout_s""",
                        (topic, now),
                    )
                reclaimed = 0
                for row in cursor.fetchall():
                    msg_id = row["id"]
                    retries = row["retries"] + 1
                    max_retries = row["max_retries"]

                    if retries > max_retries:
                        self._move_to_dead_letter(conn, msg_id, "exceeded max retries")
                    else:
                        conn.execute(
                            """UPDATE queue_messages
                               SET status = 'pending', retries = ?, dequeued_at = 0
                               WHERE id = ?""",
                            (retries, msg_id),
                        )
                        reclaimed += 1

                return reclaimed
        except Exception as exc:
            logger.warning("[mq] Reclaim failed: %s", exc)
            return 0

    # ── ACK ───────────────────────────────────────────────────

    def ack(self, msg_id: str, consumer_id: str = "") -> bool:
        """Acknowledge a message (mark as successfully processed).

        Also records in idempotent table if consumer_id is provided.
        """
        now = time.time()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE queue_messages
                       SET status = 'acked', acked_at = ?
                       WHERE id = ? AND status = 'processing'""",
                    (now, msg_id),
                )
                if cursor.rowcount == 0:
                    logger.warning("[mq] ACK failed: message %s not in processing state", msg_id)
                    return False

                # Record idempotent consumption
                if consumer_id:
                    conn.execute(
                        """INSERT OR IGNORE INTO queue_idempotent (msg_id, consumer_id, processed_at)
                           VALUES (?, ?, ?)""",
                        (msg_id, consumer_id, now),
                    )
            return True
        except Exception as exc:
            logger.warning("[mq] ACK failed: %s", exc)
            return False

    def nack(self, msg_id: str, error: str = "") -> bool:
        """Explicitly reject a message (move to dead letter or re-queue)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT retries, max_retries FROM queue_messages WHERE id = ?",
                    (msg_id,),
                ).fetchone()

                if row is None:
                    return False

                retries = row["retries"] + 1
                max_retries = row["max_retries"]

                if retries > max_retries:
                    self._move_to_dead_letter(conn, msg_id, error or "nack exceeded max retries")
                else:
                    conn.execute(
                        """UPDATE queue_messages
                           SET status = 'pending', retries = ?, dequeued_at = 0
                           WHERE id = ?""",
                        (retries, msg_id),
                    )
            return True
        except Exception as exc:
            logger.warning("[mq] NACK failed: %s", exc)
            return False

    # ── Dead Letter ───────────────────────────────────────────

    def _move_to_dead_letter(
        self,
        conn: sqlite3.Connection,
        msg_id: str,
        error: str,
    ) -> None:
        """Move a message to the dead letter table."""
        row = conn.execute(
            "SELECT topic, payload, priority, retries, consumer_group FROM queue_messages WHERE id = ?",
            (msg_id,),
        ).fetchone()

        if row is None:
            return

        now = time.time()
        # MQ-3 fix: INSERT OR IGNORE followed by an unconditional DELETE
        # silently destroyed the message when the dead-letter row already
        # existed (PK collision) — the insert was ignored AND the source row
        # was deleted. Only delete from the queue when the dead-letter row
        # was actually written (rowcount == 1) or already exists.
        cur = conn.execute(
            """INSERT OR IGNORE INTO queue_dead_letters
               (id, topic, payload, priority, retries, consumer_group, error, dead_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, row["topic"], row["payload"], row["priority"],
             row["retries"], row["consumer_group"], error, now),
        )
        if cur.rowcount == 0:
            # PK collision — verify a dead-letter copy really exists before
            # removing the source message.
            existing = conn.execute(
                "SELECT 1 FROM queue_dead_letters WHERE id = ?", (msg_id,),
            ).fetchone()
            if existing is None:
                logger.error(
                    "[mq] Dead-letter insert for %s was ignored and no existing "
                    "row found — keeping message in queue to avoid data loss",
                    msg_id,
                )
                return
            logger.warning("[mq] Dead letter %s already recorded; removing queue copy", msg_id)
        conn.execute("DELETE FROM queue_messages WHERE id = ?", (msg_id,))
        logger.warning("[mq] Dead letter: %s on %s (%s)", msg_id, row["topic"], error)

    def get_dead_letters(
        self,
        topic: str | None = None,
        limit: int = 50,
    ) -> list[DeadLetterMessage]:
        """Retrieve dead letter messages."""
        if topic:
            rows = self._query(
                "SELECT * FROM queue_dead_letters WHERE topic = ? ORDER BY dead_at DESC LIMIT ?",
                (topic, limit),
            )
        else:
            rows = self._query(
                "SELECT * FROM queue_dead_letters ORDER BY dead_at DESC LIMIT ?",
                (limit,),
            )
        return [DeadLetterMessage(
            id=r["id"], topic=r["topic"],
            payload=json.loads(r["payload"]),
            priority=r["priority"], retries=r["retries"],
            consumer_group=r.get("consumer_group", ""),
            error=r["error"], dead_at=r["dead_at"],
        ) for r in rows]

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> QueueStats:
        """Get queue statistics."""
        now = time.time()
        pending = self._count("queue_messages", "status = 'pending' AND visible_at <= ?", (now,))
        processing = self._count("queue_messages", "status = 'processing'")
        acked = self._count("queue_messages", "status = 'acked'")
        dead = self._count("queue_dead_letters")
        delayed = self._count("queue_messages", "status = 'pending' AND visible_at > ?", (now,))

        # By topic
        rows = self._query(
            "SELECT topic, COUNT(*) as cnt FROM queue_messages WHERE status = 'pending' GROUP BY topic"
        )
        by_topic = {r["topic"]: r["cnt"] for r in rows}

        # By consumer group
        rows = self._query(
            "SELECT consumer_group, COUNT(*) as cnt FROM queue_messages WHERE status = 'processing' GROUP BY consumer_group"
        )
        by_cg = {r["consumer_group"]: r["cnt"] for r in rows}

        return QueueStats(
            pending=pending,
            processing=processing,
            acked=acked,
            dead_letters=dead,
            delayed=delayed,
            by_topic=by_topic,
            by_consumer_group=by_cg,
        )

    # ── Helpers ───────────────────────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params)
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[mq] Query failed: %s", exc)
            return []

    _VALID_TABLES = frozenset({"queue_messages", "queue_dead_letters", "queue_idempotent"})

    def _count(self, table: str, where: str = "1=1", params: tuple = ()) -> int:
        if table not in self._VALID_TABLES:
            raise ValueError(f"Invalid table name: {table!r}")
        rows = self._query(f"SELECT COUNT(*) as cnt FROM {table} WHERE {where}", params)
        return rows[0]["cnt"] if rows else 0

    def purge_acked(self, older_than_s: float = 3600.0) -> int:
        """Remove acked messages older than older_than_s seconds."""
        cutoff = time.time() - older_than_s
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM queue_messages WHERE status = 'acked' AND acked_at < ?",
                    (cutoff,),
                )
                # Also purge old idempotent records
                conn.execute(
                    "DELETE FROM queue_idempotent WHERE processed_at < ?",
                    (cutoff,),
                )
                return cast(int, cursor.rowcount)
        except Exception as exc:
            logger.warning("[mq] Purge failed: %s", exc)
            return 0

    def cleanup_dead_letters(self, older_than_s: float = 86400.0) -> int:
        """Remove dead letter messages older than older_than_s seconds (default 24h)."""
        cutoff = time.time() - older_than_s
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM queue_dead_letters WHERE dead_at < ?",
                    (cutoff,),
                )
                removed = cursor.rowcount
                if removed > 0:
                    logger.info("[mq] Cleaned up %d dead letters older than %.0fs", removed, older_than_s)
                return cast(int, removed)
        except Exception as exc:
            logger.warning("[mq] Dead letter cleanup failed: %s", exc)
            return 0

    def requeue_dead_letter(self, msg_id: str, *, max_retries: int = 1) -> bool:
        """Re-queue a dead letter message for retry.

        Useful for manual intervention: inspect dead letter, fix the issue,
        then requeue for processing.
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM queue_dead_letters WHERE id = ?",
                    (msg_id,),
                ).fetchone()
                if row is None:
                    return False

                now = time.time()
                new_id = uuid.uuid4().hex[:16]
                conn.execute("""
                    INSERT INTO queue_messages
                    (id, topic, payload, priority, status, retries, max_retries,
                     ack_timeout_s, enqueued_at, visible_at, dequeued_at, acked_at,
                     consumer_group, consumer_id)
                    VALUES (?, ?, ?, ?, 'pending', 0, ?, 30.0, ?, ?, 0, 0, '', '')
                """, (
                    new_id, row["topic"], row["payload"],
                    row["priority"], max_retries,
                    now, now,
                ))
                conn.execute("DELETE FROM queue_dead_letters WHERE id = ?", (msg_id,))
                logger.info("[mq] Requeued dead letter %s as new message %s", msg_id, new_id)
                return True
        except Exception as exc:
            logger.warning("[mq] Requeue dead letter failed: %s", exc)
            return False

    def recover_unacked(self, timeout_buffer: float = 10.0) -> int:
        """Recover messages processing too long (unacked) back to pending.

        Messages in 'processing' state that exceed their ack_timeout_s
        are reset to 'pending' so they can be dequeued again.
        Exhausted messages are moved to dead letters.

        Returns count of recovered messages.
        """
        now = time.time()
        try:
            with self._connect() as conn:
                cursor = conn.execute("""
                    UPDATE queue_messages
                    SET status = 'pending', retries = retries + 1,
                        consumer_id = '', dequeued_at = 0
                    WHERE status = 'processing'
                      AND (? - dequeued_at) > (ack_timeout_s + ?)
                """, (now, timeout_buffer))

                # Move exhausted messages to dead letters
                exhausted = conn.execute("""
                    SELECT id, topic, payload, priority, retries, consumer_group
                    FROM queue_messages
                    WHERE status = 'pending' AND retries >= max_retries
                """).fetchall()

                for row in exhausted:
                    # D4 fix: reuse the MQ-3-safe dead-letter move (it confirms
                    # the DLQ row exists before deleting the source) instead of
                    # an unconditional DELETE that could drop a message on a PK
                    # clash between runs.
                    self._move_to_dead_letter(conn, row[0], "exhausted_retries")

                recovered = cursor.rowcount
                if recovered > 0:
                    logger.info("[mq] Recovered %d unacked messages", recovered)
                return cast(int, recovered)
        except Exception as exc:
            logger.warning("[mq] Unacked recovery failed: %s", exc)
            return 0
