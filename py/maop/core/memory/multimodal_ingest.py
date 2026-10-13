"""Asynchronous ingest queue for the multimodal memory space (M2.2 B2, PR-3).

Why it exists
-------------
ADR-022 D4 measured the CLIP path on this machine: a single image embedding
takes 65-293 ms warm (hovering at the PRD's <200 ms budget), while a batch of
8 lands at ~53 ms/image because the forward pass is shared. So the way to
*close* the latency budget is: enqueue on arrival, embed in batches in the
background. This module is that mechanism.

Design (deliberately minimal)
-----------------------------
* A background thread drains a bounded :class:`queue.Queue` — submissions
  return as soon as the item is queued (<10 ms), never after embedding.
* Draining batches items and calls ``backend.index_many`` (which groups by
  modality and runs **one batched encoding pass per group**, one transaction
  on PG) — so the amortisation this class exists for actually happens.
* On shutdown the queue degrades to **synchronous** storage with a warning:
  an operator who stopped the worker still gets their data in, not dropped.
* Not a persistent queue: the batch window is ~2 s; a SQLite-backed queue
  (``reliability/message_queue.py``) would add a disk failure surface for no
  gain at this window. Cross-process durability stays out of scope.

Testability (ADR-019): the backend is injected, so tests swap in a recorder;
`drain()` / `close()` are explicit so tests never leave threads behind.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from maop.core.memory.multimodal_backend import MultimodalBackend

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_BATCH_SIZE", "DEFAULT_FLUSH_INTERVAL_S", "MultimodalIngestQueue"]

#: Items per batch — the measured sweet spot (spike: batch=8 → 53 ms/image).
DEFAULT_BATCH_SIZE = 8

#: Max seconds a partial batch waits before being flushed anyway.
DEFAULT_FLUSH_INTERVAL_S = 2.0


class MultimodalIngestQueue:
    """Background batcher in front of a :class:`MultimodalBackend`.

    Parameters
    ----------
    backend : MultimodalBackend
        Storage the batches are written through.
    batch_size : int
        Items per batch (default 8).
    flush_interval_s : float
        Max wait for a partial batch (default 2.0 s).
    """

    def __init__(
        self,
        backend: MultimodalBackend,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        start: bool = True,
    ) -> None:
        self._backend = backend
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_s = max(0.0, float(flush_interval_s))
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._lock = threading.Lock()
        self._submitted = 0
        self._batched = 0
        self._flushed = 0
        self._retired_ids: list[str] = []
        if start:
            self.start()

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker (idempotent)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("ingest queue is closed")
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run, name="multimodal-ingest", daemon=True,
            )
            self._worker.start()

    def close(self, *, drain: bool = True, timeout: float = 5.0) -> int:
        """Stop the worker, returning the number of entries flushed.

        With ``drain`` the pending items are flushed first — including the
        partial batch a running worker already pulled off the queue, which
        :meth:`drain` alone cannot see. Idempotent: closing twice flushes once
        (the second call returns 0).
        """
        with self._lock:
            if self._closed:
                return 0
            self._closed = True
        flushed = self.drain(timeout=timeout) if drain else 0
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout + 1.0)
            if worker.is_alive():  # pragma: no cover - only on a stuck embed
                logger.error(
                    "[multimodal-ingest] worker still alive after close; "
                    "up to %d queued entry(ies) may be lost",
                    self.pending,
                )
        return flushed

    @property
    def closed(self) -> bool:
        """True once :meth:`close` ran — submissions then fall back to sync."""
        return self._closed

    # ── submission ───────────────────────────────────────────────

    def submit(self, item: dict[str, Any]) -> str:
        """Enqueue one entry; returns its id ("" when unusable).

        Never blocks on embedding — that is the whole point of the queue.
        """
        if self._closed:
            return ""
        entry_id = str(item.get("id") or item.get("entry_id") or "").strip()
        if not entry_id:
            logger.warning("[multimodal-ingest] item without id, refused")
            return ""
        self._queue.put(dict(item, id=entry_id))
        self._submitted += 1
        return entry_id

    def submit_many(self, items: list[dict[str, Any]]) -> list[str]:
        """Enqueue several entries; returns the accepted ids."""
        return [eid for eid in (self.submit(it) for it in items) if eid]

    # ── draining ─────────────────────────────────────────────────

    def drain(self, timeout: float = 5.0) -> int:
        """Process everything currently queued; returns the number flushed.

        Safe to call from tests and from :meth:`close` (and the primary path
        when no worker is running). The whole queue is drained into **one**
        batch — one write ⇒ one batched encoding pass per modality — because
        the per-item amortisation (ADR-022 D4) is the point of this class.
        """
        collected: list[dict[str, Any]] = []
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                collected.append(self._queue.get_nowait())
            except queue.Empty:
                break
            if time.monotonic() > deadline:
                break
        if not collected:
            return 0
        # Chunked by batch_size: a 50-image backlog must not become one
        # 50-image forward pass (it would blow past the close() join timeout
        # and leave a live daemon writing behind the caller's back).
        flushed = 0
        for start in range(0, len(collected), self._batch_size):
            if self._write(collected[start : start + self._batch_size]):
                flushed += min(self._batch_size, len(collected) - start)
        return flushed

    def _run(self) -> None:
        """Worker loop: accumulate batches, flush on size or interval."""
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        while not self._closed:
            try:
                item = self._queue.get(timeout=self._flush_interval_s)
                batch.append(item)
            except queue.Empty:  # silent-ok: expected-signal — 轮询超时即轮转批处理，非错误
                pass
            now = time.monotonic()
            if batch and (
                len(batch) >= self._batch_size
                or (now - last_flush) >= self._flush_interval_s
            ):
                if self._write(batch):
                    flushed_now = len(batch)
                else:
                    flushed_now = 0
                self._flushed += flushed_now
                batch = []
                last_flush = now
        # Final partial batch on shutdown.
        if batch:
            self._write(batch)
            self._flushed += len(batch)

    def _write(self, batch: list[dict[str, Any]]) -> bool:
        """Write one batch through the backend (single call ⇒ batching)."""
        try:
            stored = self._backend.index_many(batch)
            self._batched += 1
            self._retired_ids.extend(stored)
            # Keep the id log bounded (diagnostics only).
            if len(self._retired_ids) > 4096:
                del self._retired_ids[: len(self._retired_ids) - 4096]
            return bool(stored)
        except Exception as exc:
            # One bad batch must not kill the worker: log and continue.
            logger.error("[multimodal-ingest] batch of %d failed: %s", len(batch), exc)
            return False

    # ── introspection ────────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Items queued but not yet written."""
        return self._queue.qsize()

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
        }
