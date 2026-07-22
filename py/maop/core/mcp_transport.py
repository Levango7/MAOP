"""MAOP MCP Transport — stdio and SSE transport layers for MCP protocol.

Implements the JSON-RPC 2.0 transport layer used by the Model Context
Protocol (MCP).  Two transport modes are supported:

  - StdioTransport: launch a subprocess and communicate via stdin/stdout
  - SSETransport: connect to an HTTP SSE endpoint

Both transports handle:
  - JSON-RPC request/response correlation (id-based)
  - Automatic request_id generation
  - Connection lifecycle (connect/disconnect/reconnect)
  - Timeout handling
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

import asyncio
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TransportMessage(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: dict[str, Any] | None = None


class TransportResult(BaseModel):
    success: bool
    data: Any = None
    error: str = ""


class StdioTransport:
    """MCP transport over subprocess stdin/stdout (JSON-RPC 2.0).

    Usage::

        transport = StdioTransport(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
        await transport.connect()
        result = await transport.request("tools/list", {})
        await transport.disconnect()
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._connected = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.returncode is None

    async def connect(self) -> bool:
        if self.connected:
            return True
        try:
            import os
            env = None
            if self._env:
                env = {**os.environ, **self._env}
            self._proc = await asyncio.create_subprocess_exec(
                self._command, *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._connected = True
            self._reader_task = asyncio.ensure_future(self._read_loop())
            logger.info("MCP StdioTransport connected: %s %s", self._command, " ".join(self._args))
            return True
        except Exception as exc:
            logger.error("MCP StdioTransport connect failed: %s", exc)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> TransportResult:
        if not self.connected:
            return TransportResult(success=False, error="Not connected")
        self._request_id += 1
        rid = self._request_id
        msg = TransportMessage(id=rid, method=method, params=params or {})
        payload = msg.model_dump(exclude_none=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut

        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(line.encode("utf-8"))
                await self._proc.stdin.drain()
            else:
                return TransportResult(success=False, error="stdin not available")
            result = await asyncio.wait_for(fut, timeout=self._timeout)
            return cast(TransportResult, result)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return TransportResult(success=False, error=f"Timeout after {self._timeout}s")
        except Exception as exc:
            self._pending.pop(rid, None)
            return TransportResult(success=False, error=str(exc))

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.connected:
            return
        msg = TransportMessage(id=None, method=method, params=params or {})
        payload = msg.model_dump(exclude_none=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        while self._connected:
            try:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("MCP: invalid JSON from server: %s", line[:200])
                    continue
                rid = data.get("id")
                if rid is not None and rid in self._pending:
                    fut = self._pending.pop(rid)
                    if not fut.done():
                        error = data.get("error")
                        if error:
                            fut.set_result(TransportResult(
                                success=False,
                                error=error.get("message", str(error)),
                            ))
                        else:
                            fut.set_result(TransportResult(success=True, data=data.get("result")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MCP read error: %s", exc)

    async def reconnect(self) -> bool:
        await self.disconnect()
        return await self.connect()


class SSETransport:
    """MCP transport over HTTP SSE (JSON-RPC 2.0 over Server-Sent Events).

    Usage::

        transport = SSETransport(url="http://localhost:3001/sse")
        await transport.connect()
        result = await transport.request("tools/list", {})
        await transport.disconnect()
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self._url = url
        self._timeout = timeout
        self._connected = False
        self._request_id = 0
        self._session: Any = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        try:
            import httpx
            self._session = httpx.AsyncClient(timeout=self._timeout)
            self._connected = True
            logger.info("MCP SSETransport connected: %s", self._url)
            return True
        except Exception as exc:
            logger.error("MCP SSETransport connect failed: %s", exc)
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._session:
            await self._session.aclose()
            self._session = None

    async def request(self, method: str, params: dict[str, Any] | None = None) -> TransportResult:
        if not self.connected or not self._session:
            return TransportResult(success=False, error="Not connected")
        self._request_id += 1
        rid = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }
        try:
            resp = await self._session.post(self._url, json=payload)
            if 200 <= resp.status_code < 300:
                data = resp.json()
                error = data.get("error")
                if error:
                    return TransportResult(success=False, error=error.get("message", str(error)))
                return TransportResult(success=True, data=data.get("result"))
            return TransportResult(success=False, error=f"HTTP {resp.status_code}")
        except Exception as exc:
            return TransportResult(success=False, error=str(exc))

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.connected or not self._session:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            await self._session.post(self._url, json=payload)
        except Exception:
            pass

    async def reconnect(self) -> bool:
        await self.disconnect()
        return await self.connect()
