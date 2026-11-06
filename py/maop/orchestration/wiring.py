"""workflow 执行的 Temporal 接线层 —— M-X2 E2-a（用户批准，板面第十三轮）.

侦察实证后的落点（板面记录）：``maop_plan.execute_workflow`` 是**纯规划
合同**（"不执行任何 agent"，零生产调用方）、``maop_execute`` 在册孤儿——
今天不存在"整条 workflow 带 agent 执行"的生产入口。本模块即该入口：

- ``MAOP_ORCHESTRATION_BACKEND`` 空（默认）→ ``run_workflow`` 走 legacy
  规划路径（``maop_plan.execute_workflow``，**行为逐字节不变**，E1-a 的
  "flag 关零变化"）；
- ``"temporal"`` → 经 :class:`~maop.orchestration.temporal_adapter.TemporalAdapter`
  编译并提交，节点执行体 = E3-a 批准的 ``maop run`` **子进程**（与
  operator M3 同构：隔离、超时硬界、``[USAGE]`` 会计可后续复用）；
- 其他值 → 显式 :class:`ValueError`（不静默回退）。

不碰：engine.py / maop_plan.py / core/scheduling|reliability /
engine_utils（板面领地纪律）。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

from maop.config.loader import MaopConfig
from maop.config.settings import orchestration_backend
from maop.maop_plan import WorkflowResult
from maop.orchestration.base import CompiledWorkflow, WorkflowNode
from maop.orchestration.temporal_adapter import TemporalAdapter

#: 后端值 → 布线开关（E1-a：只有显式 "temporal" 才启用适配器）。
TEMPORAL_BACKEND = "temporal"


def resolve_backend(*, override: str = "") -> str:
    """后端决议：CLI/调用方可显式覆盖，否则读 settings（env 收敛点）.

    未知值抛 :class:`ValueError`（诚实报错，不静默回退 legacy）。
    """
    backend = (override or orchestration_backend()).strip().lower()
    if backend in ("", TEMPORAL_BACKEND):
        return backend
    raise ValueError(
        f"unknown MAOP_ORCHESTRATION_BACKEND {backend!r} "
        f"(supported: '' (legacy), '{TEMPORAL_BACKEND}')"
    )


def maop_run_executor(node: WorkflowNode) -> dict[str, Any]:
    """E3-a 默认节点执行体：``python -m maop run --task … --agent …`` 子进程.

    与 operator 的 ``AsyncSubprocessRunner`` 同构（M3 语义），但 M0 接线
    用同步 ``subprocess.run``——活动在 worker 线程内串行执行即可覆盖
    E2-a 验收面；并发活动场景的 to_thread 化属生产化小扩展（诚实边界，
    见 CHANGELOG）。走 ``sys.executable -m maop`` 而非 ``maop`` console
    script：宿主机上后者可能因 editable 安装元数据陈旧而静默 exit 1
    （E2-a 验收实撞，``python -m maop run`` 同机同参正常）。
    """
    command = [
        sys.executable, "-m", "maop", "run",
        "--agent", node.agent, "--task", node.task,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=node.timeout_s,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "step_id": node.step_id, "agent": node.agent,
            "status": "timed_out", "timeout_s": node.timeout_s,
        }
    status = "success" if completed.returncode == 0 else "failed"
    return {
        "step_id": node.step_id, "agent": node.agent,
        "status": status, "exit_code": completed.returncode,
    }


async def run_workflow(
    workflow_name: str,
    config: MaopConfig | None = None,
    initial_vars: dict[str, Any] | None = None,
    *,
    backend: str = "",
    adapter: Any = None,
    executor: Any = None,
    timeout_s: float | None = None,
) -> WorkflowResult:
    """workflow 执行的生产入口（后端决议见 :func:`resolve_backend`）.

    legacy（默认）→ ``maop_plan.execute_workflow``（纯规划，合同不变）；
    temporal → ``TemporalAdapter.compile_workflow`` + ``run``（executor
    注入，默认 :func:`maop_run_executor`），并把节点结果映射回
    :class:`~maop.plan.WorkflowResult`（``steps_planned``/``steps_completed``
    语义沿用：planned=编译节点数，completed=status=="success" 数）。
    """
    resolved = resolve_backend(override=backend)
    if resolved != TEMPORAL_BACKEND:
        # GAP-1 收口（2026-11-06）：legacy 后端自此**真实执行**（按编译层序
        # 逐层并行 dispatch 节点），不再只做规划。原「纯规划」语义保留在
        # ``maop_plan.execute_workflow``（公开 API，规划调用方继续用它）。
        if config is None or workflow_name not in config.workflows:
            result = WorkflowResult(
                workflow_name=workflow_name, variables=dict(initial_vars or {})
            )
            return result
        return await _run_legacy_execution(
            workflow_name, config.workflows[workflow_name], config,
            initial_vars, executor=executor,
        )

    if config is None or workflow_name not in config.workflows:
        result = WorkflowResult(
            workflow_name=workflow_name, variables=dict(initial_vars or {})
        )
        return result

    wf = config.workflows[workflow_name]
    chosen_adapter = adapter or TemporalAdapter()
    compiled: CompiledWorkflow = chosen_adapter.compile_workflow(
        wf, name=workflow_name
    )
    outcome = await chosen_adapter.run(
        compiled,
        executor=executor or maop_run_executor,
        timeout_s=timeout_s,
    )
    return _to_workflow_result(workflow_name, compiled, outcome, initial_vars)


async def _run_legacy_execution(
    workflow_name: str,
    wf: Any,
    config: MaopConfig,
    initial_vars: dict[str, Any] | None,
    *,
    executor: Any = None,  # 兼容签名；legacy 路径不调用（executor 来自 Temporal 路径）
) -> WorkflowResult:
    """legacy 后端的真实执行（GAP-1 收口，2026-11-06）.

    按编译层序逐层并行 dispatch 节点，每节点走 ``maop run`` 子进程
    （与 operator/Temporal 同一条执行体），节点结果聚合为
    ``WorkflowResult``，并把节点结果回写 ``run_events``
    （``run.workflow.step.started/completed/failed``），供面板按
    ``trace_id`` 把工作流事件与子 agent 的 run 事件同轴查看。
    """
    from maop.cli import _runtime_root  # 延迟导入：cli.py 是本模块的门面（re-export）
    from maop.core.run_events import record_run_event
    from maop.delegate.dispatcher import Dispatcher
    from maop.maop_plan import PlanStepResult

    trace_id = workflow_name
    compiled = TemporalAdapter().compile_workflow(wf, name=workflow_name)
    nodes_by_id = {n.step_id: n for n in compiled.nodes}
    dispatcher = Dispatcher(root_dir=str(_runtime_root()), maop_config=config)

    async def run_one(node: WorkflowNode) -> PlanStepResult:
        record_run_event(
            trace_id, "run.workflow.step.started",
            agent=node.agent, step=int(node.step_id),
            payload={"workflow": workflow_name, "node": node.step_id},
        )
        dispatch_result = await dispatcher.dispatch(
            agent=node.agent,
            task=node.task,
            routing_key="",
            workdir="",
            timeout_seconds=node.timeout_s,
            trace_id=trace_id,
            dry_run=False,
        )
        exec_result = dispatch_result.result
        status = "success" if exec_result.is_success() else "failed"
        summary = f"status={status}"
        if exec_result.exit_code is not None:
            summary += f" exit_code={exec_result.exit_code}"
        # 节点输出摘要随 step_result 带出（面板 workflow run 视图直接可见，
        # 不必再去翻子进程 stdout）。
        tail = (exec_result.stdout or "").strip().replace("\n", " ⏎ ")
        if tail:
            summary += f" output={tail[:200]}"
        record_run_event(
            trace_id, f"run.workflow.step.{status}",
            agent=node.agent, step=int(node.step_id),
            payload={
                "workflow": workflow_name, "node": node.step_id,
                "exit_code": exec_result.exit_code,
            },
        )
        return PlanStepResult(
            step_index=int(node.step_id),
            agent=node.agent,
            status=status,
            output=summary,
        )

    results: list[PlanStepResult] = []
    for group in compiled.levels:
        group_results = await asyncio.gather(
            *(run_one(nodes_by_id[st_id]) for st_id in group)
        )
        results.extend(group_results)

    return WorkflowResult(
        workflow_name=workflow_name,
        variables=dict(initial_vars or {}),
        steps_total=len(compiled.nodes),
        steps_planned=len(compiled.nodes),
        steps_completed=sum(1 for r in results if r.status == "success"),
        step_results=results,
    )


def _to_workflow_result(
    workflow_name: str,
    compiled: CompiledWorkflow,
    outcome: dict[str, Any],
    initial_vars: dict[str, Any] | None,
) -> WorkflowResult:
    """Temporal 终值 → WorkflowResult（沿用既有计数字段语义）.

    节点结果落 ``step_results``（:class:`~maop.maop_plan.PlanStepResult`，
    step_index 用编译索引，output 带 status/exit_code 摘要）。
    """
    from maop.maop_plan import PlanStepResult

    nodes_out: dict[str, dict[str, Any]] = outcome.get("nodes", {})
    results: list[PlanStepResult] = []
    for idx, node in enumerate(compiled.nodes):
        record = dict(nodes_out.get(node.step_id, {}))
        status = str(record.get("status", "missing"))
        summary = f"status={status}"
        if "exit_code" in record:
            summary += f" exit_code={record['exit_code']}"
        results.append(
            PlanStepResult(
                step_index=idx, agent=node.agent, status=status,
                output=summary,
            )
        )
    return WorkflowResult(
        workflow_name=workflow_name,
        variables=dict(initial_vars or {}),
        steps_total=len(compiled.nodes),
        steps_planned=len(compiled.nodes),
        steps_completed=sum(1 for r in results if r.status == "success"),
        step_results=results,
    )


def run_workflow_sync(
    workflow_name: str,
    config: MaopConfig | None = None,
    initial_vars: dict[str, Any] | None = None,
    **kwargs: Any,
) -> WorkflowResult:
    """CLI 面的同步包装（``maop workflow run`` 子命令用）."""
    return asyncio.run(run_workflow(workflow_name, config, initial_vars, **kwargs))
