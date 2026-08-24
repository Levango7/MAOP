"""ThreeLayerMemory Layer 2 — Episodic Memory (SQLite) mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
依赖共享 DB（``maop.db``）与 types/utils。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from maop.core.backends.db_utils import sqlite_connect
from maop.core.memory.three_layer_memory_types import (
    EpisodicEntry,
    EpisodicSearchResult,
    QualityDimensions,
    decay_weight,
)
from maop.core.memory.three_layer_memory_utils import (
    _is_negative_feedback,
)

logger = logging.getLogger(__name__)


# P1 fix: episodic_search 的硬性返回上限，防止调用方传入过大的 top
# 导致查询返回大量数据引发内存溢出。SQL LIMIT 使用 top * 3，所以
# 实际最多取 3000 行候选（排序后截取 top），内存安全。
_EPISODIC_SEARCH_MAX_LIMIT: int = 1000


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


class EpisodicStoreMixin:
    """Layer 2: Episodic Memory（SQLite 任务经验）方法。"""


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
            quality_dimensions=EpisodicStoreMixin._parse_qd(d.get("quality_dimensions", "{}")),
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
        # H8 修复：更新记忆条目数量指标
        try:
            from maop.core.monitoring.monitoring import MAOP_MEMORY_ENTRIES

            with self._episodic_connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM episodic_memory"
                ).fetchone()
            MAOP_MEMORY_ENTRIES.set(row["cnt"] if row else 0)
        except Exception:
            pass
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

        P1 fix (线程无限流保护): 对 ``top`` 参数添加硬性上限
        ``_EPISODIC_SEARCH_MAX_LIMIT``（默认 1000），防止调用方传入
        过大的值导致查询返回大量数据引发内存溢出。``top`` 会被钳制到
        [1, _EPISODIC_SEARCH_MAX_LIMIT] 范围内。SQL 始终包含 LIMIT 子句。
        """
        # P1 fix: 钳制 top 到安全范围，防止无限流/内存溢出。
        # top * 3 用于 SQL LIMIT（排序前取 3 倍候选），所以上限设为 1000
        # 意味着 SQL 层最多取 3000 行，内存安全。
        top = max(1, min(top, _EPISODIC_SEARCH_MAX_LIMIT))
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

    def _increment_access_counts(self, entry_ids: list[str]) -> None:
        """Increment access_count for the given entry IDs (P3: access-count consolidation).

        N+1 fix: 改为批量 UPDATE，使用 IN 子句一次性更新所有 entry_id，
        避免对每条记录单独执行 UPDATE 导致的 N+1 查询问题。
        对于 SQLite，单条 UPDATE ... WHERE id IN (...) 比循环 N 次
        UPDATE 快约 N 倍（减少 N-1 次语句解析和执行开销）。
        """
        if not entry_ids:
            return
        with self._episodic_connect() as conn:
            # 构造 IN 子句占位符：WHERE id IN (?, ?, ...)
            placeholders = ",".join("?" for _ in entry_ids)
            conn.execute(
                f"UPDATE episodic_memory SET access_count = access_count + 1 "
                f"WHERE id IN ({placeholders})",
                entry_ids,
            )

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

