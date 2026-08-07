"""MAOP Route Scorer — Multi-factor route matching and agent selection.

Improvements over legacy _route_by_config:
1. Multi-route scoring: instead of first-match-wins, score ALL routes and
   pick the best match. This handles ambiguous tasks that match multiple
   routing keys (e.g. "写个测试" matches both codegen and verify).
2. Weighted scoring: regex match > keyword count > keyword specificity.
3. Agent cooldown: skip agents that recently failed (within cooldown window).
4. Confidence threshold: if best score < threshold, fall through to legacy.

Usage:
    from maop.core.routing.route_scorer import RouteScorer
    scorer = RouteScorer(config)
    result = scorer.match("写个 Python 冒泡排序")
    # result = RouteMatch(routing_key="codegen", agent="mmx", score=0.85, confidence="high")
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from maop.config.loader import MaopConfig, RouteEntry
from maop.core.routing.multi_objective_scorer import ObjectiveWeights  # R8-F821 fix
from maop.core.monitoring.otel import get_tracer
from maop.core.monitoring.otel import span as otel_span
from maop.core.routing.routing_decision import (
    RoutingDecisionRecord,
    get_active_span_context,
    record_decision_safe,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
_REGEX_WEIGHT = 0.50      # regex match contributes up to 50%
_KEYWORD_BASE = 0.30      # keyword match contributes up to 30%
_KEYWORD_BONUS = 0.05     # each additional keyword adds 5%
_CAPABILITY_BONUS = 0.15  # agent capability alignment adds up to 15%
_CONFIDENCE_HIGH = 0.60   # above this = high confidence
_CONFIDENCE_MEDIUM = 0.35 # above this = medium confidence
_COOLDOWN_SEC = 300       # 5 minutes cooldown for failed agents
_MAX_COOLDOWN_ENTRIES = 200

# Score cache for RouteScorer._score_route (a pure function of task text,
# route definition, and config version). Bounded by _SCORE_CACHE_MAX with
# simple clear-on-full eviction. Stale entries from old config versions are
# naturally ignored because the config _version is part of the cache key.
_score_cache: dict[tuple, tuple[float, str]] = {}
_SCORE_CACHE_MAX = 256


# ── Data Classes ─────────────────────────────────────────────

@dataclass
class RouteMatch:
    """Result of route matching."""
    routing_key: str
    agent: str
    score: float
    confidence: str  # "high" | "medium" | "low"
    matched_by: str  # "regex" | "keywords" | "capability" | "default"

    def __bool__(self) -> bool:
        return self.score > 0


@dataclass
class _AgentCooldown:
    """Track agent failure cooldown."""
    agent: str
    last_fail_ts: float
    fail_count: int = 1


# ── Route Scorer ─────────────────────────────────────────────

class RouteScorer:
    """Score all routes against a task and select the best match.

    Scoring formula per route:
        route_score = regex_score * 0.50 + keyword_score * 0.30 + capability_score * 0.15 + specificity_bonus

    Where:
        - regex_score: 1.0 if regex matches, 0.0 otherwise
        - keyword_score: min(1.0, matched_keywords / 3) — more matched keywords = higher score
        - capability_score: 0.15 if primary agent has the routing key in its capabilities
        - specificity_bonus: 0.05 per matched keyword beyond the first (max 0.15)
    """

    def __init__(self, config: MaopConfig | None = None) -> None:
        self.config = config
        self._cooldowns: dict[str, _AgentCooldown] = {}
        # Phase γ-3: multi-objective (Pareto + TOPSIS) agent selection.
        # Off by default for full backward compatibility; flip via
        # ``enable_multi_objective()``.
        self._use_multi_objective: bool = False
        self._mo_weights: ObjectiveWeights | None = None

    # ── Public API ───────────────────────────────────────────

    def enable_multi_objective(
        self, weights: ObjectiveWeights | None = None
    ) -> None:
        """Switch agent selection to the Pareto + TOPSIS scorer.

        Parameters
        ----------
        weights : ObjectiveWeights | None
            Optional custom weights. When None, the scorer's defaults
            (success_rate=0.4, latency=0.3, cost=0.2, quota_headroom=0.1)
            are used. The strategy learner may pass tuned weights here.
        """
        self._use_multi_objective = True
        self._mo_weights = weights
        logger.info(
            "RouteScorer multi-objective mode ENABLED (weights=%s)",
            weights,
        )

    def disable_multi_objective(self) -> None:
        """Revert to the legacy weighted-sum agent selection."""
        self._use_multi_objective = False
        self._mo_weights = None
        logger.info("RouteScorer multi-objective mode DISABLED")

    @property
    def is_multi_objective_enabled(self) -> bool:
        return self._use_multi_objective

    def match(self, task: str, *, adaptive: bool = True, trace_id: str = "") -> RouteMatch | None:
        """Match task against all routes and return the best match.

        Args:
            task: Task description text.
            adaptive: If True, use performance data for agent selection.
            trace_id: Optional MAOP trace id for OTel/span correlation.

        Returns:
            RouteMatch with the best score, or None if no route matches.
        """
        if self.config is None:
            return None

        # Phase γ-4: wrap the full match in an OTel span and persist a
        # RoutingDecisionRecord so the dashboard can explain "why was
        # agent X picked for trace Z". Best-effort: span/record failures
        # never break routing.
        tracer = get_tracer("maop.routing.route_scorer")
        _start = time.monotonic()
        decision_mode = "multi_objective" if self._use_multi_objective else "weighted_sum"
        with otel_span(
            tracer, "routing.route_scorer.match", trace_id=trace_id,
            attributes={
                "routing.task_preview": task[:80],
                "routing.adaptive": 1 if adaptive else 0,
                "routing.decision_mode": decision_mode,
            },
        ) as _span:
            task_lower = task.lower()
            candidates: list[tuple[str, RouteEntry, float, str]] = []

            for rk, route in self.config.routing.items():
                score, matched_by = self._score_route(task_lower, rk, route)
                if score > 0:
                    candidates.append((rk, route, score, matched_by))

            if not candidates:
                _set_span_attr(_span, "routing.candidate_count", 0)
                _record_route_scorer_decision(
                    trace_id=trace_id, task=task, result=None,
                    candidate_count=0, decision_mode=decision_mode,
                    duration_ms=(time.monotonic() - _start) * 1000.0,
                )
                return None

            # Sort by score descending
            candidates.sort(key=lambda x: x[2], reverse=True)
            best_rk, best_route, best_score, best_matched_by = candidates[0]

            # Determine confidence level
            if best_score >= _CONFIDENCE_HIGH:
                confidence = "high"
            elif best_score >= _CONFIDENCE_MEDIUM:
                confidence = "medium"
            else:
                confidence = "low"

            # Select agent (adaptive or primary), with cooldown check
            agent = self._select_agent(best_route, best_rk, adaptive)

            # Record the routing-decision mode for observability (Phase γ-3).
            try:
                from maop.core.monitoring.monitoring import MAOP_ROUTE_DECISION_MODE
                MAOP_ROUTE_DECISION_MODE.set(
                    1.0 if self._use_multi_objective else 0.0,
                    labels={"mode": "multi_objective" if self._use_multi_objective else "weighted_sum"},
                )
            except Exception as e:
                logger.debug("ignored: %s", e, exc_info=True)

            logger.debug(
                "Route match: rk=%s agent=%s score=%.2f confidence=%s matched_by=%s "
                "(candidates=%d)",
                best_rk, agent, best_score, confidence, best_matched_by,
                len(candidates),
            )

            result = RouteMatch(
                routing_key=best_rk,
                agent=agent,
                score=round(best_score, 4),
                confidence=confidence,
                matched_by=best_matched_by,
            )

            # Phase γ-4: set span attributes + persist decision record.
            _set_span_attr(_span, "routing.routing_key", best_rk)
            _set_span_attr(_span, "routing.candidate_count", len(candidates))
            _set_span_attr(_span, "routing.selected_agent", agent)
            _set_span_attr(_span, "routing.score", best_score)
            _set_span_attr(_span, "routing.confidence", confidence)
            _set_span_attr(_span, "routing.matched_by", best_matched_by)
            _set_span_attr(_span, "routing.decision_mode", decision_mode)
            _record_route_scorer_decision(
                trace_id=trace_id, task=task, result=result,
                candidate_count=len(candidates), decision_mode=decision_mode,
                duration_ms=(time.monotonic() - _start) * 1000.0,
            )
            return result

    def mark_agent_failed(self, agent: str) -> None:
        """Record an agent failure for cooldown tracking."""
        now = time.time()
        if agent in self._cooldowns:
            self._cooldowns[agent].fail_count += 1
            self._cooldowns[agent].last_fail_ts = now
        else:
            self._cooldowns[agent] = _AgentCooldown(
                agent=agent, last_fail_ts=now, fail_count=1
            )

        # Prune old entries
        if len(self._cooldowns) > _MAX_COOLDOWN_ENTRIES:
            cutoff = now - _COOLDOWN_SEC * 4
            self._cooldowns = {
                a: c for a, c in self._cooldowns.items()
                if c.last_fail_ts > cutoff
            }

    def mark_agent_success(self, agent: str) -> None:
        """Clear cooldown for an agent after successful execution."""
        self._cooldowns.pop(agent, None)

    def is_agent_in_cooldown(self, agent: str) -> bool:
        """Check if an agent is currently in cooldown (recently failed).

        Multiple failures extend cooldown: fail_count=1 -> 1x, 2 -> 1.5x,
        3 -> 2x, 4+ -> 2.5x of _COOLDOWN_SEC.
        """
        cd = self._cooldowns.get(agent)
        if cd is None:
            return False
        elapsed = time.time() - cd.last_fail_ts
        # Compute extended cooldown FIRST, then check (B-P0-1 fix: was checked
        # against base _COOLDOWN_SEC, making extension logic unreachable)
        extended = _COOLDOWN_SEC * (1 + min(cd.fail_count - 1, 3) * 0.5)
        if elapsed > extended:
            # Cooldown (with extension) expired
            del self._cooldowns[agent]
            return False
        return True

    def get_cooldown_status(self) -> list[dict[str, Any]]:
        """Get current cooldown status for all agents (for dashboard)."""
        now = time.time()
        result = []
        for agent, cd in self._cooldowns.items():
            elapsed = now - cd.last_fail_ts
            extended = _COOLDOWN_SEC * (1 + min(cd.fail_count - 1, 3) * 0.5)
            remaining = max(0, extended - elapsed)
            if remaining > 0:
                result.append({
                    "agent": agent,
                    "fail_count": cd.fail_count,
                    "remaining_s": round(remaining),
                })
        return result

    # ── Internal: Route Scoring ──────────────────────────────

    # ── Multi-Factor Scoring Formula ──────────────────────────────
    #
    # Score = w_regex * S_regex + w_keyword * S_keyword
    #       + w_capability * S_capability + w_position * S_position
    #
    # Weights (module constants):
    #   w_regex      = _REGEX_WEIGHT     = 0.50  (highest: exact pattern match is strongest signal)
    #   w_keyword    = _KEYWORD_BASE     = 0.30  (keyword overlap indicates topical relevance)
    #   w_capability = _CAPABILITY_BONUS = 0.15  (declared capability alignment bonus)
    #   w_position   = 0.10  (keyword position bonus - earlier match = higher score)
    #
    # Sub-scores are normalized to [0, 1]:
    #   S_regex      = 1.0 if full match (re.search), 0.0 if no match
    #   S_keyword    = min(1.0, matched_count / 3) * _KEYWORD_BASE
    #   S_capability = _CAPABILITY_BONUS (0.15) if primary agent has routing key in capabilities
    #   S_position   = max(0.0, (1.0 - best_position / task_len)) * 0.10
    #
    # Specificity bonus: each additional matched keyword beyond the first adds
    #   _KEYWORD_BONUS (0.05), capped at 0.15.
    #
    # Cooldown mechanism (_COOLDOWN_SEC = 300s):
    #   Agents that recently failed are SKIPPED in _select_agent (not score-
    #   penalized). Fallback -> tertiary agents are tried; if all candidates are
    #   in cooldown, the primary agent is used anyway (better than nothing).
    #   Repeated failures extend cooldown LINEARLY (not exponentially):
    #     cooldown = _COOLDOWN_SEC * (1 + min(fail_count - 1, 3) * 0.5)
    #   i.e. fail_count 1->1.0x, 2->1.5x, 3->2.0x, 4+->2.5x (capped at 2.5x = 750s).
    #
    # Tie-breaking: candidates sorted by score descending; higher score wins.
    # Confidence: >= _CONFIDENCE_HIGH (0.60) = high, >= _CONFIDENCE_MEDIUM (0.35) = medium.

    def _score_route(
        self, task_lower: str, routing_key: str, route: RouteEntry
    ) -> tuple[float, str]:
        """Score a single route against the task, with caching.

        Returns (score, matched_by) where score is in [0, 1] and
        matched_by describes what triggered the match.

        Cached keyed by (task_lower, routing_key, route fields, config version).
        The config _version in the key invalidates entries automatically on
        reload; size is bounded by _SCORE_CACHE_MAX (clear-on-full).
        """
        config_version = getattr(self.config, "_version", 0) if self.config else 0
        cache_key = (
            task_lower, routing_key, route.match,
            tuple(route.keywords), route.primary, config_version,
        )
        cached = _score_cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._compute_score(task_lower, routing_key, route)
        if len(_score_cache) >= _SCORE_CACHE_MAX:
            _score_cache.clear()
        _score_cache[cache_key] = result
        return result

    def _compute_score(
        self, task_lower: str, routing_key: str, route: RouteEntry
    ) -> tuple[float, str]:
        """Compute the route score (uncached implementation).

        Scoring factors (in priority order):
        1. Regex match: 50% weight (highest specificity)
        2. Keyword count: up to 30% weight (more matches = higher score)
        3. Keyword position: earlier match = higher score (up to 10% bonus)
        4. Keyword specificity: each additional match adds 5% (max 15%)
        5. Capability alignment: 15% bonus if primary agent has this capability
        """
        score = 0.0
        matched_by = ""
        best_position = len(task_lower)  # track earliest keyword position

        # Step 1: regex match (highest weight)
        if route.match:
            try:
                m = re.search(route.match, task_lower)
                if m:
                    score += _REGEX_WEIGHT
                    matched_by = "regex"
                    best_position = m.start()
            except re.error:
                logger.warning(
                    "Invalid regex in routing.%s.match: %r",
                    routing_key, route.match,
                )

        # Step 2: keyword matching (weighted by count + position)
        if route.keywords:
            matched_count = 0
            for kw in route.keywords:
                kw_lower = kw.lower()
                pos = task_lower.find(kw_lower)
                if pos >= 0:
                    matched_count += 1
                    best_position = min(best_position, pos)

            if matched_count > 0:
                keyword_score = min(1.0, matched_count / 3) * _KEYWORD_BASE
                # Specificity bonus: each additional keyword adds 5%
                specificity = min(0.15, (matched_count - 1) * _KEYWORD_BONUS)
                # Position bonus: keyword at position 0 gets full 10%,
                # decreasing linearly to 0 at end of task
                task_len = max(len(task_lower), 1)
                position_bonus = max(0.0, (1.0 - best_position / task_len)) * 0.10
                score += keyword_score + specificity + position_bonus
                if not matched_by:
                    matched_by = f"keywords({matched_count})"

        # Step 3: capability alignment bonus
        if score > 0:
            cap_bonus = self._capability_bonus(route, routing_key)
            score += cap_bonus

        return min(score, 1.0), matched_by or ""

    def _capability_bonus(self, route: RouteEntry, routing_key: str) -> float:
        """Check if the primary agent has this routing key in its capabilities."""
        if not self.config or not route.primary:
            return 0.0
        agent = self.config.agents.get(route.primary)
        if agent and hasattr(agent, "capabilities") and routing_key in agent.capabilities:
            return _CAPABILITY_BONUS
        return 0.0

    # ── Internal: Multi-Objective Agent Selection (Phase γ-3) ─

    def _compute_score_multi_objective(
        self,
        candidates: list[str],
        routing_key: str,
        default: str = "",
    ) -> str:
        """Pick the best agent via Pareto + TOPSIS over live AgentStats.

        Pulls ``AgentStats`` from the global ``AgentPerformanceTracker``
        for each candidate, builds an :class:`AgentObjectiveVector` for
        each, asks :class:`MultiObjectiveScorer` to rank them, and returns
        the top agent name.  On any data-missing / empty-frontier
        condition, falls back to ``default`` so the caller (which wraps us
        in a try/except) can degrade gracefully.

        ``quota_headroom`` is derived from the agent's ``AgentStats``
        success/failure ratio as a proxy when no dedicated quota tracker
        is available — a higher recent success rate is treated as more
        "headroom" because the agent is clearly not being throttled.
        Callers with real quota data may construct vectors directly via
        :class:`MultiObjectiveScorer` instead of going through this helper.
        """
        if not candidates:
            return default

        # Lazy imports keep the module import-cost low when the multi-
        # objective path is never enabled.
        import os

        from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
        from maop.core.routing.multi_objective_scorer import (
            AgentObjectiveVector,
            MultiObjectiveScorer,
            ObjectiveWeights,
        )
        root = os.environ.get("MAOP_ROOT_DIR", ".")
        tracker = AgentPerformanceTracker(root_dir=root)

        vectors: dict[str, AgentObjectiveVector] = {}
        for agent in candidates:
            stats = tracker.get_agent_stats(agent, routing_key=routing_key)
            # quota_headroom proxy: success_rate itself.  Agents with no
            # history (total_tasks == 0) get a neutral 0.5 to give new
            # agents a fair chance without dominating established ones.
            if stats.total_tasks == 0:
                quota_headroom = 0.5
            else:
                # Blend: 70% success_rate (recent health signal) +
                # 30% inverse load (more headroom = fewer failures lately).
                quota_headroom = max(
                    0.0,
                    min(
                        1.0,
                        0.7 * stats.success_rate
                        + 0.3 * (1.0 - min(stats.failure_count / max(stats.total_tasks, 1), 1.0)),
                    ),
                )
            vectors[agent] = AgentObjectiveVector(
                success_rate=stats.success_rate,
                latency_ms=stats.avg_latency_ms,
                cost_usd=stats.avg_cost_usd,
                quota_headroom=quota_headroom,
            )

        weights = self._mo_weights if self._mo_weights is not None else ObjectiveWeights()
        scorer = MultiObjectiveScorer(weights=weights)
        ranking = scorer.rank_agents(vectors, weights=weights)

        # Record metrics for observability.
        try:
            from maop.core.monitoring.monitoring import (
                MAOP_ROUTE_MULTI_OBJECTIVE_SCORE,
                MAOP_ROUTE_PARETO_FRONTIER_SIZE,
            )
            frontier_size = scorer.compute_pareto_frontier(vectors).frontier_size
            MAOP_ROUTE_PARETO_FRONTIER_SIZE.set(float(frontier_size))
            for _name, score in ranking:
                MAOP_ROUTE_MULTI_OBJECTIVE_SCORE.observe(score)
        except Exception:
            # Metrics are best-effort; never fail routing because of them.
            pass

        if not ranking:
            return default
        return ranking[0][0]

    # ── Internal: Agent Selection ────────────────────────────

    def _select_agent(
        self, route: RouteEntry, routing_key: str, adaptive: bool
    ) -> str:
        """Select the best agent from route candidates.

        Priority:
        1. Primary agent (if not in cooldown)
        2. Fallback agent (if primary in cooldown)
        3. Tertiary agent (if fallback also in cooldown)
        4. Primary anyway (if all in cooldown — better than nothing)
        """
        candidates = [
            a for a in [route.primary, route.fallback, route.tertiary] if a
        ]
        if not candidates:
            return "claude"

        # Phase γ-3: multi-objective (Pareto + TOPSIS) path.  Takes
        # precedence over the legacy weighted-sum adaptive path when
        # explicitly enabled.  Falls through to the legacy path on any
        # exception so a transient stats-DB hiccup never breaks routing.
        if adaptive and self._use_multi_objective and len(candidates) > 1:
            try:
                best = self._compute_score_multi_objective(
                    candidates, routing_key, default=route.primary,
                )
                if best and not self.is_agent_in_cooldown(best):
                    if best != route.primary:
                        logger.info(
                            "Multi-objective routing: rk=%s primary=%s → %s "
                            "(Pareto+TOPSIS)",
                            routing_key, route.primary, best,
                        )
                    return best
            except Exception as exc:
                logger.debug(
                    "Multi-objective routing fallback to adaptive: %s", exc
                )

        # Try adaptive selection first (performance-based)
        if adaptive and len(candidates) > 1:
            try:
                import os

                from maop.core.agent.lifecycle.agent_performance import AgentPerformanceTracker
                root = os.environ.get("MAOP_ROOT_DIR", ".")
                tracker = AgentPerformanceTracker(root_dir=root)
                best = tracker.best_agent(
                    agents=candidates, routing_key=routing_key,
                    default=route.primary,
                )
                if not self.is_agent_in_cooldown(best):
                    if best != route.primary:
                        logger.info(
                            "Adaptive routing: rk=%s primary=%s → %s "
                            "(performance-based)",
                            routing_key, route.primary, best,
                        )
                    return best
            except Exception as exc:
                logger.debug("Adaptive routing fallback: %s", exc)

        # P1 fix: validate driver and warn on unknown
        _KNOWN_DRIVERS = {"cli", "wrapper", "powershell", "cmd", "python"}
        if self.config:
            for agent_name in candidates:
                adef = self.config.agents.get(agent_name) if hasattr(self.config, 'agents') else None
                if adef:
                    drv = getattr(adef, 'driver', '') or ''
                    if drv and drv not in _KNOWN_DRIVERS:
                        logger.warning(
                            "Agent '%s' has unknown driver '%s' (known: %s)",
                            agent_name, drv, ", ".join(sorted(_KNOWN_DRIVERS)),
                        )

        # Cooldown-aware selection
        for agent in candidates:
            if not self.is_agent_in_cooldown(agent):
                return agent

        # All in cooldown — return primary
        logger.warning(
            "All candidates for rk=%s are in cooldown, using primary=%s",
            routing_key, route.primary,
        )
        return route.primary

    @classmethod
    def reset(cls):
        """Reset singleton instance (for testing)."""
        global _instance
        _instance = None


# ── Singleton Instance ───────────────────────────────────────

_singleton_lock = threading.Lock()
_instance: RouteScorer | None = None


def get_route_scorer(config: MaopConfig | None = None) -> RouteScorer:
    """Get or create the singleton RouteScorer instance.

    P1-17 fix: thread-safe singleton creation with Lock.
    Config is only accepted on first initialization; subsequent calls
    ignore config to prevent race conditions from concurrent requests
    mutating the shared singleton.

    P2-1 fix: hot-reload support. When a config with a newer ``_version``
    is passed (e.g. after ``MaopConfig.reload()``), the singleton is
    reinitialized so routing changes take effect without a process restart.
    The cooldown state is preserved across the reload to avoid resetting
    agent failure tracking mid-flight.
    """
    global _instance
    if _instance is None:
        with _singleton_lock:
            if _instance is None:
                _instance = RouteScorer(config=config)
                return _instance

    # Hot-reload: reinitialize if the passed config is different
    # 1. Identity check: different config object (test isolation / hot-reload)
    # 2. Version check: same object but version changed (in-place reload)
    if config is not None and _instance.config is not None:
        new_ver = getattr(config, "_version", 0)
        cur_ver = getattr(_instance.config, "_version", 0)
        config_changed = (config is not _instance.config) or (new_ver and new_ver != cur_ver)
        if config_changed:
            with _singleton_lock:
                # Re-check inside the lock to avoid duplicate reinit
                cur_ver = getattr(_instance.config, "_version", 0)
                if new_ver != cur_ver:
                    logger.info(
                        "RouteScorer config reload: version %s -> %s",
                        cur_ver, new_ver,
                    )
                    # Preserve cooldown state across reload
                    old_cooldowns = _instance._cooldowns
                    _instance = RouteScorer(config=config)
                    _instance._cooldowns = old_cooldowns
    return _instance


# ── Phase γ-4: span / decision-record helpers ─────────────────


def _set_span_attr(s: Any, key: str, value: Any) -> None:
    """Best-effort ``set_attribute`` on a (possibly no-op) span."""
    with contextlib.suppress(Exception):
        s.set_attribute(key, value)


def _record_route_scorer_decision(
    *,
    trace_id: str,
    task: str,
    result: RouteMatch | None,
    candidate_count: int,
    decision_mode: str,
    duration_ms: float,
) -> None:
    """Persist a :class:`RoutingDecisionRecord` for ``route_scorer.match``.

    Best-effort: any failure is logged at debug level inside
    :func:`record_decision_safe` and never propagates. When ``trace_id``
    is empty we try to inherit it from the active OTel span context so
    decisions made inside a parent dispatcher span still correlate.
    """
    otel_trace_id, span_id, parent_span_id = get_active_span_context()
    effective_trace = trace_id or otel_trace_id
    if result is not None:
        output_summary = {
            "selected_agent": result.agent,
            "routing_key": result.routing_key,
            "score": result.score,
            "confidence": result.confidence,
            "matched_by": result.matched_by,
        }
        explanation = (
            f"Selected agent '{result.agent}' with confidence {result.score} "
            f"({result.confidence}) using {decision_mode} mode. "
            f"Matched on {result.matched_by}. {candidate_count} candidates evaluated."
        )
    else:
        output_summary = {"selected_agent": None}
        explanation = (
            f"No route matched (0 candidates) using {decision_mode} mode. "
            f"Task: '{task[:60]}'."
        )

    try:
        from maop.core.monitoring.monitoring import (
            MAOP_ROUTING_DECISION_DURATION_MS,
            MAOP_ROUTING_DECISION_TOTAL,
        )
        MAOP_ROUTING_DECISION_TOTAL.inc(labels={"stage": "route_scorer"})
        MAOP_ROUTING_DECISION_DURATION_MS.observe(duration_ms)
    except Exception as e:
        logger.debug("ignored: %s", e, exc_info=True)

    record_decision_safe(RoutingDecisionRecord(
        trace_id=effective_trace,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=time.time(),
        stage="route_scorer",
        input_summary={
            "task_preview": task[:80],
            "candidate_count": candidate_count,
            "decision_mode": decision_mode,
        },
        output_summary=output_summary,
        explanation=explanation,
        duration_ms=duration_ms,
        attributes={
            "routing_key": result.routing_key if result else "",
            "selected_agent": result.agent if result else "",
            "score": result.score if result else 0.0,
            "confidence": result.confidence if result else "",
            "decision_mode": decision_mode,
        },
    ))
