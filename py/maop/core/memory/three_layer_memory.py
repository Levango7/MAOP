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

import logging
from pathlib import Path
from typing import Any

from maop.core.memory.episodic_consolidation import EpisodicConsolidationMixin
from maop.core.memory.episodic_store import EpisodicStoreMixin
from maop.core.memory.protocol_aliases import ProtocolMixin
from maop.core.memory.semantic import SemanticMixin

# T2 拆分后 types/utils 常量移至子模块；主文件保留 re-export 兼容
# `from maop.core.memory.three_layer_memory import X` 的既有引用。
from maop.core.memory.three_layer_memory_types import (  # noqa: F401  # re-export 兼容
    ContextHead,
    FocusMode,
    QualityDimensions,
    decay_weight,
)
from maop.core.memory.three_layer_memory_utils import (  # noqa: F401  # re-export 兼容
    _compress_text,
    _is_negative_feedback,
    _text_relevance,
)
from maop.core.memory.transform import TransformMixin
from maop.core.memory.working_memory import WorkingMemoryMixin
from maop.core.reliability.cache import LRUCache

# 共享 DB 路径与术语映射（统一 ThreeLayerMemory 与 MemoryManager 的 DB 文件）
from maop.memory.shared_db import (
    get_memory_db_path,
    migrate_legacy_episodic_db,
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

class ThreeLayerMemory(WorkingMemoryMixin, SemanticMixin, TransformMixin, EpisodicStoreMixin, EpisodicConsolidationMixin, ProtocolMixin):
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






    # ── Consolidation (Episodic → Semantic) ──────────────────


    # ── UnifiedMemoryProtocol 别名（统一术语: short_term / long_term） ──
    # 让 ThreeLayerMemory 实现 maop.memory.unified.UnifiedMemoryProtocol。
    # 公开 API 使用 MemoryManager 的标准术语，内部映射到 episodic / semantic。
    # 详见 maop/memory/unified.py 与 maop/memory/facade.py。









    # ── F1-03 统一 CRUD 入口（store/retrieve 已存在，补 search/delete） ──




# ── Transform Helpers ─────────────────────────────────────────

