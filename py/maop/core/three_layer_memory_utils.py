"""Three-Layer Memory utility functions.

Extracted from three_layer_memory.py. Text processing helpers for
context transformation and feedback analysis.
"""
from __future__ import annotations

import json
import logging

from maop.core.three_layer_memory_types import ContextItem, FocusConfig, FocusMode

logger = logging.getLogger(__name__)


def _text_relevance(query: str, text: str) -> float:
    """Compute simple text relevance score (0.0 - 1.0).

    Uses token overlap ratio between query and text.
    """
    q_tokens = set(query.lower().split())
    t_tokens = set(text.lower().split())
    if not q_tokens or not t_tokens:
        return 0.0
    overlap = len(q_tokens & t_tokens)
    return min(overlap / len(q_tokens), 1.0)


def _item_to_text(item: ContextItem) -> str:
    """Extract searchable text from a ContextItem."""
    data = item.data
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return data.get("task", "") or data.get("content", "") or json.dumps(data, default=str)
    return json.dumps(data, default=str)


def _compress_text(text: str, max_len: int = 300) -> str:
    """Compress long text to a summary (first/last sentences + ellipsis)."""
    if len(text) <= max_len:
        return text
    sentences = text.replace("\n", ". ").split(". ")
    if len(sentences) <= 2:
        return text[:max_len] + "..."
    head = sentences[0]
    tail = sentences[-1]
    result = f"{head}. ... {tail}."
    if len(result) > max_len:
        result = text[:max_len // 2] + " ... " + text[-max_len // 2:]
    return result


_DEFAULT_FOCUS_CONFIGS: dict[FocusMode, FocusConfig] = {
    FocusMode.DEEP_FOCUS: FocusConfig(
        mode=FocusMode.DEEP_FOCUS,
        relevance_weight=0.6,
        importance_weight=0.3,
        recency_weight=0.1,
        memory_budget=0.75,
        input_budget=0.20,
        margin_budget=0.05,
        max_results=3,
    ),
    FocusMode.BROAD_SCAN: FocusConfig(
        mode=FocusMode.BROAD_SCAN,
        relevance_weight=0.3,
        importance_weight=0.2,
        recency_weight=0.5,
        memory_budget=0.50,
        input_budget=0.40,
        margin_budget=0.10,
        max_results=20,
    ),
    FocusMode.EXPLORATORY: FocusConfig(
        mode=FocusMode.EXPLORATORY,
        relevance_weight=0.4,
        importance_weight=0.3,
        recency_weight=0.3,
        memory_budget=0.60,
        input_budget=0.30,
        margin_budget=0.10,
        max_results=10,
    ),
}


_NEGATIVE_KEYWORDS = frozenset({
    "bad", "wrong", "incorrect", "broken", "failed", "terrible",
    "awful", "poor", "unacceptable", "useless", "error",
    "bug", "crash", "slow", "missing", "incomplete",
})


def _is_negative_feedback(feedback: str) -> bool:
    """Check if user feedback text indicates negative sentiment."""
    if not feedback:
        return False
    words = set(feedback.lower().split())
    return bool(words & _NEGATIVE_KEYWORDS)
