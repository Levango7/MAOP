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

    # ── F1-03 统一 CRUD 入口 ───────────────────────────────────
    # 按 layer 参数自动路由到 working / short_term / long_term 对应方法。
    # 接受两套命名：working/short_term/long_term 与 working/episodic/semantic。
    # 底层实现若提供了 store/retrieve/search/delete 方法则优先透传，
    # 否则由 Facade 兜底路由到层专属方法。

    def store(self, layer: str, content: Any, **kwargs: Any) -> str:
        """统一存储入口，按 ``layer`` 路由到对应层。

        See Also
        --------
        :meth:`maop.memory.unified.UnifiedMemoryProtocol.store`
        """
        # 优先透传给底层实现（若底层已提供统一 store 方法）
        impl = self._impl
        if hasattr(impl, "store") and callable(getattr(impl, "store")):
            try:
                return impl.store(layer, content, **kwargs)  # type: ignore[no-any-return]
            except (TypeError, ValueError):
                # 底层 store 签名不兼容（如 MemoryManager.store 不存在），
                # 落到下面的 Facade 兜底路由。
                pass

        normalized = _normalize_layer(layer)
        if normalized == "working":
            key = kwargs.pop("key", "") or f"mem-{_next_id()}"
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
            doc_id = kwargs.pop("doc_id", f"doc-{_next_id()}")
            return self.long_term_index(
                doc_id, str(content), metadata=kwargs.pop("metadata", None)
            )
        raise ValueError(f"Unknown layer: {layer!r}")

    def retrieve(self, layer: str, query: str = "", top: int = 10, **kwargs: Any) -> Any:
        """统一检索入口，按 ``layer`` 路由到对应层。

        See Also
        --------
        :meth:`maop.memory.unified.UnifiedMemoryProtocol.retrieve`

        Notes
        -----
        不优先透传给底层 ``impl.retrieve``：两套实现的 ``retrieve`` 签名
        不一致（``ThreeLayerMemory.retrieve`` 的 working 层返回 ``list``，
        ``MemoryManager.retrieve`` 返回单值）。Facade 直接路由到层专属方法
        以保证 Protocol 定义的统一返回类型。
        """
        normalized = _normalize_layer(layer)
        if normalized == "working":
            # working 层：query 作为 key，返回单值或 None
            return self.working_get(query) if query else None
        if normalized == "short_term":
            return self.short_term_search(query=query, top=top, agent=kwargs.get("agent", ""))
        if normalized == "long_term":
            return self.long_term_search(query, top=top)
        raise ValueError(f"Unknown layer: {layer!r}")

    def search(self, query: str, *, top: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """跨层搜索：同时检索 short_term + long_term，合并去重后返回。

        每条结果附带 ``layer`` 字段标识来源（``"short_term"`` / ``"long_term"``）。
        去重以 ``id`` 字段为准，short_term 优先保留。

        See Also
        --------
        :meth:`maop.memory.unified.UnifiedMemoryProtocol.search`
        """
        impl = self._impl
        if hasattr(impl, "search") and callable(getattr(impl, "search")):
            try:
                result = impl.search(query, top=top, **kwargs)
                if isinstance(result, list):
                    return result
            except (TypeError, ValueError):
                pass

        # Facade 兜底：合并 short_term + long_term
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            short_results = self.short_term_search(query=query, top=top, agent=kwargs.get("agent", ""))
            for r in short_results:
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
            logger.debug("[memory_facade] search short_term failed: %s", exc)

        try:
            long_results = self.long_term_search(query, top=top)
            for r in long_results:
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
            logger.debug("[memory_facade] search long_term failed: %s", exc)

        return merged[:top] if top > 0 else merged

    def delete(self, layer: str, entry_id: str) -> bool:
        """按 ID 删除指定层的条目。

        See Also
        --------
        :meth:`maop.memory.unified.UnifiedMemoryProtocol.delete`
        """
        impl = self._impl
        if hasattr(impl, "delete") and callable(getattr(impl, "delete")):
            try:
                result = impl.delete(layer, entry_id)
                if isinstance(result, bool):
                    return result
            except (TypeError, ValueError):
                pass

        normalized = _normalize_layer(layer)
        if normalized == "working":
            # working 层：entry_id 作为 key，调用底层 working_delete（若有）
            if hasattr(impl, "working_delete"):
                try:
                    impl.working_delete(entry_id)
                    return True
                except Exception as exc:
                    logger.debug("[memory_facade] delete working failed: %s", exc)
                    return False
            # MemoryManager 没有 working_delete，直接 pop
            cache = getattr(impl, "_working_cache", None)
            if isinstance(cache, dict):
                if entry_id in cache:
                    cache.pop(entry_id, None)
                    return True
            return False
        if normalized == "short_term":
            return self._delete_short_term(entry_id)
        if normalized == "long_term":
            return self._delete_long_term(entry_id)
        raise ValueError(f"Unknown layer: {layer!r}")

    # ── delete 辅助方法 ────────────────────────────────────────

    def _delete_short_term(self, entry_id: str) -> bool:
        """删除 short_term 条目（memory_entries 或 episodic_memory 表）。"""
        from maop.core.backends.db_utils import sqlite_connect

        from maop.memory.shared_db import get_memory_db_path

        db_path = get_memory_db_path()
        deleted = False
        try:
            with sqlite_connect(db_path, foreign_keys=False) as conn:
                # 尝试从 memory_entries 删除
                cur = conn.execute(
                    "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
                )
                if cur.rowcount > 0:
                    deleted = True
                # 尝试从 episodic_memory 删除
                cur = conn.execute(
                    "DELETE FROM episodic_memory WHERE id = ?", (entry_id,)
                )
                if cur.rowcount > 0:
                    deleted = True
        except Exception as exc:
            logger.warning("[memory_facade] delete short_term failed: %s", exc)
            return False
        return deleted

    def _delete_long_term(self, doc_id: str) -> bool:
        """删除 long_term 条目（向量索引）。"""
        impl = self._impl
        vs = getattr(impl, "_vector_store", None)
        # 若底层有 vector_search 属性（MemoryManager），尝试用它
        if vs is None:
            vs = getattr(impl, "vector_search", None)
        if vs is None:
            # ThreeLayerMemory 懒加载 _get_vector_store
            getter = getattr(impl, "_get_vector_store", None)
            if callable(getter):
                try:
                    vs = getter()
                except Exception as exc:
                    logger.debug("[memory_facade] get vector_store failed: %s", exc)
                    vs = None
        if vs is None:
            return False
        # VectorStore 通常有 delete 方法
        delete_fn = getattr(vs, "delete", None)
        if callable(delete_fn):
            try:
                delete_fn(doc_id)
                return True
            except Exception as exc:
                logger.debug("[memory_facade] vector delete failed: %s", exc)
                return False
        return False

    # ── 便捷 repr ──────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"MemoryFacade(mode={self._mode!r}, "
            f"impl={type(self._impl).__name__})"
        )


# ── 模块级辅助函数 ──────────────────────────────────────────────


def _normalize_layer(layer: str) -> str:
    """将 layer 名称标准化为 working/short_term/long_term。

    接受两套命名：MemoryManager 的 working/short_term/long_term 与
    ThreeLayerMemory 的 working/episodic/semantic。大小写不敏感。
    """
    from maop.memory.shared_db import normalize_layer_name

    return normalize_layer_name(layer)


def _next_id() -> str:
    """生成一个简短唯一 ID（用于 store 时未指定 key/doc_id 的兜底）。"""
    import time
    import uuid

    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


__all__ = ["MemoryFacade", "MemoryMode"]