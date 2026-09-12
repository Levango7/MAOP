"""Tests for the agent-versions router — Agent 版本管理 / 灰度发布.

覆盖:
  - 创建版本 (POST /api/agent-versions) — admin only
  - 列出版本 (GET /api/agent-versions) — IDOR 防护 + 过滤 + 分页
  - 获取详情 (GET /api/agent-versions/{id}) — IDOR 防护
  - 更新版本 (PUT /api/agent-versions/{id}) — admin only
  - 删除版本 (DELETE /api/agent-versions/{id}) — admin only, active 阻止
  - 激活版本 (POST /api/agent-versions/{id}/activate) — 全量切换
  - 回滚版本 (POST /api/agent-versions/{id}/rollback)
  - 灰度配置 (POST/GET /api/agent-versions/{id}/canary)
  - 指标查询 (GET /api/agent-versions/{id}/metrics) — 优雅降级
  - Pydantic 校验 (percentage 范围, agent_id 必填)
  - 401 未认证 / 403 admin required / 404 不存在 / 409 冲突
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import agent_versions as av_router


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_schema():
    """每个测试前重置 schema 初始化标志, 确保使用隔离的 DB."""
    av_router._reset_schema_for_tests()
    yield
    av_router._reset_schema_for_tests()


def _make_app(roles: list[str], user_id: str = "test-user", tenant_id: str = "") -> FastAPI:
    """构造带注入鉴权状态的 FastAPI app."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles
        request.state.auth_identity = user_id
        request.state.tenant_id = tenant_id
        return await call_next(request)

    app.include_router(av_router.router)
    return app


@pytest.fixture
def admin_client():
    """admin 角色客户端."""
    return TestClient(_make_app(roles=["admin"], user_id="admin-1"))


@pytest.fixture
def user_client():
    """普通用户客户端."""
    return TestClient(_make_app(roles=["user"], user_id="user-1"))


@pytest.fixture
def other_user_client():
    """另一个普通用户客户端 (用于 IDOR 测试)."""
    return TestClient(_make_app(roles=["user"], user_id="user-2"))


@pytest.fixture
def anon_client():
    """未认证客户端 (无 auth_identity)."""
    app = FastAPI()

    @app.middleware("http")
    async def _no_auth(request, call_next):
        request.state.auth_roles = []
        request.state.auth_identity = ""
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(av_router.router)
    return TestClient(app)


def _create_version(client: TestClient, **overrides) -> dict:
    """创建一个版本的辅助函数."""
    payload = {
        "agent_id": "agent-001",
        "version_number": "1.0.0",
        "config_snapshot": {"model": "gpt-4", "temperature": 0.7},
        "changelog": "initial release",
    }
    payload.update(overrides)
    r = client.post("/api/agent-versions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ── 创建版本 ─────────────────────────────────────────────────────


class TestCreateVersion:
    def test_create_basic(self, admin_client):
        r = admin_client.post("/api/agent-versions", json={
            "agent_id": "agent-001",
            "version_number": "1.0.0",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["data"]["version_id"].startswith("ver_")

    def test_create_with_full_payload(self, admin_client):
        data = _create_version(
            admin_client,
            config_snapshot={"model": "gpt-4", "temperature": 0.7},
            changelog="initial release",
            target_user_groups=["beta", "internal"],
        )
        assert data["status"] == "ok"

    def test_create_non_admin_forbidden(self, user_client):
        r = user_client.post("/api/agent-versions", json={
            "agent_id": "agent-001",
            "version_number": "1.0.0",
        })
        assert r.status_code == 403

    def test_create_unauthenticated(self, anon_client):
        # admin 端点用 require_admin 检查, 未认证 (空角色) → 403
        r = anon_client.post("/api/agent-versions", json={
            "agent_id": "agent-001",
            "version_number": "1.0.0",
        })
        assert r.status_code == 403

    def test_create_missing_agent_id(self, admin_client):
        r = admin_client.post("/api/agent-versions", json={
            "version_number": "1.0.0",
        })
        assert r.status_code == 422

    def test_create_missing_version_number(self, admin_client):
        r = admin_client.post("/api/agent-versions", json={
            "agent_id": "agent-001",
        })
        assert r.status_code == 422

    def test_create_empty_agent_id(self, admin_client):
        r = admin_client.post("/api/agent-versions", json={
            "agent_id": "",
            "version_number": "1.0.0",
        })
        assert r.status_code == 422


# ── 列出版本 ─────────────────────────────────────────────────────


class TestListVersions:
    def test_list_empty(self, user_client):
        r = user_client.get("/api/agent-versions")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["data"] == []
        assert data["meta"]["total"] == 0

    def test_list_own_versions(self, admin_client):
        _create_version(admin_client, agent_id="a1", version_number="1.0.0")
        _create_version(admin_client, agent_id="a2", version_number="2.0.0")
        r = admin_client.get("/api/agent-versions")
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["total"] == 2

    def test_list_non_admin_isolated(self, admin_client, user_client, other_user_client):
        """非 admin 用户只能看自己创建的版本 — IDOR 防护."""
        # admin 创建两个版本
        _create_version(admin_client, agent_id="a1", version_number="1.0.0")
        _create_version(admin_client, agent_id="a2", version_number="2.0.0")
        # user-1 看不到 admin 创建的版本
        r = user_client.get("/api/agent-versions")
        assert r.json()["meta"]["total"] == 0

    def test_list_admin_sees_all(self, admin_client, user_client):
        _create_version(admin_client, agent_id="a1", version_number="1.0.0")
        # user-1 无法创建 (非 admin), 所以用 admin 创建模拟
        _create_version(admin_client, agent_id="a2", version_number="2.0.0")
        r = admin_client.get("/api/agent-versions")
        assert r.json()["meta"]["total"] == 2

    def test_list_filter_by_agent_id(self, admin_client):
        _create_version(admin_client, agent_id="a1", version_number="1.0.0")
        _create_version(admin_client, agent_id="a2", version_number="2.0.0")
        r = admin_client.get("/api/agent-versions?agent_id=a1")
        data = r.json()
        assert data["meta"]["total"] == 1
        assert data["data"][0]["agent_id"] == "a1"

    def test_list_filter_by_status(self, admin_client):
        _create_version(admin_client, agent_id="a1", version_number="1.0.0")
        r = admin_client.get("/api/agent-versions?status=draft")
        data = r.json()
        assert data["meta"]["total"] == 1
        assert data["data"][0]["status"] == "draft"

    def test_list_invalid_status(self, admin_client):
        r = admin_client.get("/api/agent-versions?status=invalid")
        assert r.status_code == 400

    def test_list_pagination(self, admin_client):
        for i in range(5):
            _create_version(admin_client, agent_id=f"a{i}", version_number=f"1.{i}.0")
        r = admin_client.get("/api/agent-versions?page=1&page_size=2")
        data = r.json()
        assert data["meta"]["total"] == 5
        assert len(data["data"]) == 2
        r = admin_client.get("/api/agent-versions?page=3&page_size=2")
        assert len(r.json()["data"]) == 1

    def test_list_unauthenticated(self, anon_client):
        r = anon_client.get("/api/agent-versions")
        assert r.status_code == 401


# ── 获取版本详情 ─────────────────────────────────────────────────


class TestGetVersion:
    def test_get_existing(self, admin_client):
        vid = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        r = admin_client.get(f"/api/agent-versions/{vid}")
        assert r.status_code == 200
        item = r.json()["data"]
        assert item["version_id"] == vid
        assert item["agent_id"] == "a1"
        assert item["version_number"] == "1.0.0"
        assert item["status"] == "draft"
        assert item["config_snapshot"] == {"model": "gpt-4", "temperature": 0.7}

    def test_get_nonexistent(self, admin_client):
        r = admin_client.get("/api/agent-versions/ver_nonexistent")
        assert r.status_code == 404

    def test_get_idor_blocked(self, admin_client, user_client):
        """admin 创建的版本, 普通用户不能访问 — IDOR 防护."""
        vid = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        r = user_client.get(f"/api/agent-versions/{vid}")
        assert r.status_code == 404  # 不泄露存在性

    def test_get_unauthenticated(self, anon_client):
        r = anon_client.get("/api/agent-versions/ver_x")
        assert r.status_code == 401


# ── 更新版本 ─────────────────────────────────────────────────────


class TestUpdateVersion:
    def test_update_version_number(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.put(f"/api/agent-versions/{vid}", json={"version_number": "2.0.0"})
        assert r.status_code == 200
        assert r.json()["data"]["version_number"] == "2.0.0"

    def test_update_config_snapshot(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.put(f"/api/agent-versions/{vid}", json={
            "config_snapshot": {"model": "claude-3", "temperature": 0.5}
        })
        assert r.status_code == 200
        assert r.json()["data"]["config_snapshot"] == {"model": "claude-3", "temperature": 0.5}

    def test_update_changelog(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.put(f"/api/agent-versions/{vid}", json={"changelog": "updated notes"})
        assert r.status_code == 200
        assert r.json()["data"]["changelog"] == "updated notes"

    def test_update_no_fields(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.put(f"/api/agent-versions/{vid}", json={})
        assert r.status_code == 200
        assert r.json()["data"]["version_number"] == "1.0.0"

    def test_update_non_admin_forbidden(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.put(f"/api/agent-versions/{vid}", json={"version_number": "2.0.0"})
        assert r.status_code == 403

    def test_update_nonexistent(self, admin_client):
        r = admin_client.put("/api/agent-versions/ver_x", json={"version_number": "2.0.0"})
        assert r.status_code == 404

    def test_update_unauthenticated(self, anon_client):
        # admin 端点 → 403
        r = anon_client.put("/api/agent-versions/ver_x", json={"version_number": "2.0.0"})
        assert r.status_code == 403


# ── 删除版本 ─────────────────────────────────────────────────────


class TestDeleteVersion:
    def test_delete_existing(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.delete(f"/api/agent-versions/{vid}")
        assert r.status_code == 200
        assert r.json()["data"]["deleted"] is True
        # 确认状态变为 deleted
        r = admin_client.get(f"/api/agent-versions/{vid}")
        assert r.json()["data"]["status"] == "deleted"

    def test_delete_active_blocked(self, admin_client):
        """active 版本不允许直接删除."""
        vid = _create_version(admin_client)["data"]["version_id"]
        # 激活
        r = admin_client.post(f"/api/agent-versions/{vid}/activate")
        assert r.status_code == 200
        # 删除应被拒绝
        r = admin_client.delete(f"/api/agent-versions/{vid}")
        assert r.status_code == 400

    def test_delete_non_admin_forbidden(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.delete(f"/api/agent-versions/{vid}")
        assert r.status_code == 403

    def test_delete_nonexistent(self, admin_client):
        r = admin_client.delete("/api/agent-versions/ver_x")
        assert r.status_code == 404

    def test_delete_unauthenticated(self, anon_client):
        # admin 端点 → 403
        r = anon_client.delete("/api/agent-versions/ver_x")
        assert r.status_code == 403


# ── 激活版本 ─────────────────────────────────────────────────────


class TestActivateVersion:
    def test_activate_version(self, admin_client):
        vid = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/activate")
        assert r.status_code == 200
        item = r.json()["data"]
        assert item["status"] == "active"
        assert item["activated_at"] is not None

    def test_activate_retires_other_active(self, admin_client):
        """激活新版本时, 同 agent 的其他 active 版本自动转为 retired."""
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a1", version_number="2.0.0")["data"]["version_id"]
        # 激活 v1
        admin_client.post(f"/api/agent-versions/{v1}/activate")
        # 激活 v2 → v1 应变为 retired
        r = admin_client.post(f"/api/agent-versions/{v2}/activate")
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "active"
        # v1 应为 retired
        r = admin_client.get(f"/api/agent-versions/{v1}")
        assert r.json()["data"]["status"] == "retired"

    def test_activate_non_admin_forbidden(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.post(f"/api/agent-versions/{vid}/activate")
        assert r.status_code == 403

    def test_activate_nonexistent(self, admin_client):
        r = admin_client.post("/api/agent-versions/ver_x/activate")
        assert r.status_code == 404

    def test_activate_deleted_blocked(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        admin_client.delete(f"/api/agent-versions/{vid}")
        r = admin_client.post(f"/api/agent-versions/{vid}/activate")
        assert r.status_code == 400

    def test_activate_unauthenticated(self, anon_client):
        # admin 端点 → 403
        r = anon_client.post("/api/agent-versions/ver_x/activate")
        assert r.status_code == 403


# ── 回滚版本 ─────────────────────────────────────────────────────


class TestRollbackVersion:
    def test_rollback_success(self, admin_client):
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a1", version_number="2.0.0")["data"]["version_id"]
        # 激活 v2
        admin_client.post(f"/api/agent-versions/{v2}/activate")
        # 回滚到 v1
        r = admin_client.post(f"/api/agent-versions/{v2}/rollback", json={
            "target_version_id": v1,
            "reason": "v2 has regression",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["data"]["status"] == "active"
        assert data["data"]["version_id"] == v1
        assert data["meta"]["rolled_back_from"] == v2
        assert data["meta"]["rolled_back_to"] == v1
        # v2 应变为 retired
        r = admin_client.get(f"/api/agent-versions/{v2}")
        assert r.json()["data"]["status"] == "retired"

    def test_rollback_different_agent_blocked(self, admin_client):
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a2", version_number="1.0.0")["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{v2}/rollback", json={
            "target_version_id": v1,
        })
        assert r.status_code == 400

    def test_rollback_target_nonexistent(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/rollback", json={
            "target_version_id": "ver_nonexistent",
        })
        assert r.status_code == 404

    def test_rollback_current_nonexistent(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post("/api/agent-versions/ver_nonexistent/rollback", json={
            "target_version_id": vid,
        })
        assert r.status_code == 404

    def test_rollback_to_deleted_blocked(self, admin_client):
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a1", version_number="2.0.0")["data"]["version_id"]
        admin_client.delete(f"/api/agent-versions/{v1}")
        r = admin_client.post(f"/api/agent-versions/{v2}/rollback", json={
            "target_version_id": v1,
        })
        assert r.status_code == 400

    def test_rollback_non_admin_forbidden(self, admin_client, user_client):
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a1", version_number="2.0.0")["data"]["version_id"]
        r = user_client.post(f"/api/agent-versions/{v2}/rollback", json={
            "target_version_id": v1,
        })
        assert r.status_code == 403

    def test_rollback_unauthenticated(self, anon_client):
        # admin 端点 → 403
        r = anon_client.post("/api/agent-versions/ver_x/rollback", json={
            "target_version_id": "ver_y",
        })
        assert r.status_code == 403


# ── 灰度发布 ─────────────────────────────────────────────────────


class TestCanary:
    def test_configure_canary_basic(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={
            "percentage": 10,
            "target_user_groups": ["beta"],
            "strategy": "percentage",
        })
        assert r.status_code == 200
        canary = r.json()["data"]
        assert canary["percentage"] == 10
        assert canary["target_user_groups"] == ["beta"]
        assert canary["status"] == "active"
        assert canary["canary_id"].startswith("canary_")

    def test_configure_canary_with_duration(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={
            "percentage": 50,
            "duration_seconds": 3600,
        })
        assert r.status_code == 200
        assert r.json()["data"]["duration_seconds"] == 3600

    def test_canary_duplicate_conflict(self, admin_client):
        """同一版本同时只允许一个 active 灰度."""
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 10})
        assert r.status_code == 200
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 20})
        assert r.status_code == 409

    def test_canary_invalid_percentage_too_low(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": -1})
        assert r.status_code == 422

    def test_canary_invalid_percentage_too_high(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 101})
        assert r.status_code == 422

    def test_canary_invalid_strategy(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={
            "percentage": 10,
            "strategy": "invalid",
        })
        assert r.status_code == 422

    def test_canary_non_admin_forbidden(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 10})
        assert r.status_code == 403

    def test_canary_nonexistent_version(self, admin_client):
        r = admin_client.post("/api/agent-versions/ver_x/canary", json={"percentage": 10})
        assert r.status_code == 404

    def test_canary_deleted_version_blocked(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        admin_client.delete(f"/api/agent-versions/{vid}")
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 10})
        assert r.status_code == 400

    def test_canary_unauthenticated(self, anon_client):
        # admin 端点 → 403
        r = anon_client.post("/api/agent-versions/ver_x/canary", json={"percentage": 10})
        assert r.status_code == 403

    def test_get_canary_status(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        admin_client.post(f"/api/agent-versions/{vid}/canary", json={
            "percentage": 25,
            "target_user_groups": ["beta", "internal"],
        })
        r = admin_client.get(f"/api/agent-versions/{vid}/canary")
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["count"] == 1
        assert data["data"][0]["percentage"] == 25
        assert data["data"][0]["target_user_groups"] == ["beta", "internal"]

    def test_get_canary_empty(self, admin_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.get(f"/api/agent-versions/{vid}/canary")
        assert r.status_code == 200
        assert r.json()["data"] == []
        assert r.json()["meta"]["count"] == 0

    def test_get_canary_idor_blocked(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.get(f"/api/agent-versions/{vid}/canary")
        assert r.status_code == 404

    def test_get_canary_unauthenticated(self, anon_client):
        r = anon_client.get("/api/agent-versions/ver_x/canary")
        assert r.status_code == 401


# ── 指标查询 ─────────────────────────────────────────────────────


class TestMetrics:
    def test_get_metrics_basic(self, admin_client):
        vid = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        r = admin_client.get(f"/api/agent-versions/{vid}/metrics")
        assert r.status_code == 200
        m = r.json()["data"]
        assert m["version_id"] == vid
        assert m["agent_id"] == "a1"
        assert m["version_number"] == "1.0.0"
        assert "success_rate_pct" in m
        assert "avg_latency_ms" in m
        assert "error_rate_pct" in m
        assert "comparison" in m

    def test_metrics_graceful_degradation(self, admin_client):
        """个人版无真实指标数据 → 返回零值 + note."""
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.get(f"/api/agent-versions/{vid}/metrics")
        m = r.json()["data"]
        assert m["success_rate_pct"] == 0.0
        assert m["avg_latency_ms"] == 0.0
        assert m["error_rate_pct"] == 0.0
        assert m["total_requests"] == 0
        assert "note" in m

    def test_metrics_with_comparison(self, admin_client):
        """有 active 版本时, comparison 包含对比信息."""
        v1 = _create_version(admin_client, agent_id="a1", version_number="1.0.0")["data"]["version_id"]
        v2 = _create_version(admin_client, agent_id="a1", version_number="2.0.0")["data"]["version_id"]
        admin_client.post(f"/api/agent-versions/{v2}/activate")
        # 查询 v1 指标, 应对比 v2
        r = admin_client.get(f"/api/agent-versions/{v1}/metrics")
        m = r.json()["data"]
        assert m["comparison"]["active_version_id"] == v2
        assert m["comparison"]["active_version_number"] == "2.0.0"

    def test_metrics_with_canary(self, admin_client):
        """有灰度配置时, metrics 包含 canary 信息."""
        vid = _create_version(admin_client)["data"]["version_id"]
        admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 30})
        r = admin_client.get(f"/api/agent-versions/{vid}/metrics")
        m = r.json()["data"]
        assert m["canary"] is not None
        assert m["canary"]["percentage"] == 30

    def test_metrics_idor_blocked(self, admin_client, user_client):
        vid = _create_version(admin_client)["data"]["version_id"]
        r = user_client.get(f"/api/agent-versions/{vid}/metrics")
        assert r.status_code == 404

    def test_metrics_nonexistent(self, admin_client):
        r = admin_client.get("/api/agent-versions/ver_x/metrics")
        assert r.status_code == 404

    def test_metrics_unauthenticated(self, anon_client):
        r = anon_client.get("/api/agent-versions/ver_x/metrics")
        assert r.status_code == 401


# ── 路由顺序验证 (静态路由不被 /{version_id} 遮蔽) ─────────────


class TestRouteOrder:
    def test_canary_not_shadowed(self, admin_client):
        """POST /api/agent-versions/{id}/canary 不应被 /{version_id} 匹配."""
        vid = _create_version(admin_client)["data"]["version_id"]
        # 如果路由顺序错误, 这会匹配到 GET /{version_id} 并返回 405
        r = admin_client.post(f"/api/agent-versions/{vid}/canary", json={"percentage": 10})
        assert r.status_code == 200

    def test_activate_not_shadowed(self, admin_client):
        """POST /api/agent-versions/{id}/activate 不应被 /{version_id} 匹配."""
        vid = _create_version(admin_client)["data"]["version_id"]
        r = admin_client.post(f"/api/agent-versions/{vid}/activate")
        assert r.status_code == 200