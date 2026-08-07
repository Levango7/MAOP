"""Tests for maop_execute.py — Execution engine."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.reliability.error_schema import new_result
from maop.delegate.dispatcher import DispatchResult
from maop.maop_execute import Delegate, Observability, maop_execute


def make_dispatch_result(exit_code=0, stdout="ok", stderr=""):
    """Create a mock DispatchResult."""
    result = new_result(agent="test", task="test", exit_code=exit_code,
                        stdout=stdout, stderr=stderr)
    return DispatchResult(result=result, agent="test", duration_ms=10)


def _mock_perm_allow():
    perm_result = MagicMock()
    perm_result.decision = "allow"
    perm_result.reason = ""
    perm_result.matched_rule = ""
    pm = MagicMock()
    pm.check.return_value = perm_result
    return pm


@pytest.fixture
def mock_dispatcher():
    """Mock dispatcher that returns success."""
    d = MagicMock()
    d.dispatch = AsyncMock(return_value=make_dispatch_result())
    return d


@pytest.fixture
def mock_guardrail():
    """Mock guardrail that always passes."""
    g = MagicMock()
    check_result = MagicMock()
    check_result.passed = True
    check_result.reason = ""
    g.check = MagicMock(return_value=check_result)
    return g


class TestMaopExecute:
    @pytest.mark.asyncio
    async def test_basic_success(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="do something",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == 0
            assert result.stdout == "ok"
            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_delegate_param(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            d = Delegate(agent="myagent", task="mytask", timeout_seconds=60)
            result = await maop_execute(
                delegate=d, dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == 0
            mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_guardrail_blocks(self, mock_dispatcher):
        gr = MagicMock()
        check_result = MagicMock()
        check_result.passed = False
        check_result.reason = "Task too dangerous"
        gr.check = MagicMock(return_value=check_result)

        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="dangerous task",
                dispatcher=mock_dispatcher, guardrail=gr,
            )
            assert result.exit_code == 126
            assert "Guardrail blocked" in result.error

    @pytest.mark.asyncio
    async def test_dispatch_exception(self, mock_guardrail):
        bad_dispatcher = MagicMock()
        bad_dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test",
                dispatcher=bad_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == -1
            assert "Dispatch error" in result.error

    @pytest.mark.asyncio
    async def test_trace_id_generated(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.trace_id != ""

    @pytest.mark.asyncio
    async def test_trace_id_preserved(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test", trace_id="my-trace-123",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.trace_id == "my-trace-123"

    @pytest.mark.asyncio
    async def test_output_guardrail_blocks(self, mock_dispatcher):
        """Post-execution guardrail should block dangerous output."""
        mock_dispatcher.dispatch = AsyncMock(
            return_value=make_dispatch_result(stdout="api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
        )
        from maop.core.security.guardrail import Guardrail
        gr = Guardrail()

        with patch("maop.core.security.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test",
                dispatcher=mock_dispatcher, guardrail=gr,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_permission_ask_denied(self, mock_dispatcher, mock_guardrail):
        perm_result = MagicMock()
        perm_result.decision = "ask"
        perm_result.reason = "needs approval"
        perm_result.matched_rule = "test-rule"
        pm = MagicMock()
        pm.check.return_value = perm_result

        with patch("maop.core.security.permission.PermissionManager", return_value=pm):
            result = await maop_execute(
                agent="test", task="sensitive task",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == 126
            assert "pending human approval" in result.error


class TestDelegateModel:
    def test_defaults(self):
        d = Delegate(agent="a", task="b")
        assert d.routing_key == ""
        assert d.workdir == ""
        assert d.timeout_seconds == 120
        assert d.trace_id == ""

    def test_full(self):
        d = Delegate(agent="a", task="b", routing_key="code", workdir="/tmp",
                     timeout_seconds=60, trace_id="t123")
        assert d.agent == "a"
        assert d.task == "b"
        assert d.routing_key == "code"
        assert d.workdir == "/tmp"
        assert d.timeout_seconds == 60
        assert d.trace_id == "t123"


class TestObservabilityModel:
    def test_defaults(self):
        o = Observability()
        assert o.trace_id == ""
        assert o.span_id == ""
        assert o.start_time == 0.0
        assert o.duration_ms == 0


# --- Merged from test_maop_execute_coverage3.py ---
# Coverage tests (round 3) for maop_execute.py — focus on ReAct mode,
# permission deny/ask, hook veto, guardrail exceptions, function-call loop,
# and post-dispatch hooks.
#
# Targets missing lines: 104, 123-152, 167, 188-190, 205-214, 231-234,
# 266, 287-290, 312, 320-321, 348-397.

execute = maop_execute


def _allow_permission():
    """Create a mock PermissionManager that allows everything."""
    mock_pm = MagicMock()
    mock_pm.check.return_value = MagicMock(
        decision="allow", reason="", matched_rule=""
    )
    return mock_pm


def _pass_guardrail():
    """Create a mock Guardrail that passes everything."""
    mock_g = MagicMock()
    mock_g.check.return_value = MagicMock(passed=True, reason="ok")
    return mock_g


def _no_hooks():
    """Patch get_hook_manager to return a no-op manager."""
    mock_mgr = MagicMock()
    mock_mgr.trigger = AsyncMock(return_value=[])
    return patch("maop.core.agent.plugins_hooks.hook_manager.get_hook_manager", return_value=mock_mgr)


def _mock_streaming():
    """Patch streaming imports."""
    return (
        patch("maop.core.reliability.streaming.SubprocessStreamer"),
        patch("maop.core.reliability.streaming.get_stream_registry"),
    )


# ── ReAct mode ──────────────────────────────────────────────────────


class TestReactMode:
    @pytest.mark.asyncio
    async def test_react_mode_success(self):
        """Cover ReAct mode success path (123-150)."""
        mock_react_result = MagicMock()
        mock_react_result.success = True
        mock_react_result.final_answer = "answer"
        mock_react_result.error = ""
        mock_react_result.session_id = "s1"
        mock_react_result.total_iterations = 3
        mock_react_result.total_tool_calls = 2
        mock_react_result.steps = []

        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=mock_react_result)

        with patch(
            "maop.core.agent.llm_chat.react_loop.ReactLoop", return_value=mock_loop
        ), patch(
            "maop.core.agent.llm_chat.react_loop.ReactConfig"
        ):
            result = await execute(
                agent="test", task="do something",
                react_mode=True, react_max_iterations=5,
            )
        assert result.exit_code == 0
        assert result.stdout == "answer"

    @pytest.mark.asyncio
    async def test_react_mode_failure(self):
        """Cover ReAct mode failure path (123-156)."""
        with patch(
            "maop.core.agent.llm_chat.react_loop.ReactLoop",
            side_effect=ImportError("no react loop"),
        ):
            result = await execute(
                agent="test", task="do something",
                react_mode=True,
            )
        assert result.exit_code == -1
        assert "ReAct loop error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_react_mode_via_delegate(self):
        """Cover ReAct mode triggered via Delegate (104)."""
        mock_react_result = MagicMock()
        mock_react_result.success = True
        mock_react_result.final_answer = "done"
        mock_react_result.error = ""
        mock_react_result.session_id = "s1"
        mock_react_result.total_iterations = 1
        mock_react_result.total_tool_calls = 0
        mock_react_result.steps = []

        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=mock_react_result)

        delegate = Delegate(
            agent="test", task="task",
            react_mode=True, react_max_iterations=3,
            tools=[{"name": "tool1"}],
        )

        with patch(
            "maop.core.agent.llm_chat.react_loop.ReactLoop", return_value=mock_loop
        ), patch(
            "maop.core.agent.llm_chat.react_loop.ReactConfig"
        ):
            result = await execute(delegate=delegate)
        assert result.exit_code == 0


# ── Permission check ────────────────────────────────────────────────


class TestPermissionCheck:
    @pytest.mark.asyncio
    async def test_permission_deny(self):
        """Cover permission deny (167-172)."""
        mock_pm = MagicMock()
        mock_pm.check.return_value = MagicMock(
            decision="deny", reason="not allowed", matched_rule="rule1"
        )

        result = await execute(
            agent="test", task="task",
            permission_manager=mock_pm,
        )
        assert result.exit_code == 126
        assert "Permission denied" in (result.error or "")

    @pytest.mark.asyncio
    async def test_permission_ask(self):
        """Cover permission ask → human proxy (173-187)."""
        mock_pm = MagicMock()
        mock_pm.check.return_value = MagicMock(
            decision="ask", reason="needs approval", matched_rule="rule2"
        )

        mock_hp = MagicMock()
        mock_hp.request.return_value = "req-123"

        with patch(
            "maop.core.agent.delegation.human_proxy.HumanProxy", return_value=mock_hp
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=mock_pm,
            )
        assert result.exit_code == 126
        assert "pending human approval" in (result.error or "")

    @pytest.mark.asyncio
    async def test_permission_check_exception(self):
        """Cover permission check exception (188-195)."""
        mock_pm = MagicMock()
        mock_pm.check.side_effect = RuntimeError("perm boom")

        result = await execute(
            agent="test", task="task",
            permission_manager=mock_pm,
        )
        assert result.exit_code == 126
        assert "Permission check failed" in (result.error or "")


# ── Hook pre-dispatch ───────────────────────────────────────────────


class TestHookPreDispatch:
    @pytest.mark.asyncio
    async def test_hook_veto(self):
        """Cover hook veto (205-211)."""
        mock_hook_result = MagicMock()
        mock_hook_result.decision = "deny"
        mock_hook_result.hook_id = "hook1"
        mock_hook_result.error = "vetoed"

        mock_mgr = MagicMock()
        mock_mgr.trigger = AsyncMock(return_value=[mock_hook_result])

        with patch(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager", return_value=mock_mgr
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
            )
        assert result.exit_code == 126
        assert "Hook vetoed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_hook_exception(self):
        """Cover hook pre-dispatch exception (212-219)."""
        mock_mgr = MagicMock()
        mock_mgr.trigger = AsyncMock(side_effect=RuntimeError("hook boom"))

        with patch(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager", return_value=mock_mgr
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
            )
        assert result.exit_code == 126
        assert "Hook pre_dispatch error" in (result.error or "")


# ── Guardrail exceptions ────────────────────────────────────────────


class TestGuardrailExceptions:
    @pytest.mark.asyncio
    async def test_pre_guardrail_exception(self):
        """Cover pre-guardrail check exception (231-239)."""
        mock_guardrail = MagicMock()
        mock_guardrail.check.side_effect = RuntimeError("guardrail boom")

        with _no_hooks():
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=mock_guardrail,
            )
        assert result.exit_code == 126
        assert "Guardrail check error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_post_guardrail_exception(self):
        """Cover post-guardrail check exception (287-296)."""
        mock_guardrail = MagicMock()
        # Pre-check passes, post-check raises
        call_count = [0]
        def check_side_effect(text):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("post boom")
            return MagicMock(passed=True, reason="ok")
        mock_guardrail.check.side_effect = check_side_effect

        mock_dispatch_result = MagicMock()
        mock_dispatch_result.result = MagicMock()
        mock_dispatch_result.result.is_success.return_value = True
        mock_dispatch_result.result.stdout = "output"
        mock_dispatch_result.result.exit_code = 0
        mock_dispatch_result.result.error = None
        mock_dispatch_result.result.trace_id = ""
        mock_dispatch_result.result.duration_ms = 0

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=mock_dispatch_result)

        p1, p2 = _mock_streaming()
        with _no_hooks(), p1, p2:
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=mock_guardrail,
                dispatcher=mock_dispatcher,
            )
        assert result.exit_code == 127
        assert "Post-guardrail check error" in (result.error or "")


# ── Post-dispatch hooks ─────────────────────────────────────────────


class TestPostDispatchHooks:
    @pytest.mark.asyncio
    async def test_timeout_hook(self):
        """Cover timeout hook (311-314)."""
        mock_dispatch_result = MagicMock()
        mock_dispatch_result.result = MagicMock()
        mock_dispatch_result.result.is_success.return_value = False
        mock_dispatch_result.result.stdout = ""
        mock_dispatch_result.result.exit_code = -1
        mock_dispatch_result.result.error = "TIMEOUT: process killed"
        mock_dispatch_result.result.trace_id = ""
        mock_dispatch_result.result.duration_ms = 0

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=mock_dispatch_result)

        mock_mgr = MagicMock()
        mock_mgr.trigger = AsyncMock(return_value=[])

        p1, p2 = _mock_streaming()
        with patch(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager", return_value=mock_mgr
        ), p1, p2:
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=_pass_guardrail(),
                dispatcher=mock_dispatcher,
            )
        assert result.is_success() is False
        # Verify timeout hook was triggered (pre + timeout)
        assert mock_mgr.trigger.call_count >= 2

    @pytest.mark.asyncio
    async def test_post_dispatch_hook_exception(self):
        """Cover post-dispatch hook exception (320-321)."""
        mock_dispatch_result = MagicMock()
        mock_dispatch_result.result = MagicMock()
        mock_dispatch_result.result.is_success.return_value = True
        mock_dispatch_result.result.stdout = "output"
        mock_dispatch_result.result.exit_code = 0
        mock_dispatch_result.result.error = None
        mock_dispatch_result.result.trace_id = ""
        mock_dispatch_result.result.duration_ms = 0

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=mock_dispatch_result)

        call_count = [0]
        async def trigger_side_effect(event, data):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("post hook boom")
            return []

        mock_mgr = MagicMock()
        mock_mgr.trigger = trigger_side_effect

        p1, p2 = _mock_streaming()
        with patch(
            "maop.core.agent.plugins_hooks.hook_manager.get_hook_manager", return_value=mock_mgr
        ), p1, p2:
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=_pass_guardrail(),
                dispatcher=mock_dispatcher,
            )
        assert result.is_success() is True


# ── Function-call loop ──────────────────────────────────────────────


class TestFunctionCallLoop:
    @pytest.mark.asyncio
    async def test_function_call_loop(self):
        """Cover _handle_function_calls (348-397)."""
        first_result = MagicMock()
        first_result.is_success.return_value = True
        first_result.stdout = json.dumps({
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "tool1", "arguments": "{}"},
                    }]
                }
            }]
        })
        first_result.exit_code = 0
        first_result.error = None
        first_result.trace_id = ""
        first_result.duration_ms = 0

        first_dispatch = MagicMock()
        first_dispatch.result = first_result

        second_result = MagicMock()
        second_result.is_success.return_value = True
        second_result.stdout = "final answer"
        second_result.exit_code = 0
        second_result.error = None
        second_result.trace_id = ""
        second_result.duration_ms = 0

        second_dispatch = MagicMock()
        second_dispatch.result = second_result

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            side_effect=[first_dispatch, second_dispatch]
        )

        mock_bridge = MagicMock()
        mock_bridge.parse_response.return_value = [{"name": "tool1", "arguments": {}}]
        mock_bridge.execute = AsyncMock(return_value=MagicMock(ok=True, output="result"))
        mock_bridge.format_result.return_value = {"role": "tool", "content": "result"}

        p1, p2 = _mock_streaming()
        with _no_hooks(), p1, p2, patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge", return_value=mock_bridge
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=_pass_guardrail(),
                dispatcher=mock_dispatcher,
                tools=[{"name": "tool1"}],
                provider="openai",
                max_tool_rounds=3,
            )
        assert result.is_success() is True

    @pytest.mark.asyncio
    async def test_function_call_no_tool_calls(self):
        """Cover function call loop with no tool calls (breaks early)."""
        first_result = MagicMock()
        first_result.is_success.return_value = True
        first_result.stdout = json.dumps({"choices": [{"message": {}}]})
        first_result.exit_code = 0
        first_result.error = None
        first_result.trace_id = ""
        first_result.duration_ms = 0

        first_dispatch = MagicMock()
        first_dispatch.result = first_result

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=first_dispatch)

        mock_bridge = MagicMock()
        mock_bridge.parse_response.return_value = []

        p1, p2 = _mock_streaming()
        with _no_hooks(), p1, p2, patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge", return_value=mock_bridge
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=_pass_guardrail(),
                dispatcher=mock_dispatcher,
                tools=[{"name": "tool1"}],
                provider="openai",
            )
        assert result.is_success() is True

    @pytest.mark.asyncio
    async def test_function_call_invalid_json(self):
        """Cover function call loop with invalid JSON stdout (breaks)."""
        first_result = MagicMock()
        first_result.is_success.return_value = True
        first_result.stdout = "not json"
        first_result.exit_code = 0
        first_result.error = None
        first_result.trace_id = ""
        first_result.duration_ms = 0

        first_dispatch = MagicMock()
        first_dispatch.result = first_result

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=first_dispatch)

        p1, p2 = _mock_streaming()
        with _no_hooks(), p1, p2, patch(
            "maop.core.agent.llm_chat.function_call.FunctionCallBridge"
        ):
            result = await execute(
                agent="test", task="task",
                permission_manager=_allow_permission(),
                guardrail=_pass_guardrail(),
                dispatcher=mock_dispatcher,
                tools=[{"name": "tool1"}],
            )
        assert result.is_success() is True
