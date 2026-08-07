"""Memory and neural mechanism endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from maop.core.backends.db_utils import get_db_path
from maop.core.security.middleware import require_admin

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


# ── 联合查询辅助：合并 memory_entries + episodic_memory ──────────────
# /api/memory/store 写入 episodic_memory（ThreeLayerMemory），
# 而 /api/memory/search 原先只读 memory_entries（MemoryStore），
# 导致写入的数据读不到。以下辅助函数同时查两个表并合并结果。


def _episodic_to_dict(result: Any) -> dict[str, Any]:
    """把 EpisodicSearchResult 转成与 SearchResult 兼容的 dict。

    EpisodicSearchResult.entry 包含: id, task, agent, outcome, score,
    lessons, summary, metadata, created_at, access_count 等。
    """
    entry = result.entry
    meta = entry.metadata or {}
    ts = ""
    if entry.created_at:
        try:
            ts = datetime.fromtimestamp(entry.created_at, tz=timezone.utc).isoformat()
        except (OSError, ValueError, OverflowError):
            ts = str(entry.created_at)
    tags = meta.get("tags", "")
    if not tags and entry.lessons:
        tags = ",".join(entry.lessons[:5])
    snippet = entry.summary or entry.task
    return {
        "id": entry.id,
        "agent": entry.agent,
        "task": entry.task,
        "tags": tags,
        "topic": meta.get("topic", ""),
        "trace_id": meta.get("trace_id", ""),
        "timestamp": ts,
        "score": round(entry.score * result.retrieval_weight, 4),
        "snippet": snippet[:200] if snippet else "",
        "highlighted": "",
        "outcome": entry.outcome,
        "layer": "episodic",
    }


def _unified_search(query: str, top: int, agent: str = "") -> list[dict[str, Any]]:
    """联合查询 memory_entries（MemoryStore）+ episodic_memory（ThreeLayerMemory）。

    返回合并后的 dict 列表，按 score 降序排列，截取 top 条。
    每条标记 source: "memory_entries" 或 "episodic"。
    """
    results: list[dict[str, Any]] = []

    # 来源 1: MemoryStore → memory_entries 表
    try:
        from maop.memory.store import MemoryStore
        store = MemoryStore(root_dir=str(MAOP_ROOT))
        raw = store.search(query=query, top=top, agent=agent) if hasattr(store, "search") else []
        for item in raw:
            if hasattr(item, "model_dump"):
                d = item.model_dump()
            elif isinstance(item, dict):
                d = item
            else:
                d = {"content": str(item), "score": 0}
            d.setdefault("layer", "memory_entries")
            results.append(d)
    except Exception:
        logger.debug("Unified search: MemoryStore failed", exc_info=True)

    # 来源 2: ThreeLayerMemory → episodic_memory 表
    try:
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(MAOP_ROOT))
        ep_results = mem.episodic_search(query=query, agent=agent, top=top)
        for ep in ep_results:
            results.append(_episodic_to_dict(ep))
    except Exception:
        logger.debug("Unified search: ThreeLayerMemory.episodic_search failed", exc_info=True)

    # 合并去重（按 id）并按 score 降序
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in results:
        rid = entry.get("id", "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        unique.append(entry)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    return unique[:top]


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
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(MAOP_ROOT / "data" / "vectors.db"))
        stats["vector_index"] = True
        stats["vector_count"] = vs.count() if hasattr(vs, "count") else 0
    except Exception:
        logger.debug("Failed to check vector store availability", exc_info=True)

    # 联合最近条目：memory_entries + episodic_memory
    recent = _unified_search(query="", top=5)
    stats["recent_entries"] = _tenant_filter(recent, _request_tenant_id(request))

    # 补充 episodic_memory 统计
    try:
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
        mem = ThreeLayerMemory(root_dir=str(MAOP_ROOT))
        ep_stats = mem.episodic_stats()
        stats["episodic_count"] = ep_stats.get("total", 0)
        stats["episodic_by_outcome"] = ep_stats.get("by_outcome", {})
        stats["episodic_avg_score"] = ep_stats.get("avg_score", 0)
        stats["episodic_consolidated"] = ep_stats.get("consolidated", 0)
        # by_agent 需要从 episodic_stats 之外获取 (episodic_stats 不含 by_agent)
        try:
            from maop.core.backends.db_utils import sqlite_connect
            with sqlite_connect(str(MAOP_ROOT / "data" / "maop.db"), foreign_keys=False) as conn:
                rows = conn.execute(
                    "SELECT agent, COUNT(*) as cnt FROM episodic_memory GROUP BY agent ORDER BY cnt DESC"
                ).fetchall()
            stats["episodic_by_agent"] = {r[0] or "unknown": r[1] for r in rows}
        except Exception:
            stats["episodic_by_agent"] = {}
    except Exception:
        logger.debug("Failed to get episodic stats", exc_info=True)
        stats.setdefault("episodic_count", 0)

    return {"status": "ok", "stats": stats}

@router.get("/api/memory/search")
@handle_api_errors("Memory search", error_value={"status": "error", "error": "Memory search unavailable", "results": []})
async def api_memory_search(request: Request, q: str = Query(""), k: int = Query(10, alias="topk")) -> dict[str, Any]:
    # 联合查询 memory_entries + episodic_memory，确保 store 写入的数据能被搜到
    results = _unified_search(query=q, top=k) if q else _unified_search(query="", top=k)
    return {"status": "ok", "query": q, "results": (_rf := _tenant_filter(results, _request_tenant_id(request))), "count": len(_rf)}

@router.get("/api/memory/trace")
@handle_api_errors("Memory trace", error_value={"traces": [], "count": 0, "error": "Memory trace unavailable"})
async def api_memory_trace(request: Request, agent: str = Query("")) -> dict[str, Any]:
    # 联合查询 memory_entries + episodic_memory
    unified = _unified_search(query="", top=50)
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
        from maop.core.memory.vector import VectorStore
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
        from maop.core.memory.three_layer_memory import ThreeLayerMemory
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
