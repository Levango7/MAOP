"""多后端编排器适配层 —— M-X2 M0（ROADMAP「M-X2 多后端编排器适配」章程）.

只定义**后端无关**的 adapter 接口、编译产物形状与共享 DAG 预处理
（拓扑分层 / 环检测 / 边界校验）；Temporal 实现见
``temporal_adapter.py``。temporalio 与 kopf/kubernetes 同例：**可选依赖，
不进 pyproject**（避 Lock drift 门与依赖膨胀），由运行环境显式安装。

M0 诚实边界（章程原文）：只证明「MAOP WorkflowDef DAG → 后端 workflow
定义」的编译与连通（demo workflow 在 Temporal dev server 跑通一次），
**不承诺生产集成**——企业侧运行时供给未定，生产接线属后续里程碑，
本包当前为 KNOWN_ORPHANS 在册的"先行定义"（口径见
``tests/test_dead_wiring_inventory.py``）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from maop.config.loader import WorkflowDef, WorkflowStepDef


class CompileError(ValueError):
    """WorkflowDef 无法编译为后端 workflow 定义（环 / 未知依赖 / 越界字段）。"""


@dataclass(frozen=True)
class WorkflowNode:
    """编译后的单节点（WorkflowStepDef 的后端无关投影）.

    ``step_id`` 沿用 WorkflowDef 既有契约：steps 按索引寻址（``"0"``/``"1"``…，
    ``engine_utils._topological_sort`` 同口径），``depends_on`` 引用即索引串。
    """

    step_id: str
    agent: str
    task: str
    timeout_s: int


@dataclass(frozen=True)
class CompiledWorkflow:
    """一次编译的产物：结构（分层）+ 后端特定定义文本.

    ``levels`` 是拓扑层序（层内节点可并行执行），后端实现按它生成执行体；
    ``artifact`` 是后端可加载的定义（Temporal：Python 源码文本）。M0 不做
    持久化——产物即用即弃，重编译幂等。
    """

    backend: str
    name: str
    nodes: tuple[WorkflowNode, ...]
    levels: tuple[tuple[str, ...], ...]
    artifact: str


def plan_levels(steps: list[WorkflowStepDef]) -> tuple[tuple[str, ...], ...]:
    """把 steps 按 ``depends_on`` 拓扑分层（层内可并行），含边界校验.

    与 ``engine_utils._topological_sort`` 同一契约（索引串寻址），但返回
    结构化层序并**提前**拒绝三种不可编译形态——编译期失败优于运行期失败：
    环、引用不存在步骤、``condition`` 非空（条件执行语义是 M0 明示边界，
    静默降级为"恒执行"会改变工作流含义——宁拒不昧）。
    """
    ids = [str(i) for i in range(len(steps))]
    id_set = set(ids)

    for i, step in enumerate(steps):
        for dep in step.depends_on:
            if dep not in id_set:
                raise CompileError(
                    f"step {i} depends_on unknown step id {dep!r} "
                    f"(valid: {sorted(id_set)})"
                )
        if step.condition:
            raise CompileError(
                f"step {i} carries condition {step.condition!r}: conditional "
                "execution is out of M0 scope (honest boundary) — remove the "
                "condition or extend the adapter"
            )

    remaining = dict(enumerate(steps))
    placed: set[str] = set()
    levels: list[tuple[str, ...]] = []
    while remaining:
        ready = sorted(
            str(i)
            for i, step in remaining.items()
            if set(step.depends_on) <= placed
        )
        if not ready:
            stuck = sorted(str(i) for i in remaining)
            raise CompileError(f"dependency cycle among steps {stuck}")
        levels.append(tuple(ready))
        placed |= set(ready)
        for rid in ready:
            del remaining[int(rid)]
    return tuple(levels)


def nodes_from_steps(steps: list[WorkflowStepDef]) -> tuple[WorkflowNode, ...]:
    """steps → 节点元组（索引串寻址，字段直取；无默认值改写）."""
    return tuple(
        WorkflowNode(
            step_id=str(i), agent=step.agent, task=step.task,
            timeout_s=step.timeout_s,
        )
        for i, step in enumerate(steps)
    )


class OrchestrationAdapter(Protocol):
    """后端编排器适配器协议（M0 章程"adapter 接口先行"的落点）.

    实现方约定：``compile_workflow`` 纯函数式（不触网、幂等）；``run``
    才允许连后端。``executor`` 是节点执行体注入点（M0 demo 用记录型
    executor；MAOP agent 调度接入属生产集成里程碑，不在 M0 承诺内）。
    """

    def compile_workflow(
        self, workflow: WorkflowDef, *, name: str = ""
    ) -> CompiledWorkflow: ...

    async def run(
        self,
        compiled: CompiledWorkflow,
        *,
        executor: Callable[[WorkflowNode], dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]: ...
