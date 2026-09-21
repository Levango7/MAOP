"""Memory and neural mechanism endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field  # noqa: F401

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import memory_service

from .state import MAOP_ROOT  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (批次3A: 输入校验) ───────────────────────────
class NeuralAttentionRequest(BaseModel):
    """POST /api/neural/attention 请求体。"""
    query: str = ""
    top_k: int = 10


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
    tid = getattr(request.state, "tenant_id", "")
    return tid or ""


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


@router.get("/api/memory/deep")
@handle_api_errors("Memory deep stats", error_value={"status": "error", "error": "Memory stats unavailable", "stats": {}})
async def api_memory_deep(request: Request) -> dict[str, Any]:
    require_admin(request)
    stats = memory_service.get_memory_deep_stats()
    stats["recent_entries"] = _tenant_filter(stats.get("recent_entries", []), _request_tenant_id(request))
    return {"status": "ok", "stats": stats}

@router.get("/api/memory/search")
@handle_api_errors("Memory search", error_value={"status": "error", "error": "Memory search unavailable", "results": []})
async def api_memory_search(request: Request, q: str = Query(""), k: int = Query(10, ge=1, le=100, alias="topk")) -> dict[str, Any]:
    require_admin(request)
    # 联合查询 memory_entries + episodic_memory，确保 store 写入的数据能被搜到
    results = memory_service.unified_search(query=q, top=k)
    return {"status": "ok", "query": q, "results": (_rf := _tenant_filter(results, _request_tenant_id(request))), "count": len(_rf)}

@router.get("/api/memory/trace")
@handle_api_errors("Memory trace", error_value={"traces": [], "count": 0, "error": "Memory trace unavailable"})
async def api_memory_trace(request: Request, agent: str = Query("")) -> dict[str, Any]:
    require_admin(request)
    # 联合查询 memory_entries + episodic_memory
    unified = memory_service.unified_search(query="", top=50)
    traces = []
    for r_dict in unified:
        if agent and r_dict.get("agent", "") != agent:
            continue
        traces.append({"agent": r_dict.get("agent", "unknown"), "topic": r_dict.get("topic", ""),
            "timestamp": r_dict.get("timestamp", ""), "content": r_dict.get("snippet", r_dict.get("highlighted", ""))[:200],
            "tags": r_dict.get("tags", ""), "trace_id": r_dict.get("trace_id", ""), "score": r_dict.get("score", 0),
            "layer": r_dict.get("layer", ""), "outcome": r_dict.get("outcome", "")})
    return {"traces": (_tf := _tenant_filter(traces, _request_tenant_id(request))), "count": len(_tf), "agent": agent or "all"}

@router.get("/api/memory/stats")
@handle_api_errors("Memory stats", error_value={"error": "Memory stats unavailable"})
async def api_memory_stats_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    from .state import get_bridge
    return await get_bridge().memory_stats()

# ── Neural / Attention ─────────────────────────────────────────────
@router.get("/api/neural/status")
@handle_api_errors("Neural status")
async def api_neural_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    info = memory_service.get_neural_status()
    return {"status": "ok", "mechanisms": info}

@router.post("/api/neural/attention")
@handle_api_errors("Neural attention", error_value={"results": [], "attention_weights": [], "error": "Neural attention unavailable"})
async def api_neural_attention(request: Request, body: NeuralAttentionRequest) -> dict[str, Any]:
    # 批次3A: 用 Pydantic NeuralAttentionRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（query/top_k 字段类型）。
    require_admin(request)
    query = body.query
    top_k = body.top_k
    if not query:
        raise HTTPException(400, "missing query")
    results, weights = memory_service.compute_attention(query=query, top_k=top_k)
    return {"query": query, "results": results, "attention_weights": weights, "count": len(results)}

@router.get("/api/neural/attention")
@handle_api_errors("Neural attention query", error_value={"error": "Neural attention unavailable", "results": [], "attention_weights": []})
async def api_neural_attention_get(request: Request, q: str = "") -> dict[str, Any]:
    require_admin(request)
    results, weights = memory_service.compute_attention(query=q, top_k=10)
    weights = [round(w, 4) for w in weights]
    return {"query": q, "results": results, "attention_weights": weights, "count": len(results)}

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
