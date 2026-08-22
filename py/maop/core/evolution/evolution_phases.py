"""EvolutionLoop — 六阶段编排（_phase_*）mixin。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。公开 API 不变。
``run_cycle`` 保留在主文件编排，经 self 调用本 mixin 的 _phase_*。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from maop.core.evolution.evolution_loop_types import (
    EvolutionSuggestion,
    LoopPhase,
    PhaseResult,
)

logger = logging.getLogger(__name__)


class EvolutionPhasesMixin:
    """OBSERVE/HEAL/SUGGEST/EVALUATE/APPLY/VALIDATE/CONSOLIDATE 阶段方法。"""


    def _phase_observe(self) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            hotspots = ledger.get_hotspots(top=20)
            unhealed = [h for h in hotspots if h.count >= self._heal_threshold]
            patterns = [h.pattern for h in unhealed]
            return PhaseResult(
                phase=LoopPhase.OBSERVE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "hotspot_count": len(unhealed),
                    "hotspot_patterns": patterns,
                    "top_patterns": [{"pattern": h.pattern, "count": h.count} for h in unhealed[:5]],
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Observe phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.OBSERVE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_heal(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        attempts = 0
        successes = 0
        try:
            from maop.core.reliability.self_heal import SelfHealEngine
            engine = SelfHealEngine(root_dir=str(self._root))
            for pattern in patterns:
                attempts += 1
                report = engine.run_all(trigger_condition=pattern)
                if report.repaired > 0:
                    successes += 1
            return PhaseResult(
                phase=LoopPhase.HEAL,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"attempts": attempts, "successes": successes},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Heal phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.HEAL, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"attempts": attempts, "successes": successes})

    def _phase_suggest(self, patterns: list[str]) -> PhaseResult:
        start = time.time()
        suggestions: list[dict[str, Any]] = []
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            promoted = ledger.auto_promote(threshold=self._suggest_threshold)

            for rule in promoted:
                suggestions.append(EvolutionSuggestion(
                    source="error_ledger",
                    category="error",
                    mutation_type="error_pattern_rule",
                    severity="HIGH" if rule.count >= 5 else "MEDIUM",
                    description=f"Recurring pattern '{rule.pattern}' (count={rule.count}) → auto-promoted rule",
                    auto_applicable=True,
                    target_type="system",
                    target_name=rule.pattern,
                    metadata={"pattern": rule.pattern, "count": rule.count, "rule": rule.rule},
                ).model_dump())

            for pattern in patterns:
                if not any(s.get("metadata", {}).get("pattern") == pattern for s in suggestions):
                    errors = ledger.find_by_pattern(pattern)
                    if errors:
                        latest = errors[0]
                        is_routing = "routing" in pattern
                        suggestions.append(EvolutionSuggestion(
                                source="error_ledger",
                                category="routing" if is_routing else "reliability",
                                mutation_type="change_routing" if is_routing else "disable_agent",
                                severity="MEDIUM",
                                description=f"Unhealed error pattern '{pattern}' needs config adjustment",
                                auto_applicable=False,
                                target_type="routing" if is_routing else "agent",
                                target_name=pattern,
                                metadata={"pattern": pattern, "error_type": latest.error_type, "context": latest.context},
                            ).model_dump())

            self._write_suggestions(suggestions)

            return PhaseResult(
                phase=LoopPhase.SUGGEST,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"count": len(suggestions), "suggestions": suggestions},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Suggest phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.SUGGEST, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"count": len(suggestions), "suggestions": suggestions})

    def _phase_debate(self, suggestions: list[dict[str, Any]]) -> PhaseResult:
        """C-2: DEBATE 阶段 — 对每条建议发起辩论，过滤低置信度结论。

        - 对 severity=HIGH 的建议强制辩论（即使 BalancedStrategy 本会自动应用）。
        - 辩论 consensus=False 的建议标记为 "debate_blocked"，不进入 APPLY。
        - 辩论 low_confidence=True 的建议标记为 "needs_human_review"。
        - 未配置 debate_dispatcher 时透传 suggestions（向后兼容）。
        """
        start = time.time()
        accepted: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        needs_review: list[dict[str, Any]] = []
        try:
            dispatcher = getattr(self, "_debate_dispatcher", None)
            participants = getattr(self, "_debate_participants", []) or []
            if dispatcher is None or not participants:
                # 未配置 → 透传（向后兼容）
                return PhaseResult(
                    phase=LoopPhase.DEBATE,
                    success=True,
                    duration_s=round(time.time() - start, 3),
                    details={
                        "accepted_suggestions": suggestions,
                        "blocked": [],
                        "needs_review": [],
                        "debated": 0,
                        "skipped": len(suggestions),
                    },
                )
            import asyncio

            for sug in suggestions:
                severity = str(sug.get("severity", "MEDIUM")).upper()
                description = sug.get("description", "")
                # 构造辩论问题
                question = (
                    f"是否应采纳进化建议: {description} "
                    f"(severity={severity}, type={sug.get('mutation_type', '')})?"
                )
                context = {
                    "suggestion": sug,
                    "root_dir": str(getattr(self, "_root", "")),
                }
                try:
                    verdict = asyncio.run(dispatcher.run_debate(
                        question,
                        participants,
                        context=context,
                        routing_key=sug.get("target_name", ""),
                    ))
                except Exception as exc:  # pragma: no cover — 单条辩论失败兜底
                    logger.warning(
                        "[evo-loop] debate for suggestion %s failed: %s",
                        sug.get("id", "?"), exc,
                    )
                    # 辩论失败时保守起见标记为 needs_review
                    sug_copy = dict(sug)
                    sug_copy["debate_error"] = str(exc)
                    needs_review.append(sug_copy)
                    continue
                # 根据裁决结果分类
                sug_with_verdict = dict(sug)
                sug_with_verdict["debate_verdict"] = {
                    "consensus": verdict.consensus,
                    "final_confidence": verdict.final_confidence,
                    "winner": verdict.winner,
                    "low_confidence": verdict.low_confidence,
                    "cost_terminated": verdict.cost_terminated,
                }
                if not verdict.consensus:
                    sug_with_verdict["debate_blocked"] = True
                    blocked.append(sug_with_verdict)
                elif verdict.low_confidence:
                    sug_with_verdict["needs_human_review"] = True
                    needs_review.append(sug_with_verdict)
                else:
                    accepted.append(sug_with_verdict)
            return PhaseResult(
                phase=LoopPhase.DEBATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "accepted_suggestions": accepted,
                    "blocked": blocked,
                    "needs_review": needs_review,
                    "debated": len(suggestions),
                    "accepted_count": len(accepted),
                    "blocked_count": len(blocked),
                    "needs_review_count": len(needs_review),
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Debate phase failed: %s", exc)
            # 失败时透传 suggestions（向后兼容，不阻断主流程）
            return PhaseResult(
                phase=LoopPhase.DEBATE,
                success=False,
                error=str(exc),
                duration_s=round(time.time() - start, 3),
                details={
                    "accepted_suggestions": suggestions,
                    "blocked": [],
                    "needs_review": [],
                    "debated": 0,
                },
            )

    def _phase_evaluate(self, suggestions: list[dict[str, Any]]) -> PhaseResult:
        start = time.time()
        approved: list[dict[str, Any]] = []
        try:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            decisions = engine.evaluate(suggestions)
            for decision in decisions:
                if decision.should_apply:
                    approved.append({
                        "suggestion_id": decision.suggestion_id,
                        "type": decision.suggestion_type,
                        "severity": decision.severity,
                        "reason": decision.reason,
                    })
            return PhaseResult(
                phase=LoopPhase.EVALUATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"approved": approved, "total": len(suggestions), "approved_count": len(approved)},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Evaluate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.EVALUATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"approved": approved})


    def _phase_apply(self, approved: list[dict[str, Any]], dry_run: bool = False) -> PhaseResult:
        """Apply approved mutations. In dry_run mode, log proposed changes only."""
        start = time.time()
        applied = 0
        proposed: list[dict[str, Any]] = []
        try:
            from maop.core.evolution.evolution_strategies import StrategyEngine
            engine = StrategyEngine(root_dir=str(self._root), strategy_name=self._strategy_name)
            for item in approved:
                sid = item.get("suggestion_id", "")
                if not sid:
                    continue
                if dry_run:
                    proposed.append({
                        "suggestion_id": sid,
                        "type": item.get("type", ""),
                        "severity": item.get("severity", ""),
                        "reason": item.get("reason", ""),
                    })
                    applied += 1
                else:
                    result = engine.apply(sid)
                    if result.get("applied", False):
                        applied += 1
            details: dict[str, Any] = {"applied": applied, "total": len(approved)}
            if dry_run:
                details["proposed"] = proposed
                details["dry_run"] = True
            return PhaseResult(
                phase=LoopPhase.APPLY,
                success=True,
                duration_s=round(time.time() - start, 3),
                details=details,
            )
        except Exception as exc:
            logger.warning("[evo-loop] Apply phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.APPLY, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"applied": applied})

    def _phase_validate(self, baseline_errors: int) -> PhaseResult:
        start = time.time()
        try:
            from maop.core.reliability.error_ledger import ErrorLedger
            ledger = ErrorLedger(root_dir=str(self._root))
            current_hotspots = ledger.get_hotspots(top=20)
            current_unhealed = len([h for h in current_hotspots if h.count >= self._heal_threshold])
            improved = current_unhealed < baseline_errors

            if improved:
                logger.info("[evo-loop] Validation: errors decreased %d → %d", baseline_errors, current_unhealed)
            else:
                logger.info("[evo-loop] Validation: no improvement (%d → %d)", baseline_errors, current_unhealed)

            return PhaseResult(
                phase=LoopPhase.VALIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={"improved": improved, "baseline": baseline_errors, "current": current_unhealed},
            )
        except Exception as exc:
            logger.warning("[evo-loop] Validate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.VALIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

    def _phase_consolidate(self) -> PhaseResult:
        start = time.time()
        try:
            # T3: 收敛到 MemoryFacade（mode="agent"），consolidate 统一返回 dict。
            from maop.memory.facade import MemoryFacade
            mem = MemoryFacade(root_dir=str(self._root), mode="agent")
            report = mem.consolidate(min_score=0.6, limit=50) or {}
            return PhaseResult(
                phase=LoopPhase.CONSOLIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "candidates": report.get("candidates", 0),
                    "consolidated": report.get("consolidated", 0),
                    "errors": report.get("errors", 0),
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Consolidate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.CONSOLIDATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3))

