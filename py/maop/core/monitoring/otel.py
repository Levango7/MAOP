"""MAOP OpenTelemetry Integration — Native span-level tracing.

Bridges MAOP's existing trace_id propagation to OpenTelemetry spans.
When opentelemetry-api is installed, all MAOP phases (analyze/plan/execute/verify/feedback/evolve)
emit real OTel spans with proper parent-child linkage.

When opentelemetry-api is NOT installed, falls back to no-op stubs (zero overhead).

Usage::

    from maop.core.monitoring.otel import get_tracer, span

    tracer = get_tracer("maop.loop")
    with span(tracer, "execute", attributes={"agent": "claude"}) as s:
        ...  # automatically linked to parent trace

Configuration (environment variables):
    MAOP_OTEL_ENABLED=1          # Enable OTel (default: off)
    MAOP_OTEL_EXPORTER=otlp      # otlp | console | none
    MAOP_OTEL_ENDPOINT=http://... # OTLP gRPC/HTTP endpoint
    MAOP_OTEL_SERVICE_NAME=maop  # Service name in traces
"""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_OTELE_AVAILABLE = False
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import SpanKind

    _OTELE_AVAILABLE = True
except ImportError:
    pass


def is_enabled() -> bool:
    return os.getenv("MAOP_OTEL_ENABLED", "").strip() in ("1", "true", "yes")


def get_tracer(name: str = "maop") -> Any:
    if not _OTELE_AVAILABLE or not is_enabled():
        return _NoopTracer()
    return otel_trace.get_tracer(name)


@contextmanager
def span(
    tracer: Any,
    name: str,
    *,
    kind: Any = None,
    attributes: dict[str, Any] | None = None,
    trace_id: str = "",
) -> Generator[Any, None, None]:
    """Create an OTel span (or no-op if OTel disabled).

    If trace_id is provided and no active OTel context exists,
    we link the MAOP trace_id as a span attribute for correlation.
    """
    if isinstance(tracer, _NoopTracer):
        yield _NoopSpan()
        return

    span_kind = kind if kind is not None else SpanKind.INTERNAL
    attrs = dict(attributes or {})
    if trace_id:
        attrs["maop.trace_id"] = trace_id

    s = tracer.start_span(name, kind=span_kind, attributes=attrs)
    with otel_trace.use_span(s, end_on_exit=True):
        yield s


def setup_provider() -> None:
    """Initialize OTel TracerProvider based on environment config.

    Called once at startup. Safe to call multiple times.
    """
    if not _OTELE_AVAILABLE:
        logger.debug("[otel] opentelemetry-api not installed, tracing disabled")
        return

    if not is_enabled():
        logger.debug("[otel] MAOP_OTEL_ENABLED not set, tracing disabled")
        return

    service_name = os.getenv("MAOP_OTEL_SERVICE_NAME", "maop")
    resource = Resource.create({"service.name": service_name})

    exporter_type = os.getenv("MAOP_OTEL_EXPORTER", "none").lower()

    if exporter_type == "console":
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter_type == "otlp":
        endpoint = os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317")
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter as HTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                provider = TracerProvider(resource=resource)
                provider.add_span_processor(BatchSpanProcessor(HTLPSpanExporter(endpoint=endpoint)))
            except ImportError:
                logger.warning("[otel] No OTLP exporter available, using console fallback")
                from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
                provider = TracerProvider(resource=resource)
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        provider = TracerProvider(resource=resource)

    otel_trace.set_tracer_provider(provider)
    logger.info("[otel] Tracing enabled | service=%s | exporter=%s", service_name, exporter_type)
    # ── Metric Pipeline (Phase α.3.1) ───────────────────────────
    # MeterProvider mirrors the trace config: OTLP gRPC exporter to the
    # same endpoint, 15s export interval. Requires opentelemetry-sdk
    # with the metrics extra; if unavailable, logs a warning and
    # continues with traces only.
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        metric_endpoint = os.getenv("MAOP_OTEL_ENDPOINT", "http://localhost:4317")
        metric_exporter = OTLPMetricExporter(endpoint=metric_endpoint)
        metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
        meter_provider = SDKMeterProvider(resource=resource, metric_readers=[metric_reader])
        otel_metrics.set_meter_provider(meter_provider)
        logger.info("[otel] Metrics enabled | exporter=otlp | endpoint=%s", metric_endpoint)
    except ImportError:
        logger.warning("[otel] Metric pipeline not available (opentelemetry SDK incomplete)")


def inject_trace_context(carrier: dict[str, str]) -> None:
    """Inject OTel trace context into a carrier dict (for distributed tracing)."""
    if not _OTELE_AVAILABLE or not is_enabled():
        return
    from opentelemetry.propagate import inject
    inject(carrier)


def extract_trace_context(carrier: dict[str, str]) -> Any:
    """Extract OTel trace context from a carrier dict."""
    if not _OTELE_AVAILABLE or not is_enabled():
        return None
    from opentelemetry.propagate import extract
    return extract(carrier)


class _NoopTracer:
    def start_span(self, *args: Any, **kwargs: Any) -> Any:
        return _NoopSpan()


class _NoopSpan:
    def __enter__(self) -> Any:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def set_attribute(self, *args: Any) -> None:
        pass

    def add_event(self, *args: Any) -> None:
        pass

    def record_exception(self, *args: Any) -> None:
        pass

    def set_status(self, *args: Any) -> None:
        pass

    def end(self) -> None:
        pass

    @property
    def is_recording(self) -> bool:
        return False
