"""Workflow 提交端点（M-X2 E2-b 深集成切片）.

- ``GET  /api/workflows`` —— 列出 agents.yaml ``workflows:`` 段（名称 +
  步骤数 + 涉及 agent，供面板下拉/详情）；
- ``POST /api/workflows/{name}/run`` —— 经**生产入口**
  ``orchestration.wiring.run_workflow_sync`` 提交执行（backend 透传：
  空=legacy 规划合同，temporal=真实执行），并做**状态回写**：
  ``run.workflow.started / completed / failed`` 三事件写入 ``run_events``，
  面板用返回的 ``trace_id`` 打开既有 ``/api/runs/{trace_id}`` 时间线即可
  看到工作流事件与子 agent 的 run 事件同轴呈现。

admin-only：触发 agent 执行属特权操作（沿库内 ``require_admin`` 惯例）。
测试缝：``_load_config`` / ``_runner`` 模块级别名（monkeypatch 注入，
零真实 LLM/网络）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Request

from maop.config.loader import load_config
from maop.core.run_events import record_run_event
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.workflows_schemas import (
    WorkflowRunEnvelope,
    WorkflowRunRequest,
    WorkflowsEnvelope,
)
from maop.orchestration.wiring import run_workflow_sync

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

#: 测试缝：模块级别名，契约测试 monkeypatch 注入（不真跑 workflow）。
_load_config = load_config
_runner = run_workflow_sync


def _new_trace_id() -> str:
    return uuid.uuid4().hex


@router.get("", response_model=WorkflowsEnvelope)
@handle_api_errors(
    "Workflow list",
    error_value={"status": "error", "error": "Workflow list unavailable",
                 "workflows": []},
)
async def api_workflows(request: Request) -> dict[str, Any]:
    require_admin(request)
    cfg = _load_config()
    workflows = []
    for name, wf in cfg.workflows.items():
        agents = sorted({s.agent for s in wf.steps if s.agent})
        workflows.append({
            "name": name,
            "steps": len(wf.steps),
            "agents": agents,
            "description": wf.description,
        })
    return {"status": "ok", "workflows": workflows}


@router.post("/{name}/run", response_model=WorkflowRunEnvelope)
@handle_api_errors(
    "Workflow run",
    error_value={"status": "error", "error": "Workflow run unavailable",
                 "trace_id": "", "result": None},
)
async def api_workflow_run(
    request: Request, name: str, body: WorkflowRunRequest
) -> dict[str, Any]:
    require_admin(request)
    cfg = _load_config()
    if name not in cfg.workflows:
        return {
            "status": "error",
            "trace_id": "",
            "result": None,
            "error": f"workflow not found: {name}",
        }
    trace_id = _new_trace_id()
    backend = (body.backend or "").strip()
    record_run_event(
        trace_id, "run.workflow.started",
        payload={"workflow": name, "backend": backend or "auto"},
    )
    try:
        result = await asyncio.to_thread(
            _runner, name, cfg, dict(body.initial_vars), backend=backend
        )
    except Exception as exc:
        record_run_event(
            trace_id, "run.workflow.failed", status="failed",
            payload={"workflow": name, "error": str(exc)[:300]},
        )
        return {
            "status": "error",
            "trace_id": trace_id,
            "result": None,
            "error": str(exc)[:300],
        }
    record_run_event(
        trace_id, "run.workflow.completed", status="completed",
        payload={
            "workflow": name,
            "steps_total": result.steps_total,
            "steps_completed": result.steps_completed,
        },
    )
    return {
        "status": "ok",
        "trace_id": trace_id,
        "result": {
            "steps_total": result.steps_total,
            "steps_planned": result.steps_planned,
            "steps_completed": result.steps_completed,
            "step_results": [
                {
                    "step_index": r.step_index,
                    "agent": r.agent,
                    "status": r.status,
                    "output": r.output,
                }
                for r in result.step_results
            ],
        },
        "error": "",
    }
