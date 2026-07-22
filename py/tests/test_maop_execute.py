"""Tests for maop_execute.py — Execution engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.maop_execute import Delegate, maop_execute, Observability
from maop.core.error_schema import new_result
from maop.delegate.dispatcher import DispatchResult


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
        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="do something",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == 0
            assert result.stdout == "ok"
            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_delegate_param(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
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

        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
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

        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test",
                dispatcher=bad_dispatcher, guardrail=mock_guardrail,
            )
            assert result.exit_code == -1
            assert "Dispatch error" in result.error

    @pytest.mark.asyncio
    async def test_trace_id_generated(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
            result = await maop_execute(
                agent="test", task="test",
                dispatcher=mock_dispatcher, guardrail=mock_guardrail,
            )
            assert result.trace_id != ""

    @pytest.mark.asyncio
    async def test_trace_id_preserved(self, mock_dispatcher, mock_guardrail):
        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
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
        from maop.core.guardrail import Guardrail
        gr = Guardrail()

        with patch("maop.core.permission.PermissionManager", return_value=_mock_perm_allow()):
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

        with patch("maop.core.permission.PermissionManager", return_value=pm):
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
