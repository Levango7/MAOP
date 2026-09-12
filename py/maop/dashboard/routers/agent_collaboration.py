"""MAOP Dashboard — Agent Collaboration API endpoints.

暴露 ``AgentCollaboration`` 编排器 via REST API，供桌面应用调度层
执行多 Agent 协作模式、自定义步骤编排、列出预定义模式。

端点：
  - ``POST /api/agent-collaboration/execute``  — 执行预定义协作模式
  - ``POST /api/agent-collaboration/custom``   — 执行自定义步骤
  - ``GET  /api/agent-collaboration/patterns`` — 列出预定义模式

所有端点要求 admin 角色（via ``require_admin`` 守卫）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class CollaborationStepRequest(BaseModel):
    """协作步骤请求体（与 CollaborationStep 对齐）。"""

    name: str = Field(..., min_length=1, description="步骤名称")
    agent_name: str = Field(default="", description="执行 Agent 名（空则自动选择）")
    task_template: str = Field(..., description="任务模板")
    depends_on: list[str] = Field(
        default_factory=list, description="依赖步骤名列表",
    )
    capabilities_required: list[str] = Field(
        default_factory=list, description="需要的能力列表",
    )
    optional: bool = Field(default=False, description="是否可选")


class ExecutePatternRequest(BaseModel):
    """POST /api/agent-collaboration/execute 请求体。"""

    pattern_name: str = Field(
        ..., description="预定义模式名: init_and_implement/review_and_fix/parallel_and_merge",
    )
    task: str = Field(..., min_length=1, description="原始任务描述")
    context: dict[str, Any] = Field(
        default_factory=dict, description="初始上下文（可选）",
    )


class ExecuteCustomRequest(BaseModel):
    """POST /api/agent-collaboration/custom 请求体。"""

    steps: list[CollaborationStepRequest] = Field(
        ..., min_length=1, description="自定义步骤列表",
    )
    context: dict[str, Any] = Field(
        default_factory=dict, description="初始上下文（可选，可含 original_task）",
    )


# ── 单例（双重检查锁定）────────────────────────────────────────────
_collaboration: Any = None
_collab_lock = threading.Lock()


def _get_collaboration() -> Any:
    """惰性初始化全局 AgentCollaboration 单例.

    复用全局 AgentCatalog 与 AgentRouter 单例。计费引擎暂不注入
    （None），避免引入额外 DB 初始化开销；如需计费，调用方可
    通过 ``_set_collaboration`` 注入自定义实例。
    """
    global _collaboration
    if _collaboration is None:
        with _collab_lock:
            if _collaboration is None:
                from maop.core.agent.registry.agent_catalog import AgentCatalog
                from maop.core.agent.router.agent_collaboration import (
                    AgentCollaboration,
                )
                from maop.core.agent.router.agent_router import AgentRouter

                catalog = AgentCatalog.default()
                agent_router = AgentRouter(catalog=catalog)
                _collaboration = AgentCollaboration(
                    catalog=catalog, router=agent_router,
                )
    return _collaboration


def _set_collaboration(collab: Any) -> None:
    """供测试注入自定义 AgentCollaboration 实例（隔离 DB）。"""
    global _collaboration
    with _collab_lock:
        _collaboration = collab


# ── 预定义模式名 → 工厂函数 映射 ──────────────────────────────────
def _get_pattern_by_name(name: str) -> Any:
    """按名称获取预定义协作模式.

    Parameters
    ----------
    name : str
        模式名：init_and_implement / review_and_fix / parallel_and_merge。

    Returns
    -------
    CollaborationPattern

    Raises
    ------
    HTTPException
        404 当模式名不存在。
    """
    from maop.core.agent.router.agent_collaboration import (
        pattern_init_and_implement,
        pattern_parallel_and_merge,
        pattern_review_and_fix,
    )

    patterns = {
        "init_and_implement": pattern_init_and_implement,
        "review_and_fix": pattern_review_and_fix,
        "parallel_and_merge": pattern_parallel_and_merge,
    }
    factory = patterns.get(name)
    if factory is None:
        raise HTTPException(
            404,
            f"Unknown pattern: {name}. Available: {sorted(patterns.keys())}",
        )
    return factory()


# ── 端点 ──────────────────────────────────────────────────────────
@router.post("/api/agent-collaboration/execute")
@handle_api_errors(
    "Execute collaboration pattern",
    error_value={"status": "error", "error": "Execution failed"},
)
async def api_execute_pattern(
    body: ExecutePatternRequest, request: Request,
) -> dict[str, Any]:
    """执行预定义协作模式.

    请求体指定模式名与任务，返回各步骤执行结果与聚合最终结果。
    """
    require_admin(request)
    from maop.core.agent.router.agent_collaboration import AgentCollaboration

    pattern = _get_pattern_by_name(body.pattern_name)
    collab: AgentCollaboration = _get_collaboration()
    result = collab.execute_pattern(pattern, task=body.task, context=body.context)
    return {
        "status": "ok",
        "success": result.success,
        "final_result": result.final_result,
        "step_results": [r.model_dump(mode="json") for r in result.step_results],
        "total_duration_s": result.total_duration_s,
        "pattern_name": body.pattern_name,
    }


@router.post("/api/agent-collaboration/custom")
@handle_api_errors(
    "Execute custom collaboration",
    error_value={"status": "error", "error": "Custom execution failed"},
)
async def api_execute_custom(
    body: ExecuteCustomRequest, request: Request,
) -> dict[str, Any]:
    """执行自定义步骤编排.

    请求体直接传入步骤列表，无需包装为预定义模式。
    """
    require_admin(request)
    from maop.core.agent.router.agent_collaboration import (
        AgentCollaboration,
        CollaborationStep,
    )

    # 转换请求步骤为 CollaborationStep
    steps = [
        CollaborationStep(
            name=s.name,
            agent_name=s.agent_name,
            task_template=s.task_template,
            depends_on=list(s.depends_on),
            capabilities_required=list(s.capabilities_required),
            optional=s.optional,
        )
        for s in body.steps
    ]
    collab: AgentCollaboration = _get_collaboration()
    result = collab.execute_custom(steps, context=body.context)
    return {
        "status": "ok",
        "success": result.success,
        "final_result": result.final_result,
        "step_results": [r.model_dump(mode="json") for r in result.step_results],
        "total_duration_s": result.total_duration_s,
    }


@router.get("/api/agent-collaboration/patterns")
@handle_api_errors(
    "List collaboration patterns",
    error_value={"status": "error", "patterns": [], "error": "List failed"},
)
async def api_list_patterns(request: Request) -> dict[str, Any]:
    """列出所有预定义协作模式."""
    require_admin(request)
    from maop.core.agent.router.agent_collaboration import (
        list_predefined_patterns,
    )

    patterns = list_predefined_patterns()
    return {
        "status": "ok",
        "patterns": [
            {
                "name": p.name,
                "description": p.description,
                "steps": [s.model_dump(mode="json") for s in p.steps],
                "step_count": len(p.steps),
            }
            for p in patterns
        ],
        "count": len(patterns),
    }