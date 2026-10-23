"""HTTP contracts for the 10 frontend-used MCP endpoints.

Two purposes:

* pin the ``response_model`` envelopes added in P1-b — every assertion compares
  the response body against the payload the service produced, so a renamed or
  dropped field fails instead of rendering an empty cell;
* pin the four fields ``McpManager.vue`` reads but the API historically never
  sent (``servers[].url``, hourly ``avg_ms``, tool ``input_schema``/id, and the
  credential redaction on the combined endpoint). Those columns showed "—"/0
  forever because the test stubs used shapes the production hub never emits.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.mcp.mcp_call_stats import MCPCallStats
from maop.core.mcp.mcp_hub_types import MCPTool, ServerInfo, ServerStatus, TransportType
from maop.dashboard.routers import data as data_router
from maop.dashboard.routers import mcp as mcp_router
from maop.dashboard.routers import state as router_state
from maop.dashboard.services import data_service, plugin_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/mcp"),
    ("GET", "/api/mcp/servers"),
    ("POST", "/api/mcp/servers"),
    ("GET", "/api/mcp/servers/{server_id}"),
    ("PUT", "/api/mcp/servers/{server_id}"),
    ("GET", "/api/mcp/tools"),
    ("PUT", "/api/mcp/tools/{tool_id}"),
    ("GET", "/api/mcp/stats"),
    ("GET", "/api/mcp/topology"),
    ("GET", "/api/mcp/marketplace/tools"),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(mcp_router, "require_admin", lambda request: None)
    monkeypatch.setattr(data_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(mcp_router.router)
    app.include_router(data_router.router)
    return TestClient(app)


def _server(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "srv1",
        "name": "omniroute",
        "transport": "streamable_http",
        "status": "connected",
        "tools_count": 3,
        "resources_count": 1,
        "error": "",
        "enabled": True,
        "auto_connect": False,
        "url": "http://localhost:20128/mcp",
    }
    base.update(kw)
    return base


# ── servers ───────────────────────────────────────────────────────


def test_server_list_carries_url_and_tool_count(client, monkeypatch):
    row = _server()
    monkeypatch.setattr(
        plugin_service, "list_servers",
        lambda: {"servers": [ServerInfo(**row)], "count": 1},
    )
    body = client.get("/api/mcp/servers").json()
    assert body == {"status": "ok", "servers": [row], "count": 1}


def test_server_detail_shape_is_the_editable_config(client, monkeypatch):
    detail = {
        "id": "srv1", "name": "omniroute", "transport": "streamable_http",
        "url": "http://localhost:20128/mcp", "command": "", "args": ["-x"],
        "timeout_s": 30.0, "enabled": True, "auto_connect": True,
        "auth_type": "bearer", "has_auth": True,
    }
    monkeypatch.setattr(plugin_service, "get_server_detail", lambda sid: detail)
    assert client.get("/api/mcp/servers/srv1").json() == {"status": "ok", "server": detail}


def test_server_detail_missing_is_404_jsonresponse(client, monkeypatch):
    monkeypatch.setattr(plugin_service, "get_server_detail", lambda sid: None)
    assert client.get("/api/mcp/servers/ghost").status_code == 404


def test_add_and_update_server_envelopes(client, monkeypatch):
    monkeypatch.setattr(plugin_service, "add_server", lambda **kw: {"status": "ok", "server": "new"})
    monkeypatch.setattr(
        plugin_service, "update_server",
        lambda *a, **kw: {"status": "ok", "server": "new", "id": "srv9"},
    )
    payload = {
        "name": "new", "transport": "streamable_http", "url": "http://x/mcp",
        "command": "", "args": [], "env": {}, "auth_type": "none", "token": "",
        "timeout_s": 30, "enabled": True, "auto_connect": False,
    }
    assert client.post("/api/mcp/servers", json=payload).json() == {"status": "ok", "server": "new"}
    assert client.put("/api/mcp/servers/srv9", json=payload).json() == {
        "status": "ok", "server": "new", "id": "srv9",
    }


# ── tools ─────────────────────────────────────────────────────────


def test_tools_keep_input_schema_id_and_concurrency_limit(client, monkeypatch):
    """The params panel reads ``input_schema``; the PUT targets ``id``.

    An earlier stub returned ``{"name": "tool1"}`` only and the UI mapped a
    non-existent ``parameters`` key, so the schema block was always empty.
    """
    tool = MCPTool(
        name="chat", description="d", server_name="omniroute",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    monkeypatch.setattr(plugin_service, "list_tools", lambda: {
        "tools": [{**tool.model_dump(), "id": "omniroute:chat", "concurrency_limit": 3}],
        "count": 1,
    })
    body = client.get("/api/mcp/tools").json()
    assert body["count"] == 1
    assert body["tools"][0]["input_schema"]["properties"]["q"]["type"] == "string"
    assert body["tools"][0]["id"] == "omniroute:chat"
    assert body["tools"][0]["concurrency_limit"] == 3


def test_tool_limit_update_echoes_value(client, monkeypatch):
    monkeypatch.setattr(
        plugin_service, "update_tool_concurrency",
        lambda tid, limit: {"status": "ok", "tool": tid, "concurrency_limit": limit},
    )
    body = client.put("/api/mcp/tools/omniroute:chat", json={"concurrency_limit": 5}).json()
    assert body == {"status": "ok", "tool": "omniroute:chat", "concurrency_limit": 5}


# ── stats ─────────────────────────────────────────────────────────


def test_hourly_bucket_reports_average_latency() -> None:
    """``avg_ms`` per hour is what the chart's latency metric plots.

    The accumulator already summed ``total_ms`` per bucket; the series rows
    just never exposed it, so the metric drew a flat zero line.
    """
    stats = MCPCallStats()
    stats.record("omniroute", "chat", 100.0, True)
    stats.record("omniroute", "chat", 300.0, False)

    snapshot = stats.snapshot()
    bucket = snapshot["series"][0]
    assert bucket == {"ts": bucket["ts"], "count": 2, "errors": 1, "avg_ms": 200.0}
    assert snapshot["total"]["avg_ms"] == 200.0


def test_stats_envelope_keeps_series_per_tool_total(client, monkeypatch):
    payload = {
        "series": [{"ts": 1, "count": 2, "errors": 1, "avg_ms": 200.0}],
        "per_tool": [{
            "tool": "chat", "server_name": "omniroute", "calls": 2, "errors": 1,
            "avg_ms": 200.0, "max_ms": 300.0, "error_rate": 0.5,
        }],
        "total": {"calls": 2, "errors": 1, "avg_ms": 200.0},
    }
    monkeypatch.setattr(plugin_service, "mcp_call_stats", lambda hours=24: payload)
    assert client.get("/api/mcp/stats?hours=24").json() == {"status": "ok", **payload}


# ── topology ──────────────────────────────────────────────────────


def test_topology_keeps_nodes_and_edges(client, monkeypatch):
    row = _server(transport=TransportType.STREAMABLE_HTTP, status=ServerStatus.CONNECTED)
    payload = {
        "servers": [row],
        "tools": [{
            "name": "chat", "description": "d", "server_name": "omniroute",
            "input_schema": {"type": "object"}, "id": "omniroute:chat",
        }],
        "agents": [{"name": "coder", "provider": "anthropic", "enabled": True}],
        "edges": [{"id": "omniroute->omniroute:chat", "source": "omniroute",
                   "target": "omniroute:chat", "type": "server-tool"}],
    }
    monkeypatch.setattr(plugin_service, "topology", lambda: payload)
    assert client.get("/api/mcp/topology").json() == {"status": "ok", **payload}


def test_topology_omits_concurrency_limit(client, monkeypatch):
    """The topology builder never sets it — the envelope must not invent 0."""
    monkeypatch.setattr(plugin_service, "topology", lambda: {
        "servers": [], "tools": [{"name": "t", "description": "", "server_name": "s",
                                  "input_schema": {}, "id": "s:t"}],
        "agents": [], "edges": [],
    })
    tool = client.get("/api/mcp/topology").json()["tools"][0]
    assert "concurrency_limit" not in tool


# ── marketplace ───────────────────────────────────────────────────


def test_marketplace_entry_fields(client, monkeypatch):
    entry = {
        "id": "fetch", "name": "fetch", "description": "web fetch", "category": "tools, web",
        "source": "mcp", "version": "1.2.0", "installed": False, "author": "a",
        "homepage": "https://example.invalid", "transport": "stdio",
        "verified": True, "install_count": 42,
    }
    monkeypatch.setattr(plugin_service, "marketplace_tools", lambda: {"tools": [entry], "count": 1})
    assert client.get("/api/mcp/marketplace/tools").json() == {"status": "ok", "tools": [entry], "count": 1}


# ── combined endpoint + credential redaction ─────────────────────


class _FakeBridge:
    def __init__(self, servers: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        self._servers, self._tools = servers, tools

    async def mcp_servers(self) -> list[dict[str, Any]]:
        return self._servers

    async def mcp_tools(self) -> list[dict[str, Any]]:
        return self._tools


@pytest.fixture
def combined(monkeypatch):
    async def _run(servers: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        bridge = _FakeBridge(servers, tools or [])
        monkeypatch.setattr(router_state, "get_bridge", lambda: bridge)
        return await data_service.get_mcp_combined()

    return _run


async def _masking_probe(combined, servers: list[dict[str, Any]]) -> dict[str, Any]:
    return await combined(servers)


def test_combined_redacts_header_and_token_values(combined) -> None:
    servers = [{
        "name": "omniroute", "transport": "streamable_http",
        "url": "http://localhost:20128/mcp",
        "headers": {"Authorization": "Bearer sk-live-token"},
        "token": "sk-live-token",
        "api_key": "abc123",
        "env": {"OMNIROUTE_API_KEY": "sk-live-token", "LOG_LEVEL": "debug"},
        "enabled": True,
    }]
    result = asyncio.run(_masking_probe(combined, servers))
    row = result["servers"][0]
    assert row["headers"] == {"Authorization": "***"}
    assert row["token"] == "***"
    assert row["api_key"] == "***"
    assert row["env"] == {"OMNIROUTE_API_KEY": "***", "LOG_LEVEL": "debug"}
    # Non-secret config stays visible — the card needs name/transport/url.
    assert row["name"] == "omniroute"
    assert row["url"] == "http://localhost:20128/mcp"


def test_combined_endpoint_returns_masked_rows(client, monkeypatch) -> None:
    async def _fake_combined() -> dict[str, Any]:
        return {
            "servers": [{"name": "omniroute", "transport": "streamable_http", "headers": {"Authorization": "***"}}],
            "tools": [{"name": "chat", "server_name": "omniroute"}],
            "server_count": 1,
            "tool_count": 1,
        }

    monkeypatch.setattr(data_service, "get_mcp_combined", _fake_combined)
    body = client.get("/api/mcp").json()
    assert body["server_count"] == 1
    assert body["servers"][0]["headers"] == {"Authorization": "***"}


# ── OpenAPI ───────────────────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []
