"""MCPHub — 查询/健康检查 ops mixin。

T2 架构债治理：从 ``mcp_hub.py`` 拆分。公开 API 不变。
依赖宿主的 ``_servers`` / ``_db_path`` 状态与 metrics mixin。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maop.core import otel
from maop.core.mcp.mcp_hub_types import (
    MCPResource,
    MCPTool,
    ResourceContent,
    ServerInfo,
    ServerStatus,
    TransportType,
)

logger = logging.getLogger(__name__)


class MCPHubOpsMixin:
    """列表/资源/健康检查 ops。"""

    if TYPE_CHECKING:
        # 宿主类（MCPHub）提供的属性与方法 —— 仅用于类型检查
        _configs: dict[str, Any]
        _tracer: Any
        _transports: dict[str, Any]
        _connect: Callable[..., Any]
        _record_health_check: Callable[..., None]


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

    async def health_check_all(
        self, *, timeout_s: float | None = None
    ) -> dict[str, bool]:
        """Check health of all connected servers in parallel.

        P1-§4.3: each server's health check is independent — it only
        touches that server's own transport and DB rows, so the checks
        have no cross-server dependency and can run concurrently via
        ``asyncio.gather``. ``gather`` preserves input order, so
        ``zip(server_ids, outcomes)`` keeps the key↔value mapping
        identical to the previous serial loop.

        F2-03 enhancement — robust parallel health checking:

          - ``return_exceptions=True``: a single server raising (e.g. a
            transport bug) no longer cancels the whole batch; the
            exception is recorded as ``unhealthy`` for that server and
            the remaining checks still complete.
          - ``timeout_s``: optional per-server wall-clock timeout.  When
            set, each check is wrapped in ``asyncio.wait_for`` so a
            hung server cannot stall the entire sweep.  A timeout is
            treated as unhealthy (not an error) and logged at warning
            level so operators can spot the slow server.

        Parameters
        ----------
        timeout_s : float | None
            Per-server timeout in seconds.  ``None`` (default) preserves
            the original unbounded behaviour for backward compatibility.
        """
        server_ids = list(self._transports.keys())
        if not server_ids:
            return {}

        async def _check(sid: str) -> bool:
            if timeout_s is not None:
                try:
                    return await asyncio.wait_for(self.health_check(sid), timeout=timeout_s)
                except TimeoutError:
                    logger.warning(
                        "[mcp_hub] Health check timed out for '%s' after %.1fs",
                        sid, timeout_s,
                    )
                    config = self._configs.get(sid)
                    server_name = config.name if config is not None else sid
                    self._record_health_check(server_name, healthy=False)
                    return False
            return await self.health_check(sid)

        outcomes = await asyncio.gather(
            *[_check(sid) for sid in server_ids],
            return_exceptions=True,
        )
        result: dict[str, bool] = {}
        for sid, outcome in zip(server_ids, outcomes):
            if isinstance(outcome, Exception):
                logger.warning(
                    "[mcp_hub] Health check raised for '%s': %s", sid, outcome,
                )
                config = self._configs.get(sid)
                server_name = config.name if config is not None else sid
                self._record_health_check(server_name, healthy=False)
                result[sid] = False
            else:
                result[sid] = bool(outcome)
        return result

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

