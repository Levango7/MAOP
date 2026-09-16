"""MAOP Agent Collaboration — 数据模型与默认执行器.

本模块从 ``agent_collaboration.py`` 拆分而来，集中定义协作编排所需的
Pydantic 数据模型与 Agent 执行器类型，供编排器、预定义模式及外部
调用方共享。

核心组件：
  - ``CollaborationPattern``  — 协作模式（Pydantic 模型）
  - ``CollaborationStep``     — 协作步骤（Pydantic 模型）
  - ``StepResult``            — 单步执行结果（Pydantic 模型）
  - ``CollaborationResult``   — 协作聚合结果（Pydantic 模型）
  - ``AgentExecutor``         — Agent 执行器回调类型
  - ``_default_executor``     — 默认模拟执行器
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

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


__all__ = [
    "AgentExecutor",
    "CollaborationPattern",
    "CollaborationResult",
    "CollaborationStep",
    "StepResult",
    "_default_executor",
]