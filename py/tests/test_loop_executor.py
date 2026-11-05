"""Tests for maop.loop_executor — ExecuteMixin execution strategies (retry / fallback / parallel dispatch)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from maop.core.agent.analyzer import AnalysisResult, DependencyDAG, ExecutionStrategy, SubTask
from maop.core.reliability.error_schema import MaopResult, new_result
from maop.loop_executor import ExecuteMixin

# ── Helpers ──────────────────────────────────────────────────────────

def _make_result(*, success: bool, agent: str = "a", task: str = "t") -> MaopResult:
    return new_result(
        agent=agent, task=task,
        exit_code=0 if success else 1,
        error=None if success else "failed",
    )


def _make_dispatch_result(*, success: bool, breaker: bool = False, agent: str = "a"):
    """Mimic DispatchResult(result=..., breaker_tripped=...)."""
    return SimpleNamespace(
        result=_make_result(success=success, agent=agent), breaker_tripped=breaker,
    )


def _make_loop_config(*, max_attempts: int = 3, max_workers: int = 4):
    return SimpleNamespace(
        retry_backoff_ms=0,
        iterative_backoff_ms=0,
        iterative_max_attempts=max_attempts,
        max_workers=max_workers,
    )


class _Host(ExecuteMixin):
    """Minimal host providing the attributes ExecuteMixin requires."""

    def __init__(self, dispatcher, loop_config, worker_pool=None):
        self._dispatcher = dispatcher
        self._loop_config = loop_config
        self._worker_pool = worker_pool
        self._log = MagicMock()
        # PhasesMixin 在 MaopLoop 里才提供；这里记录调用，供并行用例断言逐笔对账。
        self.reconciled: list[tuple[str | None, str]] = []

    def _budget_reconciliation(self, exec_result, trace_id):
        self.reconciled.append((getattr(exec_result, "agent", None), trace_id))


# ── _execute_with_retry ──────────────────────────────────────────────

class TestExecuteWithRetry:
    @pytest.mark.asyncio
    async def test_first_agent_success(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config())

        result = await host._execute_with_retry(
            task="t", fallback_chain=["a", "b"],
            routing_key="k", workdir=".", timeout=10,
            retry=True, trace_id="tr",
        )
        assert result is not None and result.is_success()
        assert dispatcher.dispatch.await_count == 1

    @pytest.mark.asyncio
    async def test_fallback_to_second_agent(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=[
            _make_dispatch_result(success=False),
            _make_dispatch_result(success=True),
        ])
        host = _Host(dispatcher, _make_loop_config(max_attempts=1))

        result = await host._execute_with_retry(
            task="t", fallback_chain=["a", "b"],
            routing_key="k", workdir=".", timeout=10,
            retry=False, trace_id="tr",
        )
        assert result is not None and result.is_success()
        assert dispatcher.dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_all_agents_fail(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=False))
        host = _Host(dispatcher, _make_loop_config(max_attempts=1))

        result = await host._execute_with_retry(
            task="t", fallback_chain=["a", "b"],
            routing_key="k", workdir=".", timeout=10,
            retry=False, trace_id="tr",
        )
        assert result is not None and not result.is_success()

    @pytest.mark.asyncio
    async def test_empty_chain_returns_all_failed(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config())

        result = await host._execute_with_retry(
            task="t", fallback_chain=[],
            routing_key="k", workdir=".", timeout=10,
            retry=False, trace_id="tr",
        )
        # No agents → result constructed with "All agents failed".
        assert result is not None and not result.is_success()
        assert dispatcher.dispatch.await_count == 0

    @pytest.mark.asyncio
    async def test_retry_false_single_iteration(self):
        # retry=False → max_iterations=1, no iterative retry even on failure.
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=False))
        host = _Host(dispatcher, _make_loop_config(max_attempts=5))

        await host._execute_with_retry(
            task="t", fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10,
            retry=False, trace_id="tr",
        )
        assert dispatcher.dispatch.await_count == 1

    @pytest.mark.asyncio
    async def test_breaker_tripped_breaks_retry(self):
        # breaker_tripped=True should break out of iterative retry loop early.
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(
            return_value=_make_dispatch_result(success=False, breaker=True)
        )
        host = _Host(dispatcher, _make_loop_config(max_attempts=5))

        await host._execute_with_retry(
            task="t", fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10,
            retry=True, trace_id="tr",
        )
        assert dispatcher.dispatch.await_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_exception_constructs_failure(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        host = _Host(dispatcher, _make_loop_config(max_attempts=1))

        result = await host._execute_with_retry(
            task="t", fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10,
            retry=False, trace_id="tr",
        )
        assert result is not None and not result.is_success()


# ── _execute_with_strategy ───────────────────────────────────────────

class TestExecuteWithStrategy:
    @pytest.mark.asyncio
    async def test_no_analysis_falls_to_sequential(self):
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config())

        result = await host._execute_with_strategy(
            task="t", analysis=None,
            fallback_chain=["a"], routing_key="k",
            workdir=".", timeout=10, retry=True, trace_id="tr",
        )
        assert result is not None and result.is_success()

    @pytest.mark.asyncio
    async def test_sequential_strategy_falls_to_sequential(self):
        analysis = AnalysisResult(strategy=ExecutionStrategy.SEQUENTIAL)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config())

        result = await host._execute_with_strategy(
            task="t", analysis=analysis,
            fallback_chain=["a"], routing_key="k",
            workdir=".", timeout=10, retry=True, trace_id="tr",
        )
        assert result is not None and result.is_success()


# ── _execute_parallel ───────────────────────────────────────────────

def _make_analysis(nodes, edges, sub_tasks):
    return AnalysisResult(
        strategy=ExecutionStrategy.PARALLEL,
        sub_tasks=sub_tasks,
        dag=DependencyDAG(nodes=nodes, edges=edges),
    )


class TestExecuteParallel:
    @pytest.mark.asyncio
    async def test_parallel_group_both_success(self):
        sub_tasks = [
            SubTask(id="st-000", description="do a", assigned_agent="claude"),
            SubTask(id="st-001", description="do b", assigned_agent="gemini"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        pool = MagicMock(_sem=__import__("asyncio").Semaphore(2))
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=pool)

        result = await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["claude"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr",
        )
        assert result is not None and result.is_success()

    @pytest.mark.asyncio
    async def test_parallel_group_reconciles_each_subtask_not_the_aggregate(self):
        # GAP-5：并行聚合结果用 new_result 合成，不带 model/usage——修复前整批
        # 子任务的派发在成本表里一行都不留。对账必须在每笔真实派发上发生一次。
        sub_tasks = [
            SubTask(id="st-000", description="do a", assigned_agent="claude"),
            SubTask(id="st-001", description="do b", assigned_agent="gemini"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=[
            _make_dispatch_result(success=True, agent="claude"),
            _make_dispatch_result(success=True, agent="gemini"),
        ])
        pool = MagicMock(_sem=__import__("asyncio").Semaphore(2))
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=pool)

        result = await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["claude"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr",
        )
        assert len(host.reconciled) == 2, host.reconciled
        # 并行分支里两笔派发谁先落地不保证——按集合比 agent，按列表比 trace。
        assert sorted(agent for (agent, _tid) in host.reconciled) == ["claude", "gemini"]
        assert [tid for (_agent, tid) in host.reconciled] == ["tr", "tr"]
        # 聚合结果仍不带 model —— _phase_execute 的那一次对账对它是空操作，
        # 若这里改成透传 model 就会把两笔子任务成本再记一遍。
        assert result is not None and result.model is None

    @pytest.mark.asyncio
    async def test_sequential_group_chain_reconciles_each_subtask(self):
        # 单节点分支（每组一个子任务）同样逐笔对账，不能只靠聚合那一次。
        sub_tasks = [
            SubTask(id="st-000", description="first", assigned_agent="a1"),
            SubTask(id="st-001", description="second", assigned_agent="a2"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [("st-000", "st-001")], sub_tasks)
        dispatcher = MagicMock()

        async def _echo(**kwargs):
            return _make_dispatch_result(success=True, agent=str(kwargs.get("agent", "")))

        dispatcher.dispatch = AsyncMock(side_effect=_echo)
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=MagicMock())

        await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr2",
        )
        assert [agent for (agent, _tid) in host.reconciled] == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_sequential_groups_single_subtask(self):
        sub_tasks = [
            SubTask(id="st-000", description="first"),
            SubTask(id="st-001", description="second"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [("st-000", "st-001")], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=MagicMock())

        result = await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr",
        )
        assert result is not None and result.is_success()

    @pytest.mark.asyncio
    async def test_subtask_exception_yields_failure(self):
        sub_tasks = [SubTask(id="st-000", description="boom")]
        analysis = _make_analysis(["st-000"], [], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("kaboom"))
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=MagicMock())

        result = await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr",
        )
        assert result is not None and not result.is_success()

    @pytest.mark.asyncio
    async def test_partial_failure_marks_unsuccessful(self):
        sub_tasks = [
            SubTask(id="st-000", description="ok"),
            SubTask(id="st-001", description="bad"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=[
            _make_dispatch_result(success=True),
            _make_dispatch_result(success=False),
        ])
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=MagicMock())

        result = await host._execute_parallel(
            task="t", analysis=analysis, fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10, trace_id="tr",
        )
        assert result is not None and not result.is_success()

    @pytest.mark.asyncio
    async def test_strategy_dispatches_to_parallel(self):
        """_execute_with_strategy routes to _execute_parallel when conditions hold."""
        sub_tasks = [
            SubTask(id="st-000", description="a"),
            SubTask(id="st-001", description="b"),
        ]
        analysis = _make_analysis(["st-000", "st-001"], [], sub_tasks)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config(max_workers=2), worker_pool=MagicMock())

        result = await host._execute_with_strategy(
            task="t", analysis=analysis, fallback_chain=["a"],
            routing_key="k", workdir=".", timeout=10, retry=True, trace_id="tr",
        )
        assert result is not None and result.is_success()

    @pytest.mark.asyncio
    async def test_parallel_without_worker_pool_falls_to_sequential(self):
        # PARALLEL strategy but no worker_pool → sequential fallback.
        analysis = AnalysisResult(strategy=ExecutionStrategy.PARALLEL)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=_make_dispatch_result(success=True))
        host = _Host(dispatcher, _make_loop_config(), worker_pool=None)

        result = await host._execute_with_strategy(
            task="t", analysis=analysis,
            fallback_chain=["a"], routing_key="k",
            workdir=".", timeout=10, retry=True, trace_id="tr",
        )
        assert result is not None and result.is_success()