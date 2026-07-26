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
    from maop.core.monitoring import metrics

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
    from maop.core.llm_provider import LLMResponse, _record_cost

    # 1: record called with correct args
    resp = LLMResponse(
        content="Hello world", model="gpt-4o", provider="openai",
        prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_ms=500,
    )
    mock_tracker = MagicMock()
    with patch("maop.core.cost_tracker.get_cost_tracker", return_value=mock_tracker):
        _record_cost(resp, {"session_id": "sess-123", "agent": "claude"})
    mock_tracker.record.assert_called_once_with(
        model="gpt-4o", provider="openai",
        prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_ms=500,
        session_id="sess-123", agent="claude",
    )

    # 2: CostTracker failure does not raise
    with patch("maop.core.cost_tracker.get_cost_tracker", side_effect=RuntimeError("DB error")):
        _record_cost(resp, {})  # should not raise

    # 3: missing kwargs default to empty strings
    mock_tracker2 = MagicMock()
    with patch("maop.core.cost_tracker.get_cost_tracker", return_value=mock_tracker2):
        _record_cost(LLMResponse(content="Hi", model="claude-3.5-sonnet", provider="anthropic"), {})
    mock_tracker2.record.assert_called_once_with(
        model="claude-3.5-sonnet", provider="anthropic",
        prompt_tokens=0, completion_tokens=0, total_tokens=0, latency_ms=0,
        session_id="", agent="",
    )


def test_cost_tracker_singleton():
    """Verify get_cost_tracker returns a singleton instance."""
    import maop.core.cost_tracker as ct_mod
    from maop.core.cost_tracker import get_cost_tracker, CostTracker

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
    from maop.core.otel import setup_provider

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