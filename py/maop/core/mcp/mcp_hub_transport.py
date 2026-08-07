"""MCP Hub transport implementations.

Extracted from mcp_hub.py. Each transport class handles a specific
MCP transport protocol (stdio, SSE, WebSocket, streamable_http).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

from maop.core.mcp.mcp_hub_types import (
    MCPServerConfig,
)

logger = logging.getLogger(__name__)


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

        # C3 fix: a bare readline() returned whatever line came next on stdout,
        # which may be a server notification or a response to a DIFFERENT
        # request. Read lines until we find the response whose "id" matches
        # this request, skipping notifications (no "id") and stale responses,
        # within an overall 30s deadline.
        deadline = asyncio.get_event_loop().time() + 30
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return {"error": {"message": f"Timeout waiting for response to request id={self._request_id}"}}

            line = await asyncio.wait_for(self._process.stdout.readline(), timeout=remaining)
            if not line:
                return {"error": {"message": "Empty response (EOF from server)"}}

            try:
                message = cast(dict[str, Any], json.loads(line.decode()))
            except json.JSONDecodeError as exc:
                logger.debug("[mcp_hub] Skipping non-JSON stdout line: %s", exc)
                continue

            msg_id = message.get("id")
            if msg_id is None:
                # JSON-RPC notification (e.g. progress/log) — not our response.
                logger.debug("[mcp_hub] Skipping notification: %s", message.get("method", "?"))
                continue
            if msg_id != self._request_id:
                # Response to an earlier/other request — skip.
                logger.debug("[mcp_hub] Skipping stale response id=%s (want %s)", msg_id, self._request_id)
                continue
            return message

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
        # High fix: do NOT swallow exceptions here. Previously start()
        # caught everything, so MCPHub.connect() interpreted "no exception"
        # as success, marked the server CONNECTED, and every subsequent
        # call_tool failed with "WebSocket not connected". Callers
        # (connect()/health_check) already handle exceptions properly.
        try:
            import websockets
        except ImportError as exc:
            logger.warning(
                "[mcp_hub] websockets package not installed, "
                "WebSocket transport unavailable"
            )
            raise RuntimeError(
                "websockets package not installed — "
                "install 'websockets' to use the WebSocket transport"
            ) from exc

        try:
            self._ws = await websockets.connect(
                self._config.url,
                additional_headers=self._config.headers,
            )
        except Exception as exc:
            logger.warning("[mcp_hub] WebSocket connect failed: %s", exc)
            raise

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

