"""Tests for the feedback router — 用户反馈评价系统.

覆盖:
  - 提交反馈 (POST /api/feedback)
  - 查询反馈列表 (GET /api/feedback) — 非 admin 仅看自己
  - 反馈摘要 (GET /api/feedback/summary)
  - 导出反馈 (GET /api/feedback/export) — admin only, csv/json
  - 更新反馈 (PUT /api/feedback/{id}) — IDOR 防护
  - 删除反馈 (DELETE /api/feedback/{id}) — IDOR 防护
  - Pydantic 校验 (rating 范围, target_type 白名单)
  - 401 未认证 / 404 不存在 / 403 admin required
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import feedback as feedback_router


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_schema():
    """每个测试前重置 schema 初始化标志, 确保使用隔离的 DB."""
    feedback_router._reset_schema_for_tests()
    yield
    feedback_router._reset_schema_for_tests()


def _make_app(roles: list[str], user_id: str = "test-user", tenant_id: str = "") -> FastAPI:
    """构造带注入鉴权状态的 FastAPI app."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth(request, call_next):
        request.state.auth_roles = roles
        request.state.auth_identity = user_id
        request.state.tenant_id = tenant_id
        return await call_next(request)

    app.include_router(feedback_router.router)
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

    app.include_router(feedback_router.router)
    return TestClient(app)


def _submit(client: TestClient, **overrides) -> dict:
    """提交一条反馈的辅助函数."""
    payload = {
        "target_type": "agent",
        "target_id": "agent-001",
        "rating": 4,
        "comment": "good",
        "tags": ["fast", "accurate"],
    }
    payload.update(overrides)
    r = client.post("/api/feedback", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ── 提交反馈 ─────────────────────────────────────────────────────


class TestCreateFeedback:
    def test_create_basic(self, user_client):
        r = user_client.post("/api/feedback", json={
            "target_type": "agent",
            "target_id": "a1",
            "rating": 5,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["feedback_id"].startswith("fb_")

    def test_create_with_comment_and_tags(self, user_client):
        data = _submit(user_client, comment="great work", tags=["fast", "good"])
        assert data["status"] == "ok"

    def test_create_invalid_target_type(self, user_client):
        r = user_client.post("/api/feedback", json={
            "target_type": "invalid",
            "target_id": "a1",
            "rating": 3,
        })
        assert r.status_code == 400

    def test_create_rating_out_of_range(self, user_client):
        # rating=0 < 1 → Pydantic 422
        r = user_client.post("/api/feedback", json={
            "target_type": "agent",
            "target_id": "a1",
            "rating": 0,
        })
        assert r.status_code == 422

        # rating=6 > 5 → Pydantic 422
        r = user_client.post("/api/feedback", json={
            "target_type": "agent",
            "target_id": "a1",
            "rating": 6,
        })
        assert r.status_code == 422

    def test_create_missing_target_id(self, user_client):
        r = user_client.post("/api/feedback", json={
            "target_type": "agent",
            "rating": 3,
        })
        assert r.status_code == 422

    def test_create_unauthenticated(self, anon_client):
        r = anon_client.post("/api/feedback", json={
            "target_type": "agent",
            "target_id": "a1",
            "rating": 3,
        })
        assert r.status_code == 401


# ── 查询反馈列表 ─────────────────────────────────────────────────


class TestListFeedback:
    def test_list_empty(self, user_client):
        r = user_client.get("/api/feedback")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["data"] == []
        assert data["total"] == 0

    def test_list_own_feedback(self, user_client):
        _submit(user_client, target_id="a1", rating=4)
        _submit(user_client, target_id="a2", rating=5)
        r = user_client.get("/api/feedback")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert all(f["user_id"] == "user-1" for f in data["data"])

    def test_list_non_admin_isolated(self, user_client, other_user_client):
        """非 admin 用户只能看自己的反馈 — IDOR 防护."""
        _submit(user_client, target_id="a1", rating=4)
        _submit(other_user_client, target_id="a2", rating=5)

        # user-1 只看到自己的 1 条
        r = user_client.get("/api/feedback")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["target_id"] == "a1"

        # user-2 只看到自己的 1 条
        r = other_user_client.get("/api/feedback")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["target_id"] == "a2"

    def test_list_admin_sees_all(self, admin_client, user_client, other_user_client):
        _submit(user_client, target_id="a1", rating=4)
        _submit(other_user_client, target_id="a2", rating=5)
        r = admin_client.get("/api/feedback")
        assert r.json()["total"] == 2

    def test_list_filter_by_target_type(self, user_client):
        _submit(user_client, target_type="agent", target_id="a1")
        _submit(user_client, target_type="task", target_id="t1")
        r = user_client.get("/api/feedback?target_type=agent")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["target_type"] == "agent"

    def test_list_filter_by_target_id(self, user_client):
        _submit(user_client, target_id="a1")
        _submit(user_client, target_id="a2")
        r = user_client.get("/api/feedback?target_id=a1")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["target_id"] == "a1"

    def test_list_filter_by_rating(self, user_client):
        _submit(user_client, rating=3)
        _submit(user_client, rating=5)
        r = user_client.get("/api/feedback?rating=5")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["rating"] == 5

    def test_list_pagination(self, user_client):
        for i in range(5):
            _submit(user_client, target_id=f"a{i}")
        r = user_client.get("/api/feedback?page=1&page_size=2")
        assert r.json()["total"] == 5
        assert len(r.json()["data"]) == 2
        r = user_client.get("/api/feedback?page=3&page_size=2")
        assert len(r.json()["data"]) == 1

    def test_list_filter_by_date(self, user_client):
        now = time.time()
        _submit(user_client, target_id="old")
        # date_from 过滤 — 只看 1 秒前的
        r = user_client.get(f"/api/feedback?date_from={now - 1}")
        assert r.json()["total"] >= 1
        # date_to 过滤 — 未来时间不含任何记录
        r = user_client.get(f"/api/feedback?date_to={now - 100}")
        assert r.json()["total"] == 0

    def test_list_unauthenticated(self, anon_client):
        r = anon_client.get("/api/feedback")
        assert r.status_code == 401


# ── 反馈摘要 ─────────────────────────────────────────────────────


class TestSummary:
    def test_summary_empty(self, user_client):
        r = user_client.get("/api/feedback/summary")
        assert r.status_code == 200
        s = r.json()["summary"]
        assert s["total"] == 0
        assert s["average_rating"] == 0.0

    def test_summary_aggregation(self, user_client):
        _submit(user_client, rating=4, tags=["fast"], comment="ok")
        _submit(user_client, rating=5, tags=["fast", "good"], comment="great")
        _submit(user_client, rating=3, tags=["slow"], comment="")
        r = user_client.get("/api/feedback/summary")
        assert r.status_code == 200
        s = r.json()["summary"]
        assert s["total"] == 3
        assert s["average_rating"] == pytest.approx(4.0, abs=0.01)
        assert s["comment_count"] == 2
        # 评分分布
        assert s["rating_distribution"]["3"] == 1
        assert s["rating_distribution"]["4"] == 1
        assert s["rating_distribution"]["5"] == 1
        # 标签词频
        assert s["tag_frequency"]["fast"] == 2
        assert s["tag_frequency"]["good"] == 1
        assert s["tag_frequency"]["slow"] == 1

    def test_summary_filter_by_target_type(self, user_client):
        _submit(user_client, target_type="agent", rating=5)
        _submit(user_client, target_type="task", rating=1)
        r = user_client.get("/api/feedback/summary?target_type=agent")
        s = r.json()["summary"]
        assert s["total"] == 1
        assert s["average_rating"] == 5.0

    def test_summary_non_admin_isolated(self, user_client, other_user_client):
        _submit(user_client, rating=5)
        _submit(other_user_client, rating=1)
        r = user_client.get("/api/feedback/summary")
        assert r.json()["summary"]["total"] == 1
        assert r.json()["summary"]["average_rating"] == 5.0


# ── 更新反馈 ─────────────────────────────────────────────────────


class TestUpdateFeedback:
    def test_update_rating(self, user_client):
        fb_id = _submit(user_client, rating=3)["feedback_id"]
        r = user_client.put(f"/api/feedback/{fb_id}", json={"rating": 5})
        assert r.status_code == 200
        assert r.json()["feedback"]["rating"] == 5

    def test_update_comment(self, user_client):
        fb_id = _submit(user_client, comment="old")["feedback_id"]
        r = user_client.put(f"/api/feedback/{fb_id}", json={"comment": "new comment"})
        assert r.status_code == 200
        assert r.json()["feedback"]["comment"] == "new comment"

    def test_update_tags(self, user_client):
        fb_id = _submit(user_client, tags=["a"])["feedback_id"]
        r = user_client.put(f"/api/feedback/{fb_id}", json={"tags": ["b", "c"]})
        assert r.status_code == 200
        assert r.json()["feedback"]["tags"] == ["b", "c"]

    def test_update_no_fields(self, user_client):
        fb_id = _submit(user_client, rating=4)["feedback_id"]
        r = user_client.put(f"/api/feedback/{fb_id}", json={})
        assert r.status_code == 200
        assert r.json()["feedback"]["rating"] == 4

    def test_update_idor_blocked(self, user_client, other_user_client):
        """user-1 提交的反馈, user-2 不能更新 — IDOR 防护."""
        fb_id = _submit(user_client)["feedback_id"]
        r = other_user_client.put(f"/api/feedback/{fb_id}", json={"rating": 1})
        assert r.status_code == 404  # 不泄露存在性

    def test_update_admin_can_update_others(self, admin_client, user_client):
        fb_id = _submit(user_client, rating=3)["feedback_id"]
        r = admin_client.put(f"/api/feedback/{fb_id}", json={"rating": 5})
        assert r.status_code == 200
        assert r.json()["feedback"]["rating"] == 5

    def test_update_nonexistent(self, user_client):
        r = user_client.put("/api/feedback/fb_nonexistent", json={"rating": 5})
        assert r.status_code == 404

    def test_update_invalid_rating(self, user_client):
        fb_id = _submit(user_client)["feedback_id"]
        r = user_client.put(f"/api/feedback/{fb_id}", json={"rating": 99})
        assert r.status_code == 422

    def test_update_unauthenticated(self, anon_client):
        r = anon_client.put("/api/feedback/fb_x", json={"rating": 5})
        assert r.status_code == 401


# ── 删除反馈 ─────────────────────────────────────────────────────


class TestDeleteFeedback:
    def test_delete_own(self, user_client):
        fb_id = _submit(user_client)["feedback_id"]
        r = user_client.delete(f"/api/feedback/{fb_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        # 确认已删除
        r = user_client.get("/api/feedback")
        assert r.json()["total"] == 0

    def test_delete_idor_blocked(self, user_client, other_user_client):
        """user-1 提交的反馈, user-2 不能删除 — IDOR 防护."""
        fb_id = _submit(user_client)["feedback_id"]
        r = other_user_client.delete(f"/api/feedback/{fb_id}")
        assert r.status_code == 404

    def test_delete_admin_can_delete_others(self, admin_client, user_client):
        fb_id = _submit(user_client)["feedback_id"]
        r = admin_client.delete(f"/api/feedback/{fb_id}")
        assert r.status_code == 200

    def test_delete_nonexistent(self, user_client):
        r = user_client.delete("/api/feedback/fb_nonexistent")
        assert r.status_code == 404

    def test_delete_unauthenticated(self, anon_client):
        r = anon_client.delete("/api/feedback/fb_x")
        assert r.status_code == 401


# ── 导出反馈 (admin only) ────────────────────────────────────────


class TestExportFeedback:
    def test_export_csv_admin(self, admin_client, user_client):
        _submit(user_client, rating=4, comment="hello", tags=["a", "b"])
        r = admin_client.get("/api/feedback/export?format=csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        body = r.text
        assert "feedback_id" in body  # header row
        assert "hello" in body

    def test_export_json_admin(self, admin_client, user_client):
        _submit(user_client, rating=5)
        r = admin_client.get("/api/feedback/export?format=json")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1
        assert "data" in data

    def test_export_non_admin_forbidden(self, user_client):
        r = user_client.get("/api/feedback/export")
        assert r.status_code == 403

    def test_export_filter_by_target_type(self, admin_client, user_client):
        _submit(user_client, target_type="agent", target_id="a1")
        _submit(user_client, target_type="task", target_id="t1")
        r = admin_client.get("/api/feedback/export?format=json&target_type=agent")
        data = r.json()
        assert data["count"] == 1
        assert data["data"][0]["target_type"] == "agent"

    def test_export_invalid_format(self, admin_client):
        r = admin_client.get("/api/feedback/export?format=xml")
        assert r.status_code == 422


# ── 路由顺序验证 (静态路由不被 /{feedback_id} 遮蔽) ─────────────


class TestRouteOrder:
    def test_summary_not_shadowed(self, user_client):
        """GET /api/feedback/summary 不应被 /{feedback_id} 匹配."""
        r = user_client.get("/api/feedback/summary")
        assert r.status_code == 200
        assert "summary" in r.json()

    def test_export_not_shadowed(self, admin_client):
        """GET /api/feedback/export 不应被 /{feedback_id} 匹配."""
        r = admin_client.get("/api/feedback/export?format=json")
        assert r.status_code == 200