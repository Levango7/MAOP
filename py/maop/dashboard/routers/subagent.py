"""MAOP Dashboard — SubAgent management API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_subagent_mgr = None

def _get_subagent_mgr() -> Any:
    global _subagent_mgr
    if _subagent_mgr is None:
        from maop.core.agent.delegation.subagent_lifecycle import SubAgentManager
        _subagent_mgr = SubAgentManager(root_dir=str(MAOP_ROOT))
    return _subagent_mgr


@router.post("/api/subagent/spawn")
@handle_api_errors("SubAgent spawn", error_value={"status": "error", "error": "Spawn failed"})
async def api_subagent_spawn(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    task = body.get("task", "")
    context = body.get("context", "")
    if not agent_name or not task:
        raise HTTPException(400, "missing agent or task")
    mgr = _get_subagent_mgr()
    # F4a (2026-07-22, Phase F): SubAgentManager.spawn expects an
    # AgentConfig Pydantic model, not a plain dict — passing
    # ``config={"agent": agent_name}`` raised a Pydantic validation
    # error at runtime. Build the proper AgentConfig with the caller's
    # agent name (and optional model if provided in the body).
    from maop.core.agent.delegation.subagent_lifecycle import AgentConfig
    model = body.get("model", "")
    config = AgentConfig(name=agent_name, model=model)
    agent_id = await mgr.spawn(config=config, task=task, context=context)
    return {"status": "ok", "agent_id": agent_id}


@router.post("/api/subagent/wait")
@handle_api_errors("SubAgent wait", error_value={"status": "error", "error": "Wait failed"})
async def api_subagent_wait(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_id = body.get("agent_id", "")
    timeout = body.get("timeout", 120)
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    result = await mgr.wait(agent_id, timeout=timeout)
    if result is None:
        raise HTTPException(404, f"Agent {agent_id} not found or timed out")
    return {"status": "ok", "result": result.model_dump() if hasattr(result, "model_dump") else str(result)}


@router.post("/api/subagent/cancel")
@handle_api_errors("SubAgent cancel", error_value={"status": "error", "error": "Cancel failed"})
async def api_subagent_cancel(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_id = body.get("agent_id", "")
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    # F4a (2026-07-22, Phase F): SubAgentManager.cancel is a synchronous
    # method (returns bool directly, not a coroutine). The previous
    # ``await mgr.cancel(agent_id)`` raised TypeError at runtime
    # ("object bool can't be used in 'await' expression"). Drop the
    # await so the call works as designed.
    ok = mgr.cancel(agent_id)
    return {"status": "ok" if ok else "not_found", "agent_id": agent_id}


@router.get("/api/subagent/list")
@handle_api_errors("SubAgent list", error_value={"agents": [], "count": 0, "error": "List failed"})
async def api_subagent_list() -> dict[str, Any]:
    mgr = _get_subagent_mgr()
    agents = mgr.list_agents()
    return {"agents": agents, "count": len(agents)}


@router.get("/api/subagent/transcript")
@handle_api_errors("SubAgent transcript", error_value={"status": "error", "error": "Transcript failed"})
async def api_subagent_transcript(agent_id: str = "") -> dict[str, Any]:
    if not agent_id:
        raise HTTPException(400, "missing agent_id")
    mgr = _get_subagent_mgr()
    transcript = mgr.get_live_transcript(agent_id)
    return {"status": "ok", "transcript": transcript}
