"""Regression tests for the notifications WebSocket auth gate.

P0 fix (2026-08-29): ``/api/notifications/ws`` previously accepted
unauthenticated connections when no token was supplied — the ``if token:``
guard skipped validation entirely, bypassing MAOP_AUTH=1 (ws_broadcast.py
already closed with 4401 in that case). These tests pin the corrected
behaviour with a minimal stub WebSocket (no enterprise dependency —
``test_notifications.py`` is module-skipped because ``maop.enterprise``
is unpublished, so they live in their own file).

Covered cases:
  - auth enabled + no token            -> close(4401, "Authentication required"), never accept
  - auth enabled + invalid token       -> close(4401, "Invalid token"), never accept
  - auth enabled + subprotocol token   -> token picked up from Sec-WebSocket-Protocol
  - auth disabled + no token           -> accept (backward compatible)
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocketDisconnect

from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard.routers import notifications as _notif_mod


class _StubWebSocket:
    """Minimal WebSocket double covering only what ``notifications_ws`` touches."""

    def __init__(self, token: str = "", protocols: str = ""):
        self.query_params: dict[str, str] = {"token": token} if token else {}
        self.headers: dict[str, str] = {"sec-websocket-protocol": protocols} if protocols else {}
        self.accepted = False
        self.closed_with: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)

    async def send_json(self, payload: Any) -> None:  # pragma: no cover - simple sink
        self.sent = payload

    async def receive_text(self) -> str:
        # End the accept-then-loop path immediately after a successful accept.
        raise WebSocketDisconnect()


def test_ws_no_token_rejected_when_auth_enabled(monkeypatch):
    """P0 regression: missing token must close with 4401, not accept."""
    monkeypatch.setattr(_auth_mod, "_auth_enabled", True)
    ws = _StubWebSocket()
    asyncio.run(_notif_mod.notifications_ws(ws))
    assert ws.closed_with is not None
    assert ws.closed_with[0] == 4401
    assert ws.closed_with[1] == "Authentication required"
    assert not ws.accepted


def test_ws_invalid_token_rejected_when_auth_enabled(monkeypatch):
    """A supplied-but-invalid token must still close with 4401."""

    class _FakePayload:
        authenticated = False

    class _FakeJwt:
        @staticmethod
        def validate_token(token: str):
            return _FakePayload()

    class _FakeMgr:
        jwt_handler = _FakeJwt()

    monkeypatch.setattr(_auth_mod, "_auth_enabled", True)
    monkeypatch.setattr(_auth_mod, "get_auth_mgr", lambda: _FakeMgr())
    ws = _StubWebSocket(token="forged-token")
    asyncio.run(_notif_mod.notifications_ws(ws))
    assert ws.closed_with is not None
    assert ws.closed_with[0] == 4401
    assert not ws.accepted


def test_ws_token_via_subprotocol_header(monkeypatch):
    """Token delivered via Sec-WebSocket-Protocol must reach validation."""

    captured: dict[str, str] = {}

    class _FakeJwt:
        @staticmethod
        def validate_token(token: str):
            captured["token"] = token
            raise ValueError("boom -> forces 'Authentication failed' close")

    class _FakeMgr:
        jwt_handler = _FakeJwt()

    monkeypatch.setattr(_auth_mod, "_auth_enabled", True)
    monkeypatch.setattr(_auth_mod, "get_auth_mgr", lambda: _FakeMgr())
    ws = _StubWebSocket(protocols="dummy, real-token")
    asyncio.run(_notif_mod.notifications_ws(ws))
    assert captured.get("token") == "real-token"
    assert not ws.accepted
    assert ws.closed_with is not None and ws.closed_with[0] == 4401


def test_ws_no_token_accepted_when_auth_disabled(monkeypatch):
    """Backward compatibility: MAOP_AUTH=0 keeps anonymous connections allowed."""
    monkeypatch.setattr(_auth_mod, "_auth_enabled", False)
    ws = _StubWebSocket()
    asyncio.run(_notif_mod.notifications_ws(ws))
    assert ws.accepted
    assert ws.closed_with is None
