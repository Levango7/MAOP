"""EvolutionLoop — 统一数据采集器 mixin。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。公开 API 不变。
依赖宿主的 ``_db_connect`` 与 ``_root``。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EvolutionCollectorsMixin:
    """统一数据采集器（delegation/agent memory/history/strategy/cache）。"""


    def _collect_delegation_stats(self) -> dict[str, Any]:
        """采集 delegation 历史统计 (来自 EvolveEngine)。"""
        try:
            from maop.evolve import _compute_stats, _load_observability_data_from_db
            db_path = self._root / "data" / "maop.db"
            data = _load_observability_data_from_db(db_path)
            if not data:
                data = _load_observability_data_from_db(self._root / "logs")
            stats = _compute_stats(data)
            return {
                "stats": stats.model_dump(),
                "raw_data": data,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Delegation stats collection failed: %s", exc)
            return {"stats": {}, "raw_data": []}

    def _collect_agent_memory(self, agent_name: str = "") -> dict[str, Any]:
        """采集 agent 记忆数据 (来自 AgentEvolution)。"""
        try:
            from maop.core.agent.memory_ctx.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            if agent_name:
                summary = mem.summarize(agent_name)
                return {
                    "agent": agent_name,
                    "summary": summary,
                    "performances": mem.retrieve(agent_name, "performance", limit=100),
                    "error_patterns": mem.retrieve(agent_name, "error_pattern", limit=50),
                    "interactions": mem.retrieve(agent_name, "interaction", limit=100),
                    "preferences": mem.retrieve(agent_name, "preference", limit=20),
                    "lessons": mem.retrieve(agent_name, "lesson", limit=20),
                }
            return {"agent": "", "summary": {}, "performances": [], "error_patterns": [], "interactions": [], "preferences": [], "lessons": []}
        except Exception as exc:
            logger.debug("[evo-loop] Agent memory collection failed: %s", exc)
            return {"agent": agent_name, "summary": {}, "performances": [], "error_patterns": [], "interactions": [], "preferences": [], "lessons": []}

    def _collect_history_analysis(self, hours: int = 24) -> dict[str, Any]:
        """采集历史分析数据 (来自 HistoryAnalyzer)。"""
        try:
            from maop.history_analyzer import HistoryAnalyzer
            analyzer = HistoryAnalyzer(root_dir=self._root)
            report = analyzer.analyze(hours=hours)
            return {
                "failure_clusters": [{"pattern": c.pattern, "count": c.count, "agents": c.agents, "root_cause_hypothesis": c.root_cause_hypothesis} for c in report.failure_clusters],
                "bottlenecks": [{"component": b.component, "avg_duration_ms": b.avg_duration_ms, "impact_score": b.impact_score} for b in report.bottlenecks],
                "cost_drivers": [{"dimension": d.dimension, "dimension_value": d.dimension_value, "total_cost": d.total_cost, "total_tokens": d.total_tokens, "call_count": d.call_count} for d in report.cost_drivers],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] History analysis failed: %s", exc)
            return {"failure_clusters": [], "bottlenecks": [], "cost_drivers": [], "recommendations": []}

    def _collect_strategy_learning(self, hours: int = 24) -> dict[str, Any]:
        """采集 agent 策略学习数据 (来自 AgentStrategyLearner)。"""
        try:
            from maop.agent_strategy_learner import AgentStrategyLearner
            learner = AgentStrategyLearner(root_dir=self._root)
            report = learner.learn(hours=hours)
            return {
                "total_combos": report.total_combos,
                "reliable_combos": report.reliable_combos,
                "underperformers": report.underperformers,
                "routing_winners": report.routing_winners,
                "adjustments": [
                    {"agent": a.agent, "routing_key": a.routing_key, "action": a.action,
                     "confidence": a.confidence, "reason": a.reason,
                     "suggested_alternative": a.suggested_alternative,
                     "auto_applicable": a.auto_applicable}
                    for a in report.adjustments
                ],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Strategy learning failed: %s", exc)
            return {"total_combos": 0, "adjustments": [], "routing_winners": {}, "recommendations": []}

    def _collect_cache_evolution(self) -> dict[str, Any]:
        """采集缓存进化数据 (来自 CacheEvolver)。"""
        try:
            from maop.cache_evolver import CacheEvolver
            evolver = CacheEvolver()
            report = evolver.evolve(apply=False)
            return {
                "total_caches": report.total_caches,
                "adjustments": [
                    {"cache_name": a.cache_name, "cache_type": a.cache_type,
                     "parameter": a.parameter, "old_value": a.old_value,
                     "new_value": a.new_value, "reason": a.reason,
                     "auto_applicable": a.auto_applicable}
                    for a in report.adjustments
                ],
                "recommendations": report.recommendations,
            }
        except Exception as exc:
            logger.debug("[evo-loop] Cache evolution failed: %s", exc)
            return {"total_caches": 0, "adjustments": [], "recommendations": []}

