"""MAOP Agent Evolution — Agent 自进化引擎。

基于 agent 记忆（交互历史、性能指标、错误模式）自动分析 agent 表现，
生成优化建议，并安全地应用可自动执行的优化。

进化策略：
  - 性能优化：延迟过高 → 建议切换更快的模型或调整超时
  - 可靠性优化：失败率高 → 建议增加重试次数或切换 fallback
  - 能力优化：频繁用于未标记能力 → 建议添加能力标签
  - 偏好学习：用户频繁调整某参数 → 自动设置默认值
  - 错误学习：记录错误模式与解决方案，避免重复犯错

Usage::

    from maop.core.agent.evolution.agent_evolution import AgentEvolution

    evolution = AgentEvolution(root_dir="/path/to/MAOP")
    result = await evolution.evolve("claude")
    status = evolution.get_status("claude")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maop.core.agent.memory_ctx.agent_memory import AgentMemory

logger = logging.getLogger(__name__)


@dataclass
class EvolutionSuggestion:
    """单条进化建议。"""
    category: str = ""  # performance / reliability / capability / preference / error_learning
    priority: str = "low"  # high / medium / low
    description: str = ""
    action: str = ""  # auto_applied / manual_required / monitoring
    changes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0  # 0.0-1.0


@dataclass
class EvolutionResult:
    """自进化结果。"""
    agent_name: str = ""
    suggestions: list[EvolutionSuggestion] = field(default_factory=list)
    auto_applied: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "suggestions": [s.__dict__ for s in self.suggestions],
            "auto_applied": self.auto_applied,
            "errors": self.errors,
            "summary": self.summary,
        }


# 自动应用的安全阈值
HIGH_LATENCY_THRESHOLD_MS = 10000  # 延迟超过 10s
HIGH_FAILURE_RATE_THRESHOLD = 0.3  # 失败率超过 30%
FREQUENT_CAP_THRESHOLD = 3  # 同一未标记能力被使用 3 次以上
PREF_ADJUSTMENT_THRESHOLD = 5  # 同一参数被手动调整 5 次以上


class AgentEvolution:
    """Agent 自进化引擎，基于记忆数据自动优化 agent 配置。"""

    def __init__(self, root_dir: str | Path = ".") -> None:
        self._root = Path(root_dir)
        self._memory = AgentMemory(root_dir=root_dir)

    async def evolve(self, agent_name: str, agent_config: Any = None) -> EvolutionResult:
        """对指定 agent 执行自进化分析 — 委托给 EvolutionLoop.evolve_agent()。"""
        try:
            from maop.core.evolution.evolution_loop import EvolutionLoop
            loop = EvolutionLoop(root_dir=self._root)
            result = loop.evolve_agent(agent_name, agent_config)
            suggestions = [
                EvolutionSuggestion(
                    category=s.get("category", ""),
                    priority=s.get("severity", "low").lower(),
                    description=s.get("description", ""),
                    action=s.get("mutation_type", ""),
                    changes=s.get("mutation_params", {}),
                    confidence=0.8,
                )
                for s in result.get("suggestions", [])
            ]
            auto_applied = [
                {"category": s.get("category", ""), "description": s.get("description", ""), "changes": s.get("mutation_params", {})}
                for s in result.get("auto_applied", [])
            ]
            return EvolutionResult(
                agent_name=agent_name,
                suggestions=suggestions,
                auto_applied=auto_applied,
                summary=result.get("summary", ""),
            )
        except Exception as exc:
            logger.warning("[agent_evolution] EvolutionLoop failed, falling back to legacy: %s", exc)
            return await self._evolve_legacy(agent_name, agent_config)

    async def _evolve_legacy(self, agent_name: str, agent_config: Any = None) -> EvolutionResult:
        """对指定 agent 执行自进化分析。

        1. 从记忆中收集性能数据、错误模式、交互历史
        2. 分析模式，生成优化建议
        3. 自动应用安全的优化（只调整非破坏性参数）
        4. 记录进化事件到记忆
        """
        result = EvolutionResult(agent_name=agent_name)

        # 收集记忆数据

        performances = self._memory.retrieve(agent_name, "performance", limit=100)
        error_patterns = self._memory.retrieve(agent_name, "error_pattern", limit=50)
        interactions = self._memory.retrieve(agent_name, "interaction", limit=100)
        preferences = self._memory.retrieve(agent_name, "preference", limit=20)
        lessons = self._memory.retrieve(agent_name, "lesson", limit=20)

        # ── 1. 性能分析 ──
        perf_suggestions = self._analyze_performance(agent_name, performances)
        result.suggestions.extend(perf_suggestions)

        # ── 2. 可靠性分析 ──
        reliability_suggestions = self._analyze_reliability(agent_name, error_patterns, performances)
        result.suggestions.extend(reliability_suggestions)

        # ── 3. 能力分析 ──
        capability_suggestions = self._analyze_capabilities(agent_name, interactions, agent_config)
        result.suggestions.extend(capability_suggestions)

        # ── 4. 偏好学习 ──
        pref_suggestions = self._analyze_preferences(agent_name, preferences)
        result.suggestions.extend(pref_suggestions)

        # ── 5. 错误学习 ──
        error_suggestions = self._analyze_error_lessons(agent_name, lessons, error_patterns)
        result.suggestions.extend(error_suggestions)

        # 自动应用安全的优化
        for suggestion in result.suggestions:
            if suggestion.action == "auto_applied" and suggestion.confidence >= 0.7:
                applied = self._apply_suggestion(agent_name, suggestion)
                if applied:
                    result.auto_applied.append({
                        "category": suggestion.category,
                        "description": suggestion.description,
                        "changes": suggestion.changes,
                    })

        # 生成总结
        total = len(result.suggestions)
        applied_count = len(result.auto_applied)
        result.summary = (
            f"Analyzed {len(performances)} performance records, "
            f"{len(error_patterns)} error patterns, "
            f"{len(interactions)} interactions. "
            f"Generated {total} suggestions, auto-applied {applied_count}."
        )

        # 记录进化事件
        self._memory.record_evolution(
            agent_name=agent_name,
            evolution_type="full_analysis",
            description=result.summary,
            changes={
                "suggestions_count": total,
                "auto_applied_count": applied_count,
                "categories": list({s.category for s in result.suggestions}),
            },
            success=True,
        )

        # 将高优先级建议存为 lesson 记忆
        for s in result.suggestions:
            if s.priority == "high":
                self._memory.store(
                    agent_name=agent_name,
                    memory_type="lesson",
                    content={
                        "category": s.category,
                        "description": s.description,
                        "action": s.action,
                    },
                    importance=0.8,
                )

        return result

    def _analyze_performance(
        self, agent_name: str, performances: list[dict[str, Any]]
    ) -> list[EvolutionSuggestion]:
        """分析性能数据，生成优化建议。"""
        suggestions: list[EvolutionSuggestion] = []
        if not performances:
            return suggestions

        # 计算平均延迟
        latencies = []
        for p in performances:
            content = p.get("content", {})
            latency = content.get("latency_ms", 0)
            if latency and isinstance(latency, (int, float)):
                latencies.append(latency)

        if not latencies:
            return suggestions

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        if avg_latency > HIGH_LATENCY_THRESHOLD_MS:
            suggestions.append(EvolutionSuggestion(
                category="performance",
                priority="high",
                description=(
                    f"Average latency {avg_latency:.0f}ms exceeds threshold "
                    f"{HIGH_LATENCY_THRESHOLD_MS}ms. Consider switching to a "
                    f"faster model or increasing timeout."
                ),
                action="manual_required",
                changes={
                    "avg_latency_ms": round(avg_latency),
                    "max_latency_ms": max_latency,
                    "suggested_action": "switch_to_faster_model_or_increase_timeout",
                },
                confidence=0.85,
            ))
        elif max_latency > HIGH_LATENCY_THRESHOLD_MS:
            suggestions.append(EvolutionSuggestion(
                category="performance",
                priority="medium",
                description=(
                    f"Peak latency {max_latency}ms is high. Monitor for "
                    f"degradation trends."
                ),
                action="monitoring",
                changes={"max_latency_ms": max_latency},
                confidence=0.6,
            ))

        return suggestions

    def _analyze_reliability(
        self,
        agent_name: str,
        error_patterns: list[dict[str, Any]],
        performances: list[dict[str, Any]],
    ) -> list[EvolutionSuggestion]:
        """分析可靠性，生成优化建议。"""
        suggestions: list[EvolutionSuggestion] = []

        # 计算失败率
        total = len(performances)
        failures = sum(
            1 for p in performances
            if p.get("content", {}).get("success") is False
        )
        failure_rate = failures / total if total > 0 else 0.0

        if failure_rate > HIGH_FAILURE_RATE_THRESHOLD:
            suggestions.append(EvolutionSuggestion(
                category="reliability",
                priority="high",
                description=(
                    f"Failure rate {failure_rate:.1%} exceeds threshold "
                    f"{HIGH_FAILURE_RATE_THRESHOLD:.0%}. Consider increasing "
                    f"max_retries or configuring a fallback model."
                ),
                action="auto_applied",
                changes={
                    "failure_rate": round(failure_rate, 3),
                    "total_runs": total,
                    "failures": failures,
                    "suggested_max_retries": 5,
                },
                confidence=0.8,
            ))

        # 分析错误模式
        if len(error_patterns) >= 3:
            suggestions.append(EvolutionSuggestion(
                category="reliability",
                priority="medium",
                description=(
                    f"Detected {len(error_patterns)} distinct error patterns. "
                    f"Review and add error handling for common failures."
                ),
                action="manual_required",
                changes={"error_pattern_count": len(error_patterns)},
                confidence=0.7,
            ))

        return suggestions

    def _analyze_capabilities(
        self,
        agent_name: str,
        interactions: list[dict[str, Any]],
        agent_config: Any = None,
    ) -> list[EvolutionSuggestion]:
        """分析能力使用情况，发现未标记的能力。"""
        suggestions: list[EvolutionSuggestion] = []
        if not interactions:
            return suggestions

        # 统计交互中出现的任务类型
        task_type_counts: dict[str, int] = {}
        current_caps: set[str] = set()
        if agent_config:
            if hasattr(agent_config, "capabilities"):
                current_caps = set(agent_config.capabilities)
            elif isinstance(agent_config, dict):
                current_caps = set(agent_config.get("capabilities", []))

        for interaction in interactions:
            content = interaction.get("content", {})
            task_type = content.get("task_type", "")
            if task_type and task_type not in current_caps:
                task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1

        # 发现频繁使用但未标记的能力
        for task_type, count in task_type_counts.items():
            if count >= FREQUENT_CAP_THRESHOLD:
                suggestions.append(EvolutionSuggestion(
                    category="capability",
                    priority="medium",
                    description=(
                        f"Agent is frequently used for '{task_type}' "
                        f"({count} times) but this capability is not declared. "
                        f"Consider adding it to the agent's capabilities."
                    ),
                    action="auto_applied",
                    changes={
                        "task_type": task_type,
                        "usage_count": count,
                        "suggested_capability": task_type,
                    },
                    confidence=0.75,
                ))

        return suggestions

    def _analyze_preferences(
        self, agent_name: str, preferences: list[dict[str, Any]]
    ) -> list[EvolutionSuggestion]:
        """分析用户偏好，学习常用设置。"""
        suggestions: list[EvolutionSuggestion] = []
        if not preferences:
            return suggestions

        # 统计参数调整频率
        param_adjustments: dict[str, int] = {}
        param_values: dict[str, list[Any]] = {}

        for pref in preferences:
            content = pref.get("content", {})
            param = content.get("parameter", "")
            value = content.get("value")
            if param:
                param_adjustments[param] = param_adjustments.get(param, 0) + 1
                if param not in param_values:
                    param_values[param] = []
                param_values[param].append(value)

        for param, count in param_adjustments.items():
            if count >= PREF_ADJUSTMENT_THRESHOLD:
                # 找出最常用的值
                values = param_values.get(param, [])
                if values:
                    # 简单众数
                    from collections import Counter
                    most_common = Counter(
                        str(v) for v in values if v is not None
                    ).most_common(1)
                    if most_common:
                        suggested_value = most_common[0][0]
                        suggestions.append(EvolutionSuggestion(
                            category="preference",
                            priority="low",
                            description=(
                                f"Parameter '{param}' is frequently adjusted "
                                f"({count} times). Most common value: "
                                f"'{suggested_value}'. Consider setting as default."
                            ),
                            action="auto_applied",
                            changes={
                                "parameter": param,
                                "adjustment_count": count,
                                "suggested_default": suggested_value,
                            },
                            confidence=0.7,
                        ))

        return suggestions

    def _analyze_error_lessons(
        self,
        agent_name: str,
        lessons: list[dict[str, Any]],
        error_patterns: list[dict[str, Any]],
    ) -> list[EvolutionSuggestion]:
        """从历史教训和错误模式中学习。"""
        suggestions: list[EvolutionSuggestion] = []

        # 如果有重复出现的错误模式，生成解决方案建议
        error_freq: dict[str, int] = {}
        for ep in error_patterns:
            content = ep.get("content", {})
            error_msg = content.get("error", "")
            if error_msg:
                # 简化错误消息作为 key
                error_key = error_msg[:100]
                error_freq[error_key] = error_freq.get(error_key, 0) + 1

        for error_msg, freq in error_freq.items():
            if freq >= 3:
                # 检查是否已有解决方案
                has_solution = any(
                    lesson.get("content", {}).get("description", "").startswith("Error:")
                    and error_msg[:50] in lesson.get("content", {}).get("description", "")
                    for lesson in lessons
                )
                if not has_solution:
                    suggestions.append(EvolutionSuggestion(
                        category="error_learning",
                        priority="medium",
                        description=(
                            f"Recurring error ({freq} times): '{error_msg[:80]}...'. "
                            f"Document a solution to prevent future occurrences."
                        ),
                        action="manual_required",
                        changes={
                            "error": error_msg,
                            "frequency": freq,
                        },
                        confidence=0.65,
                    ))

        return suggestions

    def _apply_suggestion(self, agent_name: str, suggestion: EvolutionSuggestion) -> bool:
        """安全地自动应用一条建议。

        只应用非破坏性的变更：
        - capability: 添加新能力标签（只增不删）
        - preference: 记录建议的默认值（不直接修改配置文件）
        - reliability: 记录建议的重试次数（不直接修改配置文件）

        对于需要修改配置文件的建议，只记录不执行，由管理员手动确认。
        """
        try:
            if suggestion.category == "capability":
                # 能力标签只增不删 — 通过 ConfigMutator 真正修改 agents.yaml
                try:
                    from maop.core.reliability.config_mutator import ConfigMutator
                    mutator = ConfigMutator(root_dir=self._root)
                    # 生成临时 suggestion 并应用
                    import uuid
                    tmp_id = f"cap_{uuid.uuid4().hex[:8]}"
                    from maop.core.evolution.evolution_loop import EvolutionSuggestion
                    s = EvolutionSuggestion(
                        id=tmp_id,
                        category="capability",
                        mutation_type="add_capability",
                        severity="MEDIUM",
                        auto_applicable=True,
                        target_type="agent",
                        target_name=agent_name,
                        mutation_params={
                            "agent": agent_name,
                            "suggested_capability": suggestion.changes.get("suggested_capability"),
                        },
                    )
                    # 写入临时 suggestion 文件让 ConfigMutator 能读到
                    import json
                    sf = self._root / "data" / "evolve-suggestions.json"
                    existing = []
                    if sf.exists():
                        existing = json.loads(sf.read_text(encoding="utf-8"))
                    existing.append(s.model_dump())
                    sf.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                    result = mutator.apply_suggestion(tmp_id)
                    if result.applied:
                        logger.info("[agent_evolution] Added capability '%s' to agents.yaml for '%s'",
                                    suggestion.changes.get("suggested_capability"), agent_name)
                        return True
                except Exception as exc:
                    logger.warning("[agent_evolution] ConfigMutator failed, recording to memory only: %s", exc)
                # Fallback: 只记录到记忆
                self._memory.store(
                    agent_name=agent_name,
                    memory_type="lesson",
                    content={
                        "type": "capability_added",
                        "capability": suggestion.changes.get("suggested_capability"),
                        "description": suggestion.description,
                    },
                    importance=0.7,
                )
                return True

            elif suggestion.category == "preference":
                # 偏好只记录，不自动修改配置
                self._memory.store(
                    agent_name=agent_name,
                    memory_type="preference",
                    content={
                        "type": "suggested_default",
                        "parameter": suggestion.changes.get("parameter"),
                        "value": suggestion.changes.get("suggested_default"),
                        "auto_generated": True,
                    },
                    importance=0.6,
                )
                return True

            elif suggestion.category == "reliability":
                # 可靠性建议只记录，由管理员决定是否调整
                self._memory.store(
                    agent_name=agent_name,
                    memory_type="lesson",
                    content={
                        "type": "reliability_suggestion",
                        "suggested_max_retries": suggestion.changes.get("suggested_max_retries"),
                        "failure_rate": suggestion.changes.get("failure_rate"),
                    },
                    importance=0.8,
                )
                return True

            return False
        except Exception as exc:
            logger.error("[agent_evolution] Failed to apply suggestion: %s", exc)
            return False

    def get_status(self, agent_name: str) -> dict[str, Any]:
        """获取 agent 的自进化状态。"""
        summary = self._memory.summarize(agent_name)
        history = self._memory.get_evolution_history(agent_name, limit=5)

        # 最近一次进化结果
        last_evolution = history[0] if history else None

        return {
            "agent_name": agent_name,
            "total_memories": summary["total_memories"],
            "memory_by_type": summary["by_type"],
            "evolution_count": summary["evolution_count"],
            "last_evolution": last_evolution,
            "recent_evolution_history": history,
            "top_error_patterns": summary["top_error_patterns"],
            "avg_importance": summary["avg_importance"],
            "ready_for_evolution": summary["total_memories"] >= 10,
        }
