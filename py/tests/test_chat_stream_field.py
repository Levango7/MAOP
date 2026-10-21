"""``ChatRequestBody.stream`` 必须是真开关，不是装饰。

字段早就声明在请求体里，但两个处理器都把 stream 写死（``/api/chat`` 恒 False、
``/api/chat/stream`` 恒 True），所以在 JSON 端点上要求流式的客户端会静默拿到
一个整包响应 —— 与不发送该字段完全一样，没有任何信号告诉它"你的选择被忽略了"。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def spy(monkeypatch) -> dict[str, Any]:
    """把 service 层三个入口换成可观察的替身。"""
    from maop.dashboard.routers import chat as chat_mod

    calls: dict[str, Any] = {"build": [], "send": 0, "chunks": 0, "engine": object()}

    async def _send(engine: Any, req: Any) -> dict[str, Any]:
        calls["send"] += 1
        return {"content": "whole reply", "session_id": req["session_id"]}

    async def _chunks(engine: Any, req: Any):
        calls["chunks"] += 1
        for piece in ("par", "tial"):
            yield f"data: {piece}\n\n"

    def _build(**kwargs: Any) -> dict[str, Any]:
        calls["build"].append(kwargs)
        return kwargs

    monkeypatch.setattr(chat_mod.chat_service, "send_chat", _send)
    monkeypatch.setattr(chat_mod.chat_service, "stream_chat_chunks", _chunks)
    monkeypatch.setattr(chat_mod.chat_service, "build_chat_request", _build)
    monkeypatch.setattr(chat_mod, "_get_engine", lambda: calls["engine"])
    # require_admin 读 request.state.role；这里只测 stream 语义，鉴权另有用例。
    monkeypatch.setattr(chat_mod, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(chat_mod.router)
    calls["client"] = TestClient(app)
    return calls


_BODY = {"message": "hello", "agent": "claude", "session_id": "s1"}


def test_json_by_default(spy: dict[str, Any]) -> None:
    res = spy["client"].post("/api/chat", json=_BODY)
    assert res.status_code == 200
    assert res.json()["content"] == "whole reply"
    assert spy["build"][0]["stream"] is False
    assert spy["send"] == 1 and spy["chunks"] == 0


def test_stream_true_on_the_json_endpoint_actually_streams(spy: dict[str, Any]) -> None:
    """这是修复的本体：字段以前被丢掉，现在决定响应形状。"""
    res = spy["client"].post("/api/chat", json={**_BODY, "stream": True})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "data: par" in res.text and "data: tial" in res.text
    assert spy["build"][0]["stream"] is True
    assert spy["chunks"] == 1
    assert spy["send"] == 0, "要求流式时不应再走整包 send_chat"


def test_stream_endpoint_streams_even_when_body_says_otherwise(spy: dict[str, Any]) -> None:
    """路径本身就是选择：/api/chat/stream 不受 body.stream 左右。"""
    res = spy["client"].post("/api/chat/stream", json={**_BODY, "stream": False})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert spy["build"][0]["stream"] is True


def test_both_stream_paths_share_one_header_set(spy: dict[str, Any]) -> None:
    """两处 SSE 响应必须是同一份头：以前是两份手写，正是漂移的来源。"""
    a = spy["client"].post("/api/chat", json={**_BODY, "stream": True})
    b = spy["client"].post("/api/chat/stream", json=_BODY)
    for header in ("cache-control", "connection", "x-accel-buffering", "content-type"):
        assert a.headers[header] == b.headers[header], f"{header} 在两个端点上不一致"


def test_build_request_receives_every_body_field(spy: dict[str, Any]) -> None:
    """除 stream 外的字段也要照旧透传，别在改 stream 时把邻居弄丢。"""
    spy["client"].post("/api/chat", json={
        **_BODY, "model": "qwen-max", "system_prompt": "be brief",
        "max_tokens": 128, "temperature": 0.2, "images": ["img1"],
    })
    built = spy["build"][0]
    assert built["model"] == "qwen-max"
    assert built["system_prompt"] == "be brief"
    assert built["max_tokens"] == 128
    assert built["temperature"] == 0.2
    assert built["images"] == ["img1"]
    assert built["agent"] == "claude"
    assert built["session_id"] == "s1"
