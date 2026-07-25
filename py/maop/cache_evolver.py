"""MAOP Cache Strategy Evolver — Phase β.3b

Evolves cache configuration (TTL, max_size, similarity threshold) based
on observed hit-rate and eviction patterns.  Works with both
``LRUCache`` (exact-match cache) and ``SemanticCache`` (similarity cache).

Strategy:
  - High hit-rate (>80%): increase TTL to retain useful entries longer,
    raise similarity threshold to be more selective (precision).
  - Low hit-rate (<30%): decrease TTL to free memory from useless
    entries, lower similarity threshold to catch more matches (recall).
  - High eviction rate: increase max_size if memory allows.
  - Zero hit-rate with entries: entries are stale — shorten TTL.

Outputs ``CacheStrategyAdjustment`` suggestions that can be consumed by
``EvolveEngine.auto_evolve()``.

Usage::

    from maop.cache_evolver import CacheEvolver

    evolver = CacheEvolver()
    report = evolver.evolve()
    for adj in report.adjustments:
        print(adj.cache_name, adj.parameter, adj.old_value, adj.new_value)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class CacheStrategyAdjustment:
    """A proposed or applied cache configuration change."""
    cache_name: str = ""
    cache_type: str = ""  # "lru" | "semantic"
    parameter: str = ""   # "default_ttl_s" | "max_size" | "similarity_threshold"
    old_value: float = 0.0
    new_value: float = 0.0
    reason: str = ""
    applied: bool = False
    auto_applicable: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class CacheEvolveReport:
    """Result of a cache evolution run."""
    total_caches: int = 0
    adjustments: list[CacheStrategyAdjustment] = field(default_factory=list)
    applied_count: int = 0
    skipped_count: int = 0
    recommendations: list[str] = field(default_factory=list)


# ── Evolver ───────────────────────────────────────────────────────

class CacheEvolver:
    """Evolve cache strategies based on runtime statistics.

    The evolver inspects all registered named caches (via
    ``maop.core.cache._caches`` registry) and any ``SemanticCache``
    instances passed to ``evolve()``.  It does NOT modify caches
    unless ``apply=True`` is passed.

    Parameters
    ----------
    high_hit_rate : float
        Hit rate above which TTL is increased (default 0.8).
    low_hit_rate : float
        Hit rate below which TTL is decreased (default 0.3).
    ttl_adjust_factor : float
        Multiplicative factor for TTL adjustments (default 1.5 —
        increase by 50%, decrease by 33%).
    min_ttl_s : float
        Floor for TTL adjustments (default 30s).
    max_ttl_s : float
        Ceiling for TTL adjustments (default 3600s).
    min_samples : int
        Minimum hits+misses before adjusting (avoid noise).
    """

    def __init__(
        self,
        high_hit_rate: float = 0.8,
        low_hit_rate: float = 0.3,
        ttl_adjust_factor: float = 1.5,
        min_ttl_s: float = 30.0,
        max_ttl_s: float = 3600.0,
        min_samples: int = 20,
    ) -> None:
        self._high_hit_rate = float(high_hit_rate)
        self._low_hit_rate = float(low_hit_rate)
        self._ttl_factor = max(1.1, float(ttl_adjust_factor))
        self._min_ttl = float(min_ttl_s)
        self._max_ttl = float(max_ttl_s)
        self._min_samples = max(1, int(min_samples))

    def evolve(
        self,
        semantic_caches: dict[str, Any] | None = None,
        *,
        apply: bool = False,
    ) -> CacheEvolveReport:
        """Analyze caches and optionally apply adjustments.

        Parameters
        ----------
        semantic_caches : dict[str, SemanticCache] | None
            Named semantic caches to evolve alongside LRUCaches.
        apply : bool
            If True, auto-apply safe adjustments.  If False, only
            generate suggestions for human review.
        """
        adjustments: list[CacheStrategyAdjustment] = []
        recommendations: list[str] = []

        # ── LRU Caches ──────────────────────────────────────
        lru_caches = self._collect_lru_caches()
        for name, cache in lru_caches.items():
            adj = self._analyze_lru(name, cache)
            adjustments.extend(adj)

        # ── Semantic Caches ─────────────────────────────────
        if semantic_caches:
            for name, cache in semantic_caches.items():
                adj = self._analyze_semantic(name, cache)
                adjustments.extend(adj)

        # ── Apply safe adjustments ──────────────────────────
        applied = 0
        skipped = 0
        if apply:
            for adj in adjustments:
                if adj.auto_applicable:
                    if self._apply_adjustment(adj, lru_caches, semantic_caches or {}):
                        adj.applied = True
                        applied += 1
                    else:
                        skipped += 1
                else:
                    skipped += 1
        else:
            skipped = sum(1 for a in adjustments if not a.auto_applicable)

        # ── Recommendations ─────────────────────────────────
        total_caches = len(lru_caches) + len(semantic_caches or {})
        if not adjustments:
            recommendations.append(
                "No cache adjustments needed — all caches performing within targets."
            )
        else:
            ttl_changes = sum(1 for a in adjustments if a.parameter == "default_ttl_s")
            size_changes = sum(1 for a in adjustments if a.parameter == "max_size")
            thresh_changes = sum(
                1 for a in adjustments if a.parameter == "similarity_threshold"
            )
            if ttl_changes:
                recommendations.append(
                    f"{ttl_changes} cache(s) have TTL adjustments suggested "
                    f"(tune retention to match access patterns)."
                )
            if size_changes:
                recommendations.append(
                    f"{size_changes} cache(s) would benefit from size adjustment."
                )
            if thresh_changes:
                recommendations.append(
                    f"{thresh_changes} semantic cache(s) need similarity threshold tuning."
                )

        low_hit = [
            name for name, c in lru_caches.items()
            if (c.stats().hits + c.stats().misses) >= self._min_samples
            and c.stats().hit_rate < self._low_hit_rate
        ]
        if low_hit:
            recommendations.append(
                f"Low hit-rate caches: {', '.join(low_hit)} — "
                f"consider shortening TTL or reviewing key patterns."
            )

        return CacheEvolveReport(
            total_caches=total_caches,
            adjustments=adjustments,
            applied_count=applied,
            skipped_count=skipped,
            recommendations=recommendations,
        )

    def _collect_lru_caches(self) -> dict[str, Any]:
        """Collect all registered named LRUCache instances."""
        try:
            from maop.core.cache import _caches
            # Return a snapshot copy to avoid mutation during iteration
            return dict(_caches)
        except Exception as exc:
            logger.warning("[cache_evolver] Failed to collect LRU caches: %s", exc)
            return {}

    def _analyze_lru(self, name: str, cache: Any) -> list[CacheStrategyAdjustment]:
        """Analyze a single LRUCache and generate adjustments."""
        stats = cache.stats()
        total_ops = stats.hits + stats.misses
        adjustments: list[CacheStrategyAdjustment] = []

        if total_ops < self._min_samples:
            return adjustments  # not enough data

        hit_rate = stats.hit_rate
        # Read current config via attributes (runtime tuning — these are
        # configuration values, not internal state, despite the _ prefix)
        current_ttl = getattr(cache, "_default_ttl", 0.0)
        current_max = getattr(cache, "_max_size", 256)

        # ── TTL adjustment ──────────────────────────────────
        if hit_rate >= self._high_hit_rate and current_ttl > 0:
            # High hit-rate: increase TTL to retain useful entries
            new_ttl = min(self._max_ttl, current_ttl * self._ttl_factor)
            if new_ttl > current_ttl * 1.05:  # only if meaningful change
                adjustments.append(CacheStrategyAdjustment(
                    cache_name=name,
                    cache_type="lru",
                    parameter="default_ttl_s",
                    old_value=current_ttl,
                    new_value=round(new_ttl, 1),
                    reason=(
                        f"Hit-rate {hit_rate:.0%} (>{self._high_hit_rate:.0%} target) — "
                        f"increase TTL to retain valuable entries"
                    ),
                    auto_applicable=True,
                ))
        elif hit_rate == 0.0 and stats.size > 0 and current_ttl > 0:
            # Zero hits with entries: all stale — aggressively shorten TTL
            # NOTE: must come before low_hit_rate check, since 0.0 < low_hit_rate
            new_ttl = max(self._min_ttl, current_ttl / 3)
            adjustments.append(CacheStrategyAdjustment(
                cache_name=name,
                cache_type="lru",
                parameter="default_ttl_s",
                old_value=current_ttl,
                new_value=round(new_ttl, 1),
                reason="Zero hits with populated cache — entries likely stale",
                auto_applicable=True,
            ))
        elif hit_rate < self._low_hit_rate and current_ttl > 0:
            # Low hit-rate: decrease TTL to free memory from stale entries
            new_ttl = max(self._min_ttl, current_ttl / self._ttl_factor)
            if new_ttl < current_ttl * 0.95:
                adjustments.append(CacheStrategyAdjustment(
                    cache_name=name,
                    cache_type="lru",
                    parameter="default_ttl_s",
                    old_value=current_ttl,
                    new_value=round(new_ttl, 1),
                    reason=(
                        f"Hit-rate {hit_rate:.0%} (<{self._low_hit_rate:.0%} floor) — "
                        f"decrease TTL to evict stale entries faster"
                    ),
                    auto_applicable=True,
                ))

        # ── Size adjustment (high eviction rate) ────────────
        if total_ops > 0:
            eviction_rate = stats.evictions / total_ops if total_ops > 0 else 0
            if eviction_rate > 0.3 and stats.size >= current_max:
                # High eviction + full cache: increase size
                new_max = int(current_max * 1.5)
                adjustments.append(CacheStrategyAdjustment(
                    cache_name=name,
                    cache_type="lru",
                    parameter="max_size",
                    old_value=current_max,
                    new_value=new_max,
                    reason=(
                        f"Eviction rate {eviction_rate:.0%} with full cache — "
                        f"increase size to reduce churn"
                    ),
                    auto_applicable=False,  # size increase uses more memory, needs human
                ))

        return adjustments

    def _analyze_semantic(self, name: str, cache: Any) -> list[CacheStrategyAdjustment]:
        """Analyze a SemanticCache and generate adjustments."""
        stats = cache.stats()
        total_ops = stats.hits + stats.misses
        adjustments: list[CacheStrategyAdjustment] = []

        if total_ops < self._min_samples:
            return adjustments

        hit_rate = stats.hit_rate
        current_threshold = getattr(cache, "_threshold", 0.92)
        current_ttl = getattr(cache, "_default_ttl", 0.0)

        # ── Similarity threshold adjustment ─────────────────
        if hit_rate < self._low_hit_rate:
            # Low hit-rate: lower threshold to catch more matches (recall)
            new_threshold = max(0.7, current_threshold - 0.02)
            if new_threshold < current_threshold:
                adjustments.append(CacheStrategyAdjustment(
                    cache_name=name,
                    cache_type="semantic",
                    parameter="similarity_threshold",
                    old_value=current_threshold,
                    new_value=round(new_threshold, 3),
                    reason=(
                        f"Hit-rate {hit_rate:.0%} (<{self._low_hit_rate:.0%}) — "
                        f"lower threshold to increase recall"
                    ),
                    auto_applicable=True,
                ))
        elif hit_rate >= self._high_hit_rate:
            # High hit-rate: raise threshold for better precision
            new_threshold = min(0.99, current_threshold + 0.02)
            if new_threshold > current_threshold:
                adjustments.append(CacheStrategyAdjustment(
                    cache_name=name,
                    cache_type="semantic",
                    parameter="similarity_threshold",
                    old_value=current_threshold,
                    new_value=round(new_threshold, 3),
                    reason=(
                        f"Hit-rate {hit_rate:.0%} (>{self._high_hit_rate:.0%}) — "
                        f"raise threshold to improve precision"
                    ),
                    auto_applicable=True,
                ))

        # ── TTL adjustment for semantic cache ───────────────
        if current_ttl > 0:
            if hit_rate >= self._high_hit_rate:
                new_ttl = min(self._max_ttl, current_ttl * self._ttl_factor)
                if new_ttl > current_ttl * 1.05:
                    adjustments.append(CacheStrategyAdjustment(
                        cache_name=name,
                        cache_type="semantic",
                        parameter="default_ttl_s",
                        old_value=current_ttl,
                        new_value=round(new_ttl, 1),
                        reason="High hit-rate — extend TTL",
                        auto_applicable=True,
                    ))
            elif hit_rate < self._low_hit_rate:
                new_ttl = max(self._min_ttl, current_ttl / self._ttl_factor)
                if new_ttl < current_ttl * 0.95:
                    adjustments.append(CacheStrategyAdjustment(
                        cache_name=name,
                        cache_type="semantic",
                        parameter="default_ttl_s",
                        old_value=current_ttl,
                        new_value=round(new_ttl, 1),
                        reason="Low hit-rate — shorten TTL",
                        auto_applicable=True,
                    ))

        return adjustments

    def _apply_adjustment(
        self,
        adj: CacheStrategyAdjustment,
        lru_caches: dict[str, Any],
        semantic_caches: dict[str, Any],
    ) -> bool:
        """Apply a single adjustment to the target cache.

        Uses ``setattr`` to update configuration attributes — these are
        runtime-tunable parameters (TTL, threshold, max_size), not
        internal state.  This is the standard Python pattern for
        runtime reconfiguration without restart.
        """
        try:
            if adj.cache_type == "lru":
                cache = lru_caches.get(adj.cache_name)
                if cache is None:
                    return False
                if adj.parameter == "default_ttl_s":
                    setattr(cache, "_default_ttl", float(adj.new_value))
                elif adj.parameter == "max_size":
                    new_max = int(adj.new_value)
                    setattr(cache, "_max_size", new_max)
                    # Evict if shrinking
                    while len(getattr(cache, "_store", {})) > new_max:
                        store = getattr(cache, "_store")
                        if store:
                            store.popitem(last=False)
                logger.info(
                    "[cache_evolver] Applied %s=%s to LRU cache '%s'",
                    adj.parameter, adj.new_value, adj.cache_name,
                )
                return True

            elif adj.cache_type == "semantic":
                cache = semantic_caches.get(adj.cache_name)
                if cache is None:
                    return False
                if adj.parameter == "similarity_threshold":
                    setattr(cache, "_threshold", float(adj.new_value))
                elif adj.parameter == "default_ttl_s":
                    setattr(cache, "_default_ttl", float(adj.new_value))
                logger.info(
                    "[cache_evolver] Applied %s=%s to semantic cache '%s'",
                    adj.parameter, adj.new_value, adj.cache_name,
                )
                return True

        except Exception as exc:
            logger.warning(
                "[cache_evolver] Failed to apply %s to '%s': %s",
                adj.parameter, adj.cache_name, exc,
            )
            return False

        return False
