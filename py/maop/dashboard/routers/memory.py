"""Memory and neural mechanism endpoints for MAOP Dashboard."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

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
    """把 EpisodicSearchResult（对象）或 facade dict 转成与 SearchResult 兼容的 dict。

    EpisodicSearchResult.entry 包含: id, task, agent, outcome, score,
    lessons, summary, metadata, created_at, access_count 等。
    facade.short_term_search 返回同字段的 dict 形态。
    """
    if isinstance(result, dict):
        # facade.short_term_search 输出（T3 迁移后主路径）
        meta = result.get("metadata") or {}
        ts = ""
        created_at = result.get("created_at")
        if created_at:
            try:
                ts = datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat()
            except (OSError, ValueError, OverflowError):
                ts = str(created_at)
        tags = meta.get("tags", "")
        lessons = result.get("lessons") or []
        if not tags and lessons:
            tags = ",".join(lessons[:5])
        summary = result.get("summary") or ""
        task = result.get("task") or ""
        return {
            "id": result.get("id", ""),
            "agent": result.get("agent", ""),
            "task": task,
            "tags": tags,
            "topic": meta.get("topic", ""),
            "trace_id": meta.get("trace_id", ""),
            "timestamp": ts,
            "score": round(
                (result.get("score") or 0.0) * (result.get("retrieval_weight") or 1.0), 4
            ),
            "snippet": (summary or task)[:200],
            "highlighted": "",
            "outcome": result.get("outcome", ""),
            "layer": "episodic",
        }
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

    # 来源 2: MemoryFacade → episodic_memory 表（T3: 收敛统一入口）
    try:
        from maop.memory.facade import MemoryFacade
        mem = MemoryFacade(root_dir=str(MAOP_ROOT), mode="agent")
        ep_results = mem.short_term_search(query=query, top=top, agent=agent)
        for ep in ep_results:
            results.append(_episodic_to_dict(ep))
    except Exception:
        logger.debug("Unified search: MemoryFacade.short_term_search failed", exc_info=True)

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
        # T3: 收敛到 MemoryFacade（mode="agent"），short_term_stats == episodic_stats。
        from maop.memory.facade import MemoryFacade
        mem = MemoryFacade(root_dir=str(MAOP_ROOT), mode="agent")
        ep_stats = mem.short_term_stats()
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
        # T3: 收敛到 MemoryFacade（mode="agent"），store 按 layer 路由到同一底层。
        from maop.memory.facade import MemoryFacade
        raw_tags = body.get("tags")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, (list, tuple)):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []
        mem = MemoryFacade(root_dir=str(MAOP_ROOT), mode="agent")
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


# ── 漏斗记忆增强 API (L0 证据 / L1 原子事实 / 符号化短期记忆) ────────
# 暴露 EvidenceStore / AtomFactStore / SymbolicMemory 的功能，
# 通过 MemoryFacade(mode="agent") 统一访问，使漏斗记忆在 dashboard 可用。


def _funnel_facade():
    """获取漏斗记忆 facade 实例（agent 模式，懒加载独立组件）。"""
    from maop.memory.facade import MemoryFacade
    return MemoryFacade(root_dir=str(MAOP_ROOT), mode="agent")


# ── 请求体 Pydantic 模型 ────────────────────────────────────────────


class EvidencePruneRequest(BaseModel):
    """清理 L0 旧证据请求体。"""
    older_than_days: float = Field(90.0, ge=0, description="清理该天数之前的证据")
    session_id: str = Field("", description="限定会话 ID（空表示所有会话）")
    kind: str = Field("", description="限定证据种类（空表示所有种类）")
    limit: int = Field(500, ge=1, le=10000, description="最多清理条数")


class FactsPromoteRequest(BaseModel):
    """晋升 L1 原子事实到 L3 长期记忆请求体。"""
    fact_ids: list[str] = Field(default_factory=list, description="待晋升的事实 ID 列表")
    min_access: int = Field(3, ge=1, description="最低访问次数阈值（fact_ids 为空时按此筛选）")
    top: int = Field(50, ge=1, le=1000, description="最多晋升条数")


class TaskMapUpdateRequest(BaseModel):
    """更新任务状态图节点请求体。"""
    node_id: str = Field(..., min_length=1, description="节点 ID")
    status: str = Field("active", description="节点状态: todo/active/done/failed")
    description: str = Field("", description="节点描述")
    parent_id: str = Field("", description="父节点 ID")
    evidence_ref: str = Field("", description="关联证据 ref_id")
    metadata: dict[str, Any] | None = Field(None, description="附加元数据")


# ── 1. 漏斗记忆统计 ────────────────────────────────────────────────

@router.get("/api/memory/funnel/stats")
@handle_api_errors("Funnel memory stats", error_value={"status": "error", "error": "Funnel stats unavailable", "stats": {}})
async def api_memory_funnel_stats() -> dict[str, Any]:
    """返回漏斗记忆统计信息（L0 条数、L1 条数、各 session 条数等）。

    汇总 L0 证据层、L1 原子事实层、符号化短期记忆（任务图）的统计。
    """
    facade = _funnel_facade()
    stats: dict[str, Any] = {"l0_evidence": {}, "l1_atoms": {}, "symbolic": {}}

    # L0 证据统计
    ev_store = facade.evidence_store()
    if ev_store is not None:
        stats["l0_evidence"] = ev_store.stats()

    # L1 原子事实统计
    atoms = facade.atom_facts()
    if atoms is not None:
        stats["l1_atoms"] = atoms.stats()

    # 符号化短期记忆统计
    sym = facade.symbolic()
    if sym is not None:
        stats["symbolic"] = sym.stats()

    return {"status": "ok", "stats": stats}


# ── 2. L0 证据列表 ────────────────────────────────────────────────

@router.get("/api/memory/funnel/evidence")
@handle_api_errors("Funnel evidence list", error_value={"status": "error", "error": "Evidence list unavailable", "items": [], "count": 0})
async def api_memory_funnel_evidence_list(
    request: Request,
    limit: int = Query(50, ge=1, le=1000, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    session_id: str = Query("", description="按会话 ID 过滤"),
    kind: str = Query("", description="按证据种类过滤"),
) -> dict[str, Any]:
    """L0 证据列表（支持分页 limit/offset，支持 session_id 过滤）。"""
    facade = _funnel_facade()
    ev_store = facade.evidence_store()
    if ev_store is None:
        raise HTTPException(503, "Evidence store unavailable")

    # 底层 search_evidence 不支持 offset，取 limit+offset 后切片
    fetch_top = limit + offset
    items = ev_store.search_evidence(
        query="", session_id=session_id, kind=kind, top=fetch_top,
    )
    page = items[offset:offset + limit]
    page = _tenant_filter(page, _request_tenant_id(request))
    return {
        "status": "ok",
        "items": page,
        "count": len(page),
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


# ── 3. 单条证据详情 ───────────────────────────────────────────────

@router.get("/api/memory/funnel/evidence/{ref_id}")
@handle_api_errors("Funnel evidence detail", error_value={"status": "error", "error": "Evidence not found"})
async def api_memory_funnel_evidence_detail(ref_id: str) -> dict[str, Any]:
    """单条证据详情（含 refs 内容）。

    返回证据元数据 + 完整原文（外置时从 refs/*.md 读取）。
    """
    facade = _funnel_facade()
    ev_store = facade.evidence_store()
    if ev_store is None:
        raise HTTPException(503, "Evidence store unavailable")

    meta = ev_store.get_evidence_meta(ref_id)
    if meta is None:
        raise HTTPException(404, f"Evidence {ref_id} not found")

    content = ev_store.get_evidence(ref_id)
    return {"status": "ok", "evidence": {**meta, "content": content}}


# ── 4. 清理旧证据 ────────────────────────────────────────────────

@router.post("/api/memory/funnel/evidence/prune")
@handle_api_errors("Funnel evidence prune", error_value={"status": "error", "error": "Prune failed", "deleted": 0})
async def api_memory_funnel_evidence_prune(request: Request) -> dict[str, Any]:
    """清理旧证据（参数: older_than_days, session_id, kind, limit）。

    Body: :class:`EvidencePruneRequest`
    """
    require_admin(request)
    body = EvidencePruneRequest.model_validate(await request.json())

    facade = _funnel_facade()
    ev_store = facade.evidence_store()
    if ev_store is None:
        raise HTTPException(503, "Evidence store unavailable")

    deleted = ev_store.prune(
        older_than_days=body.older_than_days,
        session_id=body.session_id,
        kind=body.kind,
        limit=body.limit,
    )
    return {"status": "ok", "deleted": deleted}


# ── 5. L1 原子事实列表 ────────────────────────────────────────────

@router.get("/api/memory/funnel/facts")
@handle_api_errors("Funnel facts list", error_value={"status": "error", "error": "Facts list unavailable", "items": [], "count": 0})
async def api_memory_funnel_facts_list(
    request: Request,
    limit: int = Query(50, ge=1, le=1000, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    session_id: str = Query("", description="按会话 ID 过滤（通过 source_ref 关联）"),
    topic: str = Query("", description="按主题过滤"),
) -> dict[str, Any]:
    """L1 原子事实列表（支持分页，支持 session_id 过滤）。

    session_id 过滤通过 source_ref 关联 L0 证据的 session_id 实现。
    """
    facade = _funnel_facade()
    atoms = facade.atom_facts()
    if atoms is None:
        raise HTTPException(503, "Atom facts store unavailable")

    fetch_top = limit + offset
    items = atoms.search_facts(query="", topic=topic, top=fetch_top)

    # 按 session_id 过滤：通过 source_ref 关联 L0 证据的 session_id
    if session_id:
        ev_store = facade.evidence_store()
        if ev_store is not None:
            ev_refs = {
                r["ref_id"]
                for r in ev_store.search_evidence(
                    query="", session_id=session_id, top=10000,
                )
            }
            items = [it for it in items if it.get("source_ref") in ev_refs]

    page = items[offset:offset + limit]
    page = _tenant_filter(page, _request_tenant_id(request))
    return {
        "status": "ok",
        "items": page,
        "count": len(page),
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


# ── 6. 单条事实详情 ───────────────────────────────────────────────

@router.get("/api/memory/funnel/facts/{fact_id}")
@handle_api_errors("Funnel fact detail", error_value={"status": "error", "error": "Fact not found"})
async def api_memory_funnel_fact_detail(fact_id: str) -> dict[str, Any]:
    """单条事实详情。"""
    facade = _funnel_facade()
    atoms = facade.atom_facts()
    if atoms is None:
        raise HTTPException(503, "Atom facts store unavailable")

    fact = atoms.get_fact(fact_id)
    if fact is None:
        raise HTTPException(404, f"Fact {fact_id} not found")

    return {"status": "ok", "fact": fact}


# ── 7. 晋升事实到 L3 长期记忆 ────────────────────────────────────

@router.post("/api/memory/funnel/facts/promote")
@handle_api_errors("Funnel facts promote", error_value={"status": "error", "error": "Promote failed", "promoted": 0})
async def api_memory_funnel_facts_promote(request: Request) -> dict[str, Any]:
    """晋升事实到 L3 长期记忆（参数: fact_ids 列表）。

    Body: :class:`FactsPromoteRequest`

    - 若提供 ``fact_ids``：按指定 ID 晋升（通过 vector_index_fn 写入 L3）。
    - 否则按 ``min_access`` 阈值批量晋升高频事实。
    """
    require_admin(request)
    body = FactsPromoteRequest.model_validate(await request.json())

    facade = _funnel_facade()
    atoms = facade.atom_facts()
    if atoms is None:
        raise HTTPException(503, "Atom facts store unavailable")

    # 复用 facade 的 long_term_index 作为向量索引写入函数
    def _vector_index_fn(doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> str:
        return facade.long_term_index(doc_id, text, metadata=metadata)

    if body.fact_ids:
        # 按指定 ID 晋升：逐条读取并写入向量索引，重置 access_count
        promoted = 0
        for fact_id in body.fact_ids:
            fact = atoms.get_fact(fact_id)
            if not fact:
                continue
            text = f"{fact['subject']} {fact['predicate']} {fact['object_value']}"
            try:
                _vector_index_fn(fact["id"], text, {
                    "topic": fact.get("topic", ""),
                    "source_ref": fact.get("source_ref", ""),
                    "layer": "atom_fact",
                })
                promoted += 1
            except Exception as exc:
                logger.warning("[funnel_api] promote fact %s failed: %s", fact_id, exc)
        return {"status": "ok", "promoted": promoted, "fact_ids": body.fact_ids}

    # 按 min_access 批量晋升
    report = atoms.promote_facts(
        min_access=body.min_access,
        top=body.top,
        vector_index_fn=_vector_index_fn,
    )
    return {"status": "ok", "promoted": report.get("promoted", 0)}


# ── 8. 搜索事实 ──────────────────────────────────────────────────

@router.get("/api/memory/funnel/facts/search")
@handle_api_errors("Funnel facts search", error_value={"status": "error", "error": "Facts search unavailable", "items": [], "count": 0})
async def api_memory_funnel_facts_search(
    request: Request,
    query: str = Query("", description="搜索关键词"),
    limit: int = Query(10, ge=1, le=200, description="返回条数"),
    topic: str = Query("", description="按主题过滤"),
) -> dict[str, Any]:
    """搜索事实（参数: query, limit）。"""
    facade = _funnel_facade()
    items = facade.search_facts(query=query, topic=topic, top=limit)
    items = _tenant_filter(items, _request_tenant_id(request))
    return {"status": "ok", "query": query, "items": items, "count": len(items)}


# ── 9. 任务状态图（Mermaid） ─────────────────────────────────────

@router.get("/api/memory/funnel/task-map/{session_id}")
@handle_api_errors("Funnel task map", error_value={"status": "error", "error": "Task map unavailable", "mermaid": ""})
async def api_memory_funnel_task_map(session_id: str) -> dict[str, Any]:
    """获取任务状态图（返回 Mermaid 文本）。"""
    facade = _funnel_facade()
    mermaid = facade.get_task_map(session_id)
    if not mermaid:
        return {"status": "ok", "session_id": session_id, "mermaid": "", "nodes_count": 0}
    # 节点数近似为 mermaid 中节点定义的行数（去掉 ```mermaid/graph TD/``` 包裹）
    nodes_count = max(0, len(mermaid.splitlines()) - 3)
    return {"status": "ok", "session_id": session_id, "mermaid": mermaid, "nodes_count": nodes_count}


# ── 10. 任务节点列表 ─────────────────────────────────────────────

@router.get("/api/memory/funnel/task-map/{session_id}/nodes")
@handle_api_errors("Funnel task map nodes", error_value={"status": "error", "error": "Task map nodes unavailable", "nodes": [], "count": 0})
async def api_memory_funnel_task_map_nodes(session_id: str) -> dict[str, Any]:
    """获取任务节点列表。"""
    facade = _funnel_facade()
    sym = facade.symbolic()
    if sym is None:
        raise HTTPException(503, "Symbolic memory unavailable")

    nodes = sym.get_task_map_nodes(session_id)
    return {"status": "ok", "session_id": session_id, "nodes": nodes, "count": len(nodes)}


# ── 11. 更新任务节点状态 ─────────────────────────────────────────

@router.post("/api/memory/funnel/task-map/{session_id}/update")
@handle_api_errors("Funnel task map update", error_value={"status": "error", "error": "Task map update failed"})
async def api_memory_funnel_task_map_update(session_id: str, request: Request) -> dict[str, Any]:
    """更新任务节点状态（参数: node_id, status, description）。

    Body: :class:`TaskMapUpdateRequest`
    """
    require_admin(request)
    body = TaskMapUpdateRequest.model_validate(await request.json())

    facade = _funnel_facade()
    ok = facade.update_task_map(
        session_id=session_id,
        step_id=body.node_id,
        description=body.description,
        status=body.status,
        parent_id=body.parent_id,
        evidence_ref=body.evidence_ref,
        metadata=body.metadata,
    )
    if not ok:
        raise HTTPException(400, f"Failed to update node {body.node_id} (invalid status or node limit reached)")

    return {"status": "ok", "session_id": session_id, "node_id": body.node_id, "updated": True}
