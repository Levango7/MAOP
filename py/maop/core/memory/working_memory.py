"""ThreeLayerMemory Layer 1 — Working Memory (in-process LRU) mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
依赖宿主类的 ``episodic_store``（EpisodicStoreMixin）与 ``logger``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maop.core.reliability.cache import LRUCache

logger = logging.getLogger(__name__)


class WorkingMemoryMixin:
    """Layer 1: Working Memory（进程内 LRU）方法。

    构造函数中的 LRU 初始化仍留在宿主类（引用 ``self._overflow_to_episodic``）。
    """

    if TYPE_CHECKING:
        # 宿主类（ThreeLayerMemory）提供的属性 —— 仅用于类型检查
        _working: LRUCache
        episodic_store: Callable[..., str]

    def working_put(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """Store a value in Working Memory (session-scoped, fast).

        When the LRU cache evicts an entry (capacity full), the EVICTED
        entry (not the new one) is automatically overflowed to Episodic
        Memory via the ``on_evict`` callback registered at construction.
        """
        self._working.put(key, value, ttl_s=ttl_s or 3600.0)

    def working_get(self, key: str) -> Any:
        """Retrieve a value from Working Memory."""
        return self._working.get(key)

    def working_delete(self, key: str) -> None:
        """Delete a value from Working Memory."""
        self._working.delete(key)

    def working_clear(self) -> None:
        """Clear all Working Memory entries."""
        self._working.clear()

    def working_pin(self, key: str) -> bool:
        """Pin a Working Memory key so it is never evicted by LRU or compression."""
        return self._working.pin(key)  # type: ignore

    def working_unpin(self, key: str) -> None:
        """Unpin a Working Memory key, allowing normal eviction."""
        self._working.unpin(key)

    def working_pinned_keys(self) -> list[str]:
        """Return all pinned Working Memory keys."""
        return self._working.pinned_keys()  # type: ignore

    def _overflow_to_episodic(self, key: str, value: Any) -> None:
        """Overflow an evicted Working Memory entry to Episodic Memory (L1).

        Invoked by LRUCache's ``on_evict`` callback whenever an entry is
        evicted due to capacity pressure. Receives the EVICTED (key, value)
        pair — NOT the new entry that triggered the eviction.

        Args:
            key: The evicted Working Memory key.
            value: The evicted Working Memory value.
        """
        try:
            summary = json.dumps(value, default=str)[:500] if not isinstance(value, str) else value[:500]
            self.episodic_store(
                task=f"Working memory overflow: {key}",
                agent="system",
                outcome="overflow",
                score=0.3,
                lessons=[f"Evicted key: {key}", f"Value summary: {summary}"],
            )
            logger.debug("Overflowed evicted working memory key '%s' to episodic", key)
        except Exception as exc:
            logger.warning("Failed to overflow evicted working memory key '%s': %s", key, exc)

