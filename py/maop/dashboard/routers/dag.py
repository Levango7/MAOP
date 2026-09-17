"""MAOP DAG 管理 API 路由 — LLM 智能任务拆分 + DAG 可视化数据。

本路由提供:
  - ``POST /api/dag/auto-split`` — LLM 驱动的自然语言任务拆分，返回子任务 DAG

后续可扩展:
  - ``GET  /api/dag/{execution_id}`` — 查询 DAG 执行状态
  - ``POST /api/dag/execute``       — 直接执行一个 DAG

业务逻辑已提取至 ``maop.dashboard.services.execution_service``（§1）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.scheduling.task_splitter import TaskSplitError
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import execution_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求/响应模型 ────────────────────────────────────────────────

class AutoSplitRequest(BaseModel):
    """POST /api/dag/auto-split 请求体。"""

    description: str = Field(..., min_length=1, max_length=10000, description="自然语言任务描述")
    context: str = Field(default="", max_length=10000, description="额外上下文信息")
    max_subtasks: int = Field(default=10, ge=1, le=50, description="最大子任务数量")


class ExecuteDagRequest(BaseModel):
    """POST /api/dag/execute 请求体 — 前端工作流编辑器导出的 DAG。"""

    nodes: list[dict[str, Any]] = Field(default_factory=list, description="DAG 节点列表")
    edges: list[dict[str, Any]] = Field(default_factory=list, description="DAG 边列表 (source→target)")


# ── 端点 ─────────────────────────────────────────────────────────
@router.post("/api/dag/auto-split")
@handle_api_errors("dag auto-split")
async def auto_split(
    body: AutoSplitRequest,
    request: Request,
) -> dict[str, Any]:
    """LLM 智能任务拆分端点。

    接收一段自然语言任务描述，调用 :class:`TaskSplitter` 将其拆分为多个
    子任务并生成 DAG 依赖图，返回可直接用于 MAOP DAG 调度器的结构。

    需要管理员权限。
    """
    require_admin(request)
    try:
        result = await execution_service.auto_split(
            description=body.description,
            context=body.context,
            max_subtasks=body.max_subtasks,
        )
        return {"status": "ok", "data": result}
    except TaskSplitError as exc:
        logger.warning("[dag/auto-split] 任务拆分失败: %s", exc)
        raise HTTPException(status_code=400, detail="Task split failed") from exc
    except Exception as exc:
        # 防御性兜底：内部意外错误返回 500，避免泄露内部栈
        logger.exception("[dag/auto-split] 任务拆分失败")
        raise HTTPException(status_code=500, detail="Task split failed") from exc


@router.get("/api/dag/health")
@handle_api_errors("dag health")
async def dag_health() -> dict[str, Any]:
    """DAG 模块健康检查（无需鉴权，用于前端探活）。"""
    return {"status": "ok", "module": "dag", "features": ["auto-split", "execute"]}


@router.post("/api/dag/execute")
@handle_api_errors("dag execute")
async def execute_dag(
    body: ExecuteDagRequest,
    request: Request,
) -> dict[str, Any]:
    """执行前端工作流编辑器导出的 DAG，返回 trace_id。

    将编辑器节点（agent/tool/condition/parallel）映射为 :class:`WorkflowStep`，
    按 edges 推导依赖关系后调用 :class:`Engine` 执行。需要管理员权限。
    """
    require_admin(request)
    try:
        result = await execution_service.execute_dag(
            nodes=body.nodes,
            edges=body.edges,
        )
        return {"status": "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        # 防御性兜底：内部意外错误返回 500，不泄露内部栈
        logger.exception("[dag/execute] DAG 执行失败")
        raise HTTPException(status_code=500, detail="DAG execution failed") from exc
