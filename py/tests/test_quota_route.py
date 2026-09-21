"""Tests for maop.dashboard.routers.quota — Agent 额度分桶管理 API 端点.

覆盖 6 个端点的正常路径 + 错误路径，使用隔离的 tmp_path DB 避免污染主库。
每个测试通过自定义中间件注入 admin 角色，绕过+ require_admin 鉴权。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any  # noqa: F401

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.billing.quota_bucket import QuotaBucket, QuotaEntry  # noqa: F401
from maop.dashboard.routers import quota as quota_route

# ── Fixtures ─────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """创建临时 app + 注入 admin 角色 + 注册 quota 路由。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(quota_route.router)
    return app


@pytest.fixture
def isolated_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QuotaBucket:
    """提供隔离 DB 的 QuotaBucket 并注入到路由模块单例。"""
    db_path = tmp_path / "quota_test.db"
    bucket = QuotaBucket(db_path=db_path)
    # Service 层提取后，单例由 billing_service 持有；router 暴露
    # _set_quota_bucket 转发函数供测试注入隔离实例。
    quota_route._set_quota_bucket(bucket)
    return bucket


@pytest.fixture
def client(isolated_bucket: QuotaBucket) -> TestClient:
    """TestClient with admin role injected."""
    return TestClient(_make_app())


# ── 1. GET /api/quota/{agent_name} ────────────────────────────────


class TestQuotaGet:
    def test_get_nonexistent_agent_returns_empty(self, client: TestClient):
        """不存在的 Agent 返回空额度配置。"""
        resp = client.get("/api/quota/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent_name"] == "nonexistent"
        assert data["quota"]["free_quota"] == 0
        assert data["quota"]["prepaid_quota"] == 0

    def test_get_after_set_returns_config(self, client: TestClient):
        """set 后 get 应返回设置的配置。"""
        client.put("/api/quota/agent1", json={
            "free_quota": 100,
            "prepaid_quota": 200,
            "postpaid_quota": 50,
            "reset_period": "monthly",
        })
        resp = client.get("/api/quota/agent1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quota"]["free_quota"] == 100
        assert data["quota"]["prepaid_quota"] == 200


# ── 2. PUT /Aapi/quota/{agent_name} ───────────────────────────────


class TestQuotaSet:
    def test_set_basic_quota(self, client: TestClient):
        """设置基本额度配置。"""
        resp = client.put("/api/quota/agent1", json={
            "free_quota": 100,
            "prepaid_quota": 200,
            "postpaid_quota": 50,
            "reset_period": "daily",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent_name"] == "agent1"

    def test_set_quota_with_defaults(self, client: TestClient):
        """使用默认值设置额度。"""
        resp = client.put("/api/quota/agent2", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── 3. POST /api/quota/{agent_name}/consume ──────────────────────


class TestQuotaConsume:
    def test_consume_success(self, client: TestClient):
        """有足够额度时消耗成功。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        resp = client.post("/api/quota/agent1/consume", json={"amount": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"]["success"] is True
        assert data["result"]["consumed"] == 30

    def test_consume_insufficient_quota(self, client: TestClient):
        """额度不足时消耗失败。"""
        client.put("/api/quota/agent1", json={"free_quota": 10})
        resp = client.post("/api/quota/agent1/consume", json={"amount": 50})
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["success"] is False
        assert "insufficient" in data["result"]["reason"]

    def test_consume_zero_amount(self, client: TestClient):
        """消耗 0 额度应成功。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        resp = client.post("/api/quota/agent1/consume", json={"amount": 0})
        assert resp.status_code == 200
        assert resp.json()["result"]["success"] is True


# ── 4. POST /api/quota/{agent_name}/refund ───────────────────────


class TestQuotaRefund:
    def test_refund_to_specific_bucket(self, client: TestClient):
        """退还到指定桶。"""
        client.put("/api/quota/agent1", json={
            "free_quota": 100, "free_used": 50,
        })
        resp = client.post("/api/quota/agent1/refund", json={
            "amount": 20, "bucket": "free",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_refund_invalid_bucket(self, client: TestClient):
        """无效桶名返回 400。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        resp = client.post("/api/quota/agent1/refund", json={
            "amount": 10, "bucket": "invalid_bucket",
        })
        assert resp.status_code == 400

    def test_refund_auto_bucket(self, client: TestClient):
        """auto 桶自动选择退还目标。"""
        client.put("/api/quota/agent1", json={
            "free_quota": 100, "free_used": 30,
        })
        resp = client.post("/api/quota/agent1/refund", json={
            "amount": 10, "bucket": "auto",
        })
        assert resp.status_code == 200


# ── 5. GET /api/quota/{agent_name}/remaining ─────────────────────


class TestQuotaRemaining:
    def test_remaining_empty_agent(self, client: TestClient):
        """不存在的 Agent 剩余为 0。"""
        resp = client.get("/api/quota/nonexistent/remaining")
        assert resp.status_code == 200
        data = resp.json()
        assert data["remaining"]["free"] == 0
        assert data["total"] == 0

    def test_remaining_after_consume(self, client: TestClient):
        """消耗后剩余应减少。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        client.post("/api/quota/agent1/consume", json={"amount": 30})
        resp = client.get("/api/quota/agent1/remaining")
        assert resp.status_code == 200
        data = resp.json()
        assert data["remaining"]["free"] == 70
        assert data["total"] == 70


# ── 6. POST /api/quota/{agent_name}/reset ────────────────────────


class TestQuotaReset:
    def test_reset_all_buckets(self, client: TestClient):
        """重置所有桶。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        client.post("/api/quota/agent1/consume", json={"amount": 30})
        resp = client.post("/api/quota/agent1/reset", json={"bucket": "all"})
        assert resp.status_code == 200
        # 验证 used 已归零
        remaining = client.get("/api/quota/agent1/remaining").json()
        assert remaining["total"] == 100

    def test_reset_invalid_bucket(self, client: TestClient):
        """无效桶名返回 400。"""
        client.put("/api/quota/agent1", json={"free_quota": 100})
        resp = client.post("/api/quota/agent1/reset", json={"bucket": "invalid"})
        assert resp.status_code == 400

    def test_reset_nonexistent_agent_no_error(self, client: TestClient):
        """重置不存在的 Agent 应静默成功。"""
        resp = client.post("/api/quota/nonexistent/reset", json={"bucket": "all"})
        assert resp.status_code == 200