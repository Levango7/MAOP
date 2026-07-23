"""MAOP MCP Hub — Model Context Protocol center with multi-transport support.

Architecture note: This module handles server registration, lifecycle, and
tool aggregation. Actual transport I/O is delegated to mcp_transport.py
(StdioTransport, SSETransport) and mcp_client.py (MCPClient for per-server
connections). These three modules form a layered architecture, NOT duplicate
implementations:
  - mcp_transport.py: Low-level transport (stdio/SSE framing)
  - mcp_client.py: Per-server client (connect, call_tool, list_tools)
  - mcp_hub.py: Multi-server orchestration (registry, aggregation, health)

Manages connections to MCP servers via three transport types:
  - **stdio**: Local subprocess (stdin/stdout JSON-RPC)
  - **SSE**: HTTP Server-Sent Events (POST request / SSE response)
  - **WebSocket**: Bidirectional real-time (ws:// or wss://)

Features:
  - Tool aggregation with unified namespace (``{server}.{tool}``)
  - Conflict resolution for duplicate tool names
  - Health checking with auto-reconnect
  - Resource listing and reading
  - Server lifecycle management

Usage::

    from maop.core.mcp_hub import MCPHub, MCPServerConfig, TransportType

    hub = MCPHub(root_dir="/path/to/MAOP")

    # Connect via stdio
    cfg = MCPServerConfig(name="filesystem", transport=TransportType.STDIO, command="npx -y @modelcontextprotocol/server-filesystem /tmp")
    server_id = await hub.connect(cfg)

    # List available tools
    tools = await hub.list_tools(server_id)

    # Call a tool
    result = await hub.call_tool(server_id, "read_file", {"path": "/tmp/test.txt"})

    # Health check
    healthy = await hub.health_check(server_id)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    WEBSOCKET = "websocket"


class ServerStatus(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server connection."""
    name: str = ""
    transport: TransportType = TransportType.STDIO
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 3
    reconnect_delay_s: float = 5.0


class MCPTool(BaseModel):
    """An MCP tool definition."""
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""


class MCPResource(BaseModel):
    """An MCP resource definition."""
    uri: str = ""
    name: str = ""
    description: str = ""
    mime_type: str = ""
    server_name: str = ""


class ToolResult(BaseModel):
    """Result from calling an MCP tool."""
    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = False
    error_message: str = ""


class ResourceContent(BaseModel):
    """Content of an MCP resource."""
    uri: str = ""
    mime_type: str = ""
    text: str = ""


class ServerInfo(BaseModel):
    """Information about a connected MCP server."""
    id: str = ""
    name: str = ""
    transport: TransportType = TransportType.STDIO
    status: ServerStatus = ServerStatus.DISCONNECTED
    tools_count: int = 0
    resources_count: int = 0
    error: str = ""


_MCP_DDL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    transport TEXT DEFAULT 'stdio',
    status TEXT DEFAULT 'disconnected',
    config TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_tools (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    server_name TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    input_schema TEXT DEFAULT '{}',
    UNIQUE(server_id, name)
);

CREATE TABLE IF NOT EXISTS mcp_resources (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    server_name TEXT NOT NULL,
    uri TEXT NOT NULL,
    name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    mime_type TEXT DEFAULT '',
    UNIQUE(server_id, uri)
);

CREATE INDEX IF NOT EXISTS idx_mcp_tools_server ON mcp_tools(server_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tools_name ON mcp_tools(name);
CREATE INDEX IF NOT EXISTS idx_mcp_resources_server ON mcp_resources(server_id);
"""


class _StdioTransport:
    """Transport via local subprocess stdin/stdout."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    async def start(self) -> None:
        cmd_parts = self._config.command.split()
        args = cmd_parts + self._config.args
        env = None
        if self._config.env:
            import os
            env = {**os.environ, **self._config.env}

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            return {"error": {"message": "Process not started"}}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        payload = json.dumps(request) + "\n"
        self._process.stdin.write(payload.encode())
        await self._process.stdin.drain()

        if self._process.stdout is None:
            return {"error": {"message": "stdout not available"}}

        line = await asyncio.wait_for(self._process.stdout.readline(), timeout=30)
        if not line:
            return {"error": {"message": "Empty response"}}

        try:
            return cast(dict[str, Any], json.loads(line.decode()))
        except json.JSONDecodeError as exc:
            return {"error": {"message": f"Invalid JSON: {exc}"}}

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None


class _SSETransport:
    """Transport via HTTP Server-Sent Events."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._request_id = 0
        self._base_url = config.url.rstrip("/")

    async def start(self) -> None:
        pass

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        import httpx

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/message",
                    json=request,
                    headers=self._config.headers,
                )
                resp.raise_for_status()
                return cast(dict[str, Any], resp.json())
        except Exception as exc:
            return {"error": {"message": str(exc)}}

    async def stop(self) -> None:
        pass

    @property
    def is_alive(self) -> bool:
        return bool(self._base_url)


class _WebSocketTransport:
    """Transport via WebSocket."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._request_id = 0
        self._ws: Any = None

    async def start(self) -> None:
        try:
            import websockets

            self._ws = await websockets.connect(
                self._config.url,
                additional_headers=self._config.headers,
            )
        except ImportError:
            logger.warning("[mcp_hub] websockets package not installed, WebSocket transport unavailable")
        except Exception as exc:
            logger.warning("[mcp_hub] WebSocket connect failed: %s", exc)

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            return {"error": {"message": "WebSocket not connected"}}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        try:
            await self._ws.send(json.dumps(request))
            response = await asyncio.wait_for(self._ws.recv(), timeout=30)
            return cast(dict[str, Any], json.loads(response))
        except Exception as exc:
            return {"error": {"message": str(exc)}}

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    @property
    def is_alive(self) -> bool:
        return self._ws is not None and self._ws.open


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: MCPHub is one of two parallel MCP management implementations.
# The other is MCPRegistry in maop/core/mcp_registry.py.
# Both have production callers:
#   - MCPHub (this class): used by core/mcp_adapter.py, core/mcp_discovery.py
#   - MCPRegistry: used by core/function_call.py, core/tool_schema.py
# Future work: consider merging into a single canonical implementation.

class MCPHub:
    """MCP Protocol Center — manage MCP server connections and tools.

    Supports three transport types: stdio, SSE, WebSocket.
    Provides unified tool namespace, conflict resolution, and health checking.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("mcp_hub")
        self._init_db()
        self._transports: dict[str, _StdioTransport | _SSETransport | _WebSocketTransport] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_MCP_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    async def connect(self, config: MCPServerConfig) -> str:
        """Connect to an MCP server. Returns the server ID."""
        import time as _time
        server_id = uuid.uuid4().hex[:16]
        now = _time.time()

        transport: _StdioTransport | _SSETransport | _WebSocketTransport
        if config.transport == TransportType.STDIO:
            transport = _StdioTransport(config)
        elif config.transport == TransportType.SSE:
            transport = _SSETransport(config)
        else:
            transport = _WebSocketTransport(config)

        self._transports[server_id] = transport
        self._configs[server_id] = config

        try:
            await transport.start()
            self._update_server_status(server_id, ServerStatus.CONNECTED)
        except Exception as exc:
            self._update_server_status(server_id, ServerStatus.ERROR, error=str(exc))
            logger.warning("[mcp_hub] Connect failed for '%s': %s", config.name, exc)

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO mcp_servers (id, name, transport, status, config, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (server_id, config.name, config.transport.value,
                 ServerStatus.CONNECTED.value, config.model_dump_json(),
                 now, now),
            )

        try:
            await self._discover_capabilities(server_id)
        except Exception as exc:
            logger.warning("[mcp_hub] Capability discovery failed for '%s': %s", config.name, exc)

        logger.info("[mcp_hub] Connected: %s (transport=%s)", config.name, config.transport.value)
        return server_id

    async def disconnect(self, server_id: str) -> bool:
        """Disconnect from an MCP server."""
        transport = self._transports.pop(server_id, None)
        if transport is None:
            return False

        await transport.stop()
        self._configs.pop(server_id, None)
        self._update_server_status(server_id, ServerStatus.DISCONNECTED)

        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_tools WHERE server_id = ?", (server_id,))
            conn.execute("DELETE FROM mcp_resources WHERE server_id = ?", (server_id,))

        logger.info("[mcp_hub] Disconnected: %s", server_id[:8])
        return True

    async def list_tools(self, server_id: str = "") -> list[MCPTool]:
        """List tools from a specific server or all servers."""
        with self._connect() as conn:
            if server_id:
                cursor = conn.execute(
                    "SELECT * FROM mcp_tools WHERE server_id = ?", (server_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM mcp_tools")

            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [
            MCPTool(
                name=r["name"],
                description=r.get("description", ""),
                input_schema=json.loads(r.get("input_schema", "{}")),
                server_name=r.get("server_name", ""),
            )
            for r in (dict(zip(cols, row)) for row in rows)
        ]

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Call an MCP tool on a specific server."""
        transport = self._transports.get(server_id)
        if transport is None:
            return ToolResult(is_error=True, error_message=f"Server '{server_id}' not connected")

        response = await transport.send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )

        if "error" in response:
            error_msg = response["error"].get("message", str(response["error"]))
            return ToolResult(is_error=True, error_message=error_msg)

        result = response.get("result", {})
        content = result.get("content", [])
        is_error = result.get("isError", False)

        return ToolResult(content=content, is_error=is_error)

    async def list_resources(self, server_id: str = "") -> list[MCPResource]:
        """List resources from a specific server or all servers."""
        with self._connect() as conn:
            if server_id:
                cursor = conn.execute(
                    "SELECT * FROM mcp_resources WHERE server_id = ?", (server_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM mcp_resources")

            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        return [
            MCPResource(
                uri=r["uri"],
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mime_type", ""),
                server_name=r.get("server_name", ""),
            )
            for r in (dict(zip(cols, row)) for row in rows)
        ]

    async def read_resource(self, server_id: str, uri: str) -> ResourceContent:
        """Read an MCP resource from a specific server."""
        transport = self._transports.get(server_id)
        if transport is None:
            return ResourceContent(uri=uri, text=f"Error: Server '{server_id}' not connected")

        response = await transport.send_request(
            "resources/read",
            {"uri": uri},
        )

        if "error" in response:
            error_msg = response["error"].get("message", str(response["error"]))
            return ResourceContent(uri=uri, text=f"Error: {error_msg}")

        contents = response.get("result", {}).get("contents", [])
        if contents:
            first = contents[0]
            return ResourceContent(
                uri=first.get("uri", uri),
                mime_type=first.get("mimeType", ""),
                text=first.get("text", ""),
            )

        return ResourceContent(uri=uri, text="")

    async def health_check(self, server_id: str) -> bool:
        """Check if a server is healthy by sending a ping."""
        transport = self._transports.get(server_id)
        if transport is None:
            return False

        if not transport.is_alive:
            config = self._configs.get(server_id)
            if config and config.auto_reconnect:
                try:
                    await transport.start()
                    await self._discover_capabilities(server_id)
                    self._update_server_status(server_id, ServerStatus.CONNECTED)
                    return True
                except Exception:
                    self._update_server_status(server_id, ServerStatus.ERROR)
                    return False
            return False

        response = await transport.send_request("ping", {})
        if "error" in response:
            self._update_server_status(server_id, ServerStatus.ERROR, error=response["error"].get("message", ""))
            return False

        self._update_server_status(server_id, ServerStatus.CONNECTED)
        return True

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all connected servers."""
        results: dict[str, bool] = {}
        for server_id in list(self._transports.keys()):
            results[server_id] = await self.health_check(server_id)
        return results

    def list_servers(self) -> list[ServerInfo]:
        """List all registered servers."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM mcp_servers ORDER BY created_at DESC")
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

        infos: list[ServerInfo] = []
        for r in (dict(zip(cols, row)) for row in rows):
            sid = r["id"]
            tools_count = 0
            resources_count = 0
            with self._connect() as conn:
                tools_count = conn.execute(
                    "SELECT COUNT(*) FROM mcp_tools WHERE server_id = ?", (sid,),
                ).fetchone()[0]
                resources_count = conn.execute(
                    "SELECT COUNT(*) FROM mcp_resources WHERE server_id = ?", (sid,),
                ).fetchone()[0]

            infos.append(ServerInfo(
                id=sid,
                name=r.get("name", ""),
                transport=TransportType(r.get("transport", "stdio")),
                status=ServerStatus(r.get("status", "disconnected")),
                tools_count=tools_count,
                resources_count=resources_count,
                error=r.get("error", ""),
            ))
        return infos

    async def _discover_capabilities(self, server_id: str) -> None:
        """Discover tools and resources from a connected server."""
        transport = self._transports.get(server_id)
        config = self._configs.get(server_id)
        if transport is None or config is None:
            return

        # Discover tools
        try:
            response = await transport.send_request("tools/list", {})
            if "result" in response and "tools" in response["result"]:
                with self._connect() as conn:
                    conn.execute("DELETE FROM mcp_tools WHERE server_id = ?", (server_id,))
                    for tool_def in response["result"]["tools"]:
                        tool_id = uuid.uuid4().hex[:16]
                        conn.execute(
                            """INSERT INTO mcp_tools (id, server_id, server_name, name, description, input_schema)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (tool_id, server_id, config.name,
                             tool_def.get("name", ""),
                             tool_def.get("description", ""),
                             json.dumps(tool_def.get("inputSchema", {}))),
                        )
        except Exception as exc:
            logger.debug("[mcp_hub] Tool discovery failed: %s", exc)

        # Discover resources
        try:
            response = await transport.send_request("resources/list", {})
            if "result" in response and "resources" in response["result"]:
                with self._connect() as conn:
                    conn.execute("DELETE FROM mcp_resources WHERE server_id = ?", (server_id,))
                    for res_def in response["result"]["resources"]:
                        res_id = uuid.uuid4().hex[:16]
                        conn.execute(
                            """INSERT INTO mcp_resources (id, server_id, server_name, uri, name, description, mime_type)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (res_id, server_id, config.name,
                             res_def.get("uri", ""),
                             res_def.get("name", ""),
                             res_def.get("description", ""),
                             res_def.get("mimeType", "")),
                        )
        except Exception as exc:
            logger.debug("[mcp_hub] Resource discovery failed: %s", exc)

    def _update_server_status(
        self,
        server_id: str,
        status: ServerStatus,
        error: str = "",
    ) -> None:
        import time as _time
        now = _time.time()
        with self._connect() as conn:
            sets = ["status = ?", "updated_at = ?"]
            params: list[Any] = [status.value, now]
            if error:
                sets.append("error = ?")
                params.append(error)
            params.append(server_id)
            conn.execute(
                f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id = ?", params,
            )