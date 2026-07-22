"""MAOP Capability Matcher — Task-to-Agent matching algorithm.

Scores agents against task requirements using a multi-factor scoring system:
  1. Capability match: agent capabilities ∩ task requirements
  2. Provider preference: user-configured provider weights
  3. Health score: recent health check results
  4. Latency score: historical response time
  5. Load score: current task load (via CircuitBreaker state)

Usage::

    from maop.core.capability_matcher import CapabilityMatcher

    matcher = CapabilityMatcher(registry=registry)
    ranked = matcher.match(
        task="Fix the authentication bug",
        requirements=["code", "edit", "search"],
    )
    best = ranked[0]  # highest-scoring agent
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MatchScore(BaseModel):
    agent_name: str
    total_score: float = 0.0
    capability_score: float = 0.0
    health_score: float = 0.0
    latency_score: float = 0.0
    provider_score: float = 0.0
    matched_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class MatcherConfig(BaseModel):
    weight_capability: float = 0.50
    weight_health: float = 0.20
    weight_latency: float = 0.15
    weight_provider: float = 0.15
    provider_preferences: dict[str, float] = Field(default_factory=dict)
    max_latency_ms: int = 5000
    unhealthy_penalty: float = 0.1


TASK_KEYWORD_MAP: dict[str, list[str]] = {
    "fix": ["code", "edit", "search"],
    "bug": ["code", "edit", "search"],
    "refactor": ["code", "edit"],
    "test": ["code"],
    "write": ["code", "edit"],
    "implement": ["code", "edit"],
    "review": ["code", "search"],
    "search": ["search"],
    "explain": ["chat"],
    "chat": ["chat"],
    "plan": ["chat", "plan"],
    "design": ["chat", "code"],
    "deploy": ["code", "git"],
    "debug": ["code", "edit", "search", "react"],
    "analyze": ["chat", "search"],
    "vision": ["vision"],
    "image": ["vision"],
    "orchestrate": ["orchestrate", "plan"],
}


class CapabilityMatcher:
    """Score and rank agents for task suitability.

    Multi-factor scoring:
      - Capability match (50%): how many required capabilities the agent has
      - Health score (20%): recent health check results
      - Latency score (15%): historical response time
      - Provider preference (15%): user-configured provider weights
    """

    def __init__(
        self,
        registry: Any = None,
        config: MatcherConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or MatcherConfig()

    def infer_requirements(self, task: str) -> list[str]:
        """Infer required capabilities from task description."""
        task_lower = task.lower()
        requirements = set()
        for keyword, caps in TASK_KEYWORD_MAP.items():
            if keyword in task_lower:
                requirements.update(caps)
        if not requirements:
            requirements = {"chat", "code"}
        return sorted(requirements)

    def match(
        self,
        task: str,
        requirements: list[str] | None = None,
        *,
        exclude: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MatchScore]:
        """Score all eligible agents and return ranked results."""
        if requirements is None:
            requirements = self.infer_requirements(task)

        if self._registry is None:
            return []

        agents = self._registry.list_agents(enabled_only=True)
        if exclude:
            agents = [a for a in agents if a.name not in exclude]

        scores = []
        for agent in agents:
            score = self._score_agent(agent, requirements)
            scores.append(score)

        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores[:top_k]

    def _score_agent(self, agent: Any, requirements: list[str]) -> MatchScore:
        agent_caps = set(agent.capabilities) if hasattr(agent, "capabilities") else set()
        req_set = set(requirements)

        matched = agent_caps & req_set
        missing = req_set - agent_caps

        if req_set:
            cap_score = len(matched) / len(req_set)
        else:
            cap_score = 1.0

        health_score = self._health_score(agent)
        latency_score = self._latency_score(agent)
        provider_score = self._provider_score(agent)

        cfg = self._config
        total = (
            cfg.weight_capability * cap_score
            + cfg.weight_health * health_score
            + cfg.weight_latency * latency_score
            + cfg.weight_provider * provider_score
        )

        if health_score < 0.3:
            total *= cfg.unhealthy_penalty

        return MatchScore(
            agent_name=agent.name,
            total_score=round(total, 4),
            capability_score=round(cap_score, 4),
            health_score=round(health_score, 4),
            latency_score=round(latency_score, 4),
            provider_score=round(provider_score, 4),
            matched_capabilities=sorted(matched),
            missing_capabilities=sorted(missing),
        )

    def _health_score(self, agent: Any) -> float:
        health = getattr(agent, "health", "unknown")
        if health == "healthy":
            return 1.0
        if health == "degraded":
            return 0.5
        if health == "unhealthy":
            return 0.0
        return 0.3

    def _latency_score(self, agent: Any) -> float:
        latency = getattr(agent, "last_latency_ms", 0)
        if latency <= 0:
            return 0.5
        if latency < 1000:
            return 1.0
        if latency < 3000:
            return 0.7
        if latency < self._config.max_latency_ms:
            return 0.4
        return 0.1

    def _provider_score(self, agent: Any) -> float:
        provider = getattr(agent, "provider", "")
        pref = self._config.provider_preferences.get(provider)
        if pref is not None:
            return min(1.0, max(0.0, pref))
        return 0.5
