"""MAOP Dashboard — SubAgent management API endpoints.

业务逻辑已提取至 ``maop.dashboard.services.agent_service``（§6）。
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

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 ──────────────────────────────────────────────
class SubAgentSpawnRequest(BaseModel):
    """生成子代理的请求体。"""
    agent: str = Field(default="", max_length=256)
    task: str = Field(default="", max_length=10000)
    context: str = Field(default="", max_length=50000)
    model: str = Field(default="", max_length=256)


class SubAgentWaitRequest(BaseModel):
    """等待子代理完成的请求体。"""
    agent_id: str = Field(default="", max_length=128)
    timeout: int = Field(default=120, ge=1, le=3600)


class SubAgentCancelRequest(BaseModel):
    """取消子代理的请求体。"""
    agent_id: str = Field(default="", max_length=128)


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _get_subagent_mgr() -> Any:
    return agent_service._get_subagent_mgr(MAOP_ROOT)


def _set_subagent_mgr(mgr: Any) -> None:
    agent_service._set_subagent_mgr(mgr)


@router.post("/api/subagent/spawn")
@handle_api_errors("SubAgent spawn", error_value={"status": "error", "error": "Spawn failed"})
async def api_subagent_spawn(body: SubAgentSpawnRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    agent_name = body.agent
    task = body.task
    context = body.context
    if not agent_name or not task:
        raise HTTPException(400, "missing agent or task")
    mgr = _get_subagent_mgr()
    agent_id = await agent_service.subagent_spawn(
        mgr, agent_name=agent_name, task=task, context=context, model=body.model
    )
    return {"status": "ok", "agent_id": agent_id}


@router.post("/api/subagent/wait")
@handle_api_errors("SubAgent wait", error_value={"status": "error", "error": "Wait failed"})
async def api_subagent_wait(body: SubAgentWaitRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    agent_id = body.agent_id
    timeout = body.timeout
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    result = await agent_service.subagent_wait(mgr, agent_id, timeout=timeout)
    if result is None:
        raise HTTPException(404, f"Agent {agent_id} not found or timed out")
    return {"status": "ok", "result": result.model_dump() if hasattr(result, "model_dump") else str(result)}


@router.post("/api/subagent/cancel")
@handle_api_errors("SubAgent cancel", error_value={"status": "error", "error": "Cancel failed"})
async def api_subagent_cancel(body: SubAgentCancelRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    agent_id = body.agent_id
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    # F4a (2026-07-22, Phase F): SubAgentManager.cancel is a synchronous
    # method (returns bool directly, not a coroutine). The previous
    # ``await mgr.cancel(agent_id)`` raised TypeError at runtime
    # ("object bool can't be used in 'await' expression"). Drop the
    # await so the call works as designed.
    ok = agent_service.subagent_cancel(mgr, agent_id)
    if not ok:
        # H-1 fix: 资源未找到应返回 404，而非 200 + status=not_found。
        raise HTTPException(status_code=404, detail="Subagent not found")
    return {"status": "ok", "agent_id": agent_id}


@router.get("/api/subagent/list")
@handle_api_errors("SubAgent list", error_value={"agents": [], "count": 0, "error": "List failed"})
async def api_subagent_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    mgr = _get_subagent_mgr()
    result = agent_service.subagent_list(mgr)
    return {"status": "ok", **result}


@router.get("/api/subagent/transcript")
@handle_api_errors("SubAgent transcript", error_value={"status": "error", "error": "Transcript failed"})
async def api_subagent_transcript(request: Request, agent_id: str = "") -> dict[str, Any]:
    require_admin(request)
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    transcript = agent_service.subagent_transcript(mgr, agent_id)
    return {"status": "ok", "transcript": transcript}
