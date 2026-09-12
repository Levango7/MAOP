"""Dedicated tests for the deep data analysis report engine.

Covers the six endpoints exposed by ``maop.dashboard.routers.analysis``:

  * GET /api/analysis/agent-efficiency
  * GET /api/analysis/task-trends
  * GET /api/analysis/resource-utilization
  * GET /api/analysis/cost-breakdown
  * GET /api/analysis/performance-bottlenecks
  * GET /api/analysis/summary

Assertions verify:
  - admin auth gating (403 without admin role is covered by smoke tests;
    here we grant admin and check happy-path 200)
  - unified response shape ``{"status": "ok", "data": ..., "meta": {...}}``
  - graceful degradation: missing data sources return empty lists + a
    ``note`` in meta rather than 500
  - query-parameter validation (granularity / group_by / date_range enums)
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from maop.core.security.auth import AuthResult

# Admin JWT stub — any token yields an authenticated admin.
_ADMIN_RESULT = AuthResult(authenticated=True, identity="admin", roles=["admin"])


class _JwtStub:
    """Stand-in for app.state.jwt_auth (duck-typed by AuthMiddleware)."""

    def validate_token(self, token: str) -> AuthResult:
        return _ADMIN_RESULT


@pytest.fixture
async def admin_client(monkeypatch):
    """Async admin client against server.app (no lifespan, admin auth)."""
    from maop.core.security.middleware import AuthMiddleware
    from maop.dashboard.server import app

    async def _admin_dispatch_disabled(self, request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "admin"
        return await call_next(request)

    monkeypatch.setattr(
        AuthMiddleware, "_dispatch_disabled", _admin_dispatch_disabled
    )

    saved: dict[str, Any] = {}
    for attr in ("auth_manager", "api_key_auth", "jwt_auth"):
        saved[attr] = getattr(app.state, attr, None)
    app.state.jwt_auth = _JwtStub()
    app.state.auth_manager = None
    app.state.api_key_auth = None

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as c:
        yield c

    for attr, val in saved.items():
        setattr(app.state, attr, val)


# ── Response shape contract ────────────────────────────────────────


def _assert_ok_shape(resp_data: dict[str, Any], required_data_keys: list[str]) -> None:
    """Assert the unified response shape: {status, data, meta}."""
    assert resp_data["status"] == "ok"
    assert "data" in resp_data
    assert "meta" in resp_data
    data = resp_data["data"]
    for key in required_data_keys:
        assert key in data, f"missing key {key!r} in data: {list(data.keys())}"
    meta = resp_data["meta"]
    assert "date_from" in meta
    assert "date_to" in meta
    assert "generated_at" in meta
    assert "note" in meta


# ── 1. agent-efficiency ────────────────────────────────────────────


class TestAgentEfficiency:
    async def test_happy_path(self, admin_client):
        resp = await admin_client.get("/api/analysis/agent-efficiency")
        assert resp.status_code == 200
        _assert_ok_shape(resp.json(), ["agents", "total_cost_usd", "total_calls"])

    async def test_with_date_range(self, admin_client):
        resp = await admin_client.get(
            "/api/analysis/agent-efficiency",
            params={"date_from": "2026-01-01T00:00:00", "date_to": "2026-06-01T00:00:00"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["meta"]["date_from"].startswith("2026-01-01")

    async def test_with_agent_filter(self, admin_client):
        resp = await admin_client.get(
            "/api/analysis/agent-efficiency",
            params={"agent_id": "claude"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # All returned agents should match the filter (or list is empty)
        for a in body["data"]["agents"]:
            assert a["agent"] == "claude"

    async def test_agents_is_list(self, admin_client):
        resp = await admin_client.get("/api/analysis/agent-efficiency")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"]["agents"], list)


# ── 2. task-trends ─────────────────────────────────────────────────


class TestTaskTrends:
    async def test_default_granularity(self, admin_client):
        resp = await admin_client.get("/api/analysis/task-trends")
        assert resp.status_code == 200
        _assert_ok_shape(resp.json(), ["granularity", "series", "bucket_count"])
        assert resp.json()["data"]["granularity"] == "day"

    @pytest.mark.parametrize("granularity", ["hour", "day", "week"])
    async def test_granularity_values(self, admin_client, granularity):
        resp = await admin_client.get(
            "/api/analysis/task-trends",
            params={"granularity": granularity},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["granularity"] == granularity

    async def test_series_is_list_of_dicts(self, admin_client):
        resp = await admin_client.get("/api/analysis/task-trends")
        assert resp.status_code == 200
        series = resp.json()["data"]["series"]
        assert isinstance(series, list)
        for point in series:
            assert "bucket" in point
            assert "success" in point
            assert "failure" in point
            assert "timeout" in point
            assert "total" in point

    async def test_invalid_granularity_rejected(self, admin_client):
        # FastAPI Literal validation returns 422
        resp = await admin_client.get(
            "/api/analysis/task-trends",
            params={"granularity": "minute"},
        )
        assert resp.status_code == 422


# ── 3. resource-utilization ────────────────────────────────────────


class TestResourceUtilization:
    async def test_happy_path(self, admin_client):
        resp = await admin_client.get("/api/analysis/resource-utilization")
        assert resp.status_code == 200
        _assert_ok_shape(resp.json(), ["live", "history", "db_pool", "cache"])

    async def test_live_block_structure(self, admin_client):
        resp = await admin_client.get("/api/analysis/resource-utilization")
        assert resp.status_code == 200
        live = resp.json()["data"]["live"]
        # Keys may be None when psutil unavailable, but must be present
        assert "cpu_percent" in live
        assert "memory_percent" in live
        assert "memory_used_mb" in live
        assert "memory_total_mb" in live


# ── 4. cost-breakdown ──────────────────────────────────────────────


class TestCostBreakdown:
    async def test_default_group_by(self, admin_client):
        resp = await admin_client.get("/api/analysis/cost-breakdown")
        assert resp.status_code == 200
        _assert_ok_shape(
            resp.json(),
            ["group_by", "breakdown", "trend", "total_cost_usd", "total_tokens", "total_calls"],
        )
        assert resp.json()["data"]["group_by"] == "agent"

    @pytest.mark.parametrize("group_by", ["agent", "model", "tenant"])
    async def test_group_by_values(self, admin_client, group_by):
        resp = await admin_client.get(
            "/api/analysis/cost-breakdown",
            params={"group_by": group_by},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["group_by"] == group_by

    async def test_breakdown_sorted_by_cost_desc(self, admin_client):
        resp = await admin_client.get("/api/analysis/cost-breakdown")
        assert resp.status_code == 200
        breakdown = resp.json()["data"]["breakdown"]
        costs = [item["cost_usd"] for item in breakdown]
        assert costs == sorted(costs, reverse=True)

    async def test_invalid_group_by_rejected(self, admin_client):
        resp = await admin_client.get(
            "/api/analysis/cost-breakdown",
            params={"group_by": "region"},
        )
        assert resp.status_code == 422


# ── 5. performance-bottlenecks ─────────────────────────────────────


class TestPerformanceBottlenecks:
    async def test_happy_path(self, admin_client):
        resp = await admin_client.get("/api/analysis/performance-bottlenecks")
        assert resp.status_code == 200
        _assert_ok_shape(
            resp.json(),
            ["slowest_endpoints", "slowest_agents", "top_memory_consumers"],
        )

    async def test_top_n_limit(self, admin_client):
        resp = await admin_client.get(
            "/api/analysis/performance-bottlenecks",
            params={"top_n": 5},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["slowest_endpoints"]) <= 5
        assert len(data["slowest_agents"]) <= 5
        assert len(data["top_memory_consumers"]) <= 5

    async def test_top_n_bounds(self, admin_client):
        # top_n < 1 → 422
        resp = await admin_client.get(
            "/api/analysis/performance-bottlenecks",
            params={"top_n": 0},
        )
        assert resp.status_code == 422
        # top_n > 100 → 422
        resp = await admin_client.get(
            "/api/analysis/performance-bottlenecks",
            params={"top_n": 101},
        )
        assert resp.status_code == 422


# ── 6. summary ─────────────────────────────────────────────────────


class TestSummary:
    @pytest.mark.parametrize("date_range", ["7d", "30d", "90d"])
    async def test_date_range_values(self, admin_client, date_range):
        resp = await admin_client.get(
            "/api/analysis/summary",
            params={"date_range": date_range},
        )
        assert resp.status_code == 200
        _assert_ok_shape(
            resp.json(),
            [
                "date_range",
                "total_tasks",
                "success_rate_pct",
                "avg_latency_ms",
                "total_cost_usd",
                "total_calls",
                "active_agents",
                "sparkline",
                "observability",
            ],
        )
        assert resp.json()["data"]["date_range"] == date_range

    async def test_invalid_date_range_rejected(self, admin_client):
        resp = await admin_client.get(
            "/api/analysis/summary",
            params={"date_range": "1y"},
        )
        assert resp.status_code == 422

    async def test_sparkline_is_list(self, admin_client):
        resp = await admin_client.get("/api/analysis/summary")
        assert resp.status_code == 200
        sparkline = resp.json()["data"]["sparkline"]
        assert isinstance(sparkline, list)
        for point in sparkline:
            assert "date" in point
            assert "count" in point


# ── Auth regression ────────────────────────────────────────────────


class TestAuthRegression:
    async def test_endpoints_require_admin(self, monkeypatch):
        """Without admin role, endpoints must return 403 (not 200)."""
        from maop.core.security.middleware import AuthMiddleware
        from maop.dashboard.server import app

        async def _readonly_dispatch_disabled(self, request, call_next):
            # Grant only read-only role (no admin)
            request.state.auth_roles = ["reader"]
            request.state.auth_identity = "reader"
            return await call_next(request)

        monkeypatch.setattr(
            AuthMiddleware, "_dispatch_disabled", _readonly_dispatch_disabled
        )

        saved: dict[str, Any] = {}
        for attr in ("auth_manager", "api_key_auth", "jwt_auth"):
            saved[attr] = getattr(app.state, attr, None)
        app.state.jwt_auth = _JwtStub()
        app.state.auth_manager = None
        app.state.api_key_auth = None

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as c:
            for path in (
                "/api/analysis/agent-efficiency",
                "/api/analysis/task-trends",
                "/api/analysis/resource-utilization",
                "/api/analysis/cost-breakdown",
                "/api/analysis/performance-bottlenecks",
                "/api/analysis/summary",
            ):
                resp = await c.get(path)
                assert resp.status_code == 403, (
                    f"auth regression on {path}: expected 403, got {resp.status_code}"
                )

        for attr, val in saved.items():
            setattr(app.state, attr, val)