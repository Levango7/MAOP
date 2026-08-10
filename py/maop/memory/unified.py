"""Unified Memory Protocol — 公共接口契约，统一两套三层记忆实现。

背景
----
MAOP 同时存在两套三层记忆实现：

1. ``ThreeLayerMemory`` (``maop.core.memory.three_layer_memory``)
   - L1: LRUCache (working_put/get/delete/clear/pin/unpin)
   - L2: episodic_store/search/get/stats/update_feedback
   - L3: semantic_index/search
   - 调用方: ``agent_performance``、``evolution_loop``、``dashboard/routers/memory``

2. ``MemoryManager`` (``maop.memory.manager``)
   - L1: ConversationManager (add_exchange/build_context/get_messages_for_llm)
   - L2: MemoryStore (query_episodic / search)
   - L3: DreamConsolidator + KnowledgeGraph + VectorSearch
   - 调用方: ``chat_engine``

两者通过 ``shared_db.py`` 共享同一个 ``maop.db``，并通过
``LAYER_ALIASES`` 做术语映射（episodic↔short_term、semantic↔long_term）。

本模块的作用
------------
定义 ``UnifiedMemoryProtocol`` —— 一个 ``typing.Protocol``，声明两套实现
**公共方法子集** 的统一签名。所有方法使用 MemoryManager 的标准术语
(working / short_term / long_term)，让上层代码可以面向 Protocol 编程，
由 ``MemoryFacade`` 按 mode 路由到具体实现。

设计原则
--------
- **不强行合并**：两套实现的 L1 语义本质不同（LRU vs ConversationManager），
  保留双实现，仅统一对外接口。
- **向后兼容**：原 ``ThreeLayerMemory`` / ``MemoryManager`` 类名与构造签名
  保持不变，旧调用方继续直接使用原类。
- **新增推荐入口**：``MemoryFacade`` 是新代码的推荐入口，旧代码无需迁移。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ── 统一术语常量 ────────────────────────────────────────────────
# 公开 API 使用 working / short_term / long_term
# 内部由各实现映射到自己的术语（episodic / semantic）
LAYER_WORKING = "working"
LAYER_SHORT_TERM = "short_term"
LAYER_LONG_TERM = "long_term"

VALID_LAYERS: frozenset[str] = frozenset({
    LAYER_WORKING, LAYER_SHORT_TERM, LAYER_LONG_TERM,
})


@runtime_checkable
class UnifiedMemoryProtocol(Protocol):
    """统一三层记忆接口契约。

    所有方法使用标准术语 (working / short_term / long_term)。
    ``ThreeLayerMemory`` 与 ``MemoryManager`` 均应实现本 Protocol
    （通过添加别名方法实现，详见 ``facade.py`` 文档）。

    实现方需保证：
    - L1 (working): 进程内快速存取，session-scoped
    - L2 (short_term): 持久化存储，FTS5 检索，TTL 过期
    - L3 (long_term): 向量索引 / 知识图谱，永久存储
    - consolidate: L2 → L3 提炼
    - build_context: 跨层组装上下文（用于 LLM 调用）

    F1-03 增强：新增 ``store`` / ``retrieve`` / ``search`` / ``delete``
    四个统一入口方法，按 ``layer`` 参数自动路由到对应层。``MemoryFacade``
    直接实现这四个方法并转发到底层，底层实现可选实现（缺失时由 Facade 兜底）。
    """

    # ── Layer 1: Working Memory ───────────────────────────────

    def working_put(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """写入 Working Memory（进程内 LRU / 会话窗口）。"""
        ...

    def working_get(self, key: str) -> Any:
        """读取 Working Memory。"""
        ...

    def working_clear(self) -> None:
        """清空 Working Memory。"""
        ...

    # ── Layer 2: Short-term Memory ────────────────────────────

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
        """写入 Short-term Memory，返回条目 ID。

        - ``ThreeLayerMemory`` 内部映射到 ``episodic_store``
          （content → summary, task/agent 透传, topic/tags 合并到 metadata）
        - ``MemoryManager`` 内部映射到 ``MemoryStore.store``
          （content 直接存储, task/agent/topic/tags 透传）
        """
        ...

    def short_term_search(
        self,
        query: str = "",
        *,
        top: int = 10,
        agent: str = "",
    ) -> list[dict[str, Any]]:
        """检索 Short-term Memory，返回 dict 列表（统一结果格式）。

        - ``ThreeLayerMemory`` 内部调用 ``episodic_search`` 并将
          ``EpisodicSearchResult`` 序列化为 dict
        - ``MemoryManager`` 内部调用 ``MemoryStore.search`` 并将
          ``SearchResult`` 序列化为 dict
        """
        ...

    def short_term_get(self, entry_id: str) -> dict[str, Any] | None:
        """按 ID 获取单条 Short-term Memory 条目，返回 dict 或 None。"""
        ...

    def short_term_stats(self) -> dict[str, Any]:
        """Short-term Memory 统计信息（条目数、按 agent/outcome/topic 分组等）。"""
        ...

    # ── Layer 3: Long-term Memory ─────────────────────────────

    def long_term_index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """索引文档到 Long-term Memory（向量检索 / 知识图谱）。"""
        ...

    def long_term_search(
        self,
        query: str,
        *,
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """检索 Long-term Memory，返回 dict 列表。"""
        ...

    # ── 跨层操作 ───────────────────────────────────────────────

    def consolidate(self, **kwargs: Any) -> dict[str, Any] | None:
        """触发 L2 → L3 consolidation，返回报告 dict 或 None。

        实现方可接受额外参数（如 ``min_score``、``limit``、``dry_run``），
        但必须支持无参调用使用默认配置。
        """
        ...

    def build_context(
        self,
        session_id: str = "",
        query: str = "",
        *,
        max_tokens: int | None = None,
    ) -> Any:
        """组装三层上下文，返回实现特定的上下文对象。

        - ``MemoryManager`` 返回原生 ``MemoryContext`` pydantic model
          （向后兼容现有调用方如 ``chat_engine`` / ``test_chat_memory``）
        - ``ThreeLayerMemory`` 返回 ``dict``（含 working / short_term /
          long_term 字段）

        调用方应根据 ``mode`` 或返回值类型自行处理。``MemoryFacade``
        直接透传底层返回值。
        """
        ...

    def stats(self) -> dict[str, Any]:
        """跨层统计信息汇总。"""
        ...

    # ── F1-03 统一 CRUD 入口（可选实现） ──────────────────────
    # 以下四个方法为 F1-03 新增的统一入口。底层实现可选实现；
    # MemoryFacade 会兜底转发。Protocol 中声明以便 runtime_checkable
    # 能识别真正实现统一 CRUD 的对象。

    def store(self, layer: str, content: Any, **kwargs: Any) -> str:
        """统一存储入口，按 ``layer`` 路由到 working/short_term/long_term。

        Parameters
        ----------
        layer : str
            ``"working"`` / ``"short_term"`` / ``"long_term"``（或别名
            ``"episodic"`` / ``"semantic"``）。
        content : Any
            待存储内容。``working`` 层接受任意可序列化对象；
            ``short_term`` / ``long_term`` 层接受 str。
        **kwargs : Any
            透传给对应层方法的额外参数（如 ``key``、``task``、``agent``、
            ``topic``、``tags``、``metadata``、``doc_id``、``ttl_s``）。

        Returns
        -------
        str
            写入条目的 ID（working 层返回 ``key``，short_term/long_term
            层返回底层生成的 ID）。
        """
        ...

    def retrieve(self, layer: str, query: str = "", top: int = 10, **kwargs: Any) -> Any:
        """统一检索入口，按 ``layer`` 路由到对应层。

        Returns
        -------
        Any
            ``working`` 层返回单值或 None；``short_term`` / ``long_term``
            层返回 ``list[dict]``。
        """
        ...

    def search(self, query: str, *, top: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """跨层搜索：同时检索 short_term + long_term，合并去重后返回。

        Returns
        -------
        list[dict[str, Any]]
            合并后的搜索结果，每条带 ``layer`` 字段标识来源。
        """
        ...

    def delete(self, layer: str, entry_id: str) -> bool:
        """按 ID 删除指定层的条目。

        Returns
        -------
        bool
            True 表示删除成功，False 表示条目不存在或删除失败。
            ``working`` 层删除内存中 key；``short_term`` 层删除
            SQLite 行；``long_term`` 层删除向量索引条目。
        """
        ...


__all__ = [
    "UnifiedMemoryProtocol",
    "LAYER_WORKING",
    "LAYER_SHORT_TERM",
    "LAYER_LONG_TERM",
    "VALID_LAYERS",
]