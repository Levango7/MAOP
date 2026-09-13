"""MAOP Three-Layer Memory Manager — Unified interface for Working / Short-term / Long-term memory.

Architecture:
  Layer 1 — Working Memory (current turn)
    ConversationManager: sliding context window, in-flight messages

  Layer 2 — Short-term Memory (session-level, 7-30 day TTL)
    MemoryStore: SQLite + FTS5, per-session entries, auto-expire

  Layer 3 — Long-term Memory (permanent)
    DreamConsolidator: compressed summaries, project knowledge,
    architecture decisions, user preferences

The MemoryManager orchestrates all three layers:
  - On user message: inject relevant L2/L3 context into L1 window
  - On assistant response: store to L2, extract knowledge for L3
  - On consolidation trigger: L2 → L3 via DreamConsolidator
  - On retrieval: search L1 → L2 → L3 with cascading fallback

Usage::

    from maop.memory.manager import MemoryManager

    mgr = MemoryManager(root_dir="/path/to/MAOP")
    mgr.add_exchange(session_id="s1", user_msg="Fix auth bug", assistant_msg="Fixed in auth.py")
    context = mgr.build_context(session_id="s1", query="auth bug")
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from maop.core.agent.llm_chat.conversation import ConversationManager
from maop.core.backends.db_utils import sqlite_connect
from maop.memory.shared_db import (
    get_memory_db_path,
)
from maop.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryLayer(str):
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class MemoryContext(BaseModel):
    working_context: list[dict[str, Any]] = Field(default_factory=list)
    short_term_results: list[dict[str, Any]] = Field(default_factory=list)
    long_term_results: list[dict[str, Any]] = Field(default_factory=list)
    injected_summary: str = ""
    total_tokens_estimate: int = 0


class ConsolidationTrigger(BaseModel):
    entry_threshold: int = 100
    days_since_last: int = 7
    auto_trigger: bool = True


class MemoryManagerConfig(BaseModel):
    max_working_tokens: int = 4000
    short_term_ttl_days: int = 30
    long_term_min_group_size: int = 3
    consolidation: ConsolidationTrigger = Field(default_factory=ConsolidationTrigger)
    inject_max_results: int = 5
    inject_max_tokens: int = 800


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: MemoryManager is one of two parallel three-layer memory
# implementations. The other is ThreeLayerMemory in
# maop/core/three_layer_memory.py. Both have production callers:
#   - MemoryManager (this class): used by core/chat_engine.py (main chat)
#   - ThreeLayerMemory: used by core/agent_performance.py, core/evolution_loop.py
# Future work: consider merging into a single canonical implementation.

class MemoryManager:
    """Three-layer memory orchestrator.

    Coordinates Working (L1), Short-term (L2), and Long-term (L3)
    memory with automatic context injection and consolidation.
    """

    def __init__(
        self,
        root_dir: str | Path,
        config: MemoryManagerConfig | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._config = config or MemoryManagerConfig()
        from maop.core.agent.llm_chat.conversation import ConversationManager

        self._conversation = ConversationManager(
            root_dir=root_dir,
            max_context_tokens=self._config.max_working_tokens,
        )
        self._memory = MemoryStore(root_dir=root_dir)
        self._consolidator: Any = None
        self._last_consolidation: str = ""
        # 共享 DB 路径：与 MemoryStore / ThreeLayerMemory 共用同一个 maop.db
        # consolidation_log 表与 memory_entries / episodic_memory 表名不冲突。
        self._db_path = get_memory_db_path()
        self._knowledge_extractor: Any = None
        self._knowledge_graph: Any = None
        self._vector_search: Any = None
        # P3-3 fix: 缓存 memory_entries 表列名，避免 short_term_get 每次调用
        # 都执行 "SELECT * FROM memory_entries LIMIT 0" 查询列名。
        self._memory_entries_cols: list[str] | None = None
        # P1-10 fix: 限制 working cache 大小，LRU 淘汰防止 OOM
        self._working_cache: OrderedDict[str, Any] = OrderedDict()
        # P2-8 fix: MAOP_WORKING_CACHE_MAX_SIZE 非数字时 int() 会抛 ValueError，
        # 导致 MemoryManager 构造失败。用 try/except 包裹，回退到默认值 1000
        # 并记录 warning，保持构造成功。
        _cache_max_size_raw = os.getenv("MAOP_WORKING_CACHE_MAX_SIZE", "1000")
        try:
            self._working_cache_max_size: int = int(_cache_max_size_raw)
        except (ValueError, TypeError):
            logger.warning(
                "[memory] Invalid MAOP_WORKING_CACHE_MAX_SIZE=%r; falling back to 1000",
                _cache_max_size_raw,
            )
            self._working_cache_max_size = 1000
        # 修复: _working_cache 是 OrderedDict，非线程安全。多线程并发调用
        # working_put/working_get/delete 时可能触发 OrderedDict 内部状态损坏
        # （如 LRU move_to_end 与 popitem 竞争）。用专用锁保护所有
        # _working_cache 操作，锁粒度仅覆盖 OrderedDict 操作，不包含磁盘 I/O。
        self._working_cache_lock = threading.Lock()
        # H-8 fix: 保护 _maybe_consolidate() 的并发执行，使用非阻塞 acquire，
        # 获取失败则跳过本次 consolidation（已有 consolidation 在进行）。
        # （来源：coding-pattern/python-shared-dict-cache-concurrency-audit-fix-playbook）
        self._consolidate_lock = threading.Lock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS consolidation_log (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT DEFAULT '',
                    entries_scanned INTEGER DEFAULT 0,
                    entries_pruned INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0
                )
            """)

    @property
    def conversation(self) -> ConversationManager:
        return self._conversation

    @property
    def memory(self) -> MemoryStore:
        return self._memory

    def add_exchange(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        *,
        agent: str = "",
        task: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Store a user-assistant exchange across all memory layers.

        Returns dict with message IDs from L1 and entry ID from L2.
        """
        result: dict[str, str] = {}

        # L1: Working memory (conversation)
        from maop.core.agent.llm_chat.conversation import MessageRole

        user_id = self._conversation.add_message(
            session_id=session_id, role=MessageRole.USER,
            content=user_msg, metadata=metadata,
        )
        asst_id = self._conversation.add_message(
            session_id=session_id, role=MessageRole.ASSISTANT,
            content=assistant_msg, metadata=metadata,
        )
        result["working_user_id"] = user_id
        result["working_asst_id"] = asst_id

        # L2: Short-term memory (memory store)
        content = f"Q: {user_msg}\nA: {assistant_msg}"
        entry_id = self._memory.store(
            agent=agent or "user",
            task=task or user_msg[:100],
            content=content,
            tags=["conversation", "exchange"],
            topic=self._infer_topic(user_msg),
        )
        result["short_term_id"] = entry_id or ""

        # L3: Extract knowledge for the knowledge graph
        self.extract_knowledge(user_msg, assistant_msg, topic=self._infer_topic(user_msg))

        # Check if consolidation should be triggered
        if self._config.consolidation.auto_trigger:
            self._maybe_consolidate()

        return result

    def build_context(
        self,
        session_id: str,
        query: str = "",
        *,
        max_tokens: int | None = None,
    ) -> MemoryContext:
        """Build a full three-layer context for a session.

        1. L1: Get working memory (conversation window)
        2. L2: Search short-term memory for relevant results
        3. L3: Search long-term (consolidated) memory
        4. Merge into a single context with injection summary
        """
        budget = max_tokens or self._config.max_working_tokens

        # L1: Working memory
        window = self._conversation.get_context_window(
            session_id, max_tokens=budget,
        )
        working = [{"role": m.role, "content": m.content} for m in window.messages]

        # L2 + L3: 单次搜索后按标签分拆为短期/长期结果
        # M-5 fix: 原先对同一 query+top 调用两次 search，现合并为一次并去重分拆。
        short_term = []
        long_term = []
        if query:
            results = self._memory.search(query=query, top=self._config.inject_max_results)
            for r in results:
                entry = {
                    "id": r.id, "agent": r.agent, "task": r.task,
                    "snippet": r.snippet, "score": r.score, "topic": r.topic,
                }
                if any(t == "dream-consolidated" for t in (r.tags or [])):
                    long_term.append(entry)
                else:
                    short_term.append(entry)

        # Build injection summary
        injected = self._build_injection_summary(short_term, long_term)
        injected_tokens = self._conversation._estimate_tokens(injected)

        return MemoryContext(
            working_context=working,
            short_term_results=short_term,
            long_term_results=long_term,
            injected_summary=injected,
            total_tokens_estimate=window.total_tokens + injected_tokens,
        )

    def get_messages_for_llm(
        self,
        session_id: str,
        query: str = "",
        *,
        system_prompt: str = "",
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build the final message list for LLM API call.

        Includes system prompt, injected memory context, and conversation history.
        """
        ctx = self.build_context(session_id, query=query, max_tokens=max_tokens)
        messages: list[dict[str, Any]] = []

        # System prompt + memory injection
        system_content = system_prompt
        if ctx.injected_summary:
            system_content = f"{system_prompt}\n\n{ctx.injected_summary}" if system_prompt else ctx.injected_summary
        if system_content:
            messages.append({"role": "system", "content": system_content})

        # Conversation history
        messages.extend(ctx.working_context)

        return messages

    def search_all_layers(self, query: str, top: int = 10) -> dict[str, list[dict[str, Any]]]:
        """Search across all memory layers."""
        results = self._memory.search(query=query, top=top * 2)

        short_term = []
        long_term = []
        for r in results:
            entry = {
                "id": r.id, "agent": r.agent, "task": r.task,
                "snippet": r.snippet, "score": r.score, "topic": r.topic,
                "timestamp": r.timestamp,
            }
            if any(t == "dream-consolidated" for t in (r.tags or [])):
                long_term.append(entry)
            else:
                short_term.append(entry)

        return {
            "short_term": short_term[:top],
            "long_term": long_term[:top],
        }

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
        import uuid
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

    @staticmethod
    def _infer_topic(text: str) -> str:
        """Infer a topic from message text using keyword matching."""
        text_lower = text.lower()
        topic_keywords = {
            "bug": "debugging",
            "fix": "debugging",
            "error": "debugging",
            "test": "testing",
            "deploy": "deployment",
            "refactor": "refactoring",
            "implement": "development",
            "write": "development",
            "design": "architecture",
            "review": "code-review",
            "config": "configuration",
            "auth": "authentication",
            "security": "security",
        }
        for kw, topic in topic_keywords.items():
            if kw in text_lower:
                return topic
        return "general"

    @staticmethod
    def _build_injection_summary(
        short_term: list[dict[str, Any]],
        long_term: list[dict[str, Any]],
    ) -> str:
        """Build a context injection summary from L2/L3 results."""
        parts: list[str] = []

        if long_term:
            parts.append("[Long-term Memory]")
            for entry in long_term[:3]:
                parts.append(f"  - {entry.get('task', '')}: {entry.get('snippet', '')[:120]}")

        if short_term:
            parts.append("[Recent Memory]")
            for entry in short_term[:3]:
                parts.append(f"  - {entry.get('task', '')}: {entry.get('snippet', '')[:120]}")

        return "\n".join(parts) if parts else ""

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
