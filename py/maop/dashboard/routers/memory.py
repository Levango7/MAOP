"""Memory and neural mechanism endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.middleware import require_admin
from maop.core.db_utils import get_db_path

from .error_handler import handle_api_errors
from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

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
    from maop.memory.store import MemoryStore
    store = MemoryStore(root_dir=str(MAOP_ROOT))
    stats_obj = store.stats()
    if hasattr(stats_obj, 'model_dump'):
        stats: dict[str, Any] = stats_obj.model_dump()
    elif hasattr(stats_obj, 'dict'):
        stats = stats_obj.dict()
    else:
        stats = dict(stats_obj)
    stats["bloom_filter"] = False
    stats["vector_index"] = False
    try:
        stats["bloom_filter"] = True
    except Exception:
        logger.debug("Failed to check bloom filter availability", exc_info=True)
    try:
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=str(get_db_path("vectors")))
        stats["vector_index"] = True
        stats["vector_count"] = vs.count() if hasattr(vs, "count") else 0
    except Exception:
        logger.debug("Failed to check vector store availability", exc_info=True)
    raw_recent = store.search(query="", top=5) if hasattr(store, "search") else []
    recent = []
    for r in raw_recent:
        if hasattr(r, 'model_dump'):
            recent.append(r.model_dump())
        elif isinstance(r, dict):
            recent.append(r)
        else:
            recent.append({"content": str(r)})
    stats["recent_entries"] = _tenant_filter(recent, _request_tenant_id(request))
    return {"status": "ok", "stats": stats}

@router.get("/api/memory/search")
@handle_api_errors("Memory search", error_value={"status": "error", "error": "Memory search unavailable", "results": []})
async def api_memory_search(request: Request, q: str = Query(""), k: int = Query(10, alias="topk")) -> dict[str, Any]:
    from maop.memory.store import MemoryStore
    store = MemoryStore(root_dir=str(MAOP_ROOT))
    raw_results = store.search(query=q, top=k) if q else []
    results = []
    for r in raw_results:
        if hasattr(r, 'model_dump'):
            results.append(r.model_dump())
        elif isinstance(r, dict):
            results.append(r)
        else:
            results.append({"content": str(r), "score": 0})
    return {"status": "ok", "query": q, "results": (_rf := _tenant_filter(results, _request_tenant_id(request))), "count": len(_rf)}

@router.get("/api/memory/trace")
@handle_api_errors("Memory trace", error_value={"traces": [], "count": 0, "error": "Memory trace unavailable"})
async def api_memory_trace(request: Request, agent: str = Query("")) -> dict[str, Any]:
    from maop.memory.store import MemoryStore
    store = MemoryStore(root_dir=str(MAOP_ROOT))
    results = store.search(query="", top=50) if hasattr(store, "search") else []
    traces = []
    for r in results:
        if hasattr(r, 'model_dump'):
            r_dict: dict[str, Any] = r.model_dump()
        elif not isinstance(r, dict):
            r_dict = {"content": str(r)}
        else:
            r_dict = r
        if agent and r_dict.get("agent", "") != agent:
            continue
        traces.append({"agent": r_dict.get("agent", "unknown"), "topic": r_dict.get("topic", ""),
            "timestamp": r_dict.get("timestamp", ""), "content": r_dict.get("snippet", r_dict.get("highlighted", ""))[:200],
            "tags": r_dict.get("tags", ""), "trace_id": r_dict.get("trace_id", ""), "score": r_dict.get("score", 0)})
    return {"traces": (_tf := _tenant_filter(traces, _request_tenant_id(request))), "count": len(_tf), "agent": agent or "all"}

@router.get("/api/memory/stats")
@handle_api_errors("Memory stats", error_value={"error": "Memory stats unavailable"})
async def api_memory_stats_v4() -> dict[str, Any]:
    from .state import get_bridge
    return await get_bridge().memory_stats()

# ── Neural / Attention ─────────────────────────────────────────────
@router.get("/api/neural/status")
@handle_api_errors("Neural status")
async def api_neural_status() -> dict[str, Any]:
    info: dict[str, Any] = {"attention": {"enabled": False, "mechanism": "N/A"}, "transform": {"enabled": False, "layers": 0},
            "embedding": {"enabled": False, "dim": 0, "model": "N/A"}, "vector_store": {"enabled": False, "count": 0}}
    try:
        from maop.core.vector import VectorStore
        vs = VectorStore(db_path=str(get_db_path("vectors")))
        info["vector_store"] = {"enabled": True, "count": vs.count() if hasattr(vs, "count") else 0}
        if hasattr(vs, "_embedder"):
            emb = vs._embedder
            info["embedding"] = {"enabled": True, "dim": getattr(emb, "dim", 0), "model": getattr(emb, "model_name", "unknown")}
    except Exception:
        logger.exception("Neural vector store check failed")
        info["vector_store"]["error"] = "Vector store unavailable"
    try:
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(MAOP_ROOT))
        stats = store.stats()
        _te = stats.total_entries if hasattr(stats, 'total_entries') else 0
        info["attention"] = {"enabled": True, "mechanism": "FTS5_rank + vector_similarity", "total_entries": _te}
    except Exception:
        logger.debug("Failed to get memory stats", exc_info=True)
    try:
        info["transform"] = {"enabled": True, "layers": 3, "steps": ["plan", "execute", "verify"]}
    except Exception:
        logger.debug("Failed to check workflow engine", exc_info=True)
    return {"status": "ok", "mechanisms": info}

@router.post("/api/neural/attention")
@handle_api_errors("Neural attention", error_value={"results": [], "attention_weights": [], "error": "Neural attention unavailable"})
async def api_neural_attention(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    query = body.get("query", "")
    top_k = body.get("top_k", 10)
    if not query:
        raise HTTPException(400, "missing query")
    from maop.memory.store import MemoryStore
    store = MemoryStore(root_dir=str(MAOP_ROOT))
    raw_results = store.search(query=query, top=top_k)
    results = []
    for r in raw_results:
        if hasattr(r, 'model_dump'):
            results.append(r.model_dump())
        elif isinstance(r, dict):
            results.append(r)
        else:
            results.append({"content": str(r), "score": 0})
    scores = [r.get("score", 0) for r in results]
    if scores:
        mx = max(scores)
        exps = [math.exp(s - mx) for s in scores]
        total = sum(exps)
        weights = [e / total for e in exps]
    else:
        weights = []
    return {"query": query, "results": results, "attention_weights": weights, "count": len(results)}

@router.get("/api/neural/attention")
@handle_api_errors("Neural attention query", error_value={"error": "Neural attention unavailable", "results": [], "attention_weights": []})
async def api_neural_attention_get(q: str = "") -> dict[str, Any]:
    from maop.memory.store import MemoryStore
    ms = MemoryStore(root_dir=str(MAOP_ROOT))
    raw_results = ms.search(q, top=10) if q else []
    results = []
    for r in raw_results:
        if hasattr(r, 'model_dump'):
            results.append(r.model_dump())
        elif isinstance(r, dict):
            results.append(r)
        else:
            results.append({"content": str(r), "score": 0})
    scores = [r.get("score", 0) for r in results]
    if scores:
        mx = max(scores)
        exps = [math.exp(s - mx) for s in scores]
        total = sum(exps)
        weights = [round(e / total, 4) for e in exps]
    else:
        weights = []
    return {"query": q, "results": results, "attention_weights": weights, "count": len(results)}

# ── Memory Write (manual entry) ────────────────────────────────────────
@router.post("/api/memory/store")
@handle_api_errors("Memory store", error_value={"status": "error", "error": "Failed to store memory", "id": None})
async def api_memory_store(request: Request) -> dict[str, Any]:
    """Write a manual memory entry into the three-layer system.
    Body: { layer: "working"|"episodic"|"semantic", content: str,
            agent?: str, topic?: str, task?: str, tags?: str, ttl_s?: int }
    """
    require_admin(request)
    body = await request.json()
    layer = body.get("layer", "episodic")
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")

    try:
        from maop.core.three_layer_memory import ThreeLayerMemory
        raw_tags = body.get("tags")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, (list, tuple)):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []
        mem = ThreeLayerMemory(root_dir=str(MAOP_ROOT))
        entry_id = mem.store(
            layer=layer,
            content=content,
            agent=body.get("agent", "admin"),
            topic=body.get("topic", ""),
            task=body.get("task", ""),
            tags=tags,
            ttl_s=body.get("ttl_s"),
        )
        return {"status": "ok", "id": entry_id, "layer": layer}
    except Exception as exc:
        logger.exception("memory store failed")
        raise HTTPException(500, f"Store failed: {exc}")
