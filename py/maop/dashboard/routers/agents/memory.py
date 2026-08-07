"""Memory endpoints for the agents router (store/retrieve/clear/summary)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from . import _deps

router = APIRouter(prefix="/api/agents", tags=["agents"])


class MemoryStoreRequest(BaseModel):
    memory_type: str = Field(description="interaction/preference/error_pattern/performance/lesson")
    content: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


@router.get("/{name}/memory")
@handle_api_errors
async def get_agent_memory(
    name: str,
    memory_type: str = Query("", description="Filter by memory type"),
    limit: int = Query(50, ge=1, le=500),
):
    """获取 agent 的记忆记录。"""
    memory = _deps._get_memory()
    records = memory.retrieve(name, memory_type=memory_type or None, limit=limit)
    return {"memories": records, "count": len(records)}


@router.post("/{name}/memory")
@handle_api_errors
async def store_agent_memory(name: str, body: MemoryStoreRequest, request: Request) -> dict[str, Any]:
    """存储一条 agent 记忆。"""
    require_admin(request)
    memory = _deps._get_memory()
    record_id = memory.store(
        agent_name=name,
        memory_type=body.memory_type,
        content=body.content,
        metadata=body.metadata,
        importance=body.importance,
    )
    return {"id": record_id, "status": "stored"}


@router.delete("/{name}/memory")
@handle_api_errors
async def clear_agent_memory(
    name: str,
    request: Request,
    memory_id: int = Query(0, description="Specific memory ID to delete, 0 = all"),
):
    """清除 agent 的记忆（全部或指定条目）。"""
    require_admin(request)
    memory = _deps._get_memory()
    if memory_id:
        deleted = memory.forget(name, memory_id)
    else:
        deleted = memory.forget(name)
    return {"deleted": deleted}


@router.get("/{name}/memory/summary")
@handle_api_errors
async def get_memory_summary(name: str) -> dict[str, Any]:
    """获取 agent 记忆的统计摘要。"""
    memory = _deps._get_memory()
    summary = memory.summarize(name)
    return {"summary": summary}