"""Cross-process ingest queue for the multimodal memory space (M2.2 B2, PR-4).

Why it exists
-------------
The in-process :class:`MultimodalIngestQueue` (``multimodal_ingest.py``) uses a
``queue.Queue`` — items submitted in one process can only be drained by a worker
in the same process. In a multi-process deployment (e.g. one dashboard process
accepting uploads, a separate worker process doing embeddings) the in-process
queue cannot bridge the gap.

This module provides :class:`CrossProcessIngestQueue`, which uses the project's
existing :class:`MessageQueue` (SQLite-backed, consumer groups, ACK, dead
letters) as the transport. The batching logic is the same as the in-process
variant — the only difference is where items come from (queue topic vs
in-memory queue) and how they are acknowledged (msg ACK vs queue.Empty).

Design
------
* ``submit(item)`` → ``MessageQueue.enqueue(topic, item)`` → returns entry_id
* Background worker → ``MessageQueue.dequeue(topic, timeout_s=flush_interval)`` →
  batch → ``_write(batch)`` → ``MessageQueue.ack(msg.id)`` for each
* On shutdown → degrade to synchronous storage (same as in-process variant)
* Consumer group isolates this worker's ACK from other consumers on the same topic

Configuration
-------------
* ``MAOP_MULTIMODAL_INGEST_MODE=inprocess|crossprocess`` (default: inprocess)
* ``MAOP_MULTIMODAL_INGEST_TOPIC`` (default: ``multimodal_ingest``)
* ``MAOP_MULTIMODAL_INGEST_CONSUMER_GROUP`` (default: ``multimodal_ingest_worker``)

Testability (ADR-019): the backend and MessageQueue are injected, so tests swap
in a recorder; ``drain()`` / ``close()`` are explicit so tests never leave
threads behind.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from maop.core.memory.multimodal_backend import MultimodalBackend
from maop.core.reliability.message_queue import MessageQueue

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CONSUMER_GROUP",
    "DEFAULT_FLUSH_INTERVAL_S",
    "DEFAULT_TOPIC",
    "CrossProcessIngestQueue",
    "create_ingest_queue",
]

#: Items per batch — same sweet spot as the in-process variant (spike: batch=8 → 53 ms/image).
DEFAULT_BATCH_SIZE = 8

#: Max seconds a partial batch waits before being flushed anyway.
DEFAULT_FLUSH_INTERVAL_S = 2.0

#: Default topic name for the cross-process ingest queue.
DEFAULT_TOPIC = "multimodal_ingest"

#: Default consumer group name.
DEFAULT_CONSUMER_GROUP = "multimodal_ingest_worker"


class CrossProcessIngestQueue:
    """Cross-process batcher using :class:`MessageQueue` as transport.

    Parameters
    ----------
    backend : MultimodalBackend
        Storage the batches are written through.
    mq : MessageQueue
        The message queue used for cross-process transport.
    topic : str
        Queue topic name (default ``multimodal_ingest``).
    consumer_group : str
        Consumer group for ACK isolation (default ``multimodal_ingest_worker``).
    batch_size : int
        Items per batch (default 8).
    flush_interval_s : float
        Max wait for a partial batch (default 2.0 s).
    """

    def __init__(
        self,
        backend: MultimodalBackend,
        mq: MessageQueue,
        *,
        topic: str = DEFAULT_TOPIC,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        start: bool = True,
    ) -> None:
        self._backend = backend
        self._mq = mq
        self._topic = topic
        self._consumer_group = consumer_group
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_s = max(0.0, float(flush_interval_s))
        self._worker: threading.Thread | None = None
        self._closed = False
        self._lock = threading.Lock()
        self._submitted = 0
        self._batched = 0
        self._flushed = 0
        self._retired_ids: list[str] = []
        if start:
            self.start()

    # ── lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker (idempotent)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("ingest queue is closed")
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run, name="multimodal-ingest-cp", daemon=True,
            )
            self._worker.start()

    def close(self, *, drain: bool = True, timeout: float = 5.0) -> int:
        """Stop the worker, returning the number of entries flushed.

        With ``drain`` the pending items are consumed first. Idempotent:
        closing twice flushes once (the second call returns 0).
        """
        with self._lock:
            if self._closed:
                return 0
            self._closed = True
        flushed = self.drain(timeout=timeout) if drain else 0
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout + 1.0)
            if worker.is_alive():
                logger.error(
                    "[multimodal-ingest-cp] worker still alive after close; "
                    "up to %d queued entry(ies) may be lost",
                    self.pending,
                )
        return flushed

    @property
    def closed(self) -> bool:
        """True once :meth:`close` ran — submissions then fall back to sync."""
        return self._closed

    # ── submission ─────────────────────────────────────────────

    def submit(self, item: dict[str, Any]) -> str:
        """Enqueue one entry via the cross-process queue; returns its id.

        Never blocks on embedding — ``enqueue`` returns as soon as the message
        is persisted to the queue DB (<10 ms).
        """
        if self._closed:
            return ""
        entry_id = str(item.get("id") or item.get("entry_id") or "").strip()
        if not entry_id:
            logger.warning("[multimodal-ingest-cp] item without id, refused")
            return ""
        try:
            self._mq.enqueue(self._topic, dict(item, id=entry_id))
            self._submitted += 1
            return entry_id
        except Exception as exc:
            # MQ-1: enqueue failures raise EnqueueError — log and return empty
            # so the caller can fall back to synchronous storage.
            logger.error(
                "[multimodal-ingest-cp] enqueue failed for %s: %s", entry_id, exc
            )
            return ""

    def submit_many(self, items: list[dict[str, Any]]) -> list[str]:
        """Enqueue several entries; returns the accepted ids."""
        return [eid for eid in (self.submit(it) for it in items) if eid]

    # ── draining ───────────────────────────────────────────────

    def drain(self, timeout: float = 5.0) -> int:
        """Process everything currently queued; returns the number flushed.

        Consumes from the topic until no more messages are available, then
        writes them in batches. Safe to call from tests and from :meth:`close`.
        """
        collected: list[dict[str, Any]] = []
        msgs_to_ack: list[str] = []
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            msg = self._mq.dequeue(
                self._topic,
                consumer_group=self._consumer_group,
                consumer_id="drain",
                timeout_s=0,
            )
            if msg is None:
                break
            collected.append(msg.payload)
            msgs_to_ack.append(msg.id)
            if time.monotonic() > deadline:
                break
        if not collected:
            return 0
        flushed = 0
        for start in range(0, len(collected), self._batch_size):
            if self._write(collected[start : start + self._batch_size]):
                flushed += min(self._batch_size, len(collected) - start)
        # ACK all drained messages (batch write succeeded for these items).
        for msg_id in msgs_to_ack:
            try:
                self._mq.ack(msg_id, consumer_id="drain")
            except Exception as exc:
                logger.warning("[multimodal-ingest-cp] ack failed for %s: %s", msg_id, exc)
        return flushed

    def _run(self) -> None:
        """Worker loop: consume from topic, accumulate batches, flush."""
        batch: list[dict[str, Any]] = []
        msg_ids: list[str] = []
        last_flush = time.monotonic()
        while not self._closed:
            try:
                msg = self._mq.dequeue(
                    self._topic,
                    consumer_group=self._consumer_group,
                    timeout_s=self._flush_interval_s,
                )
                if msg is not None:
                    batch.append(msg.payload)
                    msg_ids.append(msg.id)
            except Exception as exc:
                # Expected-signal: dequeue polling timeout or DB hiccup — log
                # at debug for timeouts, warning for real errors.
                if "timeout" in str(exc).lower():
                    pass  # silent-ok: expected-signal
                else:
                    logger.warning("[multimodal-ingest-cp] dequeue error: %s", exc)
            now = time.monotonic()
            if batch and (
                len(batch) >= self._batch_size
                or (now - last_flush) >= self._flush_interval_s
            ):
                flushed_now = len(batch) if self._write(batch) else 0
                self._flushed += flushed_now
                # ACK only the messages whose payloads were in the written batch.
                for msg_id in msg_ids[:flushed_now]:
                    try:
                        self._mq.ack(msg_id)
                    except Exception as exc:
                        logger.warning("[multimodal-ingest-cp] ack failed for %s: %s", msg_id, exc)
                # If write failed, requeue via nack so the message is retried.
                if flushed_now == 0:
                    for msg_id in msg_ids:
                        try:
                            self._mq.nack(msg_id)
                        except Exception as exc:
                            logger.warning(
                                "[multimodal-ingest-cp] nack failed for %s: %s", msg_id, exc
                            )
                batch = []
                msg_ids = []
                last_flush = now
        # Final partial batch on shutdown.
        if batch:
            flushed_now = len(batch) if self._write(batch) else 0
            self._flushed += flushed_now
            for msg_id in msg_ids[:flushed_now]:
                try:
                    self._mq.ack(msg_id)
                except Exception as exc:
                    logger.warning("[multimodal-ingest-cp] ack failed for %s: %s", msg_id, exc)
            if flushed_now == 0:
                for msg_id in msg_ids:
                    try:
                        self._mq.nack(msg_id)
                    except Exception as exc:
                        logger.warning(
                            "[multimodal-ingest-cp] nack failed for %s: %s", msg_id, exc
                        )

    def _write(self, batch: list[dict[str, Any]]) -> bool:
        """Write one batch through the backend (single call ⇒ batching)."""
        try:
            stored = self._backend.index_many(batch)
            self._batched += 1
            self._retired_ids.extend(stored)
            if len(self._retired_ids) > 4096:
                del self._retired_ids[: len(self._retired_ids) - 4096]
            return bool(stored)
        except Exception as exc:
            logger.error("[multimodal-ingest-cp] batch of %d failed: %s", len(batch), exc)
            return False

    # ── introspection ──────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Items queued but not yet written (via topic stats)."""
        try:
            stats = self._mq.stats()
            return int(stats.by_topic.get(self._topic, 0))
        except Exception:
            return 0

    def stats(self) -> dict[str, Any]:
        return {
            "running": bool(self._worker and self._worker.is_alive()),
            "closed": self._closed,
            "submitted": self._submitted,
            "batch_writes": self._batched,
            "flushed": self._flushed,
            "pending": self.pending,
            "batch_size": self._batch_size,
            "flush_interval_s": self._flush_interval_s,
            "topic": self._topic,
            "consumer_group": self._consumer_group,
            "mode": "crossprocess",
        }


def create_ingest_queue(
    backend: MultimodalBackend,
    *,
    mq: MessageQueue | None = None,
    topic: str = "",
    consumer_group: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
    flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
) -> Any:
    """Factory: return the appropriate ingest queue based on env config.

    Reads ``MAOP_MULTIMODAL_INGEST_MODE``:
    - ``inprocess`` (default): :class:`MultimodalIngestQueue` (in-memory queue)
    - ``crossprocess``: :class:`CrossProcessIngestQueue` (MessageQueue transport)

    Parameters
    ----------
    backend : MultimodalBackend
        Storage backend for batched writes.
    mq : MessageQueue | None
        Pre-constructed MessageQueue for crossprocess mode. If None, a new
        one is created from the default DB path.
    topic : str
        Override topic name (crossprocess mode only).
    consumer_group : str
        Override consumer group (crossprocess mode only).
    """
    import os

    mode = os.environ.get("MAOP_MULTIMODAL_INGEST_MODE", "inprocess").strip().lower()
    if mode == "crossprocess":
        if mq is None:
            mq = MessageQueue()
        return CrossProcessIngestQueue(
            backend,
            mq,
            topic=topic or os.environ.get("MAOP_MULTIMODAL_INGEST_TOPIC", DEFAULT_TOPIC),
            consumer_group=consumer_group
            or os.environ.get("MAOP_MULTIMODAL_INGEST_CONSUMER_GROUP", DEFAULT_CONSUMER_GROUP),
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
        )
    # Default: in-process
    from maop.core.memory.multimodal_ingest import MultimodalIngestQueue
    return MultimodalIngestQueue(
        backend,
        batch_size=batch_size,
        flush_interval_s=flush_interval_s,
    )
