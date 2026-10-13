"""Async ingest queue for the multimodal space (M2.2 B2, PR-3).

Covers the latency-closure mechanism (ADR-022 D4): non-blocking submission,
one batched write per modality group, explicit drain/close, and degradation
to synchronous storage on shutdown.

The backend is a recorder, so nothing touches SQLite/PG here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from maop.core.memory.multimodal_ingest import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_FLUSH_INTERVAL_S,
    MultimodalIngestQueue,
)

REPO = Path(__file__).resolve().parents[2]


class _Recorder:
    """Backend that records index_many calls instead of storing."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def index_many(self, items: list[dict[str, Any]]) -> list[str]:
        self.calls.append([dict(i) for i in items])
        return [str(i["id"]) for i in items]


def _item(entry_id: str, *, modality: str = "text") -> dict[str, Any]:
    if modality == "image":
        return {
            "id": entry_id,
            "image": str(REPO / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png"),
            "modality": "image",
        }
    return {"id": entry_id, "text": f"content-{entry_id}", "modality": "text"}


# ── submission ─────────────────────────────────────────────


def test_submit_returns_immediately_without_embedding():
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    t0 = time.perf_counter()
    eid = q.submit(_item("e1"))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert eid == "e1"
    assert elapsed_ms < 10.0, "submit must not wait on embedding"
    assert q.pending == 1


def test_submit_rejects_empty_id(caplog):
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    assert q.submit({"text": "no id"}) == ""


def test_submit_returns_empty_when_closed():
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    q.close(drain=False)
    assert q.submit(_item("e1")) == ""


def test_submit_many_filters_unusable():
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    ids = q.submit_many([_item("a"), {"text": "x"}, _item("b")])
    assert ids == ["a", "b"]


# ── batching ───────────────────────────────────────────────


def test_worker_batches_and_writes_once_per_flush():
    """Mixed items must be ONE batch write (the amortisation point).

    The worker owns draining while it runs, so the test *waits* for it
    (drain() racing a live worker would take nothing).
    """
    be = _Recorder()
    q = MultimodalIngestQueue(be, batch_size=8, flush_interval_s=0.2, start=False)
    q.submit_many([_item(f"t{i}") for i in range(3)]
                  + [_item(f"i{i}", modality="image") for i in range(3)])
    q.start()
    deadline = time.monotonic() + 3.0
    while not be.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert be.calls, "worker never flushed the batch"
    # one write call containing everything (index_many groups internally)
    assert len(be.calls[0]) == 6
    q.close(drain=False)


def test_drain_alone_is_the_workerless_path():
    """Without a worker, drain() itself flushes (the explicit/test path)."""
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    q.submit_many([_item(f"t{i}") for i in range(3)]
                  + [_item(f"i{i}", modality="image") for i in range(3)])
    assert q.drain(timeout=2.0) == 6
    assert len(be.calls) == 1 and len(be.calls[0]) == 6


def test_worker_flushes_partial_batch_on_interval():
    be = _Recorder()
    q = MultimodalIngestQueue(be, batch_size=100, flush_interval_s=0.2)
    q.submit(_item("solo"))
    deadline = time.monotonic() + 3.0
    while not be.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert be.calls, "partial batch was never flushed by the interval"
    assert [i["id"] for i in be.calls[0]] == ["solo"]
    q.close(drain=False)


def test_batch_failure_does_not_kill_worker():
    class _Boom:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, Any]]] = []

        def index_many(self, items):
            self.calls.append([dict(i) for i in items])
            raise RuntimeError("backend down")

    be = _Boom()
    q = MultimodalIngestQueue(be, batch_size=2, flush_interval_s=0.1)
    q.submit_many([_item("a"), _item("b"), _item("c")])
    flushed = q.drain(timeout=2.0)
    assert flushed == 0
    # worker still alive after the failure (daemon thread, no exit)
    assert q.stats()["running"] is True or q.stats()["closed"] is False
    assert be.calls, "the failing batch was still attempted"
    q.close(drain=False)


def test_drain_is_repeatable_and_empties():
    be = _Recorder()
    q = MultimodalIngestQueue(be, start=False)
    q.submit_many([_item("a"), _item("b")])
    assert q.drain() == 2
    assert q.drain() == 0
    assert q.pending == 0


# ── lifecycle ─────────────────────────────────────────────


def test_close_drains_by_default():
    be = _Recorder()
    q = MultimodalIngestQueue(be, batch_size=100, flush_interval_s=30.0)
    q.submit(_item("late"))
    assert q.pending == 1
    q.close(drain=True, timeout=2.0)
    assert [i["id"] for i in be.calls[0]] == ["late"]
    assert q.stats()["closed"] is True


def test_start_rejects_after_close():
    q = MultimodalIngestQueue(_Recorder(), start=False)
    q.close(drain=False)
    with pytest.raises(RuntimeError, match="closed"):
        q.start()


def test_stats_shape():
    q = MultimodalIngestQueue(_Recorder(), start=False)
    q.submit(_item("a"))
    s = q.stats()
    for key in ("running", "closed", "submitted", "batch_writes", "flushed",
                "pending", "batch_size", "flush_interval_s"):
        assert key in s, key
    assert s["batch_size"] == DEFAULT_BATCH_SIZE
    assert s["flush_interval_s"] == DEFAULT_FLUSH_INTERVAL_S
    q.close(drain=False)


# ── service-level wiring ───────────────────────────────────


def test_store_async_then_drain_is_searchable(tmp_path):
    from maop.core.memory.multimodal_store import MultimodalStore
    from maop.memory.multimodal import MultimodalMemory

    store = MultimodalStore(db_path=tmp_path / "mm.db")
    mem = MultimodalMemory(store=store)
    eid = mem.store_async("async note about deployment")
    assert eid.startswith("mm-")
    assert mem.drain_ingest() == 1
    hits = mem.retrieve("async note about deployment", top=3)
    assert hits and hits[0]["id"] == eid
    assert mem.retrieve("async note", include_text_memory=False)


def test_store_async_image_entry(tmp_path):
    from maop.core.memory.multimodal_store import MultimodalStore
    from maop.memory.multimodal import MultimodalMemory

    png = REPO / "py" / "tests" / "fixtures" / "multimodal" / "red_square.png"
    store = MultimodalStore(db_path=tmp_path / "mm.db")
    mem = MultimodalMemory(store=store)
    eid = mem.store_async(png, caption="red square")
    assert mem.drain_ingest() == 1
    assert store.stats()["modalities"]["image"] == 1
    assert store.modality_of(eid) == "image"
