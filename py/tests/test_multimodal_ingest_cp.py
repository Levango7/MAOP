"""Cross-process ingest queue for the multimodal space (M2.2 B2, PR-4).

Covers the cross-process transport mechanism: submit persists to the MessageQueue
DB, the worker consumes from the topic, batches, writes, and ACKs. Tests use a
real MessageQueue (SQLite) and a recorder backend — nothing touches PG here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from maop.core.memory.multimodal_ingest_cp import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONSUMER_GROUP,
    DEFAULT_FLUSH_INTERVAL_S,
    DEFAULT_TOPIC,
    CrossProcessIngestQueue,
    create_ingest_queue,
)
from maop.core.reliability.message_queue import MessageQueue

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


@pytest.fixture
def mq(tmp_path) -> MessageQueue:
    """Real MessageQueue backed by a temp SQLite DB."""
    return MessageQueue(db_path=str(tmp_path / "mq_test.db"))


# ── submission ─────────────────────────────────────────────


def test_submit_persists_to_queue(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    eid = q.submit(_item("e1"))
    assert eid == "e1"
    # The message is in the queue DB, not yet consumed.
    stats = mq.stats()
    assert stats.by_topic.get(DEFAULT_TOPIC, 0) >= 1


def test_submit_rejects_empty_id():
    be = _Recorder()
    mq = MessageQueue(db_path=":memory:")
    q = CrossProcessIngestQueue(be, mq, start=False)
    assert q.submit({"text": "no id"}) == ""


def test_submit_returns_empty_when_closed(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    q.close(drain=False)
    assert q.submit(_item("e1")) == ""


def test_submit_many_filters_unusable(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    ids = q.submit_many([_item("a"), {"text": "x"}, _item("b")])
    assert ids == ["a", "b"]


# ── draining (synchronous, no worker) ───────────────────────


def test_drain_consumes_and_writes(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    q.submit_many([_item(f"t{i}") for i in range(3)]
                  + [_item(f"i{i}", modality="image") for i in range(3)])
    flushed = q.drain(timeout=2.0)
    assert flushed == 6
    assert len(be.calls) == 1  # one batch write
    assert sum(len(c) for c in be.calls) == 6
    # Messages are ACKed after successful write.
    stats = mq.stats()
    assert stats.by_topic.get(DEFAULT_TOPIC, 0) == 0


def test_drain_batches_in_chunks(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, batch_size=2, start=False)
    q.submit_many([_item(f"e{i}") for i in range(5)])
    flushed = q.drain(timeout=2.0)
    assert flushed == 5
    # 5 items / batch_size=2 → 3 write calls (2+2+1)
    assert len(be.calls) == 3


def test_drain_returns_zero_when_empty(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    assert q.drain(timeout=0.1) == 0


# ── worker (background thread) ──────────────────────────────


def test_worker_consumes_and_batches(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, batch_size=4, flush_interval_s=0.3, start=False)
    q.submit_many([_item(f"e{i}") for i in range(4)])
    q.start()
    try:
        deadline = time.monotonic() + 3.0
        while not be.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        assert be.calls, "worker never flushed"
        assert sum(len(c) for c in be.calls) == 4
        # Messages are ACKed.
        stats = mq.stats()
        assert stats.by_topic.get(DEFAULT_TOPIC, 0) == 0
    finally:
        q.close(drain=False, timeout=2.0)


def test_close_drains_pending(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    q.submit_many([_item(f"e{i}") for i in range(3)])
    flushed = q.close(drain=True, timeout=2.0)
    assert flushed == 3
    assert len(be.calls) == 1


def test_close_idempotent(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    q.submit(_item("e1"))
    q.close(drain=True, timeout=2.0)
    second = q.close(drain=True, timeout=2.0)
    assert second == 0


# ── stats & introspection ───────────────────────────────────


def test_stats_reports_mode_and_topic(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    s = q.stats()
    assert s["mode"] == "crossprocess"
    assert s["topic"] == DEFAULT_TOPIC
    assert s["consumer_group"] == DEFAULT_CONSUMER_GROUP
    assert s["batch_size"] == DEFAULT_BATCH_SIZE
    assert s["flush_interval_s"] == DEFAULT_FLUSH_INTERVAL_S


def test_pending_reflects_queue_depth(mq: MessageQueue):
    be = _Recorder()
    q = CrossProcessIngestQueue(be, mq, start=False)
    assert q.pending == 0
    q.submit_many([_item(f"e{i}") for i in range(3)])
    assert q.pending == 3
    q.drain(timeout=2.0)
    assert q.pending == 0


# ── factory ─────────────────────────────────────────────────


def test_factory_defaults_to_inprocess(tmp_path):
    """Default mode is in-process (no settings override)."""
    from maop.config.settings import get_settings
    s = get_settings()
    # Ensure no override
    s.multimodal_ingest_mode = ""
    be = _Recorder()
    q = create_ingest_queue(be)
    try:
        assert q.stats()["mode"] == "inprocess"
    finally:
        # 工厂默认 start=True：不 close 会留下活线程（leak-probe 会抓）。
        q.close(drain=False, timeout=2.0)


def test_factory_crossprocess_mode(tmp_path):
    from maop.config.settings import get_settings
    s = get_settings()
    s.multimodal_ingest_mode = "crossprocess"
    be = _Recorder()
    mq = MessageQueue(db_path=str(tmp_path / "factory.db"))
    q = create_ingest_queue(be, mq=mq)
    try:
        assert q.stats()["mode"] == "crossprocess"
    finally:
        # worker 线程持有的 MessageQueue 在 tmp_path 清理后就打不开 DB 了 ——
        # 不 close 会在后续测试里刷 "unable to open database file" 噪声。
        q.close(drain=False, timeout=2.0)


def test_factory_crossprocess_custom_topic(tmp_path):
    from maop.config.settings import get_settings
    s = get_settings()
    s.multimodal_ingest_mode = "crossprocess"
    s.multimodal_ingest_topic = "custom_topic"
    s.multimodal_ingest_consumer_group = "custom_group"
    be = _Recorder()
    mq = MessageQueue(db_path=str(tmp_path / "factory2.db"))
    q = create_ingest_queue(be, mq=mq)
    try:
        stats = q.stats()
        assert stats["topic"] == "custom_topic"
        assert stats["consumer_group"] == "custom_group"
    finally:
        q.close(drain=False, timeout=2.0)
    # Cleanup
    s.multimodal_ingest_mode = ""
    s.multimodal_ingest_topic = ""
    s.multimodal_ingest_consumer_group = ""


# ── cross-process isolation ─────────────────────────────────


def test_two_workers_share_topic_without_conflict(tmp_path):
    """Two CrossProcessIngestQueue instances on the same MessageQueue
    but different consumer groups don't steal each other's messages."""
    mq = MessageQueue(db_path=str(tmp_path / "shared.db"))
    be1 = _Recorder()
    be2 = _Recorder()
    q1 = CrossProcessIngestQueue(be1, mq, consumer_group="worker-1", start=False)
    q2 = CrossProcessIngestQueue(be2, mq, consumer_group="worker-2", start=False)

    # Both submit to the same topic.
    q1.submit(_item("e1"))
    q2.submit(_item("e2"))
    assert q1.pending == 2  # both messages are in the topic
    assert q2.pending == 2

    # Drain with worker-1 only picks up messages (consumer group isolation
    flushed1 = q1.drain(timeout=1.0)
    assert flushed1 == 2  # worker-1 consumes all pending
    assert be1.calls  # worker-1 wrote
    # Worker-2's drain sees nothing (already consumed by worker-1).
    flushed2 = q2.drain(timeout=0.5)
    assert flushed2 == 0
