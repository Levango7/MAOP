"""White-box tests for SemanticCache — similarity-based LLM response caching.

Exercises hit/miss/threshold/TTL/clear/stats paths against the in-memory
implementation in maop.core.semantic_cache. HashEmbedding yields cosine
similarity ≈1.0 for identical text and ≈0.85 for distinct text, which makes
the threshold boundary testable. No external services required.
"""

from __future__ import annotations

import time

import pytest

from maop.core.semantic_cache import SemanticCache


# ── 1. Cache hit ────────────────────────────────────────────────


def test_identical_query_hits() -> None:
    """An identical query returns the cached response."""
    cache = SemanticCache(similarity_threshold=0.92)
    cache.put("how to fix a timeout error in python", "set socket timeout")
    assert cache.get("how to fix a timeout error in python") == "set socket timeout"


# ── 2. Cache miss ───────────────────────────────────────────────


def test_unrelated_query_misses() -> None:
    """A semantically unrelated query does not hit the cache."""
    cache = SemanticCache(similarity_threshold=0.92)
    cache.put("how to fix a timeout error in python", "set socket timeout")
    assert cache.get("best pizza dough recipe for home oven") is None


# ── 3. Similarity threshold ─────────────────────────────────────


def test_threshold_governs_hit_or_miss() -> None:
    """The same query pair hits under a loose threshold and misses under a strict one."""
    q1 = "how to fix a timeout error in python"
    q2 = "how to resolve timeout issues in python"  # sim(q1,q2) ≈ 0.85

    loose = SemanticCache(similarity_threshold=0.80)
    loose.put(q1, "resp")
    assert loose.get(q2) == "resp"

    strict = SemanticCache(similarity_threshold=0.92)
    strict.put(q1, "resp")
    assert strict.get(q2) is None


# ── 4. TTL expiry ───────────────────────────────────────────────


def test_expired_entry_does_not_hit() -> None:
    """An entry past its TTL is skipped on get."""
    cache = SemanticCache(similarity_threshold=0.92, default_ttl_s=0.01)
    cache.put("query", "response")
    time.sleep(0.05)
    assert cache.get("query") is None


# ── 5. Clear ────────────────────────────────────────────────────


def test_clear_empties_cache() -> None:
    """clear() removes all entries so subsequent lookups miss."""
    cache = SemanticCache(similarity_threshold=0.92)
    cache.put("q1", "r1")
    cache.put("q2", "r2")
    cache.clear()
    assert cache.stats().entries == 0
    assert cache.get("q1") is None


# ── 6. Statistics ───────────────────────────────────────────────


def test_stats_track_hits_and_misses() -> None:
    """stats() reports correct hit/miss counts, entry count, and hit_rate."""
    cache = SemanticCache(similarity_threshold=0.92)
    cache.put("q1", "r1")
    assert cache.get("q1") == "r1"
    assert cache.get("q2-totally-unrelated-text") is None
    s = cache.stats()
    assert s.hits == 1
    assert s.misses == 1
    assert s.entries == 1
    assert s.hit_rate == pytest.approx(0.5)