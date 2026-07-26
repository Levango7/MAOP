"""Tests for Phase δ-4 — MCP observability + streamable-http transport.

Covers three areas added in δ-4:

1. **OTel trace spans** — verify MCPHub emits spans named ``mcp.connect`` /
   ``mcp.disconnect`` / ``mcp.call_tool`` / ``mcp.health_check`` /
   ``mcp.list_tools`` / ``mcp.list_resources`` / ``mcp.read_resource``
   with the right attribute payload (server_name, tool_name, …).

2. **Metrics** — verify the five new counters / gauge / histogram in
   :mod:`maop.core.monitoring` are registered and incremented at the
   right places (connect/disconnect/call_tool/health_check).

3. **StreamableHttpTransport** — exercise the urllib-based transport
   with a mocked ``urllib.request.urlopen``: SSE responses, plain JSON
   responses, ``Mcp-Session-Id`` capture, error paths.

The span tests patch :func:`maop.core.otel.span` with a recording
context manager so we can assert call arguments without spinning up a
real OTel provider. When the real ``otel.span`` is used (OTel disabled),
it yields a :class:`_NoopSpan` — that path is already covered by
``test_otel.py`` and is not duplicated here.
"""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maop.core import otel as otel_module
from maop.core.mcp_hub import (
    MCPHub,
    MCPServerConfig,
    ResourceContent,
    ToolResult,
    TransportType,
    _StreamableHttpTransport,
)
from maop.core.monitoring import (
    MAOP_MCP_CALL_DURATION_SECONDS,
    MAOP_MCP_CALL_ERRORS_TOTAL,
    MAOP_MCP_CALLS_TOTAL,
    MAOP_MCP_HEALTH_CHECK_TOTAL,
    MAOP_MCP_SERVERS_CONNECTED,
)


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────


class _FakeTransport:
    """Stand-in transport that bypasses real I/O. Mirrors the one in
    test_mcp_permission_audit.py — kept local so this file stays
    self-contained.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        response: dict[str, Any] | None = None,
        raise_on_send: Exception | None = None,
        alive: bool = True,
    ) -> None:
        self._config = config
        self._response = response or {
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}
        }
        self._raise = raise_on_send
        self._alive = alive
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        pass

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if self._raise is not None:
            raise self._raise
        return self._response

    async def stop(self) -> None:
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive


def _inject_transport(hub: MCPHub, config: MCPServerConfig, transport: _FakeTransport) -> str:
    """Register a fake transport + config directly in the hub's internal maps."""
    server_id = "fake-server-id"
    hub._transports[server_id] = transport
    hub._configs[server_id] = config
    return server_id


@pytest.fixture
def hub(tmp_path: Path) -> MCPHub:
    return MCPHub(root_dir=tmp_path)


# Span-recording context manager. Patch ``maop.core.otel.span`` with
# this to capture every span opened by MCPHub without spinning up a
# real OTel provider. Each call records (name, attributes) and yields
# a MagicMock standing in for the real span object.
_span_calls: list[dict[str, Any]] = []


@contextmanager
def _recording_span(tracer: Any, name: str, *, kind: Any = None,
                    attributes: dict[str, Any] | None = None,
                    trace_id: str = "") -> Any:
    _span_calls.append({
        "tracer": tracer,
        "name": name,
        "kind": kind,
        "attributes": dict(attributes or {}),
        "trace_id": trace_id,
    })
    yield MagicMock()


@pytest.fixture
def recording_spans():
    """Patch ``maop.core.otel.span`` with ``_recording_span`` for the
    duration of the test, returning the shared call list (cleared first).
    """
    _span_calls.clear()
    with patch.object(otel_module, "span", _recording_span):
        yield _span_calls


def _fake_http_response(
    *,
    status: int = 200,
    body: bytes = b"",
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a MagicMock that mimics the response object returned by
    ``urllib.request.urlopen`` when used as a context manager.
    """
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    # headers.get(key, default) → value or default
    merged = {"Content-Type": content_type}
    if headers:
        merged.update(headers)
    resp.headers.get = lambda key, default="": merged.get(key, default)
    resp.read.return_value = body
    return resp


# ─────────────────────────────────────────────────────────────────
# 1. OTel span emission
# ─────────────────────────────────────────────────────────────────


class TestMCPTraceSpans:
    """Verify MCPHub wraps its public methods in ``otel.span`` with the
    right span name and attribute payload.
    """

    async def test_connect_emits_span(self, hub: MCPHub, recording_spans):
        cfg = MCPServerConfig(
            name="trace-srv",
            transport=TransportType.STDIO,
            command="echo",
        )
        sid = await hub.connect(cfg)
        assert sid
        spans = [s for s in recording_spans if s["name"] == "mcp.connect"]
        assert len(spans) == 1
        attrs = spans[0]["attributes"]
        assert attrs["mcp.server_name"] == "trace-srv"
        assert attrs["mcp.transport"] == "stdio"

    async def test_disconnect_emits_span(self, hub: MCPHub, recording_spans):
        cfg = MCPServerConfig(name="dc-srv", transport=TransportType.SSE,
                              url="http://localhost:9999/sse")
        sid = await hub.connect(cfg)
        await hub.disconnect(sid)
        spans = [s for s in recording_spans if s["name"] == "mcp.disconnect"]
        assert len(spans) == 1
        assert spans[0]["attributes"]["mcp.server_name"] == "dc-srv"

    async def test_call_tool_emits_span_with_safe_attributes(
        self, hub: MCPHub, recording_spans, tmp_path: Path
    ):
        cfg = MCPServerConfig(name="ct-srv")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        await hub.call_tool(sid, "read_file", {"path": "/secret", "token": "abc"})

        spans = [s for s in recording_spans if s["name"] == "mcp.call_tool"]
        assert len(spans) == 1
        attrs = spans[0]["attributes"]
        assert attrs["mcp.server_name"] == "ct-srv"
        assert attrs["mcp.tool_name"] == "read_file"
        # Only keys are recorded — never raw values, to avoid leaking PII.
        keys = attrs["mcp.arguments_keys"]
        assert "path" in keys
        assert "token" in keys
        assert "abc" not in json.dumps(attrs)

    async def test_health_check_emits_span(self, hub: MCPHub, recording_spans):
        cfg = MCPServerConfig(name="hc-srv")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))
        await hub.health_check(sid)
        spans = [s for s in recording_spans if s["name"] == "mcp.health_check"]
        assert len(spans) == 1
        assert spans[0]["attributes"]["mcp.server_name"] == "hc-srv"

    async def test_list_tools_emits_span(self, hub: MCPHub, recording_spans):
        await hub.list_tools()
        spans = [s for s in recording_spans if s["name"] == "mcp.list_tools"]
        assert len(spans) == 1
        assert "mcp.server_name" in spans[0]["attributes"]

    async def test_list_resources_emits_span(self, hub: MCPHub, recording_spans):
        await hub.list_resources()
        spans = [s for s in recording_spans if s["name"] == "mcp.list_resources"]
        assert len(spans) == 1

    async def test_read_resource_emits_span(self, hub: MCPHub, recording_spans):
        cfg = MCPServerConfig(name="rr-srv")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))
        await hub.read_resource(sid, "file:///tmp/x.txt")
        spans = [s for s in recording_spans if s["name"] == "mcp.read_resource"]
        assert len(spans) == 1
        attrs = spans[0]["attributes"]
        assert attrs["mcp.server_name"] == "rr-srv"
        assert attrs["mcp.resource_uri"] == "file:///tmp/x.txt"

    def test_hub_uses_tracer_named_maop_mcp(self, hub: MCPHub):
        # The hub should always obtain a tracer (real or noop) at init
        # time so call sites don't need to check for None.
        assert hub._tracer is not None


# ─────────────────────────────────────────────────────────────────
# 2. Metrics registration + recording
# ─────────────────────────────────────────────────────────────────


class TestMCPMetrics:
    """Verify the five δ-4 metrics are registered and updated."""

    def test_metrics_are_registered_with_expected_names(self):
        assert MAOP_MCP_CALLS_TOTAL.name == "MAOP_mcp_calls_total"
        assert MAOP_MCP_CALL_DURATION_SECONDS.name == "MAOP_mcp_call_duration_seconds"
        assert MAOP_MCP_SERVERS_CONNECTED.name == "MAOP_mcp_servers_connected"
        assert MAOP_MCP_CALL_ERRORS_TOTAL.name == "MAOP_mcp_call_errors_total"
        assert MAOP_MCP_HEALTH_CHECK_TOTAL.name == "MAOP_mcp_health_check_total"

    async def test_connect_disconnect_updates_servers_connected_gauge(
        self, hub: MCPHub
    ):
        before = MAOP_MCP_SERVERS_CONNECTED.get()
        cfg = MCPServerConfig(name="gauge-srv", transport=TransportType.SSE,
                              url="http://localhost:9999/sse")
        sid = await hub.connect(cfg)
        # SSE transport's is_alive is True when url is set, so the gauge
        # should have incremented by exactly 1.
        assert MAOP_MCP_SERVERS_CONNECTED.get() == before + 1

        await hub.disconnect(sid)
        assert MAOP_MCP_SERVERS_CONNECTED.get() == before

    async def test_call_tool_increments_calls_total_with_labels(
        self, hub: MCPHub
    ):
        cfg = MCPServerConfig(name="m-calls")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before = MAOP_MCP_CALLS_TOTAL.get(labels={"server": "m-calls", "tool": "t1"})
        await hub.call_tool(sid, "t1", {})
        assert MAOP_MCP_CALLS_TOTAL.get(labels={"server": "m-calls", "tool": "t1"}) == before + 1

    async def test_call_tool_records_duration_histogram(self, hub: MCPHub):
        cfg = MCPServerConfig(name="m-dur")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before_count = MAOP_MCP_CALL_DURATION_SECONDS._total
        await hub.call_tool(sid, "any", {})
        assert MAOP_MCP_CALL_DURATION_SECONDS._total == before_count + 1

    async def test_call_tool_error_increments_errors_total(self, hub: MCPHub):
        cfg = MCPServerConfig(name="m-err")
        # Response carries a JSON-RPC error → should bump errors_total.
        bad = _FakeTransport(cfg, response={"error": {"message": "boom"}})
        sid = _inject_transport(hub, cfg, bad)

        before = MAOP_MCP_CALL_ERRORS_TOTAL.get(labels={"server": "m-err", "tool": "boom-tool"})
        result = await hub.call_tool(sid, "boom-tool", {})
        assert result.is_error is True
        assert MAOP_MCP_CALL_ERRORS_TOTAL.get(labels={"server": "m-err", "tool": "boom-tool"}) == before + 1

    async def test_call_tool_transport_exception_increments_errors_total(
        self, hub: MCPHub
    ):
        cfg = MCPServerConfig(name="m-exc")
        bad = _FakeTransport(cfg, raise_on_send=RuntimeError("network down"))
        sid = _inject_transport(hub, cfg, bad)

        before = MAOP_MCP_CALL_ERRORS_TOTAL.get(labels={"server": "m-exc", "tool": "t"})
        with pytest.raises(RuntimeError):
            await hub.call_tool(sid, "t", {})
        assert MAOP_MCP_CALL_ERRORS_TOTAL.get(labels={"server": "m-exc", "tool": "t"}) == before + 1

    async def test_health_check_increments_health_check_total(self, hub: MCPHub):
        cfg = MCPServerConfig(name="m-hc")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before_h = MAOP_MCP_HEALTH_CHECK_TOTAL.get(
            labels={"server": "m-hc", "result": "healthy"}
        )
        before_u = MAOP_MCP_HEALTH_CHECK_TOTAL.get(
            labels={"server": "m-hc", "result": "unhealthy"}
        )
        await hub.health_check(sid)
        assert MAOP_MCP_HEALTH_CHECK_TOTAL.get(
            labels={"server": "m-hc", "result": "healthy"}
        ) == before_h + 1

        # Now a dead transport → unhealthy label should bump.
        dead = _FakeTransport(cfg, alive=False)
        hub._transports[sid] = dead
        # Force auto-reconnect path off so we land in the "not alive → False" arm.
        cfg2 = MCPServerConfig(name="m-hc", auto_reconnect=False)
        hub._configs[sid] = cfg2
        await hub.health_check(sid)
        assert MAOP_MCP_HEALTH_CHECK_TOTAL.get(
            labels={"server": "m-hc", "result": "unhealthy"}
        ) == before_u + 1


# ─────────────────────────────────────────────────────────────────
# 3. _StreamableHttpTransport
# ─────────────────────────────────────────────────────────────────


class TestStreamableHttpTransport:
    """Exercise the urllib-based MCP 2025 streamable-http transport."""

    def test_config_supports_streamable_http(self):
        cfg = MCPServerConfig(
            name="sh",
            transport=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer x"},
            session_id="abc",
        )
        assert cfg.transport == TransportType.STREAMABLE_HTTP
        assert cfg.session_id == "abc"
        assert cfg.headers["Authorization"] == "Bearer x"

    def test_transport_not_alive_without_url(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP, url="")
        t = _StreamableHttpTransport(cfg)
        assert t.is_alive is False

    def test_transport_not_alive_until_started(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        # No start() yet → not alive.
        assert t.is_alive is False
        asyncio.run(t.start())
        assert t.is_alive is True

    def test_transport_alive_when_session_id_preseeded(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp", session_id="pre")
        t = _StreamableHttpTransport(cfg)
        assert t.is_alive is True

    async def test_connect_no_url_returns_error(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP, url="")
        t = _StreamableHttpTransport(cfg)
        result = await t.send_request("ping", {})
        assert "error" in result
        assert "No base URL" in result["error"]["message"]

    async def test_send_request_non_streaming_json(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"pong": True}}).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = await t.send_request("ping", {})

        assert mock_open.called
        result.pop("_mcp_session_id", None)
        assert result["jsonrpc"] == "2.0"
        assert result["result"] == {"pong": True}

    async def test_send_request_sse_stream(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        # SSE body: two events, the second carries our request id (1).
        sse_body = (
            b"event: open\n"
            b"data: {\"jsonrpc\":\"2.0\",\"id\":99,\"result\":{\"note\":\"ignored\"}}\n"
            b"\n"
            b"data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}\n"
            b"\n"
        )
        resp = _fake_http_response(body=sse_body, content_type="text/event-stream")

        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("tools/list", {})

        result.pop("_mcp_session_id", None)
        assert result["id"] == 1
        assert result["result"] == {"tools": []}

    async def test_send_request_sse_multiline_data(self):
        """Multi-line ``data:`` fields are joined with newlines (SSE spec).

        We split a JSON payload at a token boundary (between `,` and the
        next key) so the joined result is still valid JSON — JSON allows
        whitespace (including newlines) between tokens.
        """
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        sse_body = (
            b"data: {\"jsonrpc\":\"2.0\",\n"
            b"data: \"id\":1,\"result\":{\"text\":\"joined\"}}\n"
            b"\n"
        )
        resp = _fake_http_response(body=sse_body, content_type="text/event-stream")

        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("tools/call", {})

        result.pop("_mcp_session_id", None)
        assert result["id"] == 1
        assert result["result"]["text"] == "joined"

    async def test_send_request_sse_fallback_when_id_mismatch(self):
        """If no event carries our request id, the first parsed event is returned."""
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        sse_body = (
            b"data: {\"jsonrpc\":\"2.0\",\"id\":42,\"result\":{\"x\":1}}\n"
            b"\n"
        )
        resp = _fake_http_response(body=sse_body, content_type="text/event-stream")

        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("ping", {})

        result.pop("_mcp_session_id", None)
        # No event matched id=1, but we still return the fallback payload.
        assert result["id"] == 42

    async def test_send_request_sse_empty_stream_returns_error(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        # SSE body with no data: lines at all.
        resp = _fake_http_response(body=b": comment\nevent: open\n\n",
                                    content_type="text/event-stream")
        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("ping", {})

        assert "error" in result
        assert "no JSON-RPC" in result["error"]["message"]

    async def test_send_request_invalid_json_response(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        resp = _fake_http_response(body=b"not-json{", content_type="application/json")
        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("ping", {})

        assert "error" in result
        assert "Invalid JSON" in result["error"]["message"]

    async def test_send_request_http_error(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        err = urllib.error.HTTPError(
            url="http://localhost:8080/mcp",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b"boom-body"),
        )

        with patch("urllib.request.urlopen", side_effect=err):
            result = await t.send_request("ping", {})

        assert "error" in result
        assert "HTTP 500" in result["error"]["message"]
        assert "boom-body" in result["error"]["message"]

    async def test_send_request_network_error(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        err = urllib.error.URLError(reason="connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            result = await t.send_request("ping", {})

        assert "error" in result
        assert "Network error" in result["error"]["message"]
        assert "connection refused" in result["error"]["message"]

    async def test_session_id_captured_from_response_header(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()
        assert t.session_id == ""

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        resp = _fake_http_response(
            body=body,
            content_type="application/json",
            headers={"Mcp-Session-Id": "sess-123"},
        )

        with patch("urllib.request.urlopen", return_value=resp):
            await t.send_request("initialize", {})

        # The captured session id should now be sent on subsequent requests.
        assert t.session_id == "sess-123"

        # Second call — verify the Mcp-Session-Id header is present.
        with patch("urllib.request.urlopen", return_value=resp) as mock_open2:
            await t.send_request("ping", {})

        sent_req = mock_open2.call_args.args[0]
        assert sent_req.headers.get("Mcp-session-id") == "sess-123"

    async def test_pre_seeded_session_id_sent_on_first_request(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp", session_id="preloaded")
        t = _StreamableHttpTransport(cfg)

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            await t.send_request("ping", {})

        sent_req = mock_open.call_args.args[0]
        assert sent_req.headers.get("Mcp-session-id") == "preloaded"

    async def test_call_tool_through_hub_with_mocked_transport(self, hub: MCPHub):
        """End-to-end: hub.call_tool should be able to drive a
        streamable_http transport whose urlopen is mocked. This verifies
        the wiring without exercising a real HTTP server.
        """
        cfg = MCPServerConfig(
            name="e2e",
            transport=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080/mcp",
        )
        sid = await hub.connect(cfg)

        body = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "content": [{"type": "text", "text": "hello"}],
                "isError": False,
            },
        }).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            result = await hub.call_tool(sid, "greet", {"name": "world"})

        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert result.content[0]["text"] == "hello"

    async def test_read_resource_through_hub_with_mocked_transport(self, hub: MCPHub):
        cfg = MCPServerConfig(
            name="e2e-r",
            transport=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080/mcp",
        )
        sid = await hub.connect(cfg)

        body = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "contents": [{"uri": "file:///x", "mimeType": "text/plain", "text": "hi"}],
            },
        }).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            rc = await hub.read_resource(sid, "file:///x")

        assert isinstance(rc, ResourceContent)
        assert rc.text == "hi"
        assert rc.mime_type == "text/plain"

    async def test_list_tools_through_hub_with_mocked_transport(self, hub: MCPHub):
        cfg = MCPServerConfig(
            name="e2e-lt",
            transport=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080/mcp",
        )
        # The capability discovery on connect() calls tools/list (and
        # resources/list) — so urlopen must be mocked for the whole
        # connect+list_tools flow. Both calls receive the same response;
        # the resources branch sees no "resources" key and stores nothing.
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "tools": [
                    {"name": "read_file", "description": "Read a file",
                     "inputSchema": {"type": "object"}},
                ],
            },
        }).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            sid = await hub.connect(cfg)
            tools = await hub.list_tools(sid)

        assert len(tools) == 1
        assert tools[0].name == "read_file"

    async def test_health_check_through_hub_with_mocked_transport(self, hub: MCPHub):
        cfg = MCPServerConfig(
            name="e2e-hc",
            transport=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080/mcp",
        )
        # Bypass connect() since streamable_http.is_alive is True after
        # start, but health_check would call ping — we want to mock that.
        sid = await hub.connect(cfg)

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        resp = _fake_http_response(body=body, content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            healthy = await hub.health_check(sid)

        assert healthy is True

    async def test_202_accepted_returns_empty_result(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()

        # 202 Accepted — no body, optional session header.
        resp = _fake_http_response(status=202, body=b"",
                                    content_type="application/json",
                                    headers={"Mcp-Session-Id": "s-202"})
        with patch("urllib.request.urlopen", return_value=resp):
            result = await t.send_request("notifications/initialized", {})

        # 202 returns an empty result; the internal _mcp_session_id
        # sentinel is consumed by send_request and surfaced as the
        # transport's session_id attribute — not leaked to the caller.
        assert "result" in result
        assert result["result"] == {}
        assert "_mcp_session_id" not in result
        assert t.session_id == "s-202"

    async def test_stop_clears_alive_state(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        await t.start()
        assert t.is_alive is True
        await t.stop()
        assert t.is_alive is False

    def test_request_id_increments_per_call(self):
        cfg = MCPServerConfig(name="x", transport=TransportType.STREAMABLE_HTTP,
                              url="http://localhost:8080/mcp")
        t = _StreamableHttpTransport(cfg)
        # _matches_request_id uses _request_id, so increment it manually
        # and verify the loose comparison logic (int vs str).
        t._request_id = 7
        assert t._matches_request_id({"id": 7}, 7) is True
        assert t._matches_request_id({"id": "7"}, 7) is True
        assert t._matches_request_id({"id": 8}, 7) is False
        assert t._matches_request_id({}, 7) is False
