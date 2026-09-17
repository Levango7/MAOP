"""MAOP Dashboard — Agent Collaboration API endpoints.

暴露 ``AgentCollaboration`` 编排器 via REST API，供桌面应用调度层
执行多 Agent 协作模式、自定义步骤编排、列出预定义模式。

端点：
  - ``POST /api/agent-collaboration/execute``  — 执行预定义协作模式
  - ``POST /api/agent-collaboration/custom``   — 执行自定义步骤
  - ``GET  /api/agent-collaboration/patterns`` — 列出预定义模式

所有端点要求 admin 角色（via ``require_admin`` 守卫）。

业务逻辑已提取至 ``maop.dashboard.services.agent_service``（§5）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import agent_service

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


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_collaboration() -> Any:
    return agent_service._get_collaboration()


def _set_collaboration(collab: Any) -> None:
    agent_service._set_collaboration(collab)


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
    try:
        result = agent_service.execute_pattern(body.pattern_name, body.task, body.context)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return {"status": "ok", **result}


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
    # 转换 Pydantic 步骤为 dict 供 service 构建 CollaborationStep
    steps = [
        {
            "name": s.name,
            "agent_name": s.agent_name,
            "task_template": s.task_template,
            "depends_on": list(s.depends_on),
            "capabilities_required": list(s.capabilities_required),
            "optional": s.optional,
        }
        for s in body.steps
    ]
    result = agent_service.execute_custom(steps, body.context)
    return {"status": "ok", **result}


@router.get("/api/agent-collaboration/patterns")
@handle_api_errors(
    "List collaboration patterns",
    error_value={"status": "error", "patterns": [], "error": "List failed"},
)
async def api_list_patterns(request: Request) -> dict[str, Any]:
    """列出所有预定义协作模式."""
    require_admin(request)
    result = agent_service.list_patterns()
    return {"status": "ok", **result}
