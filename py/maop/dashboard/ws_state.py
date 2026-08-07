"""WebSocket shared state for the dashboard server.

Module-level mutable state (client set, lock, snapshot cache, push task)
lives here so that ``ws_broadcast``, ``lifespan``, and ``server`` can all
read/write it without circular imports. Mutators must go through
``ws_state.<name> = ...`` (not ``global``) because each importer holds its
own binding — see ws_broadcast._ws_push_loop for the pattern.
"""

from __future__ import annotations

import asyncio


from fastapi import WebSocket

# Connected WebSocket clients (dashboard real-time push).
_ws_clients: set[WebSocket] = set()

# Serializes access to _ws_clients.
_ws_lock: asyncio.Lock = asyncio.Lock()

# Cached snapshot to avoid redundant DB queries on every push.
_ws_snapshot_cache: dict | None = None
_ws_snapshot_ts: float = 0.0
_WS_SNAPSHOT_TTL: float = 5.0  # seconds

# Background push task (created in lifespan, cancelled on shutdown).
_ws_push_task: asyncio.Task | None = None

# Per-client send timeout — one slow client must not block broadcasts.
_WS_SEND_TIMEOUT: float = 5.0



__all__ = [
    "_ws_clients",
    "_ws_lock",
    "_ws_snapshot_cache",
    "_ws_snapshot_ts",
    "_WS_SNAPSHOT_TTL",
    "_ws_push_task",
    "_WS_SEND_TIMEOUT",
]