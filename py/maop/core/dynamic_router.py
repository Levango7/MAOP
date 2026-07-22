"""MAOP Dynamic Router — Score agents by health + delegation history.

Python port of archive/ps-legacy/dynamic-router.ps1.

Reads health data, delegation history, and config/agents.yaml routing table.
Calculates total_score for each agent per routing key as:
    total_score = success_rate * 0.6 + speed_score * 0.4

Caches results for 30 seconds. Use refresh=True to bypass cache.

Usage:
    from maop.core.dynamic_router import DynamicRouter
    router = DynamicRouter(project_root="/path/to/MAOP")
    scores = router.route()  # dict[routing_key, list[AgentScore]]
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
_CACHE_TTL_SEC = 30
_SPEED_NORMALIZATION_MS = 30_000
_DEFAULT_SUCCESS_RATE = 0.5
_DEFAULT_SPEED_SCORE = 0.5
_DEAD_AGENT_SCORE = 0.05
_RECENT_DELEGATION_LIMIT = 100


class AgentScore(BaseModel):
    """Scored agent for a routing key."""
    agent: str
    score: float
    success_rate: float
    speed: float


class DynamicRouter:
    """Dynamic agent router that scores agents by health and performance.

    Scoring formula:
        total_score = success_rate * 0.6 + speed_score * 0.4

    Speed score blends delegation speed (70%) and health-check speed (30%)
    when both are available.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        cache_ttl: int = _CACHE_TTL_SEC,
    ) -> None:
        self.root = Path(project_root)
        self.data_dir = self.root / "data"
        self.cache_file = self.data_dir / "dynamic-routing-cache.json"
        self.cache_ttl = cache_ttl
        self._cache: dict[str, Any] | None = None
        self._cache_ts: float = 0.0

    # ── Public API ───────────────────────────────────────────

    def route(self, refresh: bool = False) -> dict[str, list[AgentScore]]:
        """Get scored agents per routing key.

        Args:
            refresh: Bypass cache and re-read all inputs.

        Returns:
            Dict mapping routing_key -> list[AgentScore] sorted by score desc.
        """
        if not refresh and self._cache_is_fresh():
            return self._load_cached()

        result = self._compute_scores()
        self._save_cache(result)
        return result

    def route_for_key(self, routing_key: str, refresh: bool = False) -> list[AgentScore]:
        """Get scored agents for a single routing key."""
        all_scores = self.route(refresh=refresh)
        return all_scores.get(routing_key, [])

    def best_agent(self, routing_key: str, refresh: bool = False) -> str | None:
        """Get the highest-scoring agent for a routing key."""
        scores = self.route_for_key(routing_key, refresh=refresh)
        return scores[0].agent if scores else None

    # ── Cache ────────────────────────────────────────────────

    def _cache_is_fresh(self) -> bool:
        """Check if in-memory or file cache is still valid."""
        # In-memory cache
        if self._cache is not None and (time.time() - self._cache_ts) < self.cache_ttl:
            return True
        # File cache
        if self.cache_file.exists():
            age = time.time() - self.cache_file.stat().st_mtime
            if age < self.cache_ttl:
                return True
        return False

    def _load_cached(self) -> dict[str, list[AgentScore]]:
        """Load scores from cache (in-memory or file)."""
        if self._cache is not None and (time.time() - self._cache_ts) < self.cache_ttl:
            return self._deserialize(self._cache)

        if self.cache_file.exists():
            try:
                raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self._cache = raw
                self._cache_ts = time.time()
                return self._deserialize(raw)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load cache: %s", e)

        # Fallback: compute fresh
        return self._compute_scores()

    def _save_cache(self, result: dict[str, list[AgentScore]]) -> None:
        """Save scores to in-memory and file cache."""
        serialized = {k: [s.model_dump() for s in v] for k, v in result.items()}
        self._cache = serialized
        self._cache_ts = time.time()

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(serialized, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to write cache file: %s", e)

    @staticmethod
    def _deserialize(raw: dict[str, Any]) -> dict[str, list[AgentScore]]:
        """Convert raw dict to AgentScore lists."""
        result: dict[str, list[AgentScore]] = {}
        for key, agents in raw.items():
            if isinstance(agents, list):
                result[key] = [
                    AgentScore(**a) if isinstance(a, dict) else a
                    for a in agents
                ]
        return result

    # ── Input readers ────────────────────────────────────────

    def _read_health(self) -> dict[str, dict[str, Any]]:
        """Read agent health from healthcheck_latest.json.

        Returns:
            Dict mapping agent -> {alive: bool, ms: int}
        """
        # Primary: data/ directory (canonical location)
        health_file = self.data_dir / "healthcheck_latest.json"
        health_map: dict[str, dict[str, Any]] = {}

        if not health_file.exists():
            # Fallback: legacy src/logs/ location (pre-migration)
            health_file = self.root / "src" / "logs" / "healthcheck_latest.json"
            if not health_file.exists():
                logger.debug("Health file not found")
                return health_map

        try:
            raw = health_file.read_text(encoding="utf-8").strip()
            if not raw:
                return health_map
            data = json.loads(raw)
            if not isinstance(data, list):
                data = [data]
            for h in data:
                agent = h.get("agent", "")
                if agent:
                    health_map[agent] = {
                        "alive": h.get("status") == "alive",
                        "ms": int(h.get("ms", 0)),
                    }
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse health file: %s", e)

        return health_map

    def _read_delegations(self) -> dict[str, dict[str, float]]:
        """Read delegation history and compute per-agent stats.

        Returns:
            Dict mapping agent -> {success_rate: float, avg_duration_ms: float}
        """
        deleg_file = self.root / "logs" / "delegations.json"
        stats: dict[str, dict[str, float]] = {}

        if not deleg_file.exists():
            # Also try data/ directory (SQLite is primary, JSON may be mirror)
            deleg_file = self.data_dir / "delegations.json"
            if not deleg_file.exists():
                logger.debug("Delegations file not found")
                return stats

        try:
            raw = deleg_file.read_text(encoding="utf-8").strip()
            if not raw:
                return stats
            data = json.loads(raw)
            if not isinstance(data, list):
                data = [data]

            recent = data[-_RECENT_DELEGATION_LIMIT:]

            # Group by agent
            groups: dict[str, list[dict]] = {}
            for entry in recent:
                agent = entry.get("agent", "")
                if agent:
                    groups.setdefault(agent, []).append(entry)

            for agent, entries in groups.items():
                total = len(entries)
                success_count = 0
                total_duration = 0.0
                duration_count = 0

                for entry in entries:
                    result = entry.get("result", {})
                    exit_code = result.get("exit_code")
                    duration_ms = result.get("duration_ms")

                    if exit_code == 0:
                        success_count += 1

                    if duration_ms is not None and duration_ms > 0:
                        total_duration += duration_ms
                        duration_count += 1

                success_rate = round(success_count / total, 4) if total > 0 else _DEFAULT_SUCCESS_RATE
                avg_duration = round(total_duration / duration_count) if duration_count > 0 else 0.0

                stats[agent] = {
                    "success_rate": success_rate,
                    "avg_duration_ms": avg_duration,
                }
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse delegations file: %s", e)

        return stats

    def _read_routing_table(self) -> dict[str, list[str]]:
        """Read routing table from config/agents.yaml.

        Returns:
            Dict mapping routing_key -> list of agent names (primary, fallback, tertiary).
        """
        config_file = self.root / "config" / "agents.yaml"
        routing: dict[str, list[str]] = {}

        if not config_file.exists():
            logger.warning("agents.yaml not found at %s", config_file)
            return routing

        try:
            cfg = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            routing_cfg = cfg.get("routing", {}) if cfg else {}

            for rk, entry in routing_cfg.items():
                agents: list[str] = []
                if not isinstance(entry, dict):
                    continue
                for key in ("primary", "fallback", "tertiary"):
                    val = entry.get(key)
                    if val and val not in agents:
                        agents.append(val)
                routing[rk] = agents
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to parse agents.yaml: %s", e)

        return routing

    # ── Scoring ──────────────────────────────────────────────

    def _compute_scores(self) -> dict[str, list[AgentScore]]:
        """Compute agent scores for all routing keys."""
        health_map = self._read_health()
        deleg_stats = self._read_delegations()
        routing = self._read_routing_table()

        result: dict[str, list[AgentScore]] = {}

        for rk, agents in routing.items():
            scored: list[AgentScore] = []

            for agent in agents:
                success_rate, speed_score = self._score_agent(
                    agent, health_map, deleg_stats
                )
                total_score = round(
                    success_rate * 0.6 + speed_score * 0.4, 4
                )

                scored.append(AgentScore(
                    agent=agent,
                    score=total_score,
                    success_rate=round(success_rate, 4),
                    speed=round(speed_score, 4),
                ))

            scored.sort(key=lambda s: s.score, reverse=True)
            result[rk] = scored

        return result

    @staticmethod
    def _score_agent(
        agent: str,
        health_map: dict[str, dict[str, Any]],
        deleg_stats: dict[str, dict[str, float]],
    ) -> tuple[float, float]:
        """Calculate (success_rate, speed_score) for a single agent.

        Returns:
            Tuple of (success_rate, speed_score), each in [0, 1].
        """
        success_rate = _DEFAULT_SUCCESS_RATE
        speed_score = _DEFAULT_SPEED_SCORE

        # Health check data
        health = health_map.get(agent)
        if health:
            if not health["alive"]:
                success_rate = _DEAD_AGENT_SCORE
                speed_score = _DEAD_AGENT_SCORE
            else:
                health_ms = health["ms"]
                if health_ms > 0:
                    speed_score = max(0.0, min(1.0, 1.0 - health_ms / _SPEED_NORMALIZATION_MS))

        # Delegation history (overrides health-based success rate)
        stats = deleg_stats.get(agent)
        if stats:
            success_rate = stats["success_rate"]

            avg_ms = stats.get("avg_duration_ms", 0)
            if avg_ms and avg_ms > 0:
                deleg_speed = max(0.0, min(1.0, 1.0 - avg_ms / _SPEED_NORMALIZATION_MS))

                # Blend delegation speed (70%) with health speed (30%) when both available
                h = health_map.get(agent)
                if h and h["alive"] and h["ms"] > 0:
                    health_speed = max(0.0, min(1.0, 1.0 - h["ms"] / _SPEED_NORMALIZATION_MS))
                    speed_score = deleg_speed * 0.7 + health_speed * 0.3
                else:
                    speed_score = deleg_speed

        return success_rate, speed_score
