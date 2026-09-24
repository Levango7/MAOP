"""Tests for ``GET /api/hooks/{hook_id}/history``（2026-09-23 新增）。

背景：前端 Webhooks 的「投递历史」面板调用本端点，但后端此前没有 ——
请求落到 SPA 兜底返回 200 + HTML（该调用带 try/catch 降级，故只表现为
面板内报错，不崩页面）。

实现要点（本测试要守护的性质）：
1. 数据来自 ``hook_logs`` 表 —— ``HookManager._log_result`` 在**成功与异常
   两条路径**都会写入，所以"投递历史"是有真实来源的，不是新造的存储。
2. 必须按 **hook_id** 过滤。既有 ``/api/hook/logs`` 按 **event** 过滤，
   会混入同事件的其他 hook —— 这正是单 hook 历史视图不能复用它
   的原因。
3. ``response_code`` 是 2026-09-23 新增的**结构化**字段（此前状态码只以
   字符串形式塞在 ``response`` 里，前端拿不到数字）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    from maop.dashboard.routers.hooks import router
    app.include_router(router)
    return app


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """真实 HookManager，DB 隔离到 tmp_path。"""
    import maop.dashboard.routers.hooks as hooks_mod
    from maop.core.agent.plugins_hooks.hook_manager import HookManager

    m = HookManager(root_dir=str(tmp_path))
    monkeypatch.setattr(hooks_mod, "_get_hook_mgr", lambda: m)
    return m


@pytest.fixture
def client(mgr) -> TestClient:
    return TestClient(_make_app())


def _seed(mgr, hook_id: str, *, event: str = "agent.post_dispatch",
          success: bool = True, code: int = 200, ms: int = 12,
          error: str = "") -> None:
    """写入一条投递记录 —— 走 ``_log_result``，即投递路径实际调用的那个方法。"""
    from maop.core.agent.plugins_hooks.hook_manager import HookResult

    mgr._log_result(HookResult(
        hook_id=hook_id, event=event, success=success,
        error=error, duration_ms=ms, response_code=code,
    ))


def _create_hook(client: TestClient, name: str = "h1") -> str:
    resp = client.post("/api/hooks", json={
        "name": name,
        "event": "agent.post_dispatch",
        "url": "https://example.com/hook",
        "method": "POST",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestHookHistory:
    def test_unknown_hook_404(self, client: TestClient):
        assert client.get("/api/hooks/nope/history").status_code == 404

    def test_empty_history(self, client: TestClient):
        hid = _create_hook(client)
        resp = client.get(f"/api/hooks/{hid}/history")
        assert resp.status_code == 200
        d = resp.json()
        assert d["records"] == []
        assert d["count"] == 0

    def test_records_shape_matches_frontend(self, client: TestClient, mgr):
        """前端读 ``d.items || d.history || d.records``，且用到
        time/event/status/latency_ms/response_code —— 逐字段校验。"""
        hid = _create_hook(client)
        _seed(mgr, hid, success=True, code=200, ms=15)

        d = client.get(f"/api/hooks/{hid}/history").json()
        assert d["count"] == 1
        r = d["records"][0]
        for key in ("id", "time", "event", "status", "latency_ms", "response_code"):
            assert key in r, f"缺少前端需要的字段 {key}"
        assert r["status"] == "ok"
        assert r["response_code"] == 200
        assert r["latency_ms"] == 15

    def test_failed_delivery_marked_failed(self, client: TestClient, mgr):
        hid = _create_hook(client)
        _seed(mgr, hid, success=False, code=500, ms=30, error="boom")
        r = client.get(f"/api/hooks/{hid}/history").json()["records"][0]
        assert r["status"] == "failed"
        assert r["response_code"] == 500
        assert r["error"] == "boom"

    def test_filtered_by_hook_id_not_event(self, client: TestClient, mgr):
        """**核心性质**：两个 hook 注册同一事件时，各自的历史不能串。

        这正是不能复用按 event 过滤的 /api/hook/logs 的原因。
        """
        h1 = _create_hook(client, "a")
        h2 = _create_hook(client, "b")
        assert h1 != h2

        _seed(mgr, h1, ms=11)
        _seed(mgr, h1, ms=12)
        _seed(mgr, h2, ms=99)

        d1 = client.get(f"/api/hooks/{h1}/history").json()
        d2 = client.get(f"/api/hooks/{h2}/history").json()
        assert d1["count"] == 2, f"h1 应只有自己的 2 条，实际 {d1['count']}"
        assert d2["count"] == 1, f"h2 应只有自己的 1 条，实际 {d2['count']}"
        assert d2["records"][0]["latency_ms"] == 99
        assert all(r["latency_ms"] in (11, 12) for r in d1["records"])

    def test_newest_first(self, client: TestClient, mgr):
        hid = _create_hook(client)
        _seed(mgr, hid, ms=1)
        _seed(mgr, hid, ms=2)
        _seed(mgr, hid, ms=3)
        times = [r["time"] for r in client.get(f"/api/hooks/{hid}/history").json()["records"]]
        assert times == sorted(times, reverse=True), "应按时间倒序（最新在前）"

    def test_limit_respected(self, client: TestClient, mgr):
        hid = _create_hook(client)
        for i in range(5):
            _seed(mgr, hid, ms=i)
        d = client.get(f"/api/hooks/{hid}/history", params={"limit": 2}).json()
        assert d["count"] == 2

    def test_response_code_defaults_zero(self, client: TestClient, mgr):
        """未发起 HTTP 的投递（callback 类型 / SSRF 拦截）response_code 为 0，
        不应是 None 或缺失（前端做数值比较）。"""
        hid = _create_hook(client)
        _seed(mgr, hid, success=False, code=0, error="SSRF blocked")
        r = client.get(f"/api/hooks/{hid}/history").json()["records"][0]
        assert r["response_code"] == 0
