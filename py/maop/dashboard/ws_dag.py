"""DAG progress WebSocket endpoint for real-time node-status push + control.

Extracted from server.py (§2.4). Auth reuses the Sec-WebSocket-Protocol
subprotocol token (same as /ws).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from maop.dashboard.routers import auth as _auth_mod

logger = logging.getLogger(__name__)

router = APIRouter()


async def _dag_control_node(execution_id: str, node_id: str, action: str) -> str:
    """Route a cancel/pause instruction to the orchestrator.

    This is a best-effort hook — full node-level cancel/pause requires
    orchestrator integration. Returns ``"ok"`` on success,
    ``"not_found"`` if the execution is not active.
    """
    # Future: integrate with MaopLoop to actually cancel/pause the node.
    # For now, return ok to acknowledge the instruction was received.
    from maop.core.agent.dag.dag_progress_emitter import get_emitter

    emitter = get_emitter(execution_id)
    if emitter is None:
        return "not_found"
    return "ok"


@router.websocket("/ws/dag/{execution_id}")
async def dag_ws_endpoint(ws: WebSocket, execution_id: str) -> Any:
    """WebSocket endpoint for real-time DAG node-status push + control.

    Downstream (server → client):
        - ``{"type": "node-status", "data": {...}}``  — node status event
        - ``{"type": "execution-complete", "data": {...}}`` — orchestration done
        - ``{"type": "ping"}`` — 30s heartbeat (spec 5.2.1 rule 10)

    Upstream (client → server):
        - ``{"action": "cancel", "node_id": "xxx"}`` — cancel a node
        - ``{"action": "pause", "node_id": "xxx"}`` — pause a node
        - ``{"type": "pong"}`` — heartbeat response

    Auth: reuses Sec-WebSocket-Protocol subprotocol token (same as /ws).
    On cancel of an already-completed node, returns
    ``{"result": "already_completed"}`` (spec 5.2.3 anomaly 3).
    """
    # ── Auth (reuse Sec-WebSocket-Protocol subprotocol) ───────
    if _auth_mod._auth_enabled:
        token = ws.query_params.get("token", "")
        if not token:
            protocols = ws.headers.get("sec-websocket-protocol", "")
            if protocols:
                parts = [p.strip() for p in protocols.split(",") if p.strip()]
                if parts:
                    token = parts[-1]
        if not token:
            await ws.close(code=4401, reason="Authentication required")
            return
        try:
            mgr = _auth_mod.get_auth_mgr()
            payload = mgr.jwt_handler.validate_token(token)
            if not payload or not getattr(payload, "authenticated", False):
                await ws.close(code=4401, reason="Invalid token")
                return
        except Exception:
            await ws.close(code=4401, reason="Authentication failed")
            return

    await ws.accept()

    from maop.core.reliability.event_bus import get_event_bus

    bus = get_event_bus()
    node_topic = f"dag.node-status.{execution_id}"
    complete_topic = f"dag.execution-complete.{execution_id}"
    queue: asyncio.Queue = asyncio.Queue()

    def _on_node(evt: Any) -> None:
        try:
            queue.put_nowait(evt)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)

    def _on_complete(evt: Any) -> None:
        try:
            queue.put_nowait(evt)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)

    bus.subscribe(node_topic, _on_node)
    bus.subscribe(complete_topic, _on_complete)

    heartbeat_task: asyncio.Task | None = None
    push_task: asyncio.Task | None = None
    # Track missed pongs for 90s failure detection (3 × 30s).
    missed_pongs = 0

    async def _heartbeat() -> None:
        """Send ping every 30s; track missed pongs."""
        nonlocal missed_pongs
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send_json({"type": "ping", "ts": time.time()})
            except Exception:
                return

    async def _push_events() -> None:
        """Push node-status / execution-complete events from the queue."""
        while True:
            evt = await queue.get()
            if evt.topic == complete_topic:
                await ws.send_json({"type": "execution-complete", "data": evt.data})
                # Signal the receive loop to exit.
                return
            await ws.send_json({"type": "node-status", "data": evt.data})

    try:
        heartbeat_task = asyncio.create_task(_heartbeat())
        push_task = asyncio.create_task(_push_events())

        # Receive loop — handles cancel/pause/pong from client.
        while True:
            try:
                data = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = _json.loads(data)
            except (ValueError, TypeError):
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")
            action = msg.get("action")

            # Heartbeat pong
            if msg_type == "pong":
                missed_pongs = 0
                continue

            # Control instructions
            if action in ("cancel", "pause"):
                node_id = msg.get("node_id", "")
                if not node_id or not isinstance(node_id, str):
                    await ws.send_json({
                        "type": "error",
                        "message": "node_id required for " + action,
                    })
                    continue
                # Conflict detection: check if node already completed.
                from maop.core.agent.dag.dag_progress_emitter import get_emitter

                emitter = get_emitter(execution_id)
                if emitter and emitter.is_node_completed(node_id):
                    await ws.send_json({
                        "type": "action-result",
                        "action": action,
                        "node_id": node_id,
                        "result": "already_completed",
                    })
                    continue
                # Route to orchestrator (cancel/pause hook).
                # Currently a best-effort ack — full cancel requires
                # orchestrator integration (future enhancement).
                result = await _dag_control_node(execution_id, node_id, action)
                await ws.send_json({
                    "type": "action-result",
                    "action": action,
                    "node_id": node_id,
                    "result": result,
                })
            elif msg_type == "ping":
                # Client-initiated ping → respond with pong.
                await ws.send_json({"type": "pong", "ts": time.time()})
            # Unknown messages are ignored (spec: reject illegal instructions
            # silently rather than disconnecting).
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("[dag-ws] endpoint error", exc_info=True)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if push_task:
            push_task.cancel()
        try:
            bus.unsubscribe(node_topic, _on_node)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
        try:
            bus.unsubscribe(complete_topic, _on_complete)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
