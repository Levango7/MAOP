"""EvolutionLoop — Agent 专属进化 + 全量进化 mixin。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。公开 API 不变。
依赖宿主的 collectors/analyzers/phases 方法与 ``_db_connect``。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


class EvolutionAgentMixin:
    """Agent 专属进化 / 全量进化 / 建议写入。"""

    if TYPE_CHECKING:
        # 宿主类（EvolutionLoop）提供的属性与方法 —— 仅用于类型检查
        _root: Path
        _data_dir: Path
        _strategy_name: str
        _collect_agent_memory: Callable[..., dict[str, Any]]
        _analyze_agent_dimensions: Callable[..., list[dict[str, Any]]]
        _collect_delegation_stats: Callable[..., dict[str, Any]]
        _collect_history_analysis: Callable[..., dict[str, Any]]
        _collect_strategy_learning: Callable[..., dict[str, Any]]
        _collect_cache_evolution: Callable[..., dict[str, Any]]
        _analyze_delegation_stats: Callable[..., list[dict[str, Any]]]
        _analyze_history: Callable[..., list[dict[str, Any]]]
        _analyze_strategy_learning: Callable[..., list[dict[str, Any]]]
        _analyze_cache_evolution: Callable[..., list[dict[str, Any]]]
        run_cycle: Callable[..., Any]


    def evolve_agent(self, agent_name: str, agent_config: Any = None) -> dict[str, Any]:
        """对指定 agent 执行 5 维度进化分析 (AgentEvolution.evolve 的统一替代)。"""
        mem_data = self._collect_agent_memory(agent_name)
        suggestions = self._analyze_agent_dimensions(mem_data)

        # 自动应用安全的建议
        auto_applied = []
        for s in suggestions:
            if s.get("auto_applicable") and not s.get("applied"):
                try:
                    from maop.core.reliability.config_mutator import ConfigMutator
                    mutator = ConfigMutator(root_dir=str(self._root))
                    mut_result = mutator.apply_suggestion(s.get("id", ""))
                    if getattr(mut_result, "applied", False):
                        auto_applied.append(s)
                except Exception as exc:
                    logger.debug("[evo-loop] Auto-apply failed for %s: %s", s.get("id", ""), exc)

        # 记录进化事件到记忆
        try:
            from maop.core.agent.memory_ctx.agent_memory import AgentMemory
            mem = AgentMemory(root_dir=str(self._root))
            mem.record_evolution(
                agent_name=agent_name,
                evolution_type="full_analysis",
                description=f"Generated {len(suggestions)} suggestions, auto-applied {len(auto_applied)}.",
                changes={"suggestions_count": len(suggestions), "auto_applied_count": len(auto_applied)},
                success=True,
            )
        except Exception as exc:
            logger.debug("[evo-loop] Record evolution failed: %s", exc)

        return {
            "agent_name": agent_name,
            "suggestions": suggestions,
            "auto_applied": auto_applied,
            "summary": f"Generated {len(suggestions)} suggestions, auto-applied {len(auto_applied)}.",
        }

    def get_agent_status(self, agent_name: str) -> dict[str, Any]:
        """获取 agent 进化状态 (AgentEvolution.get_status 的统一替代)。"""
        mem_data = self._collect_agent_memory(agent_name)
        summary = mem_data.get("summary", {})
        return {
            "agent_name": agent_name,
            "total_memories": summary.get("total_memories", 0),
            "memory_by_type": summary.get("by_type", {}),
            "evolution_count": summary.get("evolution_count", 0),
            "top_error_patterns": summary.get("top_error_patterns", []),
            "avg_importance": summary.get("avg_importance", 0),
            "ready_for_evolution": summary.get("total_memories", 0) >= 10,
        }

    def run_full_evolution(self, hours: int = 24, dry_run: bool = False, auto_rollback: bool = True) -> dict[str, Any]:
        """运行全量进化 (合并 EvolveEngine.auto_evolve 的能力)。

        采集所有数据源 → 10 维度分析 → 策略评估 → 安全应用 → 验证
        """
        # 1. 采集所有数据源
        delegation_stats = self._collect_delegation_stats()
        history_data = self._collect_history_analysis(hours=hours)
        strategy_data = self._collect_strategy_learning(hours=hours)
        cache_data = self._collect_cache_evolution()

        # 2. 生成所有建议
        all_suggestions: list[dict[str, Any]] = []
        all_suggestions.extend(self._analyze_delegation_stats(delegation_stats))
        all_suggestions.extend(self._analyze_history(history_data))
        all_suggestions.extend(self._analyze_strategy_learning(strategy_data))
        all_suggestions.extend(self._analyze_cache_evolution(cache_data))

        # 3. 持久化建议
        self._write_suggestions(all_suggestions)

        # 4. 运行标准循环 (含 ErrorLedger + Heal + Strategy + Apply + Validate + Consolidate)
        loop_report = self.run_cycle(dry_run=dry_run, auto_rollback=auto_rollback)

        # 5. 策略评估并应用可自动应用的建议
        auto_applied = 0
        if not dry_run:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            decisions = engine.evaluate(all_suggestions)
            for d in decisions:
                if d.should_apply:
                    result = engine.apply(d.suggestion_id)
                    if result.get("applied"):
                        auto_applied += 1

        return {
            "loop_report": loop_report.model_dump(),
            "total_suggestions": len(all_suggestions),
            "auto_applied": auto_applied,
            "delegation_stats": delegation_stats.get("stats", {}),
            "history_analysis": {
                "failure_clusters": len(history_data.get("failure_clusters", [])),
                "bottlenecks": len(history_data.get("bottlenecks", [])),
                "cost_drivers": len(history_data.get("cost_drivers", [])),
            },
            "strategy_learning": {
                "total_combos": strategy_data.get("total_combos", 0),
                "adjustments": len(strategy_data.get("adjustments", [])),
                "reliable_combos": strategy_data.get("reliable_combos", []),
                "underperformers": strategy_data.get("underperformers", []),
                "routing_winners": strategy_data.get("routing_winners", {}),
                "recommendations": strategy_data.get("recommendations", []),
            },
            "cache_evolution": {
                "total_caches": cache_data.get("total_caches", 0),
                "adjustments": len(cache_data.get("adjustments", [])),
                "recommendations": cache_data.get("recommendations", []),
            },
        }

    def _write_suggestions(self, suggestions: list[dict[str, Any]]) -> None:
        """原子写入建议文件，保留已应用状态。"""
        path = self._data_dir / "evolve-suggestions.json"
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = []
        # 保留已应用的建议状态，merge 而非覆盖
        existing_applied = {s.get("id"): s.get("applied", False) for s in existing if s.get("applied")}
        existing_ids = {s.get("id") for s in existing}
        for s in suggestions:
            sid = s.get("id", "")
            if sid not in existing_ids:
                # 保留之前的 applied 状态
                if sid in existing_applied:
                    s["applied"] = True
                existing.append(s)
        # 原子写入
        try:
            from maop.core.reliability.filelock import FileLock
            from maop.core.reliability.safe_writer import safe_write_text
            lock_path = str(path) + ".lock"
            with FileLock(lock_path, timeout_seconds=5):
                safe_write_text(path, json.dumps(existing[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
        except ImportError:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing[-200:], f, indent=2, ensure_ascii=False)

