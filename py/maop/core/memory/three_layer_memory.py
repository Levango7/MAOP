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

_EPISODIC_DDL = """
CREATE TABLE IF NOT EXISTS episodic_memory (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    agent TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    score REAL DEFAULT 0.0,
    lessons TEXT DEFAULT '[]',
    user_feedback TEXT DEFAULT '',
    quality_dimensions TEXT DEFAULT '{}',
    summary TEXT DEFAULT '',
    key_decisions TEXT DEFAULT '[]',
    files_touched TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    consolidated INTEGER DEFAULT 0,
    access_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodic_agent ON episodic_memory(agent);
CREATE INDEX IF NOT EXISTS idx_episodic_outcome ON episodic_memory(outcome);
CREATE INDEX IF NOT EXISTS idx_episodic_score ON episodic_memory(score DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memory(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_consolidated ON episodic_memory(consolidated);
CREATE INDEX IF NOT EXISTS idx_episodic_access ON episodic_memory(access_count DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS episodic_memory_fts USING fts5(
    task, agent, summary, user_feedback,
    content='episodic_memory',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS episodic_ai AFTER INSERT ON episodic_memory BEGIN
    INSERT INTO episodic_memory_fts(rowid, task, agent, summary, user_feedback)
    VALUES (new.rowid, new.task, new.agent, new.summary, new.user_feedback);
END;

CREATE TRIGGER IF NOT EXISTS episodic_ad AFTER DELETE ON episodic_memory BEGIN
    INSERT INTO episodic_memory_fts(episodic_memory_fts, rowid, task, agent, summary, user_feedback)
    VALUES ('delete', old.rowid, old.task, old.agent, old.summary, old.user_feedback);
END;

CREATE TRIGGER IF NOT EXISTS episodic_au AFTER UPDATE ON episodic_memory BEGIN
    INSERT INTO episodic_memory_fts(episodic_memory_fts, rowid, task, agent, summary, user_feedback)
    VALUES ('delete', old.rowid, old.task, old.agent, old.summary, old.user_feedback);
    INSERT INTO episodic_memory_fts(rowid, task, agent, summary, user_feedback)
    VALUES (new.rowid, new.task, new.agent, new.summary, new.user_feedback);
END;
"""


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

class ThreeLayerMemory(WorkingMemoryMixin):
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

    @staticmethod
    def _row_to_episodic(d: dict[str, Any]) -> EpisodicEntry:
        """Convert a DB row dict to an EpisodicEntry, parsing JSON fields."""
        return EpisodicEntry(
            id=d["id"],
            task=d["task"],
            agent=d.get("agent", ""),
            outcome=d.get("outcome", ""),
            score=d.get("score", 0.0),
            lessons=json.loads(d.get("lessons", "[]")),
            user_feedback=d.get("user_feedback", ""),
            quality_dimensions=ThreeLayerMemory._parse_qd(d.get("quality_dimensions", "{}")),
            summary=d.get("summary", ""),
            key_decisions=json.loads(d.get("key_decisions", "[]")),
            files_touched=json.loads(d.get("files_touched", "[]")),
            metadata=json.loads(d.get("metadata", "{}")),
            created_at=d["created_at"],
            access_count=d.get("access_count", 0),
        )

    @staticmethod
    def _parse_qd(raw: str | dict[str, Any] | None) -> QualityDimensions:
        """Parse quality_dimensions from JSON string or dict."""
        if raw is None:
            return QualityDimensions()
        if isinstance(raw, QualityDimensions):
            return raw
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
            return QualityDimensions(**d)
        except (json.JSONDecodeError, TypeError, ValueError):
            return QualityDimensions()

    # ── Layer 2: Episodic Memory ─────────────────────────────

    def _init_episodic_db(self) -> None:
        """Create episodic tables if not exists."""
        with self._episodic_connect() as conn:
            conn.executescript(_EPISODIC_DDL)

    def _episodic_connect(self):
        return sqlite_connect(self._episodic_path, foreign_keys=False)

    def episodic_store(
        self,
        task: str,
        agent: str = "",
        outcome: str = "",
        score: float = 0.0,
        lessons: list[str] | None = None,
        user_feedback: str = "",
        quality_dimensions: QualityDimensions | None = None,
        summary: str = "",
        key_decisions: list[str] | None = None,
        files_touched: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a task experience in Episodic Memory.

        Returns the entry ID.
        """
        qd = quality_dimensions or QualityDimensions()
        if score == 0.0 and qd.composite() > 0:
            score = qd.composite()
        entry = EpisodicEntry(
            task=task, agent=agent, outcome=outcome, score=score,
            lessons=lessons or [], user_feedback=user_feedback,
            quality_dimensions=qd, summary=summary,
            key_decisions=key_decisions or [], files_touched=files_touched or [],
            metadata=metadata or {},
        )
        with self._episodic_connect() as conn:
            conn.execute(
                """INSERT INTO episodic_memory
                   (id, task, agent, outcome, score, lessons, user_feedback,
                    quality_dimensions, summary, key_decisions, files_touched, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.id, entry.task, entry.agent, entry.outcome, entry.score,
                 json.dumps(entry.lessons), entry.user_feedback,
                 json.dumps(entry.quality_dimensions.model_dump()),
                 entry.summary, json.dumps(entry.key_decisions),
                 json.dumps(entry.files_touched),
                 json.dumps(entry.metadata), entry.created_at),
            )
        logger.debug("Episodic stored: %s (outcome=%s score=%.2f)", entry.id[:8], outcome, score)
        return entry.id

    def episodic_search(
        self,
        query: str = "",
        agent: str = "",
        outcome: str = "",
        min_score: float = 0.0,
        top: int = 10,
        apply_decay: bool = True,
    ) -> list[EpisodicSearchResult]:
        """Search episodic memories with FTS5 full-text search and decay weighting.

        When a query is provided, uses FTS5 for high-quality full-text search.
        Falls back to LIKE if FTS5 is unavailable.
        Results are ranked by (score * decay_weight) descending.
        """
        with self._episodic_connect() as conn:
            rows: list | None = None
            cols: list[str] = []
            if query:
                # High fix (FTS5 injection): quote each token as an FTS5
                # string literal (internal double quotes escaped by doubling)
                # so user input cannot inject FTS5 syntax (*, -, NEAR, etc.).
                tokens = [t.replace('"', '""') for t in query.split() if t]
                fts_query = " OR ".join(f'"{t}"' for t in tokens)
                sql = """SELECT em.* FROM episodic_memory em
                         JOIN episodic_memory_fts fts ON em.rowid = fts.rowid
                         WHERE episodic_memory_fts MATCH ?"""
                params: list[Any] = [fts_query]
                if agent:
                    sql += " AND em.agent = ?"
                    params.append(agent)
                if outcome:
                    sql += " AND em.outcome = ?"
                    params.append(outcome)
                if min_score > 0:
                    sql += " AND em.score >= ?"
                    params.append(min_score)
                sql += " ORDER BY em.created_at DESC LIMIT ?"
                params.append(top * 3)
                # High fix: actually execute inside try so the LIKE fallback
                # triggers when FTS5 is unavailable (previously the except
                # branch was unreachable — only string building was wrapped).
                try:
                    cursor = conn.execute(sql, params)
                    cols = (
                        [d[0] for d in cursor.description]
                        if cursor.description else []
                    )
                    rows = cursor.fetchall()
                except sqlite3.OperationalError:
                    rows = None  # fall back to LIKE below
                if rows is None:
                    sql = "SELECT * FROM episodic_memory WHERE task LIKE ?"
                    params = [f"%{query}%"]
                    if agent:
                        sql += " AND agent = ?"
                        params.append(agent)
                    if outcome:
                        sql += " AND outcome = ?"
                        params.append(outcome)
                    if min_score > 0:
                        sql += " AND score >= ?"
                        params.append(min_score)
                    sql += " ORDER BY created_at DESC LIMIT ?"
                    params.append(top * 3)
            else:
                sql = "SELECT * FROM episodic_memory WHERE 1=1"
                params = []
                if agent:
                    sql += " AND agent = ?"
                    params.append(agent)
                if outcome:
                    sql += " AND outcome = ?"
                    params.append(outcome)
                if min_score > 0:
                    sql += " AND score >= ?"
                    params.append(min_score)
                sql += " ORDER BY created_at DESC LIMIT ?"
                params.append(top * 3)

            if rows is None:
                cursor = conn.execute(sql, params)
                cols = (
                    [d[0] for d in cursor.description]
                    if cursor.description else []
                )
                rows = cursor.fetchall()

        results = []
        for row in rows:
            d = dict(zip(cols, row))
            entry = self._row_to_episodic(d)
            weight = decay_weight(entry.created_at) if apply_decay else 1.0
            results.append(EpisodicSearchResult(entry=entry, retrieval_weight=weight))

        entry_ids = [r.entry.id for r in results]
        if entry_ids:
            self._increment_access_counts(entry_ids)

        results.sort(key=lambda r: r.entry.score * r.retrieval_weight, reverse=True)
        return results[:top]

    def episodic_get(self, entry_id: str) -> EpisodicEntry | None:
        """Get a single episodic entry by ID."""
        with self._episodic_connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM episodic_memory WHERE id = ?", (entry_id,)
            )
            cols = [d[0] for d in cursor.description] if cursor.description else []
            row = cursor.fetchone()
        if not row:
            return None

        d = dict(zip(cols, row))
        return self._row_to_episodic(d)

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

    def _increment_access_counts(self, entry_ids: list[str]) -> None:
        """Increment access_count for the given entry IDs (P3: access-count consolidation)."""
        if not entry_ids:
            return
        with self._episodic_connect() as conn:
            for eid in entry_ids:
                conn.execute(
                    "UPDATE episodic_memory SET access_count = access_count + 1 WHERE id = ?",
                    (eid,),
                )

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

    def episodic_stats(self) -> dict[str, Any]:
        """Get episodic memory statistics."""
        with self._episodic_connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM episodic_memory").fetchone()[0]
            by_outcome = dict(
                conn.execute(
                    "SELECT outcome, COUNT(*) FROM episodic_memory GROUP BY outcome"
                ).fetchall()
            )
            avg_score = conn.execute(
                "SELECT AVG(score) FROM episodic_memory"
            ).fetchone()[0] or 0.0
            consolidated = conn.execute(
                "SELECT COUNT(*) FROM episodic_memory WHERE consolidated = 1"
            ).fetchone()[0]
        return {
            "total": total,
            "by_outcome": by_outcome,
            "avg_score": round(avg_score, 3),
            "consolidated": consolidated,
            "unconsolidated": total - consolidated,
        }

    def episodic_update_feedback(
        self,
        entry_id: str,
        user_feedback: str = "",
        quality_dimensions: QualityDimensions | None = None,
    ) -> bool:
        """Update user feedback and/or quality dimensions for an episodic entry.

        Returns True if the entry was found and updated.
        """
        with self._episodic_connect() as conn:
            row = conn.execute(
                "SELECT id FROM episodic_memory WHERE id = ?", (entry_id,)
            ).fetchone()
            if not row:
                return False

            sets: list[str] = []
            params: list[Any] = []
            if user_feedback:
                sets.append("user_feedback = ?")
                params.append(user_feedback)
            if quality_dimensions is not None:
                sets.append("quality_dimensions = ?")
                params.append(json.dumps(quality_dimensions.model_dump()))
                new_score = quality_dimensions.composite()
                if new_score > 0:
                    sets.append("score = ?")
                    params.append(new_score)
            if not sets:
                return True

            params.append(entry_id)
            conn.execute(
                f"UPDATE episodic_memory SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        logger.debug("Feedback updated for %s", entry_id[:8])
        return True

    def submit_feedback(
        self,
        entry_id: str,
        user_feedback: str,
        quality_dimensions: QualityDimensions | None = None,
    ) -> dict[str, Any]:
        """Submit user feedback for an episodic entry and trigger evolution reflection.

        If the feedback indicates low quality (composite < 0.5 or negative sentiment),
        automatically triggers an evolution reflection cycle.

        Returns a dict with the update status and any triggered actions.
        """
        updated = self.episodic_update_feedback(
            entry_id, user_feedback=user_feedback, quality_dimensions=quality_dimensions,
        )

        triggered_actions: list[str] = []
        qd = quality_dimensions or QualityDimensions()
        composite = qd.composite()

        if updated and (composite < 0.5 or _is_negative_feedback(user_feedback)):
            triggered_actions.append("evolution_reflection")
            # High fix: run the evolution cycle in a background daemon thread
            # instead of synchronously — run_cycle() may involve LLM API calls
            # and DB writes with no timeout, which previously blocked the
            # caller (e.g. an HTTP handler) indefinitely.
            def _run_evolution_cycle(root: str) -> None:
                try:
                    from maop.core.evolution.evolution_loop import EvolutionLoop
                    EvolutionLoop(root_dir=root).run_cycle()
                    logger.info("Background evolution cycle completed")
                except Exception as exc:
                    logger.warning(
                        "Evolution reflection triggered but failed: %s", exc
                    )

            import threading
            threading.Thread(
                target=_run_evolution_cycle,
                args=(str(self._root),),
                name="evolution-cycle",
                daemon=True,
            ).start()
            triggered_actions.append("evolution_cycle_scheduled")

            try:
                from maop.core.reliability.error_ledger import ErrorLedger
                ledger = ErrorLedger(root_dir=str(self._root))
                entry = self.episodic_get(entry_id)
                if entry:
                    ledger.record(
                        error_type="low_quality_feedback",
                        context=entry.task[:200],
                        pattern=f"low_quality:{entry.agent}",
                        output=user_feedback[:500],
                    )
                    triggered_actions.append("error_ledger_recorded")
            except Exception as exc:
                logger.warning("Error ledger recording failed: %s", exc)

        return {
            "entry_id": entry_id,
            "updated": updated,
            "composite_score": composite,
            "triggered_actions": triggered_actions,
        }

    # ── Layer 3: Semantic Memory ─────────────────────────────

    def _get_vector_store(self):
        """Lazy-load VectorStore for Semantic Memory."""
        if self._vector_store is None:
            from maop.core.memory.vector import VectorStore
            self._vector_store = VectorStore(db_path=str(self._data_dir / "vectors.db"))
        return self._vector_store

    def semantic_index(
        self, doc_id: str, text: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Index a document in Semantic Memory (vector search)."""
        vs = self._get_vector_store()
        return cast(str, vs.index(doc_id, text, metadata=metadata))

    def semantic_search(self, query: str, top: int = 5) -> list[Any]:
        """Search Semantic Memory by text query."""
        vs = self._get_vector_store()
        return cast(list[Any], vs.search(query, top=top))

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

    # ── Transform (Focus Mode) ────────────────────────────────

    def transform(
        self,
        query: str,
        mode: FocusMode = FocusMode.DEEP_FOCUS,
        config: FocusConfig | None = None,
        token_budget: int = 4000,
    ) -> TransformResult:
        """Apply a Transform five-step pipeline to assemble context.

        Pipeline: scoreRelevance → focusAttention → deduplicate
                  → compress → budgetControl

        Parameters
        ----------
        query : str
            The input query or task description.
        mode : FocusMode
            deep_focus / broad_scan / exploratory.
        config : FocusConfig, optional
            Override default weights and budgets.
        token_budget : int
            Max token budget for final context.

        Returns
        -------
        TransformResult
        """
        cfg = config or _DEFAULT_FOCUS_CONFIGS[mode]
        stats: dict[str, int] = {}

        # ── Gather raw context from all layers ──────────────
        items: list[ContextItem] = []

        working_data = self.working_get(query)
        if working_data is not None:
            items.append(ContextItem(layer="working", source=query, data=working_data, weight=1.0))

        episodic_results = self.episodic_search(
            query=query, top=cfg.max_results, apply_decay=True,
        )
        for er in episodic_results:
            items.append(ContextItem(
                layer="episodic", source=er.entry.id,
                data=er.entry.model_dump(), weight=er.entry.score,
            ))

        try:
            semantic_results = self.semantic_search(query, top=cfg.max_results)
            for sr in semantic_results:
                items.append(ContextItem(
                    layer="semantic", source=getattr(sr, "id", ""),
                    data=str(sr), weight=0.6,
                ))
        except Exception as exc:
            logger.debug("Semantic search skipped in transform: %s", exc)

        stats["raw_items"] = len(items)

        # ── Step 1: scoreRelevance ──────────────────────────
        for item in items:
            text = _item_to_text(item)
            item.relevance_score = _text_relevance(query, text)
            # C4 fix: decay_weight(time.time()) computed age=0 (always the top
            # tier, recency factor constantly 1.0). Use the item's actual
            # created_at from the episodic entry dump so older memories decay.
            if item.layer == "episodic":
                created_at = time.time()
                if isinstance(item.data, dict):
                    created_at = float(item.data.get("created_at") or created_at)
                recency = decay_weight(created_at)
            else:
                recency = 1.0
            item.weight = (
                item.relevance_score * cfg.relevance_weight
                + item.weight * cfg.importance_weight
                + recency * cfg.recency_weight
            )

        # ── Step 2: focusAttention ──────────────────────────
        items.sort(key=lambda i: i.weight, reverse=True)
        if mode == FocusMode.DEEP_FOCUS:
            items = items[:3]
        elif mode == FocusMode.BROAD_SCAN:
            items = items[:cfg.max_results]
        stats["after_focus"] = len(items)

        # ── Step 3: deduplicate ─────────────────────────────
        seen_hashes: set[int] = set()
        deduped: list[ContextItem] = []
        for item in items:
            h = hash(_item_to_text(item)[:200])
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped.append(item)
        items = deduped
        stats["after_dedup"] = len(items)

        # ── Step 4: compress ────────────────────────────────
        for item in items:
            text = _item_to_text(item)
            if len(text) > 500:
                item.data = _compress_text(text)
                item.compressed = True
        stats["compressed"] = sum(1 for i in items if i.compressed)

        # ── Step 5: budgetControl ───────────────────────────
        budget_items: list[ContextItem] = []
        used_tokens = 0
        for item in items:
            item_chars = len(json.dumps(item.data, default=str))
            item_tokens = item_chars // 4
            if used_tokens + item_tokens <= token_budget or item.layer == "working":
                budget_items.append(item)
                used_tokens += item_tokens
        items = budget_items
        stats["final_items"] = len(items)

        context_parts = [
            {"layer": i.layer, "source": i.source, "data": i.data,
             "weight": round(i.weight, 4), "compressed": i.compressed}
            for i in items
        ]

        n_parts = len(context_parts) or 1
        memory_parts = sum(1 for p in context_parts if p["layer"] != "input")
        memory_ratio = round(memory_parts / n_parts, 2) if context_parts else 0.0

        return TransformResult(
            mode=mode,
            context_parts=context_parts,
            total_tokens_estimate=used_tokens,
            memory_ratio=memory_ratio,
            input_ratio=round(1.0 - memory_ratio, 2),
            pipeline_stats=stats,
        )

    def transform_multi_head(
        self,
        query: str,
        heads: list[ContextHead] | None = None,
        token_budget: int = 4000,
    ) -> MultiHeadResult:
        """Apply multi-head context analysis from different perspectives.

        Each head filters and weights context items by its perspective:
          - FACTS: objective data, measurements, outcomes
          - INTENT: user goals, task descriptions, requirements
          - CONSTRAINTS: limits, rules, errors, pitfalls

        Results are fused via weighted merge (dedup + re-rank).

        Parameters
        ----------
        query : str
            The input query or task description.
        heads : list[ContextHead], optional
            Which heads to activate. Default: all three.
        token_budget : int
            Max token budget for fused context.

        Returns
        -------
        MultiHeadResult
        """
        active_heads = heads or list(ContextHead)
        all_items = self._gather_context_items(query)

        head_results: list[HeadResult] = []
        for head in active_heads:
            filtered = self._filter_by_head(all_items, head, query)
            summary = self._summarize_head(filtered, head)
            tokens = sum(len(_item_to_text(i)) // 4 for i in filtered)
            head_results.append(HeadResult(
                head=head, items=filtered, summary=summary, token_estimate=tokens,
            ))

        fused = self._fuse_heads(head_results, token_budget)

        return MultiHeadResult(
            heads=head_results,
            fused_context=fused,
            total_tokens_estimate=sum(len(json.dumps(p, default=str)) // 4 for p in fused),
            fusion_strategy="weighted_merge",
        )

    def _gather_context_items(self, query: str) -> list[ContextItem]:
        items: list[ContextItem] = []
        working_data = self.working_get(query)
        if working_data is not None:
            items.append(ContextItem(layer="working", source=query, data=working_data, weight=1.0))

        episodic_results = self.episodic_search(query=query, top=10, apply_decay=True)
        for er in episodic_results:
            items.append(ContextItem(
                layer="episodic", source=er.entry.id,
                data=er.entry.model_dump(), weight=er.entry.score,
            ))

        try:
            semantic_results = self.semantic_search(query, top=10)
            for sr in semantic_results:
                items.append(ContextItem(
                    layer="semantic", source=getattr(sr, "id", ""),
                    data=str(sr), weight=0.6,
                ))
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)

        return items

    @staticmethod
    def _filter_by_head(items: list[ContextItem], head: ContextHead, query: str) -> list[ContextItem]:
        """Filter and re-weight items by head perspective."""
        _HEAD_KEYWORDS: dict[ContextHead, set[str]] = {
            ContextHead.FACTS: {"result", "output", "data", "score", "outcome", "success", "failure", "metric", "value", "count"},
            ContextHead.INTENT: {"task", "goal", "want", "need", "require", "should", "must", "plan", "objective", "request"},
            ContextHead.CONSTRAINTS: {"error", "limit", "timeout", "budget", "rule", "constraint", "cannot", "forbidden", "pitfall", "warning"},
        }
        keywords = _HEAD_KEYWORDS.get(head, set())

        scored: list[tuple[float, ContextItem]] = []
        for item in items:
            text = _item_to_text(item).lower()
            overlap = len(keywords & set(text.split()))
            keyword_score = min(overlap / max(len(keywords), 1), 1.0) if keywords else 0.0
            relevance = _text_relevance(query, text)
            combined = item.weight * 0.4 + keyword_score * 0.4 + relevance * 0.2
            scored.append((combined, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:10]]

    @staticmethod
    def _summarize_head(items: list[ContextItem], head: ContextHead) -> str:
        if not items:
            return f"No context from {head.value} perspective"
        parts = [_item_to_text(i)[:100] for i in items[:3]]
        return f"{head.value}: {'; '.join(parts)}"

    @staticmethod
    def _fuse_heads(head_results: list[HeadResult], token_budget: int) -> list[dict[str, Any]]:
        """Fuse multi-head results via weighted merge with dedup."""
        seen_hashes: set[int] = set()
        all_weighted: list[tuple[float, ContextItem]] = []

        for hr in head_results:
            head_weight = {
                ContextHead.FACTS: 0.35,
                ContextHead.INTENT: 0.40,
                ContextHead.CONSTRAINTS: 0.25,
            }.get(hr.head, 0.3)
            for item in hr.items:
                h = hash(_item_to_text(item)[:200])
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                all_weighted.append((item.weight * head_weight, item))

        all_weighted.sort(key=lambda x: x[0], reverse=True)

        fused: list[dict[str, Any]] = []
        used_tokens = 0
        for w, item in all_weighted:
            item_tokens = len(json.dumps(item.data, default=str)) // 4
            if used_tokens + item_tokens <= token_budget:
                fused.append({
                    "layer": item.layer, "source": item.source,
                    "data": item.data, "weight": round(w, 4),
                    "compressed": item.compressed,
                })
                used_tokens += item_tokens

        return fused

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

