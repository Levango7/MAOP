"""Tests for /api/stream/agent/{execution_id} SSE endpoint (v5.0.0).

Tests token-level streaming for agent execution via SSE.

P1-13: verifies the full token-flow pipeline:
  1. ``_emit_agent_event`` publishes events to the EventBus
  2. ``_make_token_line_callback`` bridges subprocess stdout to tokens
  3. ``maop_execute`` emits meta/token/done events during execution
  4. ``chat_engine.chat_stream`` emits tokens when execution_id is set
  5. The SSE endpoint replays history + live events to the client
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


async def _drain_pending_publishes() -> None:
    """Wait for all pending fire-and-forget EventBus publish tasks to complete.

    ``publish_sync`` uses ``asyncio.ensure_future`` (fire-and-forget) when
    an event loop is running, so history is not updated synchronously.
    This helper drains the pending tasks so tests can assert on history.
    """
    from maop.core.reliability.event_bus import get_event_bus
    bus = get_event_bus()
    pending = [t for t in bus._pending_publish_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Yield once more to let done-callbacks (discard from set) run.
    await asyncio.sleep(0)


@pytest.fixture
def client(monkeypatch):
    """Create a test client with auth disabled."""
    monkeypatch.setenv("MAOP_AUTH", "0")
    monkeypatch.setenv("MAOP_AUTH_ENABLED", "0")
    monkeypatch.setenv("MAOP_ENV", "test")
    # Patch require_admin to no-op for testing — must patch where it is used
    # (the stream router imports require_admin directly into its namespace).
    import maop.dashboard.routers.stream as stream_mod
    monkeypatch.setattr(stream_mod, "require_admin", lambda req: None)
    from maop.dashboard.server import app
    return TestClient(app)


@pytest.fixture
def clean_event_bus():
    """Provide a fresh global EventBus and restore it after the test."""
    from maop.core.reliability import event_bus as eb_module
    old_bus = eb_module._global_bus
    eb_module._global_bus = None
    bus = eb_module.get_event_bus()
    yield bus
    bus.clear()
    eb_module._global_bus = old_bus


# ── Unit tests: event emission helpers ──────────────────────────────


class TestAgentEventEmission:
    """Test _emit_agent_event and _make_token_line_callback helpers."""

    def test_emit_agent_event_publishes_to_bus(self, clean_event_bus):
        """_emit_agent_event should publish an event on the correct topic."""
        from maop.maop_execute import _emit_agent_event

        bus = clean_event_bus
        _emit_agent_event("exec-123", "token", {"content": "hello", "type": "token"})

        history = bus.get_history(limit=10)
        assert len(history) == 1
        evt = history[0]
        assert evt.topic == "agent.exec-123.token"
        assert evt.data == {"content": "hello", "type": "token"}

    def test_emit_agent_event_multiple_types(self, clean_event_bus):
        """_emit_agent_event should support meta, token, done, error types."""
        from maop.maop_execute import _emit_agent_event

        bus = clean_event_bus
        _emit_agent_event("e1", "meta", {"agent": "mavis", "type": "meta"})
        _emit_agent_event("e1", "token", {"content": "Hi", "type": "token"})
        _emit_agent_event("e1", "done", {"content_length": 2, "tokens": 0})

        history = bus.get_history(limit=10)
        topics = [e.topic for e in history]
        assert "agent.e1.meta" in topics
        assert "agent.e1.token" in topics
        assert "agent.e1.done" in topics

    def test_emit_agent_event_never_raises(self, clean_event_bus):
        """_emit_agent_event should swallow exceptions (fire-and-forget)."""
        from maop.maop_execute import _emit_agent_event

        # Even with a broken bus, this should not raise.
        # Passing an empty execution_id still works (just odd topic).
        _emit_agent_event("", "token", {"content": "x"})
        # No assertion needed — just verifying no exception.

    def test_token_line_callback_emits_tokens(self, clean_event_bus):
        """_make_token_line_callback should emit a token event per line."""
        from maop.maop_execute import _make_token_line_callback

        bus = clean_event_bus
        callback = _make_token_line_callback("trace-abc")
        callback("line one")
        callback("line two")

        history = bus.get_history(limit=10)
        token_events = [e for e in history if e.topic == "agent.trace-abc.token"]
        assert len(token_events) == 2
        assert token_events[0].data["content"] == "line one"
        assert token_events[1].data["content"] == "line two"

    def test_token_line_callback_ignores_empty(self, clean_event_bus):
        """_make_token_line_callback should skip empty lines."""
        from maop.maop_execute import _make_token_line_callback

        bus = clean_event_bus
        callback = _make_token_line_callback("trace-x")
        callback("")
        callback("real content")

        history = bus.get_history(limit=10)
        token_events = [e for e in history if e.topic == "agent.trace-x.token"]
        assert len(token_events) == 1


# ── Integration tests: maop_execute event emission ──────────────────


class TestMaopExecuteEventEmission:
    """Test that maop_execute emits meta/done/error events during execution."""

    @pytest.mark.asyncio
    async def test_maop_execute_emits_meta_and_done(self, clean_event_bus, monkeypatch):
        """maop_execute should emit meta + done events on successful dispatch."""
        from maop.core.reliability.error_schema import new_result
        from maop.maop_execute import maop_execute

        bus = clean_event_bus

        # Mock dispatcher to return a successful result immediately.
        mock_result = new_result(
            agent="test-agent", task="test",
            exit_code=0, stdout="Hello world",
        )
        mock_dispatch_result = MagicMock()
        mock_dispatch_result.result = mock_result
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=mock_dispatch_result)

        # Mock guardrail to pass.
        mock_guardrail = MagicMock()
        mock_guardrail.check = MagicMock(return_value=MagicMock(passed=True))

        # Mock permission manager to allow.
        monkeypatch.setattr(
            "maop.core.security.permission.PermissionManager",
            lambda **kw: MagicMock(check=MagicMock(
                return_value=MagicMock(decision="allow"),
            )),
        )
        # Mock hook manager to no-op.
        async def _noop_trigger(*a, **kw):
            return []
        monkeypatch.setattr(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager",
            lambda: MagicMock(trigger=_noop_trigger),
        )

        await maop_execute(
            agent="test-agent", task="test",
            trace_id="exec-flow-1",
            dispatcher=mock_dispatcher,
            guardrail=mock_guardrail,
        )

        await _drain_pending_publishes()
        history = bus.get_history(limit=20)
        topics = [e.topic for e in history]
        assert "agent.exec-flow-1.meta" in topics, f"meta event missing: {topics}"
        assert "agent.exec-flow-1.done" in topics, f"done event missing: {topics}"

    @pytest.mark.asyncio
    async def test_maop_execute_emits_error_on_dispatch_failure(self, clean_event_bus, monkeypatch):
        """maop_execute should emit an error event when dispatch raises."""
        from maop.maop_execute import maop_execute

        bus = clean_event_bus

        # Mock dispatcher to raise an exception.
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("boom"))


        mock_guardrail = MagicMock()
        mock_guardrail.check = MagicMock(return_value=MagicMock(passed=True))

        monkeypatch.setattr(
            "maop.core.security.permission.PermissionManager",
            lambda **kw: MagicMock(check=MagicMock(
                return_value=MagicMock(decision="allow"),
            )),
        )
        async def _noop_trigger(*a, **kw):
            return []
        monkeypatch.setattr(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager",
            lambda: MagicMock(trigger=_noop_trigger),
        )

        await maop_execute(
            agent="test-agent", task="test",
            trace_id="exec-err-1",
            dispatcher=mock_dispatcher,
            guardrail=mock_guardrail,
        )

        await _drain_pending_publishes()
        history = bus.get_history(limit=20)
        topics = [e.topic for e in history]
        assert "agent.exec-err-1.error" in topics, f"error event missing: {topics}"


# ── SSE endpoint end-to-end tests ───────────────────────────────────


class TestAgentTokenStreamEndpoint:
    """Test /api/stream/agent/{execution_id} SSE endpoint."""

    def test_endpoint_route_registered(self, client):
        """Endpoint should be registered (not 405 Method Not Allowed)."""
        from maop.dashboard.routers.stream import router
        routes = [r.path for r in router.routes]
        assert "/agent/{execution_id}" in routes or any("/agent/" in r for r in routes)

    def test_sse_replays_history_tokens(self, client, clean_event_bus):
        """SSE endpoint should replay token events from EventBus history.

        P1-13: when a client connects after tokens were emitted, the
        endpoint replays them from history before subscribing live.
        """
        from maop.maop_execute import _emit_agent_event


        exec_id = "sse-replay-1"
        # Emit a complete token stream to history.
        _emit_agent_event(exec_id, "meta", {"agent": "mavis", "type": "meta"})
        _emit_agent_event(exec_id, "token", {"content": "Hello", "type": "token"})
        _emit_agent_event(exec_id, "token", {"content": " world", "type": "token"})
        _emit_agent_event(exec_id, "done", {"content_length": 11, "tokens": 2})

        # Ensure no streamer is registered so we take the event_bus path.
        from maop.core.reliability.streaming import get_stream_registry
        registry = get_stream_registry()
        registry.unregister(exec_id)

        response = client.get(f"/api/stream/agent/{exec_id}")
        assert response.status_code == 200
        body = response.text
        # The SSE body should contain token events with our content.
        assert "event: token" in body
        assert "Hello" in body
        assert " world" in body
        assert "event: done" in body
        assert "event: meta" in body

    def test_sse_classifies_event_types(self, client, clean_event_bus):
        """SSE endpoint should correctly map sub-topics to SSE event types."""
        from maop.maop_execute import _emit_agent_event


        exec_id = "sse-classify-1"
        _emit_agent_event(exec_id, "meta", {"agent": "a", "type": "meta"})
        _emit_agent_event(exec_id, "token", {"content": "x", "type": "token"})
        _emit_agent_event(exec_id, "error", {"error": "fail"})

        from maop.core.reliability.streaming import get_stream_registry
        registry = get_stream_registry()
        registry.unregister(exec_id)

        response = client.get(f"/api/stream/agent/{exec_id}")
        assert response.status_code == 200
        body = response.text
        assert "event: meta" in body
        assert "event: token" in body
        assert "event: error" in body

    def test_sse_empty_history_timeout(self, client, clean_event_bus):
        """SSE endpoint with no history and no live events returns timeout done.

        The event_bus path has a 60s timeout; in test we verify the
        endpoint doesn't crash on empty history. We only check that
        the response starts streaming (status 200). The timeout would
        take 60s so we don't wait for it — instead we emit a done event
        immediately so the history path terminates.
        """
        exec_id = "sse-empty-1"
        from maop.maop_execute import _emit_agent_event
        _emit_agent_event(exec_id, "done", {"content_length": 0, "tokens": 0})

        from maop.core.reliability.streaming import get_stream_registry
        registry = get_stream_registry()
        registry.unregister(exec_id)

        response = client.get(f"/api/stream/agent/{exec_id}")
        assert response.status_code == 200
        assert "event: done" in response.text


# ── ChatEngine execution_id emission tests ──────────────────────────


class TestChatEngineTokenEmission:
    """Test that ChatEngine.chat_stream emits tokens when execution_id is set."""

    @pytest.mark.asyncio
    async def test_chat_stream_emits_tokens_with_execution_id(self, clean_event_bus, monkeypatch):
        """chat_stream should emit token events to EventBus when execution_id is set."""
        from maop.core.agent.llm_chat.chat_engine import ChatEngine, ChatRequest

        bus = clean_event_bus

        # Build a ChatEngine with a mocked provider that yields tokens.
        engine = ChatEngine.__new__(ChatEngine)
        engine._root = None
        engine._memory_mgr = MagicMock()
        engine._memory_mgr.get_messages_for_llm = MagicMock(return_value=[])
        engine._memory_mgr.conversation = MagicMock()
        engine._memory_mgr.conversation.add_message = MagicMock()
        engine._memory_mgr.add_exchange = MagicMock()
        engine._default_agent = "mavis"
        engine._default_model = "test-model"
        engine._default_system_prompt = ""
        engine._provider_factory = MagicMock()

        # Mock _stream_llm to yield three tokens.
        async def _fake_stream_llm(agent, messages, request):
            for tok in ["Hel", "lo ", "world"]:
                yield tok
        monkeypatch.setattr(engine, "_stream_llm", _fake_stream_llm)

        request = ChatRequest(
            message="hi",
            execution_id="chat-exec-1",
        )

        # Consume the SSE generator.
        chunks = []
        async for chunk in engine.chat_stream(request):
            chunks.append(chunk)

        await _drain_pending_publishes()
        # Verify EventBus received meta + 3 tokens + done.
        history = bus.get_history(limit=20)
        topics = [e.topic for e in history]
        assert "agent.chat-exec-1.meta" in topics
        token_events = [e for e in history if e.topic == "agent.chat-exec-1.token"]
        assert len(token_events) == 3
        assert "agent.chat-exec-1.done" in topics

        # Verify the SSE chunks were also yielded.
        full_sse = "".join(chunks)
        assert "event: token" in full_sse
        assert "event: done" in full_sse

    @pytest.mark.asyncio
    async def test_chat_stream_no_emission_without_execution_id(self, clean_event_bus, monkeypatch):
        """chat_stream should NOT emit to EventBus when execution_id is empty."""
        from maop.core.agent.llm_chat.chat_engine import ChatEngine, ChatRequest

        bus = clean_event_bus

        engine = ChatEngine.__new__(ChatEngine)
        engine._root = None
        engine._memory_mgr = MagicMock()
        engine._memory_mgr.get_messages_for_llm = MagicMock(return_value=[])
        engine._memory_mgr.conversation = MagicMock()
        engine._memory_mgr.conversation.add_message = MagicMock()
        engine._memory_mgr.add_exchange = MagicMock()
        engine._default_agent = "mavis"
        engine._default_model = ""
        engine._default_system_prompt = ""
        engine._provider_factory = MagicMock()

        async def _fake_stream_llm(agent, messages, request):
            yield "token1"
        monkeypatch.setattr(engine, "_stream_llm", _fake_stream_llm)

        request = ChatRequest(message="hi")  # execution_id defaults to ""

        async for _ in engine.chat_stream(request):
            pass

        await _drain_pending_publishes()
        # No events should have been emitted to the bus.
        history = bus.get_history(limit=10)
        agent_events = [e for e in history if e.topic.startswith("agent.")]
        assert len(agent_events) == 0
