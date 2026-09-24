"""Tests for MCP router 新增端点（2026-09-23）：PUT /servers/{id} 与 GET /topology。

背景：前端 McpManager 的「编辑服务器」与 Tools 的「MCP 拓扑」标签页分别调用
这两个端点，但后端此前没有实现 —— 请求落到 SPA 兜底返回 **200 + HTML**，
前端 ``res.json()`` 抛 ``Unexpected token '<'``，功能不可用。

本测试用隔离的 hub（内存/临时 DB）注入到 service 层，避免污染主库。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.dashboard.routers import mcp as mcp_route
from maop.dashboard.services import plugin_service

# ── Fixtures ─────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        return await call_next(request)

    app.include_router(mcp_route.router)
    return app


class _FakeHub:
    """最小 MCPHub 替身：只实现本次测试的端点所需的方法。

    刻意保持单文件、不依赖真实 MCP 传输（stdio/http），
    这样测试不启动子进程、不发网络请求。
    """

    def __init__(self) -> None:
        self._servers: dict[str, dict[str, Any]] = {}
        self._tools: list[dict[str, Any]] = []
        self._seq = 0

    # ── servers ──
    def list_servers(self) -> list[Any]:
        from maop.core.mcp.mcp_hub_types import ServerInfo

        return [
            ServerInfo(
                id=sid,
                name=d["name"],
                transport=d["transport"],
                status=d["status"],
                tools_count=d["tools_count"],
            )
            for sid, d in self._servers.items()
        ]

    def add_server(self, config) -> bool:
        # 模拟真实 hub：按 name 覆盖 + 重新生成 id
        self._seq += 1
        sid = f"srv{self._seq}"
        for old_id, d in list(self._servers.items()):
            if d["name"] == config.name:
                del self._servers[old_id]
        self._servers[sid] = {
            "name": config.name,
            "transport": config.transport,
            "status": "disconnected",
            "tools_count": 0,
            "config": config,
        }
        return True

    def update_server(self, server_id: str, config) -> bool:
        if server_id not in self._servers:
            return False
        self._servers[server_id].update({
            "name": config.name,
            "transport": config.transport,
            "config": config,
        })
        return True

    def remove_server(self, name: str) -> bool:
        for sid, d in list(self._servers.items()):
            if d["name"] == name:
                del self._servers[sid]
                return True
        return False

    def all_tools(self) -> list[Any]:
        from maop.core.mcp.mcp_hub_types import MCPTool

        return [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                server_name=t["server_name"],
            )
            for t in self._tools
        ]


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> _FakeHub:
    fake = _FakeHub()
    monkeypatch.setattr(plugin_service, "_get_hub", lambda: fake)
    monkeypatch.setattr(mcp_route, "_get_hub", lambda: fake)
    return fake


@pytest.fixture
def client(hub: _FakeHub) -> TestClient:
    return TestClient(_make_app())


# ── PUT /api/mcp/servers/{server_id} ─────────────────────────────


class TestUpdateServer:
    def test_updates_in_place_preserving_id(self, client: TestClient, hub: _FakeHub):
        """更新必须**保留 id** —— 不能用 add_server 代替（它会重新生成 id）。"""
        client.post("/api/mcp/servers", json={"name": "svc", "transport": "stdio"})
        sid = client.get("/api/mcp/servers").json()["servers"][0]["id"]

        # TransportType 合法取值：stdio / sse / websocket / streamable_http
        resp = client.put(f"/api/mcp/servers/{sid}", json={
            "name": "svc", "transport": "sse", "url": "http://localhost:9000",
        })
        assert resp.status_code == 200
        servers = client.get("/api/mcp/servers").json()["servers"]
        assert len(servers) == 1, "原地更新不应新增 server"
        assert servers[0]["id"] == sid, "id 漂移了 —— 不能用 add_server 实现更新"
        assert servers[0]["transport"] == "sse"

    def test_unknown_id_returns_404(self, client: TestClient):
        """不存在的 id → 404。"""
        resp = client.put("/api/mcp/servers/nope", json={
            "name": "x", "transport": "stdio",
        })
        assert resp.status_code == 404

    def test_rename_does_not_leave_duplicate(self, client: TestClient):
        """改名场景下不能留下两条记录。"""
        client.post("/api/mcp/servers", json={"name": "old", "transport": "stdio"})
        sid = client.get("/api/mcp/servers").json()["servers"][0]["id"]
        client.put(f"/api/mcp/servers/{sid}", json={"name": "new", "transport": "stdio"})
        servers = client.get("/api/mcp/servers").json()["servers"]
        assert [s["name"] for s in servers] == ["new"]


# ── GET /api/mcp/topology ────────────────────────────────────────


class TestTopology:
    def test_shape_and_edges(self, client: TestClient, hub: _FakeHub):
        """返回四段结构；边只来自 tool.server_name，且每条边都有 id/source/target。"""
        client.post("/api/mcp/servers", json={"name": "fs", "transport": "stdio"})
        hub._tools = [
            {"name": "read_file", "server_name": "fs", "description": "读文件"},
            {"name": "write_file", "server_name": "fs", "description": "写文件"},
        ]

        resp = client.get("/api/mcp/topology")
        assert resp.status_code == 200
        d = resp.json()
        for key in ("servers", "tools", "agents", "edges"):
            assert key in d, f"缺少字段 {key}"

        assert [s["name"] for s in d["servers"]] == ["fs"]
        assert len(d["tools"]) == 2
        # MCPTool 无 id 字段，服务端必须合成，否则前端建不出节点
        assert all(t.get("id") for t in d["tools"]), "tool 缺少 id，前端无法建节点"

        assert len(d["edges"]) == 2
        for e in d["edges"]:
            assert e["id"] and e["source"] == "fs" and e["target"]
            assert e["type"] == "server-tool"

    def test_empty_topology(self, client: TestClient):
        """无数据时返回空结构而非报错。"""
        resp = client.get("/api/mcp/topology")
        assert resp.status_code == 200
        d = resp.json()
        assert d["servers"] == [] and d["tools"] == [] and d["edges"] == []

    def test_agent_list_failure_degrades(self, client: TestClient, hub: _FakeHub, monkeypatch):
        """Agent 注册表不可用时，拓扑其余部分仍应返回（降级而非整体失败）。"""
        from maop.dashboard.services import agent_service

        def _boom(_enabled):
            raise RuntimeError("registry down")

        monkeypatch.setattr(agent_service, "list_agents", _boom)
        client.post("/api/mcp/servers", json={"name": "fs", "transport": "stdio"})
        resp = client.get("/api/mcp/topology")
        assert resp.status_code == 200
        assert resp.json()["agents"] == []
        assert len(resp.json()["servers"]) == 1
