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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from maop.core.agent.llm_chat.conversation import ConversationManager

from maop.core.backends.db_utils import sqlite_connect

# ── Re-exports from models.py (backward compatibility) ───────
# MemoryContext / MemoryManagerConfig 在本模块内部使用并 re-export；
# ConsolidationTrigger / MemoryLayer 仅为 re-export，供现有
# `from maop.memory.manager import ConsolidationTrigger` 等调用方使用。
from maop.memory.models import (  # noqa: F401
    ConsolidationTrigger,
    MemoryContext,
    MemoryLayer,
    MemoryManagerConfig,
)
from maop.memory.shared_db import (
    get_memory_db_path,
)
from maop.memory.store import MemoryStore

logger = logging.getLogger(__name__)


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
        self._working_cache: dict[str, Any] = {}
        # 漏斗增强（Funnel Enhancement）懒加载组件
        self._evidence_store: Any = None
        self._atom_facts: Any = None
        self._symbolic: Any = None
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

    # ── 漏斗增强懒加载组件 ──────────────────────────────────────

    @property
    def evidence_store(self):
        """L0 证据层：原始对话/工具结果存储 + refs 回查（懒加载）。"""
        if self._evidence_store is None:
            try:
                from maop.memory.evidence import EvidenceStore
                self._evidence_store = EvidenceStore(root_dir=self._root)
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init EvidenceStore: %s", exc)
        return self._evidence_store

    @property
    def atom_facts(self):
        """L1 原子事实层：抽取 + 语义指纹去重（懒加载）。

        方案 A：按 ``MemoryManagerConfig.llm_dedup`` 决定是否启用 LLM
        语义去重。judge 不在此传入——``AtomFactStore`` 内部在
        ``llm_dedup=True`` 时会懒加载默认判定器（models.yaml 配置），
        构造失败自动降级为纯 SHA-256 去重。
        """
        if self._atom_facts is None:
            try:
                from maop.memory.atoms import AtomFactStore
                self._atom_facts = AtomFactStore(
                    root_dir=self._root,
                    knowledge_extractor=self.knowledge_extractor,
                    llm_dedup=self._config.llm_dedup,
                )
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init AtomFactStore: %s", exc)
        return self._atom_facts

    @property
    def symbolic(self):
        """符号化短期记忆：工具结果外置 + 任务状态图（懒加载）。"""
        if self._symbolic is None:
            try:
                from maop.memory.symbolic import SymbolicMemory
                self._symbolic = SymbolicMemory(
                    root_dir=self._root,
                    evidence_store=self.evidence_store,
                )
            except Exception as exc:
                logger.warning("[memory_manager] Failed to init SymbolicMemory: %s", exc)
        return self._symbolic

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

        # L0: 原始对话证据入库（漏斗底层的"黑匣子"，可回查原文）
        user_ref = ""
        asst_ref = ""
        if self.evidence_store is not None:
            try:
                user_ref = self.evidence_store.store_evidence(
                    user_msg, session_id=session_id, kind="conversation",
                    source="user", metadata={"agent": agent, "task": task},
                )
                asst_ref = self.evidence_store.store_evidence(
                    assistant_msg, session_id=session_id, kind="conversation",
                    source="assistant", metadata={"agent": agent, "task": task},
                )
            except Exception as exc:
                logger.warning("[memory_manager] L0 evidence store failed: %s", exc)
        result["evidence_user_ref"] = user_ref or ""
        result["evidence_asst_ref"] = asst_ref or ""

        # L1: 原子事实抽取 + 指纹去重（复用 knowledge_extractor 模式匹配）
        if self.atom_facts is not None:
            try:
                ingest_report = self.atom_facts.ingest(
                    f"{user_msg}\n{assistant_msg}",
                    source_ref=user_ref or "",
                    topic=self._infer_topic(user_msg),
                )
                result["atom_new"] = str(ingest_report.get("new", 0))
                result["atom_merged"] = str(ingest_report.get("merged", 0))
            except Exception as exc:
                logger.warning("[memory_manager] L1 atom ingest failed: %s", exc)

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

        # 漏斗增强：L1 原子事实 + 符号化短期记忆（任务图/证据引用）
        atom_facts: list[dict[str, Any]] = []
        if query and self.atom_facts is not None:
            try:
                atom_facts = self.atom_facts.search_facts(
                    query=query, top=self._config.inject_max_results,
                )
            except Exception as exc:
                logger.warning("[memory_manager] atom_facts search failed: %s", exc)

        symbolic_map = ""
        evidence_refs: list[dict[str, Any]] = []
        if self.symbolic is not None:
            try:
                symbolic_map = self.symbolic.get_task_map(session_id)
                evidence_refs = self.symbolic.evidence.search_evidence(
                    session_id=session_id, top=5,
                )
            except Exception as exc:
                logger.warning("[memory_manager] symbolic injection failed: %s", exc)

        # Build injection summary
        injected = self._build_injection_summary(
            short_term, long_term,
            atom_facts=atom_facts,
            symbolic_map=symbolic_map,
        )
        injected_tokens = self._conversation._estimate_tokens(injected)

        return MemoryContext(
            working_context=working,
            short_term_results=short_term,
            long_term_results=long_term,
            atom_facts=atom_facts,
            evidence_refs=evidence_refs,
            symbolic_map=symbolic_map,
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
        result = cast(dict[str, Any] | None, report.model_dump())
        if isinstance(result, dict):
            result["atom_facts_promoted"] = self._promote_atom_facts(dry_run=dry_run)

        # 顺带清理 L0 过期证据（与 consolidation 同周期），
        # 避免 refs 文件持续膨胀。prune 失败不影响 consolidation 主流程。
        if self.evidence_store is not None:
            try:
                pruned = self.evidence_store.prune(older_than_days=90)
                if pruned:
                    logger.info(
                        "[memory_manager] L0 prune: %d 条过期证据已清理", pruned
                    )
            except Exception as exc:
                logger.warning("[memory_manager] L0 prune failed: %s", exc)

        return result

    def _promote_atom_facts(self, *, dry_run: bool = False) -> int:
        """漏斗增强：把高频原子事实晋升到 L3 长期记忆（向量索引）。

        L1 → L3 晋升链路：``atom_facts.access_count >= min_access`` 的事实
        通过 ``long_term_index`` 写入向量库，晋升后重置 access_count 防止
        重复晋升。dry_run 或组件不可用时返回 0，永不抛异常。

        签名契约：``long_term_index(doc_id, text, metadata)`` 与
        ``atoms.promote_facts`` 调用 ``vector_index_fn(row["id"], text, {...})``
        的 3 参数位置完全匹配，无需包装适配。
        """
        if dry_run or self.atom_facts is None:
            return 0
        try:
            report = self.atom_facts.promote_facts(
                min_access=self._config.long_term_min_group_size,
                vector_index_fn=self.long_term_index,
            )
            promoted = int(report.get("promoted", 0))
            if promoted:
                logger.info("[memory_manager] 晋升 %d 条原子事实到 L3", promoted)
            return promoted
        except Exception as exc:
            logger.warning("[memory_manager] atom facts promotion failed: %s", exc)
            return 0

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
        *,
        atom_facts: list[dict[str, Any]] | None = None,
        symbolic_map: str = "",
    ) -> str:
        """Build a context injection summary from L2/L3 results + 漏斗增强。

        注入顺序（漏斗由窄到宽，先高层后细节）：
          1. 符号化任务状态图（最浓缩）
          2. 长期记忆（L3）
          3. 原子事实（L1，结构化知识）
          4. 近期记忆（L2）
        """
        parts: list[str] = []

        if symbolic_map:
            parts.append("[Task Map]")
            parts.append(symbolic_map)

        if long_term:
            parts.append("[Long-term Memory]")
            for entry in long_term[:3]:
                parts.append(f"  - {entry.get('task', '')}: {entry.get('snippet', '')[:120]}")

        if atom_facts:
            parts.append("[Known Facts]")
            for fact in atom_facts[:3]:
                parts.append(
                    f"  - {fact.get('subject', '')} {fact.get('predicate', '')} "
                    f"{fact.get('object_value', '')} "
                    f"(×{fact.get('access_count', 1)})"
                )

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
        """写入 Working Memory（临时键值缓存）。"""
        self._working_cache[key] = value

    def working_get(self, key: str) -> Any:
        """读取 Working Memory。"""
        return self._working_cache.get(key)

    def working_clear(self) -> None:
        """清空 Working Memory。"""
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
                cols = [d[0] for d in conn.execute(
                    "SELECT * FROM memory_entries LIMIT 0").description]
                return dict(zip(cols, row))
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
        """索引文档到 Long-term Memory（映射到 VectorSearch.index_entry）。

        Note: VectorSearch 的索引方法是 ``index_entry(entry_id, text)``，
        早期版本误调了不存在的 ``index()`` 导致索引静默失败。
        """
        if self.vector_search is None:
            return ""
        try:
            self.vector_search.index_entry(doc_id, text)
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
            key = kwargs.pop("key", "") or f"mem-{int(time.time() * 1000)}"
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
