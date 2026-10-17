"""MAOP — MCP Hub (Model Context Protocol Hub).

A centralized registry and routing layer for MCP tools and backends.
Supports multiple transport protocols:

- **Stdio**: Local subprocess with JSON-RPC stdio transport.
- **SSE**: HTTP Server-Sent Events streaming transport.
- **Websocket**: Bidirectional WebSocket transport.
- **StreamableHTTP**: HTTP POST + optional SSE stream.

The hub maintains a distributed registry backed by Redis so that all
MAOP nodes share the same tool topology.

Usage::

    hub = MCPHub(redis_url="redis://localhost:6379/11")
    await hub.start()

    # Register a local tool server
    srv = StdioMCPServer(name="my-tools", command="/path/to/tool-server")
    srv.register_tool("calc_sum", fn=lambda a, b: a + b)
    await hub.register_server(srv)

    # Discover tools
    results = hub.find_tools("calc")
    print([t["name"] for t in results])

    await hub.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── MCP Transport Protocols ─────────────────────────────────────────


class MCPTransport(Protocol):
    """MCP transport interface.

    Transports must implement ``send_request(method, params)`` which
    returns a ``dict`` with ``"result"`` or ``"error"``.
    """

    name: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


# ── Stdio Transport ────────────────────────────────────────────────


class StdioTransport:
    """Stdio-based MCP transport (subprocess)."""

    def __init__(self, command: str, args: list[str] | None = None, name: str = "stdio") -> None:
        self.name = name
        self._command = command
        self._args = args or []
        self._proc: subprocess.Popen | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        # Popen 为毫秒级本地进程创建；stdio MCP server 为长生命周期请求/响应
        # 进程，阻塞窗口可接受（全异步化属独立重构，见 runbook）
        self._proc = subprocess.Popen(  # noqa: ASYNC220
            [self._command] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.debug("[mcp-stdio] connected to %s", self._command)

    async def disconnect(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._proc.wait()
            self._proc = None

    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("Stdio transport not connected")
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": str(uuid.uuid4())})
        line = payload + "\n"
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()
        resp_line = self._proc.stdout.readline()
        data: dict[str, Any] = json.loads(resp_line.decode())
        return data


# ── SSE Transport ──────────────────────────────────────────────────


class SSETransport:
    """HTTP Server-Sent Events MCP transport."""

    def __init__(self, url: str, name: str = "sse") -> None:
        self.name = name
        self._url = url
        self._client: Any | None = None

    async def connect(self) -> None:
        try:
            import httpx
            self._client = httpx.AsyncClient()
        except ImportError as e:
            raise RuntimeError(f"[mcp-sse] httpx is required for SSETransport: {e.__class__.__name__}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("SSE transport not connected")
        resp = await self._client.post(
            self._url,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": str(uuid.uuid4())},
            headers={"Accept": "application/json"},
        )
        data: dict[str, Any] = resp.json()
        return data


# ── WebSocket Transport ────────────────────────────────────────────


class WebSocketTransport:
    """Bidirectional WebSocket MCP transport."""

    def __init__(self, ws_url: str, name: str = "ws") -> None:
        self.name = name
        self._ws_url = ws_url
        self._ws: Any | None = None

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as e:
            raise RuntimeError(f"[mcp-ws] websockets is required: {e.__class__.__name__}") from e
        self._ws = await websockets.connect(self._ws_url)

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("WebSocket transport not connected")
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": str(uuid.uuid4())})
        await self._ws.send(payload)
        resp = await self._ws.recv()
        data: dict[str, Any] = json.loads(resp)
        return data


# ── Streamable HTTP Transport ──────────────────────────────────────


class StreamableHTTPTransport:
    """Streamable HTTP (POST + optional SSE) MCP transport."""

    def __init__(self, url: str, name: str = "streamable-http") -> None:
        self.name = name
        self._url = url
        self._client: Any | None = None

    async def connect(self) -> None:
        try:
            import httpx
            self._client = httpx.AsyncClient()
        except ImportError as e:
            raise RuntimeError(f"[mcp-http] httpx required: {e.__class__.__name__}") from e

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("StreamableHTTP transport not connected")
        resp = await self._client.post(
            self._url,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": str(uuid.uuid4())},
        )
        data: dict[str, Any] = resp.json()
        return data


# ── MCP Server (tool container) ────────────────────────────────────


@dataclass
class MCPServer:
    """Represents a single MCP tool server.

    Parameters
    ----------
    name : str
        Unique server name.
    transport : MCPTransport
        Transport used to communicate with the server.
    description : str
        Human-readable description.
    tools : list[dict[str, Any]]
        Declared tool schemas (OpenAPI-style).
    metadata : dict[str, Any]
        Extra metadata.
    """

    name: str
    transport: MCPTransport
    description: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    async def initialize(self) -> dict[str, Any]:
        """Perform MCP handshake and return the server's declared capabilities."""
        await self.transport.connect()
        return await self.transport.send_request("initialize", {"protocolVersion": "2024-11-05"})

    async def call_tool(self, tool_name: str, tool_params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool on this MCP server."""
        return await self.transport.send_request("tools/call", {"name": tool_name, "arguments": tool_params})


# ── In-Memory Registry Tests ───────────────────────────────────────


class InMemoryMCPRegistry:
    """A non-persistent MCP registry for testing / single-node scenarios."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}

    async def register(self, server: MCPServer) -> None:
        self._servers[server.name] = server

    async def unregister(self, name: str) -> None:
        self._servers.pop(name, None)

    def find_tools(self, search_term: str = "", only_active: bool = True) -> list[dict[str, Any]]:
        """MCP-style tool search across all registered servers."""
        results: list[dict[str, Any]] = []
        for srv in self._servers.values():
            for tool in srv.tools:
                tool_name = tool.get("name", "")
                if not search_term or search_term.lower() in tool_name.lower():
                    results.append({
                        "server_name": srv.name,
                        "server_description": srv.description,
                        "tool": tool,
                    })
        return results

    def list_servers(self) -> list[MCPServer]:
        return list(self._servers.values())


# ── Redis Distributed Registry ─────────────────────────────────────


class RedisMCPRegistry:
    """Distributed MCP registry backed by Redis.

    Uses ``maop:mcp:servers`` hash (each entry: server_name → serialized
    JSON tool manifest). A pub/sub channel ``maop:mcp:registry-changes``
    notifies all nodes of topology changes.
    """

    _HASH_KEY = "maop:mcp:servers"
    _CHANNEL_KEY = "maop:mcp:registry-changes"

    def __init__(self, redis_url: str = "redis://localhost:6379/11") -> None:
        self._redis_url = redis_url
        self._redis = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._redis_url)
        return self._redis

    async def register(self, server: MCPServer) -> None:
        r = await self._get_redis()
        data = json.dumps({
            "name": server.name,
            "transport": server.transport.name,
            "description": server.description,
            "tools": server.tools,
            "metadata": server.metadata,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })
        pipe = r.pipeline()
        pipe.hset(self._HASH_KEY, server.name, data)
        pipe.publish(self._CHANNEL_KEY, json.dumps({"action": "register", "name": server.name}))
        await pipe.execute()
        logger.debug("[mcp-redis] registered server %s", server.name)

    async def unregister(self, name: str) -> None:
        r = await self._get_redis()
        pipe = r.pipeline()
        pipe.hdel(self._HASH_KEY, name)
        pipe.publish(self._CHANNEL_KEY, json.dumps({"action": "unregister", "name": name}))
        await pipe.execute()
        logger.debug("[mcp-redis] unregistered server %s", name)

    def find_tools(self, search_term: str = "") -> list[dict[str, Any]]:
        """Search Redis for tool manifests matching the term.

        This is a synchronous wrapper; in production, await
        ``async_find_tools`` instead.
        """
        # A sync method cannot await the Redis client, and returning an empty
        # list silently looks like "no tools registered" — say so instead.
        logger.warning(
            "[mcp-redis] find_tools() is synchronous and cannot query Redis; "
            "use await async_find_tools() instead. Returning [].",
        )
        return []

    async def async_find_tools(self, search_term: str = "") -> list[dict[str, Any]]:
        """Async tool search across all Redis-stored MCP server manifests."""
        r = await self._get_redis()
        # redis.asyncio: hgetall is a coroutine — without await, items is a
        # coroutine, isinstance(items, dict) is False, and this silently
        # returns [] for every search.
        items = await r.hgetall(self._HASH_KEY)
        results: list[dict[str, Any]] = []
        for _, raw in items.items() if isinstance(items, dict) else []:
            try:
                data = json.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw
            except (json.JSONDecodeError, TypeError):  # silent-ok: scan-continue
                continue
            for tool in data.get("tools", []):
                tool_name = tool.get("name", "")
                if not search_term or search_term.lower() in tool_name.lower():
                    results.append({
                        "server_name": data.get("name", ""),
                        "server_description": data.get("description", ""),
                        "tool": tool,
                    })
        return results


# ── MCP Hub (main entry point) ─────────────────────────────────────


class MCPHub:
    """Central MCP Hub: registry, routing, and protocol multiplexing.

    Parameters
    ----------
    redis_url : str
        Redis URL for distributed registry. If None, falls back to
        in-memory registry (single-node mode).
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or "redis://localhost:6379/11"
        self._registry = RedisMCPRegistry(self._redis_url)
        self._local_servers: dict[str, MCPServer] = {}  # local connection handles
        self._running = False

    async def start(self) -> None:
        """Start the hub (open Redis connection)."""
        self._running = True
        await self._registry._get_redis()  # warm connection
        logger.info("[mcp-hub] started (redis=%s)", self._redis_url)

    async def stop(self) -> None:
        """Stop the hub and disconnect all transports."""
        self._running = False
        for srv in self._local_servers.values():
            await srv.transport.disconnect()
        self._local_servers.clear()
        logger.info("[mcp-hub] stopped")

    # ── Registration ─────────────────────────────────────────────

    async def register_server(self, server: MCPServer) -> None:
        """Register an MCP tool server."""
        self._local_servers[server.name] = server
        await self._registry.register(server)
        logger.info("[mcp-hub] registered server '%s' (%d tools)", server.name, len(server.tools))

    async def unregister_server(self, name: str) -> None:
        """Remove an MCP tool server."""
        srv = self._local_servers.pop(name, None)
        if srv:
            await srv.transport.disconnect()
        await self._registry.unregister(name)

    # ── Tool Discovery & Invocation ──────────────────────────────

    async def find_tools(self, search_term: str = "") -> list[dict[str, Any]]:
        """Search for tools across all registered MCP servers."""
        return await self._registry.async_find_tools(search_term)

    async def call_tool(self, server_name: str, tool_name: str, tool_params: dict[str, Any]) -> dict[str, Any]:
        """Call a specific tool on a specific MCP server."""
        srv = self._local_servers.get(server_name)
        if not srv:
            raise ValueError(f"MCP server '{server_name}' not found in local hub; only stored in Redis.")
        return await srv.call_tool(tool_name, tool_params)

    # ── Stdio server factory ─────────────────────────────────────

    async def add_stdio_server(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        description: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> MCPServer:
        """Convenience: create and register an Stdio-based MCP server."""
        transport = StdioTransport(command, args, name=name)
        server = MCPServer(name=name, transport=transport, description=description, tools=tools or [])
        await self.register_server(server)
        return server

    # ── SSE server factory ───────────────────────────────────────

    async def add_sse_server(
        self,
        name: str,
        url: str,
        description: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> MCPServer:
        """Convenience: create and register an SSE-based MCP server."""
        transport = SSETransport(url, name=name)
        server = MCPServer(name=name, transport=transport, description=description, tools=tools or [])
        await self.register_server(server)
        return server

    # ── WebSocket server factory ─────────────────────────────────

    async def add_ws_server(
        self,
        name: str,
        ws_url: str,
        description: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> MCPServer:
        """Convenience: create and register a WebSocket-based MCP server."""
        transport = WebSocketTransport(ws_url, name=name)
        server = MCPServer(name=name, transport=transport, description=description, tools=tools or [])
        await self.register_server(server)
        return server

    # ── Streamable HTTP server factory ──────────────────────────

    async def add_streamable_http_server(
        self,
        name: str,
        url: str,
        description: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> MCPServer:
        """Convenience: create and register a StreamableHTTP-based MCP server."""
        transport = StreamableHTTPTransport(url, name=name)
        server = MCPServer(name=name, transport=transport, description=description, tools=tools or [])
        await self.register_server(server)
        return server
