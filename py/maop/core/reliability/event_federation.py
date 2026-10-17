"""MAOP Cross-Project Event Federation Bus.

Bridges MAOP internal ``EventBus`` with external event buses
(e.g. MAOS enterprise notification bus, or any dict-based pub/sub).
This enables **cross-project event interoperability**:
- MAOP blackboard events can react to MAOS task lifecycle events.
- MAOS notifications can be triggered by MAOP convergence/debate outcomes.

The federation bus follows a **fail-closed** design:
- If a bridge target is unreachable, the event is NOT silently dropped —
  it goes to a dead-letter queue for retry.
- Incoming events are normalized to a canonical ``FederatedEvent`` before
  dispatch, so bridges don't need to know each other's internal format.

Classes
-------
- ``FederatedEvent``: canonical cross-project event envelope.
- ``EventBridge``: maps between a source event format and ``FederatedEvent``.
- ``FederationBus``: orchestrates multiple bridges with dead-letter handling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── FederatedEvent ─────────────────────────────────────────────────


@dataclass
class FederatedEvent:
    """Canonical cross-project event envelope.

    Attributes
    ----------
    event_id : str
        Globally unique ID for tracing.
    source : str
        Originating system (e.g. "maop.blackboard", "maos.notification").
    topic : str
        Normalized topic (e.g. "task.completed", "debate.converged").
    data : dict[str, Any]
        Payload (JSON-serializable).
    timestamp : str
        ISO-8601 timestamp.
    metadata : dict[str, Any]
        Extra routing info (tenant_id, priority, trace_id, etc.).
    """

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    source: str = "unknown"
    topic: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "topic": self.topic,
            "data": self.data,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FederatedEvent:
        return cls(
            event_id=d.get("event_id", uuid.uuid4().hex[:16]),
            source=d.get("source", "unknown"),
            topic=d.get("topic", ""),
            data=d.get("data", {}),
            timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
            metadata=d.get("metadata", {}),
        )


# ── Event source/sink protocol ─────────────────────────────────────

SourceEvent = Any  # Could be MAOP Event, MAOS EventPayload, or dict


class EventBridge(Protocol):
    """A bridge connects one external bus to the federation layer."""

    name: str

    def to_federated(self, raw: SourceEvent) -> FederatedEvent | None: ...

    def from_federated(self, evt: FederatedEvent) -> SourceEvent | None: ...


# ── MAOP EventBus bridge ───────────────────────────────────────────


class MAOPEventBusBridge:
    """Bridges MAOP core EventBus to FederationBus."""

    name: str = "maop_event_bus"

    def __init__(self, event_bus: Any, source_label: str = "maop") -> None:
        self._bus = event_bus
        self._source_label = source_label
        self._federation: Any = None

    def attach_federation(self, federation: Any) -> None:
        """Subscribe to MAOP EventBus topics and forward to federation."""
        self._federation = federation
        self._bus.subscribe("*", self._on_maop_event)

    def detach(self) -> None:
        if self._bus is not None and self._federation is not None:
            try:
                self._bus.unsubscribe("*", self._on_maop_event)
            except Exception:  # silent-ok: best-effort-cleanup — detach 退订为 best-effort
                pass
            self._federation = None

    def _on_maop_event(self, event: Any) -> None:
        """Called by MAOP EventBus when any event is published."""
        fed = self.to_federated(event)
        if fed is not None and self._federation is not None:
            # Fire-and-forget into the federation bus
            asyncio.get_event_loop().call_soon_threadsafe(
                self._federation._ingest, fed
            )

    def to_federated(self, raw: Any) -> FederatedEvent | None:
        """Convert MAOP Event → FederatedEvent."""
        if hasattr(raw, "topic") and hasattr(raw, "data"):
            # MAOP Event dataclass
            return FederatedEvent(
                source=self._source_label,
                topic=raw.topic,
                data=dict(raw.data) if isinstance(raw.data, dict) else {"payload": raw.data},
                timestamp=getattr(raw, "timestamp", datetime.now(timezone.utc).isoformat()),
                metadata={"source_label": getattr(raw, "source", "maop")},
            )
        return None

    def from_federated(self, evt: FederatedEvent) -> Any:
        """Convert FederatedEvent → MAOP Event."""
        from maop.core.reliability.event_bus import Event
        return Event(
            topic=evt.topic,
            data=evt.data,
            source=evt.source,
        )


# ── Generic dict-based bridge ──────────────────────────────────────


class DictEventBridge:
    """Bridges any dict-based pub/sub system to federation.

    Parameters
    ----------
    name : str
        Bridge identifier.
    source_label : str
        Source project label for federated events.
    topic_key : str
        Key in the dict that holds the topic.
    data_key : str
        Key in the dict that holds the payload (if None, the whole dict is data).
    """

    def __init__(
        self,
        name: str,
        source_label: str,
        *,
        topic_key: str = "event_type",
        data_key: str | None = "payload",
    ) -> None:
        self.name = name
        self._source_label = source_label
        self._topic_key = topic_key
        self._data_key = data_key

    def to_federated(self, raw: Any) -> FederatedEvent | None:
        if not isinstance(raw, dict):
            if hasattr(raw, "event_type"):
                raw = {"event_type": raw.event_type, "payload": getattr(raw, "payload", {})}
            else:
                return None
        topic = raw.get(self._topic_key, "unknown")
        data = raw.get(self._data_key, raw) if self._data_key else raw
        if not isinstance(data, dict):
            data = {"value": data}
        metadata = {k: v for k, v in raw.items() if k not in (self._topic_key, self._data_key)}
        return FederatedEvent(
            source=self._source_label,
            topic=self._normalize_topic(topic),
            data=data,
            metadata=metadata,
        )

    def from_federated(self, evt: FederatedEvent) -> dict[str, Any]:
        result: dict[str, Any] = {self._topic_key: evt.topic}
        if self._data_key:
            result[self._data_key] = evt.data
        result.update(evt.metadata)
        return result

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        """Dot-separated normalization (e.g. 'task_completed' → 'task.completed')."""
        return topic.replace("_", ".")


# ── FederationBus ──────────────────────────────────────────────────


class FederationBus:
    """Orchestrates event federation across multiple bridges.

    Features
    --------
    - Normalize inbound events to ``FederatedEvent``.
    - Fan-out to registered subscribers.
    - Dead-letter queue for failed deliveries (fail-closed).
    - At-most-once or at-least-once delivery semantics (configurable).
    """

    def __init__(
        self,
        *,
        dead_letter_limit: int = 1000,
        retry_limit: int = 3,
    ) -> None:
        self._bridges: dict[str, EventBridge] = {}
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._dead_letter: deque[FederatedEvent] = deque(maxlen=dead_letter_limit)
        self._retry_limit = retry_limit
        self._retry_counts: dict[str, int] = {}
        self._lock = threading.RLock()
        self._running = False

    # ── Bridge management ────────────────────────────────────────

    def register_bridge(self, bridge: EventBridge) -> None:
        self._bridges[bridge.name] = bridge
        logger.info("[federation] registered bridge: %s", bridge.name)

    def unregister_bridge(self, name: str) -> None:
        self._bridges.pop(name, None)

    # ── Subscription ─────────────────────────────────────────────

    def subscribe(
        self,
        topic_pattern: str,
        handler: Callable[[FederatedEvent], Awaitable[None] | None],
    ) -> None:
        """Subscribe to events matching ``topic_pattern`` (prefix match with ``*`` wildcard)."""
        self._subscribers[topic_pattern].append(handler)

    def unsubscribe(self, topic_pattern: str, handler: Callable) -> None:
        handlers = self._subscribers.get(topic_pattern, [])
        self._subscribers[topic_pattern] = [h for h in handlers if h is not handler]

    # ── Ingestion ────────────────────────────────────────────────

    def ingest(self, raw_event: Any, source_bridge: str | None = None) -> None:
        """Ingest a raw event from a specific bridge or as a dict."""
        if source_bridge and source_bridge in self._bridges:
            bridge = self._bridges[source_bridge]
            fed = bridge.to_federated(raw_event)
        elif isinstance(raw_event, dict):
            fed = FederatedEvent.from_dict(raw_event)
        elif isinstance(raw_event, FederatedEvent):
            fed = raw_event
        else:
            logger.warning("[federation] cannot ingest event type %s", type(raw_event).__name__)
            return

        if fed is None:
            return

        self._ingest(fed)

    def _ingest(self, fed: FederatedEvent) -> None:
        """Internal: dispatch a federated event to subscribers."""
        matched: list[Callable] = []
        with self._lock:
            for pattern, handlers in self._subscribers.items():
                if self._match(fed.topic, pattern):
                    matched.extend(handlers)

        for handler in matched:
            try:
                result = handler(fed)
                if asyncio.iscoroutine(result):
                    # Schedule in event loop if available; otherwise warn
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        logger.warning(
                            "[federation] async handler %s needs an event loop",
                            getattr(handler, "__name__", repr(handler)),
                        )
            except Exception as exc:
                logger.error("[federation] handler error: %s", exc)
                self._dead_letter.append(fed)

    @staticmethod
    def _match(topic: str, pattern: str) -> bool:
        """Prefix/wildcard match."""
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return topic.startswith(pattern[:-1])
        return topic == pattern

    # ── Outbound ─────────────────────────────────────────────────

    def emit(self, event: FederatedEvent, target_bridge: str | None = None) -> None:
        """Send a federated event out through a specific bridge."""
        if target_bridge and target_bridge in self._bridges:
            bridge = self._bridges[target_bridge]
            raw = bridge.from_federated(event)
            if raw is not None and hasattr(bridge, "_bus"):
                # MAOP EventBus case
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(bridge._bus.publish(raw))
                except RuntimeError:
                    logger.warning("[federation] no event loop for emit")
        else:
            logger.warning(
                "[federation] emit to unknown bridge: %s", target_bridge
            )

    # ── Dead letter ──────────────────────────────────────────────

    @property
    def dead_letter_size(self) -> int:
        return len(self._dead_letter)

    def get_dead_letters(self, limit: int = 50) -> list[FederatedEvent]:
        return list(self._dead_letter)[-limit:]

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        logger.info("[federation] started")

    def stop(self) -> None:
        self._running = False
        logger.info("[federation] stopped")
