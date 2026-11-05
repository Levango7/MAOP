"""GAP-5 回归：并行子任务的派发必须逐笔进成本表。

修复前 ``ExecuteMixin._execute_parallel`` 只返回一个 ``new_result`` 聚合体，
其中 ``model``/``usage`` 都是空的，而 ``PhasesMixin._phase_execute`` 是唯一的
成本对账点——``_budget_reconciliation`` 见到 ``model`` 为空即早退
（``maop_loop_phases.py``）。于是"任务文本被子任务化"这一条路径上，
N 笔真实派发的成本在账面上整体为 0 行。

触发形态与 M-SIM 的证据一致（``docs/audits/business-sim-m1-evidence.md``
GAP-5 节）：英文任务文本被 analyzer 拆成 st-000/st-001 两个子任务，
节点自身 ``exit_code=0/[OK]``，成本表却一行不记。
"""

from __future__ import annotations

import pytest

from maop.core.agent.analyzer import AnalysisResult, DependencyDAG, ExecutionStrategy, SubTask
from maop.core.reliability.error_schema import new_result


class _DispatchResult:
    def __init__(self, result) -> None:
        self.result = result
        self.breaker_tripped = False


class _ParallelDispatcher:
    """按被派发的 agent 回显一条带 model/usage 的成功结果。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch(self, **kwargs) -> _DispatchResult:
        agent = str(kwargs.get("agent", ""))
        self.calls.append(agent)
        result = new_result(
            agent=agent,
            task=str(kwargs.get("task", "")),
            exit_code=0,
            stdout="ok",
            trace_id=str(kwargs.get("trace_id", "")),
            model="m1",
            usage={
                "prompt_tokens": 100 + len(agent),
                "completion_tokens": 50,
                "total_tokens": 150 + len(agent),
            },
        )
        return _DispatchResult(result)


def _loop(tmp_path):
    from maop.maop_loop import LoopConfig, MaopLoop

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (config_dir / "models.yaml").write_text("models: {}\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    lc = LoopConfig(
        enable_semantic_analyze=False,
        enable_parallel=True,
        enable_load_balancer=False,
        enable_result_cache=False,
        enable_metrics=False,
        enable_timeseries=False,
        enable_evolve=False,
        enable_dream=False,
        enable_cache_guard=False,
        iterative_max_attempts=1,
        iterative_backoff_ms=0,
        retry_backoff_ms=0,
        max_workers=2,
        default_timeout_s=5,
    )
    return MaopLoop(root_dir=str(tmp_path), loop_config=lc)


def _two_subtask_analysis() -> AnalysisResult:
    return AnalysisResult(
        strategy=ExecutionStrategy.PARALLEL,
        sub_tasks=[
            SubTask(id="st-000", description="reset session", assigned_agent="pa"),
            SubTask(id="st-001", description="verify refresh chain", assigned_agent="pb"),
        ],
        dag=DependencyDAG(nodes=["st-000", "st-001"], edges=[]),
    )


@pytest.mark.asyncio
async def test_parallel_subtasks_each_record_one_cost_row(tmp_path):
    from maop.core.cost_tracker import CostTracker

    loop = _loop(tmp_path)
    dispatcher = _ParallelDispatcher()
    loop._dispatcher = dispatcher

    result = await loop._execute_parallel(
        task="Ticket 1042: reset session and verify refresh chain",
        analysis=_two_subtask_analysis(),
        fallback_chain=["pa"],
        routing_key="",
        workdir=str(tmp_path),
        timeout=5,
        trace_id="gap5-parallel",
    )

    assert result is not None and result.is_success()
    assert sorted(dispatcher.calls) == ["pa", "pb"]

    tracker = CostTracker(root_dir=str(tmp_path))
    rows = {
        agent: tracker.get_entries(agent=agent)
        for agent in ("pa", "pb")
    }
    assert [len(v) for v in rows.values()] == [1, 1], {k: len(v) for k, v in rows.items()}
    # 逐笔入账用各自的 usage，而不是聚合后丢空
    assert rows["pa"][0].prompt_tokens == 102
    assert rows["pb"][0].prompt_tokens == 102
    assert sum(e.total_tokens for v in rows.values() for e in v) == 304


@pytest.mark.asyncio
async def test_parallel_aggregate_does_not_add_a_second_cost_row(tmp_path):
    """聚合体只是展示层结果——成本已由子任务各自记清，不能再记第二次。"""
    from maop.core.cost_tracker import CostTracker

    loop = _loop(tmp_path)
    loop._dispatcher = _ParallelDispatcher()

    result = await loop._execute_parallel(
        task="t", analysis=_two_subtask_analysis(), fallback_chain=["pa"],
        routing_key="", workdir=str(tmp_path), timeout=5, trace_id="gap5-aggregate",
    )
    assert result is not None and result.model is None

    # 复现修复前的调用序：聚合结果再走一次对账入口，不应产生新行。
    loop._budget_reconciliation(result, "gap5-aggregate")
    tracker = CostTracker(root_dir=str(tmp_path))
    assert len(tracker.get_entries(agent="pa")) == 1
    assert len(tracker.get_entries(agent="pb")) == 1
