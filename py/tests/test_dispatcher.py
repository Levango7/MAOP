"""Tests for MAOP.delegate.dispatcher — Agent dispatch with driver registry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maop.core.reliability.circuit_breaker import CircuitBreaker
from maop.core.reliability.error_schema import new_result
from maop.delegate.dispatcher import (
    _DRIVERS,
    AgentConfig,
    Dispatcher,
    DispatchResult,
    _escape_for_cmd,
    _escape_for_ps_command,
    _retry_with_backoff,
)


class TestSecurityEscaping:
    def test_cmd_escape_special_chars(self):
        result = _escape_for_cmd("hello & world | test")
        assert "^&" in result
        assert "^|" in result

    def test_cmd_escape_caret(self):
        result = _escape_for_cmd("foo^bar")
        assert "^^" in result

    def test_ps_command_escape(self):
        result = _escape_for_ps_command("it's a test")
        assert result.startswith("'")
        assert result.endswith("'")
        assert "''" in result  # single quote escaped


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig(name="claude")
        assert cfg.driver == "cli"
        assert cfg.timeout_s == 180
        assert cfg.capabilities == []
        # F2a (2026-07-22, Phase F): provider field defaults to empty.
        assert cfg.provider == ""

    def test_full_config(self):
        cfg = AgentConfig(
            name="kimi", cli="kimi-cli", driver="wrapper",
            cli_args="--model moonshot", timeout_s=300,
            model="moonshot-v1", wrapper="kimi-wrapper.ps1",
            provider="moonshot",  # F2a (Phase F): LLM provider name
        )
        assert cfg.driver == "wrapper"
        assert cfg.timeout_s == 300
        assert cfg.provider == "moonshot"


class TestDispatcher:
    def test_dispatch_unknown_agent(self):
        """Dispatching to an unconfigured agent returns error result."""
        dispatcher = Dispatcher()
        result = asyncio.run(
            dispatcher.dispatch(agent="nonexistent", task="test")
        )
        assert not result.result.is_success()
        assert "not found" in (result.result.error or "")
        assert result.driver_used == ""

    def test_dispatch_with_config(self):
        """Dispatcher resolves agent from config."""
        # Create a mock config with agents
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "claude-3"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        # F2c (2026-07-22, Phase F): explicitly set provider so
        # dispatcher._resolve_agent receives a str, not a MagicMock
        # auto-attribute (would fail Pydantic validation).
        mock_agent.provider = ""

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        # The CLI driver will try to run "echo" — mock _DRIVERS dict directly
        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            return new_result(
                agent="claude", task="test", exit_code=0, stdout="hello",
                driver="cli", model="claude-3",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli

    def test_circuit_breaker_blocks(self, tmp_path):
        """When circuit breaker is open, dispatch returns error."""
        mock_agent = MagicMock()
        mock_agent.name = "failing-agent"
        mock_agent.cli = "fail"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        # Isolate breaker state in a per-test tmp DB so repeated runs do not
        # accumulate failures in the production maop.db (which would leave
        # "failing-agent" permanently OPEN and break other tests).
        breaker = CircuitBreaker(tmp_path / "test_breaker.db")
        # Trip the breaker: record failures >= threshold (3)
        for _ in range(3):
            breaker.record_failure("failing-agent")

        dispatcher = Dispatcher(MAOP_config=mock_config, breaker=breaker)
        result = asyncio.run(
            dispatcher.dispatch(agent="failing-agent", task="test")
        )
        assert result.breaker_tripped
        assert "Circuit breaker OPEN" in (result.result.error or "")

    def test_dispatch_unknown_driver(self, tmp_path):
        """Agent with unknown driver returns error."""
        mock_agent = MagicMock()
        mock_agent.name = "bad-driver"
        mock_agent.cli = "echo"
        mock_agent.driver = "nonexistent"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        # Isolate breaker state in a per-test tmp DB. Without this, each test
        # run calls arecord_failure("bad-driver") (dispatcher.py:935) into the
        # shared production maop.db; after 3 runs the breaker trips OPEN and
        # the OPEN check (dispatcher.py:874) short-circuits before the
        # "Unknown driver" path, breaking the assertion.
        breaker = CircuitBreaker(tmp_path / "test_breaker.db")
        dispatcher = Dispatcher(MAOP_config=mock_config, breaker=breaker)
        result = asyncio.run(
            dispatcher.dispatch(agent="bad-driver", task="test")
        )
        assert not result.result.is_success()
        assert "Unknown driver" in (result.result.error or "")

    def test_wildcard_agent_match(self):
        """Dispatcher falls back to wildcard matching."""
        mock_agent = MagicMock()
        mock_agent.name = "codex*"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): see test_dispatch_with_config

        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        # Patch _DRIVERS dict to avoid actually running a subprocess
        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            return new_result(
                agent=config.name, task=prompt, exit_code=0, stdout="ok", driver="cli",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="codex-mini", task="test")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli


# ── Coverage tests (merged from test_dispatcher_coverage.py) ──


def _agent_config(name="claude", driver="cli", **kw):
    return AgentConfig(name=name, driver=driver, **kw)


def _ok_result(agent="claude", task="t"):
    return DispatchResult(result=new_result(agent=agent, task=task, exit_code=0, stdout="ok"))


# ── _retry_with_backoff ───────────────────────────────────────

class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await _retry_with_backoff(factory, max_retries=3, base_delay_ms=1)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        result = await _retry_with_backoff(
            factory, max_retries=3, base_delay_ms=1,
            retryable_exceptions=(ValueError,),
        )
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_fail_raises(self):
        async def factory():
            raise ValueError("permanent")

        with pytest.raises(ValueError, match="permanent"):
            await _retry_with_backoff(
                factory, max_retries=2, base_delay_ms=1,
                retryable_exceptions=(ValueError,),
            )

    @pytest.mark.asyncio
    async def test_non_retryable_exception_not_retried(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await _retry_with_backoff(
                factory, max_retries=3, base_delay_ms=1,
                retryable_exceptions=(ValueError,),
            )
        assert call_count == 1


# ── Lazy subsystem import error paths ─────────────────────────

class TestLazySubsystemImports:
    def test_get_load_balancer_import_error(self):
        import maop.delegate.dispatcher as disp
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "maop.core.routing.load_balancer":
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", _fake_import):
            assert disp._get_load_balancer() is None

    def test_get_load_balancer_runtime_error(self):
        import maop.delegate.dispatcher as disp

        def _fake_get_lb():
            raise RuntimeError("init failed")

        with patch("maop.core.routing.load_balancer.get_load_balancer", _fake_get_lb):
            assert disp._get_load_balancer() is None

    def test_get_runtime_import_error(self):
        import maop.delegate.dispatcher as disp
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "maop.core.agent.lifecycle.runtime":
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", _fake_import):
            assert disp._get_runtime() is None

    def test_get_runtime_runtime_error(self):
        import maop.delegate.dispatcher as disp

        def _fake_create_runtime(*args, **kwargs):
            raise RuntimeError("init failed")

        with patch("maop.core.agent.lifecycle.runtime.create_runtime", _fake_create_runtime):
            assert disp._get_runtime() is None

    def test_get_sandbox_manager_import_error(self):
        import maop.delegate.dispatcher as disp
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "maop.core.security.sandbox":
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", _fake_import):
            assert disp._get_sandbox_manager() is None

    def test_get_sandbox_manager_runtime_error(self):
        import maop.delegate.dispatcher as disp

        def _fake_sandbox(*args, **kwargs):
            raise RuntimeError("init failed")

        with patch("maop.core.security.sandbox.SandboxManager", _fake_sandbox):
            assert disp._get_sandbox_manager() is None

    def test_get_subagent_manager_import_error(self):
        import maop.delegate.dispatcher as disp
        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "maop.core.agent.delegation.subagent_lifecycle":
                raise ImportError("no module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", _fake_import):
            assert disp._get_subagent_manager() is None

    def test_get_subagent_manager_runtime_error(self):
        import maop.delegate.dispatcher as disp

        def _fake_subagent(*args, **kwargs):
            raise RuntimeError("init failed")

        with patch("maop.core.agent.delegation.subagent_lifecycle.SubagentManager", _fake_subagent):
            assert disp._get_subagent_manager() is None


# ── Dispatcher simple methods ─────────────────────────────────

class TestDispatcherSimpleMethods:
    def test_clear_agent_cache(self):
        dispatcher = Dispatcher()
        resolver = MagicMock()
        dispatcher._resolver = resolver
        dispatcher.clear_agent_cache()
        resolver.clear_cache.assert_called_once()

    def test_set_priority_queue(self):
        dispatcher = Dispatcher()
        q = MagicMock()
        dispatcher.set_priority_queue(q)
        assert dispatcher.priority_queue is q

    def test_set_priority_queue_none(self):
        dispatcher = Dispatcher()
        dispatcher.set_priority_queue(None)
        assert dispatcher.priority_queue is None

    def test_effective_model_default_none(self):
        dispatcher = Dispatcher()
        assert dispatcher.effective_model is None


# ── dispatch_priority / drain_pending ─────────────────────────

class TestDispatchPriority:
    @pytest.mark.asyncio
    async def test_dispatch_priority_no_queue_falls_back(self):
        """Without a queue, dispatch_priority calls dispatch directly."""
        dispatcher = Dispatcher()
        with patch.object(dispatcher, "dispatch", AsyncMock(return_value=_ok_result())) as mock_d:
            result = await dispatcher.dispatch_priority("claude", "task")
            mock_d.assert_awaited_once()
        assert result.result.exit_code == 0

    @pytest.mark.asyncio
    async def test_drain_pending_no_queue_returns_zero(self):
        dispatcher = Dispatcher()
        assert await dispatcher.drain_pending() == 0

    @pytest.mark.asyncio
    async def test_drain_pending_empty_queue_returns_zero(self):
        dispatcher = Dispatcher()
        q = MagicMock()
        q.pop = MagicMock(return_value=None)
        dispatcher.set_priority_queue(q)
        assert await dispatcher.drain_pending(limit=5) == 0

    @pytest.mark.asyncio
    async def test_drain_pending_dispatches_one(self):
        dispatcher = Dispatcher()
        fut = asyncio.get_running_loop().create_future()
        pt = MagicMock()
        pt.payload = {"agent": "claude", "task": "t", "future": fut}
        pt.priority = 3
        pt.deadline_ms = None
        q = MagicMock()
        q.pop = MagicMock(side_effect=[pt, None])
        dispatcher.set_priority_queue(q)

        with patch.object(dispatcher, "dispatch", AsyncMock(return_value=_ok_result())):
            count = await dispatcher.drain_pending(limit=5)

        assert count == 1
        assert fut.done()
        assert fut.result().result.exit_code == 0

    @pytest.mark.asyncio
    async def test_drain_pending_dispatch_exception_sets_future_exception(self):
        dispatcher = Dispatcher()
        fut = asyncio.get_running_loop().create_future()
        pt = MagicMock()
        pt.payload = {"agent": "claude", "task": "t", "future": fut}
        pt.priority = 3
        pt.deadline_ms = None
        q = MagicMock()
        q.pop = MagicMock(side_effect=[pt, None])
        dispatcher.set_priority_queue(q)

        with patch.object(dispatcher, "dispatch", AsyncMock(side_effect=RuntimeError("boom"))):
            count = await dispatcher.drain_pending(limit=5)

        assert count == 1
        assert fut.done()
        with pytest.raises(RuntimeError, match="boom"):
            fut.result()


# ── _notify_route_scorer exception path ───────────────────────

class TestNotifyRouteScorer:
    def test_exception_does_not_raise(self):
        dispatcher = Dispatcher()

        def _fake_get_scorer(*args):
            raise RuntimeError("scorer unavailable")

        with patch("maop.core.routing.route_scorer.get_route_scorer", _fake_get_scorer):
            # Should not raise
            dispatcher._notify_route_scorer("claude", success=True)


# ── dispatch: capability matching fallback ────────────────────

class TestCapabilityMatchingFallback:
    @pytest.mark.asyncio
    async def test_capability_match_fallback(self):
        """When agent not in config but capability matcher finds one, use it."""
        dispatcher = Dispatcher()
        matched_config = _agent_config(name="matched-agent", driver="cli")

        # Make resolve return None (not found), match_agent return a config
        dispatcher._resolver = MagicMock()
        dispatcher._resolver.resolve = MagicMock(return_value=None)
        dispatcher._resolver.match_agent = MagicMock(return_value=matched_config)
        dispatcher._resolver.clear_cache = MagicMock()

        # Mock the driver to return success
        from maop.delegate import drivers
        fake_driver = AsyncMock(return_value=new_result(agent="matched-agent", task="t", exit_code=0, stdout="ok"))
        with patch.dict(drivers.DRIVERS, {"cli": fake_driver}):
            result = await dispatcher.dispatch("unknown-agent", "task")

        assert result.result.exit_code == 0
        assert result.driver_used == "cli"


# ── dispatch: circuit-breaker failover ────────────────────────

class TestCircuitBreakerFailover:
    @pytest.mark.asyncio
    async def test_failover_when_breaker_open(self):
        """When breaker is open and failover resolves, dispatch to failover agent."""
        dispatcher = Dispatcher()
        config = _agent_config(name="primary", driver="cli")
        dispatcher._resolver = MagicMock()
        dispatcher._resolver.resolve = MagicMock(return_value=config)
        dispatcher._resolver.clear_cache = MagicMock()

        # Breaker: primary not available, failover to "backup"
        failover = MagicMock()
        failover.agent = "backup"
        failover.degraded = True
        dispatcher._breaker = MagicMock()
        dispatcher._breaker.ais_available = AsyncMock(side_effect=[False, True])  # primary closed, backup open
        dispatcher._breaker.resolve_failover = MagicMock(return_value=failover)
        dispatcher._breaker.arecord_success = AsyncMock()
        dispatcher._breaker.arecord_failure = AsyncMock()

        # When failover dispatches, resolve "backup" config
        backup_config = _agent_config(name="backup", driver="cli")
        dispatcher._resolver.resolve = MagicMock(side_effect=[config, backup_config])

        from maop.delegate import drivers
        fake_driver = AsyncMock(return_value=new_result(agent="backup", task="t", exit_code=0, stdout="ok"))
        with patch.dict(drivers.DRIVERS, {"cli": fake_driver}):
            result = await dispatcher.dispatch("primary", "task")

        assert result.result.exit_code == 0

    @pytest.mark.asyncio
    async def test_failover_resolve_exception(self):
        """When resolve_failover raises, treat as no failover."""
        dispatcher = Dispatcher()
        config = _agent_config(name="primary", driver="cli")
        dispatcher._resolver = MagicMock()
        dispatcher._resolver.resolve = MagicMock(return_value=config)
        dispatcher._resolver.clear_cache = MagicMock()

        dispatcher._breaker = MagicMock()
        dispatcher._breaker.ais_available = AsyncMock(return_value=False)
        dispatcher._breaker.resolve_failover = MagicMock(side_effect=RuntimeError("no failover"))
        dispatcher._breaker.arecord_failure = AsyncMock()

        result = await dispatcher.dispatch("primary", "task")
        # Should return breaker_tripped=True with exit_code=-3
        assert result.breaker_tripped is True
        assert result.result.exit_code == -3


# ── dispatch: driver exception ────────────────────────────────

class TestDriverException:
    @pytest.mark.asyncio
    async def test_driver_raises_returns_error_result(self):
        dispatcher = Dispatcher()
        config = _agent_config(name="claude", driver="cli")
        dispatcher._resolver = MagicMock()
        dispatcher._resolver.resolve = MagicMock(return_value=config)
        dispatcher._resolver.clear_cache = MagicMock()

        dispatcher._breaker = MagicMock()
        dispatcher._breaker.ais_available = AsyncMock(return_value=True)
        dispatcher._breaker.arecord_failure = AsyncMock()
        dispatcher._breaker.arecord_success = AsyncMock()

        from maop.delegate import drivers
        fake_driver = AsyncMock(side_effect=RuntimeError("driver crashed"))
        with patch.dict(drivers.DRIVERS, {"cli": fake_driver}):
            result = await dispatcher.dispatch("claude", "task")

        assert result.result.exit_code == -5
        assert "driver crashed" in (result.result.error or "")


# ── delegate_to_subagent ──────────────────────────────────────

class TestDelegateToSubagent:
    @pytest.mark.asyncio
    async def test_no_subagent_manager_returns_error(self):
        dispatcher = Dispatcher()
        dispatcher._subagent_mgr = None

        with patch("maop.delegate.dispatcher._get_subagent_manager", return_value=None):
            result = await dispatcher.delegate_to_subagent("parent", "child", "task")

        assert result.result.exit_code == -2
        # P0-2: 错误消息从 "SubagentManager" 改为 "SubAgentManager"
        assert "SubAgentManager" in (result.result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_success(self):
        dispatcher = Dispatcher()
        dispatcher._subagent_mgr = None

        mock_mgr = MagicMock()
        sa_info = MagicMock()
        sa_info.id = "sa-1"
        # P0-2: spawn_child 替代原 spawn（委派语义）
        mock_mgr.spawn_child = MagicMock(return_value=sa_info)
        mock_mgr.terminate = MagicMock()

        with patch("maop.delegate.dispatcher._get_subagent_manager", return_value=mock_mgr), \
             patch.object(dispatcher, "dispatch", AsyncMock(return_value=_ok_result())):
            result = await dispatcher.delegate_to_subagent("parent", "child", "task")

        assert result.result.exit_code == 0
        mock_mgr.spawn_child.assert_called_once()
        mock_mgr.terminate.assert_called_once_with("sa-1", exit_code=0)


# ── Extended tests (merged from test_dispatcher_extended.py) ──

class TestDriverRegistry:
    def test_all_expected_drivers_registered(self):
        assert "cli" in _DRIVERS
        assert "wrapper" in _DRIVERS
        assert "powershell" in _DRIVERS
        assert "cmd" in _DRIVERS
        assert "python" in _DRIVERS

    def test_driver_count(self):
        assert len(_DRIVERS) == 5


class TestGuardrailIntegration:
    def test_guardrail_blocks_dispatch(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        with patch("maop.core.security.guardrail.Guardrail") as MockGuardrail:
            mock_gr = MagicMock()
            mock_check = MagicMock()
            mock_check.passed = False
            mock_violation = MagicMock()
            mock_violation.action = "block"
            mock_violation.message = "sensitive content detected"
            mock_check.violations = [mock_violation]
            mock_gr.check.return_value = mock_check
            MockGuardrail.return_value = mock_gr

            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="sk-abc1234567890123456789012")
            )
            assert result.result.exit_code == -4
            assert "Guardrail BLOCKED" in (result.result.error or "")

    def test_guardrail_fail_closed_on_exception(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = None
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        dispatcher = Dispatcher(MAOP_config=mock_config)

        with patch("maop.core.security.guardrail.Guardrail", side_effect=RuntimeError("guardrail crashed")):
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test")
            )
            assert result.result.exit_code == -4
            assert "fail-closed" in (result.result.error or "")


class TestModelResolution:
    def test_model_selector_injects_model(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "original-model"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        mock_selector = MagicMock()
        mock_em = MagicMock()
        mock_em.model_name = "resolved-model"
        mock_selector.select_for_routing_key.return_value = mock_em

        dispatcher = Dispatcher(MAOP_config=mock_config, model_selector=mock_selector)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.model == "resolved-model"
            return new_result(agent="claude", task="test", exit_code=0, stdout="ok", driver="cli")

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test", routing_key="chat")
            )
            assert result.result.is_success()
            assert result.model_resolved is True
        finally:
            disp_mod._DRIVERS["cli"] = original_cli

    def test_model_selector_failure_keeps_original(self):
        mock_agent = MagicMock()
        mock_agent.name = "claude"
        mock_agent.cli = "echo"
        mock_agent.driver = "cli"
        mock_agent.cli_args = ""
        mock_agent.capabilities = []
        mock_agent.timeout_s = 10
        mock_agent.model = "fallback-model"
        mock_agent.wrapper = ""
        mock_agent.command = ""
        mock_agent.provider = ""  # F2c (Phase F): explicit str to avoid MagicMock
        mock_config = MagicMock()
        mock_config.agents = [mock_agent]
        mock_config.workflows = []

        mock_selector = MagicMock()
        mock_selector.select_for_routing_key.side_effect = RuntimeError("model unavailable")

        dispatcher = Dispatcher(MAOP_config=mock_config, model_selector=mock_selector)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.model == "fallback-model"
            return new_result(agent="claude", task="test", exit_code=0, stdout="ok", driver="cli")

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="claude", task="test", routing_key="chat")
            )
            assert result.model_resolved is False
        finally:
            disp_mod._DRIVERS["cli"] = original_cli


class TestEscapeForCmd:
    def test_empty_string(self):
        assert _escape_for_cmd("") == ""

    def test_no_special_chars(self):
        assert _escape_for_cmd("hello world") == "hello world"

    def test_all_special_chars(self):
        result = _escape_for_cmd("& | < > ^ ( )")
        assert "^&" in result
        assert "^|" in result
        assert "^<" in result
        assert "^>" in result
        assert "^^" in result
        assert "^(" in result
        assert "^)" in result

    def test_newline_escaped(self):
        result = _escape_for_cmd("line1\nline2")
        assert "^\n" in result


class TestEscapeForPsCommand:
    def test_empty_string(self):
        result = _escape_for_ps_command("")
        assert result == "''"

    def test_single_quote_escaped(self):
        result = _escape_for_ps_command("it's")
        assert "''" in result

    def test_null_bytes_stripped(self):
        result = _escape_for_ps_command("test\x00injection")
        assert "\x00" not in result

    def test_dollar_not_expanded(self):
        result = _escape_for_ps_command("$env:PATH")
        assert result.startswith("'")
        assert result.endswith("'")
        assert "$env" in result


class TestDispatchResult:
    def test_default_values(self):
        r = DispatchResult(result=new_result(agent="a", task="t", exit_code=0))
        assert r.driver_used == ""
        assert r.breaker_tripped is False
        assert r.model_resolved is True


class TestSubagentResolution:
    """Test Dispatcher._resolve_agent() with parent/child format."""

    def _make_config_with_subagents(self):
        from maop.config.loader import AgentDef, MaopConfig, SubagentDef
        parent = AgentDef(
            cli="mavis",
            cli_args="{task}",
            driver="cli",
            capabilities=["codegen", "chat", "review"],
            model="minimax-m2.7",
            timeout_s=120,
            subagents={
                "coder": SubagentDef(
                    cli_args="agent start coder --prompt {task}",
                    capabilities=["codegen", "refactor"],
                    description="Mavis sub-agent: Coder",
                ),
                "verifier": SubagentDef(
                    cli_args="agent start verifier --prompt {task}",
                    capabilities=["review", "verify"],
                    description="Mavis sub-agent: Verifier",
                ),
            },
        )
        return MaopConfig(agents={"mavis": parent})

    def test_resolve_subagent_parent_child(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/verifier")
        assert resolved is not None
        assert resolved.name == "mavis/verifier"
        assert resolved.cli == "mavis"
        assert resolved.cli_args == "agent start verifier --prompt {task}"
        assert resolved.driver == "cli"
        assert resolved.model == "minimax-m2.7"
        assert "verify" in resolved.capabilities

    def test_resolve_subagent_coder(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/coder")
        assert resolved is not None
        assert resolved.name == "mavis/coder"
        assert resolved.cli_args == "agent start coder --prompt {task}"
        assert "codegen" in resolved.capabilities

    def test_resolve_subagent_unknown_child(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis/nonexistent")
        assert resolved is None

    def test_resolve_subagent_unknown_parent(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("unknown/child")
        assert resolved is None

    def test_resolve_subagent_caching(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        first = dispatcher._resolve_agent("mavis/verifier")
        second = dispatcher._resolve_agent("mavis/verifier")
        assert first is not None
        assert second is not None
        assert first.name == second.name
        assert first.cli == second.cli

    def test_resolve_parent_agent_still_works(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)
        resolved = dispatcher._resolve_agent("mavis")
        assert resolved is not None
        assert resolved.name == "mavis"
        assert resolved.cli_args == "{task}"

    def test_dispatch_subagent_integration(self):
        cfg = self._make_config_with_subagents()
        dispatcher = Dispatcher(MAOP_config=cfg)

        from maop.delegate import dispatcher as disp_mod
        original_cli = disp_mod._DRIVERS["cli"]

        async def mock_cli(config, prompt, timeout, workdir, trace_id, *, streamer=None):
            assert config.name == "mavis/verifier"
            assert config.cli == "mavis"
            assert "verifier" in config.cli_args
            return new_result(
                agent=config.name, task=prompt, exit_code=0,
                stdout="verified", driver="cli",
            )

        disp_mod._DRIVERS["cli"] = mock_cli
        try:
            result = asyncio.run(
                dispatcher.dispatch(agent="mavis/verifier", task="check this code")
            )
            assert result.result.is_success()
            assert result.driver_used == "cli"
        finally:
            disp_mod._DRIVERS["cli"] = original_cli


class TestConfigLoaderSubagents:
    """Test ConfigLoader correctly parses subagents from YAML."""

    def test_load_config_parses_subagents(self, tmp_path):
        from maop.config.loader import ConfigLoader
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_yaml = config_dir / "agents.yaml"
        agents_yaml.write_text("""
agents:
  mavis:
    cli: mavis
    cli_args: '{task}'
    driver: cli
    model: minimax-m2.7
    timeout_s: 120
    subagents:
      coder:
        cli_args: agent start coder --prompt {task}
        capabilities:
          - codegen
          - refactor
        description: 'Mavis sub-agent: Coder'
      verifier:
        cli_args: agent start verifier --prompt {task}
        capabilities:
          - review
          - verify
        description: 'Mavis sub-agent: Verifier'
routing:
  verify:
    primary: mavis/verifier
    fallback: claude
""", encoding="utf-8")
        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("guards:\n  retry:\n    max_attempts: 3\n", encoding="utf-8")

        loader = ConfigLoader(project_root=tmp_path)
        cfg = loader.load()

        assert "mavis" in cfg.agents
        mavis = cfg.agents["mavis"]
        assert len(mavis.subagents) == 2
        assert "coder" in mavis.subagents
        assert "verifier" in mavis.subagents
        assert mavis.subagents["coder"].cli_args == "agent start coder --prompt {task}"
        assert "codegen" in mavis.subagents["coder"].capabilities
        assert mavis.subagents["verifier"].cli_args == "agent start verifier --prompt {task}"

        assert "verify" in cfg.routing
        assert cfg.routing["verify"].primary == "mavis/verifier"

    def test_load_config_agent_without_subagents(self, tmp_path):
        from maop.config.loader import ConfigLoader
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_yaml = config_dir / "agents.yaml"
        agents_yaml.write_text("""
agents:
  claude:
    cli: claude
    cli_args: "-p '{task}'"
    driver: cli
    model: yi-large
    timeout_s: 120
""", encoding="utf-8")
        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("guards:\n  retry:\n    max_attempts: 3\n", encoding="utf-8")

        loader = ConfigLoader(project_root=tmp_path)
        cfg = loader.load()

        assert "claude" in cfg.agents
        assert cfg.agents["claude"].subagents == {}
