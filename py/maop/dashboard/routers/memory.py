"""Memory and neural mechanism endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field  # noqa: F401

from maop.core.security.middleware import require_admin
from maop.core.tenant.context import tenant_id_from_request
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import memory_service

from .state import MAOP_ROOT  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (批次3A: 输入校验) ───────────────────────────

class MemoryStoreRequest(BaseModel):
    """POST /api/memory/store 请求体。

    Body: { layer: "working"|"episodic"|"semantic", content: str,
            agent?: str, topic?: str, task?: str, tags?: str|list, ttl_s?: int }
    """
    layer: str = "episodic"
    content: str = ""
    agent: str = "admin"
    topic: str = ""
    task: str = ""
    tags: str | list[str] | None = None
    ttl_s: int | None = None

def _request_tenant_id(request: Request) -> str:
    """委托给唯一实现（见 maop/core/tenant/context.py）。

    本函数曾经是各路由里手抄的一份；抄写已经出现分歧（compliance fail-closed
    而 rbac 软回退），所以统一到共享实现，此处只保留函数名给既有调用点。
    """
    return tenant_id_from_request(request)


def _tenant_filter(data: Any, tenant_id: str) -> Any:
    if not tenant_id:
        return data
    if isinstance(data, dict):
        return {k: _tenant_filter(v, tenant_id) for k, v in data.items()}
    if isinstance(data, list):
        return [
            _tenant_filter(it, tenant_id) for it in data
            if not (isinstance(it, dict) and it.get("tenant_id") and it.get("tenant_id") != tenant_id)
        ]
    return data


@router.get("/api/memory/search")
@handle_api_errors("Memory search", error_value={"status": "error", "error": "Memory search unavailable", "results": []})
async def api_memory_search(request: Request, q: str = Query(""), k: int = Query(10, ge=1, le=100, alias="topk")) -> dict[str, Any]:
    require_admin(request)
    # 联合查询 memory_entries + episodic_memory，确保 store 写入的数据能被搜到
    results = memory_service.unified_search(query=q, top=k)
    return {"status": "ok", "query": q, "results": (_rf := _tenant_filter(results, _request_tenant_id(request))), "count": len(_rf)}

@router.get("/api/memory/stats")
@handle_api_errors("Memory stats", error_value={"error": "Memory stats unavailable"})
async def api_memory_stats_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    from .state import get_bridge
    return await get_bridge().memory_stats()

# ── Memory Write (manual entry) ────────────────────────────────────────
@router.post("/api/memory/store")
@handle_api_errors("Memory store", error_value={"status": "error", "error": "Failed to store memory", "id": None})
async def api_memory_store(request: Request, body: MemoryStoreRequest) -> dict[str, Any]:
    """Write a manual memory entry into the three-layer system.
    Body: { layer: "working"|"episodic"|"semantic", content: str,
            agent?: str, topic?: str, task?: str, tags?: str, ttl_s?: int }
    """
    # 批次3A: 用 Pydantic MemoryStoreRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体字段类型。
    require_admin(request)
    layer = body.layer
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "content is required")

    try:
        entry_id = memory_service.store_memory(
            layer=layer,
            content=content,
            agent=body.agent,
            topic=body.topic,
            task=body.task,
            tags=body.tags,
            ttl_s=body.ttl_s,
        )
        return {"status": "ok", "id": entry_id, "layer": layer}
    except Exception :
        logger.exception("memory store failed")
        raise HTTPException(500, "Store failed, please try again later")
