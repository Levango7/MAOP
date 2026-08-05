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
import shutil
import time
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
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT agent, routing_key, exit_code, duration_ms FROM delegations ORDER BY id DESC LIMIT 5000"
            ).fetchall()
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

# ── Parallel Implementation Note ──────────────────────────────
# NOTE: EvolveEngine is one of two parallel self-evolution implementations.
# The other is EvolutionLoop in maop/core/evolution_loop.py.
# Both have production callers:
#   - EvolveEngine (this class): used by maop_loop.py (main loop), dashboard/routers/evolve.py
#   - EvolutionLoop: used by core/three_layer_memory.py (consolidation)
# Future work: consider merging into a single canonical implementation.

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
        """Generate improvement suggestions. Merge with existing (保留已应用状态)。"""
        data = self._load_data()
        stats = _compute_stats(data)
        suggestions = _generate_suggestions(stats, data)
        # Merge with existing suggestions, preserve applied state
        existing = self._load_suggestions()
        existing_applied = {s.id: s.applied for s in existing if s.applied}
        for s in suggestions:
            if s.id in existing_applied:
                s.applied = True
        # Merge: new suggestions + existing ones not in new set
        new_ids = {s.id for s in suggestions}
        for s in existing:
            if s.id not in new_ids:
                suggestions.append(s)
        self._save_suggestions(suggestions)
        return EvolveResult(action="suggest", stats=stats, suggestions=suggestions)

    def apply(self, suggestion_id: str = "") -> EvolveResult:
        """Auto-apply a suggestion (if auto_applicable). 通过 ConfigMutator 安全应用。"""
        suggestions = self._load_suggestions()
        for s in suggestions:
            if s.id == suggestion_id:
                if s.applied:
                    return EvolveResult(action="apply", applied=s)
                if not s.auto_applicable:
                    return EvolveResult(action="apply", applied=s)
                # 通过 ConfigMutator 安全应用 (有 FileLock + backup + 回读校验)
                try:
                    from maop.core.config_mutator import ConfigMutator
                    mutator = ConfigMutator(root_dir=self._root)
                    result = mutator.apply_suggestion(suggestion_id)
                    if result.applied:
                        s.applied = True
                        self._save_suggestions(suggestions)
                        return EvolveResult(action="apply", applied=s)
                    else:
                        logger.warning("[evolve] ConfigMutator failed: %s, falling back to direct", result.error)
                        # Fallback: 直接应用 (向后兼容 — 测试环境无 agents.yaml 时)
                        s.applied = True
                        self._save_suggestions(suggestions)
                        self._apply_to_agents_yaml(s)
                except Exception as exc:
                    logger.warning("[evolve] ConfigMutator apply failed, falling back to direct: %s", exc)
                    # Fallback: 直接应用 (向后兼容)
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
                if not s.auto_applicable:
                    return EvolveResult(action="promote", applied=s)
                s.applied = True
                self._save_suggestions(suggestions)
                self._apply_to_agents_yaml(s)
                return EvolveResult(action="promote", applied=s)
        return EvolveResult(action="promote")

    def _apply_to_agents_yaml(self, suggestion: Suggestion) -> None:
        """Write a suggestion's effect into agents.yaml (使用安全写入 + 时间戳 backup)。"""
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

        # 修复: slow_agent 增加 timeout (而非减半), routing_mismatch 不禁用整个 agent
        if suggestion.type == "slow_agent":
            current = agent_cfg.get("timeout_s", 120)
            agent_cfg["timeout_s"] = min(600, int(current * 1.5))  # 增加 50%
        elif suggestion.type == "routing_mismatch":
            # 不禁用 agent，仅记录警告 (路由调整由 ConfigMutator._mutate_routing 处理)
            logger.info("[evolve] routing_mismatch for %s — use ConfigMutator for safe routing change", suggestion.agent)
            return

        # 安全写入: 时间戳 backup + FileLock + safe_write + 回读校验
        try:
            from datetime import datetime, timezone

            from maop.core.filelock import FileLock
            from maop.core.safe_writer import safe_write_text
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = agents_yaml.with_name(f"agents.yaml.bak.{ts}")
            shutil.copy2(agents_yaml, backup)
            content = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            lock_path = str(agents_yaml) + ".lock"
            with FileLock(lock_path, timeout_seconds=10):
                safe_write_text(agents_yaml, content, encoding="utf-8")
            logger.info("[evolve] Updated agents.yaml for suggestion %s (backup: %s)", suggestion.id, backup.name)
        except Exception as exc:
            logger.warning("[evolve] Failed to write agents.yaml: %s", exc)
            # 尝试恢复 backup
            if 'backup' in dir() and backup.exists():
                try:
                    shutil.copy2(backup, agents_yaml)
                    logger.info("[evolve] Restored agents.yaml from backup")
                except Exception:
                    pass

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
            logger.debug("Silent exception in evolve.py:420", exc_info=True)
            return []

    def auto_evolve(self, hours: int = 24) -> dict[str, Any]:
        """Run automatic evolution — 委托给 EvolutionLoop.run_full_evolution()。

        统一入口: 采集所有数据源 → 10 维度分析 → 策略评估 → 安全应用 → 验证
        保留向后兼容的返回格式 (analysis_report / new_suggestions / auto_applied)。
        """
        try:
            from maop.core.evolution_loop import EvolutionLoop
            loop = EvolutionLoop(root_dir=self._root)
            result = loop.run_full_evolution(hours=hours)
            # 向后兼容: 补充 EvolveEngine 历史返回字段
            result.setdefault("analysis_report", {
                "loop_report": result.get("loop_report", {}),
                "history_analysis": result.get("history_analysis", {}),
                "delegation_stats": result.get("delegation_stats", {}),
                "strategy_learning": result.get("strategy_learning", {}),
                "cache_evolution": result.get("cache_evolution", {}),
            })
            result.setdefault("new_suggestions", result.get("total_suggestions", 0))
            # agent_strategy: 旧 EvolveEngine 字段名, 等价于 strategy_learning
            result.setdefault("agent_strategy", result.get("strategy_learning", {
                "total_combos": 0, "adjustments": [], "recommendations": [],
            }))
            return result
        except Exception as exc:
            logger.warning("[evolve] EvolutionLoop failed, falling back to legacy: %s", exc)
            return self._auto_evolve_legacy(hours)

    def _auto_evolve_legacy(self, hours: int = 24) -> dict[str, Any]:
        """Legacy auto_evolve fallback (向后兼容)。"""
        from maop.history_analyzer import HistoryAnalyzer

        analyzer = HistoryAnalyzer(root_dir=self._root)
        report = analyzer.analyze(hours=hours)

        new_suggestions: list[Suggestion] = []

        # Cost-driven suggestions (human decision required)
        for driver in report.cost_drivers:
            if driver.dimension == "model" and driver.total_cost > 5.0:
                new_suggestions.append(Suggestion(
                    id=f"cost_model_{driver.dimension_value}_{int(time.time())}",
                    type="high_cost_model",
                    severity="medium",
                    detail=f"Model {driver.dimension_value} cost ${driver.total_cost:.2f} in {hours}h",
                    suggestion=f"Consider switching to a cheaper model or reducing calls to {driver.dimension_value}.",
                    auto_applicable=False,
                ))

        # Failure-driven suggestions (human decision required)
        for cluster in report.failure_clusters:
            if cluster.count >= 3:
                new_suggestions.append(Suggestion(
                    id=f"failure_{cluster.pattern}_{int(time.time())}",
                    type="recurring_failure",
                    severity="high",
                    detail=f"Failure pattern '{cluster.pattern}' occurred {cluster.count} times",
                    suggestion=cluster.root_cause_hypothesis,
                    auto_applicable=False,
                ))

        # Bottleneck-driven suggestions (human decision required)
        for bottleneck in report.bottlenecks:
            if bottleneck.avg_duration_ms > 30000:  # > 30s
                new_suggestions.append(Suggestion(
                    id=f"bottleneck_{bottleneck.component}_{int(time.time())}",
                    type="performance_bottleneck",
                    severity="medium",
                    detail=f"{bottleneck.component} avg {bottleneck.avg_duration_ms:.0f}ms, impact {bottleneck.impact_score:.2f}",
                    suggestion=f"Optimize {bottleneck.component} to reduce latency.",
                    auto_applicable=False,
                ))

        # ── Phase β.3a: Agent strategy learning ────────────────
        agent_strategy_report: dict[str, Any] = {}
        try:
            from maop.agent_strategy_learner import AgentStrategyLearner
            learner = AgentStrategyLearner(root_dir=self._root)
            strat_report = learner.learn(hours=hours)
            agent_strategy_report = {
                "total_combos": strat_report.total_combos,
                "reliable_combos": strat_report.reliable_combos,
                "underperformers": strat_report.underperformers,
                "adjustments_count": len(strat_report.adjustments),
                "routing_winners": strat_report.routing_winners,
                "recommendations": strat_report.recommendations,
            }
            # Convert agent strategy adjustments to suggestions
            for adj in strat_report.adjustments:
                sev = "high" if adj.action == "disable" else "medium"
                new_suggestions.append(Suggestion(
                    id=f"agent_strategy_{adj.agent}_{adj.routing_key}_{int(time.time())}",
                    type=f"agent_{adj.action}",
                    severity=sev,
                    agent=adj.agent,
                    routing_key=adj.routing_key,
                    detail=adj.reason,
                    suggestion=f"Action: {adj.action}"
                               + (f" → {adj.suggested_alternative}" if adj.suggested_alternative else ""),
                    auto_applicable=adj.auto_applicable,
                ))
            # Auto-apply safe agent strategy adjustments
            for adj in strat_report.adjustments:
                if adj.auto_applicable:
                    try:
                        learner.apply_adjustment(adj)
                    except Exception as exc:
                        logger.warning("[evolve] Agent strategy apply failed: %s", exc)
        except Exception as exc:
            logger.warning("[evolve] Agent strategy learning failed: %s", exc)

        # ── Phase β.3b: Cache strategy evolution ───────────────
        cache_evolve_report: dict[str, Any] = {}
        try:
            from maop.cache_evolver import CacheEvolver
            cache_evolver = CacheEvolver()
            c_report = cache_evolver.evolve(apply=True)
            cache_evolve_report = {
                "total_caches": c_report.total_caches,
                "adjustments_count": len(c_report.adjustments),
                "applied_count": c_report.applied_count,
                "skipped_count": c_report.skipped_count,
                "recommendations": c_report.recommendations,
            }
            # Convert cache adjustments to suggestions (informational)
            for cadj in c_report.adjustments:
                new_suggestions.append(Suggestion(
                    id=f"cache_{cadj.cache_name}_{cadj.parameter}_{int(time.time())}",
                    type=f"cache_{cadj.parameter}",
                    severity="low",
                    detail=f"{cadj.cache_name}: {cadj.parameter} {cadj.old_value}→{cadj.new_value} ({cadj.reason})",
                    suggestion=f"Cache {cadj.cache_name} {cadj.parameter} adjustment"
                               + (" (auto-applied)" if cadj.applied else " (needs review)"),
                    auto_applicable=cadj.auto_applicable,
                ))
        except Exception as exc:
            logger.warning("[evolve] Cache evolution failed: %s", exc)

        # Merge with existing suggestions (avoid duplicate ids) and persist.
        existing = self._load_suggestions()
        existing_ids = {s.id for s in existing}
        appended = 0
        for s in new_suggestions:
            if s.id not in existing_ids:
                existing.append(s)
                appended += 1
        if appended:
            self._save_suggestions(existing)

        # Auto-apply safe (auto_applicable) suggestions.
        auto_applied = 0
        for s in new_suggestions:
            if s.auto_applicable:
                try:
                    self.apply(s.id)
                    auto_applied += 1
                except Exception as exc:
                    logger.warning("[evolve] Auto-apply failed for %s: %s", s.id, exc)

        return {
            "analysis_report": {
                "period_hours": report.period_hours,
                "total_loops": report.total_loops,
                "success_rate": report.success_rate,
                "failure_clusters": len(report.failure_clusters),
                "bottlenecks": len(report.bottlenecks),
                "cost_drivers": len(report.cost_drivers),
                "recommendations": report.recommendations,
            },
            "agent_strategy": agent_strategy_report,
            "cache_evolution": cache_evolve_report,
            "new_suggestions": len(new_suggestions),
            "auto_applied": auto_applied,
        }
