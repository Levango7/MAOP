"""MAOP Improvement Suggester — LLM-driven candidate improvement generation.

F2-01 Agent 自演化闭环的第二环：接收 :class:`PerformanceMetrics` 与
agent 上下文，调用 LLM 生成结构化的候选改进建议（prompt 调整、参数
调整、路由变更等）。当 LLM 不可用时回退到基于规则的确定性建议生成，
保证闭环在离线/无 API key 环境下依然可运行。

输出统一为 :class:`EvolutionSuggestion`（来自 evolution_loop_types），
可直接喂给 StrategyEngine / ConfigMutator / ABTestFramework。

Usage::

    from maop.core.evolution.suggester import ImprovementSuggester
    from maop.core.evolution.evaluator import PerformanceEvaluator

    evaluator = PerformanceEvaluator()
    metrics = evaluator.evaluate(traces)
    suggester = ImprovementSuggester(root_dir="/path/to/MAOP")
    suggestions = await suggester.suggest(metrics, agent_name="researcher")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.evolution.evaluator import MetricDelta, PerformanceMetrics
from maop.core.evolution.evolution_loop_types import EvolutionSuggestion

logger = logging.getLogger(__name__)


_SUGGESTION_PROMPT = """You are an agent self-evolution engineer for the MAOP platform.
Given the performance metrics below, propose 1-3 concrete improvement candidates.
Each candidate MUST be a JSON object with these fields:
  - mutation_type: one of [adjust_timeout, change_routing, switch_model, adjust_prompt, adjust_retries, adjust_cache]
  - severity: HIGH | MEDIUM | LOW
  - description: one-line human-readable reason
  - target_name: the agent or routing key to change
  - mutation_params: object with concrete parameters (e.g. {"timeout_s": 120})
  - auto_applicable: true if safe to auto-apply without human review

Return a JSON array. No prose, no markdown fences.

Performance metrics (JSON):
{metrics_json}

Agent context (JSON):
{context_json}
"""


class SuggestionContext(BaseModel):
    """附加上下文，帮助 LLM 生成更精准的建议。"""

    agent_name: str = ""
    current_config: dict[str, Any] = Field(default_factory=dict)
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)
    baseline_metrics: PerformanceMetrics | None = None
    delta: MetricDelta | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ImprovementSuggester:
    """LLM 驱动的候选改进生成器。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录，用于定位 LLM provider 配置。
    model : str
        调用的模型名（从 models.yaml 解析）。默认 ``""`` 表示由
        LLMProviderFactory 自动选择。
    enable_llm : bool
        是否启用 LLM 路径。``False`` 时始终走规则回退（测试/离线）。
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        model: str = "",
        enable_llm: bool = True,
    ) -> None:
        self._root = Path(root_dir) if root_dir else None
        self._model = model
        self._enable_llm = enable_llm

    async def suggest(
        self,
        metrics: PerformanceMetrics,
        context: SuggestionContext | None = None,
    ) -> list[EvolutionSuggestion]:
        """生成候选改进建议。

        先尝试 LLM 路径；失败或禁用时回退到 :meth:`_rule_based`。
        """
        context = context or SuggestionContext()
        suggestions: list[EvolutionSuggestion] = []

        if self._enable_llm:
            try:
                suggestions = await self._llm_suggest(metrics, context)
            except Exception as exc:
                logger.warning("[suggester] LLM path failed, falling back to rules: %s", exc)
                suggestions = []

        if not suggestions:
            suggestions = self._rule_based(metrics, context)

        # 持久化（best-effort）
        self._persist(suggestions)
        return suggestions

    # ── LLM 路径 ───────────────────────────────────────────────

    async def _llm_suggest(
        self,
        metrics: PerformanceMetrics,
        context: SuggestionContext,
    ) -> list[EvolutionSuggestion]:
        """调用 LLM 生成建议，解析 JSON 数组。"""
        factory = self._get_factory()
        if factory is None:
            return []

        prompt = _SUGGESTION_PROMPT.format(
            metrics_json=json.dumps(metrics.to_dict(), ensure_ascii=False),
            context_json=json.dumps(context.model_dump(mode="json"), ensure_ascii=False),
        )
        messages = [{"role": "user", "content": prompt}]

        # chat_with_fallback 自动解析 default model 并走 fallback 链
        result = await factory.chat_with_fallback(messages=messages, model_name=self._model)
        response = getattr(result, "response", None)
        content = getattr(response, "content", "") or ""
        candidates = _parse_json_array(content)
        if not candidates:
            logger.debug("[suggester] LLM returned no parseable candidates")
            return []

        suggestions: list[EvolutionSuggestion] = []
        for c in candidates:
            try:
                suggestions.append(_candidate_to_suggestion(c, source="llm"))
            except Exception as exc:
                logger.debug("[suggester] skip malformed candidate: %s", exc)
        return suggestions

    def _get_factory(self) -> Any:
        """惰性获取 LLMProviderFactory，失败返回 None。"""
        if self._root is None:
            return None
        try:
            from maop.core.agent.llm_chat.llm_provider import LLMProviderFactory

            return LLMProviderFactory(root_dir=str(self._root))
        except Exception as exc:
            logger.debug("[suggester] LLM factory unavailable: %s", exc)
            return None

    # ── 规则回退路径 ───────────────────────────────────────────

    def _rule_based(
        self,
        metrics: PerformanceMetrics,
        context: SuggestionContext,
    ) -> list[EvolutionSuggestion]:
        """基于阈值的确定性建议生成（离线/无 LLM 时使用）。"""
        suggestions: list[EvolutionSuggestion] = []
        agent = context.agent_name or "default"

        # 1. 成功率低 → 增加重试
        if metrics.sample_count >= 3 and metrics.success_rate < 0.8:
            suggestions.append(EvolutionSuggestion(
                source="rule",
                category="reliability",
                mutation_type="adjust_retries",
                severity="HIGH" if metrics.success_rate < 0.5 else "MEDIUM",
                description=f"{agent}: success_rate {metrics.success_rate:.1%} below 80% → increase retries",
                auto_applicable=True,
                target_type="agent",
                target_name=agent,
                mutation_params={"agent": agent, "suggested_max_retries": 5},
                metadata={"success_rate": metrics.success_rate},
            ))

        # 2. 延迟高 → 调整 timeout / 切换更快的模型
        if metrics.avg_latency_ms > 5000:
            suggested_timeout = min(600, int(metrics.avg_latency_ms / 1000 * 1.5))
            suggestions.append(EvolutionSuggestion(
                source="rule",
                category="performance",
                mutation_type="adjust_timeout",
                severity="HIGH" if metrics.avg_latency_ms > 20000 else "MEDIUM",
                description=f"{agent}: avg_latency {metrics.avg_latency_ms:.0f}ms → adjust timeout to {suggested_timeout}s",
                auto_applicable=True,
                target_type="agent",
                target_name=agent,
                mutation_params={"agent": agent, "suggested_timeout": suggested_timeout},
                metadata={"avg_latency_ms": metrics.avg_latency_ms},
            ))

        # 3. 成本高 → 建议切换更便宜的模型
        if metrics.avg_cost_usd > 0.02:
            suggestions.append(EvolutionSuggestion(
                source="rule",
                category="cost",
                mutation_type="switch_model",
                severity="MEDIUM",
                description=f"{agent}: avg_cost ${metrics.avg_cost_usd:.4f} → consider cheaper model",
                auto_applicable=False,
                target_type="agent",
                target_name=agent,
                mutation_params={"agent": agent, "reason": "cost_reduction"},
                metadata={"avg_cost_usd": metrics.avg_cost_usd},
            ))

        # 4. 按 agent 分组定位瓶颈
        for ag, st in metrics.by_agent.items():
            if st.get("count", 0) >= 3 and st.get("success_rate", 1) < 0.6:
                suggestions.append(EvolutionSuggestion(
                    source="rule",
                    category="reliability",
                    mutation_type="change_routing",
                    severity="HIGH",
                    description=f"{ag}: per-agent success_rate {st['success_rate']:.1%} → reroute traffic",
                    auto_applicable=False,
                    target_type="routing",
                    target_name=ag,
                    mutation_params={"agent": ag, "success_rate": st["success_rate"]},
                    metadata=st,
                ))

        # 5. delta 驱动（当提供 baseline 时）
        if context.delta is not None and context.delta.regression:
            suggestions.append(EvolutionSuggestion(
                source="rule",
                category="performance",
                mutation_type="adjust_prompt",
                severity="HIGH",
                description=f"Regression detected: {context.delta.summary}",
                auto_applicable=False,
                target_type="agent",
                target_name=agent,
                mutation_params={"regression": True},
                metadata=context.delta.to_dict(),
            ))

        return suggestions

    # ── 持久化 ─────────────────────────────────────────────────

    def _persist(self, suggestions: list[EvolutionSuggestion]) -> None:
        """best-effort 写入 data/evolution-suggestions.json。"""
        if not suggestions or self._root is None:
            return
        try:
            path = self._root / "data" / "evolution-suggestions.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            existing_ids = {s.get("id") for s in existing}
            for s in suggestions:
                if s.id not in existing_ids:
                    existing.append(s.model_dump())
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing[-200:], f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.debug("[suggester] persist failed: %s", exc)

    # ── 同步便捷入口 ───────────────────────────────────────────

    def suggest_sync(
        self,
        metrics: PerformanceMetrics,
        context: SuggestionContext | None = None,
    ) -> list[EvolutionSuggestion]:
        """同步包装：在无运行 event loop 时直接调用。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.suggest(metrics, context))
        # 已在 loop 中：用线程跑
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.suggest(metrics, context)).result()


# ── 解析辅助 ──────────────────────────────────────────────────────


def _parse_json_array(content: str) -> list[dict[str, Any]]:
    """从 LLM 输出中提取 JSON 数组，容忍 markdown fence / 前后噪声。"""
    text = content.strip()
    # 去除 markdown fence
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # 截取第一个 [ 到最后一个 ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        result = json.loads(text[start : end + 1])
        if isinstance(result, list):
            return [c for c in result if isinstance(c, dict)]
    except json.JSONDecodeError as exc:
        logger.debug("[suggester] JSON parse failed: %s", exc)
    return []


def _candidate_to_suggestion(c: dict[str, Any], *, source: str) -> EvolutionSuggestion:
    """将 LLM 候选 dict 映射为 EvolutionSuggestion。"""
    return EvolutionSuggestion(
        source=source,
        category=str(c.get("category", "performance")),
        mutation_type=str(c.get("mutation_type", "adjust_prompt")),
        severity=str(c.get("severity", "MEDIUM")).upper(),
        description=str(c.get("description", "")),
        auto_applicable=bool(c.get("auto_applicable", False)),
        target_type=str(c.get("target_type", "agent")),
        target_name=str(c.get("target_name", "")),
        mutation_params=c.get("mutation_params", {}) if isinstance(c.get("mutation_params"), dict) else {},
        metadata={"raw": c, "generated_at": time.time()},
    )