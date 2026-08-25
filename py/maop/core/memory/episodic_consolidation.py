"""ThreeLayerMemory — Episodic→Semantic consolidation mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
依赖宿主的 ``_episodic_connect``（EpisodicStoreMixin）与 ``_get_vector_store``
（SemanticMixin）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maop.core.memory.three_layer_memory_types import (
    ConsolidationReport,
)

logger = logging.getLogger(__name__)


class EpisodicConsolidationMixin:
    """Episodic → Semantic consolidation 方法。"""

    if TYPE_CHECKING:
        # 宿主类（ThreeLayerMemory）提供的方法 —— 仅用于类型检查
        _episodic_connect: Callable[..., Any]
        _get_vector_store: Callable[..., Any]


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

