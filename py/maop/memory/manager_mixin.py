"""MAOP Three-Layer Memory — MemoryManager mixin.

Extracted from ``maop.memory.manager`` to keep each module under 500 lines.
Groups the *consolidation*, *maintenance*, *knowledge-graph*,
*episodic cross-query*, and *UnifiedMemoryProtocol adapter* methods of
:class:`MemoryManager`. Core orchestration (``__init__``, ``add_exchange``,
``build_context``, ``get_messages_for_llm``, ``search_all_layers``) stays
in ``manager.py``. Relies on attributes set by ``MemoryManager.__init__``
(``_config``, ``_memory``, ``_db_path``, ``_consolidator``, ``_last_consolidation``,
``_root``, ``_knowledge_extractor``, ``_knowledge_graph``, ``_vector_search``,
``_working_cache``, ``_working_cache_lock``, ``_working_cache_max_size``,
``_memory_entries_cols``, ``_consolidate_lock``).
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)


class MemoryManagerMixin:
    """Consolidation / maintenance / unified-protocol methods (impl detail)."""

    # ── Consolidation ───────────────────────────────────────

    def consolidate(self, dry_run: bool = False) -> dict[str, Any] | None:
        """Trigger L2 → L3 consolidation via DreamConsolidator."""
        if self._consolidator is None:
            try:
                from maop.memory.consolidator import DreamConsolidator
                self._consolidator = DreamConsolidator(
                    memory_store=self._memory,
                    min_group_size=self._config.long_term_min_group_size,
                )
            except Exception as exc:
                logger.error("[memory_manager] Failed to init DreamConsolidator: %s", exc)
                return None

        # P1-9 fix: 将 dream() 和 consolidation_log 写入放在同一事务中，
        # 确保 dream() 失败时日志也记录失败状态（success=0），且日志写入
        # 失败不会产生无日志的 dream 结果。原代码 dream() 和日志写入是
        # 两个独立连接/事务，日志写入失败会丢失记录。
        log_id = f"cl-{uuid.uuid4().hex[:8]}"
        try:
            report = self._consolidator.dream(dry_run=dry_run)
            success = report.success
            started_at = report.started_at
            finished_at = report.finished_at
            entries_scanned = report.total_entries_scanned
            entries_pruned = report.entries_pruned
        except Exception as exc:
            logger.error("[memory_manager] dream() failed: %s", exc, exc_info=True)
            success = False
            started_at = ""
            finished_at = ""
            entries_scanned = 0
            entries_pruned = 0

        # 日志写入用独立事务，但保证 dream() 失败时也写入 success=0 记录。
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO consolidation_log (id, started_at, finished_at, entries_scanned, entries_pruned, success)
                   VALUES (?,?,?,?,?,?)""",
                (log_id, started_at, finished_at,
                 entries_scanned, entries_pruned,
                 1 if success else 0),
            )

        if not success:
            return None

        self._last_consolidation = finished_at
        return cast(dict[str, Any] | None, report.model_dump())

    def prune_expired(self) -> int:
        """Prune expired short-term memory entries."""
        pruned = self._memory.prune(ttl_days=self._config.short_term_ttl_days)
        return len(pruned)

    def stats(self) -> dict[str, Any]:
        """Get statistics across all memory layers."""
        mem_stats = self._memory.stats()
        return {
            "short_term_entries": mem_stats.total_entries,
            "short_term_traces": mem_stats.total_traces,
            "by_agent": mem_stats.by_agent,
            "by_topic": mem_stats.by_topic,
            "last_consolidation": self._last_consolidation,
        }

    def _maybe_consolidate(self) -> None:
        """Check if consolidation should be triggered and run it.

        H-8 fix: 使用非阻塞锁保护并发执行。如果已有 consolidation 在进行，
        acquire(blocking=False) 返回 False，直接跳过本次触发，避免
        多线程并发触发重复 consolidation 导致资源浪费和潜在竞态。
        （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
        """
        if not self._consolidate_lock.acquire(blocking=False):
            return  # 已有 consolidation 在进行，跳过本次
        try:
            cfg = self._config.consolidation
            stats = self._memory.stats()
            if stats.total_entries < cfg.entry_threshold:
                return

            if self._last_consolidation:
                try:
                    last = datetime.fromisoformat(self._last_consolidation)
                    now = datetime.now(timezone.utc)
                    if (now - last).days < cfg.days_since_last:
                        return
                except (ValueError, TypeError):
                    pass

            try:
                self.consolidate()
            except Exception as exc:
                logger.warning("[memory_manager] Auto-consolidation failed: %s", exc)
        finally:
            self._consolidate_lock.release()

    # ── Knowledge graph / vector search ─────────────────────

    @property
    def knowledge_extractor(self):
        if self._knowledge_extractor is None:
            try:
                from maop.core.memory.knowledge_extractor import KnowledgeExtractor
                self._knowledge_extractor = KnowledgeExtractor(root_dir=self._root)
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init KnowledgeExtractor: %s", exc)
        return self._knowledge_extractor

    @property
    def knowledge_graph(self):
        if self._knowledge_graph is None:
            try:
                from maop.core.memory.knowledge_graph import KnowledgeGraph
                self._knowledge_graph = KnowledgeGraph(root_dir=self._root)
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init KnowledgeGraph: %s", exc)
        return self._knowledge_graph

    @property
    def vector_search(self):
        if self._vector_search is None:
            try:
                from maop.memory.vector_search import VectorSearch
                self._vector_search = VectorSearch(root_dir=self._root)
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init VectorSearch: %s", exc)
        return self._vector_search

    def extract_knowledge(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        topic: str = "",
    ) -> dict[str, int] | None:
        """Extract knowledge from an exchange and store to the knowledge graph."""
        if self.knowledge_extractor is None:
            return None
        try:
            result = self.knowledge_extractor.extract_from_exchange(
                user_msg, assistant_msg, topic=topic,
            )
            return cast(dict[str, int] | None, self.knowledge_extractor.store_extraction(result))
        except Exception as exc:
            logger.warning("[memory_manager] Knowledge extraction failed: %s", exc)
            return None

    def query_knowledge(
        self,
        entity: str = "",
        topic: str = "",
        max_depth: int = 2,
    ) -> str:
        """Query the knowledge graph for context about an entity."""
        if self.knowledge_graph is None:
            return ""
        try:
            return cast(str, self.knowledge_graph.build_context(
                entity, max_depth=max_depth,
            ))
        except Exception as exc:
            logger.warning("[memory_manager] Knowledge query failed: %s", exc)
            return ""

    def semantic_search(self, query: str, top: int = 10) -> list[dict[str, Any]]:
        """Perform semantic vector search."""
        if self.vector_search is None:
            return []
        try:
            results = self.vector_search.search(query, top=top)
            return [r.model_dump() for r in results]
        except Exception as exc:
            logger.warning("[memory_manager] Semantic search failed: %s", exc)
            return []

    # ── ThreeLayerMemory 兼容查询 ────────────────────────

    def query_episodic(self, query: str = "", top: int = 10) -> list[dict[str, Any]]:
        """查询 ThreeLayerMemory 写入的 episodic_memory 表（跨实现通信）。

        ``ThreeLayerMemory`` 被 agent_performance / evolution_loop 调用，
        将任务经验（含用户反馈、质量评分、lessons）写入同一 DB 的
        ``episodic_memory`` 表。本方法让 ``MemoryManager`` 能够读取这些
        条目，从而让 chat 上下文能看到 agent_performance 的反馈数据。

        Returns
        -------
        list[dict[str, Any]]
            每行包含 id/task/agent/outcome/score/summary/user_feedback/
            quality_dimensions/lessons 等字段（JSON 字段已反序列化为 dict/list）。
        """
        import json as _json
        sql = "SELECT * FROM episodic_memory"
        params: list[Any] = []
        if query:
            sql += " WHERE task LIKE ? OR summary LIKE ? OR user_feedback LIKE ?"
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(top)
        try:
            with sqlite_connect(self._db_path) as conn:
                cursor = conn.execute(sql, params)
                cols = [d[0] for d in cursor.description] if cursor.description else []
                rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            logger.warning("[memory_manager] query_episodic failed: %s", exc)
            return []

        # 反序列化 JSON 字段，方便上层使用
        json_fields = ("lessons", "quality_dimensions", "key_decisions",
                       "files_touched", "metadata")
        for row in rows:
            for field in json_fields:
                if field in row and isinstance(row[field], str):
                    with contextlib.suppress(ValueError, TypeError):
                        row[field] = _json.loads(row[field])
        return rows

    # ── UnifiedMemoryProtocol adapter methods ─────────────
    # 以下方法将统一术语 API (working/short_term/long_term) 映射到
    # MemoryManager 的现有方法，使 MemoryManager 实现 UnifiedMemoryProtocol。
    # 详见 maop/memory/unified.py 与 maop/memory/facade.py。

    def working_put(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """写入 Working Memory（临时键值缓存）。

        P1-10 fix: 超过 ``_working_cache_max_size`` 时按 LRU 淘汰最旧条目，
        防止无限制增长导致 OOM。重复写入同一 key 时 OrderedDict 赋值会
        原地更新值（不改变顺序），如需提升为最近使用请先 get 再 put。
        """
        # 修复: 加锁保护 _working_cache 的并发读写，防止多线程下
        # OrderedDict 内部状态损坏（赋值与 LRU 淘汰竞争）。
        with self._working_cache_lock:
            self._working_cache[key] = value
            # P1-10 fix: LRU 淘汰 —— 超过上限时移除最旧（最久未访问）条目
            if len(self._working_cache) > self._working_cache_max_size:
                self._working_cache.popitem(last=False)

    def working_get(self, key: str) -> Any:
        """读取 Working Memory。

        P1-10 fix: 命中时移到末尾（LRU 顺序更新），使最近访问的条目
        不易被淘汰；未命中返回 None。
        """
        # 修复: 加锁保护 _working_cache 的并发读，防止与 working_put 的
        # 写入竞争导致 move_to_end 迭代器失效。
        with self._working_cache_lock:
            if key in self._working_cache:
                self._working_cache.move_to_end(key)
                return self._working_cache[key]
        return None

    def working_clear(self) -> None:
        """清空 Working Memory。"""
        # 修复: 加锁保护 _working_cache 的并发清空操作。
        with self._working_cache_lock:
            self._working_cache.clear()

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
        """写入 Short-term Memory（映射到 MemoryStore.store）。"""
        entry_id = self._memory.store(
            agent=agent or "user",
            task=task or content[:80],
            content=content,
            tags=tags or [],
            topic=topic or "general",
        )
        return entry_id or ""

    def short_term_search(
        self,
        query: str = "",
        *,
        top: int = 10,
        agent: str = "",
    ) -> list[dict[str, Any]]:
        """检索 Short-term Memory（映射到 MemoryStore.search）。"""
        try:
            results = self._memory.search(query=query, agent=agent, top=top)
            return [
                r.model_dump() if hasattr(r, "model_dump") else dict(r)
                for r in results
            ]
        except Exception as exc:
            logger.warning("[memory_manager] short_term_search failed: %s", exc)
            return []

    def short_term_get(self, entry_id: str) -> dict[str, Any] | None:
        """按 ID 获取单条 Short-term Memory 条目。"""
        try:
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
                ).fetchone()
                if row is None:
                    return None
                # P3-3 fix: 缓存列名到实例变量，避免每次调用都执行
                # "SELECT * FROM memory_entries LIMIT 0" 查询列名。
                if self._memory_entries_cols is None:
                    self._memory_entries_cols = [d[0] for d in conn.execute(
                        "SELECT * FROM memory_entries LIMIT 0").description]
                return dict(zip(self._memory_entries_cols, row))
        except Exception as exc:
            logger.warning("[memory_manager] short_term_get failed: %s", exc)
            return None

    def short_term_stats(self) -> dict[str, Any]:
        """Short-term Memory 统计信息。"""
        try:
            result = self._memory.stats()
            if hasattr(result, "model_dump"):
                return result.model_dump()  # type: ignore
            if isinstance(result, dict):
                return result
            return dict(result)
        except Exception as exc:
            logger.warning("[memory_manager] short_term_stats failed: %s", exc)
            return {}

    def long_term_index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """索引文档到 Long-term Memory（映射到 VectorSearch.index）。"""
        if self.vector_search is None:
            return ""
        try:
            self.vector_search.index(doc_id, text)
            return doc_id
        except Exception as exc:
            logger.warning("[memory_manager] long_term_index failed: %s", exc)
            return ""

    def long_term_search(
        self,
        query: str,
        *,
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """检索 Long-term Memory（映射到 semantic_search）。"""
        return self.semantic_search(query, top=top)

    # ── F1-03 统一 CRUD 入口 ──────────────────────────────────
    # 实现 maop.memory.unified.UnifiedMemoryProtocol 的
    # store / retrieve / search / delete 四个统一方法。

    def store(self, layer: str, content: Any, **kwargs: Any) -> str:
        """统一存储入口，按 ``layer`` 路由到 working/short_term/long_term。"""
        from maop.memory.shared_db import normalize_layer_name

        normalized = normalize_layer_name(layer)
        if normalized == "working":
            key = kwargs.pop("key", "") or f"mem-{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}"
            self.working_put(key, content, ttl_s=kwargs.pop("ttl_s", None))
            return key
        if normalized == "short_term":
            return self.short_term_store(
                str(content),
                task=kwargs.pop("task", ""),
                agent=kwargs.pop("agent", ""),
                topic=kwargs.pop("topic", ""),
                tags=kwargs.pop("tags", None),
                metadata=kwargs.pop("metadata", None),
            )
        if normalized == "long_term":
            doc_id = kwargs.pop("doc_id", f"doc-{int(time.time() * 1000)}")
            return self.long_term_index(
                doc_id, str(content), metadata=kwargs.pop("metadata", None)
            )
        raise ValueError(f"Unknown layer: {layer!r}")

    def retrieve(self, layer: str, query: str = "", top: int = 10, **kwargs: Any) -> Any:
        """统一检索入口，按 ``layer`` 路由到对应层。"""
        from maop.memory.shared_db import normalize_layer_name

        normalized = normalize_layer_name(layer)
        if normalized == "working":
            return self.working_get(query) if query else None
        if normalized == "short_term":
            return self.short_term_search(query=query, top=top, agent=kwargs.get("agent", ""))
        if normalized == "long_term":
            return self.long_term_search(query, top=top)
        raise ValueError(f"Unknown layer: {layer!r}")

    def search(self, query: str, *, top: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """跨层搜索：合并 short_term + long_term，附带 ``layer`` 字段。"""
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
            logger.debug("[memory_manager] search short_term failed: %s", exc)

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
            logger.debug("[memory_manager] search long_term failed: %s", exc)

        return merged[:top] if top > 0 else merged

    def delete(self, layer: str, entry_id: str) -> bool:
        """按 ID 删除指定层的条目。"""
        from maop.memory.shared_db import normalize_layer_name

        normalized = normalize_layer_name(layer)
        if normalized == "working":
            # 修复: 加锁保护 _working_cache 的并发删除，防止与
            # working_put/working_get 的竞争导致状态不一致。
            with self._working_cache_lock:
                if entry_id in self._working_cache:
                    self._working_cache.pop(entry_id, None)
                    return True
            return False
        if normalized == "short_term":
            try:
                with sqlite_connect(self._db_path, foreign_keys=False) as conn:
                    cur = conn.execute(
                        "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
                    )
                    return cur.rowcount > 0
            except Exception as exc:
                logger.warning("[memory_manager] delete short_term failed: %s", exc)
                return False
        if normalized == "long_term":
            vs = self._vector_search
            if vs is None:
                vs = self.vector_search  # 触发 property 懒加载
            delete_fn = getattr(vs, "delete", None)
            if callable(delete_fn):
                try:
                    delete_fn(entry_id)
                    return True
                except Exception as exc:
                    logger.debug("[memory_manager] vector delete failed: %s", exc)
                    return False
            return False
        raise ValueError(f"Unknown layer: {layer!r}")