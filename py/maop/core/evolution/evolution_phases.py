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

            self._merge_queued_suggestions(suggestions)

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

    def _merge_queued_suggestions(self, suggestions: list[dict[str, Any]]) -> None:
        """把建议队列里"尚未应用且本轮未重新产出"的条目并入本轮建议集。

        T3.1 修的链路断点：队列文件 ``data/evolve-suggestions.json`` 此前是
        **只写不读**——``_write_suggestions`` 会把新建议写进去，但 SUGGEST
        阶段从不读它，而 ConfigMutator.apply_suggestion 恰恰按 id 从这个文件
        取建议（config_mutator.py:63）。结果是：外部注入的建议（如
        ``maop evolution inject-degradation`` 的劣化演练条目）永远到不了
        EVALUATE/APPLY，演练链路形同虚设。

        合并规则（保守，避免把陈旧条目当新工作）：
          - 跳过 ``applied=True`` 的（已应用，重放无意义）；
          - 跳过本轮已重新产出的同 id 条目（ledger 生成的优先）；
          - 其余按队列顺序追加。
        """
        import json

        queue_file = self._root / "data" / "evolve-suggestions.json"
        if not queue_file.exists():
            return
        try:
            queued = json.loads(queue_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(queued, list):
            return

        current_ids = {s.get("id") for s in suggestions}
        for item in queued:
            if not isinstance(item, dict):
                continue
            sid = item.get("id")
            if not sid or sid in current_ids:
                continue
            if item.get("applied", False):
                continue
            suggestions.append(item)
            current_ids.add(sid)

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

            # T3.1-e：跨轮审批回流。人工批准过的建议，下一轮必须真的被应用——
            # 此前 approved_suggestions 只被写入（evolution_service.decide_evolution_approval）
            # 而无任何消费者，批准的建议要等同类错误再次触发、由规则引擎重新产出
            # 才可能落地，人工闸门形同"记录了但不执行"。
            # 处理方式：在策略决策**之后**把人工批准的条目直接并入 approved——
            # 人工决定高于策略判断（cooldown/限流都不应否决人的决定），
            # 但仍走同一 APPLY 路径（ConfigMutator 按 id 取实体 → 真实变更 →
            # 快照 → VALIDATE → 失败即回滚），不新增旁路。
            carried = self._carry_approved_from_last_cycle(
                approved,
                pending_approval,
                getattr(self, "_current_cycle_id", ""),
            )

            return PhaseResult(
                phase=LoopPhase.EVALUATE,
                success=True,
                duration_s=round(time.time() - start, 3),
                details={
                    "approved": approved,
                    "pending_approval": pending_approval,
                    "carried_over": carried,
                    "total": len(suggestions),
                    "approved_count": len(approved),
                    "pending_count": len(pending_approval),
                },
            )
        except Exception as exc:
            logger.warning("[evo-loop] Evaluate phase failed: %s", exc)
            return PhaseResult(phase=LoopPhase.EVALUATE, success=False, error=str(exc), duration_s=round(time.time() - start, 3), details={"approved": approved, "pending_approval": []})


    def _carry_approved_from_last_cycle(
        self,
        approved: list[dict[str, Any]],
        pending_approval: list[dict[str, Any]],
        current_cycle_id: str = "",
    ) -> list[str]:
        """把上一轮人工批准的建议并入本轮 approved（跨轮审批回流，T3.1-e）。

        读取**上一条** cycle 报告的 ``approved_suggestions``，从建议队列
        （``data/evolve-suggestions.json``——``ConfigMutator.apply_suggestion``
        也按 id 从这里取实体）取回建议元数据，绕过策略引擎直接放行。

        边界（刻意的）：
          - **只带仍存在于队列中的建议**：已被应用（applied=True）或从队列
            移除的不再重复放行；
          - **同一 id 已在 approved / pending 里则跳过**，不重复；
          - **仍走同一条 APPLY 路径**（真实变更 → 快照 → VALIDATE → 失败回滚），
            不新增旁路——回流改变的只是"能不能进 APPLY"，不是"APPLY 怎么做"；
          - 队列读取失败静默跳过（演化闭环不能因读不到建议文件而整体失败）。
        """
        import json

        try:
            # 历史读取由宿主注入（EvolutionLoop.__init__ 设 _cycle_history_reader）。
            # mixin 不假设宿主一定有 get_cycle_history——本类也被
            # PerformanceEvolutionLoop 等复用，那些宿主的历史表语义不同。
            reader = getattr(self, "_cycle_history_reader", None)
            if not callable(reader):
                return []
            last = None
            for candidate in reader(limit=5):
                # 跳过本轮自己（并发/同秒落库时 ORDER BY started_at 可能把自己排到前面）
                if current_cycle_id and candidate.cycle_id == current_cycle_id:
                    continue
                last = candidate
                break
            if last is None:
                return []
            carried_ids = list(getattr(last, "approved_suggestions", []) or [])
            if not carried_ids:
                return []
            queue_file = self._root / "data" / "evolve-suggestions.json"
            if not queue_file.exists():
                return []
            queued = json.loads(queue_file.read_text(encoding="utf-8"))
            if not isinstance(queued, list):
                return []
        except Exception as exc:
            logger.debug("[evo-loop] carry-over skipped: %s", exc)
            return []

        by_id = {
            item.get("id"): item
            for item in queued
            if isinstance(item, dict) and item.get("id")
        }
        if os.getenv("MAOP_DEBUG_CARRYOVER"):
            logger.info(
                "[evo-loop][debug] carry ids=%s queue_ids=%s applied_flags=%s",
                carried_ids,
                list(by_id),
                {k[:8]: v.get("applied") for k, v in by_id.items()},
            )
        existing = {a.get("suggestion_id") for a in approved} | {
            p.get("suggestion_id") for p in pending_approval
        }
        carried: list[str] = []
        for sid in carried_ids:
            item = by_id.get(sid)
            if item is None or item.get("applied", False):
                continue
            if sid in existing:
                # 该建议本轮已由 SUGGEST 队列合并带入（上一轮 T3.1 修的 merge 会让
                # 未应用建议跨轮存活并重新参与决策），因此这里不是"新引入"，而是
                # **把策略重新拦下的那一条按人工决定放行**：从 pending 移到 approved。
                # 这才是"人工批准高于策略"的落点——此前若一律跳过，批准过的建议
                # 会在下一轮再次回到 pending，人工闸门等于没批过。
                still_pending = [
                    p for p in pending_approval if p.get("suggestion_id") == sid
                ]
                if still_pending:
                    pending_approval.remove(still_pending[0])
                    approved.append({
                        "suggestion_id": sid,
                        "type": still_pending[0].get("type")
                        or item.get("mutation_type") or item.get("type", ""),
                        "severity": still_pending[0].get("severity")
                        or item.get("severity", ""),
                        "reason": "human-approved in a previous cycle (carried over)",
                        "human_approved": True,
                    })
                    carried.append(sid)
                continue
            approved.append({
                "suggestion_id": sid,
                "type": item.get("mutation_type") or item.get("type", ""),
                "severity": item.get("severity", ""),
                "reason": "human-approved in a previous cycle (carried over)",
                "human_approved": True,
            })
            existing.add(sid)
            carried.append(sid)
        if carried:
            logger.info(
                "[evo-loop] carried %d human-approved suggestion(s) from previous cycle: %s",
                len(carried),
                carried,
            )
        return carried

    def _phase_apply(self, approved: list[dict[str, Any]], dry_run: bool = False) -> PhaseResult:
        """Apply approved mutations. In dry_run mode, log proposed changes only.

        条目里的 ``human_approved=True`` 表示该建议来自跨轮人工审批回流
        （T3.1-e），豁免 ConfigMutator 的 auto_applicable 前置检查。
        """
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
                    result = engine.apply(sid, human_approved=bool(item.get("human_approved")))
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

