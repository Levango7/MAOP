# _register_routes._websocket: WebSocket endpoint.
# Split from _register_routes.py into _register_routes/_websocket.py.

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from maop.dashboard._ws_manager import _ws_clients, _ws_lock
from maop.dashboard.routers import auth as _auth_mod

logger: logging.Logger = logging.getLogger(__name__)

__all__: list[str] = []


def register_websocket(app: FastAPI) -> None:
    """Register the /ws WebSocket endpoint on ``app``."""
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> Any:
        # Auth must be validated BEFORE accept() — BaseHTTPMiddleware cannot
        # intercept WebSocket scope, so we enforce it here directly.
        if _auth_mod._auth_enabled:
            # #5 fix: token via Sec-WebSocket-Protocol subprotocol (not URL query)
            # Browser: new WebSocket(url, [token]). Avoids URL/access-log exposure.
            # Fallback: query param kept for non-browser clients (curl, CLI).
            token = ws.query_params.get("token", "")
            if not token:
                # Try Sec-WebSocket-Protocol header (browser subprotocol)
                protocols = ws.headers.get("sec-websocket-protocol", "")
                if protocols:
                    # Format: "token, <actual_token>" or just "<actual_token>"
                    parts = [p.strip() for p in protocols.split(",") if p.strip()]
                    if parts:
                        token = parts[-1]  # last protocol identifier
            if not token:
                # M7 fix: same-origin browser WS handshakes carry the httpOnly
                # maop_token cookie automatically. The frontend no longer stores
                # the JWT in localStorage (XSS-readable), so the subprotocol
                # path has no client-side token source — the cookie is the
                # browser's primary credential. Explicit query/subprotocol
                # tokens still take precedence for non-browser clients.
                token = ws.cookies.get("maop_token", "")
            if not token:
                await ws.close(code=4401, reason="Authentication required")
                return
            try:
                mgr = _auth_mod.get_auth_mgr()
                payload = mgr.jwt_handler.validate_token(token)
                # validate_token always returns an AuthResult (never None).
                # Must check .authenticated flag — otherwise forged/expired
                # tokens bypass WebSocket auth entirely (P1-1 fix).
                if not payload or not getattr(payload, "authenticated", False):
                    await ws.close(code=4401, reason="Invalid token")
                    return
            except Exception:
                await ws.close(code=4401, reason="Authentication failed")
                return
        await ws.accept()
        async with _ws_lock:
            _ws_clients.add(ws)
        try:
            await ws.send_json({"type": "hello", "msg": "MAOP Dashboard WebSocket connected", "ts": time.time()})
            while True:
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
        except WebSocketDisconnect:  # silent-ok: ws-disconnect — 客户端断开
            pass
        finally:
            async with _ws_lock:
                _ws_clients.discard(ws)
