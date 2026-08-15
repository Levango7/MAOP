"""White-box regression tests for ChatEngine.

Covers three bugs found via real-agent integration testing:
  1. chat_engine.py:279 — ``getattr(result.result, 'output', None)`` →
     ``result.result.stdout`` (MaopResult has no ``output`` attribute).
  2. chat_engine.py:143 — ``content = await ... or ""`` prevents None
     from reaching Pydantic's ``ChatResponse(content: str)``.
  3. Stream / field / agent-routing regressions for ChatResponse &
     chat_stream.

All tests mock external dependencies (Dispatcher, ConfigLoader, LLM
provider) so no real agent or network is invoked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.agent.llm_chat.chat_engine import ChatEngine, ChatRequest, ChatResponse
from maop.core.reliability.error_schema import MaopResult
from maop.delegate.models import DispatchResult
from maop.memory.manager import ConsolidationTrigger, MemoryManagerConfig

# ── Helpers ──────────────────────────────────────────────────────


def _make_config() -> MemoryManagerConfig:
    """Build a MemoryManagerConfig with consolidation auto-trigger disabled.

    Disabling auto-trigger keeps tests deterministic — no background
    consolidation runs interfere with message-count assertions.
    """
    return MemoryManagerConfig(
        consolidation=ConsolidationTrigger(auto_trigger=False),
    )


def _make_dispatch_result(
    *,
    ok: bool = True,
    exit_code: int = 0,
    stdout: str = "",
    error: str | None = None,
) -> DispatchResult:
    """Build a DispatchResult wrapping a MaopResult with the given fields.

    Only the fields relevant to the stdout-vs-output regression are
    parameterised; agent/task are fixed placeholders.
    """
    return DispatchResult(
        result=MaopResult(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            error=error,
            agent="test-agent",
            task="test-task",
        ),
    )


def _patch_dispatch(dispatch_result: DispatchResult):
    """Context-manager bundle that patches ConfigLoader + Dispatcher.

    Returns a tuple of mock objects so individual tests can assert on
    call args if needed.
    """
    return (
        patch("maop.config.loader.ConfigLoader"),
        patch("maop.delegate.dispatcher.Dispatcher"),
    )


# ═══════════════════════════════════════════════════════════════════
# Bug 1: _call_llm_fallback must read .stdout (not .output)
# ═══════════════════════════════════════════════════════════════════


class TestCallLlmStdoutRegression:
    """Regression: _call_llm_fallback must read result.result.stdout.

    Before the fix the code used ``getattr(result.result, 'output', None)``
    which always returned None (MaopResult has no ``output`` field),
    causing every successful dispatch to fall through to "No response".
    """

    @pytest.mark.asyncio
    async def test_call_llm_returns_stdout_not_output(self, tmp_path):
        """Dispatch success → return stdout, not 'No response' from missing .output."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())
        dispatch_result = _make_dispatch_result(stdout="hello-from-stdout")

        with (
            patch("maop.config.loader.ConfigLoader") as MockLoader,
            patch("maop.delegate.dispatcher.Dispatcher") as MockDispatcher,
        ):
            MockLoader.return_value.load.return_value = MagicMock()
            MockDispatcher.return_value.dispatch = AsyncMock(return_value=dispatch_result)

            request = ChatRequest(message="hi", stream=False)
            result = await engine._call_llm(
                "test-agent",
                [{"role": "user", "content": "hi"}],
                request,
            )

        assert result == "hello-from-stdout"
        assert result != "No response"

    @pytest.mark.asyncio
    async def test_call_llm_none_result_returns_no_response(self, tmp_path):
        """Dispatch failure → return error string, never stdout content."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())
        dispatch_result = _make_dispatch_result(
            ok=False,
            exit_code=1,
            stdout="should-not-see-this",
            error="Dispatch failed",
        )

        with (
            patch("maop.config.loader.ConfigLoader") as MockLoader,
            patch("maop.delegate.dispatcher.Dispatcher") as MockDispatcher,
        ):
            MockLoader.return_value.load.return_value = MagicMock()
            MockDispatcher.return_value.dispatch = AsyncMock(return_value=dispatch_result)

            request = ChatRequest(message="hi", stream=False)
            result = await engine._call_llm(
                "test-agent",
                [{"role": "user", "content": "hi"}],
                request,
            )

        assert result == "Dispatch failed"
        assert "should-not-see-this" not in result


# ═══════════════════════════════════════════════════════════════════
# Bug 3: chat() must tolerate None from _call_llm
# ═══════════════════════════════════════════════════════════════════


class TestChatNoneContentRegression:
    """Regression: chat() must not crash when _call_llm returns None.

    Before the fix, ``content = await self._call_llm(...)`` could be
    None, which Pydantic rejected when building ChatResponse(content=str).
    The fix adds ``or ""`` so None becomes an empty string.
    """

    @pytest.mark.asyncio
    async def test_chat_with_none_content_doesnt_crash(self, tmp_path):
        """_call_llm → None must yield content='' not Pydantic ValidationError."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())

        with patch.object(engine, "_call_llm", new=AsyncMock(return_value=None)):
            request = ChatRequest(session_id="s-none", message="hi", stream=False)
            response = await engine.chat(request)

        assert response.content == ""
        assert isinstance(response, ChatResponse)

    @pytest.mark.asyncio
    async def test_chat_with_empty_string_content(self, tmp_path):
        """_call_llm → '' must yield content='' without error."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())

        with patch.object(engine, "_call_llm", new=AsyncMock(return_value="")):
            request = ChatRequest(session_id="s-empty", message="hi", stream=False)
            response = await engine.chat(request)

        assert response.content == ""

    @pytest.mark.asyncio
    async def test_chat_response_has_correct_fields(self, tmp_path):
        """ChatResponse must carry all contract fields after a normal chat."""
        engine = ChatEngine(
            root_dir=str(tmp_path),
            config=_make_config(),
            default_agent="mavis",
            default_model="test-model",
        )

        with patch.object(engine, "_call_llm", new=AsyncMock(return_value="response-body")):
            request = ChatRequest(
                session_id="s-fields",
                message="hi",
                agent="mavis",
                model="test-model",
                stream=False,
            )
            response = await engine.chat(request)

        assert isinstance(response, ChatResponse)
        assert response.session_id == "s-fields"
        assert response.message_id  # non-empty uuid
        assert response.content == "response-body"
        assert response.agent == "mavis"
        assert response.latency_ms >= 0
        # Fields defined on ChatResponse even if unset by chat().
        assert hasattr(response, "model")
        assert hasattr(response, "tokens_used")
        assert hasattr(response, "finish_reason")
        assert hasattr(response, "memory_context_tokens")


# ═══════════════════════════════════════════════════════════════════
# Stream regression: chat_stream must forward tokens
# ═══════════════════════════════════════════════════════════════════


class TestChatStreamRegression:
    """Regression: chat_stream must yield SSE token events from _stream_llm."""

    @pytest.mark.asyncio
    async def test_chat_stream_yields_tokens(self, tmp_path):
        """chat_stream forwards each _stream_llm token as an SSE 'token' event."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())

        async def fake_stream(agent, messages, request):
            """Minimal async generator standing in for _stream_llm."""
            yield "Hello"
            yield " world"

        with patch.object(engine, "_stream_llm", new=fake_stream):
            request = ChatRequest(session_id="s-stream", message="hi", stream=True)
            events: list[str] = []
            async for chunk in engine.chat_stream(request):
                events.append(chunk)

        # session event first, then 2 token events, then done event.
        assert any("event: session" in e for e in events)
        token_events = [e for e in events if "event: token" in e]
        assert len(token_events) == 2
        assert any("Hello" in e for e in token_events)
        assert any("world" in e for e in token_events)
        assert any("event: done" in e for e in events)


# ═══════════════════════════════════════════════════════════════════
# Agent routing regression
# ═══════════════════════════════════════════════════════════════════


class TestChatEngineAgentRouting:
    """Regression: multiple agent names must route to dispatcher unchanged."""

    @pytest.mark.asyncio
    async def test_chat_engine_with_different_agents(self, tmp_path):
        """Each agent name is passed through to _call_llm without mutation."""
        engine = ChatEngine(root_dir=str(tmp_path), config=_make_config())
        seen_agents: list[str] = []

        async def capture_agent(agent, messages, request):
            """Record the agent name and return a deterministic response."""
            seen_agents.append(agent)
            return f"resp-{agent}"

        agent_names = ["mavis", "claude", "gpt", "gemini", "custom-agent-42"]
        with patch.object(engine, "_call_llm", new=capture_agent):
            for name in agent_names:
                request = ChatRequest(
                    session_id=f"s-{name}",
                    message="hi",
                    agent=name,
                    stream=False,
                )
                response = await engine.chat(request)
                assert response.agent == name
                assert response.content == f"resp-{name}"

        assert seen_agents == agent_names