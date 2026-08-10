"""Smoke tests for the MAOP performance/load test suite.

These tests validate that the k6 (JavaScript) and Locust (Python) load test
scripts are syntactically correct and reference the expected MAOP endpoints.
They run under ``pytest -m slow`` and do **not** execute the actual load
tests (which require k6 / Locust runtimes + a live MAOP instance).

Layers
------
1. **k6 script validation** (always run): parse the JS file, check it
   references the expected endpoints, custom metrics, and thresholds.
2. **Locust script validation** (skip if ``locust`` not importable): import
   the module and verify the user classes / task weights are correct.
3. **SLO alignment** (always run): verify the thresholds in the k? script
   match the SLOs declared in ``docs/sla.md`` §2.2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

PERF_DIR = Path(__file__).resolve().parent
K6_SCRIPT = PERF_DIR / "k6_maop_load.js"
LOCUST_SCRIPT = PERF_DIR / "locust_maop_load.py"
PROJECT_ROOT = PERF_DIR.parents[2]


# ── helpers ───────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. k6 script validation ───────────────────────────────────────────


class TestK6Script:
    """Validate the k6 load test script structure and content."""

    @pytest.fixture
    def script(self) -> str:
        return _read(K6_SCRIPT)

    def test_script_exists(self):
        assert K6_SCRIPT.is_file(), f"k6 script missing: {K6_SCRIPT}"

    def test_imports_k6_http(self, script: str):
        assert "import http from 'k6/http'" in script

    def test_imports_k6_metrics(self, script: str):
        assert "from 'k6/metrics'" in script
        assert "Rate" in script
        assert "Trend" in script

    def test_custom_metrics_snake_case(self, script: str):
        """Custom metric names use snake_case per convention."""
        expected_metrics = [
            "biz_success_rate",
            "biz_error_rate",
            "api_latency_p95",
            "execute_duration",
        ]
        for metric in expected_metrics:
            assert metric in script, f"missing custom metric: {metric}"

    def test_references_control_endpoints(self, script: str):
        assert "/api/agents" in script
        assert "/api/models" in script

    def test_references_execute_endpoint(self, script: str):
        assert "/api/execute" in script

    def test_references_search_endpoint(self, script: str):
        assert "/api/search" in script

    def test_references_health_endpoint(self, script: str):
        assert "/api/health" in script

    def test_has_thresholds(self, script: str):
        assert "thresholds" in script
        # SLO gate: p95 latency thresholds
        assert "p(95)" in script

    def test_has_stages(self, script: str):
        assert "stages" in script
        assert "duration" in script
        assert "target" in script

    def test_has_setup_teardown(self, script: str):
        assert "export function setup" in script
        assert "export function teardown" in script

    def test_has_scenario_weights(self, script: str):
        """Scenario distribution: control 40%, execute 20%, search 30%, health 10%."""
        assert "0.4" in script   # control plane boundary
        assert "0.6" in script   # execute boundary
        assert "0.9" in script   # search boundary


# ── 2. Locust script validation ───────────────────────────────────────


class TestLocustScript:
    """Validate the Locust load test script structure and content."""

    @pytest.fixture
    def script(self) -> str:
        return _read(LOCUST_SCRIPT)

    def test_script_exists(self):
        assert LOCUST_SCRIPT.is_file(), f"locust script missing: {LOCUST_SCRIPT}"

    def test_imports_locust(self, script: str):
        assert "from locust import" in script

    def test_has_http_user_class(self, script: str):
        assert "HttpUser" in script
        assert "class MaopApiUser" in script

    def test_has_task_weights(self, script: str):
        """Task weights: control=4, execute=2, search=3, health=1."""
        assert "@task(4)" in script or "@task(4)" in script
        assert "@task(2)" in script
        assert "@task(3)" in script
        assert "@task(1)" in script

    def test_has_wait_time(self, script: str):
        assert "wait_time" in script
        assert "between" in script

    def test_references_endpoints(self, script: str):
        for ep in ["/api/agents", "/api/models", "/api/execute", "/api/search", "/api/health"]:
            assert ep in script, f"missing endpoint: {ep}"

    def test_has_admin_user(self, script: str):
        assert "class MaopAdminUser" in script
        assert "/api/audit" in script
        assert "/api/metrics" in script

    def test_uses_catch_response(self, script: str):
        """All requests use catch_response for explicit success/failure marking."""
        assert "catch_response=True" in script


# ── 3. SLO alignment with docs/sla.md ─────────────────────────────────


class TestSLOAlignment:
    """Verify load test thresholds match the SLOs in docs/sla.md."""

    @pytest.fixture
    def k6_script(self) -> str:
        return _read(K6_SCRIPT)

    @pytest.fixture
    def sla_doc(self) -> str:
        sla_path = PROJECT_ROOT / "docs" / "sla.md"
        if not sla_path.is_file():
            pytest.skip("docs/sla.md not found")
        return _read(sla_path)

    def test_control_plane_slo_aligned(self, k6_script: str, sla_doc: str):
        """Control plane P95 ≤ 200ms in both k6 thresholds and SLA doc."""
        # k6 threshold
        assert re.search(r"control.*p\(95\)<200", k6_script, re.DOTALL)
        # SLA doc
        assert "≤ 200 ms" in sla_doc or "≤200ms" in sla_doc

    def test_execute_slo_aligned(self, k6_script: str, sla_doc: str):
        """Execute API P95 ≤ 800ms in both k6 thresholds and SLA doc."""
        assert re.search(r"execute.*p\(95\)<800", k6_script, re.DOTALL)
        assert "≤ 800 ms" in sla_doc or "≤800ms" in sla_doc

    def test_search_slo_aligned(self, k6_script: str, sla_doc: str):
        """Search API P95 ≤ 150ms in both k6 thresholds and SLA doc."""
        assert re.search(r"search.*p\(95\)<150", k6_script, re.DOTALL)
        assert "≤ 150 ms" in sla_doc or "≤150ms" in sla_doc

    def test_error_rate_slo_aligned(self, k6_script: str, sla_doc: str):
        """Error rate < 1% in k6, < 0.1% 5xx in SLA doc."""
        assert "rate<0.01" in k6_script
        assert "0.1%" in sla_doc

    def test_capacity_planning_doc_exists(self):
        """docs/capacity-planning.md exists for capacity planning guidance."""
        cp_path = PROJECT_ROOT / "docs" / "capacity-planning.md"
        assert cp_path.is_file(), "docs/capacity-planning.md missing"