"""GAP-5 回归：并行聚合结果必须透传 model/usage（成本台账不再静默缺行）.

根因（M-SIM M1 Temporal 腿，2026-11-05）：任务文本触发 analyzer 拆子任务走
并行路径时，``_execute_parallel`` 的聚合 ``new_result`` 不带 model/usage ⇒
``_budget_reconciliation`` 早退 ⇒ ``run_events`` 无 ``run.cost``、成本表静默
缺行。修复 = 聚合结果 model 取首个非空子任务值、usage 按键求和（并累计进
loop 级 ``_tokens_used_measured``，与串行路径口径一致）；对账入口硬化为
「有实测 usage 就记账（model 留空同启发式路径归档），两者皆缺才早退」。
"""

from __future__ import annotations

import pytest

from maop.core.agent.analyzer import AnalysisResult, DependencyDAG, SubTask
from maop.core.reliability.error_schema import new_result
from maop.loop_executor import ExecuteMixin
from maop.loop_models import LoopConfig


class _Harness:
    """最小宿主：只提供 ``_execute_parallel`` 触及的属性与方法。"""

    def __init__(self, results_by_task: dict[str, object]) -> None:
        self._loop_config = LoopConfig()
        self._worker_pool = None
        self._tokens_used_measured = 0
        self._results_by_task = results_by_task

    async def _execute_with_retry(self, task: str, fallback_chain: list[str], **kwargs: object):
        result = self._results_by_task[task]
        assert fallback_chain == ["sim-worker"], fallback_chain
        return result

    def _log(self, *args: object, **kwargs: object) -> None:
        return None


def _harness_with_mixin(results_by_task: dict[str, object]) -> object:
    """把 ExecuteMixin 的 ``_execute_parallel`` 挂到最小宿主上。

    MRO 注意：``_Harness`` 必须在 ``ExecuteMixin`` 之前——宿主的
    ``_execute_with_retry`` stub 要盖掉 mixin 的真实现（真实现需要完整
    dispatcher 装配，与本测试无关）。
    """
    return type("_ProbeExecutor", (_Harness, ExecuteMixin), {})(results_by_task)


def _two_subtask_analysis() -> AnalysisResult:
    return AnalysisResult(
        task="Ticket 1042 resolve",
        sub_tasks=[
            SubTask(id="0", description="reset session", assigned_agent="sim-worker"),
            SubTask(id="1", description="verify refresh chain", assigned_agent="sim-worker"),
        ],
        dag=DependencyDAG(nodes=["0", "1"], edges=[]),
    )


@pytest.mark.asyncio
async def test_parallel_combine_preserves_model_and_sums_usage() -> None:
    """聚合结果 model 取首个非空子任务值；usage 按键求和；累计进实测口径。"""
    results = {
        "reset session": new_result(
            agent="sim-worker", task="reset session", exit_code=0,
            stdout="ok-0", model="auto",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        ),
        "verify refresh chain": new_result(
            agent="sim-worker", task="verify refresh chain", exit_code=0,
            stdout="ok-1", model="auto",
            usage={"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260},
        ),
    }
    executor = _harness_with_mixin(results)
    combined = await executor._execute_parallel(
        task="Ticket 1042 resolve: reset session and verify refresh chain",
        analysis=_two_subtask_analysis(),
        fallback_chain=["sim-worker"],
        routing_key="chat", workdir=None, timeout=10,
        trace_id="gap5-unit", dry_run=False,
    )
    assert combined.model == "auto"
    assert combined.usage == {
        "prompt_tokens": 300,
        "completion_tokens": 110,
        "total_tokens": 410,
    }
    # loop 级实测口径与串行路径一致（[USAGE] 行不再回落 estimated/0）
    assert executor._tokens_used_measured == 410
    assert combined.is_success()


@pytest.mark.asyncio
async def test_parallel_combine_without_usage_stays_none() -> None:
    """全部子任务无实测 → 聚合 usage 保持 None（不冒充实测），model 照传。"""
    results = {
        "reset session": new_result(agent="sim-worker", task="reset session",
                                    exit_code=0, stdout="ok-0", model="auto"),
        "verify refresh chain": new_result(agent="sim-worker", task="verify refresh chain",
                                           exit_code=0, stdout="ok-1", model="auto"),
    }
    executor = _harness_with_mixin(results)
    combined = await executor._execute_parallel(
        task="t", analysis=_two_subtask_analysis(),
        fallback_chain=["sim-worker"], routing_key="chat",
        workdir=None, timeout=10, trace_id="gap5-unit2", dry_run=False,
    )
    assert combined.model == "auto"
    assert combined.usage is None
    assert executor._tokens_used_measured == 0


def test_budget_reconciliation_records_usage_without_model(tmp_path) -> None:
    """对账入口硬化：model 空 + 实测 usage 在手 ⇒ 照样落账（GAP-5 静默缺行）。"""
    import sqlite3
    from types import SimpleNamespace

    from maop.core.backends.db_utils import get_db_path
    from maop.maop_loop_phases import PhasesMixin

    # _budget_reconciliation 只读 self._root 与两个入参——宿主 stub 带上
    # _root（隔离 tmp），用未绑定调用即可（不需要完整 loop 宿主）。
    host = SimpleNamespace(_root=str(tmp_path))
    result = SimpleNamespace(
        model=None,
        usage={"prompt_tokens": 218, "completion_tokens": 146, "total_tokens": 364},
        task="Ticket 1042 resolve: reset session and verify refresh chain",
        agent="sim-worker",
        stdout="ok",
    )
    PhasesMixin._budget_reconciliation(host, result, "gap5-trace-rec")
    db = get_db_path("cost_tracker")
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT agent, prompt_tokens, completion_tokens, total_tokens "
            "FROM cost_entries WHERE agent = 'sim-worker' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert rows, "usage 在手时对账必须落行"
    _agent, tin, tout, total = rows[0]
    assert (tin, tout, total) == (218, 146, 364)
