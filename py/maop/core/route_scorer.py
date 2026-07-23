"""MAOP Route Scorer — Multi-factor route matching and agent selection.

Improvements over legacy _route_by_config:
1. Multi-route scoring: instead of first-match-wins, score ALL routes and
   pick the best match. This handles ambiguous tasks that match multiple
   routing keys (e.g. "写个测试" matches both codegen and verify).
2. Weighted scoring: regex match > keyword count > keyword specificity.
3. Agent cooldown: skip agents that recently failed (within cooldown window).
4. Confidence threshold: if best score < threshold, fall through to legacy.

Usage:
    from maop.core.route_scorer import RouteScorer
    scorer = RouteScorer(config)
    result = scorer.match("写个 Python 冒泡排序")
    # result = RouteMatch(routing_key="codegen", agent="mmx", score=0.85, confidence="high")
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from maop.config.loader import MaopConfig, RouteEntry

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

    # ── Public API ───────────────────────────────────────────

    def match(self, task: str, *, adaptive: bool = True) -> RouteMatch | None:
        """Match task against all routes and return the best match.

        Args:
            task: Task description text.
            adaptive: If True, use performance data for agent selection.

        Returns:
            RouteMatch with the best score, or None if no route matches.
        """
        if self.config is None:
            return None

        task_lower = task.lower()
        candidates: list[tuple[str, RouteEntry, float, str]] = []

        for rk, route in self.config.routing.items():
            score, matched_by = self._score_route(task_lower, rk, route)
            if score > 0:
                candidates.append((rk, route, score, matched_by))

        if not candidates:
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

        logger.debug(
            "Route match: rk=%s agent=%s score=%.2f confidence=%s matched_by=%s "
            "(candidates=%d)",
            best_rk, agent, best_score, confidence, best_matched_by,
            len(candidates),
        )

        return RouteMatch(
            routing_key=best_rk,
            agent=agent,
            score=round(best_score, 4),
            confidence=confidence,
            matched_by=best_matched_by,
        )

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
                    "remaining_sec": round(remaining),
                })
        return result

    # ── Internal: Route Scoring ──────────────────────────────

    def _score_route(
        self, task_lower: str, routing_key: str, route: RouteEntry
    ) -> tuple[float, str]:
        """Score a single route against the task.

        Returns (score, matched_by) where score is in [0, 1] and
        matched_by describes what triggered the match.

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
                    if pos < best_position:
                        best_position = pos

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
        if agent and hasattr(agent, "capabilities"):
            if routing_key in agent.capabilities:
                return _CAPABILITY_BONUS
        return 0.0

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

        # Try adaptive selection first (performance-based)
        if adaptive and len(candidates) > 1:
            try:
                from maop.core.agent_performance import AgentPerformanceTracker
                import os
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


# ── Singleton Instance ───────────────────────────────────────

_instance: RouteScorer | None = None


def get_route_scorer(config: MaopConfig | None = None) -> RouteScorer:
    """Get or create the singleton RouteScorer instance."""
    global _instance
    if _instance is None:
        _instance = RouteScorer(config=config)
    elif config is not None:
        _instance.config = config
    return _instance
