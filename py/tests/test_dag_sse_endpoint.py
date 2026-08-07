"""Integration tests for the DAG progress SSE endpoint (v4.5.0 T14).

Tests the /api/stream/dag/{execution_id} SSE endpoint:
  - SSE pushes node-status events
  - Last-Event-ID resumption (replay missed events)
  - execution-complete closes connection
  - Event format (id/event/data lines)
  - Existing endpoints unchanged

Note: SSE endpoints produce infinite streams. All tests pre-publish an
``execution-complete`` event so the SSE generator terminates and the
TestClient stream closes cleanly.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.dag.dag_progress_emitter import DagProgressEmitter
from maop.core.reliability.event_bus import EventBus


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the stream router attached."""
    app = FastAPI()
    from maop.dashboard.routers.stream import router as stream_router
    app.include_router(stream_router)
    return app


def _disable_auth(monkeypatch):
    """Disable auth for testing by patching require_admin and _check_sse_token.

    Patches the stream module's bound references directly (not the origin
    module) because ``stream.py`` uses ``from ... import require_admin``
    which binds the function object at import time. Patching the origin
    module only works if stream hasn't been imported yet — in full-test
    runs a prior test already imports stream, so the bound reference
    would bypass the patch. Patching ``stream_mod`` directly is robust
    regardless of import order.
    """
    import maop.dashboard.routers.stream as stream_mod
    monkeypatch.setattr(stream_mod, "require_admin", lambda request: None)
    monkeypatch.setattr(stream_mod, "_check_sse_token", lambda request: None)


def _patch_event_bus(monkeypatch, bus: EventBus):
    """Patch get_event_bus to return our test bus."""
    import maop.core.reliability.event_bus as eb_mod
    monkeypatch.setattr(eb_mod, "get_event_bus", lambda: bus)


def _flush_events():
    """Allow fire-and-forget publish tasks to complete."""
    async def _flush():
        await asyncio.sleep(0.15)
    asyncio.run(_flush())


class TestDagSseEndpoint:
    """Tests for /api/stream/dag/{execution_id} SSE endpoint."""

    def test_endpoint_returns_event_stream(self, monkeypatch):
        """SSE endpoint returns text/event-stream content type."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        # Pre-publish a complete event so the stream terminates.
        emitter = DagProgressEmitter(test_bus, execution_id="test-exec")
        emitter.emit_execution_complete()
        _flush_events()

        with client.stream("GET", "/api/stream/dag/test-exec") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
            # Consume to let the stream close cleanly.
            for line in response.iter_lines():
                if line.startswith("event: execution-complete"):
                    break

    def test_sse_pushes_node_status_events(self, monkeypatch):
        """SSE pushes node-status events when emitter fires."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        emitter = DagProgressEmitter(test_bus, execution_id="sse-test-1")
        emitter.emit_pending("n1")
        emitter.emit_running("n1", assigned_agent="claude")
        emitter.emit_success("n1", duration_ms=100)
        emitter.emit_execution_complete()
        _flush_events()

        events_received = []
        with client.stream("GET", "/api/stream/dag/sse-test-1") as response:
            for line in response.iter_lines():
                if line.startswith("event: node-status"):
                    events_received.append("node-status")
                elif line.startswith("event: execution-complete"):
                    events_received.append("execution-complete")
                    break
                if len(events_received) > 10:
                    break

        assert "node-status" in events_received
        assert "execution-complete" in events_received

    def test_last_event_id_resumption(self, monkeypatch):
        """Last-Event-ID header triggers replay of seq > last_event_id only."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        emitter = DagProgressEmitter(test_bus, execution_id="sse-test-2")
        emitter.emit_pending("n1")     # seq 1
        emitter.emit_running("n1")     # seq 2
        emitter.emit_success("n1")     # seq 3
        emitter.emit_execution_complete()  # seq 4
        _flush_events()

        # Connect with Last-Event-ID: 1 → should replay seq 2, 3 only.
        replayed_seqs = []
        with client.stream(
            "GET",
            "/api/stream/dag/sse-test-2",
            headers={"Last-Event-ID": "1"},
        ) as response:
            for line in response.iter_lines():
                if line.startswith("id: "):
                    seq = int(line[4:])
                    replayed_seqs.append(seq)
                elif line.startswith("event: execution-complete"):
                    break
                if len(replayed_seqs) > 5:
                    break

        assert 2 in replayed_seqs
        assert 3 in replayed_seqs
        assert 1 not in replayed_seqs

    def test_execution_complete_closes_connection(self, monkeypatch):
        """execution-complete event is pushed and connection closes."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        emitter = DagProgressEmitter(test_bus, execution_id="sse-test-3")
        emitter.emit_pending("n1")
        emitter.emit_success("n1")
        emitter.emit_execution_complete()
        _flush_events()

        got_complete = False
        with client.stream("GET", "/api/stream/dag/sse-test-3") as response:
            for line in response.iter_lines():
                if line.startswith("event: execution-complete"):
                    got_complete = True
                    break

        assert got_complete, "Should receive execution-complete event"

    def test_event_format_is_correct(self, monkeypatch):
        """SSE event format: `id: {seq}\\nevent: node-status\\ndata: {json}\\n\\n`."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        emitter = DagProgressEmitter(test_bus, execution_id="sse-test-4")
        emitter.emit_pending("n1")
        emitter.emit_execution_complete()
        _flush_events()

        lines_collected = []
        with client.stream("GET", "/api/stream/dag/sse-test-4") as response:
            for line in response.iter_lines():
                lines_collected.append(line)
                if line.startswith("event: execution-complete"):
                    break
                if len(lines_collected) > 10:
                    break

        has_id = any(line.startswith("id: ") for line in lines_collected)
        has_event = any(line.startswith("event: node-status") for line in lines_collected)
        has_data = any(line.startswith("data: ") for line in lines_collected)
        assert has_id, f"Missing id: line in {lines_collected}"
        assert has_event, f"Missing event: line in {lines_collected}"
        assert has_data, f"Missing data: line in {lines_collected}"

    def test_data_payload_has_4_core_fields(self, monkeypatch):
        """SSE data payload contains node_id, status, timestamp, metadata (spec 5.2.1 rule 2)."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        test_bus = EventBus()
        _patch_event_bus(monkeypatch, test_bus)

        emitter = DagProgressEmitter(test_bus, execution_id="sse-test-5")
        emitter.emit_running("n1", assigned_agent="claude")
        emitter.emit_execution_complete()
        _flush_events()

        data_payload = None
        with client.stream("GET", "/api/stream/dag/sse-test-5") as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_payload = json.loads(line[6:])
                    break
                if line.startswith("event: execution-complete"):
                    break

        assert data_payload is not None
        assert "node_id" in data_payload
        assert "status" in data_payload
        assert "timestamp" in data_payload
        assert "metadata" in data_payload
        assert data_payload["node_id"] == "n1"
        assert data_payload["status"] == "running"

    def test_existing_stream_endpoints_unchanged(self, monkeypatch):
        """Existing /api/stream/active endpoint still works (not SSE)."""
        _disable_auth(monkeypatch)
        app = _make_test_app()
        client = TestClient(app)

        response = client.get("/api/stream/active")
        assert response.status_code == 200
        data = response.json()
        assert "active" in data
        assert "count" in data
