"""MAOP Agent Collaboration — 多 Agent 协作编排器.

将复杂任务分解为多个步骤，按协作模式分配给不同类型的 Agent 执行，
聚合各步骤结果。支持依赖管理（有依赖串行、无依赖并行）、可选步骤
（失败不阻断）、任务模板变量替换、基于能力的 Agent 自动选择。

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
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
)
from maop.core.agent.router.agent_router import (
    AgentRouter,
    RoutingContext,
    RoutingStrategy,
)

logger = logging.getLogger(__name__)

# Agent 执行器类型：(agent_name, task, context) -> result_str
# 调用方可注入真实执行器（如 AgentProxy.call）；未注入时使用默认模拟执行器。
AgentExecutor = Callable[[str, str, dict[str, Any]], str]


# ── Pydantic 模型 ─────────────────────────────────────────────────
class CollaborationStep(BaseModel):
    """协作步骤定义.

    一个步骤代表由单个 Agent 执行的子任务。步骤之间通过 ``depends_on``
    声明依赖关系：有依赖的步骤等依赖完成后再执行，无依赖的步骤可并行。

    Attributes
    ----------
    name : str
        步骤名称（在同一 Pattern 内唯一，用于 depends_on 引用）。
    agent_name : str
        执行该步骤的 Agent 名称。为空时按 ``capabilities_required``
        自动选择（via AgentRouter）。
    task_template : str
        任务模板字符串，支持 ``{prev_result}`` / ``{original_task}`` /
        ``{step_result:xxx}`` 变量替换。
    depends_on : list[str]
        依赖步骤名称列表。空列表表示无依赖（可与其他无依赖步骤并行）。
    capabilities_required : list[str]
        需要的 Agent 能力（用于自动选择 Agent）。每项为 AgentCapability 枚举值。
    optional : bool
        是否可选。可选步骤失败不阻断后续步骤；必需步骤失败中止整个协作。
    """

    name: str = Field(..., min_length=1, description="步骤名称（Pattern 内唯一）")
    agent_name: str = Field(default="", description="执行 Agent 名（空则自动选择）")
    task_template: str = Field(..., description="任务模板（支持变量替换）")
    depends_on: list[str] = Field(
        default_factory=list, description="依赖步骤名列表（空表示无依赖，可并行）",
    )
    capabilities_required: list[str] = Field(
        default_factory=list, description="需要的能力（用于自动选择 Agent）",
    )
    optional: bool = Field(default=False, description="是否可选（失败不阻断）")


class CollaborationPattern(BaseModel):
    """协作模式定义.

    一个模式包含一组有序/并行的步骤，描述多 Agent 协作完成任务的流程。

    Attributes
    ----------
    name : str
        模式名称。
    description : str
        模式描述（供展示与文档）。
    steps : list[CollaborationStep]
        步骤列表。步骤之间的执行顺序由 ``depends_on`` 决定。
    """

    name: str = Field(..., min_length=1, description="模式名称")
    description: str = Field(default="", description="模式描述")
    steps: list[CollaborationStep] = Field(
        default_factory=list, description="步骤列表",
    )


class StepResult(BaseModel):
    """单步执行结果.

    Attributes
    ----------
    step_name : str
        步骤名称。
    agent_name : str
        实际执行的 Agent 名称。
    success : bool
        是否成功。
    result : str
        结果文本（成功时为 Agent 输出，失败时可能为空）。
    error : str
        错误信息（失败时填充）。
    duration_s : float
        执行耗时（秒）。
    """

    step_name: str = Field(..., description="步骤名称")
    agent_name: str = Field(..., description="实际执行的 Agent 名称")
    success: bool = Field(..., description="是否成功")
    result: str = Field(default="", description="结果文本")
    error: str = Field(default="", description="错误信息")
    duration_s: float = Field(default=0.0, ge=0.0, description="执行耗时（秒）")


class CollaborationResult(BaseModel):
    """协作聚合结果.

    Attributes
    ----------
    success : bool
        整体是否成功（所有必需步骤成功）。
    final_result : str
        聚合后的最终结果文本。
    step_results : list[StepResult]
        各步骤执行结果（按执行顺序）。
    total_duration_s : float
        总耗时（秒）。
    """

    success: bool = Field(..., description="整体是否成功")
    final_result: str = Field(default="", description="聚合最终结果")
    step_results: list[StepResult] = Field(
        default_factory=list, description="各步骤执行结果",
    )
    total_duration_s: float = Field(default=0.0, ge=0.0, description="总耗时（秒）")


# ── 默认执行器 ────────────────────────────────────────────────────
def _default_executor(agent_name: str, task: str, context: dict[str, Any]) -> str:
    """默认模拟执行器.

    不调用真实 Agent，直接返回一个包含 Agent 名和任务的模拟结果字符串。
    供测试与无真实 Agent 环境使用。生产环境应注入真实执行器。

    Parameters
    ----------
    agent_name : str
        Agent 名称。
    task : str
        任务描述。
    context : dict
        执行上下文（本默认执行器不使用，仅为接口对齐）。

    Returns
    -------
    str
        模拟结果：``[agent_name] executed: <task>``。
    """
    return f"[{agent_name}] executed: {task}"


# ── AgentCollaboration 编排器 ─────────────────────────────────────
class AgentCollaboration:
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

    # ── 单步执行 ──────────────────────────────────────────────────
    def _execute_step(
        self,
        step: CollaborationStep,
        context: dict[str, Any],
    ) -> StepResult:
        """执行单个协作步骤.

        流程：
          1. 解析任务模板（替换 ``{prev_result}`` / ``{original_task}`` /
             ``{step_result:xxx}``）
          2. 选择 Agent：``agent_name`` 非空直接用；否则按
             ``capabilities_required`` 通过 AgentRouter 自动选择
          3. 调用执行器执行任务
          4. 计费（若配置了 billing_engine）
          5. 返回 StepResult

        Parameters
        ----------
        step : CollaborationStep
            步骤定义。
        context : dict
            执行上下文（含 ``original_task`` / ``prev_result`` /
            ``step_results_map``）。

        Returns
        -------
        StepResult
            步骤执行结果。
        """
        start = time.monotonic()

        # 1. 解析任务模板
        try:
            resolved_task = self._resolve_template(step.task_template, context)
        except Exception as exc:
            return StepResult(
                step_name=step.name,
                agent_name=step.agent_name or "<auto>",
                success=False,
                error=f"template resolution failed: {exc}",
                duration_s=time.monotonic() - start,
            )

        # 2. 选择 Agent
        agent_name = step.agent_name
        if not agent_name:
            # 按 capabilities_required 自动选择
            agent_name = self._auto_select_agent(step, context)
            if not agent_name:
                return StepResult(
                    step_name=step.name,
                    agent_name="<auto>",
                    success=False,
                    error=(
                        f"no agent selected for step {step.name!r}: "
                        f"agent_name empty and auto-select failed "
                        f"(capabilities_required={step.capabilities_required})"
                    ),
                    duration_s=time.monotonic() - start,
                )

        # 校验 Agent 是否在 catalog 中注册
        descriptor = self._catalog.get(agent_name)
        if descriptor is None:
            return StepResult(
                step_name=step.name,
                agent_name=agent_name,
                success=False,
                error=f"agent {agent_name!r} not found in catalog",
                duration_s=time.monotonic() - start,
            )

        # 3. 执行任务
        try:
            result_str = self._executor(agent_name, resolved_task, context)
            success = True
            error = ""
        except Exception as exc:
            result_str = ""
            success = False
            error = f"executor raised: {exc}"
            logger.warning(
                "[collaboration] step %r executor failed: %s", step.name, exc,
            )

        # 4. 计费（成功时）
        if success and self._billing_engine is not None:
            try:
                self._billing_engine.charge(agent_name, calls=1)
            except Exception as exc:
                # 计费失败不应影响执行结果
                logger.warning(
                    "[collaboration] billing failed for step %r agent %r: %s",
                    step.name, agent_name, exc,
                )

        duration = time.monotonic() - start
        return StepResult(
            step_name=step.name,
            agent_name=agent_name,
            success=success,
            result=result_str,
            error=error,
            duration_s=duration,
        )

    # ── 结果聚合 ──────────────────────────────────────────────────
    def _aggregate_results(
        self,
        step_results: list[StepResult],
    ) -> CollaborationResult:
        """聚合各步骤结果为协作结果.

        聚合策略：
          - ``success``：所有必需步骤成功（此处简化为所有步骤成功；
            可选步骤失败已在 execute_custom 中处理不中止，但此处
            仍将其计入 success 判定——若需区分，可扩展 StepResult
            添加 ``required`` 字段）。
          - ``final_result``：拼接所有成功步骤的结果（按执行顺序）。
          - ``total_duration_s``：所有步骤耗时之和（不含批次间等待，
            因当前为顺序执行）。

        Parameters
        ----------
        step_results : list[StepResult]
            各步骤执行结果（按执行顺序）。

        Returns
        -------
        CollaborationResult
            聚合后的协作结果。
        """
        # success: 所有步骤成功（可选步骤失败也会使 overall success=False，
        # 但 execute_custom 中可选失败不中止后续；此处 success 反映
        # 是否有任一步骤失败）
        success = all(r.success for r in step_results)

        # final_result: 拼接成功步骤的结果
        parts = [r.result for r in step_results if r.success and r.result]
        final_result = "\n---\n".join(parts) if parts else ""

        total_duration = sum(r.duration_s for r in step_results)

        return CollaborationResult(
            success=success,
            final_result=final_result,
            step_results=step_results,
            total_duration_s=total_duration,
        )

    # ── 任务模板解析 ──────────────────────────────────────────────
    @staticmethod
    def _resolve_template(template: str, context: dict[str, Any]) -> str:
        """解析任务模板，替换变量.

        支持的变量：
          - ``{prev_result}``     — 上一个成功步骤的结果
          - ``{original_task}``   — 原始任务描述
          - ``{step_result:xxx}`` — 指定步骤 xxx 的结果

        Parameters
        ----------
        template : str
            任务模板字符串。
        context : dict
            执行上下文。

        Returns
        -------
        str
            替换后的任务字符串。
        """
        result = template
        prev = context.get("prev_result", "")
        original = context.get("original_task", "")
        result = result.replace("{prev_result}", prev)
        result = result.replace("{original_task}", original)

        # 替换 {step_result:xxx}
        step_map: dict[str, StepResult] = context.get("step_results_map", {})
        # 使用正则匹配 {step_result:xxx} 模式
        def _replace_step_result(match: re.Match[str]) -> str:
            step_name = match.group(1)
            sr = step_map.get(step_name)
            return sr.result if sr and sr.success else ""

        result = re.sub(r"\{step_result:([^}]+)\}", _replace_step_result, result)
        return result

    # ── Agent 自动选择 ────────────────────────────────────────────
    def _auto_select_agent(
        self,
        step: CollaborationStep,
        context: dict[str, Any],
    ) -> str:
        """按 ``capabilities_required`` 自动选择 Agent.

        将 ``capabilities_required``（字符串列表）转换为
        ``AgentCapability`` 枚举，构造 ``RoutingContext`` 并调用
        ``AgentRouter.route`` 选择最优 Agent。

        Parameters
        ----------
        step : CollaborationStep
            步骤定义（取 ``capabilities_required``）。
        context : dict
            执行上下文（当前未使用，预留扩展）。

        Returns
        -------
        str
            选中的 Agent 名称。无匹配时返回空字符串。
        """
        if not step.capabilities_required:
            return ""
        # 转换能力字符串为枚举
        caps: list[AgentCapability] = []
        for cap_str in step.capabilities_required:
            try:
                caps.append(AgentCapability(cap_str))
            except ValueError:
                logger.warning(
                    "[collaboration] unknown capability %r in step %r, skipping",
                    cap_str, step.name,
                )
        if not caps:
            return ""
        ctx = RoutingContext(required_capabilities=caps)
        result = self._router.route(ctx, strategy=RoutingStrategy.CAPABILITY_MATCH)
        if result.primary is not None:
            return result.primary.name
        return ""

    # ── 拓扑排序（分批）────────────────────────────────────────────
    @staticmethod
    def _topological_batches(
        steps: list[CollaborationStep],
    ) -> list[list[CollaborationStep]]:
        """将步骤按依赖关系拓扑排序，分批返回.

        同一批次内的步骤无相互依赖，可并行执行。批次之间有依赖关系，
        必须按顺序执行。

        算法：Kahn 算法分层拓扑排序。
          1. 构建步骤名 → 步骤的映射
          2. 计算每个步骤的入度（依赖数）
          3. 入度为 0 的步骤入第一批
          4. 移除该批步骤后，更新剩余步骤入度，入度为 0 的入下一批
          5. 重复直到所有步骤分配完毕

        依赖指向不存在的步骤名时，视为该依赖已满足（容错处理，
        避免因拼写错误导致死锁）。

        Parameters
        ----------
        steps : list[CollaborationStep]
            步骤列表。

        Returns
        -------
        list[list[CollaborationStep]]
            按执行顺序的批次列表。每批内步骤可并行。
        """
        # 步骤名 → 步骤
        step_map = {s.name: s for s in steps}
        # 步骤名 → 依赖步骤名列表（仅保留存在的依赖）
        deps: dict[str, set[str]] = {}
        for s in steps:
            valid_deps = {d for d in s.depends_on if d in step_map and d != s.name}
            deps[s.name] = valid_deps

        # 入度 = 依赖数
        in_degree = {name: len(d) for name, d in deps.items()}
        # 反向邻接表：依赖 → 依赖它的步骤
        dependents: dict[str, list[str]] = {name: [] for name in step_map}
        for name, d in deps.items():
            for dep in d:
                dependents[dep].append(name)

        batches: list[list[CollaborationStep]] = []
        remaining = set(step_map.keys())

        while remaining:
            # 当前批次：入度为 0 的剩余步骤
            batch_names = [n for n in remaining if in_degree[n] == 0]
            if not batch_names:
                # 存在循环依赖；将剩余步骤全部放入最后一批（容错）
                logger.warning(
                    "[collaboration] circular dependency detected among %s; "
                    "forcing remaining steps into one batch",
                    sorted(remaining),
                )
                batch_names = list(remaining)

            # 按步骤在原列表中的顺序排序，保证确定性
            order_index = {s.name: i for i, s in enumerate(steps)}
            batch_names.sort(key=lambda n: order_index.get(n, 0))
            batch = [step_map[n] for n in batch_names]
            batches.append(batch)

            # 移除该批步骤，更新入度
            for n in batch_names:
                remaining.discard(n)
                for dependent in dependents[n]:
                    in_degree[dependent] -= 1

        return batches


# ── 预定义协作模式 ────────────────────────────────────────────────
def pattern_init_and_implement() -> CollaborationPattern:
    """预定义模式：项目初始化 + 功能实现.

    流程：
      1. ``init_skeleton`` — Vibe 平台生成项目骨架（CODE_GENERATION）
      2. ``implement_features`` — CLI Agent 实现具体功能（CODE_GENERATION + TOOL_USE）
         依赖 ``init_skeleton``
      3. ``polish_details`` — IDE 插件补全细节（FILE_EDIT）
         依赖 ``implement_features``

    Returns
    -------
    CollaborationPattern
        "项目初始化+功能实现" 协作模式。
    """
    return CollaborationPattern(
        name="init_and_implement",
        description="项目初始化+功能实现：Vibe平台生成骨架 → CLI实现功能 → IDE插件补全",
        steps=[
            CollaborationStep(
                name="init_skeleton",
                agent_name="",
                task_template="使用 Vibe 平台生成项目骨架，任务：{original_task}",
                capabilities_required=[AgentCapability.CODE_GENERATION.value],
            ),
            CollaborationStep(
                name="implement_features",
                agent_name="",
                task_template=(
                    "基于已生成的项目骨架实现具体功能。\n"
                    "骨架信息：\n{prev_result}\n"
                    "原始任务：{original_task}"
                ),
                depends_on=["init_skeleton"],
                capabilities_required=[
                    AgentCapability.CODE_GENERATION.value,
                    AgentCapability.TOOL_USE.value,
                ],
            ),
            CollaborationStep(
                name="polish_details",
                agent_name="",
                task_template=(
                    "补全项目细节（类型标注、文档、导入整理等）。\n"
                    "当前代码：\n{prev_result}"
                ),
                depends_on=["implement_features"],
                capabilities_required=[AgentCapability.FILE_EDIT.value],
            ),
        ],
    )


def pattern_review_and_fix() -> CollaborationPattern:
    """预定义模式：代码审查 + 修复.

    流程：
      1. ``review_code`` — 自主 Agent 审查代码（CODE_REVIEW + REASONING）
      2. ``fix_issues`` — CLI Agent 修复审查发现的问题（CODE_GENERATION + TOOL_USE）
         依赖 ``review_code``

    Returns
    -------
    CollaborationPattern
        "代码审查+修复" 协作模式。
    """
    return CollaborationPattern(
        name="review_and_fix",
        description="代码审查+修复：自主Agent审查 → CLI修复问题",
        steps=[
            CollaborationStep(
                name="review_code",
                agent_name="",
                task_template="审查以下代码并指出问题：\n{original_task}",
                capabilities_required=[
                    AgentCapability.CODE_REVIEW.value,
                    AgentCapability.REASONING.value,
                ],
            ),
            CollaborationStep(
                name="fix_issues",
                agent_name="",
                task_template=(
                    "修复代码审查发现的问题。\n"
                    "审查结果：\n{prev_result}\n"
                    "原始代码：\n{original_task}"
                ),
                depends_on=["review_code"],
                capabilities_required=[
                    AgentCapability.CODE_GENERATION.value,
                    AgentCapability.TOOL_USE.value,
                ],
            ),
        ],
    )


def pattern_parallel_and_merge() -> CollaborationPattern:
    """预定义模式：并行处理 + 合并.

    流程：
      1. ``process_module_a`` — Agent A 处理模块 A（CODE_GENERATION）
      2. ``process_module_b`` — Agent B 处理模块 B（CODE_GENERATION）
      3. ``process_module_c`` — Agent C 处理模块 C（CODE_GENERATION）
         以上三步无依赖，可并行
      4. ``merge_results`` — 合并各模块结果（REASONING）
         依赖 ``process_module_a`` / ``process_module_b`` / ``process_module_c``

    Returns
    -------
    CollaborationPattern
        "并行处理+合并" 协作模式。
    """
    return CollaborationPattern(
        name="parallel_and_merge",
        description="并行处理+合并：多Agent并行处理不同模块 → 合并结果",
        steps=[
            CollaborationStep(
                name="process_module_a",
                agent_name="",
                task_template="处理模块 A，任务：{original_task}",
                capabilities_required=[AgentCapability.CODE_GENERATION.value],
            ),
            CollaborationStep(
                name="process_module_b",
                agent_name="",
                task_template="处理模块 B，任务：{original_task}",
                capabilities_required=[AgentCapability.CODE_GENERATION.value],
            ),
            CollaborationStep(
                name="process_module_c",
                agent_name="",
                task_template="处理模块 C，任务：{original_task}",
                capabilities_required=[AgentCapability.CODE_GENERATION.value],
            ),
            CollaborationStep(
                name="merge_results",
                agent_name="",
                task_template=(
                    "合并以下模块结果：\n"
                    "模块 A：{step_result:process_module_a}\n"
                    "模块 B：{step_result:process_module_b}\n"
                    "模块 C：{step_result:process_module_c}"
                ),
                depends_on=["process_module_a", "process_module_b", "process_module_c"],
                capabilities_required=[AgentCapability.REASONING.value],
            ),
        ],
    )


# ── 预定义模式注册表 ──────────────────────────────────────────────
def list_predefined_patterns() -> list[CollaborationPattern]:
    """列出所有预定义协作模式.

    Returns
    -------
    list[CollaborationPattern]
        预定义模式列表。
    """
    return [
        pattern_init_and_implement(),
        pattern_review_and_fix(),
        pattern_parallel_and_merge(),
    ]


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