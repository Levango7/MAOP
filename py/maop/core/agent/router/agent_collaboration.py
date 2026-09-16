"""MAOP Agent Collaboration — 多 Agent 协作编排器.

将复杂任务分解为多个步骤，按协作模式分配给不同类型的 Agent 执行，
聚合各步骤结果。支持依赖管理（有依赖串行、无依赖并行）、可选步骤
（失败不阻断）、任务模板变量替换、基于能力的 Agent 自动选择。

本模块原为单文件实现，现按职责物理拆分为三个子模块（纯拆分，不改
任何方法签名与逻辑）：
  - ``collaboration_models.py``    — Pydantic 数据模型与默认执行器
  - ``collaboration_executor.py``  — 执行逻辑 Mixin（单步执行/聚合/模板/选择/拓扑）
  - ``collaboration_patterns.py``  — 预定义协作模式工厂函数

本模块仍保留 ``AgentCollaboration`` 编排器主类，并 re-export 所有模型
与模式函数，以保持 ``from maop.core.agent.router.agent_collaboration
import ...`` 的向后兼容。

核心组件：
  - ``CollaborationPattern``  — 协作模式（Pydantic 模型）
  - ``CollaborationStep``     — 协作步骤（Pydantic 模型）
  - ``StepResult``            — 单步执行结果（Pydantic 模型）
  - ``CollaborationResult``   — 协作聚合结果（Pydantic 模型）
  - ``AgentCollaboration``    — 协作编排器
  - ``pattern_init_and_implement``   — 预定义模式：项目初始化+功能实现
  - ``pattern_review_and_fix``       — 预定义模式：代码审查+修复
  - ``pattern_parallel_and_merge``   — 预定义模式：并行处理+合并

Usage::

    from maop.core.agent.router.agent_collaboration import (
        AgentCollaboration, pattern_init_and_implement,
    )

    collab = AgentCollaboration(catalog=catalog, router=router)
    pattern = pattern_init_and_implement()
    result = collab.execute_pattern(pattern, task="创建一个 Web 服务")
    if result.success:
        print(result.final_result)

任务模板变量：
  - ``{prev_result}``     — 上一个成功步骤的结果
  - ``{original_task}``   — 原始任务描述
  - ``{step_result:xxx}`` — 指定步骤 xxx 的结果
"""

from __future__ import annotations

import logging
import time
from typing import Any

from maop.core.agent.registry.agent_catalog import AgentCatalog
from maop.core.agent.router.agent_router import AgentRouter
from maop.core.agent.router.collaboration_executor import CollaborationExecutorMixin
from maop.core.agent.router.collaboration_models import (
    AgentExecutor,
    CollaborationPattern,
    CollaborationResult,
    CollaborationStep,
    StepResult,
    _default_executor,
)
from maop.core.agent.router.collaboration_patterns import (
    list_predefined_patterns,
    pattern_init_and_implement,
    pattern_parallel_and_merge,
    pattern_review_and_fix,
)

logger = logging.getLogger(__name__)


# ── AgentCollaboration 编排器 ─────────────────────────────────────
class AgentCollaboration(CollaborationExecutorMixin):
    """多 Agent 协作编排器.

    按协作模式（``CollaborationPattern``）分解任务、分配 Agent、
    管理依赖与并行执行、聚合结果。复用 ``AgentRouter`` 进行 Agent
    路由选择，复用 ``BillingEngine`` 进行计费。

    Parameters
    ----------
    catalog : AgentCatalog
        Agent 元数据注册中心（必须提供）。
    router : AgentRouter
        Agent 路由器（必须提供，用于按能力自动选择 Agent）。
    billing_engine : BillingEngine | None
        计费引擎。``None`` 时不计费。
    executor : AgentExecutor | None
        Agent 执行器回调。``None`` 时使用 ``_default_executor``。
        生产环境可注入 ``AgentProxy.call`` 等真实执行器。

    执行流程：
      1. 解析步骤依赖关系，构建执行批次（拓扑排序）
      2. 按批次执行：同批次内无依赖步骤并行（当前为顺序模拟并行）
      3. 每步：解析任务模板 → 选择 Agent → 执行 → 计费 → 记录结果
      4. 必需步骤失败立即中止；可选步骤失败记录后继续
      5. 聚合所有步骤结果
    """

    def __init__(
        self,
        catalog: AgentCatalog,
        router: AgentRouter,
        billing_engine: Any | None = None,
        executor: AgentExecutor | None = None,
    ) -> None:
        self._catalog: AgentCatalog = catalog
        self._router: AgentRouter = router
        self._billing_engine: Any | None = billing_engine
        self._executor: AgentExecutor = executor if executor is not None else _default_executor

    # ── 公共属性 ──────────────────────────────────────────────────
    @property
    def catalog(self) -> AgentCatalog:
        """底层 AgentCatalog（只读访问）。"""
        return self._catalog

    @property
    def router(self) -> AgentRouter:
        """底层 AgentRouter（只读访问）。"""
        return self._router

    @property
    def billing_engine(self) -> Any | None:
        """计费引擎（None 表示未启用计费）。"""
        return self._billing_engine

    @property
    def executor(self) -> AgentExecutor:
        """Agent 执行器回调。"""
        return self._executor

    # ── 模式执行 ──────────────────────────────────────────────────
    def execute_pattern(
        self,
        pattern: CollaborationPattern,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> CollaborationResult:
        """按协作模式执行任务.

        将 ``pattern.steps`` 按依赖关系排序后逐步执行，聚合结果。

        Parameters
        ----------
        pattern : CollaborationPattern
            协作模式定义。
        task : str
            原始任务描述（注入到各步骤的 ``{original_task}`` 变量）。
        context : dict | None
            初始上下文（可选）。执行过程中会追加 ``original_task`` 和
            各步骤结果。

        Returns
        -------
        CollaborationResult
            协作聚合结果。
        """
        ctx: dict[str, Any] = dict(context) if context else {}
        ctx["original_task"] = task
        ctx["step_results_map"] = {}  # step_name -> StepResult
        ctx["prev_result"] = ""

        # 空模式直接返回成功空结果
        if not pattern.steps:
            return CollaborationResult(
                success=True,
                final_result="",
                step_results=[],
                total_duration_s=0.0,
            )

        return self.execute_custom(pattern.steps, context=ctx)

    def execute_custom(
        self,
        steps: list[CollaborationStep],
        context: dict[str, Any] | None = None,
    ) -> CollaborationResult:
        """自定义步骤执行.

        与 ``execute_pattern`` 类似，但直接接收步骤列表，无需包装为 Pattern。
        供调用方灵活执行临时编排的步骤。

        Parameters
        ----------
        steps : list[CollaborationStep]
            步骤列表。
        context : dict | None
            初始上下文。应包含 ``original_task``（否则为空字符串）。

        Returns
        -------
        CollaborationResult
            协作聚合结果。
        """
        ctx: dict[str, Any] = dict(context) if context else {}
        # 确保上下文必要字段存在
        ctx.setdefault("original_task", "")
        ctx.setdefault("step_results_map", {})
        ctx.setdefault("prev_result", "")

        start_time = time.monotonic()
        step_results: list[StepResult] = []
        aborted = False

        # 按依赖拓扑排序得到执行批次
        batches = self._topological_batches(steps)

        for batch in batches:
            if aborted:
                break
            # 同批次内步骤无相互依赖，可并行
            # 当前实现为顺序执行（模拟并行语义，结果等价）
            for step in batch:
                result = self._execute_step(step, ctx)
                step_results.append(result)
                # 更新上下文
                ctx["step_results_map"][step.name] = result
                if result.success:
                    ctx["prev_result"] = result.result
                else:
                    # 必需步骤失败：中止整个协作
                    if not step.optional:
                        logger.warning(
                            "[collaboration] required step %r failed, aborting: %s",
                            step.name, result.error,
                        )
                        aborted = True
                        break
                    # 可选步骤失败：记录后继续
                    logger.info(
                        "[collaboration] optional step %r failed, continuing: %s",
                        step.name, result.error,
                    )

        total_duration = time.monotonic() - start_time
        return self._aggregate_results(step_results)


__all__ = [
    "AgentCollaboration",
    "AgentExecutor",
    "CollaborationPattern",
    "CollaborationResult",
    "CollaborationStep",
    "StepResult",
    "list_predefined_patterns",
    "pattern_init_and_implement",
    "pattern_parallel_and_merge",
    "pattern_review_and_fix",
]
