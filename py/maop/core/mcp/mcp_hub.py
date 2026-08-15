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

    from maop.core.mcp.mcp_hub import MCPHub, MCPServerConfig, TransportType

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
import logging
import time as _time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maop.core import otel
from maop.core.backends.db_utils import get_db_path, sqlite_connect

if TYPE_CHECKING:
    # Imported lazily at runtime inside MCPHub.call_tool to avoid a
    # circular import: mcp_permission.py imports MCPServerConfig from
    # this module, so we cannot import it at module load time.
    from maop.core.mcp.mcp_audit import MCPAuditLogger
    from maop.core.mcp.mcp_cache import MCPCache
    from maop.core.mcp.mcp_concurrency import MCPServerConcurrency, MCPServerRateLimiter
    from maop.core.mcp.mcp_permission import MCPPermissionChecker

logger = logging.getLogger(__name__)
from maop.core.mcp.mcp_hub_compat import MCPHubCompatMixin
from maop.core.mcp.mcp_hub_metrics import MCPHubMetricsMixin
from maop.core.mcp.mcp_hub_ops import MCPHubOpsMixin
from maop.core.mcp.mcp_hub_transport import (
    _SSETransport,
    _StdioTransport,
    _StreamableHttpTransport,
    _WebSocketTransport,
)
from maop.core.mcp.mcp_hub_types import (  # noqa: F401  # 类型 re-export（测试与外部经 mcp_hub 引用）
    MCPPermissionDeniedError,
    MCPRateLimitedError,
    MCPResource,
    MCPServerConfig,
    MCPTool,
    ResourceContent,
    ServerInfo,
    ServerStatus,
    ToolResult,
    TransportType,
)

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


class MCPHub(MCPHubMetricsMixin, MCPHubOpsMixin, MCPHubCompatMixin):
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
        # High fix: strong references to in-flight transport.stop() tasks
        # (asyncio only keeps weak refs — tasks could be GC'd mid-flight).
        self._stop_tasks: set[asyncio.Task] = set()
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

            # C2 fix: the row does not exist yet, so calling
            # _update_server_status() before the INSERT was a silent no-op
            # (UPDATE matched 0 rows) and the INSERT hard-coded CONNECTED even
            # when transport.start() failed. Track the real status/error and
            # persist them in the INSERT itself.
            connect_status = ServerStatus.CONNECTED
            connect_error = ""
            try:
                await transport.start()
            except Exception as exc:
                connect_status = ServerStatus.ERROR
                connect_error = str(exc)
                logger.warning("[mcp_hub] Connect failed for '%s': %s", config.name, exc)

            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO mcp_servers (id, name, transport, status, config, error, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (server_id, config.name, config.transport.value,
                     connect_status.value, config.model_dump_json(),
                     connect_error, now, now),
                )

            if connect_status == ServerStatus.CONNECTED:
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
                from maop.core.mcp.mcp_cache import MCPCacheKey

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
                    from maop.core.mcp.mcp_cache import MCPCacheKey

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



    # ── δ-4: OTel + metrics helpers ──────────────────────────────







    # ── δ-5: cache / concurrency / rate-limit helpers ──────────











    # ── δ-1: Name-based compat shims (Stack A/B unification) ──────
    # These methods support callers (dashboard routers, function_call.py,
    # tool_schema.py) that previously used the removed MCPRegistry (Stack B).
    # They translate name-based operations to MCPHub's id-based core API
    # (connect/disconnect/call_tool which are server-id based).







