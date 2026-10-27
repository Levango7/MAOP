"""Evolution Loop AC-07 端点子域 — 闭环状态/触发/审批/AB结果/回滚.

从 ``evolution_service`` 拆分（file-size ratchet）。包含 F2-01
Evolution Loop AC-07 Dashboard Endpoints 段：

- ``get_evolution_loop_status`` — 闭环状态机 + 最近 cycle 摘要
- ``trigger_evolution_loop`` — 手动触发一轮闭环（dry_run）
- ``list_evolution_approvals`` — 待审批改进列表
- ``decide_evolution_approval`` — 审批通过/拒绝
- ``get_evolution_ab_results`` — A/B 结果与显著性检验
- ``rollback_evolution_loop`` — 手动触发回滚

``_evo_loop`` 工厂函数定义在父 ``evolution_service`` 中，本模块
通过 ``from maop.dashboard.services.evolution_service import _evo_loop``
在调用时引用（父模块的 forwarding import 在本模块导入时已定义完
``_evo_loop``，无循环依赖）。

``evolution_service`` forwards every public name below, so callers
and tests keep importing from the parent module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

# Shared runtime state — same as parent evolution_service
from maop.dashboard.routers import state

logger = logging.getLogger(__name__)


def _evo_loop() -> Any:
    """EvolutionLoop 实例（ErrorLedger 驱动的七段闭环，写入 evolution_cycles 表）。

    与父 ``evolution_service._evo_loop`` 保持同一实现；此处本地定义
    避免循环 import（父 forwarding import 在本模块导入时尚未执行完）。
    """
    from maop.core.evolution.evolution_loop import EvolutionLoop

    return EvolutionLoop(root_dir=str(state.MAOP_ROOT))


# ════════════════════════════════════════════════════════════════════════════
# evolve_insights: F2-01 Evolution Loop AC-07 Dashboard Endpoints
# ════════════════════════════════════════════════════════════════════════════

def get_evolution_loop_status() -> dict[str, Any]:
    """闭环状态机当前状态 + 最近 cycle 摘要 (AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=5)
        stats = loop.get_stats()
        evolution_enabled = os.getenv("MAOP_EVOLUTION_LOOP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")

        # 确定当前状态机状态
        loop_state = "idle"
        if history:
            latest = history[0]
            if latest.rolled_back:
                loop_state = "rolled_back"
            elif latest.validation_improved:
                loop_state = "validated"
            elif latest.pending_approval:
                loop_state = "pending_approval"
            elif latest.suggestions_applied > 0:
                loop_state = "applying"
            else:
                loop_state = "evaluating"

        return {
            "status": "ok",
            "state": loop_state,
            "evolution_loop_enabled": evolution_enabled,
            "recent_cycles": [h.model_dump() for h in history],
            "stats": stats,
            "pending_approval_count": len(history[0].pending_approval) if history else 0,
            "pending_approval_ids": history[0].pending_approval if history else [],
        }
    except Exception as exc:
        logger.warning("Evolution loop status failed: %s", exc, exc_info=True)
        raise


async def trigger_evolution_loop(dry_run: bool) -> dict[str, Any]:
    """手动触发一轮闭环（支持 dry_run）（AC-07）.

    T3.0 修复：``run_cycle`` 是同步阻塞实现（SQLite + 文件快照 + 各阶段执行），
    此前直接 ``await`` 它必然 TypeError → 端点恒 500。现放线程池执行，
    既修哑弹又不阻塞事件循环。

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        report = await asyncio.to_thread(loop.run_cycle, dry_run=dry_run, auto_rollback=True)
        return {"status": "ok", "report": report.model_dump()}
    except Exception as exc:
        logger.warning("Evolution loop trigger failed: %s", exc, exc_info=True)
        raise


def list_evolution_approvals() -> dict[str, Any]:
    """待审批改进列表 (AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    try:
        loop = _evo_loop()
        history = loop.get_cycle_history(limit=10)

        approvals = []
        for h in history:
            if h.pending_approval:
                approvals.append({
                    "cycle_id": h.cycle_id,
                    "started_at": h.started_at,
                    "pending_approval_ids": h.pending_approval,
                    "approval_state": h.approval_state,
                    "errors_observed": h.errors_observed,
                    "suggestions_generated": h.suggestions_generated,
                    "validation_improved": h.validation_improved,
                })

        return {"status": "ok", "approvals": approvals, "total": len(approvals)}
    except Exception as exc:
        logger.warning("Evolution approvals failed: %s", exc, exc_info=True)
        raise


def decide_evolution_approval(
    approval_id: str,
    decision: str,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    """审批通过 / 拒绝 (AC-07).

    ``approval_id`` 格式：``cycle_id:suggestion_id``。
    Raises ``ValueError`` for invalid ``approval_id`` format, unknown
    ``decision`` value, or a suggestion not pending in that cycle
    (router maps to 400).
    Raises ``KeyError`` if the cycle is not found (router maps to 404).
    Raises the underlying exception on persistence failure (router maps to 500).
    """
    try:
        cycle_id, suggestion_id = approval_id.split(":", 1)
    except ValueError as exc:
        raise ValueError("Invalid approval_id format (cycle_id:suggestion_id)") from exc

    normalized_decision = decision.lower()
    if normalized_decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")

    loop = _evo_loop()
    # T3.0 修复：此前调用不存在的 ``loop._load_report``（AttributeError → 端点
    # 恒 500），且 approve 分支是 ``pass``——决策根本不落库，闭环断链。
    # 现在走公开 API 读取/更新：suggestion 从 pending 挪入 approved/rejected
    # 两支，全部决策完后 approval_state 收敛（枚举语义见 LoopReport 注释）。
    report = loop.get_report(cycle_id)
    if report is None:
        raise KeyError(cycle_id)

    if suggestion_id not in report.pending_approval:
        raise ValueError(
            f"Suggestion {suggestion_id!r} is not pending approval in cycle {cycle_id!r}"
        )

    if normalized_decision == "approve":
        report.approved_suggestions.append(suggestion_id)
    else:
        report.rejected_suggestions.append(suggestion_id)
    report.pending_approval = [sid for sid in report.pending_approval if sid != suggestion_id]

    if report.pending_approval:
        report.approval_state = "pending"
    elif report.approved_suggestions and report.rejected_suggestions:
        report.approval_state = "partial"
    elif report.approved_suggestions:
        report.approval_state = "approved"
    else:
        report.approval_state = "rejected"

    report.approved_by = approved_by
    report.approved_at = time.time()
    if not loop.update_report(report):
        # 并发窗口内该 cycle 被删除——按 404 语义处理
        raise KeyError(cycle_id)

    return {
        "status": "ok",
        "decision": normalized_decision,
        "approval_id": approval_id,
        "cycle_id": cycle_id,
        "approval_state": report.approval_state,
    }


def get_evolution_ab_results(cycle_id: str) -> dict[str, Any]:
    """A/B 结果与显著性检验数据 (AC-06/AC-07).

    Raises on failure so the router can map to HTTP 500.
    """
    from maop.core.evolution.ab_test import ABTestManager

    try:
        _evo_loop()
        ab_manager = ABTestManager(root_dir=str(state.MAOP_ROOT))

        # 查找该 cycle 的 A/B 实验
        # 简化：查找 experiment 名称为 "evo-{cycle_id}" 的实验
        exp_name = f"evo-{cycle_id}"
        try:
            result = ab_manager.evaluate(exp_name)
            # 修复：EvaluationResult 并没有 control_rate / control_count /
            # treatment_rate / treatment_count 字段——A/B 数据实际在
            # `variants`（list[VariantStats]，含 name/samples/successes/
            # success_rate）。此前读取不存在的属性会抛 AttributeError，
            # 又被下方 except Exception 吞掉，导致 ab_result 恒为 None，
            # 即 **A/B 评估结果一直是空**（静默失效，无报错）。
            variants = {v.name: v for v in result.variants}
            control = variants.get("control")
            treatment = variants.get("treatment")
            ab_result = {
                "experiment": exp_name,
                "p_value": result.p_value,
                "significant": result.p_value < 0.05,
                "control": {
                    "success_rate": control.success_rate if control else 0.0,
                    "samples": control.samples if control else 0,
                },
                "treatment": {
                    "success_rate": treatment.success_rate if treatment else 0.0,
                    "samples": treatment.samples if treatment else 0,
                },
            }
        except Exception:
            ab_result = None

        return {
            "status": "ok",
            "cycle_id": cycle_id,
            "ab_result": ab_result,
        }
    except Exception as exc:
        logger.warning("Evolution A/B results failed: %s", exc, exc_info=True)
        raise


def rollback_evolution_loop(cycle_id: str, snapshot_id: str) -> dict[str, Any]:
    """手动触发回滚 (AC-05).

    Raises ``ValueError`` if ``cycle_id`` is empty (router maps to 400).
    Raises the underlying exception on failure (router maps to 500).
    """
    if not cycle_id:
        raise ValueError("cycle_id required")

    loop = _evo_loop()
    restored = loop.rollback_cycle(cycle_id, snapshot_id=snapshot_id)
    return {"status": "ok", "restored_files": restored, "cycle_id": cycle_id}


__all__ = [
    "decide_evolution_approval",
    "get_evolution_ab_results",
    "get_evolution_loop_status",
    "list_evolution_approvals",
    "rollback_evolution_loop",
    "trigger_evolution_loop",
]
