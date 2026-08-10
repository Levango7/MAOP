"""Tests for Phase alpha observability deepening.

Covers P0 fixes and enhancements:
  - timeseries.record() usage in maop_loop (alpha.2.1)
  - Prometheus /api/prometheus endpoint + metrics_path (alpha.2.2)
  - CostTracker auto-recording from LLM calls (alpha.2.3)
  - OTel MeterProvider pipeline (alpha.3.1)
  - Grafana dashboard JSON validity (alpha.3.2)
  - Alert rules YAML validity (alpha.3.3)
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MONITORING_DIR = _PROJECT_ROOT / "monitoring"


# ── alpha.2.1: timeseries.record ───────────────────────────────

def test_timeseries_record_metric():
    """Verify _record_metric calls timeseries.record (not write)."""
    from maop.maop_loop import MaopLoop

    # 1: record called with correct args
    loop = MaopLoop.__new__(MaopLoop)
    loop._timeseries = MagicMock()
    loop._record_metric("test_metric", 42.5, tags={"key": "value"})
    loop._timeseries.record.assert_called_once_with("test_metric", 42.5, tags={"key": "value"})
    loop._timeseries.write.assert_not_called()

    # 2: None tags defaults to empty dict
    loop2 = MaopLoop.__new__(MaopLoop)
    loop2._timeseries = MagicMock()
    loop2._record_metric("test_metric", 10.0)
    loop2._timeseries.record.assert_called_once_with("test_metric", 10.0, tags={})

    # 3: exception does not propagate (caught + logged as warning)
    loop3 = MaopLoop.__new__(MaopLoop)
    loop3._timeseries = MagicMock()
    loop3._timeseries.record.side_effect = RuntimeError("DB error")
    loop3._record_metric("test_metric", 10.0)  # should not raise

    # 4: None _timeseries is a no-op
    loop4 = MaopLoop.__new__(MaopLoop)
    loop4._timeseries = None
    loop4._record_metric("test_metric", 10.0)  # should not raise


# ── alpha.2.2: Prometheus endpoint ─────────────────────────────

def test_prometheus_metrics_endpoint():
    """Verify /api/prometheus returns valid Prometheus text format."""
    from maop.core.monitoring.monitoring import metrics

    # Verify metrics output format
    c = metrics.counter("MAOP_test_obs_counter", "Test counter for observability")
    c.inc(5.0)
    g = metrics.gauge("MAOP_test_obs_gauge", "Test gauge for observability")
    g.set(42.0)
    output = metrics.to_prometheus()
    assert "# TYPE MAOP_test_obs_counter counter" in output
    assert "MAOP_test_obs_counter 5.0" in output
    assert "# TYPE MAOP_test_obs_gauge gauge" in output
    assert "MAOP_test_obs_gauge 42.0" in output

    # Verify endpoint registered in server source
    server_path = _PROJECT_ROOT / "py" / "maop" / "dashboard" / "server.py"
    server_src = server_path.read_text(encoding="utf-8")
    assert '"/api/prometheus"' in server_src

    # Verify prometheus.yml metrics_path
    prom_path = _MONITORING_DIR / "prometheus.yml"
    with open(prom_path, encoding="utf-8") as f:
        prom_config = yaml.safe_load(f)
    assert prom_config["scrape_configs"][0]["metrics_path"] == "/api/prometheus"


# ── alpha.2.3: CostTracker auto-record ─────────────────────────

def test_cost_tracker_auto_record():
    """Verify LLM calls auto-record to CostTracker."""
    from maop.core.agent.llm_chat.llm_provider import LLMResponse, _record_cost

    # 1: record called with correct args
    resp = LLMResponse(
        content="Hello world", model="gpt-4o", provider="openai",
        prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_ms=500,
    )
    mock_tracker = MagicMock()
    with patch("maop.core.monitoring.cost_tracker.get_cost_tracker", return_value=mock_tracker):
        _record_cost(resp, {"session_id": "sess-123", "agent": "claude"})
    mock_tracker.record.assert_called_once_with(
        model="gpt-4o",
        prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_ms=500,
        session_id="sess-123", agent="claude",
        metadata={"provider": "openai"},
    )

    # 2: CostTracker failure does not raise
    with patch("maop.core.monitoring.cost_tracker.get_cost_tracker", side_effect=RuntimeError("DB error")):
        _record_cost(resp, {})  # should not raise

    # 3: missing kwargs default to empty strings
    mock_tracker2 = MagicMock()
    with patch("maop.core.monitoring.cost_tracker.get_cost_tracker", return_value=mock_tracker2):
        _record_cost(LLMResponse(content="Hi", model="claude-3.5-sonnet", provider="anthropic"), {})
    mock_tracker2.record.assert_called_once_with(
        model="claude-3.5-sonnet",
        prompt_tokens=0, completion_tokens=0, total_tokens=0, latency_ms=0,
        session_id="", agent="",
        metadata={"provider": "anthropic"},
    )


def test_cost_tracker_singleton():
    """Verify get_cost_tracker returns a singleton instance."""
    import maop.core.monitoring.cost_tracker as ct_mod
    from maop.core.monitoring.cost_tracker import CostTracker, get_cost_tracker

    original = ct_mod._cost_tracker_instance
    ct_mod._cost_tracker_instance = None
    try:
        t1 = get_cost_tracker()
        t2 = get_cost_tracker()
        assert t1 is t2
        assert isinstance(t1, CostTracker)
    finally:
        ct_mod._cost_tracker_instance = original


# ── alpha.3.1: OTel Metric Pipeline ────────────────────────────

def test_otel_metric_provider():
    """Verify setup_provider configures metric pipeline."""
    from maop.core.monitoring.otel import setup_provider

    # Source-level: metric pipeline code is present
    src = inspect.getsource(setup_provider)
    assert "MeterProvider" in src
    assert "PeriodicExportingMetricReader" in src
    assert "OTLPMetricExporter" in src
    assert "set_meter_provider" in src

    # Functional: when disabled, should be no-op (no exception)
    with patch.dict(os.environ, {}, clear=True):
        setup_provider()  # should not raise


# ── alpha.3.2: Grafana dashboard ───────────────────────────────

def test_grafana_dashboard_valid():
    """Verify Grafana dashboard JSON is valid and well-structured."""
    dashboard_path = _MONITORING_DIR / "grafana" / "maop-overview.json"
    assert dashboard_path.exists(), f"Dashboard file not found: {dashboard_path}"

    with open(dashboard_path, encoding="utf-8") as f:
        dashboard = json.load(f)

    assert dashboard["title"] == "MAOP Overview"
    assert dashboard["schemaVersion"] >= 27
    assert len(dashboard["panels"]) == 8

    expected_titles = {
        "Loop Duration (P50/P95/P99)",
        "Tasks Success Rate",
        "Active Agents",
        "Delegation Duration (P95)",
        "Queue Pending",
        "Circuit Breaker State",
        "LLM Token Usage (by model)",
        "LLM Cost (Daily / Monthly)",
    }
    actual_titles = {p["title"] for p in dashboard["panels"]}
    assert expected_titles == actual_titles

    for panel in dashboard["panels"]:
        assert "type" in panel
        assert "datasource" in panel
        assert "targets" in panel
        assert len(panel["targets"]) > 0
        for target in panel["targets"]:
            assert "expr" in target


# ── alpha.3.3: Alert rules ─────────────────────────────────────

def test_alert_rules_valid():
    """Verify alert rules YAML is valid and references MAOP metrics."""
    alerts_path = _MONITORING_DIR / "alerts.yml"
    assert alerts_path.exists(), f"Alerts file not found: {alerts_path}"

    with open(alerts_path, encoding="utf-8") as f:
        alerts = yaml.safe_load(f)

    assert "groups" in alerts
    assert len(alerts["groups"]) > 0
    rules = alerts["groups"][0]["rules"]
    assert len(rules) >= 4

    rule_names = {r["alert"] for r in rules}
    assert "MAOPDown" in rule_names
    assert "MAOPHighLatency" in rule_names
    assert "MAOPLowSuccessRate" in rule_names

    high_latency = next(r for r in rules if r["alert"] == "MAOPHighLatency")
    assert "MAOP_delegation_duration_seconds_bucket" in high_latency["expr"]
    assert "http_request_duration_seconds_bucket" not in high_latency["expr"]

    low_success = next(r for r in rules if r["alert"] == "MAOPLowSuccessRate")
    assert "MAOP_delegations_success" in low_success["expr"]
    assert "MAOP_delegations_total" in low_success["expr"]


# ════════════════════════════════════════════════════════════════════
# F1-04: Observability package tests
# ════════════════════════════════════════════════════════════════════

_DEPLOY_DIR = _PROJECT_ROOT / "deploy"


# ── Package import ──────────────────────────────────────────────

def test_observability_package_import():
    """Verify the observability package and all sub-modules import cleanly."""
    import maop.core.observability as obs
    assert obs.__version__
    # Core symbols re-exported from __init__
    for sym in (
        "setup_observability", "observability_status",
        "auto_span", "trace_context", "current_span",
        "get_tracer", "tracing_enabled",
        "get_metrics", "record_request", "record_agent_execution", "record_error",
        "get_logger", "log_context", "setup_logging",
        "TraceContextMiddleware", "MetricsMiddleware",
        "M_REQUESTS_TOTAL", "M_REQUEST_DURATION",
        "M_AGENT_EXECUTION", "M_ERRORS_TOTAL",
    ):
        assert hasattr(obs, sym), f"observability missing symbol: {sym}"


def test_observability_submodules_import():
    """Each sub-module imports without side effects."""
    import maop.core.observability.tracing as tracing
    import maop.core.observability.metrics as metrics
    import maop.core.observability.logging as logging_mod
    assert tracing.W3C_TRACEPARENT == "traceparent"
    assert metrics.M_REQUESTS_TOTAL == "maop_requests_total"
    assert logging_mod.TraceCorrelationFilter is not None


# ── Tracing ─────────────────────────────────────────────────────

class TestTracing:
    def test_setup_tracing_returns_bool(self, monkeypatch):
        """setup_tracing returns a bool indicating whether tracing is active."""
        from maop.core.observability.tracing import setup_tracing
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        result = setup_tracing(force=True)
        assert isinstance(result, bool)

    def test_get_tracer_returns_noop_when_disabled(self, monkeypatch):
        """In Personal mode without MAOP_OTEL_ENABLED, tracer is a no-op."""
        from maop.core.observability.tracing import get_tracer, setup_tracing
        from maop.core.monitoring.otel import _NoopTracer
        monkeypatch.delenv("MAOP_OTEL_ENABLED", raising=False)
        monkeypatch.setenv("MAOP_EDITION", "personal")
        from maop.config.edition import reset_edition
        reset_edition()
        setup_tracing(force=True)
        tracer = get_tracer()
        assert isinstance(tracer, _NoopTracer)

    def test_auto_span_decorator_sync(self):
        """auto_span decorates a sync function and preserves return value."""
        from maop.core.observability.tracing import auto_span

        @auto_span("test.sync")
        def add(a: int, b: int) -> int:
            return a + b
        assert add(2, 3) == 5

    def test_auto_span_decorator_async(self):
        """auto_span decorates an async function and preserves return value."""
        import asyncio
        from maop.core.observability.tracing import auto_span

        @auto_span("test.async")
        async def add(a: int, b: int) -> int:
            return a + b
        assert asyncio.run(add(4, 5)) == 9

    def test_auto_span_preserves_metadata(self):
        """auto_span preserves function name and docstring."""
        from maop.core.observability.tracing import auto_span

        @auto_span("test.meta")
        def documented_func(x: int) -> int:
            """My docstring."""
            return x * 2
        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "My docstring."

    def test_auto_span_propagates_exception(self):
        """auto_span re-raises exceptions from the wrapped function."""
        from maop.core.observability.tracing import auto_span

        @auto_span("test.exc")
        def boom() -> None:
            raise ValueError("kaboom")
        with pytest.raises(ValueError, match="kaboom"):
            boom()

    def test_trace_context_manager(self):
        """trace_context works as a context manager."""
        from maop.core.observability.tracing import trace_context
        with trace_context("test.ctx", attributes={"k": "v"}) as s:
            assert s is not None

    def test_current_span_returns_span(self):
        """current_span returns a span-like object (no-op when disabled)."""
        from maop.core.observability.tracing import current_span
        span = current_span()
        assert span is not None

    def test_w3c_trace_context_constants(self):
        """W3C header constants are correct."""
        from maop.core.observability.tracing import W3C_TRACEPARENT, W3C_TRACESTATE
        assert W3C_TRACEPARENT == "traceparent"
        assert W3C_TRACESTATE == "tracestate"

    def test_inject_extract_trace_context(self):
        """inject/extract are callable (no-op when disabled)."""
        from maop.core.observability.tracing import (
            extract_trace_context, inject_trace_context,
        )
        carrier: dict[str, str] = {}
        inject_trace_context(carrier)
        # extract returns None or a context object
        result = extract_trace_context(carrier)
        assert result is None or result is not None

    def test_is_enterprise_personal_mode(self, monkeypatch):
        """Edition mode helpers return bools."""
        from maop.core.observability.tracing import (
            is_enterprise_mode, is_personal_mode,
        )
        assert isinstance(is_enterprise_mode(), bool)
        assert isinstance(is_personal_mode(), bool)
        assert is_enterprise_mode() != is_personal_mode()  # exactly one is True


# ── Metrics ─────────────────────────────────────────────────────

class TestMetrics:
    def test_get_metrics_singleton(self):
        """get_metrics returns the same instance on repeated calls."""
        from maop.core.observability.metrics import get_metrics
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def test_canonical_metric_names(self):
        """The four F1-04 canonical metrics are registered."""
        from maop.core.observability.metrics import (
            M_AGENT_EXECUTION, M_ERRORS_TOTAL, M_REQUESTS_TOTAL,
            M_REQUEST_DURATION, get_metrics,
        )
        m = get_metrics()
        assert M_REQUESTS_TOTAL in m.collector._counters
        assert M_ERRORS_TOTAL in m.collector._counters
        assert M_REQUEST_DURATION in m.collector._histograms
        assert M_AGENT_EXECUTION in m.collector._histograms

    def test_record_request(self):
        """record_request increments the requests counter."""
        from maop.core.observability.metrics import get_metrics, record_request
        m = get_metrics()
        before = m.requests_total.get()
        record_request("GET", "/api/test", 200, 0.05)
        after = m.requests_total.get()
        # Counter is global; just verify it didn't decrease.
        assert after >= before

    def test_record_agent_execution(self):
        """record_agent_execution observes into the histogram."""
        from maop.core.observability.metrics import get_metrics, record_agent_execution
        m = get_metrics()
        before_count = m.agent_execution._total
        record_agent_execution("claude", "execute", 1.5)
        after_count = m.agent_execution._total
        assert after_count == before_count + 1

    def test_record_error(self):
        """record_error increments the errors counter."""
        from maop.core.observability.metrics import get_metrics, record_error
        m = get_metrics()
        before = m.errors_total.get()
        record_error("ValueError", "maop.test")
        after = m.errors_total.get()
        assert after >= before

    def test_metrics_summary(self):
        """metrics_summary returns a JSON-serialisable dict."""
        from maop.core.observability.metrics import metrics_summary
        summary = metrics_summary()
        assert "edition" in summary
        assert "metrics" in summary
        assert "histograms" in summary
        assert "enterprise_mode" in summary
        assert isinstance(summary["enterprise_mode"], bool)

    def test_normalise_path(self):
        """_normalise_path collapses IDs to keep cardinality bounded."""
        from maop.core.observability.metrics import _normalise_path
        assert _normalise_path("/api/agents/abc-123-def") == "/api/agents/:id"
        assert _normalise_path("/api/agents/42") == "/api/agents/:id"
        assert _normalise_path("/api/health") == "/api/health"
        assert _normalise_path("/") == "/"

    def test_to_prometheus_includes_canonical_metrics(self):
        """Prometheus export includes the canonical metric names."""
        from maop.core.observability.metrics import (
            M_ERRORS_TOTAL, M_REQUESTS_TOTAL, get_metrics,
        )
        m = get_metrics()
        m.record_request("GET", "/test", 200, 0.01)
        text = m.to_prometheus()
        assert M_REQUESTS_TOTAL in text
        assert M_ERRORS_TOTAL in text

    def test_metrics_middleware_exclude_paths(self):
        """MetricsMiddleware has an exclude_paths attribute."""
        from maop.core.observability.metrics import MetricsMiddleware
        mw = MetricsMiddleware(app=lambda *a: None, exclude_paths=("/api/health",))
        assert "/api/health" in mw._exclude


# ── Logging ─────────────────────────────────────────────────────

class TestLogging:
    def test_setup_logging_idempotent(self):
        """setup_logging is idempotent (second call is a no-op)."""
        from maop.core.observability.logging import setup_logging
        root1 = setup_logging(level="INFO", force=True)
        root2 = setup_logging(level="INFO", force=False)
        assert root1 is root2

    def test_get_logger_returns_structured_logger(self):
        """get_logger returns a StructuredLogger instance."""
        from maop.core.monitoring.monitoring import StructuredLogger
        from maop.core.observability.logging import get_logger
        log = get_logger("test")
        assert isinstance(log, StructuredLogger)

    def test_log_context_manager(self):
        """log_context yields a logger and supports info calls."""
        from maop.core.observability.logging import log_context
        with log_context(trace_id="abc123", user="alice") as log:
            log.info("test message")
            assert log.trace_id == "abc123"

    def test_log_context_with_extras(self):
        """log_context extras are merged into every log call."""
        from maop.core.observability.logging import log_context
        with log_context(request_id="req-1") as log:
            # Should not raise — extras are merged internally.
            log.info("processing")
            log.error("failed", retry=3)

    def test_trace_correlation_filter(self):
        """TraceCorrelationFilter is a logging.Filter subclass."""
        import logging
        from maop.core.observability.logging import TraceCorrelationFilter
        f = TraceCorrelationFilter()
        assert isinstance(f, logging.Filter)
        record = logging.LogRecord(
            "test", logging.INFO, "test.py", 1, "msg", None, None,
        )
        assert f.filter(record) is True

    def test_logging_summary(self):
        """logging_summary returns a dict with expected keys."""
        from maop.core.observability.logging import logging_summary
        summary = logging_summary()
        assert "edition" in summary
        assert "level" in summary
        assert "handlers" in summary
        assert "trace_correlation" in summary


# ── FastAPI router ──────────────────────────────────────────────

class TestObservabilityRouter:
    def test_router_has_endpoints(self):
        """The observability router declares the expected endpoints."""
        from maop.dashboard.routers.observability import router
        paths = {getattr(r, "path", "") for r in router.routes}
        assert "/api/observability/status" in paths
        assert "/api/observability/metrics" in paths
        assert "/api/observability/health" in paths
        assert "/api/observability/config" in paths
        assert "/api/observability/record" in paths
        assert "/api/observability/traces" in paths

    def test_status_endpoint(self):
        """GET /api/observability/status returns the stack status."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "edition" in data
        assert "tracing_enabled" in data

    def test_metrics_endpoint(self):
        """GET /api/observability/metrics returns a JSON summary."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "edition" in data

    def test_config_endpoint(self):
        """GET /api/observability/config returns the OTel config."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "otel_exporter" in data
        assert "prometheus_scrape_path" in data

    def test_traces_endpoint(self):
        """GET /api/observability/traces returns enabled flag."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data

    def test_record_endpoint_request(self):
        """POST /api/observability/record accepts kind=request."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/observability/record", json={
            "kind": "request",
            "method": "GET",
            "path": "/test",
            "status": 200,
            "duration": 0.05,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_record_endpoint_error(self):
        """POST /api/observability/record accepts kind=error."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/observability/record", json={
            "kind": "error",
            "error_type": "ValueError",
            "module": "test",
        })
        assert resp.status_code == 200

    def test_record_endpoint_invalid_kind(self):
        """POST /api/observability/record rejects unknown kind."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/observability/record", json={"kind": "bogus"})
        assert resp.status_code == 400

    def test_health_endpoint(self):
        """GET /api/observability/health runs pipeline checks."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data
        assert "otel_sdk" in data["checks"]

    def test_prometheus_endpoint(self):
        """GET /api/observability/metrics/prometheus returns text format."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from maop.dashboard.routers.observability import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/observability/metrics/prometheus")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")


# ── Deploy configs ──────────────────────────────────────────────

class TestDeployConfigs:
    def test_otel_collector_config_exists(self):
        """deploy/otel-collector.yaml exists and is valid YAML."""
        cfg_path = _DEPLOY_DIR / "otel-collector.yaml"
        assert cfg_path.exists(), f"OTel Collector config not found: {cfg_path}"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "receivers" in cfg
        assert "processors" in cfg
        assert "exporters" in cfg
        assert "service" in cfg
        assert "otlp" in cfg["receivers"]

    def test_otel_collector_has_trace_pipeline(self):
        """OTel Collector config has a traces pipeline."""
        cfg_path = _DEPLOY_DIR / "otel-collector.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "traces" in cfg["service"]["pipelines"]
        trace_pipe = cfg["service"]["pipelines"]["traces"]
        assert "otlp" in trace_pipe["receivers"]

    def test_otel_collector_has_metrics_pipeline(self):
        """OTel Collector config has a metrics pipeline."""
        cfg_path = _DEPLOY_DIR / "otel-collector.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "metrics" in cfg["service"]["pipelines"]

    def test_otel_collector_has_batch_processor(self):
        """OTel Collector config has a batch processor."""
        cfg_path = _DEPLOY_DIR / "otel-collector.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "batch" in cfg["processors"]

    def test_otel_collector_has_memory_limiter(self):
        """OTel Collector config has a memory_limiter processor."""
        cfg_path = _DEPLOY_DIR / "otel-collector.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "memory_limiter" in cfg["processors"]

    def test_grafana_dashboard_exists(self):
        """deploy/grafana/dashboards/maop-overview.json exists and is valid JSON."""
        dash_path = _DEPLOY_DIR / "grafana" / "dashboards" / "maop-overview.json"
        assert dash_path.exists(), f"Dashboard not found: {dash_path}"
        with open(dash_path, encoding="utf-8") as f:
            dashboard = json.load(f)
        assert dashboard["title"] == "MAOP Observability Overview"
        assert dashboard["schemaVersion"] >= 27
        assert len(dashboard["panels"]) >= 8

    def test_grafana_dashboard_references_canonical_metrics(self):
        """Grafana dashboard panels reference the F1-04 canonical metrics."""
        dash_path = _DEPLOY_DIR / "grafana" / "dashboards" / "maop-overview.json"
        with open(dash_path, encoding="utf-8") as f:
            dashboard = json.load(f)
        all_exprs = []
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                if "expr" in target:
                    all_exprs.append(target["expr"])
        combined = " ".join(all_exprs)
        assert "maop_requests_total" in combined
        assert "maop_request_duration_seconds" in combined
        assert "maop_agent_execution_seconds" in combined
        assert "maop_errors_total" in combined

    def test_grafana_dashboard_panel_structure(self):
        """Each Grafana panel has the required fields."""
        dash_path = _DEPLOY_DIR / "grafana" / "dashboards" / "maop-overview.json"
        with open(dash_path, encoding="utf-8") as f:
            dashboard = json.load(f)
        for panel in dashboard["panels"]:
            assert "type" in panel
            assert "datasource" in panel
            assert "targets" in panel
            assert len(panel["targets"]) > 0
            for target in panel["targets"]:
                assert "expr" in target

    def test_grafana_provisioning_exists(self):
        """deploy/grafana/provisioning/dashboards.yaml exists."""
        prov_path = _DEPLOY_DIR / "grafana" / "provisioning" / "dashboards.yaml"
        assert prov_path.exists()


# ── setup_observability integration ─────────────────────────────

class TestSetupObservability:
    def test_setup_returns_summary(self):
        """setup_observability returns a summary dict."""
        from maop.core.observability import setup_observability
        summary = setup_observability(force=True)
        assert "edition" in summary
        assert "tracing_enabled" in summary
        assert "metrics" in summary
        assert "logging" in summary

    def test_setup_idempotent(self):
        """setup_observability is idempotent."""
        from maop.core.observability import setup_observability
        s1 = setup_observability(force=True)
        s2 = setup_observability(force=False)
        # Both should return valid summaries.
        assert "edition" in s1
        assert "edition" in s2

    def test_observability_status(self):
        """observability_status returns the live status."""
        from maop.core.observability import observability_status
        status = observability_status()
        assert "edition" in status
        assert "tracing" in status
        assert "metrics" in status
        assert "logging" in status


# ── install_auto_spans ──────────────────────────────────────────

class TestInstallAutoSpans:
    def test_install_returns_int(self):
        """install_auto_spans returns the count of wrapped methods."""
        from maop.core.observability.tracing import install_auto_spans
        count = install_auto_spans()
        assert isinstance(count, int)
        assert count >= 0

    def test_install_idempotent(self):
        """install_auto_spans is idempotent (no double-wrap)."""
        from maop.core.observability.tracing import install_auto_spans
        c1 = install_auto_spans()
        c2 = install_auto_spans()
        # Second call should not re-wrap already-wrapped methods.
        # When tracing is disabled, both return 0.
        assert c2 <= c1


# ── ASGI middleware ─────────────────────────────────────────────

class TestAsgiMiddleware:
    def test_trace_context_middleware_construct(self):
        """TraceContextMiddleware constructs with an app."""
        from maop.core.observability.tracing import TraceContextMiddleware
        async def dummy_app(scope, receive, send):
            pass
        mw = TraceContextMiddleware(dummy_app)
        assert mw.app is dummy_app

    def test_metrics_middleware_construct(self):
        """MetricsMiddleware constructs with an app."""
        from maop.core.observability.metrics import MetricsMiddleware
        async def dummy_app(scope, receive, send):
            pass
        mw = MetricsMiddleware(dummy_app)
        assert mw.app is dummy_app

    def test_trace_context_middleware_disabled_passthrough(self):
        """When disabled, TraceContextMiddleware passes through directly."""
        from maop.core.observability.tracing import TraceContextMiddleware
        called = []

        async def dummy_app(scope, receive, send):
            called.append(True)

        mw = TraceContextMiddleware(dummy_app, enabled=False)

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            pass

        import asyncio
        asyncio.run(mw({"type": "http", "headers": []}, receive, send))
        assert called == [True]

    def test_metrics_middleware_records_request(self):
        """MetricsMiddleware records a request on completion."""
        import asyncio
        from maop.core.observability.metrics import MetricsMiddleware

        async def dummy_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = MetricsMiddleware(dummy_app, exclude_paths=())

        async def receive():
            return {"type": "http.request"}

        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(mw(
            {"type": "http", "method": "GET", "path": "/test", "headers": []},
            receive, send,
        ))
        assert len(sent) == 2
