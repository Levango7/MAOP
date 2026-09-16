"""MAOP Agent Collaboration — 预定义协作模式.

本模块从 ``agent_collaboration.py`` 拆分而来，集中定义开箱即用的协作模式
工厂函数。每个函数返回一个 ``CollaborationPattern`` 实例，描述多 Agent
协作完成常见任务（项目初始化、代码审查、并行处理等）的流程。

核心组件：
  - ``pattern_init_and_implement``   — 预定义模式：项目初始化+功能实现
  - ``pattern_review_and_fix``       — 预定义模式：代码审查+修复
  - ``pattern_parallel_and_merge``   — 预定义模式：并行处理+合并
  - ``list_predefined_patterns``     — 列出所有预定义模式
"""

from __future__ import annotations

from maop.core.agent.registry.agent_catalog import AgentCapability
from maop.core.agent.router.collaboration_models import (
    CollaborationPattern,
    CollaborationStep,
)


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
    "list_predefined_patterns",
    "pattern_init_and_implement",
    "pattern_parallel_and_merge",
    "pattern_review_and_fix",
]