"""MAOP Observability — unified tracing, metrics, and structured logging.

This package is the public façade for the F1-04 observability stack.
It composes three sub-modules:

* :mod:`tracing`  — OpenTelemetry SDK integration, auto-spans, W3C
                     Trace Context propagation, FastAPI middleware.
* :mod:`metrics`  — Prometheus counters/histograms + ASGI middleware.
* :mod:`logging`  — Structured JSON logging with trace correlation.

Edition gating
--------------
* **Personal**  — lightweight: structured logs + basic counters only.
  OTel SDK is an optional extra; when absent every tracing API
  degrades to a zero-overhead no-op.
* **Enterprise** — full OTel + Prometheus + Grafana pipeline.

One-shot setup
--------------
>>> from maop.core.observability import setup_observability
>>> setup_observability()  # call once at process start

This initialises logging, tracing, and metrics; installs auto-spans on
``Orchestrator.execute`` / ``Dispatcher.dispatch`` / ``Agent.run``; and
returns a summary dict describing what was enabled.
"""
from __future__ import annotations

import logging
from typing import Any

# Create the module logger BEFORE importing the .logging sub-module —
# otherwise `from maop.core.observability.logging import ...` rebinds
# the name `logging` to the sub-module and `logging.getLogger` breaks.
logger = logging.getLogger(__name__)

from maop.config.edition import get_edition
from maop.core.observability.logging import (
    TraceCorrelationFilter,
    get_logger,
    log_context,
    logging_summary,
    setup_logging,
)
from maop.core.observability.metrics import (
    M_ACTIVE_SPANS,
    M_AGENT_EXECUTION,
    M_ERRORS_TOTAL,
    M_REQUEST_DURATION,
    M_REQUESTS_TOTAL,
    M_TRACE_EXPORT_TOTAL,
    MetricsMiddleware,
    ObservabilityMetrics,
    get_metrics,
    metrics_summary,
    record_agent_execution,
    record_error,
    record_request,
)
from maop.core.observability.tracing import (
    W3C_TRACEPARENT,
    W3C_TRACESTATE,
    TraceContextMiddleware,
    auto_span,
    current_span,
    extract_trace_context,
    get_tracer,
    inject_trace_context,
    install_auto_spans,
    is_enterprise_mode,
    is_personal_mode,
    setup_tracing,
    trace_context,
    tracing_enabled,
)

__version__ = "1.0.0"

_setup_done = False


def setup_observability(
    *,
    log_level: str = "INFO",
    log_file: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """One-shot setup for the full observability stack.

    Idempotent: subsequent calls with ``force=False`` return the cached
    summary.  Safe to call from FastAPI lifespan or CLI entrypoint.

    Returns a summary dict describing what was enabled, suitable for
    logging or for the ``/api/observability/status`` endpoint.
    """
    global _setup_done
    if _setup_done and not force:
        return _build_summary()

    edition = get_edition()

    # 1. Structured logging + trace correlation (always on).
    setup_logging(level=log_level, log_file=log_file, force=force)

    # 2. Tracing (no-op in Personal unless MAOP_OTEL_ENABLED=1).
    tracing_active = setup_tracing(force=force)

    # 3. Metrics (always on — counters are cheap).
    get_metrics()

    # 4. Auto-spans on the spec-named methods (only when tracing active).
    spans_installed = install_auto_spans() if tracing_active else 0

    _setup_done = True
    summary = _build_summary()
    summary["spans_installed"] = spans_installed
    logger.info(
        "[observability] setup complete | edition=%s | tracing=%s | spans=%d",
        edition.value, tracing_active, spans_installed,
    )
    return summary


def _build_summary() -> dict[str, Any]:
    """Compose the current observability status from all sub-modules."""
    return {
        "edition": get_edition().value,
        "tracing_enabled": tracing_enabled(),
        "enterprise_mode": is_enterprise_mode(),
        "tracing": {
            "enabled": tracing_enabled(),
            "tracer_type": type(get_tracer()).__name__,
        },
        "metrics": metrics_summary(),
        "logging": logging_summary(),
    }


def observability_status() -> dict[str, Any]:
    """Return the live observability status (for the API endpoint)."""
    return _build_summary()


__all__ = [
    "M_ACTIVE_SPANS",
    "M_AGENT_EXECUTION",
    "M_ERRORS_TOTAL",
    "M_REQUESTS_TOTAL",
    "M_REQUEST_DURATION",
    "M_TRACE_EXPORT_TOTAL",
    "W3C_TRACEPARENT",
    "W3C_TRACESTATE",
    "MetricsMiddleware",
    "ObservabilityMetrics",
    "TraceContextMiddleware",
    "TraceCorrelationFilter",
    "__version__",
    "auto_span",
    "current_span",
    "extract_trace_context",
    "get_logger",
    "get_metrics",
    "get_tracer",
    "inject_trace_context",
    "install_auto_spans",
    "is_enterprise_mode",
    "is_personal_mode",
    "log_context",
    "logging_summary",
    "metrics_summary",
    "observability_status",
    "record_agent_execution",
    "record_error",
    "record_request",
    "setup_logging",
    "setup_observability",
    "setup_tracing",
    "trace_context",
    "tracing_enabled",
]