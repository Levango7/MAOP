"""MAOP MCP Client — Runtime client for Model Context Protocol servers.

Provides a high-level interface for connecting to MCP servers,
discovering tools, calling tools, and reading resources.

Usage::

    from maop.core.mcp_client import MCPClient

    client = MCPClient(name="filesystem", command="npx",
                       args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    await client.connect()
    tools = await client.discover_tools()
    result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})
    await client.disconnect()
"""


from __future__ import annotations


# ── t14: Deprecation notice ──────────────────────────────────────────────
# This module is part of the "Stack B" MCP implementation that duplicates
# concepts already provided by maop.core.mcp_hub (the canonical "Stack A").
#
# Duplicated concepts:
#   - Transport: StdioTransport/SSETransport  ↔  mcp_hub._StdioTransport/_SSETransport
#   - Config:    MCPServerConfig              ↔  mcp_hub.MCPServerConfig
#   - Models:    MCPToolDef/MCPResourceDef/... ↔  mcp_hub.MCPTool/MCPResource/...
#
# Migration path (future PR):
#   1. Align class names with mcp_hub (or add aliases).
#   2. Re-export from mcp_hub instead of maintaining separate impls.
#   3. Update function_call.py / tool_schema.py to import from mcp_hub.
#   4. Remove this file once all callers have migrated.
#
# Until then, this module remains fully functional but emits a one-time
# DeprecationWarning on first import.
import warnings as _warnings_t14
_warnings_t14.warn(
    f"{__name__} is part of the deprecated Stack B MCP implementation; "
    "prefer maop.core.mcp_hub for new code. See module docstring for "
    "the migration path.",
    DeprecationWarning,
    stacklevel=2,
)
del _warnings_t14

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from maop.core.mcp_transport import StdioTransport, SSETransport, TransportResult

logger = logging.getLogger(__name__)


class MCPServerStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class MCPToolDef(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""


class MCPResourceDef(BaseModel):
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""
    server_name: str = ""


class MCPToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str = ""
    duration_ms: int = 0


class MCPServerConfig(BaseModel):
    name: str
    transport: str = "stdio"  # stdio | sse
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    auto_connect: bool = False
    timeout: float = 30.0


class MCPClient:
    """High-level MCP client for a single server connection.

    Wraps a transport (Stdio or SSE) and provides typed methods
    for the standard MCP protocol operations.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._transport: StdioTransport | SSETransport | None = None
        self._status = MCPServerStatus.DISCONNECTED
        self._tools: list[MCPToolDef] = []
        self._resources: list[MCPResourceDef] = []
        self._connected_at: float = 0.0
        self._last_error: str = ""
        self._call_count: int = 0
        self._error_count: int = 0

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def status(self) -> MCPServerStatus:
        return self._status

    @property
    def tools(self) -> list[MCPToolDef]:
        return self._tools

    @property
    def resources(self) -> list[MCPResourceDef]:
        return self._resources

    @property
    def connected_at(self) -> float:
        return self._connected_at

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self._status.value,
            "tools_count": len(self._tools),
            "resources_count": len(self._resources),
            "call_count": self._call_count,
            "error_count": self._error_count,
            "connected_at": self._connected_at,
            "last_error": self._last_error,
        }

    def _create_transport(self) -> StdioTransport | SSETransport:
        if self._config.transport == "sse":
            return SSETransport(url=self._config.url, timeout=self._config.timeout)
        return StdioTransport(
            command=self._config.command,
            args=self._config.args,
            env=self._config.env,
            timeout=self._config.timeout,
        )

    async def connect(self) -> bool:
        if self._status == MCPServerStatus.CONNECTED:
            return True
        self._status = MCPServerStatus.CONNECTING
        try:
            self._transport = self._create_transport()
            ok = await self._transport.connect()
            if ok:
                self._status = MCPServerStatus.CONNECTED
                self._connected_at = time.time()
                logger.info("MCP client '%s' connected", self.name)
                await self.discover_tools()
                return True
            self._status = MCPServerStatus.ERROR
            self._last_error = "Transport connect failed"
            return False
        except Exception as exc:
            self._status = MCPServerStatus.ERROR
            self._last_error = str(exc)
            logger.error("MCP client '%s' connect error: %s", self.name, exc)
            return False

    async def disconnect(self) -> None:
        if self._transport:
            await self._transport.disconnect()
            self._transport = None
        self._status = MCPServerStatus.DISCONNECTED
        self._tools = []
        self._resources = []

    async def discover_tools(self) -> list[MCPToolDef]:
        if not self._transport or self._status != MCPServerStatus.CONNECTED:
            return []
        result = await self._transport.request("tools/list", {})
        if not result.success:
            logger.warning("MCP '%s' tools/list failed: %s", self.name, result.error)
            return []
        tools_data = result.data
        if isinstance(tools_data, dict):
            tools_list = tools_data.get("tools", [])
        elif isinstance(tools_data, list):
            tools_list = tools_data
        else:
            return []
        self._tools = [
            MCPToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.name,
            )
            for t in tools_list if isinstance(t, dict)
        ]
        logger.info("MCP '%s' discovered %d tools", self.name, len(self._tools))
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        if not self._transport or self._status != MCPServerStatus.CONNECTED:
            return MCPToolResult(success=False, error="Not connected")
        start = time.monotonic()
        self._call_count += 1
        try:
            result = await self._transport.request("tools/call", {
                "name": tool_name,
                "arguments": arguments or {},
            })
            duration_ms = int((time.monotonic() - start) * 1000)
            if result.success:
                return MCPToolResult(success=True, output=result.data, duration_ms=duration_ms)
            self._error_count += 1
            return MCPToolResult(success=False, error=result.error, duration_ms=duration_ms)
        except Exception as exc:
            self._error_count += 1
            duration_ms = int((time.monotonic() - start) * 1000)
            return MCPToolResult(success=False, error=str(exc), duration_ms=duration_ms)

    async def list_resources(self) -> list[MCPResourceDef]:
        if not self._transport or self._status != MCPServerStatus.CONNECTED:
            return []
        result = await self._transport.request("resources/list", {})
        if not result.success:
            return []
        res_data = result.data
        if isinstance(res_data, dict):
            resources_list = res_data.get("resources", [])
        elif isinstance(res_data, list):
            resources_list = res_data
        else:
            return []
        self._resources = [
            MCPResourceDef(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", ""),
                server_name=self.name,
            )
            for r in resources_list if isinstance(r, dict)
        ]
        return self._resources

    async def read_resource(self, uri: str) -> TransportResult:
        if not self._transport or self._status != MCPServerStatus.CONNECTED:
            return TransportResult(success=False, error="Not connected")
        return await self._transport.request("resources/read", {"uri": uri})

    async def list_prompts(self) -> TransportResult:
        if not self._transport or self._status != MCPServerStatus.CONNECTED:
            return TransportResult(success=False, error="Not connected")
        return await self._transport.request("prompts/list", {})

    async def reconnect(self) -> bool:
        await self.disconnect()
        return await self.connect()
