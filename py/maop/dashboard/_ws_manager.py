"""WebSocket real-time push manager for the MAOP dashboard server.

Extracted from ``server.py`` to keep the main module focused on app
construction.  Holds the connected-client pool, the broadcast helper,
and the background snapshot push loop.

Re-exported by ``server.py`` for backward compatibility (tests and
``routers/audit.py`` import ``_ws_broadcast`` / ``_ws_clients`` from
``server``).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket

from maop.dashboard.routers import state as _state

logger = logging.getLogger(__name__)

# ── WebSocket real-time push ───────────────────────────────────────
_ws_clients: set[WebSocket] = set()
_ws_lock = asyncio.Lock()
_ws_snapshot_cache: dict | None = None
_ws_snapshot_ts: float = 0.0
_WS_SNAPSHOT_TTL = 5.0  # seconds — cache snapshot to avoid redundant DB queries
_ws_push_task: asyncio.Task | None = None

_WS_SEND_TIMEOUT = 5.0  # OPS-3 fix: cap per-client send time


async def _ws_broadcast(msg: dict) -> Any:
    # OPS-3 fix: snapshot clients under the lock, but send OUTSIDE the lock
    # (concurrently, with a per-client timeout) so one slow client can no
    # longer block all broadcasts and new connections.
    async with _ws_lock:
        clients = list(_ws_clients)
    if not clients:
        return

    async def _send(ws: WebSocket) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=_WS_SEND_TIMEOUT)
            return None
        except Exception:
            return ws

    results = await asyncio.gather(*(_send(ws) for ws in clients))
    dead = {ws for ws in results if ws is not None}
    if dead:
        async with _ws_lock:
            _ws_clients.difference_update(dead)


async def _ws_push_loop() -> Any:
    global _ws_snapshot_cache, _ws_snapshot_ts
    while True:
        await asyncio.sleep(15)
        if not _ws_clients:
            continue
        try:
            now = time.time()
            # Reuse cached snapshot if within TTL
            if _ws_snapshot_cache and (now - _ws_snapshot_ts) < _WS_SNAPSHOT_TTL:
                snapshot = _ws_snapshot_cache
            else:
                bridge = _state.get_bridge()
                live = await bridge.live()
                report = await bridge.report(hours=48)
                ts = await bridge.timeseries(hours=168)
                snapshot = {"type": "snapshot", "ts": now, "live": live, "report": report, "timeseries": ts}
                _ws_snapshot_cache = snapshot
                _ws_snapshot_ts = now
            await _ws_broadcast(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("WS push error: %s", e)