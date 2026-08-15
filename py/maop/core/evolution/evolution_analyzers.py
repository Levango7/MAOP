"""EvolutionLoop — 统一分析器（10 维度）mixin。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。公开 API 不变。
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

from maop.core.evolution.evolution_loop_types import (
    EvolutionSuggestion,
    LoopPhase,
    LoopReport,
    PhaseResult,
)

logger = logging.getLogger(__name__)


class EvolutionAnalyzersMixin:
    """统一分析器（delegation/agent dimensions/history/strategy/cache）。"""


    def _analyze_delegation_stats(self, stats_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从 delegation 统计生成建议 (合并 EvolveEngine 的 4 种规则)。"""
        suggestions: list[dict[str, Any]] = []
        stats = stats_data.get("stats", {})
        raw_data = stats_data.get("raw_data", [])

        # 1. agent 成功率低
        for a in stats.get("by_agent", []):
            if a.get("total", 0) >= 3 and a.get("rate", 100) < 60:
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="reliability",
                    mutation_type="disable_agent",
                    severity="HIGH",
                    description=f"{a['agent']}: {a['rate']}% success ({a['success']}/{a['total']})",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=a["agent"],
                    mutation_params={"agent": a["agent"], "success_rate": a["rate"]},
                ).model_dump())

        # 2. 路由不匹配 — 修改路由而非禁用 agent
        for ak in stats.get("by_agent_key", []):
            if ak.get("total", 0) >= 3 and ak.get("rate", 100) < 50:
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="routing",
                    mutation_type="change_routing",
                    severity="HIGH",
                    description=f"{ak['agent']}/{ak['routing_key']}: {ak['rate']}% ({ak['success']}/{ak['total']})",
                    auto_applicable=True,
                    target_type="routing",
                    target_name=ak["routing_key"],
                    mutation_params={"agent": ak["agent"], "routing_key": ak["routing_key"], "success_rate": ak["rate"]},
                ).model_dump())

        # 3. agent 慢 — 增加 timeout 而非减半
        for a in stats.get("by_agent", []):
            if a.get("total", 0) >= 2 and a.get("avg_duration_ms", 0) > 60000:
                suggested_timeout = min(600, int(a["avg_duration_ms"] / 1000 * 1.5))
                suggestions.append(EvolutionSuggestion(
                    source="delegation_stats",
                    category="performance",
                    mutation_type="adjust_timeout",
                    severity="MEDIUM",
                    description=f"{a['agent']}: avg {a['avg_duration_ms']}ms",
                    auto_applicable=True,
                    target_type="agent",
                    target_name=a["agent"],
                    mutation_params={"agent": a["agent"], "suggested_timeout": suggested_timeout},
                ).model_dump())

        # 4. 空路由键
        no_key = [d for d in raw_data if not d.get("routing_key")]
        if no_key:
            suggestions.append(EvolutionSuggestion(
                source="delegation_stats",
                category="routing",
                mutation_type="change_routing",
                severity="LOW",
                description=f"{len(no_key)} delegations with empty routing_key",
                auto_applicable=False,
                target_type="system",
                target_name="empty_routing_key",
            ).model_dump())

        return suggestions

    def _analyze_agent_dimensions(self, mem_data: dict[str, Any]) -> list[dict[str, Any]]:
        """5 维度 agent 进化分析 (合并 AgentEvolution)。"""
        suggestions: list[dict[str, Any]] = []
        agent_name = mem_data.get("agent", "")
        if not agent_name:
            return suggestions

        performances = mem_data.get("performances", [])
        error_patterns = mem_data.get("error_patterns", [])
        interactions = mem_data.get("interactions", [])
        preferences = mem_data.get("preferences", [])


        # 1. 性能维度
        latencies = [p.get("content", {}).get("latency_ms", 0) for p in performances
                     if isinstance(p.get("content", {}).get("latency_ms"), (int, float))]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            if avg_latency > 10000:
                suggested_timeout = min(600, int(avg_latency / 1000 * 1.5))
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="performance",
                    mutation_type="adjust_timeout",
                    severity="HIGH",
                    description=f"Average latency {avg_latency:.0f}ms exceeds 10000ms threshold",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "suggested_timeout": suggested_timeout, "avg_latency_ms": round(avg_latency)},
                ).model_dump())

        # 2. 可靠性维度
        total = len(performances)
        failures = sum(1 for p in performances if p.get("content", {}).get("success") is False)
        failure_rate = failures / total if total > 0 else 0
        if failure_rate > 0.3:
            suggestions.append(EvolutionSuggestion(
                source="agent_memory",
                category="reliability",
                mutation_type="adjust_retries",
                severity="HIGH",
                description=f"Failure rate {failure_rate:.1%} exceeds 30% threshold",
                auto_applicable=True,
                target_type="agent",
                target_name=agent_name,
                mutation_params={"agent": agent_name, "suggested_max_retries": 5, "failure_rate": round(failure_rate, 3)},
            ).model_dump())

        # 3. 能力维度
        task_type_counts: dict[str, int] = {}
        for interaction in interactions:
            task_type = interaction.get("content", {}).get("task_type", "")
            if task_type:
                task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1
        for task_type, count in task_type_counts.items():
            if count >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="capability",
                    mutation_type="add_capability",
                    severity="MEDIUM",
                    description=f"Agent frequently used for '{task_type}' ({count} times) but not declared",
                    auto_applicable=True,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "suggested_capability": task_type},
                ).model_dump())

        # 4. 偏好维度
        param_adjustments: dict[str, list] = {}
        for pref in preferences:
            content = pref.get("content", {})
            param = content.get("parameter", "")
            if param:
                param_adjustments.setdefault(param, []).append(content.get("value"))
        for param, values in param_adjustments.items():
            if len(values) >= 5:
                from collections import Counter
                most_common = Counter(str(v) for v in values if v is not None).most_common(1)
                if most_common:
                    suggestions.append(EvolutionSuggestion(
                        source="agent_memory",
                        category="preference",
                        mutation_type="record_preference",
                        severity="LOW",
                        description=f"Parameter '{param}' adjusted {len(values)} times, most common: '{most_common[0][0]}'",
                        auto_applicable=True,
                        target_type="agent",
                        target_name=agent_name,
                        mutation_params={"agent": agent_name, "parameter": param, "suggested_default": most_common[0][0]},
                    ).model_dump())

        # 5. 错误学习维度
        error_freq: dict[str, int] = {}
        for ep in error_patterns:
            error_msg = ep.get("content", {}).get("error", "")[:100]
            if error_msg:
                error_freq[error_msg] = error_freq.get(error_msg, 0) + 1
        for error_msg, freq in error_freq.items():
            if freq >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="agent_memory",
                    category="error",
                    mutation_type="record_lesson",
                    severity="MEDIUM",
                    description=f"Recurring error ({freq} times): '{error_msg[:80]}'",
                    auto_applicable=False,
                    target_type="agent",
                    target_name=agent_name,
                    mutation_params={"agent": agent_name, "error": error_msg, "frequency": freq},
                ).model_dump())

        return suggestions

    def _analyze_history(self, history_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从历史分析生成建议 (成本/失败/瓶颈)。"""
        suggestions: list[dict[str, Any]] = []

        for driver in history_data.get("cost_drivers", []):
            if driver.get("total_cost", 0) > 5.0:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="cost",
                    mutation_type="switch_model",
                    severity="MEDIUM",
                    description=f"Model {driver.get('dimension_value', '')} cost ${driver.get('total_cost', 0):.2f}",
                    auto_applicable=False,
                    target_type="system",
                    target_name=driver.get("dimension_value", ""),
                    mutation_params={"model": driver.get("dimension_value", ""), "total_cost": driver.get("total_cost", 0)},
                ).model_dump())

        for cluster in history_data.get("failure_clusters", []):
            if cluster.get("count", 0) >= 3:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="error",
                    mutation_type="recurring_failure",
                    severity="HIGH",
                    description=f"Failure pattern '{cluster.get('pattern', '')}' occurred {cluster.get('count', 0)} times",
                    auto_applicable=False,
                    target_type="system",
                    target_name=cluster.get("pattern", ""),
                    mutation_params={"pattern": cluster.get("pattern", ""), "count": cluster.get("count", 0), "root_cause": cluster.get("root_cause_hypothesis", "")},
                ).model_dump())

        for bottleneck in history_data.get("bottlenecks", []):
            if bottleneck.get("avg_duration_ms", 0) > 30000:
                suggestions.append(EvolutionSuggestion(
                    source="history_analyzer",
                    category="bottleneck",
                    mutation_type="adjust_timeout",
                    severity="MEDIUM",
                    description=f"{bottleneck.get('component', '')} avg {bottleneck.get('avg_duration_ms', 0):.0f}ms",
                    auto_applicable=False,
                    target_type="system",
                    target_name=bottleneck.get("component", ""),
                    mutation_params={"component": bottleneck.get("component", ""), "avg_duration_ms": bottleneck.get("avg_duration_ms", 0)},
                ).model_dump())

        return suggestions

    def _analyze_strategy_learning(self, strategy_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从策略学习生成建议。"""
        suggestions: list[dict[str, Any]] = []
        for adj in strategy_data.get("adjustments", []):
            action = adj.get("action", "")
            mutation_map = {
                "disable": "disable_agent",
                "reroute": "change_routing",
                "reduce_timeout": "adjust_timeout",
                "prefer": "change_routing",
                "demote": "change_routing",
            }
            mutation_type = mutation_map.get(action, "record_lesson")
            severity = "HIGH" if action == "disable" else "MEDIUM"
            suggestions.append(EvolutionSuggestion(
                source="strategy_learner",
                category="routing" if action in ("reroute", "prefer", "demote") else "reliability",
                mutation_type=mutation_type,
                severity=severity,
                description=adj.get("reason", ""),
                auto_applicable=adj.get("auto_applicable", False),
                target_type="agent" if action in ("disable", "reduce_timeout") else "routing",
                target_name=adj.get("agent", ""),
                mutation_params={
                    "agent": adj.get("agent", ""),
                    "routing_key": adj.get("routing_key", ""),
                    "suggested_agent": adj.get("suggested_alternative", ""),
                },
            ).model_dump())
        return suggestions

    def _analyze_cache_evolution(self, cache_data: dict[str, Any]) -> list[dict[str, Any]]:
        """从缓存进化生成建议。"""
        suggestions: list[dict[str, Any]] = []
        for adj in cache_data.get("adjustments", []):
            suggestions.append(EvolutionSuggestion(
                source="cache_evolver",
                category="cache",
                mutation_type="adjust_cache",
                severity="LOW",
                description=f"{adj.get('cache_name', '')}: {adj.get('parameter', '')} {adj.get('old_value', 0)}→{adj.get('new_value', 0)} ({adj.get('reason', '')})",
                auto_applicable=adj.get("auto_applicable", False),
                target_type="cache",
                target_name=adj.get("cache_name", ""),
                mutation_params={
                    "cache_name": adj.get("cache_name", ""),
                    "parameter": adj.get("parameter", ""),
                    "new_value": adj.get("new_value", 0),
                },
            ).model_dump())
        return suggestions

