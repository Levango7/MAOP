"""WebSocket broadcast infrastructure for the dashboard server.

Owns the /ws endpoint and the background snapshot push loop. Shared
state (client set, lock, snapshot cache) lives in ``ws_state`` and is
mutated via ``ws_state.<name> = ...`` so all importers see updates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard.routers import state as _state

from . import ws_state

logger = logging.getLogger(__name__)

router = APIRouter()


async def _ws_broadcast(msg: dict) -> Any:
    # OPS-3 fix: snapshot clients under the lock, but send OUTSIDE the lock
    # (concurrently, with a per-client timeout) so one slow client can no
    # longer block all broadcasts and new connections.
    async with ws_state._ws_lock:
        clients = list(ws_state._ws_clients)
    if not clients:
        return

    async def _send(ws: WebSocket) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=ws_state._WS_SEND_TIMEOUT)
            return None
        except Exception:
            return ws

    results = await asyncio.gather(*(_send(ws) for ws in clients))
    dead = {ws for ws in results if ws is not None}
    if dead:
        async with ws_state._ws_lock:
            ws_state._ws_clients.difference_update(dead)


async def _ws_push_loop() -> Any:
    while True:
        await asyncio.sleep(15)
        if not ws_state._ws_clients:
            continue
        try:
            now = time.time()
            # Reuse cached snapshot if within TTL
            if ws_state._ws_snapshot_cache and (now - ws_state._ws_snapshot_ts) < ws_state._WS_SNAPSHOT_TTL:
                snapshot = ws_state._ws_snapshot_cache
            else:
                bridge = _state.get_bridge()
                live = await bridge.live()
                report = await bridge.report(hours=48)
                ts = await bridge.timeseries(hours=168)
                snapshot = {"type": "snapshot", "ts": now, "live": live, "report": report, "timeseries": ts}
                ws_state._ws_snapshot_cache = snapshot
                ws_state._ws_snapshot_ts = now
            await _ws_broadcast(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("WS push error: %s", e)


@router.websocket("/ws")
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
    async with ws_state._ws_lock:
        ws_state._ws_clients.add(ws)
    try:
        await ws.send_json({"type": "hello", "msg": "MAOP Dashboard WebSocket connected", "ts": time.time()})
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        async with ws_state._ws_lock:
            ws_state._ws_clients.discard(ws)