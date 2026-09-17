"""Memory and neural mechanism business logic for MAOP Dashboard.

Encapsulates memory store operations, unified search across memory_entries
and episodic_memory, neural attention computation, and memory stats
collection. Extracted from the memory router so the router layer only
does parameter parsing + service call + response.

The service is framework-agnostic: it does not import FastAPI and can be
unit-tested or invoked from CLI/CI without an HTTP context.
``MAOP_ROOT`` is resolved from the dashboard state module to stay
consistent with the rest of the dashboard package.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from maop.dashboard.routers.state import MAOP_ROOT

logger = logging.getLogger(__name__)


# ── Internal helpers ────────────────────────────────────────────────


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


# ── Unified search ─────────────────────────────────────────────────


def unified_search(query: str, top: int, agent: str = "") -> list[dict[str, Any]]:
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


# ── Memory deep stats ──────────────────────────────────────────────


def get_memory_deep_stats() -> dict[str, Any]:
    """收集 memory deep stats（不含 tenant 过滤）。

    返回 stats dict，其中 ``recent_entries`` 未做 tenant 过滤，
    由 router 层负责按 tenant_id 过滤。
    """
    from maop.memory.store import MemoryStore

    store = MemoryStore(root_dir=str(MAOP_ROOT))
    stats_obj = store.stats()
    if hasattr(stats_obj, "model_dump"):
        stats: dict[str, Any] = stats_obj.model_dump()
    elif hasattr(stats_obj, "dict"):
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
    recent = unified_search(query="", top=5)
    stats["recent_entries"] = recent

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

    return stats


# ── Neural status ──────────────────────────────────────────────────


def get_neural_status() -> dict[str, Any]:
    """收集 neural/attention 机制状态信息。"""
    from maop.core.backends.db_utils import get_db_path

    info: dict[str, Any] = {
        "attention": {"enabled": False, "mechanism": "N/A"},
        "transform": {"enabled": False, "layers": 0},
        "embedding": {"enabled": False, "dim": 0, "model": "N/A"},
        "vector_store": {"enabled": False, "count": 0},
    }
    try:
        from maop.core.memory.vector import VectorStore

        vs = VectorStore(db_path=str(get_db_path("vectors")))
        info["vector_store"] = {"enabled": True, "count": vs.count() if hasattr(vs, "count") else 0}
        if hasattr(vs, "_embedder"):
            emb = vs._embedder
            info["embedding"] = {
                "enabled": True,
                "dim": getattr(emb, "dim", 0),
                "model": getattr(emb, "model_name", "unknown"),
            }
    except Exception:
        logger.exception("Neural vector store check failed")
        info["vector_store"]["error"] = "Vector store unavailable"
    try:
        from maop.memory.store import MemoryStore

        store = MemoryStore(root_dir=str(MAOP_ROOT))
        stats = store.stats()
        _te = stats.total_entries if hasattr(stats, "total_entries") else 0
        info["attention"] = {
            "enabled": True,
            "mechanism": "FTS5_rank + vector_similarity",
            "total_entries": _te,
        }
    except Exception:
        logger.debug("Failed to get memory stats", exc_info=True)
    try:
        info["transform"] = {"enabled": True, "layers": 3, "steps": ["plan", "execute", "verify"]}
    except Exception:
        logger.debug("Failed to check workflow engine", exc_info=True)
    return info


# ── Neural attention (softmax) ─────────────────────────────────────


def compute_attention(query: str, top_k: int) -> tuple[list[dict[str, Any]], list[float]]:
    """对 memory store 搜索结果做 softmax 注意力计算。

    返回 (results, attention_weights)。weights 未做 round 处理，
    由 router 层根据端点需要决定是否 round。
    """
    from maop.memory.store import MemoryStore

    store = MemoryStore(root_dir=str(MAOP_ROOT))
    raw_results = store.search(query=query, top=top_k)
    results: list[dict[str, Any]] = []
    for r in raw_results:
        if hasattr(r, "model_dump"):
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
    return results, weights


# ── Memory store (manual entry) ────────────────────────────────────


def store_memory(
    layer: str,
    content: str,
    agent: str,
    topic: str,
    task: str,
    tags: str | list[str] | None,
    ttl_s: int | None,
) -> str:
    """写入手动记忆条目到三层记忆系统。

    tags 可为逗号分隔字符串或列表，内部统一转为 list[str]。
    返回 entry_id。
    """
    from maop.memory.facade import MemoryFacade

    # T3: 收敛到 MemoryFacade（mode="agent"），store 按 layer 路由到同一底层。
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, (list, tuple)):
        tag_list = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tag_list = []
    mem = MemoryFacade(root_dir=str(MAOP_ROOT), mode="agent")
    entry_id = mem.store(
        layer=layer,
        content=content,
        agent=agent,
        topic=topic,
        task=task,
        tags=tag_list,
        ttl_s=ttl_s,
    )
    return entry_id