"""EvolutionLoop — 六阶段编排（_phase_*）mixin。

T2 架构债治理：从 ``evolution_loop.py`` 拆分。公开 API 不变。
``run_cycle`` 保留在主文件编排，经 self 调用本 mixin 的 _phase_*。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maop.core.evolution.evolution_loop_types import (
    EvolutionSuggestion,
    LoopPhase,
    PhaseResult,
)

logger = logging.getLogger(__name__)


class EvolutionPhasesMixin:
    """OBSERVE/HEAL/SUGGEST/EVALUATE/APPLY/VALIDATE/CONSOLIDATE 阶段方法。"""

    if TYPE_CHECKING:
        # 宿主类（EvolutionLoop）提供的属性与方法 —— 仅用于类型检查
        _root: Path
        _heal_threshold: int
        _suggest_threshold: int
        _strategy_name: str
        _write_suggestions: Callable[..., None]


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
                    # H2 修复：asyncio.run() 在已有事件循环中会抛
                    # RuntimeError。检测循环状态：在循环内时用线程池
                    # 在新循环上同步执行并等待结果（verdict 需被使用，
                    # 不能 fire-and-forget）；不在循环内时直接 asyncio.run。
                    import asyncio
                    _debate_coro = dispatcher.run_debate(
                        question,
                        participants,
                        context=context,
                        routing_key=sug.get("target_name", ""),
                    )
                    try:
                        asyncio.get_running_loop()
                        # 已在事件循环内 —— 在独立线程的新循环上同步等待
                        # P2 修复：coroutine 可能引用主循环原语（绑定主循环的
                        # asyncio.Lock/Queue 等），在新事件循环中执行时抛
                        # RuntimeError。包装执行函数捕获并记录 warning，
                        # 异常向上传播由外层兜底标记 needs_review
                        import concurrent.futures

                        # B023：用默认参数显式绑定循环变量 _debate_coro。
                        # 当前调用点 _pool.submit(...).result() 在同轮内阻塞求值，
                        # 故无实际缺陷；显式绑定是防止日后改为延迟/并发调用时
                        # 静默捕获到错误迭代的协程。
                        def _run_debate_in_new_loop(_coro: Any = _debate_coro) -> Any:
                            try:
                                return asyncio.run(_coro)
                            except RuntimeError as _re:
                                logger.warning(
                                    "[evo-loop] debate coroutine 在新循环执行时"
                                    "可能引用了主循环原语: %s", _re,
                                )
                                raise

                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                            verdict = _pool.submit(_run_debate_in_new_loop).result()
                    except RuntimeError:
                        # 无运行中的事件循环 —— 直接同步执行
                        verdict = asyncio.run(_debate_coro)
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
        pending_approval: list[dict[str, Any]] = []
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
                elif decision.suggestion_id and decision.severity in ("HIGH", "MEDIUM"):
                    # 非自动应用但有一定严重性的建议，暂存待人工审批
                    pending_approval.append({
                        "suggestion_id": decision.suggestion_id,
                        "type": decision.suggestion_type,
                        "severity": decision.severity,
                        "reason": decision.reason,
                    })
            return PhaseResult(
                phase=LoopPhase.EVALUATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "approved": approved,
                    "pending_approval": pending_approval,
                    "total": len(suggestions),
                    "approved_count": len(approved),
                    "pending_count": len(pending_approval),
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Evaluate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.EVALUATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"approved": approved, "pending_approval": []})


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

            # v5.2.0: 接入 A/B SPRT 决策（AC-04）。
            # 在传统错误计数判定之上，叠加序贯概率比检验（SPRT）结果，
            # 为 run_cycle 的回滚分支提供更细粒度的 promote/rollback 建议。
            # 向后兼容：无 A/B 实验或框架构造失败时，ab_recommendation 保持 None，
            # 不影响既有回滚逻辑。
            ab_recommendation: str | None = None
            ab_decision = ""
            ab_winner = ""
            ab_experiment = ""
            try:
                from maop.core.evolution.ab_test import ABTestFramework, SPRTDecision
                ab_fw = ABTestFramework(root_dir=str(self._root))
                # 优先使用环境变量指定的实验名，否则取最新创建的实验。
                ab_experiment = os.getenv("MAOP_AB_EXPERIMENT", "").strip()
                if not ab_experiment:
                    experiments = ab_fw.list_experiments()
                    if experiments:
                        ab_experiment = experiments[0]
                if ab_experiment:
                    sprt_result = ab_fw.evaluate_sprt(ab_experiment)
                    ab_decision = sprt_result.decision.value
                    ab_winner = sprt_result.winner
                    if sprt_result.decision == SPRTDecision.ACCEPT_H1:
                        # treatment 显著胜出 → 建议推广（跳过回滚）。
                        ab_recommendation = "promote"
                        logger.info(
                            "[evo-loop] AB/SPRT: ACCEPT_H1 (winner=%s) → promote",
                            ab_winner,
                        )
                    elif sprt_result.decision == SPRTDecision.ACCEPT_H0:
                        # treatment 未胜出 → 建议回滚。
                        ab_recommendation = "rollback"
                        logger.info("[evo-loop] AB/SPRT: ACCEPT_H0 → rollback")
                    else:
                        # CONTINUE：数据不足，继续收集，不改变回滚决策。
                        ab_recommendation = "continue"
                        logger.info("[evo-loop] AB/SPRT: CONTINUE (insufficient data)")
            except Exception as ab_exc:
                # A/B 框架不可用或查询失败时不阻断 VALIDATE 主流程。
                logger.debug("[evo-loop] AB/SPRT evaluation skipped: %s", ab_exc)

            return PhaseResult(
                phase=LoopPhase.VALIDATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "improved": improved,
                    "baseline": baseline_errors,
                    "current": current_unhealed,
                    "ab_recommendation": ab_recommendation,
                    "ab_decision": ab_decision,
                    "ab_winner": ab_winner,
                    "ab_experiment": ab_experiment,
                },
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

