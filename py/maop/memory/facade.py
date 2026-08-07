"""Memory Facade — 统一三层记忆入口，按 mode 路由到对应实现。

设计动机
--------
MAOP 同时存在两套三层记忆实现（详见 ``unified.py`` 文档）。强行合并风险
极高（L1 层语义不同：LRU vs ConversationManager），改用 Facade 模式统一
对外入口，保留双实现：

- ``mode="chat"``  → ``MemoryManager``（对话上下文，被 ``chat_engine`` 使用）
- ``mode="agent"`` → ``ThreeLayerMemory``（任务经验，被 ``agent_performance`` /
  ``evolution_loop`` 使用）

公开 API 使用统一术语 (working / short_term / long_term)，内部由各实现
映射到自己的术语（episodic / semantic）。

向后兼容
--------
- 原 ``ThreeLayerMemory`` / ``MemoryManager`` 类名与构造签名保持不变
- 旧调用方继续直接使用原类，无需修改
- ``MemoryFacade`` 仅作为新代码的推荐入口

使用示例
--------

::

    from maop.memory.facade import MemoryFacade

    # 对话场景
    chat_mem = MemoryFacade(root_dir="/path/to/MAOP", mode="chat")
    chat_mem.short_term_store("Fix auth bug", task="auth", agent="user")

    # Agent 场景
    agent_mem = MemoryFacade(root_dir="/path/to/MAOP", mode="agent")
    agent_mem.short_term_store(
        "Always set socket timeout",
        task="Fix login timeout",
        agent="claude",
    )

    # 两者共享同一个 maop.db，可互相读取对方写入的数据
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal


logger = logging.getLogger(__name__)

MemoryMode = Literal["chat", "agent"]


class MemoryFacade:
    """统一三层记忆入口（Facade）。

    按 ``mode`` 路由到 ``MemoryManager`` (chat) 或 ``ThreeLayerMemory``
    (agent)，对外暴露 ``UnifiedMemoryProtocol`` 定义的统一 API。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录。
    mode : {"chat", "agent"}
        - ``"chat"``: 使用 ``MemoryManager``，适合对话上下文管理
        - ``"agent"``: 使用 ``ThreeLayerMemory``，适合任务经验管理
    config : Any, optional
        透传给底层实现的自定义配置（``MemoryManagerConfig`` 或 None）。
        ``mode="agent"`` 时忽略此参数。
    **kwargs : Any
        透传给底层实现的额外构造参数（如 ``working_max``、``working_ttl``）。

    Notes
    -----
    - 两个 mode 共享同一个 ``maop.db``，可互相读取对方写入的数据
      （通过 ``query_episodic`` / ``query_memory_entries`` 跨实现查询方法）
    - 本类不缓存底层实例的内部状态，所有方法直接转发
    """

    def __init__(
        self,
        root_dir: str | Path,
        mode: MemoryMode = "agent",
        *,
        config: Any = None,
        **kwargs: Any,
    ) -> None:
        if mode not in ("chat", "agent"):
            raise ValueError(
                f"Invalid mode: {mode!r}. Expected 'chat' or 'agent'."
            )
        self._root = Path(root_dir)
        self._mode: MemoryMode = mode
        self._kwargs = dict(kwargs)
        self._config = config
        # 底层实现类型放宽为 Any：两套实现的 consolidate 等方法签名不同
        # （MemoryManager.consolidate(dry_run) / ThreeLayerMemory.consolidate(min_score, limit)），
        # 无法用单一 Protocol 签名精确表达。运行时由 Facade 统一转发。
        self._impl: Any = self._build_impl(mode, config, kwargs)

    # ── 内部构造 ───────────────────────────────────────────────

    def _build_impl(
        self,
        mode: MemoryMode,
        config: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        """按 mode 实例化底层实现。"""
        if mode == "chat":
            from maop.memory.manager import MemoryManager

            if config is not None:
                return MemoryManager(root_dir=self._root, config=config)
            return MemoryManager(root_dir=self._root)

        # mode == "agent"
        from maop.core.memory.three_layer_memory import ThreeLayerMemory

        working_max = kwargs.get("working_max", 200)
        working_ttl = kwargs.get("working_ttl", 3600.0)
        return ThreeLayerMemory(
            root_dir=self._root,
            working_max=working_max,
            working_ttl=working_ttl,
        )

    # ── 元信息 ─────────────────────────────────────────────────

    @property
    def mode(self) -> MemoryMode:
        """当前路由模式 ('chat' 或 'agent')。"""
        return self._mode

    @property
    def impl(self) -> Any:
        """底层实现实例（``MemoryManager`` 或 ``ThreeLayerMemory``）。

        暴露底层实例以支持高级用法（如调用 mode 独有的方法）。
        """
        return self._impl

    def is_chat(self) -> bool:
        return self._mode == "chat"

    def is_agent(self) -> bool:
        return self._mode == "agent"

    # ── Layer 1: Working Memory ───────────────────────────────

    def working_put(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """写入 Working Memory。"""
        self._impl.working_put(key, value, ttl_s=ttl_s)

    def working_get(self, key: str) -> Any:
        """读取 Working Memory。"""
        return self._impl.working_get(key)

    def working_clear(self) -> None:
        """清空 Working Memory。"""
        self._impl.working_clear()

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
        """写入 Short-term Memory，返回条目 ID。"""
        return self._impl.short_term_store(  # type: ignore[no-any-return]
            content,
            task=task,
            agent=agent,
            topic=topic,
            tags=tags,
            metadata=metadata,
        )

    def short_term_search(
        self,
        query: str = "",
        *,
        top: int = 10,
        agent: str = "",
    ) -> list[dict[str, Any]]:
        """检索 Short-term Memory，返回 dict 列表。"""
        return self._impl.short_term_search(query, top=top, agent=agent)  # type: ignore[no-any-return]

    def short_term_get(self, entry_id: str) -> dict[str, Any] | None:
        """按 ID 获取单条 Short-term Memory 条目。"""
        return self._impl.short_term_get(entry_id)  # type: ignore[no-any-return]

    def short_term_stats(self) -> dict[str, Any]:
        """Short-term Memory 统计信息。"""
        return self._impl.short_term_stats()  # type: ignore[no-any-return]

    # ── Layer 3: Long-term Memory ─────────────────────────────

    def long_term_index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """索引文档到 Long-term Memory。"""
        return self._impl.long_term_index(doc_id, text, metadata=metadata)  # type: ignore[no-any-return]

    def long_term_search(
        self,
        query: str,
        *,
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """检索 Long-term Memory。"""
        return self._impl.long_term_search(query, top=top)  # type: ignore[no-any-return]

    # ── 跨层操作 ───────────────────────────────────────────────

    def consolidate(self, **kwargs: Any) -> dict[str, Any] | None:
        """触发 L2 → L3 consolidation。

        统一返回 dict 或 None：若底层返回 pydantic Model（如
        ``ConsolidationReport``），自动调用 ``model_dump()`` 转换。
        """
        result = self._impl.consolidate(**kwargs)
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        if hasattr(result, "model_dump"):
            return result.model_dump()  # type: ignore[no-any-return]
        # 兜底：尝试 dict() 转换或字符串包装
        try:
            return dict(result)
        except (TypeError, ValueError):
            return {"result": str(result)}

    def build_context(
        self,
        session_id: str = "",
        query: str = "",
        *,
        max_tokens: int | None = None,
    ) -> Any:
        """组装三层上下文，透传底层返回值。

        - ``mode="chat"``: 返回 ``MemoryContext`` pydantic model
        - ``mode="agent"``: 返回 ``dict``
        """
        return self._impl.build_context(
            session_id=session_id,
            query=query,
            max_tokens=max_tokens,
        )

    def stats(self) -> dict[str, Any]:
        """跨层统计信息汇总。"""
        return self._impl.stats()  # type: ignore[no-any-return]

    # ── 跨实现通信（透传到底层） ───────────────────────────────

    def query_episodic(self, query: str = "", top: int = 10) -> list[dict[str, Any]]:
        """查询 ThreeLayerMemory 写入的 episodic_memory 表。

        两种 mode 均支持：``MemoryManager`` 与 ``ThreeLayerMemory`` 都
        实现了 ``query_episodic`` 方法（``ThreeLayerMemory`` 直接查自己的表，
        ``MemoryManager`` 通过共享 DB 读取）。
        """
        impl = self._impl
        if hasattr(impl, "query_episodic"):
            return impl.query_episodic(query=query, top=top)  # type: ignore[no-any-return]
        logger.warning("[memory_facade] impl has no query_episodic method")
        return []

    def query_memory_entries(self, query: str = "", top: int = 10) -> list[dict[str, Any]]:
        """查询 MemoryManager 写入的 memory_entries 表。"""
        impl = self._impl
        if hasattr(impl, "query_memory_entries"):
            return impl.query_memory_entries(query=query, top=top)  # type: ignore[no-any-return]
        logger.warning("[memory_facade] impl has no query_memory_entries method")
        return []

    # ── 便捷 repr ──────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"MemoryFacade(mode={self._mode!r}, "
            f"impl={type(self._impl).__name__})"
        )


__all__ = ["MemoryFacade", "MemoryMode"]