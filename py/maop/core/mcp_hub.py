"""MAOP MCP Hub — Model Context Protocol center with multi-transport support.

Architecture note: This module handles server registration, lifecycle, and
tool aggregation. Actual transport I/O is delegated to mcp_transport.py
(StdioTransport, SSETransport) and mcp_client.py (MCPClient for per-server
connections). These three modules form a layered architecture, NOT duplicate
implementations:
  - mcp_transport.py: Low-level transport (stdio/SSE framing)
  - mcp_client.py: Per-server client (connect, call_tool, list_tools)
  - mcp_hub.py: Multi-server orchestration (registry, aggregation, health)

Manages connections to MCP servers via four transport types:
  - **stdio**: Local subprocess (stdin/stdout JSON-RPC)
  - **SSE**: HTTP Server-Sent Events (POST request / SSE response)
  - **WebSocket**: Bidirectional real-time (ws:// or wss://)
  - **streamable_http**: MCP 2025 spec — POST JSON-RPC, response is
    either ``text/event-stream`` (SSE-framed) or ``application/json``
    (single-shot), with optional ``Mcp-Session-Id`` header.

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
import contextlib
import json
import logging
import time as _time
import urllib.error
import urllib.request
import uuid
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from maop.core import otel
from maop.core.db_utils import get_db_path, sqlite_connect

if TYPE_CHECKING:
    # Imported lazily at runtime inside MCPHub.call_tool to avoid a
    # circular import: mcp_permission.py imports MCPServerConfig from
    # this module, so we cannot import it at module load time.
    from maop.core.mcp_audit import MCPAuditLogger
    from maop.core.mcp_cache import MCPCache
    from maop.core.mcp_concurrency import MCPServerConcurrency, MCPServerRateLimiter
    from maop.core.mcp_permission import MCPPermissionChecker

logger = logging.getLogger(__name__)


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
    """Transport via local subprocess stdin/stdout.

    Security (C-3 fix): the user-supplied ``command`` is validated against
    a static whitelist of known MCP server runners. The first token of
    ``command`` (after ``shlex`` splitting) must resolve to one of the
    whitelisted binaries; otherwise ``start()`` raises ``ValueError`` and
    no subprocess is spawned. This prevents command injection via a
    malicious ``MCPServerConfig.command`` value (e.g. ``"rm -rf /"`` or
    ``"bash -c '...'"``).

    The whitelist is conservative by design — extend it only when a new
    MCP server runtime genuinely requires a binary not already covered.
    Operators who need an unlisted binary can set the
    ``MAOP_MCP_STRICT_COMMAND_WHITELIST=0`` env var to fall back to a
    warning-only mode (NOT recommended for production).
    """

    #: Whitelisted command basenames. The first token of
    #: ``MCPServerConfig.command`` must resolve to one of these (after
    #: ``shlex.split`` and ``shutil.which``).
    ALLOWED_COMMANDS: tuple[str, ...] = (
        "npx", "npm", "node", "deno", "bun",
        "python", "python3", "py", "uv", "uvx", "poetry", "pipx",
        "ruby", "bundle", "gem",
        "go", "java", "javac",
        "docker",
    )

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    @classmethod
    def _validate_command(cls, command: str) -> list[str]:
        """Split and validate ``command`` against the whitelist.

        Returns the resolved argv list (executable + native args) ready
        for :func:`asyncio.create_subprocess_exec`. Raises ``ValueError``
        if the command is empty or its first token is not whitelisted.

        When ``MAOP_MCP_STRICT_COMMAND_WHITELIST=0`` is set, the check
        degrades to a logged warning and the command is allowed through
        — this escape hatch exists for development only and MUST NOT be
        used in production.
        """
        import os
        import re
        import shlex
        import shutil

        if not command or not command.strip():
            raise ValueError("MCPServerConfig.command is empty; cannot start stdio transport")

        try:
            cmd_parts = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Invalid command syntax '{command}': {exc}") from exc

        if not cmd_parts:
            raise ValueError("MCPServerConfig.command produced empty argv after shlex.split")

        executable = cmd_parts[0]
        # Resolve via PATH so ``npx`` matches /usr/local/bin/npx etc.
        resolved = shutil.which(executable) or executable
        basename = Path(resolved).name
        # On Windows, ``shutil.which`` returns the full path including
        # the ``.EXE`` / ``.CMD`` / ``.BAT`` extension (e.g. ``npx.CMD``).
        # Strip these so the whitelist (``"npx"``, ``"node"``, ...) matches
        # cross-platform. POSIX systems have no such extensions, so this
        # is a no-op there.
        basename = re.sub(r"\.(?:exe|cmd|bat)$", "", basename, flags=re.IGNORECASE)

        strict = os.environ.get("MAOP_MCP_STRICT_COMMAND_WHITELIST", "1") != "0"
        if basename not in cls.ALLOWED_COMMANDS:
            msg = (
                f"MCP stdio command '{executable}' (resolved to '{basename}') "
                f"is not in the whitelist {cls.ALLOWED_COMMANDS}. "
                f"Refusing to spawn subprocess."
            )
            if strict:
                raise ValueError(msg)
            logger.warning("[mcp_hub] %s (MAOP_MCP_STRICT_COMMAND_WHITELIST=0 — allowed)", msg)

        # Return the original argv (don't replace executable with resolved
        # path — the original may carry semantic meaning, e.g. ``npx`` vs
        # the full path to node).
        return cmd_parts

    async def start(self) -> None:
        cmd_parts = self._validate_command(self._config.command)
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
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    @property
    def is_alive(self) -> bool:
        return self._ws is not None and self._ws.open


class _StreamableHttpTransport:
    """δ-4: MCP 2025 streamable-http transport.

    Sends JSON-RPC requests via HTTP POST. The server may respond with
    either:

    * ``text/event-stream`` — SSE-framed JSON-RPC responses. We parse
      ``data: {...}`` lines until we see the response carrying our
      request id (or a terminal event).
    * ``application/json`` — a single JSON-RPC response.

    Implements MCP 2025 session management via the ``Mcp-Session-Id``
    header: when the server returns it on the initial response, the
    transport stores it and sends it on every subsequent request. The
    caller can also pre-seed a session id via ``MCPServerConfig.session_id``.

    Uses only the stdlib ``urllib`` package — zero new dependencies. All
    I/O is synchronous and offloaded via ``asyncio.to_thread`` so the
    hub's async API contract is preserved (matching how the existing
    transports wrap blocking stdlib calls).
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._request_id = 0
        self._base_url = config.url.rstrip("/")
        self._session_id = config.session_id or ""
        # Set to True after the first successful request (or after the
        # server returns a session id). is_alive uses this so callers
        # can detect "never connected" without a real probe.
        self._initialized = bool(self._session_id)

    async def start(self) -> None:
        # No persistent connection to open — the transport is stateless
        # HTTP. Marking ourselves alive if we either already have a
        # session id (caller-provided) or a base url is set; the real
        # probe happens on the first send_request.
        self._initialized = bool(self._base_url)

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._base_url:
            return {"error": {"message": "No base URL configured for streamable_http transport"}}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        try:
            response = await asyncio.to_thread(self._http_post_jsonrpc, request)
        except Exception as exc:
            return {"error": {"message": str(exc)}}

        # Capture the session id returned by the server (MCP 2025). This
        # is the only side-effect of send_request besides the response.
        if response.get("_mcp_session_id"):
            self._session_id = response.pop("_mcp_session_id")
            self._initialized = True

        return response

    def _http_post_jsonrpc(self, request: dict[str, Any]) -> dict[str, Any]:
        """Synchronous POST + response parser. Runs in a worker thread.

        Returns a JSON-RPC response dict. On HTTP/network errors returns
        ``{"error": {"message": ...}}`` to match the convention used by
        the other transports. A hidden ``_mcp_session_id`` key is added
        when the server returns the ``Mcp-Session-Id`` header so the
        async wrapper can capture it without re-parsing the headers.
        """
        body = json.dumps(request).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        # Caller-provided headers (e.g. Authorization) win over defaults.
        headers.update(self._config.headers or {})
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(
            self._base_url,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                session_id_hdr = resp.headers.get("Mcp-Session-Id")
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Server returned an HTTP error code — try to surface the
            # response body if it's JSON, otherwise fall back to the
            # standard reason string.
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover — defensive
                err_body = ""
            return {"error": {"message": f"HTTP {exc.code}: {err_body or exc.reason}"}}
        except urllib.error.URLError as exc:
            return {"error": {"message": f"Network error: {exc.reason}"}}
        except Exception as exc:
            return {"error": {"message": f"Request failed: {exc}"}}

        # 202 (Accepted) is used by some MCP servers for async notifications
        # — there is no body to parse, so return an empty success result.
        if status == 202:
            result: dict[str, Any] = {"result": {}}
            if session_id_hdr:
                result["_mcp_session_id"] = session_id_hdr
            return result

        # Non-streaming JSON response.
        if "text/event-stream" not in content_type:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return {"error": {"message": f"Invalid JSON response: {exc}"}}
            if session_id_hdr:
                parsed["_mcp_session_id"] = session_id_hdr
            return cast(dict[str, Any], parsed)

        # SSE streaming response. Parse `data: {...}` lines and return
        # the first JSON-RPC response carrying our request id (or the
        # first JSON object if no id match is found).
        return self._parse_sse_response(raw, session_id_hdr)

    def _parse_sse_response(self, raw: bytes, session_id_hdr: str | None) -> dict[str, Any]:
        """Parse an SSE stream and return the JSON-RPC response payload.

        SSE framing (per the HTML spec) uses ``data: <payload>`` lines.
        A blank line marks an event boundary. We accumulate multi-line
        ``data:`` payloads (joining with newlines, matching the SSE
        spec) and parse each accumulated event as JSON.
        """
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return {"error": {"message": f"Invalid SSE encoding: {exc}"}}

        fallback: dict[str, Any] | None = None
        expected_id = self._request_id

        data_buf: list[str] = []
        for line in text.splitlines():
            if not line:
                # Blank line — event boundary. Try to parse the buffered
                # data as a JSON-RPC response.
                if data_buf:
                    payload = "\n".join(data_buf).strip()
                    data_buf = []
                    if not payload:
                        continue
                    parsed = self._try_parse_sse_payload(payload)
                    if parsed is None:
                        continue
                    if fallback is None:
                        fallback = parsed
                    if self._matches_request_id(parsed, expected_id):
                        result = dict(parsed)
                        if session_id_hdr:
                            result["_mcp_session_id"] = session_id_hdr
                        return result
                continue

            if line.startswith("data:"):
                # Per SSE spec: strip a single leading space after the colon.
                payload = line[5:]
                payload = payload.removeprefix(" ")
                data_buf.append(payload)
            # Lines starting with ":" are comments; "event:" / "id:" /
            # "retry:" are SSE control fields we don't need for JSON-RPC.

        # Handle trailing buffer without a final blank line.
        if data_buf:
            payload = "\n".join(data_buf).strip()
            if payload:
                parsed = self._try_parse_sse_payload(payload)
                if parsed is not None:
                    if fallback is None:
                        fallback = parsed
                    if self._matches_request_id(parsed, expected_id):
                        result = dict(parsed)
                        if session_id_hdr:
                            result["_mcp_session_id"] = session_id_hdr
                        return result

        if fallback is not None:
            result = dict(fallback)
            if session_id_hdr:
                result["_mcp_session_id"] = session_id_hdr
            return result

        return {"error": {"message": "SSE stream contained no JSON-RPC payload"}}

    @staticmethod
    def _try_parse_sse_payload(payload: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)
        return None

    @staticmethod
    def _matches_request_id(parsed: dict[str, Any], expected_id: int) -> bool:
        # Some servers return id as int, some as str. Compare loosely.
        rid = parsed.get("id")
        return rid == expected_id or str(rid) == str(expected_id)

    async def stop(self) -> None:
        # Stateless transport — nothing to close. We just drop the
        # session id so a subsequent start() re-establishes a new one.
        self._initialized = False

    @property
    def is_alive(self) -> bool:
        return bool(self._base_url) and self._initialized

    @property
    def session_id(self) -> str:
        """Expose the current Mcp-Session-Id (read-only view for tests)."""
        return self._session_id


# ── δ-1: Stack A/B unification ────────────────────────────────
# NOTE: MCPHub is the single canonical MCP management implementation.
# The former MCPRegistry/MCPClient/MCPTransport modules (Stack B) have
# been removed. Name-based compat shims are provided at the end of this
# class so legacy callers (dashboard routers, function_call.py,
# tool_schema.py) can use MCPHub without touching the id-based core API.

class MCPHub:
    """MCP Protocol Center — manage MCP server connections and tools.

    Supports four transport types: stdio, SSE, WebSocket, streamable_http.
    Provides unified tool namespace, conflict resolution, and health checking.

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    permission_checker:
        δ-3: Optional :class:`MCPPermissionChecker` enforcing per-server
        tool / user / role scope. When ``None`` (default), permission
        checks are skipped — backward-compatible with pre-δ-3 callers.
    audit_logger:
        δ-3: Optional :class:`MCPAuditLogger` persisting every call_tool
        invocation. When ``None``, no audit records are written.
    cache:
        δ-5: Optional :class:`MCPCache` for tool-call result caching.
        When ``None`` (default), every call goes straight to the
        transport — backward-compatible with pre-δ-5 behaviour.
    concurrency:
        δ-5: Optional :class:`MCPServerConcurrency` bounding in-flight
        calls per server. When ``None``, no concurrency limit is
        enforced.
    rate_limiter:
        δ-5: Optional :class:`MCPServerRateLimiter` enforcing per-server
        RPM. When ``None``, calls are not rate-limited.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        permission_checker: MCPPermissionChecker | None = None,
        audit_logger: MCPAuditLogger | None = None,
        cache: MCPCache | None = None,
        concurrency: MCPServerConcurrency | None = None,
        rate_limiter: MCPServerRateLimiter | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = get_db_path("mcp_hub")
        self._init_db()
        self._transports: dict[
            str,
            _StdioTransport | _SSETransport | _WebSocketTransport | _StreamableHttpTransport,
        ] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        # δ-3: optional permission + audit hooks. Kept as attributes so
        # tests / dashboard can also inject them post-construction.
        self._permission_checker = permission_checker
        self._audit_logger = audit_logger
        # δ-5: optional resilience hooks (cache / concurrency / RPM).
        # All three default to None so legacy callers see no behaviour
        # change; the call_tool hot path guards each with ``is not None``.
        self._cache = cache
        self._concurrency = concurrency
        self._rate_limiter = rate_limiter
        # δ-4: OTel tracer for MCP operations. When OTel is disabled,
        # get_tracer returns a NoopTracer so all span() blocks become
        # no-ops with zero overhead — no feature flag needed at the
        # call sites.
        self._tracer = otel.get_tracer("maop.mcp")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_MCP_DDL)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    async def connect(self, config: MCPServerConfig) -> str:
        """Connect to an MCP server. Returns the server ID."""
        with otel.span(
            self._tracer,
            "mcp.connect",
            attributes={
                "mcp.server_name": config.name,
                "mcp.transport": config.transport.value,
            },
        ):
            server_id = uuid.uuid4().hex[:16]
            now = _time.time()

            transport: _StdioTransport | _SSETransport | _WebSocketTransport | _StreamableHttpTransport
            if config.transport == TransportType.STDIO:
                transport = _StdioTransport(config)
            elif config.transport == TransportType.SSE:
                transport = _SSETransport(config)
            elif config.transport == TransportType.STREAMABLE_HTTP:
                transport = _StreamableHttpTransport(config)
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

            # δ-4: bump the connected-servers gauge so operators can
            # alert on drops. We only count successful connects — the
            # transport.start() above may have failed silently and
            # status would be ERROR, in which case we don't inc.
            if transport.is_alive:
                self._inc_connected_servers()

            logger.info("[mcp_hub] Connected: %s (transport=%s)", config.name, config.transport.value)
            return server_id

    async def disconnect(self, server_id: str) -> bool:
        """Disconnect from an MCP server."""
        config = self._configs.get(server_id)
        server_name = config.name if config is not None else server_id
        with otel.span(
            self._tracer,
            "mcp.disconnect",
            attributes={"mcp.server_name": server_name},
        ):
            transport = self._transports.pop(server_id, None)
            if transport is None:
                return False

            was_alive = transport.is_alive
            await transport.stop()
            self._configs.pop(server_id, None)
            self._update_server_status(server_id, ServerStatus.DISCONNECTED)

            with self._connect() as conn:
                conn.execute("DELETE FROM mcp_tools WHERE server_id = ?", (server_id,))
                conn.execute("DELETE FROM mcp_resources WHERE server_id = ?", (server_id,))

            # δ-4: mirror the connect-time gauge inc with a dec, but
            # only if the transport was actually alive before disconnect
            # (avoid drifting the gauge negative on double-disconnect).
            if was_alive:
                self._dec_connected_servers()

            logger.info("[mcp_hub] Disconnected: %s", server_id[:8])
            return True

    async def list_tools(self, server_id: str = "") -> list[MCPTool]:
        """List tools from a specific server or all servers."""
        config = self._configs.get(server_id) if server_id else None
        server_name = config.name if config is not None else (server_id or "all")
        with otel.span(
            self._tracer,
            "mcp.list_tools",
            attributes={"mcp.server_name": server_name},
        ):
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
        *,
        user_context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Call an MCP tool on a specific server.

        δ-3: When ``permission_checker`` was injected at construction,
        the call is first authorised against the server's
        :class:`MCPServerConfig` scope. A denied decision raises
        :class:`MCPPermissionDeniedError` (instead of returning a
        ``ToolResult``) and, when an ``audit_logger`` is present, is
        recorded with ``allowed=False``. When no checker was injected,
        the pre-δ-3 behaviour is preserved unchanged.

        ``user_context`` is an optional per-call override forwarded to
        :meth:`MCPPermissionChecker.check_tool_permission`. When omitted
        the checker falls back to its own ``user_context_provider``.
        """
        transport = self._transports.get(server_id)
        config = self._configs.get(server_id)
        server_name = config.name if config is not None else server_id

        # δ-4: Wrap the whole call_tool in an OTel span. attributes
        # include server/tool and the *keys* of arguments only — never
        # the raw arguments (could carry PII / secrets).
        arg_keys = list((arguments or {}).keys())
        with otel.span(
            self._tracer,
            "mcp.call_tool",
            attributes={
                "mcp.server_name": server_name,
                "mcp.tool_name": tool_name,
                "mcp.arguments_keys": ",".join(arg_keys),
            },
        ):
            # δ-3: Permission gate. Run only when a checker was injected;
            # otherwise behaviour is identical to the original implementation.
            checker = self._permission_checker
            if checker is not None and config is not None:
                decision = checker.check_tool_permission(config, tool_name, user_context)
                if not decision.allowed:
                    # Denied calls are still audited (allowed=False) so the
                    # rejection is observable later. We do not invoke the
                    # transport, so success=False, duration=0, error=reason.
                    self._record_audit(
                        server_name=server_name,
                        tool_name=tool_name,
                        user_context=user_context,
                        arguments=arguments,
                        allowed=False,
                        decision_reason=decision.reason,
                        success=False,
                        duration_ms=0.0,
                        error=decision.reason,
                    )
                    self._inc_metrics(allowed=False, reason=decision.matched_rule)
                    # δ-4: denied calls count toward call_errors_total
                    # (the call did not succeed) but not toward
                    # calls_total (it was never actually invoked). The
                    # audited counter from δ-3 still fires above.
                    self._record_call_error(server_name, tool_name)
                    raise MCPPermissionDeniedError(
                        server_name=server_name,
                        tool_name=tool_name,
                        reason=decision.reason,
                        matched_rule=decision.matched_rule,
                    )
                self._inc_metrics(allowed=True, reason=decision.matched_rule)

            # δ-5: cache check. Runs before the transport-None check so a
            # cached result can still be served during a transient server
            # disconnect — the whole point of caching. Skipped entirely
            # when no cache was injected (preserves pre-δ-5 behaviour).
            mcp_cache = self._cache
            if mcp_cache is not None:
                from maop.core.mcp_cache import MCPCacheKey

                cache_key = MCPCacheKey.from_arguments(server_id, tool_name, arguments)
                cached = mcp_cache.get(cache_key)
                if cached is not None:
                    self._record_cache_hit(server_name)
                    return ToolResult(
                        content=cached.get("content", []),
                        is_error=cached.get("is_error", False),
                        error_message=cached.get("error_message", ""),
                    )
                self._record_cache_miss(server_name)

            if transport is None:
                # δ-3: still audit "allowed-but-failed" so operators can see
                # authorised calls that never reached the server (e.g. broken
                # transport). Skip when no checker — preserve legacy semantics.
                if checker is not None:
                    self._record_audit(
                        server_name=server_name,
                        tool_name=tool_name,
                        user_context=user_context,
                        arguments=arguments,
                        allowed=True,
                        decision_reason="default allow",
                        success=False,
                        duration_ms=0.0,
                        error=f"Server '{server_id}' not connected",
                    )
                # δ-4: count "server not connected" as a call error.
                self._record_call_error(server_name, tool_name)
                return ToolResult(is_error=True, error_message=f"Server '{server_id}' not connected")

            # δ-5: per-server RPM rate-limit check. ``check`` is a peek —
            # it does NOT consume quota. The actual record happens after
            # the transport call returns (see ``rl.record`` below). When
            # the limiter rejects the call we raise MCPRateLimitedError
            # instead of returning a ToolResult, matching the δ-3
            # permission-denied convention so callers can distinguish
            # "rate limited" from "tool error".
            rl = self._rate_limiter
            if rl is not None and not rl.check(server_id):
                self._record_rate_limited(server_name)
                self._record_call_error(server_name, tool_name)
                raise MCPRateLimitedError(
                    server_id=server_id,
                    server_name=server_name,
                    tool_name=tool_name,
                )

            # δ-5: per-server concurrency acquire. The slot is released in
            # the ``finally`` block below so every return / raise path
            # (transport exception, response error, success) frees it.
            # ``cc.acquire`` is a synchronous blocking call (it uses
            # ``threading.Condition.wait`` to block until a slot frees up).
            # Running it directly inside this async method would stall the
            # entire event loop for up to ``timeout_s`` seconds whenever a
            # caller has to wait, starving every other coroutine — including
            # the one currently holding the slot (which would never get to
            # release it). Wrap the acquire in ``asyncio.to_thread`` so the
            # wait happens on a worker thread while the event loop keeps
            # running. ``release`` is cheap (decrement + notify) and stays
            # synchronous.
            cc = self._concurrency
            acquired_slot = False
            if cc is not None:
                acquired = await asyncio.to_thread(cc.acquire, server_id, 30.0)
                if not acquired:
                    self._record_call_error(server_name, tool_name)
                    return ToolResult(
                        is_error=True,
                        error_message=f"Concurrency limit reached for server '{server_id}'",
                    )
                acquired_slot = True

            try:
                # Time the actual transport call so the audit row carries real
                # latency, not just the permission overhead.
                call_started = _time.monotonic()
                error_msg: str | None = None
                try:
                    response = await transport.send_request(
                        "tools/call",
                        {"name": tool_name, "arguments": arguments or {}},
                    )
                except Exception as exc:
                    # Surface transport-level exceptions through the audit path
                    # and re-raise — the legacy code path also propagated these
                    # indirectly via the "error" key in the response, but a raw
                    # exception now skips that wrapper. Record + re-raise keeps
                    # the audit trail complete for debugging.
                    error_msg = str(exc)
                    duration_ms = (_time.monotonic() - call_started) * 1000.0
                    if checker is not None:
                        self._record_audit(
                            server_name=server_name,
                            tool_name=tool_name,
                            user_context=user_context,
                            arguments=arguments,
                            allowed=True,
                            decision_reason="default allow",
                            success=False,
                            duration_ms=duration_ms,
                            error=error_msg,
                        )
                    # δ-4: transport exception → error counter + duration.
                    self._record_call_error(server_name, tool_name)
                    self._record_call_duration(call_started)
                    self._record_call_attempt(server_name, tool_name)
                    raise

                duration_ms = (_time.monotonic() - call_started) * 1000.0
                # δ-4: record call volume + latency on every attempt that
                # returned a response (success or JSON-RPC error).
                self._record_call_attempt(server_name, tool_name)
                self._record_call_duration(call_started)

                # δ-5: record this call against the server's RPM quota.
                # Happens after the transport returns (success or
                # response-level error) but NOT on transport exceptions
                # (the call never reached the server, so it should not
                # consume RPM quota).
                if rl is not None:
                    rl.record(server_id)

                if "error" in response:
                    error_msg = response["error"].get("message", str(response["error"]))
                    if checker is not None:
                        self._record_audit(
                            server_name=server_name,
                            tool_name=tool_name,
                            user_context=user_context,
                            arguments=arguments,
                            allowed=True,
                            decision_reason="default allow",
                            success=False,
                            duration_ms=duration_ms,
                            error=error_msg,
                        )
                    self._record_call_error(server_name, tool_name)
                    return ToolResult(is_error=True, error_message=error_msg)

                result = response.get("result", {})
                content = result.get("content", [])
                is_error = result.get("isError", False)

                if checker is not None:
                    self._record_audit(
                        server_name=server_name,
                        tool_name=tool_name,
                        user_context=user_context,
                        arguments=arguments,
                        allowed=True,
                        decision_reason="default allow",
                        success=not is_error,
                        duration_ms=duration_ms,
                        error=error_msg if is_error else None,
                    )

                # δ-4: tool-level error flag from the server also counts.
                if is_error:
                    self._record_call_error(server_name, tool_name)

                # δ-5: cache the successful result. ``put`` re-checks
                # cacheability (no error / no _mcp_nocache) so error
                # results and opt-out tools are silently skipped.
                if mcp_cache is not None and not is_error:
                    from maop.core.mcp_cache import MCPCacheKey

                    cache_key = MCPCacheKey.from_arguments(server_id, tool_name, arguments)
                    mcp_cache.put(
                        cache_key,
                        {"content": content, "is_error": is_error, "error_message": ""},
                    )

                return ToolResult(content=content, is_error=is_error)
            finally:
                # δ-5: release the concurrency slot on every exit path —
                # normal return, response-level error return, and
                # transport-exception re-raise.
                if acquired_slot and cc is not None:
                    cc.release(server_id)

    # ── δ-3: permission + audit helpers ──────────────────────────

    def _record_audit(
        self,
        *,
        server_name: str,
        tool_name: str,
        user_context: dict[str, Any] | None,
        arguments: dict[str, Any] | None,
        allowed: bool,
        decision_reason: str,
        success: bool,
        duration_ms: float,
        error: str | None,
    ) -> None:
        """Write one MCP audit record (if an audit_logger is injected)."""
        audit = self._audit_logger
        if audit is None:
            return
        # Lazy import keeps the module-load graph acyclic.
        from maop.core.mcp_audit import MCPAuditRecord, hash_arguments

        user_id = ""
        if user_context:
            user_id = str(user_context.get("user_id", "") or "")
        record = MCPAuditRecord(
            timestamp=_time.time(),
            server_name=server_name,
            tool_name=tool_name,
            user_id=user_id,
            arguments_hash=hash_arguments(arguments),
            allowed=allowed,
            decision_reason=decision_reason,
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
        try:
            audit.log_call(record)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[mcp_hub] audit log_call failed: %s", exc)

    def _inc_metrics(self, *, allowed: bool, reason: str) -> None:
        """Update the δ-3 MCP metrics counters.

        Imports are local so a missing/optional monitoring module never
        breaks the call_tool hot path. The three counters are
        pre-registered in monitoring.py at module load; we just increment
        them here.
        """
        try:
            from maop.core.monitoring import (
                MAOP_MCP_CALL_ALLOWED_TOTAL,
                MAOP_MCP_CALL_AUDITED_TOTAL,
                MAOP_MCP_CALL_DENIED_TOTAL,
            )
        except Exception:
            return
        MAOP_MCP_CALL_AUDITED_TOTAL.inc()
        if allowed:
            MAOP_MCP_CALL_ALLOWED_TOTAL.inc()
        else:
            MAOP_MCP_CALL_DENIED_TOTAL.inc(labels={"reason": reason or "unknown"})

    # ── δ-4: OTel + metrics helpers ──────────────────────────────

    def _record_call_attempt(self, server_name: str, tool_name: str) -> None:
        """Increment MAOP_mcp_calls_total (label=server,tool)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CALLS_TOTAL
        except Exception:
            return
        MAOP_MCP_CALLS_TOTAL.inc(labels={"server": server_name, "tool": tool_name})

    def _record_call_error(self, server_name: str, tool_name: str) -> None:
        """Increment MAOP_mcp_call_errors_total (label=server,tool)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CALL_ERRORS_TOTAL
        except Exception:
            return
        MAOP_MCP_CALL_ERRORS_TOTAL.inc(labels={"server": server_name, "tool": tool_name})

    def _record_call_duration(self, started_monotonic: float) -> None:
        """Observe MAOP_mcp_call_duration_seconds (no labels; Histogram
        class in monitoring.py does not carry labels)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CALL_DURATION_SECONDS
        except Exception:
            return
        elapsed = _time.monotonic() - started_monotonic
        if elapsed < 0:
            elapsed = 0.0
        MAOP_MCP_CALL_DURATION_SECONDS.observe(elapsed)

    def _record_health_check(self, server_name: str, *, healthy: bool) -> None:
        """Increment MAOP_mcp_health_check_total (label=server,result)."""
        try:
            from maop.core.monitoring import MAOP_MCP_HEALTH_CHECK_TOTAL
        except Exception:
            return
        MAOP_MCP_HEALTH_CHECK_TOTAL.inc(
            labels={"server": server_name, "result": "healthy" if healthy else "unhealthy"},
        )

    def _inc_connected_servers(self) -> None:
        """+1 on MAOP_mcp_servers_connected (no labels)."""
        try:
            from maop.core.monitoring import MAOP_MCP_SERVERS_CONNECTED
        except Exception:
            return
        MAOP_MCP_SERVERS_CONNECTED.inc()

    def _dec_connected_servers(self) -> None:
        """-1 on MAOP_mcp_servers_connected (no labels)."""
        try:
            from maop.core.monitoring import MAOP_MCP_SERVERS_CONNECTED
        except Exception:
            return
        MAOP_MCP_SERVERS_CONNECTED.dec()

    # ── δ-5: cache / concurrency / rate-limit helpers ──────────

    def _record_cache_hit(self, server_name: str) -> None:
        """Increment MAOP_mcp_cache_hit_total (label=server)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CACHE_HIT_TOTAL
        except Exception:
            return
        MAOP_MCP_CACHE_HIT_TOTAL.inc(labels={"server": server_name})

    def _record_cache_miss(self, server_name: str) -> None:
        """Increment MAOP_mcp_cache_miss_total (label=server)."""
        try:
            from maop.core.monitoring import MAOP_MCP_CACHE_MISS_TOTAL
        except Exception:
            return
        MAOP_MCP_CACHE_MISS_TOTAL.inc(labels={"server": server_name})

    def _record_rate_limited(self, server_name: str) -> None:
        """Increment MAOP_mcp_rate_limited_total (label=server)."""
        try:
            from maop.core.monitoring import MAOP_MCP_RATE_LIMITED_TOTAL
        except Exception:
            return
        MAOP_MCP_RATE_LIMITED_TOTAL.inc(labels={"server": server_name})

    async def list_resources(self, server_id: str = "") -> list[MCPResource]:
        """List resources from a specific server or all servers."""
        config = self._configs.get(server_id) if server_id else None
        server_name = config.name if config is not None else (server_id or "all")
        with otel.span(
            self._tracer,
            "mcp.list_resources",
            attributes={"mcp.server_name": server_name},
        ):
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
        config = self._configs.get(server_id)
        server_name = config.name if config is not None else server_id
        with otel.span(
            self._tracer,
            "mcp.read_resource",
            attributes={
                "mcp.server_name": server_name,
                "mcp.resource_uri": uri,
            },
        ):
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
        config = self._configs.get(server_id)
        server_name = config.name if config is not None else server_id
        with otel.span(
            self._tracer,
            "mcp.health_check",
            attributes={"mcp.server_name": server_name},
        ):
            transport = self._transports.get(server_id)
            if transport is None:
                self._record_health_check(server_name, healthy=False)
                return False

            if not transport.is_alive:
                if config and config.auto_reconnect:
                    try:
                        await transport.start()
                        await self._discover_capabilities(server_id)
                        self._update_server_status(server_id, ServerStatus.CONNECTED)
                        self._record_health_check(server_name, healthy=True)
                        return True
                    except Exception:
                        self._update_server_status(server_id, ServerStatus.ERROR)
                        self._record_health_check(server_name, healthy=False)
                        return False
                self._record_health_check(server_name, healthy=False)
                return False

            response = await transport.send_request("ping", {})
            if "error" in response:
                self._update_server_status(server_id, ServerStatus.ERROR, error=response["error"].get("message", ""))
                self._record_health_check(server_name, healthy=False)
                return False

            self._update_server_status(server_id, ServerStatus.CONNECTED)
            self._record_health_check(server_name, healthy=True)
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

    # ── δ-1: Name-based compat shims (Stack A/B unification) ──────
    # These methods support callers (dashboard routers, function_call.py,
    # tool_schema.py) that previously used the removed MCPRegistry (Stack B).
    # They translate name-based operations to MCPHub's id-based core API
    # (connect/disconnect/call_tool which are server-id based).

    def get_server_config(self, name: str) -> MCPServerConfig | None:
        """Look up a stored server config by name."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config FROM mcp_servers WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        if row is None:
            return None
        try:
            return MCPServerConfig.model_validate_json(row["config"])
        except Exception:
            return None

    def find_server_id_by_name(self, name: str) -> str | None:
        """Look up a server_id by name (most recent record)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM mcp_servers WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return row["id"] if row else None

    def add_server(self, config: MCPServerConfig) -> bool:
        """Register a server config without connecting (compat shim)."""
        now = _time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE name = ?", (config.name,))
            server_id = uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO mcp_servers (id, name, transport, status, config, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (server_id, config.name, config.transport.value,
                 ServerStatus.DISCONNECTED.value, config.model_dump_json(),
                 "", now, now),
            )
        return True

    def remove_server(self, name: str) -> bool:
        """Remove a registered server by name (compat shim)."""
        server_id = self.find_server_id_by_name(name)
        if server_id is None:
            return False
        transport = self._transports.pop(server_id, None)
        if transport is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(transport.stop())
            except RuntimeError:
                pass
        self._configs.pop(server_id, None)
        with self._connect() as conn:
            conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            conn.execute("DELETE FROM mcp_tools WHERE server_id = ?", (server_id,))
            conn.execute("DELETE FROM mcp_resources WHERE server_id = ?", (server_id,))
        return True

    def all_tools(self) -> list[MCPTool]:
        """Return all tools across all servers (sync, compat shim)."""
        with self._connect() as conn:
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

    def find_tool(self, qualified_name: str) -> tuple[str | None, str]:
        """Find a tool by qualified name. Returns (server_id, tool_name)."""
        with self._connect() as conn:
            if "." in qualified_name:
                server_name, tool_name = qualified_name.split(".", 1)
                row = conn.execute(
                    "SELECT server_id FROM mcp_tools WHERE server_name = ? AND name = ? LIMIT 1",
                    (server_name, tool_name),
                ).fetchone()
                if row:
                    return row["server_id"], tool_name
            row = conn.execute(
                "SELECT server_id, name FROM mcp_tools WHERE name = ? LIMIT 1",
                (qualified_name,),
            ).fetchone()
            if row:
                return row["server_id"], row["name"]
        return None, qualified_name

    async def call_tool_by_name(
        self,
        qualified_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user_context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Call a tool by qualified name 'server.tool' (compat shim).

        δ-3: Forwards the optional ``user_context`` to :meth:`call_tool`
        so the dashboard router can pass the authenticated caller through
        to the permission checker without restructuring its existing
        name-based call sites.
        """
        server_id, tool_name = self.find_tool(qualified_name)
        if server_id is None:
            return ToolResult(is_error=True, error_message=f"Tool '{qualified_name}' not found")
        return await self.call_tool(server_id, tool_name, arguments or {}, user_context=user_context)
