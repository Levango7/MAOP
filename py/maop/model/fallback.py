"""FallbackManager — Model-level fallback chain execution."""

from __future__ import annotations

import logging

from maop.model.registry import ModelRegistry
from maop.model.schema import EffectiveModel

logger = logging.getLogger(__name__)


class FallbackManager:
    """Manages model-level fallback when a model fails.

    Usage::

        fm = FallbackManager(registry)
        chain = fm.get_chain(effective_model)
        for model_name in chain:
            result = try_dispatch(model_name)
            if result.success:
                break
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry
        self._failure_counts: dict[str, int] = {}  # model_name -> consecutive failures

    def get_chain(self, effective: EffectiveModel) -> list[str]:
        """获取完整 fallback 链（含主模型），过滤失败次数过多的模型。

        环路检测：去重保留首次出现，防止 A→B→C→A 配置导致外层遍历死循环。
        自引用检测：若主模型出现在其自身 fallback_chain 中，记录告警并过滤。
        """
        chain = [effective.model_name]
        chain.extend(effective.fallback_chain)
        # 过滤连续失败次数过多的模型（阈值 5）
        chain = [m for m in chain if self._failure_counts.get(m, 0) < 5]

        # 自引用检测：主模型不应出现在其 fallback_chain 中
        if effective.model_name in effective.fallback_chain:
            logger.warning(
                "Model %s appears in its own fallback chain, filtering out duplicates",
                effective.model_name,
            )

        # 环路检测：去重，保留首次出现
        seen: set[str] = set()
        deduped: list[str] = []
        for m in chain:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        return deduped

    def record_success(self, model_name: str) -> None:
        self._failure_counts.pop(model_name, None)

    def record_failure(self, model_name: str) -> None:
        self._failure_counts[model_name] = self._failure_counts.get(model_name, 0) + 1
        if self._failure_counts[model_name] >= 3:
            logger.warning(
                "Model %s has %d consecutive failures, will be deprioritized",
                model_name, self._failure_counts[model_name],
            )

    def should_fallback(self, error: str, policy_fallback_on_error: bool = True,
                        policy_fallback_on_timeout: bool = True) -> bool:
        """Determine if an error should trigger fallback."""
        if not policy_fallback_on_error:
            return False
        err_lower = error.lower()
        if "timeout" in err_lower or "timed out" in err_lower:
            return policy_fallback_on_timeout
        if "quota" in err_lower or "rate limit" in err_lower:
            return True
        if "circuit breaker" in err_lower:
            return True
        return True

    def get_failure_stats(self) -> dict[str, int]:
        return dict(self._failure_counts)

    def reset(self, model_name: str = "") -> None:
        if model_name:
            self._failure_counts.pop(model_name, None)
        else:
            self._failure_counts.clear()
