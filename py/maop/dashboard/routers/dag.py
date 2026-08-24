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
from maop.dashboard.error_handler import handle_api_errors

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
@handle_api_errors("DAG auto-split")
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
@handle_api_errors("DAG health", error_value={"status": "error", "error": "DAG health unavailable"})
async def dag_health() -> dict[str, Any]:
    """DAG 模块健康检查（无需鉴权，用于前端探活）。"""
    return {"status": "ok", "module": "dag", "features": ["auto-split", "execute"]}


@router.post("/api/dag/execute")
@handle_api_errors("DAG execute")
async def execute_dag(
    body: ExecuteDagRequest,
    request: Request,
) -> dict[str, Any]:
    """执行前端工作流编辑器导出的 DAG，返回 trace_id。

    将编辑器节点（agent/tool/condition/parallel）映射为 :class:`WorkflowStep`，
    按 edges 推导依赖关系后调用 :class:`Engine` 执行。需要管理员权限。
    """
    require_admin(request)
    if not body.nodes:
        raise HTTPException(status_code=400, detail="DAG 无节点，无法执行")

    try:
        from maop.engine import Engine, StepType, WorkflowStep

        # edges source→target 推导 depends_on
        depends: dict[str, list[str]] = {}
        for edge in body.edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src and tgt:
                depends.setdefault(tgt, []).append(src)

        _type_map = {
            "agent": StepType.AGENT,
            "tool": StepType.AGENT,
            "condition": StepType.CONDITION,
            "parallel": StepType.DAG,
        }
        steps: list[WorkflowStep] = []
        for node in body.nodes:
            nid = node.get("id", "")
            cfg = node.get("config") or {}
            steps.append(
                WorkflowStep(
                    id=nid,
                    type=_type_map.get(node.get("type", "agent"), StepType.AGENT),
                    agent=cfg.get("agent") or cfg.get("tool") or "",
                    task=node.get("label") or cfg.get("task") or cfg.get("predicate") or "",
                    depends_on=depends.get(nid, []),
                )
            )

        from types import SimpleNamespace

        async def _default_step_executor(step, context, workdir, trace_id):
            """Default step executor using Dispatcher to run agent/tool steps."""
            from maop.delegate.dispatch_core import Dispatcher
            dispatcher = Dispatcher()
            try:
                dr = await dispatcher.dispatch(
                    agent=step.agent,
                    task=step.task,
                    workdir=str(workdir) if workdir else "",
                    trace_id=trace_id or "",
                )
                return SimpleNamespace(
                    output=dr.result.stdout,
                    exit_code=dr.result.exit_code,
                    error=dr.result.error or ("" if dr.result.ok else dr.result.stderr),
                )
            except Exception as exc:
                return SimpleNamespace(
                    output="",
                    exit_code=1,
                    error=str(exc),
                )

        result = await Engine(step_executor=_default_step_executor).run(steps)
        return {
            "status": "ok",
            "run_id": result.trace_id,
            "success": result.success,
            "steps": [s.model_dump() for s in result.steps],
        }
    except HTTPException:
        raise
    except Exception as exc:
        # 防御性兜底：不泄露内部栈
        logger.exception("[dag/execute] DAG 执行失败")
        raise HTTPException(status_code=400, detail=f"DAG 执行失败: {exc}") from exc