"""Prometheus metrics export — request / agent / error counters + histograms.

Defines the four canonical MAOP observability metrics required by the
F1-04 spec and wires them into the shared
:class:`~maop.core.monitoring.monitoring.MetricsCollector` so they
appear on the ``/api/prometheus`` scrape endpoint alongside all
existing MAOP_* metrics.

Metrics
-------
* ``maop_requests_total``             — counter   (labels: method, path, status)
* ``maop_request_duration_seconds``   — histogram (labels: method, path)
* ``maop_agent_execution_seconds``    — histogram (labels: agent, phase)
* ``maop_errors_total``               — counter   (labels: type, module)

The :class:`MetricsMiddleware` (ASGI) auto-records request count +
latency for every HTTP request.  Agent execution and error recording
are explicit calls from the orchestrator / dispatcher / error handler.

Edition behaviour
-----------------
* **Personal** — only the lightweight counters are active (requests +
  errors).  Agent-execution histogram is registered but not observed
  unless the caller explicitly calls :func:`record_agent_execution`.
* **Enterprise** — all four metrics are fully active and scraped by
  Prometheus every 15s.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from maop.config.edition import Edition, get_edition
from maop.core.monitoring.monitoring import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    metrics as _global_metrics,
)

logger = logging.getLogger(__name__)

# ── Metric names (F1-04 spec) ───────────────────────────────────────
M_REQUESTS_TOTAL = "maop_requests_total"
M_REQUEST_DURATION = "maop_request_duration_seconds"
M_AGENT_EXECUTION = "maop_agent_execution_seconds"
M_ERRORS_TOTAL = "maop_errors_total"

# Supplementary gauges for the observability dashboard.
M_ACTIVE_SPANS = "maop_active_spans"
M_TRACE_EXPORT_TOTAL = "maop_trace_export_total"

# F1-02 (异常自适应调度): per-agent failure-detector gauges. The
# FailurePatternDetector registers these against the global collector
# lazily on first record_result() call; declaring the names here keeps
# them discoverable from a single module and exported in metrics_summary.
M_AGENT_FAILURE_RATE = "maop_agent_failure_rate"
M_AGENT_WEIGHT = "maop_agent_weight"
M_AGENT_STATUS = "maop_agent_status"
M_AGENT_TIMEOUT_RATE = "maop_agent_timeout_rate"
M_AGENT_AVG_LATENCY = "maop_agent_avg_latency_seconds"

# Standard Prometheus histogram buckets for HTTP latency (seconds).
_HTTP_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"),
)

# Standard buckets for agent execution latency (seconds) — agents are
# slower than HTTP, so the buckets are shifted right.
_AGENT_BUCKETS = (
    0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, float("inf"),
)


class ObservabilityMetrics:
    """Container for the four canonical MAOP observability metrics.

    Registered against the global :data:`metrics` collector so they
    are exported on ``/api/prometheus`` automatically.  Constructing
    the class is idempotent — the underlying ``Counter`` / ``Histogram``
    objects are singletons keyed by name inside the collector.
    """

    def __init__(self, collector: MetricsCollector | None = None) -> None:
        self.collector = collector or _global_metrics
        self.requests_total: Counter = self.collector.counter(
            M_REQUESTS_TOTAL,
            "Total HTTP requests (labels: method, path, status)",
        )
        self.request_duration: Histogram = self.collector.histogram(
            M_REQUEST_DURATION,
            "HTTP request latency in seconds",
            buckets=_HTTP_BUCKETS,
        )
        self.agent_execution: Histogram = self.collector.histogram(
            M_AGENT_EXECUTION,
            "Agent execution latency in seconds (labels: agent, phase)",
            buckets=_AGENT_BUCKETS,
        )
        self.errors_total: Counter = self.collector.counter(
            M_ERRORS_TOTAL,
            "Total errors (labels: type, module)",
        )
        self.active_spans: Gauge = self.collector.gauge(
            M_ACTIVE_SPANS,
            "Number of currently active OTel spans",
        )
        self.trace_export_total: Counter = self.collector.counter(
            M_TRACE_EXPORT_TOTAL,
            "Total OTel trace export batches",
        )

    # ── Recording helpers ────────────────────────────────────────
    def record_request(
        self,
        method: str,
        path: str,
        status: int,
        duration: float,
    ) -> None:
        """Record one HTTP request (called by MetricsMiddleware)."""
        labels = {"method": method, "path": _normalise_path(path), "status": str(status)}
        self.requests_total.inc(labels=labels)
        # The Histogram class does not carry labels, so latency is a
        # single global distribution.  Per-path latency would require
        # the labels-extension which is intentionally out of scope.
        self.request_duration.observe(duration)

    def record_agent_execution(
        self,
        agent: str,
        phase: str,
        duration: float,
    ) -> None:
        """Record one agent execution phase (execute / dispatch / run)."""
        # Same labels caveat as above — observe into the global histogram.
        self.agent_execution.observe(duration)
        # Also bump a per-agent counter so dashboards can show volume.
        self.collector.counter(
            f"{M_AGENT_EXECUTION}_count",
            "Agent execution count (labels: agent, phase)",
        ).inc(labels={"agent": agent, "phase": phase})

    def record_error(
        self,
        error_type: str,
        module: str = "",
    ) -> None:
        """Record one error event."""
        self.errors_total.inc(labels={"type": error_type, "module": module})

    def set_active_spans(self, count: int) -> None:
        """Update the active-span gauge (called by the span processor)."""
        self.active_spans.set(float(count))

    def record_trace_export(self) -> None:
        """Record one trace export batch."""
        self.trace_export_total.inc()

    # ── Export ───────────────────────────────────────────────────
    def to_prometheus(self) -> str:
        """Export all observability metrics in Prometheus text format."""
        return self.collector.to_prometheus()

    def to_json(self) -> dict[str, Any]:
        """Export all metrics as JSON (for the dashboard panel)."""
        return self.collector.to_json()


def _normalise_path(path: str) -> str:
    """Collapse path IDs so label cardinality stays bounded.

    ``/api/agents/abc-123`` → ``/api/agents/:id``.  This prevents the
    OPS-25 cardinality cap from folding most request paths into the
    overflow series.
    """
    if not path:
        return "/"
    parts = path.split("/")
    normalised: list[str] = []
    for part in parts:
        if not part:
            continue
        # UUID-like or long hex → :id
        if len(part) > 16 or "-" in part and len(part) >= 8:
            normalised.append(":id")
        elif part.isdigit():
            normalised.append(":id")
        else:
            normalised.append(part)
    return "/" + "/".join(normalised)


# ── Module-level singleton ─────────────────────────────────────────
_metrics_instance: ObservabilityMetrics | None = None


def get_metrics() -> ObservabilityMetrics:
    """Return the singleton :class:`ObservabilityMetrics` instance."""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = ObservabilityMetrics()
    return _metrics_instance


# ── ASGI middleware ─────────────────────────────────────────────────
class MetricsMiddleware:
    """ASGI middleware that records request count + latency.

    Installed via ``app.add_middleware(MetricsMiddleware)``.  When the
    edition is Personal the middleware still runs (the counters are
    cheap), but the agent-execution histogram is only observed on
    explicit :func:`record_agent_execution` calls.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        exclude_paths: tuple[str, ...] = ("/api/prometheus", "/api/health"),
    ) -> None:
        self.app = app
        self._enabled = enabled
        self._exclude = exclude_paths
        self._metrics = get_metrics()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not self._enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(ex) for ex in self._exclude):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            try:
                self._metrics.record_request(method, path, status_code, duration)
            except Exception:  # noqa: BLE001 — metrics must never break the request
                pass


# ── Edition-aware summary (for the observability API) ──────────────
def metrics_summary() -> dict[str, Any]:
    """Return a JSON-serialisable summary for the dashboard panel."""
    m = get_metrics()
    edition = get_edition().value
    return {
        "edition": edition,
        "metrics": {
            M_REQUESTS_TOTAL: m.requests_total.get(),
            M_ERRORS_TOTAL: m.errors_total.get(),
            M_ACTIVE_SPANS: m.active_spans.get(),
            M_TRACE_EXPORT_TOTAL: m.trace_export_total.get(),
        },
        "histograms": {
            M_REQUEST_DURATION: {
                "count": m.request_duration._total,
                "sum": round(m.request_duration._sum, 6),
            },
            M_AGENT_EXECUTION: {
                "count": m.agent_execution._total,
                "sum": round(m.agent_execution._sum, 6),
            },
        },
        "enterprise_mode": edition == Edition.ENTERPRISE.value,
    }


__all__ = [
    "M_ACTIVE_SPANS",
    "M_AGENT_AVG_LATENCY",
    "M_AGENT_EXECUTION",
    "M_AGENT_FAILURE_RATE",
    "M_AGENT_STATUS",
    "M_AGENT_TIMEOUT_RATE",
    "M_AGENT_WEIGHT",
    "M_ERRORS_TOTAL",
    "M_REQUESTS_TOTAL",
    "M_REQUEST_DURATION",
    "M_TRACE_EXPORT_TOTAL",
    "MetricsMiddleware",
    "ObservabilityMetrics",
    "get_metrics",
    "metrics_summary",
    "record_agent_execution",
    "record_error",
    "record_request",
]


# ── Convenience module-level functions ─────────────────────────────
def record_request(method: str, path: str, status: int, duration: float) -> None:
    """Module-level shortcut for :meth:`ObservabilityMetrics.record_request`."""
    get_metrics().record_request(method, path, status, duration)


def record_agent_execution(agent: str, phase: str, duration: float) -> None:
    """Module-level shortcut for :meth:`ObservabilityMetrics.record_agent_execution`."""
    get_metrics().record_agent_execution(agent, phase, duration)


def record_error(error_type: str, module: str = "") -> None:
    """Module-level shortcut for :meth:`ObservabilityMetrics.record_error`."""
    get_metrics().record_error(error_type, module)