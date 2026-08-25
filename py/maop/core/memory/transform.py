"""ThreeLayerMemory — Transform (Focus Mode) mixin.

T2 架构债治理：从 ``three_layer_memory.py`` 拆分。公开 API 不变。
依赖 types/utils（ContextItem/HeadResult/_compress_text 等）与宿主的
``episodic_search`` / ``semantic_search`` / ``short_term_search``。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maop.core.memory.three_layer_memory_types import (
    ContextHead,
    ContextItem,
    FocusConfig,
    FocusMode,
    HeadResult,
    MultiHeadResult,
    TransformResult,
    decay_weight,
)
from maop.core.memory.three_layer_memory_utils import (
    _DEFAULT_FOCUS_CONFIGS,
    _compress_text,
    _item_to_text,
    _text_relevance,
)

logger = logging.getLogger(__name__)


class TransformMixin:
    """Transform（Focus Mode）方法：多 head 上下文重组。"""

    if TYPE_CHECKING:
        # 宿主类（ThreeLayerMemory）提供的方法 —— 仅用于类型检查
        working_get: Callable[..., Any]
        episodic_search: Callable[..., list[Any]]
        semantic_search: Callable[..., list[Any]]

    # ── Transform (Focus Mode) ────────────────────────────────

    def transform(
        self,
        query: str,
        mode: FocusMode = FocusMode.DEEP_FOCUS,
        config: FocusConfig | None = None,
        token_budget: int = 4000,
    ) -> TransformResult:
        """Apply a Transform five-step pipeline to assemble context.

        Pipeline: scoreRelevance → focusAttention → deduplicate
                  → compress → budgetControl

        Parameters
        ----------
        query : str
            The input query or task description.
        mode : FocusMode
            deep_focus / broad_scan / exploratory.
        config : FocusConfig, optional
            Override default weights and budgets.
        token_budget : int
            Max token budget for final context.

        Returns
        -------
        TransformResult
        """
        cfg = config or _DEFAULT_FOCUS_CONFIGS[mode]
        stats: dict[str, int] = {}

        # ── Gather raw context from all layers ──────────────
        items: list[ContextItem] = []

        working_data = self.working_get(query)
        if working_data is not None:
            items.append(ContextItem(layer="working", source=query, data=working_data, weight=1.0))

        episodic_results = self.episodic_search(
            query=query, top=cfg.max_results, apply_decay=True,
        )
        for er in episodic_results:
            items.append(ContextItem(
                layer="episodic", source=er.entry.id,
                data=er.entry.model_dump(), weight=er.entry.score,
            ))

        try:
            semantic_results = self.semantic_search(query, top=cfg.max_results)
            for sr in semantic_results:
                items.append(ContextItem(
                    layer="semantic", source=getattr(sr, "id", ""),
                    data=str(sr), weight=0.6,
                ))
        except Exception as exc:
            logger.debug("Semantic search skipped in transform: %s", exc)

        stats["raw_items"] = len(items)

        # ── Step 1: scoreRelevance ──────────────────────────
        for item in items:
            text = _item_to_text(item)
            item.relevance_score = _text_relevance(query, text)
            # C4 fix: decay_weight(time.time()) computed age=0 (always the top
            # tier, recency factor constantly 1.0). Use the item's actual
            # created_at from the episodic entry dump so older memories decay.
            if item.layer == "episodic":
                created_at = time.time()
                if isinstance(item.data, dict):
                    created_at = float(item.data.get("created_at") or created_at)
                recency = decay_weight(created_at)
            else:
                recency = 1.0
            item.weight = (
                item.relevance_score * cfg.relevance_weight
                + item.weight * cfg.importance_weight
                + recency * cfg.recency_weight
            )

        # ── Step 2: focusAttention ──────────────────────────
        items.sort(key=lambda i: i.weight, reverse=True)
        if mode == FocusMode.DEEP_FOCUS:
            items = items[:3]
        elif mode == FocusMode.BROAD_SCAN:
            items = items[:cfg.max_results]
        stats["after_focus"] = len(items)

        # ── Step 3: deduplicate ─────────────────────────────
        seen_hashes: set[int] = set()
        deduped: list[ContextItem] = []
        for item in items:
            h = hash(_item_to_text(item)[:200])
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped.append(item)
        items = deduped
        stats["after_dedup"] = len(items)

        # ── Step 4: compress ────────────────────────────────
        for item in items:
            text = _item_to_text(item)
            if len(text) > 500:
                item.data = _compress_text(text)
                item.compressed = True
        stats["compressed"] = sum(1 for i in items if i.compressed)

        # ── Step 5: budgetControl ───────────────────────────
        budget_items: list[ContextItem] = []
        used_tokens = 0
        for item in items:
            item_chars = len(json.dumps(item.data, default=str))
            item_tokens = item_chars // 4
            if used_tokens + item_tokens <= token_budget or item.layer == "working":
                budget_items.append(item)
                used_tokens += item_tokens
        items = budget_items
        stats["final_items"] = len(items)

        context_parts = [
            {"layer": i.layer, "source": i.source, "data": i.data,
             "weight": round(i.weight, 4), "compressed": i.compressed}
            for i in items
        ]

        n_parts = len(context_parts) or 1
        memory_parts = sum(1 for p in context_parts if p["layer"] != "input")
        memory_ratio = round(memory_parts / n_parts, 2) if context_parts else 0.0

        return TransformResult(
            mode=mode,
            context_parts=context_parts,
            total_tokens_estimate=used_tokens,
            memory_ratio=memory_ratio,
            input_ratio=round(1.0 - memory_ratio, 2),
            pipeline_stats=stats,
        )

    def transform_multi_head(
        self,
        query: str,
        heads: list[ContextHead] | None = None,
        token_budget: int = 4000,
    ) -> MultiHeadResult:
        """Apply multi-head context analysis from different perspectives.

        Each head filters and weights context items by its perspective:
          - FACTS: objective data, measurements, outcomes
          - INTENT: user goals, task descriptions, requirements
          - CONSTRAINTS: limits, rules, errors, pitfalls

        Results are fused via weighted merge (dedup + re-rank).

        Parameters
        ----------
        query : str
            The input query or task description.
        heads : list[ContextHead], optional
            Which heads to activate. Default: all three.
        token_budget : int
            Max token budget for fused context.

        Returns
        -------
        MultiHeadResult
        """
        active_heads = heads or list(ContextHead)
        all_items = self._gather_context_items(query)

        head_results: list[HeadResult] = []
        for head in active_heads:
            filtered = self._filter_by_head(all_items, head, query)
            summary = self._summarize_head(filtered, head)
            tokens = sum(len(_item_to_text(i)) // 4 for i in filtered)
            head_results.append(HeadResult(
                head=head, items=filtered, summary=summary, token_estimate=tokens,
            ))

        fused = self._fuse_heads(head_results, token_budget)

        return MultiHeadResult(
            heads=head_results,
            fused_context=fused,
            total_tokens_estimate=sum(len(json.dumps(p, default=str)) // 4 for p in fused),
            fusion_strategy="weighted_merge",
        )

    def _gather_context_items(self, query: str) -> list[ContextItem]:
        items: list[ContextItem] = []
        working_data = self.working_get(query)
        if working_data is not None:
            items.append(ContextItem(layer="working", source=query, data=working_data, weight=1.0))

        episodic_results = self.episodic_search(query=query, top=10, apply_decay=True)
        for er in episodic_results:
            items.append(ContextItem(
                layer="episodic", source=er.entry.id,
                data=er.entry.model_dump(), weight=er.entry.score,
            ))

        try:
            semantic_results = self.semantic_search(query, top=10)
            for sr in semantic_results:
                items.append(ContextItem(
                    layer="semantic", source=getattr(sr, "id", ""),
                    data=str(sr), weight=0.6,
                ))
        except Exception as e:
            logger.debug("ignored: %s", e, exc_info=True)

        return items

    @staticmethod
    def _filter_by_head(items: list[ContextItem], head: ContextHead, query: str) -> list[ContextItem]:
        """Filter and re-weight items by head perspective."""
        _HEAD_KEYWORDS: dict[ContextHead, set[str]] = {
            ContextHead.FACTS: {"result", "output", "data", "score", "outcome", "success", "failure", "metric", "value", "count"},
            ContextHead.INTENT: {"task", "goal", "want", "need", "require", "should", "must", "plan", "objective", "request"},
            ContextHead.CONSTRAINTS: {"error", "limit", "timeout", "budget", "rule", "constraint", "cannot", "forbidden", "pitfall", "warning"},
        }
        keywords = _HEAD_KEYWORDS.get(head, set())

        scored: list[tuple[float, ContextItem]] = []
        for item in items:
            text = _item_to_text(item).lower()
            overlap = len(keywords & set(text.split()))
            keyword_score = min(overlap / max(len(keywords), 1), 1.0) if keywords else 0.0
            relevance = _text_relevance(query, text)
            combined = item.weight * 0.4 + keyword_score * 0.4 + relevance * 0.2
            scored.append((combined, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:10]]

    @staticmethod
    def _summarize_head(items: list[ContextItem], head: ContextHead) -> str:
        if not items:
            return f"No context from {head.value} perspective"
        parts = [_item_to_text(i)[:100] for i in items[:3]]
        return f"{head.value}: {'; '.join(parts)}"

    @staticmethod
    def _fuse_heads(head_results: list[HeadResult], token_budget: int) -> list[dict[str, Any]]:
        """Fuse multi-head results via weighted merge with dedup."""
        seen_hashes: set[int] = set()
        all_weighted: list[tuple[float, ContextItem]] = []

        for hr in head_results:
            head_weight = {
                ContextHead.FACTS: 0.35,
                ContextHead.INTENT: 0.40,
                ContextHead.CONSTRAINTS: 0.25,
            }.get(hr.head, 0.3)
            for item in hr.items:
                h = hash(_item_to_text(item)[:200])
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                all_weighted.append((item.weight * head_weight, item))

        all_weighted.sort(key=lambda x: x[0], reverse=True)

        fused: list[dict[str, Any]] = []
        used_tokens = 0
        for w, item in all_weighted:
            item_tokens = len(json.dumps(item.data, default=str)) // 4
            if used_tokens + item_tokens <= token_budget:
                fused.append({
                    "layer": item.layer, "source": item.source,
                    "data": item.data, "weight": round(w, 4),
                    "compressed": item.compressed,
                })
                used_tokens += item_tokens

        return fused

