"""MAOP Three-Layer Memory System — Working / Episodic / Semantic.

Inspired by OmniAgent's layered memory architecture with decay policies.

Layer 1 — Working Memory: In-process LRU cache (session-scoped, fast).
Layer 2 — Episodic Memory: Task experiences with time-decay retrieval.
Layer 3 — Semantic Memory: Vector-indexed knowledge (delegates to VectorStore).

Consolidation: Periodically extracts knowledge from Episodic → Semantic.

Usage::

    from maop.core.memory.three_layer_memory import ThreeLayerMemory

    mem = ThreeLayerMemory(root_dir="/path/to/MAOP")

    # Working Memory (fast, session-scoped)
    mem.working_put("current_task", {"agent": "claude", "step": 3})

    # Episodic Memory (task experiences)
    mem.episodic_store(
        task="Fix login timeout", agent="claude",
        outcome="success", score=0.9,
        lessons=["Always set socket timeout before connect"],
    )

    # Semantic Memory (vector search)
    results = mem.semantic_search("authentication timeout", top=5)

    # Consolidation (Episodic → Semantic)
    mem.consolidate()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, cast

from maop.core.reliability.cache import LRUCache
from maop.core.memory.working_memory import WorkingMemoryMixin
from maop.core.memory.semantic import SemanticMixin
from maop.core.memory.transform import TransformMixin
from maop.core.memory.episodic_store import EpisodicStoreMixin
from maop.core.backends.db_utils import sqlite_connect

# 共享 DB 路径与术语映射（统一 ThreeLayerMemory 与 MemoryManager 的 DB 文件）
from maop.memory.shared_db import (
    get_memory_db_path,
    migrate_legacy_episodic_db,
    normalize_layer_name,
)

logger = logging.getLogger(__name__)


# ── 术语映射：ThreeLayerMemory 命名 ↔ MemoryManager 命名 ──────
# 统一映射到 MemoryManager 的标准命名（working/short_term/long_term）
# episodic 等价于 short_term，semantic 等价于 long_term
LAYER_NAME_MAP: dict[str, str] = {
    "working": "working",
    "episodic": "short_term",   # episodic 等价于 short_term
    "semantic": "long_term",    # semantic 等价于 long_term
}


# ── Models ────────────────────────────────────────────────────

from maop.core.memory.three_layer_memory_types import (
    ConsolidationReport,
    ContextHead,
    ContextItem,
    EpisodicEntry,
    EpisodicSearchResult,
    FocusConfig,
    FocusMode,
    HeadResult,
    MultiHeadResult,
    QualityDimensions,
    TransformResult,
    decay_weight,
)
from maop.core.memory.three_layer_memory_utils import (
    _DEFAULT_FOCUS_CONFIGS,
    _compress_text,
    _is_negative_feedback,
    _item_to_text,
    _text_relevance,
)

# ── Episodic DDL ─────────────────────────────────────────────



# ── ThreeLayerMemory ─────────────────────────────────────────

# ── Parallel Implementation Note ──────────────────────────────
# NOTE: ThreeLayerMemory is one of two parallel three-layer memory
# implementations. The other is MemoryManager in maop/memory/manager.py.
# Both have production callers:
#   - ThreeLayerMemory (this class): used by core/agent_performance.py, core/evolution_loop.py
#   - MemoryManager: used by core/chat_engine.py (main chat engine)
# Future work: consider merging into a single canonical implementation.

# ── Three-Layer Memory Architecture ────────────────────────────
#
# Inspired by cognitive science (Atkinson-Shiffrin model adapted):
#
# L1 — Working Memory (short-term, current session)
#   Storage: in-process LRUCache + overflow to Episodic (SQLite)
#   TTL: working_ttl = 3600.0s (configurable via constructor param)
#   Capacity: working_max = 200 entries (LRU eviction when exceeded)
#   Trigger: every user turn writes to L1 via working_put()
#   Eviction: when LRU evicts an entry, on_evict callback
#             (_overflow_to_episodic) overflows it to L2 automatically
#
# L2 — Episodic Memory (medium-term, conversation history)
#   Storage: SQLite episodic_memory table (EpisodicEntry rows)
#   Promotion: L1 -> L2 via LRU eviction overflow (not access-count based)
#   Consolidation: consolidate_by_access(min_access_count=3) promotes
#                  episodic entries with access_count >= 3 to L3 (semantic)
#   Query: recency-weighted (recent episodes score higher via retrieval_weight)
#   access_count incremented on each episodic_search hit
#
# L3 — Semantic Memory (long-term, vector-indexed knowledge)
#   Storage: VectorStore (embeddings for similarity search), lazy-loaded
#   Promotion: L2 -> L3 via consolidate_by_access() or consolidate(min_score=0.7)
#   Query: semantic_search() delegates to VectorStore similarity search
#
# Consolidation Flow:
#   1. consolidate(min_score=0.7, limit=50) — score-based promotion L2 -> L3
#   2. consolidate_by_access(min_access_count=3, limit=50) — access-count based:
#      a. SELECT entries WHERE consolidated=0 AND access_count >= min_access_count
#      b. Generate text summary -> store in VectorStore (semantic)
#      c. Mark episodic entry consolidated=1
#   3. No Bloom filter or knowledge graph in this module - those live in
#      DreamConsolidator (maop.memory.consolidator) and MemoryStore.

class ThreeLayerMemory(WorkingMemoryMixin, SemanticMixin, TransformMixin, EpisodicStoreMixin):
    """Three-layer memory: Working (LRU) + Episodic (SQLite) + Semantic (Vector).

    Parameters
    ----------
    root_dir : str | Path
        MAOP project root directory.
    working_max : int
        Max entries in Working Memory LRU cache.
    working_ttl : float
        Default TTL for Working Memory entries (seconds).
    """

    def __init__(
        self,
        root_dir: str | Path,
        working_max: int = 200,
        working_ttl: float = 3600.0,
    ) -> None:
        self._root = Path(root_dir)
        self._data_dir = self._root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Layer 1: Working Memory (in-process LRU)
        # t13: pass on_evict callback so evicted entries are overflowed to
        # Episodic Memory automatically — no manual size check needed.
        self._working = LRUCache(
            max_size=working_max,
            default_ttl_s=working_ttl,
            on_evict=self._overflow_to_episodic,
        )

        # Layer 2: Episodic Memory (SQLite)
        # 改用共享 DB 路径（与 MemoryManager / MemoryStore 共用 maop.db），
        # 消除双 DB 不通信问题。episodic_memory 表与 memory_entries 表
        # schema 不同但表名不冲突，可安全共存于同一 SQLite 文件。
        self._episodic_path = get_memory_db_path()
        self._init_episodic_db()
        # 迁移旧的 <root>/data/episodic.db 数据到统一 DB（幂等）
        try:
            migrated = migrate_legacy_episodic_db(self._root)
            if migrated > 0:
                logger.info(
                    "[three_layer_memory] Migrated %d episodic entries from legacy DB",
                    migrated,
                )
        except Exception as exc:
            logger.warning("[three_layer_memory] Legacy DB migration failed: %s", exc)

        # Layer 3: Semantic Memory (lazy — delegates to VectorStore)
        self._vector_store: Any = None

    # ── Layer 1: Working Memory ───────────────────────────────



    # ── Layer 2: Episodic Memory ─────────────────────────────






    # ── 统一 API（与 MemoryManager 术语对齐） ─────────────
    # 接受 working/short_term/long_term 或 working/episodic/semantic
    # 让 chat_engine (MemoryManager) 与 evolution_loop (ThreeLayerMemory)
    # 能够互相读取对方写入的数据。

    def store(self, layer: str, content: str, **kwargs: Any) -> str:
        """统一 layer 存储入口。

        将 ``layer`` 标准化后路由到对应层：
          - working    -> working_put (LRU 内存)
          - short_term -> episodic_store (等价于 episodic)
          - long_term  -> semantic_index (等价于 semantic)

        接受两套命名：``short_term``/``episodic``、``long_term``/``semantic``。
        返回写入条目的 ID。
        """
        normalized = normalize_layer_name(layer)
        if normalized == "working":
            key = kwargs.get("key", "") or f"mem-{int(time.time() * 1000)}"
            self.working_put(key, content, ttl_s=kwargs.get("ttl_s"))
            return key
        if normalized == "short_term":
            # episodic_store 不接受 content 参数，把 content 拼到 summary
            # 合并 topic/tags 到 metadata (修复: 之前 topic/tags 被静默丢弃)
            meta = dict(kwargs.get("metadata") or {})
            topic = kwargs.get("topic", "")
            tags = kwargs.get("tags", "")
            if topic:
                meta["topic"] = topic
            if tags:
                meta["tags"] = tags if isinstance(tags, str) else ",".join(str(t) for t in tags)
            trace_id = kwargs.get("trace_id", "")
            if trace_id:
                meta["trace_id"] = trace_id
            return self.episodic_store(
                task=kwargs.get("task", content[:80]),
                agent=kwargs.get("agent", ""),
                outcome=kwargs.get("outcome", ""),
                score=kwargs.get("score", 0.0),
                lessons=kwargs.get("lessons"),
                user_feedback=kwargs.get("user_feedback", ""),
                summary=content,
                key_decisions=kwargs.get("key_decisions"),
                files_touched=kwargs.get("files_touched"),
                metadata=meta,
            )
        if normalized == "long_term":
            doc_id = kwargs.get("doc_id", f"doc-{int(time.time() * 1000)}")
            return self.semantic_index(doc_id, content, metadata=kwargs.get("metadata"))
        raise ValueError(f"Unknown layer: {layer!r}")

    def retrieve(self, layer: str, query: str = "", top: int = 10, **kwargs: Any) -> list[Any]:
        """统一 layer 检索入口。

        将 ``layer`` 标准化后路由到对应层：
          - working    -> working_get (单条返回，包装为 list)
          - short_term -> episodic_search (等价于 episodic)
          - long_term  -> semantic_search (等价于 semantic)

        接受两套命名：``short_term``/``episodic``、``long_term``/``semantic``。
        """
        normalized = normalize_layer_name(layer)
        if normalized == "working":
            val = self.working_get(query) if query else None
            return [val] if val is not None else []
        if normalized == "short_term":
            return self.episodic_search(
                query=query, top=top,
                agent=kwargs.get("agent", ""),
                outcome=kwargs.get("outcome", ""),
                min_score=kwargs.get("min_score", 0.0),
                apply_decay=kwargs.get("apply_decay", True),
            )
        if normalized == "long_term":
            return self.semantic_search(query, top=top)
        raise ValueError(f"Unknown layer: {layer!r}")

    # ── MemoryManager 兼容查询 ────────────────────────────

    def query_memory_entries(self, query: str = "", top: int = 10) -> list[dict[str, Any]]:
        """查询 MemoryManager 写入的 memory_entries 表（跨实现通信）。

        ``MemoryManager`` 通过 ``MemoryStore`` 将对话交换写入同一 DB 的
        ``memory_entries`` 表。本方法让 ``ThreeLayerMemory`` 能够读取这些
        条目，从而让 evolution_loop / agent_performance 看到 chat 中存入的记忆。

        Returns
        -------
        list[dict[str, Any]]
            每行包含 id/agent/task/content/tags/topic/timestamp 等字段。
        """
        sql = "SELECT * FROM memory_entries"
        params: list[Any] = []
        if query:
            sql += " WHERE task LIKE ? OR content LIKE ?"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(top)
        try:
            with self._episodic_connect() as conn:
                cursor = conn.execute(sql, params)
                cols = [d[0] for d in cursor.description] if cursor.description else []
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[three_layer_memory] query_memory_entries failed: %s", exc)
            return []


    def consolidate_by_access(self, min_access_count: int = 3, limit: int = 50) -> ConsolidationReport:
        """Auto-promote frequently recalled episodic entries to Semantic Memory.

        Entries with access_count >= min_access_count that haven't been
        consolidated yet are automatically promoted to long-term (Semantic) memory.

        Returns a ConsolidationReport with promotion stats.
        """
        report = ConsolidationReport()

        with self._episodic_connect() as conn:
            cursor = conn.execute(
                """SELECT * FROM episodic_memory
                   WHERE consolidated = 0 AND access_count >= ?
                   ORDER BY access_count DESC LIMIT ?""",
                (min_access_count, limit),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            report.candidates = len(rows)

            if not rows:
                return report

            vs = self._get_vector_store()

            for row in rows:
                d = dict(zip(cols, row))
                entry_id = d["id"]
                task = d["task"]
                agent = d["agent"]
                outcome = d["outcome"]
                score = d["score"]
                lessons = json.loads(d.get("lessons", "[]"))
                access_count = d.get("access_count", 0)

                parts = [f"Task: {task}", f"Agent: {agent}", f"Outcome: {outcome}"]
                if lessons:
                    parts.append(f"Lessons: {'; '.join(lessons)}")
                parts.append(f"AccessCount: {access_count}")
                text = " | ".join(parts)

                try:
                    vs.index(
                        entry_id=f"access_consolidated:{entry_id}",
                        text=text,
                        metadata={
                            "source": "access_consolidation",
                            "agent": agent,
                            "outcome": outcome,
                            "score": score,
                            "access_count": access_count,
                        },
                    )
                    conn.execute(
                        "UPDATE episodic_memory SET consolidated = 1 WHERE id = ?",
                        (entry_id,),
                    )
                    report.consolidated += 1
                except Exception as exc:
                    logger.warning("Access-consolidation failed for %s: %s", entry_id[:8], exc)
                    report.errors += 1

        logger.info(
            "Access-consolidation: %d/%d promoted, %d errors",
            report.consolidated, report.candidates, report.errors,
        )
        return report




    # ── Consolidation (Episodic → Semantic) ──────────────────

    def consolidate(self, min_score: float = 0.7, limit: int = 50) -> ConsolidationReport:
        """Extract high-value episodic memories into Semantic Memory.

        Only consolidates entries with score >= min_score that haven't
        been consolidated yet.
        """
        report = ConsolidationReport()

        with self._episodic_connect() as conn:
            cursor = conn.execute(
                """SELECT * FROM episodic_memory
                   WHERE consolidated = 0 AND score >= ?
                   ORDER BY score DESC LIMIT ?""",
                (min_score, limit),
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            report.candidates = len(rows)

            if not rows:
                return report

            vs = self._get_vector_store()

            for row in rows:
                d = dict(zip(cols, row))
                entry_id = d["id"]
                task = d["task"]
                agent = d["agent"]
                outcome = d["outcome"]
                score = d["score"]
                lessons = json.loads(d.get("lessons", "[]"))

                # Build consolidation text
                parts = [f"Task: {task}", f"Agent: {agent}", f"Outcome: {outcome}"]
                if lessons:
                    parts.append(f"Lessons: {'; '.join(lessons)}")
                if d.get("user_feedback"):
                    parts.append(f"Feedback: {d['user_feedback']}")
                text = " | ".join(parts)

                try:
                    vs.index(
                        entry_id=f"episodic:{entry_id}",
                        text=text,
                        metadata={
                            "source": "episodic",
                            "agent": agent,
                            "outcome": outcome,
                            "score": score,
                        },
                    )
                    conn.execute(
                        "UPDATE episodic_memory SET consolidated = 1 WHERE id = ?",
                        (entry_id,),
                    )
                    report.consolidated += 1
                except Exception as exc:
                    logger.warning("Consolidation failed for %s: %s", entry_id[:8], exc)
                    report.errors += 1

        logger.info(
            "Consolidation: %d/%d consolidated, %d errors",
            report.consolidated, report.candidates, report.errors,
        )
        return report

    # ── UnifiedMemoryProtocol 别名（统一术语: short_term / long_term） ──
    # 让 ThreeLayerMemory 实现 maop.memory.unified.UnifiedMemoryProtocol。
    # 公开 API 使用 MemoryManager 的标准术语，内部映射到 episodic / semantic。
    # 详见 maop/memory/unified.py 与 maop/memory/facade.py。

    def short_term_store(
        self,
        content: str,
        *,
        task: str = "",
        agent: str = "",
        topic: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """写入 Short-term Memory（等价于 episodic_store）。

        将 ``content`` 写入 ``summary`` 字段，``topic`` / ``tags`` 合并到
        ``metadata``，其余参数透传给 ``episodic_store``。
        """
        meta = dict(metadata or {})
        if topic:
            meta["topic"] = topic
        if tags:
            meta["tags"] = tags if isinstance(tags, str) else ",".join(str(t) for t in tags)
        return self.episodic_store(
            task=task or content[:80],
            agent=agent,
            summary=content,
            metadata=meta,
        )

    def short_term_search(
        self,
        query: str = "",
        *,
        top: int = 10,
        agent: str = "",
    ) -> list[dict[str, Any]]:
        """检索 Short-term Memory（等价于 episodic_search），返回 dict 列表。"""
        results = self.episodic_search(query=query, top=top, agent=agent)
        return [
            {
                "id": r.entry.id,
                "task": r.entry.task,
                "agent": r.entry.agent,
                "outcome": r.entry.outcome,
                "score": r.entry.score,
                "summary": r.entry.summary,
                "lessons": r.entry.lessons,
                "user_feedback": r.entry.user_feedback,
                "retrieval_weight": r.retrieval_weight,
                "created_at": r.entry.created_at,
                "metadata": r.entry.metadata,
            }
            for r in results
        ]

    def short_term_get(self, entry_id: str) -> dict[str, Any] | None:
        """按 ID 获取单条 Short-term Memory 条目（等价于 episodic_get）。"""
        entry = self.episodic_get(entry_id)
        if entry is None:
            return None
        return entry.model_dump()

    def short_term_stats(self) -> dict[str, Any]:
        """Short-term Memory 统计信息（等价于 episodic_stats）。"""
        return self.episodic_stats()

    def long_term_index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """索引文档到 Long-term Memory（等价于 semantic_index）。"""
        return self.semantic_index(doc_id, text, metadata=metadata)

    def long_term_search(
        self,
        query: str,
        *,
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """检索 Long-term Memory（等价于 semantic_search），返回 dict 列表。"""
        results = self.semantic_search(query, top=top)
        out: list[dict[str, Any]] = []
        for r in results:
            if hasattr(r, "model_dump"):
                out.append(r.model_dump())
            elif isinstance(r, dict):
                out.append(r)
            else:
                out.append({"text": str(r)})
        return out

    def build_context(
        self,
        session_id: str = "",
        query: str = "",
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """组装三层上下文，返回 dict。

        聚合 working_get + episodic_search + semantic_search 结果。
        ``session_id`` 与 ``max_tokens`` 在 ThreeLayerMemory 中不直接使用
        （L1 是 LRU 而非会话窗口），仅用于 Protocol 签名兼容。
        """
        working: Any = None
        if query:
            working = self.working_get(query)

        short_term = self.short_term_search(query=query, top=10)
        try:
            long_term = self.long_term_search(query, top=5) if query else []
        except Exception as exc:
            logger.debug("[three_layer_memory] build_context long_term skipped: %s", exc)
            long_term = []

        return {
            "working": working,
            "short_term": short_term,
            "long_term": long_term,
            "session_id": session_id,
            "max_tokens": max_tokens,
        }

    def stats(self) -> dict[str, Any]:
        """跨层统计信息汇总。"""
        return {
            "working_size": self._working.size(),
            "short_term": self.episodic_stats(),
        }

    # ── F1-03 统一 CRUD 入口（store/retrieve 已存在，补 search/delete） ──

    def search(self, query: str, *, top: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """跨层搜索：合并 short_term + long_term 结果，附带 ``layer`` 字段。

        F1-03 新增：实现 UnifiedMemoryProtocol.search。
        """
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            for r in self.short_term_search(query=query, top=top, agent=kwargs.get("agent", "")):
                if isinstance(r, dict):
                    rid = str(r.get("id", ""))
                    if rid and rid in seen_ids:
                        continue
                    if rid:
                        seen_ids.add(rid)
                    entry = dict(r)
                    entry.setdefault("layer", "short_term")
                    merged.append(entry)
        except Exception as exc:
            logger.debug("[three_layer_memory] search short_term failed: %s", exc)

        try:
            for r in self.long_term_search(query, top=top):
                if isinstance(r, dict):
                    rid = str(r.get("id", ""))
                    if rid and rid in seen_ids:
                        continue
                    if rid:
                        seen_ids.add(rid)
                    entry = dict(r)
                    entry.setdefault("layer", "long_term")
                    merged.append(entry)
        except Exception as exc:
            logger.debug("[three_layer_memory] search long_term failed: %s", exc)

        return merged[:top] if top > 0 else merged

    def delete(self, layer: str, entry_id: str) -> bool:
        """按 ID 删除指定层的条目。

        F1-03 新增：实现 UnifiedMemoryProtocol.delete。
        """
        normalized = normalize_layer_name(layer)
        if normalized == "working":
            self.working_delete(entry_id)
            return True
        if normalized == "short_term":
            try:
                with self._episodic_connect() as conn:
                    cur = conn.execute(
                        "DELETE FROM episodic_memory WHERE id = ?", (entry_id,)
                    )
                    return int(cur.rowcount or 0) > 0
            except Exception as exc:
                logger.warning("[three_layer_memory] delete short_term failed: %s", exc)
                return False
        if normalized == "long_term":
            vs = self._get_vector_store()
            delete_fn = getattr(vs, "delete", None)
            if callable(delete_fn):
                try:
                    delete_fn(entry_id)
                    return True
                except Exception as exc:
                    logger.debug("[three_layer_memory] vector delete failed: %s", exc)
                    return False
            return False
        raise ValueError(f"Unknown layer: {layer!r}")


# ── Transform Helpers ─────────────────────────────────────────

