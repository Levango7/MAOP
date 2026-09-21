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

Note: this module was split for maintainability. Pydantic models live in
``maop.memory.memory_models`` and the consolidation / unified-protocol
methods live in ``maop.memory.manager_mixin``. Both are re-exported here
so existing ``from maop.memory.manager import ...`` imports keep working.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maop.core.backends.db_utils import sqlite_connect
from maop.memory.manager_mixin import MemoryManagerMixin
from maop.memory.memory_models import (
    ConsolidationTrigger,
    MemoryContext,
    MemoryLayer,
    MemoryManagerConfig,
)
from maop.memory.shared_db import (
    get_memory_db_path,
)
from maop.memory.store import MemoryStore

if TYPE_CHECKING:
    from maop.core.agent.llm_chat.conversation import ConversationManager

logger = logging.getLogger(__name__)

# Re-export models for backward compatibility.
# ``from maop.memory.manager import MemoryManagerConfig`` must keep working.
__all__ = [
    "ConsolidationTrigger",
    "MemoryContext",
    "MemoryLayer",
    "MemoryManager",
    "MemoryManagerConfig",
]


# ── Parallel Implementation Note ──────────────────────────────
# NOTE: MemoryManager is one of two parallel three-layer memory
# implementations. The other is ThreeLayerMemory in
# maop/core/three_layer_memory.py. Both have production callers:
#   - MemoryManager (this class): used by core/chat_engine.py (main chat)
#   - ThreeLayerMemory: used by core/agent_performance.py, core/evolution_loop.py
# Future work: consider merging into a single canonical implementation.


class MemoryManager(MemoryManagerMixin):
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
