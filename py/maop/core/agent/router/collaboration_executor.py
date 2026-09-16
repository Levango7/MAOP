"""MAOP Agent Collaboration — 执行逻辑 Mixin.

本模块从 ``agent_collaboration.py`` 拆分而来，将协作编排的单步执行、
结果聚合、任务模板解析、Agent 自动选择与拓扑排序等执行逻辑提取为
``CollaborationExecutorMixin``。``AgentCollaboration`` 通过继承该 Mixin
获得这些方法，保持对外接口不变。

Mixin 依赖宿主类提供以下实例属性（由 ``AgentCollaboration.__init__`` 设置）：
  - ``_catalog``         — AgentCatalog 实例
  - ``_router``          — AgentRouter 实例
  - ``_billing_engine``  — 计费引擎或 None
  - ``_executor``        — Agent 执行器回调
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from maop.core.agent.registry.agent_catalog import AgentCapability
from maop.core.agent.router.agent_router import (
    RoutingContext,
    RoutingStrategy,
)
from maop.core.agent.router.collaboration_models import (
    CollaborationResult,
    CollaborationStep,
    StepResult,
)

logger = logging.getLogger(__name__)


class CollaborationExecutorMixin:
    """协作执行逻辑 Mixin.

    提供 ``AgentCollaboration`` 的单步执行、结果聚合、模板解析、Agent
    自动选择与拓扑排序能力。本 Mixin 不定义 ``__init__``，依赖宿主类
    设置 ``_catalog`` / ``_router`` / ``_billing_engine`` / ``_executor``
    四个实例属性。
    """

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


__all__ = ["CollaborationExecutorMixin"]