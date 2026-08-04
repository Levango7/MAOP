"""Three-Layer Memory type definitions and decay policy.

Extracted from three_layer_memory.py for single-responsibility separation.
All Pydantic models, enums, and decay weight functions live here.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QualityDimensions(BaseModel):
    """Multi-dimensional quality scores for an episodic entry.

    Each dimension is 0.0 - 1.0. The composite score is the weighted average.
    """
    correctness: float = 0.0
    completeness: float = 0.0
    efficiency: float = 0.0
    clarity: float = 0.0
    safety: float = 0.0

    def composite(self) -> float:
        return round(
            (self.correctness * 0.35 + self.completeness * 0.25
             + self.efficiency * 0.20 + self.clarity * 0.10 + self.safety * 0.10),
            3,
        )


class EpisodicEntry(BaseModel):
    """A single episodic memory entry (task experience)."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    task: str = ""
    agent: str = ""
    outcome: str = ""  # success | partial | failure
    score: float = 0.0  # 0.0 - 1.0
    lessons: list[str] = Field(default_factory=list)
    user_feedback: str = ""
    quality_dimensions: QualityDimensions = Field(default_factory=QualityDimensions)
    summary: str = ""
    key_decisions: list[str] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    access_count: int = 0


class EpisodicSearchResult(BaseModel):
    """A retrieved episodic memory with decay-adjusted weight."""
    entry: EpisodicEntry
    retrieval_weight: float = 1.0


class ConsolidationReport(BaseModel):
    """Result of a consolidation pass."""
    candidates: int = 0
    consolidated: int = 0
    skipped: int = 0
    errors: int = 0


class FocusMode(str, Enum):
    """Transform focus modes."""
    DEEP_FOCUS = "deep_focus"
    BROAD_SCAN = "broad_scan"
    EXPLORATORY = "exploratory"


class ContextHead(str, Enum):
    """Multi-head context analysis perspectives.

    Inspired by Transformer multi-head attention: each head analyzes
    the same context from a different angle, then results are fused.
    """
    FACTS = "facts"
    INTENT = "intent"
    CONSTRAINTS = "constraints"


class HeadResult(BaseModel):
    """Result from a single context head analysis."""
    head: ContextHead
    items: list[ContextItem] = Field(default_factory=list)
    summary: str = ""
    token_estimate: int = 0


class MultiHeadResult(BaseModel):
    """Fused result from multi-head context analysis."""
    heads: list[HeadResult] = Field(default_factory=list)
    fused_context: list[dict[str, Any]] = Field(default_factory=list)
    total_tokens_estimate: int = 0
    fusion_strategy: str = "weighted_merge"


class FocusConfig(BaseModel):
    """Configuration for a focus mode."""
    mode: FocusMode = FocusMode.DEEP_FOCUS
    relevance_weight: float = 0.5
    importance_weight: float = 0.3
    recency_weight: float = 0.2
    memory_budget: float = 0.75
    input_budget: float = 0.20
    margin_budget: float = 0.05
    max_results: int = 10


class TransformResult(BaseModel):
    """Result of a Transform focus operation."""
    mode: FocusMode
    context_parts: list[dict[str, Any]] = Field(default_factory=list)
    total_tokens_estimate: int = 0
    memory_ratio: float = 0.0
    input_ratio: float = 0.0
    pipeline_stats: dict[str, int] = Field(default_factory=dict)


class ContextItem(BaseModel):
    """A single context item for Transform pipeline processing."""
    layer: str = ""
    source: str = ""
    data: Any = None
    weight: float = 1.0
    relevance_score: float = 0.0
    compressed: bool = False


# ── Decay Policy ─────────────────────────────────────────────

DECAY_TIERS = [
    (7, 1.0),      # 0-7 days: full weight
    (30, 0.7),     # 7-30 days: 70%
    (90, 0.4),     # 30-90 days: 40%
    (365, 0.2),    # 90-365 days: 20%
]


def decay_weight(created_at: float) -> float:
    """Compute retrieval weight based on age (time-decay)."""
    age_days = (time.time() - created_at) / 86400
    for threshold, weight in DECAY_TIERS:
        if age_days <= threshold:
            return weight
    return 0.1  # > 1 year: minimal weight
