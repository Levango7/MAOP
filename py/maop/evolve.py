"""MAOP Evolve — Self-Evolution Engine v2.

Self-evolution strategy and error learning.: reads execution history, computes stats,
generates improvement suggestions, and optionally auto-applies them.

Actions:
  - analyze: Compute stats from delegation history
  - suggest: Generate improvement suggestions
  - apply:   Auto-apply a suggestion
  - promote: Promote a suggestion to permanent config
  - status:  Show current evolution status
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────

class AgentStats(BaseModel):
    agent: str = ""
    total: int = 0
    success: int = 0
    fail: int = 0
    rate: float = 0.0
    avg_duration_ms: int = 0


class RoutingKeyStats(BaseModel):
    routing_key: str = ""
    total: int = 0
    success: int = 0
    rate: float = 0.0


class AgentKeyStats(BaseModel):
    agent: str = ""
    routing_key: str = ""
    total: int = 0
    success: int = 0
    rate: float = 0.0
    avg_duration_ms: int = 0


class EvolutionStats(BaseModel):
    by_agent: list[AgentStats] = Field(default_factory=list)
    by_key: list[RoutingKeyStats] = Field(default_factory=list)
    by_agent_key: list[AgentKeyStats] = Field(default_factory=list)


from enum import Enum

class SuggestionSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Suggestion(BaseModel):
    id: str = ""
    type: str = ""
    severity: str = "medium"
    agent: str = ""
    routing_key: str = ""
    detail: str = ""
    suggestion: str = ""
    auto_applicable: bool = False
    applied: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvolveResult(BaseModel):
    action: str = ""
    stats: EvolutionStats | None = None
    suggestions: list[Suggestion] = Field(default_factory=list)
    applied: Suggestion | None = None


# ── Observability data loader ─────────────────────────────────

def _load_observability_data_from_db(db_path: Path) -> list[dict[str, Any]]:
    """Load delegation history from SQLite (maop.db).

    This is the primary data source in production — delegations are
    persisted via MaopDatabase.insert_delegation().  Returns rows in the
    same dict shape that _compute_stats expects::

        {"agent": str, "routing_key": str, "result": {"exit_code": int, "duration_ms": int}}
    """
    if not db_path.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT agent, routing_key, exit_code, duration_ms FROM delegations ORDER BY id DESC LIMIT 5000"
        ).fetchall()
        conn.close()
        return [
            {
                "agent": r["agent"] or "unknown",
                "routing_key": r["routing_key"] or "",
                "result": {
                    "exit_code": r["exit_code"] if r["exit_code"] is not None else -1,
                    "duration_ms": r["duration_ms"] if r["duration_ms"] is not None else 0,
                },
            }
            for r in rows
        ]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[evolve] Failed to load from DB %s: %s", db_path, exc)
        return []


def _load_observability_data(log_dir: Path) -> list[dict[str, Any]]:
    """Load delegation history from JSON log (legacy fallback)."""
    log_file = log_dir / "delegations.json"
    if not log_file.exists():
        return []
    try:
        raw = json.loads(log_file.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        return [raw]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[evolve] Failed to load JSON %s: %s", log_file, exc)
        return []


# ── Stats computation ─────────────────────────────────────────

def _compute_stats(data: list[dict[str, Any]]) -> EvolutionStats:
    """Compute per-agent, per-key, and per-agent-key stats."""
    if not data:
        return EvolutionStats()

    # By agent
    by_agent: dict[str, dict[str, Any]] = {}
    for item in data:
        agent = item.get("agent", "unknown")
        if agent not in by_agent:
            by_agent[agent] = {"total": 0, "success": 0, "duration_sum": 0, "duration_count": 0}
        by_agent[agent]["total"] += 1
        result = item.get("result", {})
        if result.get("exit_code") == 0:
            by_agent[agent]["success"] += 1
        dur = result.get("duration_ms", 0)
        if dur:
            by_agent[agent]["duration_sum"] += dur
            by_agent[agent]["duration_count"] += 1

    agent_stats = []
    for agent, s in by_agent.items():
        fail = s["total"] - s["success"]
        rate = round((s["success"] / s["total"]) * 100, 1) if s["total"] > 0 else 0
        avg_dur = round(s["duration_sum"] / s["duration_count"]) if s["duration_count"] > 0 else 0
        agent_stats.append(AgentStats(
            agent=agent, total=s["total"], success=s["success"],
            fail=fail, rate=rate, avg_duration_ms=avg_dur,
        ))

    # By routing key
    by_key: dict[str, dict[str, Any]] = {}
    for item in data:
        rk = item.get("routing_key", "")
        if rk not in by_key:
            by_key[rk] = {"total": 0, "success": 0}
        by_key[rk]["total"] += 1
        result = item.get("result", {})
        if result.get("exit_code") == 0:
            by_key[rk]["success"] += 1

    key_stats = []
    for rk, s in by_key.items():
        rate = round((s["success"] / s["total"]) * 100, 1) if s["total"] > 0 else 0
        key_stats.append(RoutingKeyStats(routing_key=rk, total=s["total"], success=s["success"], rate=rate))

    # By agent:key
    by_ak: dict[str, dict[str, Any]] = {}
    for item in data:
        agent = item.get("agent", "unknown")
        rk = item.get("routing_key", "")
        key = f"{agent}:{rk}"
        if key not in by_ak:
            by_ak[key] = {"total": 0, "success": 0, "duration_sum": 0, "duration_count": 0}
        by_ak[key]["total"] += 1
        result = item.get("result", {})
        if result.get("exit_code") == 0:
            by_ak[key]["success"] += 1
        dur = result.get("duration_ms", 0)
        if dur:
            by_ak[key]["duration_sum"] += dur
            by_ak[key]["duration_count"] += 1

    ak_stats = []
    for key, s in by_ak.items():
        parts = key.split(":", 1)
        agent, rk = parts[0], parts[1] if len(parts) > 1 else ""
        rate = round((s["success"] / s["total"]) * 100, 1) if s["total"] > 0 else 0
        avg_dur = round(s["duration_sum"] / s["duration_count"]) if s["duration_count"] > 0 else 0
        ak_stats.append(AgentKeyStats(
            agent=agent, routing_key=rk, total=s["total"],
            success=s["success"], rate=rate, avg_duration_ms=avg_dur,
        ))

    return EvolutionStats(by_agent=agent_stats, by_key=key_stats, by_agent_key=ak_stats)


# ── Suggestion generation ─────────────────────────────────────

def _generate_suggestions(stats: EvolutionStats, data: list[dict]) -> list[Suggestion]:
    """Generate improvement suggestions from stats."""
    suggestions: list[Suggestion] = []
    sid = 0

    # 1. Low success rate per agent
    for a in stats.by_agent:
        if a.total >= 3 and a.rate < 60:
            suggestions.append(Suggestion(
                id=f"S{sid:03d}", type="agent_low_success", severity="high",
                agent=a.agent,
                detail=f"{a.agent}: {a.rate}% success ({a.success}/{a.total})",
                suggestion=f"Check if {a.agent} CLI is working. Consider switching primary agent.",
                auto_applicable=False,
            ))
            sid += 1

    # 2. Per-agent-key optimization
    for ak in stats.by_agent_key:
        if ak.total >= 3 and ak.rate < 50:
            suggestions.append(Suggestion(
                id=f"S{sid:03d}", type="routing_mismatch", severity="high",
                agent=ak.agent, routing_key=ak.routing_key,
                detail=f"{ak.agent}/{ak.routing_key}: {ak.rate}% ({ak.success}/{ak.total})",
                suggestion=f"Agent {ak.agent} underperforming for {ak.routing_key}. Change routing.",
                auto_applicable=True,
            ))
            sid += 1

    # 3. Slow agents
    for a in stats.by_agent:
        if a.total >= 2 and a.avg_duration_ms > 60000:
            suggestions.append(Suggestion(
                id=f"S{sid:03d}", type="slow_agent", severity="medium",
                agent=a.agent,
                detail=f"{a.agent}: avg {a.avg_duration_ms}ms",
                suggestion=f"Reduce timeout_s for {a.agent} or try a faster model.",
                auto_applicable=True,
            ))
            sid += 1

    # 4. Empty routing key
    no_key = [d for d in data if not d.get("routing_key")]
    if len(no_key) > 0:
        suggestions.append(Suggestion(
            id=f"S{sid:03d}", type="empty_routing_key", severity="low",
            detail=f"{len(no_key)} delegations with empty routing_key",
            suggestion="Ensure all tasks are routed through MAOP-plan.",
            auto_applicable=False,
        ))
        sid += 1

    return suggestions


# ── Evolve Engine ─────────────────────────────────────────────

class EvolveEngine:
    """Self-evolution engine for MAOP.

    Usage::

        engine = EvolveEngine(root_dir="/path/to/MAOP")
        result = engine.analyze()
        result = engine.suggest()
        result = engine.apply(suggestion_id="S000")
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            root_dir = Path.cwd()
        self._root = Path(root_dir)
        self._log_dir = self._root / "logs"
        self._db_path = self._root / "data" / "maop.db"
        self._suggestions_file = self._root / "data" / "evolve-suggestions.json"

    def _load_data(self) -> list[dict[str, Any]]:
        """Load delegation history: SQLite first, JSON fallback."""
        data = _load_observability_data_from_db(self._db_path)
        if data:
            return data
        return _load_observability_data(self._log_dir)

    def analyze(self) -> EvolveResult:
        """Compute stats from delegation history."""
        data = self._load_data()
        stats = _compute_stats(data)
        return EvolveResult(action="analyze", stats=stats)

    def suggest(self) -> EvolveResult:
        """Generate improvement suggestions."""
        data = self._load_data()
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        self._save_suggestions(suggestions)
        return EvolveResult(action="suggest", stats=stats, suggestions=suggestions)

    def apply(self, suggestion_id: str = "") -> EvolveResult:
        """Auto-apply a suggestion (if auto_applicable)."""
        suggestions = self._load_suggestions()
        for s in suggestions:
            if s.id == suggestion_id:
                if not s.auto_applicable:
                    return EvolveResult(action="apply", applied=s)
                s.applied = True
                self._save_suggestions(suggestions)
                self._apply_to_agents_yaml(s)
                return EvolveResult(action="apply", applied=s)
        return EvolveResult(action="apply")

    def promote(self, suggestion_id: str = "") -> EvolveResult:
        """Promote a suggestion to permanent config."""
        suggestions = self._load_suggestions()
        for s in suggestions:
            if s.id == suggestion_id:
                s.applied = True
                self._save_suggestions(suggestions)
                self._apply_to_agents_yaml(s)
                return EvolveResult(action="promote", applied=s)
        return EvolveResult(action="promote")

    def _apply_to_agents_yaml(self, suggestion: Suggestion) -> None:
        """Write a suggestion's effect into agents.yaml."""
        try:
            import yaml
        except ImportError:
            logger.warning("[evolve] PyYAML not installed, cannot apply to agents.yaml")
            return

        agents_yaml = self._root / "config" / "agents.yaml"
        if not agents_yaml.exists():
            logger.warning("[evolve] agents.yaml not found at %s", agents_yaml)
            return

        try:
            with open(agents_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("[evolve] Failed to read agents.yaml: %s", exc)
            return

        agents = data.get("agents", {})
        agent_cfg = agents.get(suggestion.agent)
        if agent_cfg is None:
            logger.debug("[evolve] Agent '%s' not found in agents.yaml", suggestion.agent)
            return

        if suggestion.type == "slow_agent":
            current = agent_cfg.get("timeout_s", 120)
            agent_cfg["timeout_s"] = max(30, current // 2)
        elif suggestion.type == "routing_mismatch":
            agent_cfg["enabled"] = False

        try:
            with open(agents_yaml, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.info("[evolve] Updated agents.yaml for suggestion %s", suggestion.id)
        except Exception as exc:
            logger.warning("[evolve] Failed to write agents.yaml: %s", exc)

    def status(self) -> EvolveResult:
        """Show current evolution status."""
        suggestions = self._load_suggestions()
        data = self._load_data()
        stats = _compute_stats(data)
        return EvolveResult(action="status", stats=stats, suggestions=suggestions)

    def _save_suggestions(self, suggestions: list[Suggestion]) -> None:
        try:
            self._suggestions_file.parent.mkdir(parents=True, exist_ok=True)
            self._suggestions_file.write_text(
                json.dumps([s.model_dump() for s in suggestions], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("[evolve] Failed to save suggestions: %s", exc)

    def _load_suggestions(self) -> list[Suggestion]:
        if not self._suggestions_file.exists():
            return []
        try:
            raw = json.loads(self._suggestions_file.read_text(encoding="utf-8"))
            return [Suggestion(**s) for s in raw]
        except Exception:
            return []
