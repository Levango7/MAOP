"""MCP Hub type definitions and exceptions.

Extracted from mcp_hub.py for single-responsibility separation.
All MCP protocol data models, enums, and exceptions live here.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    WEBSOCKET = "websocket"
    # δ-4: MCP 2025 spec streamable-http transport. Server responds with
    # either text/event-stream (SSE-framed JSON-RPC) or application/json.
    STREAMABLE_HTTP = "streamable_http"


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
    # δ-4: optional pre-existing MCP session id for streamable_http
    # transport (MCP 2025 Mcp-Session-Id header). When set, the transport
    # sends it on every request; when unset, the transport captures the
    # session id returned by the server on the first request.
    session_id: str = ""
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 3
    reconnect_delay_s: float = 5.0
    # ── δ-3: per-server permission scope (all optional, backward-compatible) ──
    # When None, no restriction is applied for that dimension.
    # Blacklist (denied_tools) takes precedence over whitelist (allowed_tools).
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    allowed_users: list[str] | None = None
    allowed_roles: list[str] | None = None


class MCPPermissionDeniedError(Exception):
    """Raised when an MCP tool call is denied by the permission checker.

    δ-3: Carries the permission decision so callers (and tests) can inspect
    the matched rule and reason. The hub converts a denied decision into
    this exception instead of returning a ``ToolResult`` so the failure mode
    is unambiguous: a returned ``ToolResult`` always represents an actual
    tool invocation, never a permission rejection.
    """

    def __init__(self, server_name: str, tool_name: str, reason: str, matched_rule: str = "") -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.reason = reason
        self.matched_rule = matched_rule
        super().__init__(
            f"MCP tool '{tool_name}' on server '{server_name}' denied: {reason}"
            + (f" (rule={matched_rule})" if matched_rule else "")
        )


class MCPRateLimitedError(Exception):
    """Raised when an MCP tool call is rejected by the per-server RPM limiter.

    δ-5: Carries the server id / name so callers (and tests) can surface
    a meaningful "retry after" hint. The hub raises this instead of
    returning a ``ToolResult`` so a rate-limited call is unambiguously
    distinguishable from a tool-level error — matching the existing
    :class:`MCPPermissionDeniedError` convention.
    """

    def __init__(self, server_id: str, server_name: str, tool_name: str) -> None:
        self.server_id = server_id
        self.server_name = server_name
        self.tool_name = tool_name
        super().__init__(
            f"MCP tool '{tool_name}' on server '{server_name}' ({server_id}) "
            f"rate-limited by per-server RPM quota"
        )


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
