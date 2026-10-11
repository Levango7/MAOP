"""MAOP Rank Fusion — Reciprocal Rank Fusion for cross-modal retrieval (F2-02 B1).

RRF combines several ranked result lists into one ordering using only the
**ranks** (not the raw scores), which is exactly what cross-modal fusion
needs: a CLIP cosine score (0…0.35 in practice for ViT-B/32) and a text
memory similarity (0…1) are not comparable, but "position in its own list"
is.

    score(d) = Σ_sources  weight_source / (k + rank_source(d))

``k`` damps the influence of top ranks (60 is the canonical value from
Cormack et al. 2009 and what the spike/ADR-022 propose). The function is
pure and stateless so it can be unit-tested and reused by any retriever;
weighted fusion / reranking can be layered on top later without changing
this contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["FusedHit", "reciprocal_rank_fusion"]

#: Ranked list as accepted by :func:`reciprocal_rank_fusion`:
#: ``(source_name, [(doc_id, score), ...])`` with the list already ordered
#: best-first.
RankedList = tuple[str, Sequence[tuple[str, float]]]


class FusedHit(BaseModel):
    """One fused result: which doc won and which sources ranked it."""

    id: str
    score: float  # RRF score — rank-derived, NOT a similarity in [0, 1]
    sources: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[RankedList],
    *,
    k: int = 60,
    weights: dict[str, float] | None = None,
    top: int | None = None,
) -> list[FusedHit]:
    """Fuse ranked lists by Reciprocal Rank Fusion.

    Parameters
    ----------
    ranked_lists : sequence of ``(source, [(id, score), ...])``
        Each inner list must already be ordered best-first; the function
        never re-sorts within a list. A repeated id inside one list counts
        only its first (best) rank.
    k : int
        RRF damping constant (> 0).
    weights : dict | None
        Optional per-source weight (default 1.0 for every source).
    top : int | None
        Truncate the fused output.

    Returns
    -------
    list[FusedHit]
        Ordered by fused score descending; ties broken by id for
        determinism. ``payload["best_score"]`` keeps the best raw score per
        source so callers can still surface similarity values.
    """
    if k <= 0:
        raise ValueError("k must be positive")

    scores: dict[str, float] = {}
    best_raw: dict[str, dict[str, float]] = {}
    sources_seen: dict[str, list[str]] = {}

    for source, ranked in ranked_lists:
        weight = float((weights or {}).get(source, 1.0))
        seen_in_this_list: set[str] = set()
        for rank, (doc_id, raw_score) in enumerate(ranked, start=1):
            if doc_id in seen_in_this_list:
                continue  # same id twice in one list: count the better rank
            seen_in_this_list.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
            sources_seen.setdefault(doc_id, []).append(source)
            per_source = best_raw.setdefault(doc_id, {})
            prev = per_source.get(source)
            if prev is None or raw_score > prev:
                per_source[source] = float(raw_score)

    fused = [
        FusedHit(
            id=doc_id,
            score=round(score, 10),
            sources=sources_seen[doc_id],
            payload={"best_score": best_raw[doc_id]},
        )
        for doc_id, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.id))
    if top is not None and top > 0:
        return fused[:top]
    return fused
