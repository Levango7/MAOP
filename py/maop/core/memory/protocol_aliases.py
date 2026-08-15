"""ThreeLayerMemory — UnifiedMemoryProtocol 别名 + F1-03 统一 CRUD mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
统一术语映射（short_term↔episodic、long_term↔semantic）经宿主 LAYER_NAME_MAP。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, cast

from maop.core.backends.db_utils import sqlite_connect
from maop.memory.shared_db import get_memory_db_path, migrate_legacy_episodic_db, normalize_layer_name

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

logger = logging.getLogger(__name__)


class ProtocolMixin:
    """统一 API / UnifiedMemoryProtocol 别名 / F1-03 CRUD 方法。"""


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

