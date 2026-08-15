"""Unit tests for DagProgressEmitter and NodeStatusEvent (v4.5.0 T14).

Covers:
  - NodeStatusEvent model validation (4-field public payload)
  - DagProgressEmitter emit_pending/running/success/failed/skipped
  - seq monotonic increment
  - emit_failed cascade skipped (via loop_executor helper)
  - fire-and-forget (publish does not block)
  - global emitter registry (get_emitter)
"""

from __future__ import annotations

import asyncio

import pytest

from maop.core.agent.dag.dag_progress_emitter import (
    DagProgressEmitter,
    NodeStatus,
    NodeStatusEvent,
    get_emitter,
)
from maop.core.reliability.event_bus import Event, EventBus


class TestNodeStatusEvent:
    """NodeStatusEvent model validation (spec 5.2.1 rule 2)."""

    def test_status_enum_values(self):
        """status 取值 ∈ {pending, running, success, failed, skipped}."""
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.SUCCESS.value == "success"
        assert NodeStatus.FAILED.value == "failed"
        assert NodeStatus.SKIPPED.value == "skipped"

    def test_event_default_fields(self):
        """NodeStatusEvent has node_id, status, timestamp, metadata."""
        evt = NodeStatusEvent(node_id="n1", status=NodeStatus.PENDING)
        assert evt.node_id == "n1"
        assert evt.status == NodeStatus.PENDING
        assert evt.timestamp  # auto-generated
        assert evt.metadata == {}  # default empty dict
        assert evt.execution_id == ""
        assert evt.seq == 0

    def test_public_payload_has_4_core_fields(self):
        """public_payload returns exactly the 4 spec fields + seq."""
        evt = NodeStatusEvent(
            node_id="n1",
            status=NodeStatus.RUNNING,
            metadata={"assigned_agent": "claude"},
            execution_id="exec-1",
            seq=5,
        )
        payload = evt.public_payload()
        # spec 5.2.1 rule 2: node_id, status, timestamp, metadata
        assert "node_id" in payload
        assert "status" in payload
        assert "timestamp" in payload
        assert "metadata" in payload
        assert payload["node_id"] == "n1"
        assert payload["status"] == "running"
        assert payload["metadata"]["assigned_agent"] == "claude"
        # seq is included for SSE Last-Event-ID echo
        assert payload["seq"] == 5

    def test_status_must_be_valid_enum(self):
        """status field rejects invalid values."""
        with pytest.raises(Exception):  # noqa: B017
            NodeStatusEvent(node_id="n1", status="invalid_status")


class TestDagProgressEmitter:
    """DagProgressEmitter event publishing."""

    @pytest.mark.asyncio
    async def test_emit_pending_publishes_event(self):
        """emit_pending publishes a pending status event to EventBus."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.node-status.exec-1", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-1")
        evt = emitter.emit_pending("n1")

        assert evt.node_id == "n1"
        assert evt.status == NodeStatus.PENDING
        assert evt.seq == 1  # first event
        # Allow fire-and-forget task to complete.
        await asyncio.sleep(0.05)
        assert len(received) >= 1
        assert received[0].data["node_id"] == "n1"
        assert received[0].data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_emit_running_with_agent(self):
        """emit_running publishes running status with assigned_agent metadata."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.node-status.exec-2", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-2")
        emitter.emit_running("n1", assigned_agent="claude")

        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].data["status"] == "running"
        assert received[0].data["metadata"]["assigned_agent"] == "claude"

    @pytest.mark.asyncio
    async def test_emit_success_with_duration(self):
        """emit_success publishes success status with duration_ms."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.node-status.exec-3", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-3")
        emitter.emit_success("n1", duration_ms=150)

        await asyncio.sleep(0.05)
        assert received[0].data["status"] == "success"
        assert received[0].data["metadata"]["duration_ms"] == 150

    @pytest.mark.asyncio
    async def test_emit_failed_with_error(self):
        """emit_failed publishes failed status with truncated error."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.node-status.exec-4", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-4")
        long_error = "x" * 500
        emitter.emit_failed("n1", error=long_error)

        await asyncio.sleep(0.05)
        assert received[0].data["status"] == "failed"
        # error truncated to 200 chars
        assert len(received[0].data["metadata"]["error"]) == 200

    @pytest.mark.asyncio
    async def test_emit_skipped_with_reason(self):
        """emit_skipped publishes skipped status with reason."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.node-status.exec-5", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-5")
        emitter.emit_skipped("n2", reason="dependency n1 failed")

        await asyncio.sleep(0.05)
        assert received[0].data["status"] == "skipped"
        assert received[0].data["metadata"]["reason"] == "dependency n1 failed"

    @pytest.mark.asyncio
    async def test_seq_monotonically_increasing(self):
        """事件 seq 单调递增."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-6")

        e1 = emitter.emit_pending("n1")
        e2 = emitter.emit_running("n1")
        e3 = emitter.emit_success("n1")
        e4 = emitter.emit_pending("n2")
        e5 = emitter.emit_failed("n2")

        seqs = [e1.seq, e2.seq, e3.seq, e4.seq, e5.seq]
        assert seqs == [1, 2, 3, 4, 5]
        # Strictly increasing
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1]

    @pytest.mark.asyncio
    async def test_emit_execution_complete_publishes_and_unregisters(self):
        """emit_execution_complete publishes complete event and unregisters."""
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("dag.execution-complete.exec-7", lambda e: received.append(e))

        emitter = DagProgressEmitter(bus, execution_id="exec-7")
        emitter.emit_pending("n1")
        emitter.emit_success("n1")
        emitter.emit_execution_complete()

        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].data["success_count"] == 1
        # Emitter should be unregistered after complete.
        assert get_emitter("exec-7") is None

    @pytest.mark.asyncio
    async def test_fire_and_forget_does_not_block(self):
        """emitter 发布不阻塞编排执行（fire-and-forget）."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-8")

        # Emit many events rapidly — should return near-instantly
        # since publish_sync is fire-and-forget.
        import time
        start = time.monotonic()
        for i in range(100):
            emitter.emit_pending(f"n{i}")
        elapsed = time.monotonic() - start

        # 100 emits should take < 0.5s (fire-and-forget, no awaiting).
        assert elapsed < 0.5, f"emit was too slow: {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_get_history_returns_public_payloads(self):
        """get_history returns recent node-status events."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-9")
        emitter.emit_pending("n1")
        emitter.emit_running("n1")

        await asyncio.sleep(0.05)
        history = emitter.get_history(limit=10)
        assert len(history) >= 2
        assert history[0]["node_id"] == "n1"
        assert history[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_is_node_completed(self):
        """is_node_completed detects terminal states."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-10")
        emitter.emit_pending("n1")
        assert not emitter.is_node_completed("n1")

        emitter.emit_success("n1")
        assert emitter.is_node_completed("n1")

        emitter.emit_running("n2")
        assert not emitter.is_node_completed("n2")

        emitter.emit_failed("n2")
        assert emitter.is_node_completed("n2")

    @pytest.mark.asyncio
    async def test_node_states_snapshot(self):
        """node_states property returns current state snapshot."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-11")
        emitter.emit_pending("n1")
        emitter.emit_running("n2")
        emitter.emit_success("n3")

        states = emitter.node_states
        assert states["n1"] == NodeStatus.PENDING
        assert states["n2"] == NodeStatus.RUNNING
        assert states["n3"] == NodeStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_emitter_auto_registers_in_global_registry(self):
        """Emitter auto-registers so WS endpoint can find it."""
        bus = EventBus()
        emitter = DagProgressEmitter(bus, execution_id="exec-12")
        assert get_emitter("exec-12") is emitter
        emitter.emit_execution_complete()
        assert get_emitter("exec-12") is None


class TestGetDownstreamHelper:
    """Test the _get_downstream method on ExecuteMixin."""

    def test_get_downstream_traverses_edges(self):
        """_get_downstream returns transitive successors."""
        from maop.core.agent.analyzer import DependencyDAG
        from maop.loop_executor import ExecuteMixin

        # n1 → n2 → n3, n1 → n4
        dag = DependencyDAG(
            nodes=["n1", "n2", "n3", "n4"],
            edges=[("n1", "n2"), ("n2", "n3"), ("n1", "n4")],
        )
        downstream = ExecuteMixin()._get_downstream("n1", dag)
        assert set(downstream) == {"n2", "n3", "n4"}

    def test_get_downstream_no_successors(self):
        """_get_downstream returns empty for leaf node."""
        from maop.core.agent.analyzer import DependencyDAG
        from maop.loop_executor import ExecuteMixin

        dag = DependencyDAG(
            nodes=["n1", "n2"],
            edges=[("n1", "n2")],
        )
        assert ExecuteMixin()._get_downstream("n2", dag) == []

    def test_get_downstream_empty_dag(self):
        """_get_downstream handles empty DAG gracefully."""
        from maop.core.agent.analyzer import DependencyDAG
        from maop.loop_executor import ExecuteMixin

        dag = DependencyDAG(nodes=[], edges=[])
        assert ExecuteMixin()._get_downstream("n1", dag) == []