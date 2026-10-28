"""多 worker 下 WS 快照推送被关闭必须是显式的（阶段三 d）。

每个 uvicorn worker 各跑一份 lifespan；多 worker 模式下 ``MAOP_BACKGROUND_TASKS``
被置 0，推送循环不启动（否则 N 个 worker 推 N 份重复快照）。旧行为只有一条
启动日志，客户端无感：连上 /ws、收到 hello、然后永远等不到 snapshot。

现在 hello 帧带 ``push_enabled``，客户端可据此改走轮询。本文件锁定这个契约。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from maop.dashboard import _ws_manager


@pytest.fixture
def app():
    """真实 FastAPI app（conftest 已设 MAOP_ENV=test、MAOP_AUTH=0）。"""
    from maop.dashboard.server import app as fastapi_app

    return fastapi_app


def _hello(client: TestClient) -> dict:
    with client.websocket_connect("/ws") as ws:
        return ws.receive_json()


def test_hello_reports_push_enabled_by_default(app):
    with TestClient(app) as client:
        hello = _hello(client)
    assert hello["type"] == "hello"
    assert hello["push_enabled"] is True


def test_hello_reports_push_disabled_when_background_tasks_off(app, monkeypatch):
    # monkeypatch.setattr 只用于把模块级标志在用例结束后恢复成 True，
    # 避免污染同会话里其他依赖推送的用例。
    monkeypatch.setattr(_ws_manager, "_ws_push_enabled", True)
    monkeypatch.setenv("MAOP_BACKGROUND_TASKS", "0")
    with TestClient(app) as client:
        hello = _hello(client)
    assert hello["push_enabled"] is False, (
        "多 worker 关闭推送时 hello 必须显式告知客户端，不能让它静默等待 snapshot"
    )
    assert _ws_manager._ws_push_task is None
