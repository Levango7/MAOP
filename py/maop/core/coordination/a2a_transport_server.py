"""MAOP — A2A Transport Server (HTTP).

Exposes the MAOP Agent-to-Agent protocol via HTTP so that external agents
(google-a2a compatible) can discover and communicate with this node using
the standard A2A spec.

Endpoints
--------
- ``GET /.well-known/agent-card.json`` — discovery endpoint per A2A spec
- ``GET /a2a/agent-card`` — same as above (alias)
- ``POST /a2a/tasks`` — create a new A2A task (agent contributes work)
- ``GET /a2a/tasks/{task_id}`` — get task status
- ``POST /a2a/messages`` — send an inter-agent message (JSON-RPC 2.0)
- ``GET /a2a/health`` — liveness probe
- ``GET /a2a/agents`` — list registered agents
- ``POST /a2a/agents/register`` — register an external agent

The server uses Python's stdlib ``http.server`` + asyncio (no FastAPI
needed for protocol compliance), making it deployable with zero extra deps.

Usage::

    from maop.core.coordination.a2a_transport_server import A2ATransportServer
    from maop.core.coordination.a2a_blackboard_bridge import A2ABlackboardBridge

    bridge = A2ABlackboardBridge(blackboard=bb, registry=registry)
    server = A2ATransportServer(
        bridge=bridge,
        registry=registry,
        host="0.0.0.0",
        port=9090,
        agent_card_url="http://this-node:9090",
    )
    await server.start()
    # Server is now reachable at http://this-node:9090/.well-known/agent-card.json

Design reference: docs/design-a2a-transport.md (M2-B implementation slice).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Route handler contract: takes an optional JSON request body, returns
# (status_code, response_payload).
_RouteHandler = Callable[[bytes | None], Awaitable[tuple[int, dict[str, Any]]]]


# ── A2A Agent Card ────────────────────────────────────────────────


A2A_AGENT_CARD_TEMPLATE: dict[str, Any] = {
    "name": "MAOP Node",
    "description": "MAOP Multi-Agent Orchestration Platform Node",
    "url": "",  # filled at runtime
    "version": "1.0.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    },
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [],
}


# ── Task Store ────────────────────────────────────────────────────


class InMemoryTaskStore:
    """In-memory task status tracking.

    For production use, replace with Redis-backed store (see M2-A).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}

    def create(self, task: dict[str, Any]) -> str:
        task_id = task.get("id") or f"task-{uuid.uuid4().hex[:12]}"
        task["id"] = task_id
        task.setdefault("status", "submitted")
        task.setdefault("created_at", _now())
        self._tasks[task_id] = task
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def update(self, task_id: str, updates: dict[str, Any]) -> bool:
        if task_id not in self._tasks:
            return False
        self._tasks[task_id].update(updates)
        self._tasks[task_id]["updated_at"] = _now()
        return True

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._tasks.values())


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── HTTP Handler ──────────────────────────────────────────────────


class _A2ARequestHandler:
    """Minimal async HTTP request handler for the A2A protocol.

    Uses raw asyncio sockets (no framework dependency) for zero-dependency
    deployment. Supports GET/POST JSON routes defined by the coordinator.
    """

    def __init__(
        self,
        bridge: Any,
        registry: Any,
        task_store: InMemoryTaskStore,
        agent_card: dict[str, Any],
    ) -> None:
        self._bridge = bridge
        self._registry = registry
        self._tasks = task_store
        self._agent_card = agent_card
        self._routes: dict[str, dict[str, _RouteHandler]] = {
            "GET": {},
            "POST": {},
        }
        self._register_routes()

    def _register_routes(self) -> None:
        self._routes["GET"]["/.well-known/agent-card.json"] = self._handle_agent_card
        self._routes["GET"]["/a2a/agent-card"] = self._handle_agent_card
        self._routes["GET"]["/a2a/health"] = self._handle_health
        self._routes["GET"]["/a2a/agents"] = self._handle_list_agents
        self._routes["POST"]["/a2a/tasks"] = self._handle_create_task
        self._routes["POST"]["/a2a/messages"] = self._handle_message
        self._routes["POST"]["/a2a/agents/register"] = self._handle_register_agent

    async def handle_request(
        self, method: str, path: str, body: bytes | None,
    ) -> tuple[int, dict[str, Any]]:
        """Route an HTTP request to the appropriate handler."""
        handler = self._routes.get(method, {}).get(path)
        if handler is None:
            return 404, {"error": f"No route for {method} {path}"}
        try:
            return await handler(body)
        except Exception as exc:
            logger.error("[a2a-server] handler error for %s %s: %s", method, path, exc)
            return 500, {"error": str(exc)}

    # ── Route handlers ────────────────────────────────────────────

    async def _handle_agent_card(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        return 200, self._agent_card

    async def _handle_health(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        return 200, {"status": "ok", "timestamp": _now()}

    async def _handle_list_agents(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        try:
            raw_list = await self._registry.list_agents()
            for a in raw_list:
                agents.append({
                    "card_id": getattr(a, "card_id", str(id(a))),
                    "name": getattr(a, "name", "unknown"),
                    "url": getattr(a, "url", ""),
                    "description": getattr(a, "description", ""),
                    "capabilities": getattr(a, "capabilities", []),
                    "status": getattr(a, "status", "active"),
                })
        except Exception as exc:
            logger.debug("[a2a-server] list_agents error: %s", exc)
        return 200, {"agents": agents, "count": len(agents)}

    async def _handle_create_task(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        task_req = _parse_json(body) if body else {}
        task = {
            "description": task_req.get("description", ""),
            "context": task_req.get("context", {}),
            "input": task_req.get("input", ""),
            "requester": task_req.get("requester", "anonymous"),
        }
        task_id = self._tasks.create(task)
        return 201, {"id": task_id, "status": "submitted", "created_at": task.get("created_at")}

    async def _handle_message(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        msg = _parse_json(body) if body else {}
        from maop.core.coordination.a2a_blackboard_bridge import A2AMessage

        a2a_msg = A2AMessage(
            method=msg.get("method", ""),
            params=msg.get("params", {}),
            id=msg.get("id", str(uuid.uuid4())),
            source_agent=msg.get("source_agent", ""),
            target_agent=msg.get("target_agent", ""),
            task_id=msg.get("task_id"),
        )
        # Route through the bridge
        result = await self._bridge.on_a2a_message(a2a_msg)
        resp: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": a2a_msg.id,
            "result": {"status": "accepted", "bridge_result": str(result) if result else None},
        }
        return 200, resp

    async def _handle_register_agent(self, body: bytes | None) -> tuple[int, dict[str, Any]]:
        data = _parse_json(body) if body else {}
        from maop.core.coordination.a2a_blackboard_bridge import AgentCard

        card = AgentCard(
            card_id=data.get("card_id", f"agent-{uuid.uuid4().hex[:8]}"),
            name=data.get("name", "unnamed"),
            url=data.get("url", ""),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            status="active",
        )
        try:
            # Sync/async register — try async first
            register_fn = getattr(self._registry, "register_agent", None)
            if register_fn is None:
                register_fn = getattr(self._registry, "register", None)
            if register_fn is None:
                return 500, {"error": "registry does not support register_agent/register"}
            result = register_fn(card)
            if asyncio.iscoroutine(result):
                await result
            return 201, {"card_id": card.card_id, "status": "registered"}
        except Exception as exc:
            return 400, {"error": str(exc)}


def _parse_json(data: bytes | str) -> dict[str, Any]:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    try:
        result = json.loads(text) if text else {}
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Raw Async HTTP Server ─────────────────────────────────────────


class _HTTPServerProto:
    """Minimal asyncio-based HTTP server.

    Accepts raw TCP connections, parses the HTTP request, routes it through
    the handler, and writes back the response.
    """

    def __init__(self, handler: _A2ARequestHandler, host: str, port: int) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, self._host, self._port,
        )
        addr = self._server.sockets[0].getsockname() if self._server.sockets else ("?", "?")
        logger.info("[a2a-server] listening on %s:%d", addr[0], addr[1])

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            # Read HTTP request line + headers (simple: read until \r\n\r\n)
            request_line = await reader.readline()
            if not request_line:
                return
            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split(" ")
            if len(parts) < 2:
                await self._write_response(writer, 400, {"error": "malformed request"})
                return
            method, raw_path = parts[0], parts[1]

            # Read headers to find Content-Length
            content_length = 0
            while True:
                line = await reader.readline()
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    break
                lower = decoded.lower()
                if lower.startswith("content-length:"):
                    content_length = int(decoded.split(":", 1)[1].strip())

            body = await reader.read(content_length) if content_length else None

            path = urlparse(raw_path).path
            status, resp = await self._handler.handle_request(method, path, body)
            await self._write_response(writer, status, resp)
        except asyncio.CancelledError:  # silent-ok: interrupt — 任务取消即退出连接处理
            pass
        except Exception as exc:
            logger.debug("[a2a-server] connection error: %s", exc)
            try:
                await self._write_response(writer, 500, {"error": str(exc)})
            except Exception:  # silent-ok: best-effort-cleanup — 对端已断开，原错误已记录
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # silent-ok: best-effort-cleanup — 关闭传输层为 best-effort
                pass

    async def _write_response(
        self, writer: asyncio.StreamWriter, status: int, data: dict[str, Any],
    ) -> None:
        body = json.dumps(data).encode("utf-8")
        status_text = {
            200: "OK", 201: "Created", 400: "Bad Request",
            404: "Not Found", 500: "Internal Server Error",
        }.get(status, "OK")
        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        writer.write(response)
        await writer.drain()


# ── A2A Transport Server (Public API) ─────────────────────────────


class A2ATransportServer:
    """HTTP server exposing the MAOP A2A protocol.

    Parameters
    ----------
    bridge : A2ABlackboardBridge
        The A2A↔blackboard bridge instance.
    registry : AgentRegistry
        Agent registry for discovery and registration.
    host : str
        Bind address (default "0.0.0.0").
    port : int
        Bind port (default 9090).
    agent_card_url : str
        Public endpoint URL to advertise in the AgentCard.
    agent_card_overrides : dict | None
        Additional fields to merge into the AgentCard.
    """

    def __init__(
        self,
        bridge: Any = None,
        registry: Any = None,
        # A2A peers address this node over the network, so the default binds all
        # interfaces; deployments narrow it via compose/network policy (the same
        # default as core/agent/delegation/a2a.py).
        host: str = "0.0.0.0",  # nosec B104 — 节点间通信需对外监听，部署侧用网络策略收敛
        port: int = 9090,
        agent_card_url: str = "",
        agent_card_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._bridge = bridge
        self._registry = registry
        self._host = host
        self._port = port

        # Build AgentCard
        card = dict(A2A_AGENT_CARD_TEMPLATE)
        card["url"] = agent_card_url or f"http://localhost:{port}"
        if agent_card_overrides:
            card.update(agent_card_overrides)

        self._task_store = InMemoryTaskStore()
        self._handler = _A2ARequestHandler(
            bridge=bridge,
            registry=registry,
            task_store=self._task_store,
            agent_card=card,
        )
        self._http_server = _HTTPServerProto(self._handler, host, port)

    async def start(self) -> None:
        """Start the HTTP server."""
        await self._http_server.start()
        logger.info(
            "[a2a-transport] A2A server started on %s:%d (agent-card at %s/.well-known/agent-card.json)",
            self._host, self._port, self._handler._agent_card.get("url", ""),
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        await self._http_server.stop()
        logger.info("[a2a-transport] A2A server stopped")

    @property
    def task_store(self) -> InMemoryTaskStore:
        return self._task_store

    @property
    def agent_card(self) -> dict[str, Any]:
        return dict(self._handler._agent_card)
