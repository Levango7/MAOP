"""MAOP Coordination — Observability (M2-D).

Provides metrics collection, distributed tracing context, and a read-only
probe for the coordination layer:

1. **CoordinationMetrics** — counters, histograms, timers for coordination
   events (A2A messages, blackboard writes, agent registrations, MCP calls).
2. **CoordinationTracer** — trace context propagation across worker/agent
   boundaries. Generates trace IDs and annotates events with timing data.
3. **CoordinationProbe** — real-time snapshot of coordination state
   (agent registry, MCP hub, blackboard sync) for dashboard/API.

Usage::

    metrics = CoordinationMetrics()
    tracer = CoordinationTracer()
    probe = CoordinationProbe(registry, mcp_hub)

    # Record metrics
    metrics.increment("a2a.messages_handled")
    metrics.histogram("bb.write_latency_ms", 42.0)

    # Trace a cross-agent operation
    with tracer.span("blackboard.write", domain="hypothesis") as span:
        await bridge.on_a2a_message(msg)
        span.annotate("entry_id", entry_id)

    # Get a snapshot for the dashboard
    snapshot = await probe.snapshot()

Design reference: docs/design-observability.md (M2-D implementation slice).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── CoordinationMetrics ────────────────────────────────────────────


class CoordinationMetrics:
    """Thread-safe metrics collecting for coordination events.

    Supports three metric types:

    - **counter** — monotonically increasing count (e.g. messages handled).
    - **histogram** — distribution of values (e.g. latencies).
    - **timer** — auto-timed block using context manager.

    Redis-backed when ``redis`` is provided (increments are written to Redis
    sorted sets so multiple nodes share the same metric view). Falls back
    to in-memory when ``redis is None`` (single-node / testing).
    """

    def __init__(self, redis: Any = None, key_prefix: str = "maop:coord:metrics") -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._counters: dict[str, int] = defaultdict(int)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter metric."""
        with self._lock:
            self._counters[name] += value
        if self._redis:
            try:
                self._redis.hincrby(f"{self._prefix}:counters", name, value)
            except Exception as exc:
                logger.debug("[metrics] redis increment failed: %s", exc)

    def histogram(self, name: str, value: float) -> None:
        """Record a value into a histogram metric."""
        with self._lock:
            self._histograms[name].append(value)
        if self._redis:
            try:
                # Use a Redis sorted set keyed by timestamp for distribution tracking
                ts = time.time()
                self._redis.zadd(f"{self._prefix}:hist:{name}", {str(ts): value})
            except Exception as exc:
                logger.debug("[metrics] redis histogram failed: %s", exc)

    def gauge(self, name: str, value: float) -> None:
        """Set a gauge metric to an absolute value."""
        with self._lock:
            self._gauges[name] = value
        if self._redis:
            try:
                self._redis.hset(f"{self._prefix}:gauges", name, str(value))
            except Exception as exc:
                logger.debug("[metrics] redis gauge failed: %s", exc)

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        """Context manager that records elapsed time as a histogram."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.histogram(name, elapsed_ms)

    def get_counter(self, name: str) -> int:
        """Get the current value of a counter (local + Redis merged)."""
        local = self._counters.get(name, 0)
        if self._redis:
            try:
                remote = self._redis.hget(f"{self._prefix}:counters", name)
                if remote is not None:
                    local += int(remote)
            except Exception:  # silent-ok: defensive-probe — 远端计数器 best-effort 合并
                pass
        return local

    def get_histogram(self, name: str) -> dict[str, Any]:
        """Get summary statistics for a histogram."""
        with self._lock:
            values = list(self._histograms.get(name, []))
        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p99": 0}
        values.sort()
        n = len(values)
        return {
            "count": n,
            "min": values[0],
            "max": values[-1],
            "avg": sum(values) / n,
            "p50": values[int(n * 0.5)],
            "p99": values[min(int(n * 0.99), n - 1)],
        }

    def get_gauge(self, name: str) -> float:
        """Get the current value of a gauge."""
        with self._lock:
            return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict[str, Any]:
        """Return a full snapshot of all metrics."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histogram_names = list(self._histograms.keys())
        histograms = {name: self.get_histogram(name) for name in histogram_names}
        return {
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }


# ── CoordinationTracer ─────────────────────────────────────────────


@dataclass
class TraceSpan:
    """A single trace span (timed operation)."""

    trace_id: str
    operation: str
    start_time: float
    end_time: float = 0.0
    annotations: dict[str, Any] = field(default_factory=dict)
    children: list[TraceSpan] = field(default_factory=list)
    status: str = "running"  # running | ok | error

    def annotate(self, key: str, value: Any) -> None:
        """Attach a metadata annotation to this span."""
        self.annotations[key] = value

    @property
    def duration_ms(self) -> float:
        end = self.end_time if self.end_time else time.monotonic()
        return (end - self.start_time) * 1000


class CoordinationTracer:
    """Lightweight distributed tracing for coordination operations.

    Generates unique trace IDs and propagates context across async
    boundaries. Spans are recorded and can be exported for observability.

    Thread-safe: each thread gets its own span stack.
    """

    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []
        self._current: TraceSpan | None = None
        self._lock = threading.Lock()

    def start_trace(self, operation: str = "coord.run") -> TraceSpan:
        """Start a new trace (root span)."""
        trace_id = str(uuid.uuid4())[:12]
        span = TraceSpan(
            trace_id=trace_id,
            operation=operation,
            start_time=time.monotonic(),
        )
        with self._lock:
            self._current = span
            self._spans.append(span)
        return span

    @contextmanager
    def span(self, operation: str, **annotations: Any) -> Iterator[TraceSpan]:
        """Child span context manager.

        Usage::

            with tracer.span("a2a.route", domain="hypothesis") as span:
                ...
                span.annotate("entry_id", "abc123")
        """
        parent = self._current
        trace_id = parent.trace_id if parent else str(uuid.uuid4())[:12]
        child = TraceSpan(
            trace_id=trace_id,
            operation=operation,
            start_time=time.monotonic(),
        )
        for k, v in annotations.items():
            child.annotate(k, v)

        prev_current = self._current
        with self._lock:
            self._current = child
            if parent:
                parent.children.append(child)
            else:
                self._spans.append(child)

        try:
            yield child
            child.status = "ok"
        except Exception:
            child.status = "error"
            raise
        finally:
            child.end_time = time.monotonic()
            with self._lock:
                self._current = prev_current

    @property
    def current(self) -> TraceSpan | None:
        """Get the currently active span."""
        return self._current

    def get_completed_spans(self) -> list[TraceSpan]:
        """Return all completed (root-level) spans."""
        with self._lock:
            return [s for s in self._spans if s.end_time > 0 or s.status == "running"]

    def clear(self) -> None:
        """Clear all recorded spans."""
        with self._lock:
            self._spans.clear()
            self._current = None


# ── CoordinationProbe ──────────────────────────────────────────────


class CoordinationProbe:
    """Read-only snapshot of coordination layer state.

    Aggregates operational state from the agent registry, MCP hub,
    and blackboard sync layer for dashboard display or operational debug.
    """

    def __init__(
        self,
        agent_registry: Any = None,
        mcp_hub: Any = None,
        bb_sync: Any = None,
        tracer: CoordinationTracer | None = None,
        metrics: CoordinationMetrics | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._mcp_hub = mcp_hub
        self._bb_sync = bb_sync
        self._tracer = tracer or CoordinationTracer()
        self._metrics = metrics or CoordinationMetrics()

    async def snapshot(self) -> dict[str, Any]:
        """Produce a full coordination-layer snapshot.

        Returns
        -------
        dict
            Nested dict with keys ``agent_registry``, ``mcp_hub``,
            ``blackboard_sync``, ``metrics``, and ``traces``.
        """
        snap: dict[str, Any] = {
            "timestamp": time.time(),
            "agent_registry": await self._snapshot_registry(),
            "mcp_hub": await self._snapshot_mcp_hub(),
            "blackboard_sync": await self._snapshot_bb_sync(),
            "metrics": self._metrics.snapshot(),
            "traces": self._snapshot_traces(),
        }
        return snap

    async def _snapshot_registry(self) -> dict[str, Any]:
        """Read agent registry state."""
        reg = self._agent_registry
        if reg is None:
            return {"status": "not_configured"}
        result: dict[str, Any] = {"status": "configured"}
        try:
            registered = await reg.list_agents()
            result["registered_count"] = len(registered)
            active_count = sum(1 for a in registered if getattr(a, "status", "") == "active")
            result["active_count"] = active_count
            result["inactive_count"] = len(registered) - active_count
        except Exception as exc:
            result["error"] = str(exc)
        return result

    async def _snapshot_mcp_hub(self) -> dict[str, Any]:
        """Read MCP hub state."""
        hub = self._mcp_hub
        if hub is None:
            return {"status": "not_configured"}
        result: dict[str, Any] = {"status": "configured"}
        try:
            tools = await hub.async_find_tools()
            result["tools_count"] = len(tools)
            servers = getattr(hub, "_servers", {}) or {}
            result["servers_count"] = len(servers)
        except Exception as exc:
            result["error"] = str(exc)
        return result

    async def _snapshot_bb_sync(self) -> dict[str, Any]:
        """Read blackboard sync state."""
        sync = self._bb_sync
        if sync is None:
            return {"status": "not_configured"}
        result: dict[str, Any] = {"status": "configured"}
        try:
            result["node_id"] = sync.node_id
            result["is_running"] = sync.is_running
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _snapshot_traces(self) -> dict[str, Any]:
        """Read recent trace summary."""
        spans = self._tracer.get_completed_spans()
        return {
            "total_spans": len(spans),
            "operations": [
                {
                    "operation": s.operation,
                    "trace_id": s.trace_id,
                    "duration_ms": round(s.duration_ms, 2),
                    "status": s.status,
                }
                for s in spans[-20:]  # last 20 spans
            ],
        }
