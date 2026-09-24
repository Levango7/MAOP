"""Tests for ``MCPHub.seed_from_config`` / ``POST /api/mcp/sync-config``
（2026-09-24 新增）。

## 背景

``config/mcp_servers.yaml`` 头部写明「servers MAOP can connect to」，
``MCPDiscovery`` 也写好了（docstring 甚至给出预期用法
``for cfg in configs: hub.add_server(cfg)``）—— 但那段代码**从来没被实现**：
``MCPDiscovery`` 与 ``ToolDiscovery`` 两个模块全仓**零调用方**，也没有任何
启动播种路径。结果是配置**静默失效**：写进去的服务器从未被连接，且无任何报错。

本测试守护修复后的性质：
1. 配置里的服务器**真的**被注册
2. **幂等** —— 重复播种不产生重复行、不破坏已有 id
3. 异常配置不炸启动
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.mcp.mcp_hub import MCPHub

YAML_TWO_SERVERS = """\
servers:
  - name: alpha
    transport: stdio
    command: echo
    args: ["hi"]
  - name: beta
    transport: sse
    url: http://127.0.0.1:9999/sse
"""


def _write_yaml(root: Path, content: str) -> None:
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "mcp_servers.yaml").write_text(content, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _write_yaml(tmp_path, YAML_TWO_SERVERS)
    return tmp_path


@pytest.fixture
def hub(root: Path) -> MCPHub:
    return MCPHub(root_dir=root)


class TestSeedFromConfig:
    def test_seeds_configured_servers(self, hub: MCPHub):
        """配置里的服务器必须真的被注册 —— 这是修复的核心。"""
        assert hub.list_servers() == [], "前置：hub 初始为空"

        result = hub.seed_from_config()
        names = sorted(s.name for s in hub.list_servers())
        assert names == ["alpha", "beta"], f"未注册配置中的服务器：{names}"
        assert sorted(result["added"]) == ["alpha", "beta"]
        assert result["skipped"] == []
        assert result["found"] == 2

    def test_is_idempotent(self, hub: MCPHub):
        """重复播种不得产生重复行，且 id 不变。"""
        hub.seed_from_config()
        first = {s.name: s.id for s in hub.list_servers()}

        result = hub.seed_from_config()
        second = {s.name: s.id for s in hub.list_servers()}

        assert len(second) == 2, f"重复播种产生了重复行：{second}"
        assert result["added"] == [], "第二次不应新增"
        assert sorted(result["skipped"]) == ["alpha", "beta"]
        assert first == second, "幂等播种不应改动 id"

    def test_preserves_existing_ids(self, hub: MCPHub):
        """**关键**：已注册服务器的 id 不能被播种改动。

        不能用「每次全量 add_server」实现播种 —— add_server 是
        DELETE WHERE name + INSERT 且**重新生成 uuid**，会让 id 漂移，
        破坏前端 McpManager 的 ``:key`` 与 ``PUT /servers/{id}``。
        """
        hub.seed_from_config()
        before = {s.name: s.id for s in hub.list_servers()}
        for _ in range(3):
            hub.seed_from_config()
        after = {s.name: s.id for s in hub.list_servers()}
        assert before == after, f"id 漂移了：{before} → {after}"

    def test_does_not_clobber_api_created_server(self, hub: MCPHub):
        """API 手工创建的服务器不应被配置文件覆盖。"""
        from maop.core.mcp.mcp_hub_types import MCPServerConfig, TransportType

        hub.add_server(MCPServerConfig(
            name="alpha", transport=TransportType.SSE, url="http://manual/api",
        ))
        manual_id = next(s.id for s in hub.list_servers() if s.name == "alpha")

        hub.seed_from_config()

        row = next(s for s in hub.list_servers() if s.name == "alpha")
        assert row.id == manual_id, "手工创建的 server 被播种覆盖了"
        # 配置里的 beta 仍应被补上
        assert "beta" in {s.name for s in hub.list_servers()}

    def test_no_config_file_is_not_an_error(self, tmp_path: Path):
        """无配置文件时应静默通过（不是所有部署都有这个文件）。"""
        hub = MCPHub(root_dir=tmp_path)
        result = hub.seed_from_config()
        assert result["added"] == []
        assert hub.list_servers() == []

    def test_malformed_yaml_does_not_raise(self, tmp_path: Path):
        """YAML 损坏时不得抛异常 —— 播种失败不该阻塞启动。"""
        _write_yaml(tmp_path, "servers: [ this is: not valid: yaml")
        hub = MCPHub(root_dir=tmp_path)
        result = hub.seed_from_config()
        assert isinstance(result, dict), "应返回报告而非抛异常"
        assert "errors" in result

    def test_skips_duplicate_names_in_config(self, tmp_path: Path):
        """配置文件内同名重复项不应产生两行。"""
        _write_yaml(tmp_path, """\
servers:
  - name: dup
    transport: stdio
    command: echo
  - name: dup
    transport: sse
    url: http://127.0.0.1:1/sse
""")
        hub = MCPHub(root_dir=tmp_path)
        hub.seed_from_config()
        names = [s.name for s in hub.list_servers()]
        assert names.count("dup") == 1, f"同名重复项产生了多行：{names}"


class TestSyncConfigEndpoint:
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def _inject_admin(request, call_next):
            request.state.auth_roles = ["admin"]
            return await call_next(request)

        from maop.dashboard.routers import mcp as mcp_route
        app.include_router(mcp_route.router)
        return app

    def test_endpoint_syncs(self, monkeypatch, root: Path):
        from maop.dashboard.services import plugin_service

        hub = MCPHub(root_dir=root)
        monkeypatch.setattr(plugin_service, "_get_hub", lambda: hub)

        client = TestClient(self._app())
        resp = client.post("/api/mcp/sync-config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert sorted(body["added"]) == ["alpha", "beta"]

        # 幂等：再调一次不新增
        again = client.post("/api/mcp/sync-config").json()
        assert again["added"] == []
