"""回归测试：SPA 兜底路由必须放行 /api/*，不得把未知 API 路径变成 HTML 200。

## 背景（2026-09-23）

`_register_routes.register_static_routes` 里的兜底路由是
``@app.get("/{full_path:path}")``，注释声称 "Any non-API, non-asset path
returns index.html ... so /api/* ... are not affected"，但**实现里没有 /api/
判断**。"已注册路由先匹配"只对**存在**的路径成立；未注册的 /api/* 会落到
兜底路由，返回 200 + index.html。

后果：前端 ``res.json()`` 抛
``Unexpected token '<', "<!DOCTYPE "... is not valid JSON``，
把"端点不存在"伪装成一个令人费解的解析错误，并掩盖真实的契约缺口。

本项目已因此踩坑三次（kg_router、compliance.router 定义了却没挂载；本次
发现 alerts/audit/mcp 三组端点前端调用但后端未实现），每次都靠人肉发现。
本测试把该行为固化，防止回归。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from maop.dashboard.server import app

    return TestClient(app, raise_server_exceptions=False)


def test_unknown_api_path_returns_404_json(client):
    """未注册的 /api/* 必须返回 404 JSON，而不是 200 + HTML。"""
    resp = client.get("/api/__definitely_not_a_route__")
    assert resp.status_code == 404, (
        f"未知 API 路径应返回 404，实际 {resp.status_code}"
        "（返回 200 + HTML 会让前端报 JSON 解析错误，掩盖契约缺口）"
    )
    assert resp.headers.get("content-type", "").startswith("application/json"), (
        f"应返回 JSON，实际 content-type={resp.headers.get('content-type')}"
    )
    body = resp.json()
    assert body.get("code") == "HTTP_404"
    assert body.get("status") == "error"


def test_unknown_nested_api_path_returns_404_json(client):
    """深层未注册路径同样适用（如 /api/alerts/rules）。"""
    resp = client.get("/api/alerts/__nonexistent__")
    assert resp.status_code == 404
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_bare_api_prefix_returns_404_json(client):
    """/api 本身也应返回 404 JSON。"""
    resp = client.get("/api")
    assert resp.status_code == 404
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_spa_client_route_still_serves_html(client):
    """SPA 客户端路由（非 /api/）仍必须返回 HTML，不能被误伤。"""
    resp = client.get("/monitor")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_known_api_route_unaffected(client):
    """已注册的 API 路由不受影响。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
