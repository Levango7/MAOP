"""MAOP DAG 管理 API 路由 — LLM 智能任务拆分 + DAG 可视化数据。

本路由提供:
  - ``POST /api/dag/auto-split`` — LLM 驱动的自然语言任务拆分，返回子任务 DAG

后续可扩展:
  - ``GET  /api/dag/{execution_id}`` — 查询 DAG 执行状态
  - ``POST /api/dag/execute``       — 直接执行一个 DAG
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.core.scheduling.task_splitter import TaskSplitError, TaskSplitter
from maop.core.security.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求/响应模型 ────────────────────────────────────────────────

class AutoSplitRequest(BaseModel):
    """POST /api/dag/auto-split 请求体。"""

    description: str = Field(..., min_length=1, max_length=10000, description="自然语言任务描述")
    context: str = Field(default="", max_length=10000, description="额外上下文信息")
    max_subtasks: int = Field(default=10, ge=1, le=50, description="最大子任务数量")


# ── 端点 ─────────────────────────────────────────────────────────

@router.post("/api/dag/auto-split")
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
        splitter = TaskSplitter()
        result = await splitter.split(
            description=body.description,
            context=body.context,
            max_subtasks=body.max_subtasks,
        )
        return {"success": True, "data": result}
    except TaskSplitError as exc:
        logger.warning("[dag/auto-split] 任务拆分失败: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # 防御性兜底：任何意外错误都返回 400 而非 500，避免泄露内部栈
        logger.exception("[dag/auto-split] 意外错误")
        raise HTTPException(status_code=400, detail=f"任务拆分失败: {exc}") from exc


@router.get("/api/dag/health")
async def dag_health() -> dict[str, Any]:
    """DAG 模块健康检查（无需鉴权，用于前端探活）。"""
    return {"status": "ok", "module": "dag", "features": ["auto-split"]}