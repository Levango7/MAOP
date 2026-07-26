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

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.conversation import ConversationManager, MessageRole
from maop.core.db_utils import sqlite_connect
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

        # L2: Short-term memory search
        short_term = []
        if query:
            results = self._memory.search(query=query, top=self._config.inject_max_results)
            short_term = [
                {"id": r.id, "agent": r.agent, "task": r.task,
                 "snippet": r.snippet, "score": r.score, "topic": r.topic}
                for r in results
                if not any(t == "dream-consolidated" for t in (r.tags or []))
            ]

        # L3: Long-term memory search (consolidated entries)
        long_term = []
        if query:
            results = self._memory.search(query=query, top=self._config.inject_max_results)
            long_term = [
                {"id": r.id, "agent": r.agent, "task": r.task,
                 "snippet": r.snippet, "score": r.score, "topic": r.topic}
                for r in results
                if any(t == "dream-consolidated" for t in (r.tags or []))
            ]

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

        report = self._consolidator.dream(dry_run=dry_run)

        with sqlite_connect(self._db_path) as conn:
            import uuid
            conn.execute(
                """INSERT INTO consolidation_log (id, started_at, finished_at, entries_scanned, entries_pruned, success)
                   VALUES (?,?,?,?,?,?)""",
                (f"cl-{uuid.uuid4().hex[:8]}", report.started_at, report.finished_at,
                 report.total_entries_scanned, report.entries_pruned,
                 1 if report.success else 0),
            )

        self._last_consolidation = report.finished_at
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
        """Check if consolidation should be triggered and run it."""
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
                from maop.core.knowledge_extractor import KnowledgeExtractor
                self._knowledge_extractor = KnowledgeExtractor(root_dir=self._root)
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init KnowledgeExtractor: %s", exc)
        return self._knowledge_extractor

    @property
    def knowledge_graph(self):
        if self._knowledge_graph is None:
            try:
                from maop.core.knowledge_graph import KnowledgeGraph
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
                    try:
                        row[field] = _json.loads(row[field])
                    except (ValueError, TypeError):
                        pass
        return rows
