"""DAG execution progress event emitter.

Provides ``NodeStatusEvent`` (Pydantic model) and ``DagProgressEmitter``
which encapsulates EventBus publishing of DAG node-status events for
real-time streaming to SSE/WebSocket subscribers.

Event topics (per execution_id):
    - ``dag.node-status.{execution_id}``      — node status change events
    - ``dag.execution-complete.{execution_id}`` — orchestration complete event

The emitter maintains a monotonically increasing ``seq`` counter per
instance so that SSE clients can resume via ``Last-Event-ID``.

Publishing is fire-and-forget (``publish_sync`` schedules the coroutine
on the running loop without awaiting), so emitter calls never block
the orchestration main flow. This is critical for the P95 < 200ms
latency target (spec 4.1.1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from maop.core.reliability.event_bus import EventBus

logger = logging.getLogger(__name__)


# ── Status enum ────────────────────────────────────────────────


class NodeStatus(str, Enum):
    """Lifecycle status of a DAG node during orchestration.

    Values match spec 5.2.1 rule 2:
        pending  — queued, not yet started
        running  — currently executing
        success  — completed successfully
        failed   — completed with error
        skipped  — bypassed (e.g. dependency failed)
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Event model ───────────────────────────────────────────────


class NodeStatusEvent(BaseModel):
    """A single DAG node status change event.

    Public payload (serialized to frontend) — spec 5.2.1 rule 2
    requires exactly four fields: node_id, status, timestamp, metadata.

    Internal fields (``execution_id``, ``seq``) are not part of the
    public payload but are carried in ``metadata`` for SSE
    ``Last-Event-ID`` resumption and topic routing.
    """

    node_id: str
    status: NodeStatus
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Internal fields (not serialized to frontend payload directly;
    # included in metadata for downstream consumers / SSE resumption).
    execution_id: str = ""
    seq: int = 0

    def public_payload(self) -> dict[str, Any]:
        """Return the 4-field public payload for SSE/WS clients.

        Per spec 5.2.1 rule 2, the wire format is exactly::
            {"node_id", "status", "timestamp", "metadata"}
        ``seq`` is carried via the SSE ``id:`` line (not in data),
        and ``execution_id`` is implicit in the topic/URL.
        """
        return {
            "node_id": self.node_id,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            # seq included for client-side ordering / Last-Event-ID echo
            "seq": self.seq,
        }


# ── Emitter ───────────────────────────────────────────────────


class DagProgressEmitter:
    """DAG node-status event emitter.

    Wraps EventBus publishing so that ``MaopLoop`` / ``ExecuteMixin``
    can emit node-status events without coupling to EventBus details.

    Construction::

        emitter = DagProgressEmitter(bus, execution_id=trace_id)
        emitter.emit_pending("n1")
        emitter.emit_running("n1", assigned_agent="claude")
        emitter.emit_success("n1", duration_ms=120)
        emitter.emit_execution_complete()

    All emit methods are fire-and-forget — they schedule the EventBus
    publish on the running loop without awaiting, so they never block
    the orchestration main flow (spec 4.1.1, design 2.3.1.3).
    """

    def __init__(self, bus: EventBus, execution_id: str) -> None:
        self._bus = bus
        self._execution_id = execution_id
        self._seq = 0
        # Track node states for already-completed detection (WS cancel
        # conflict — spec 5.2.3 anomaly 3).
        self._node_states: dict[str, NodeStatus] = {}
        # Auto-register in the global registry so the WebSocket endpoint
        # can look up this emitter for cancel/pause conflict detection.
        _register_emitter(execution_id, self)

    @property
    def execution_id(self) -> str:
        """The execution_id (trace_id) this emitter is scoped to."""
        return self._execution_id

    @property
    def node_states(self) -> dict[str, NodeStatus]:
        """Current snapshot of node → status (for cancel-conflict checks)."""
        return dict(self._node_states)

    # ── Topic helpers ────────────────────────────────────────

    def _node_topic(self) -> str:
        return f"dag.node-status.{self._execution_id}"

    def _complete_topic(self) -> str:
        return f"dag.execution-complete.{self._execution_id}"

    # ── Core publish ─────────────────────────────────────────

    def _next_seq(self) -> int:
        """Return the next monotonic sequence number."""
        self._seq += 1
        return self._seq

    def _emit(self, event: NodeStatusEvent) -> None:
        """Publish a NodeStatusEvent to the EventBus (fire-and-forget).

        Records the node state for cancel-conflict detection and
        schedules the EventBus publish without awaiting. If no event
        loop is running, falls back to synchronous publish.
        """
        self._node_states[event.node_id] = event.status
        try:
            from maop.core.reliability.event_bus import Event

            bus_event = Event(
                topic=self._node_topic(),
                data=event.public_payload(),
            )
            # publish_sync is fire-and-forget when a loop is running
            # (it uses asyncio.ensure_future and does not block).
            self._bus.publish_sync(bus_event)
        except Exception:
            # Emitter must never break orchestration — log and swallow.
            logger.debug(
                "DagProgressEmitter publish failed for node %s status %s",
                event.node_id, event.status, exc_info=True,
            )

    # ── Status emitters ──────────────────────────────────────

    def emit_pending(self, node_id: str) -> NodeStatusEvent:
        """Emit a ``pending`` status event for a node."""
        evt = NodeStatusEvent(
            node_id=node_id,
            status=NodeStatus.PENDING,
            execution_id=self._execution_id,
            seq=self._next_seq(),
        )
        self._emit(evt)
        return evt

    def emit_running(
        self, node_id: str, assigned_agent: str = ""
    ) -> NodeStatusEvent:
        """Emit a ``running`` status event for a node."""
        metadata: dict[str, Any] = {}
        if assigned_agent:
            metadata["assigned_agent"] = assigned_agent
        evt = NodeStatusEvent(
            node_id=node_id,
            status=NodeStatus.RUNNING,
            execution_id=self._execution_id,
            seq=self._next_seq(),
            metadata=metadata,
        )
        self._emit(evt)
        return evt

    def emit_success(
        self, node_id: str, duration_ms: int = 0
    ) -> NodeStatusEvent:
        """Emit a ``success`` status event for a node."""
        metadata: dict[str, Any] = {}
        if duration_ms:
            metadata["duration_ms"] = duration_ms
        evt = NodeStatusEvent(
            node_id=node_id,
            status=NodeStatus.SUCCESS,
            execution_id=self._execution_id,
            seq=self._next_seq(),
            metadata=metadata,
        )
        self._emit(evt)
        return evt

    def emit_failed(
        self,
        node_id: str,
        error: str = "",
        traceback: str = "",
    ) -> NodeStatusEvent:
        """Emit a ``failed`` status event for a node.

        ``error`` is truncated to 200 chars and ``traceback`` to 2000
        chars in metadata to keep event payloads lean (design 2.3.6).
        """
        metadata: dict[str, Any] = {}
        if error:
            metadata["error"] = error[:200]
        if traceback:
            metadata["traceback"] = traceback[:2000]
        evt = NodeStatusEvent(
            node_id=node_id,
            status=NodeStatus.FAILED,
            execution_id=self._execution_id,
            seq=self._next_seq(),
            metadata=metadata,
        )
        self._emit(evt)
        return evt

    def emit_skipped(
        self, node_id: str, reason: str = ""
    ) -> NodeStatusEvent:
        """Emit a ``skipped`` status event for a node."""
        metadata: dict[str, Any] = {}
        if reason:
            metadata["reason"] = reason
        evt = NodeStatusEvent(
            node_id=node_id,
            status=NodeStatus.SKIPPED,
            execution_id=self._execution_id,
            seq=self._next_seq(),
            metadata=metadata,
        )
        self._emit(evt)
        return evt

    def emit_execution_complete(self) -> None:
        """Emit the ``execution-complete`` event and close the emitter.

        After this call, subscribers receive a final
        ``dag.execution-complete.{execution_id}`` event and the SSE/WS
        endpoints close their connections (spec 5.2.1 rule 11).
        """
        try:
            from maop.core.reliability.event_bus import Event

            bus_event = Event(
                topic=self._complete_topic(),
                data={
                    "execution_id": self._execution_id,
                    "seq": self._next_seq(),
                    "completed": len(self._node_states),
                    "success_count": sum(
                        1 for s in self._node_states.values()
                        if s == NodeStatus.SUCCESS
                    ),
                    "failed_count": sum(
                        1 for s in self._node_states.values()
                        if s == NodeStatus.FAILED
                    ),
                    "skipped_count": sum(
                        1 for s in self._node_states.values()
                        if s == NodeStatus.SKIPPED
                    ),
                },
            )
            self._bus.publish_sync(bus_event)
        except Exception:
            logger.debug(
                "DagProgressEmitter execution-complete publish failed",
                exc_info=True,
            )
        # Unregister from the global registry (execution finished).
        _unregister_emitter(self._execution_id)

    # ── Query ────────────────────────────────────────────────

    def get_history(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent node-status events from the EventBus history.

        Used by SSE endpoint for ``Last-Event-ID`` resumption.
        Returns public payloads (dicts), not Event objects.
        """
        events = self._bus.get_history(topic=self._node_topic(), limit=limit)
        return [e.data for e in events]

    def is_node_completed(self, node_id: str) -> bool:
        """Check if a node has reached a terminal state.

        Used by the WebSocket cancel handler to detect the
        ``already_completed`` conflict (spec 5.2.3 anomaly 3).
        """
        state = self._node_states.get(node_id)
        return state in (NodeStatus.SUCCESS, NodeStatus.FAILED, NodeStatus.SKIPPED)


# ── Global emitter registry ───────────────────────────────────
# Maps execution_id → DagProgressEmitter so the WebSocket endpoint
# can look up the emitter for a given execution to check node states
# (cancel/pause conflict detection — spec 5.2.3 anomaly 3).
# Emitters auto-register on construction and unregister on
# emit_execution_complete().

_emitter_registry: dict[str, DagProgressEmitter] = {}


def _register_emitter(execution_id: str, emitter: DagProgressEmitter) -> None:
    """Register an emitter in the global registry (internal)."""
    _emitter_registry[execution_id] = emitter


def _unregister_emitter(execution_id: str) -> None:
    """Unregister an emitter from the global registry (internal)."""
    _emitter_registry.pop(execution_id, None)


def get_emitter(execution_id: str) -> DagProgressEmitter | None:
    """Look up a live DagProgressEmitter by execution_id.

    Returns None if no execution is in progress for the given id.
    Used by the WebSocket cancel/pause handler.
    """
    return _emitter_registry.get(execution_id)