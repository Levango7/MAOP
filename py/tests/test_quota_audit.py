"""Tests for quota_audit — 配额变更历史落库、使用量采样与概览/趋势端点。

回归的缺陷：``quota_history`` 表此前只有读路径（``GET /api/quotas/history`` 因表
不存在恒返回空列表），配额变更历史面板永远是空的；「使用量趋势」与「配额总量分布」
两个面板也压根没有端点。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maop.enterprise.quota import QuotaManager

from maop.dashboard.services import billing_service, quota_audit


@pytest.fixture(autouse=True)
def enterprise_mode():
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture
def qm(tmp_path: Path) -> Any:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    mgr = QuotaManager(d / "maop.db", cache_ttl_s=0.0)
    billing_service._set_quota_manager(mgr)
    yield mgr
    billing_service._set_quota_manager(None)


def _history(tenant_id: str) -> list[dict[str, Any]]:
    return billing_service.list_quota_history(tenant_id=tenant_id)["history"]


class TestQuotaChangeHistory:
    def test_set_quota_records_every_effective_field(self, qm: Any):
        billing_service.set_quota(
            "t-hist", "api_calls", 1000, soft_limit=800, period="daily", actor="alice",
        )
        rows = _history("t-hist")
        assert {r["field"] for r in rows} == {"hard_limit", "soft_limit", "period"}
        assert all(r["changed_by"] == "alice" for r in rows)
        hard = next(r for r in rows if r["field"] == "hard_limit")
        assert (hard["old_value"], hard["new_value"]) == ("", "1000")

    def test_update_records_only_changed_fields(self, qm: Any):
        billing_service.set_quota("t-upd", "storage_mb", 1000, actor="alice")
        billing_service.update_quota("t-upd", "storage_mb", hard_limit=2000, actor="bob")
        rows = [r for r in _history("t-upd") if r["field"] == "hard_limit"]
        assert {(r["old_value"], r["new_value"]) for r in rows} == {
            ("", "1000"), ("1000", "2000"),
        }
        assert any(r["changed_by"] == "bob" for r in rows)
        # soft_limit/period 未变 ⇒ 不产生额外的 hard_limit 之外的新字段历史
        assert {r["field"] for r in _history("t-upd")} == {"hard_limit", "period"}

    def test_delete_records_deletion(self, qm: Any):
        billing_service.set_quota("t-del", "api_calls", 500, actor="alice")
        assert billing_service.delete_quota("t-del", "api_calls", actor="carol") is True
        rows = [r for r in _history("t-del") if r["field"] == "deleted"]
        assert len(rows) == 1
        assert "hard_limit=500" in rows[0]["old_value"]
        assert rows[0]["new_value"] == ""
        assert rows[0]["changed_by"] == "carol"


class TestUsageTrend:
    def test_sample_written_on_set_usage_and_bucketed(self, qm: Any):
        billing_service.set_quota("t-trend", "api_calls", 1000)
        billing_service.set_quota_usage("t-trend", "api_calls", 300)
        trend = quota_audit.usage_trend("t-trend", hours=6)
        assert len(trend["labels"]) == 6
        assert trend["sampled"] is True
        series = next(s for s in trend["series"] if s["label"] == "api_calls")
        assert series["data"][-1] == 300

    def test_consume_writes_sample(self, qm: Any):
        billing_service.set_quota("t-cons", "api_calls", 1000)
        result = billing_service.consume_quota("t-cons", "api_calls", amount=70)
        assert result["allowed"] is True
        trend = quota_audit.usage_trend("t-cons", hours=3)
        assert trend["sampled"] is True

    def test_reset_writes_zero_sample(self, qm: Any):
        billing_service.set_quota("t-reset", "api_calls", 1000)
        billing_service.set_quota_usage("t-reset", "api_calls", 400)
        billing_service.reset_quota_usage("t-reset", "api_calls")
        trend = quota_audit.usage_trend("t-reset", hours=3)
        api_series = next(s for s in trend["series"] if s["label"] == "api_calls")
        assert api_series["data"][-1] == 0

    def test_trend_without_samples_is_honest(self, qm: Any):
        trend = quota_audit.usage_trend("t-empty", hours=4)
        assert trend["sampled"] is False
        assert trend["series"] == []
        assert len(trend["labels"]) == 4


class TestQuotaOverview:
    def test_sums_configured_caps_and_prefers_display_name(self):
        tenants = [
            {
                "tenant_id": "t1", "name": "Acme",
                "quota": {"max_agents": 10, "max_storage_mb": 2000, "max_users": None},
            },
            {"tenant_id": "t2", "name": "", "quota": {"max_agents": 5}},
            {"tenant_id": "t3", "quota": {}},
        ]
        overview = quota_audit.quota_overview(tenants)
        assert overview["allocation"]["labels"] == ["Acme", "t2", "t3"]
        assert overview["allocation"]["values"] == [2010, 5, 0]
        assert overview["tenant_count"] == 3

    def test_ignores_non_numeric_and_negative(self):
        tenants = [{"tenant_id": "t1", "quota": {"max_agents": -5, "max_users": "10"}}]
        assert quota_audit.quota_overview(tenants)["allocation"]["values"] == [0]


class TestQuotaAuditRoutes:
    @pytest.fixture
    def client(self, qm: Any) -> TestClient:
        from maop.dashboard.routers import quotas as quotas_router
        quotas_router._set_manager(qm)
        app = FastAPI()
        app.include_router(quotas_router.router)

        from fastapi import Request as _Req
        from starlette.middleware.base import BaseHTTPMiddleware

        class _InjectAdmin(BaseHTTPMiddleware):
            async def dispatch(self, request: _Req, call_next):
                request.state.auth_roles = ["admin"]
                request.state.auth_identity = "auditor"
                return await call_next(request)

        app.add_middleware(_InjectAdmin)
        return TestClient(app)

    def test_overview_route_is_not_shadowed_by_tenant_id(self, client: TestClient, monkeypatch):
        """``/overview`` 必须早于 ``/{tenant_id}`` 注册，否则会被当租户 ID 吞掉."""
        monkeypatch.setattr(
            "maop.dashboard.routers.quotas.tenant_service.list_tenants",
            lambda status="": {"tenants": [
                {"tenant_id": "t9", "name": "Nine", "quota": {"max_agents": 3, "max_storage_mb": 100}},
            ]},
        )
        data = client.get("/api/quotas/overview").json()
        assert "allocation" in data
        assert data["allocation"]["labels"] == ["Nine"]
        assert data["allocation"]["values"] == [103]

    def test_trend_route_returns_buckets(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        client.put("/api/quotas/t1/api_calls/usage", json={"value": 25})
        data = client.get("/api/quotas/t1/trend", params={"hours": 5}).json()
        assert data["tenant_id"] == "t1"
        assert len(data["labels"]) == 5
        assert data["sampled"] is True
        assert data["series"][0]["data"][-1] == 25

    def test_trend_route_rejects_bad_hours(self, client: TestClient):
        assert client.get("/api/quotas/t1/trend", params={"hours": 0}).status_code == 422

    def test_history_route_lists_rows_written_by_set_quota(self, client: TestClient):
        client.post("/api/quotas/t1/api_calls", json={"hard_limit": 100})
        data = client.get("/api/quotas/history", params={"tenant_id": "t1"}).json()
        assert data["count"] >= 1
        assert all(r["changed_by"] == "auditor" for r in data["history"])
        assert all(r["changed_at"] <= time.time() for r in data["history"])

    def test_personal_edition_returns_404(self, qm: Any):
        from maop.config.edition import Edition, reset_edition, set_edition
        from maop.dashboard.routers import quotas as quotas_router
        set_edition(Edition.PERSONAL)
        try:
            app = FastAPI()
            app.include_router(quotas_router.router)

            from starlette.middleware.base import BaseHTTPMiddleware

            class _InjectAdmin(BaseHTTPMiddleware):
                async def dispatch(self, request, call_next):
                    request.state.auth_roles = ["admin"]
                    return await call_next(request)

            app.add_middleware(_InjectAdmin)
            client = TestClient(app)
            assert client.get("/api/quotas/overview").status_code == 404
            assert client.get("/api/quotas/t1/trend").status_code == 404
        finally:
            set_edition(Edition.ENTERPRISE)
            reset_edition()


class TestAuditRetention:
    def test_prune_removes_only_expired_rows(self):
        quota_audit.record_usage_sample("t-prune", "api_calls", 10)
        quota_audit.record_quota_change("t-prune", "api_calls", "hard_limit", 1, 2)
        # 直接把两行改写成"很久以前"，避免等 7/90 天
        from maop.core.backends.db_utils import sqlite_connect, unified_db_path

        old = time.time() - 400 * 86400.0
        with sqlite_connect(unified_db_path()) as conn:
            conn.execute("UPDATE quota_usage_samples SET sampled_at = ? WHERE tenant_id = ?",
                         (old, "t-prune"))
            conn.execute("UPDATE quota_history SET changed_at = ? WHERE tenant_id = ?",
                         (old, "t-prune"))

        deleted = quota_audit.prune_audit()
        assert deleted["samples"] >= 1
        assert deleted["history"] >= 1
        assert quota_audit.usage_trend("t-prune", hours=3)["sampled"] is False
        assert billing_service.list_quota_history(tenant_id="t-prune")["count"] == 0

    def test_prune_keeps_recent_rows(self):
        quota_audit.record_usage_sample("t-keep", "api_calls", 5)
        deleted = quota_audit.prune_audit()
        assert deleted["samples"] == 0
        assert quota_audit.usage_trend("t-keep", hours=2)["sampled"] is True


class TestAuditWriteFailureDoesNotBreakQuota:
    """审计写失败只留日志：配额本身已经改成功，回滚反而更糟。"""

    def test_history_write_error_is_contained(self, qm: Any, monkeypatch):
        import sqlite3

        def _boom(*a: Any, **k: Any):
            raise sqlite3.OperationalError("audit db locked")

        monkeypatch.setattr(quota_audit, "sqlite_connect", _boom)
        quota = billing_service.set_quota("t-err", "api_calls", 100)
        assert quota["hard_limit"] == 100
        assert qm.get_quota("t-err", "api_calls").hard_limit == 100

    def test_sample_write_error_is_contained(self, qm: Any, monkeypatch):
        import sqlite3

        def _boom(*a: Any, **k: Any):
            raise sqlite3.OperationalError("audit db locked")

        billing_service.set_quota("t-err2", "api_calls", 100)
        monkeypatch.setattr(quota_audit, "sqlite_connect", _boom)
        assert billing_service.set_quota_usage("t-err2", "api_calls", 42) == 42
