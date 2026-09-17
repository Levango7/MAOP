"""Self-evolution endpoints for MAOP Dashboard.

业务逻辑由 ``maop.dashboard.services.evolution_service`` 提供；本模块
仅负责路由定义、请求解析、权限检查、调用 service、响应格式化与错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import evolution_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (批次3A: 输入校验) ───────────────────────────
class EvolveAnalyzeRequest(BaseModel):
    """POST /api/evolve/analyze 请求体。"""
    action: str = ""
    suggestion_id: str = ""
    hours: int = 24


class EvolutionLoopTriggerRequest(BaseModel):
    """POST /api/evolution/loop/trigger 请求体。"""
    dry_run: bool = True


class EvolutionApprovalDecisionRequest(BaseModel):
    """POST /api/evolution/approvals/{approval_id}/decision 请求体。"""
    decision: str = ""
    approved_by: str = "admin"
    reason: str = ""


class EvolutionLoopRollbackRequest(BaseModel):
    """POST /api/evolution/loop/rollback 请求体。"""
    cycle_id: str = ""
    snapshot_id: str = ""


class EvolveApplySuggestionRequest(BaseModel):
    """POST /api/evolve/apply-suggestion 请求体。"""
    suggestion_id: str = ""

@router.get("/api/evolve/status")
@handle_api_errors("Evolve status", error_value={"status": "error", "error": "Evolve status unavailable"})
async def api_evolve_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    return evolution_service.get_evolve_status()


@router.get("/api/evolve/metrics")
@handle_api_errors("Evolve metrics", error_value={"timeseries": [], "heatmap": [], "lineage": []})
async def api_evolve_metrics(request: Request) -> dict[str, Any]:
    """演化指标聚合（时间序列 / 热力图 / 世系）。

    从 evolution_cycles 表聚合真实数据；优先 EvolutionLoop，空回退 EvolveEngine。
    """
    require_admin(request)
    return evolution_service.get_evolve_metrics()


@router.post("/api/evolve/analyze")
@handle_api_errors("Evolve analyze", error_value={"status": "error", "error": "Evolve analyze unavailable"})
async def api_evolve_analyze(request: Request, body: EvolveAnalyzeRequest) -> dict[str, Any]:
    # 批次3A: 用 Pydantic EvolveAnalyzeRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（action/suggestion_id/hours 字段类型）。
    require_admin(request)
    return evolution_service.analyze_evolve(body.action, body.suggestion_id, body.hours)


@router.get("/api/evolve/suggestions")
@handle_api_errors("Evolve suggestions", error_value={"status": "error", "error": "Evolve suggestions unavailable", "suggestions": {"stats": {"by_agent": []}}})
async def api_evolve_suggestions(request: Request) -> dict[str, Any]:
    require_admin(request)
    return evolution_service.get_evolve_suggestions()


@router.get("/api/evolve/report")
@handle_api_errors("Evolve report", error_value={"performance": [], "error": "Evolve report unavailable"})
async def api_evolve_report_v4(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await evolution_service.get_evolve_report()


@router.get("/api/evolve/strategies")
@handle_api_errors("Evolve strategies", error_value={"status": "error", "strategies": []})
async def api_evolve_strategies(request: Request) -> dict[str, Any]:
    """返回可用进化策略列表。"""
    require_admin(request)
    return evolution_service.list_evolve_strategies()


@router.get("/api/evolve/history")
@handle_api_errors("Evolve history", error_value={"status": "error", "history": []})
async def api_evolve_history(request: Request) -> dict[str, Any]:
    """返回进化循环历史。"""
    require_admin(request)
    return evolution_service.get_evolve_history()


@router.get("/api/evolve/suggestions-list")
@handle_api_errors("Evolve suggestions list", error_value={"status": "error", "suggestions": []})
async def api_evolve_suggestions_list(request: Request) -> dict[str, Any]:
    """返回所有进化建议列表 (含已应用状态)。"""
    require_admin(request)
    return evolution_service.list_evolve_suggestions()


@router.post("/api/evolve/apply-suggestion")
@handle_api_errors("Evolve apply suggestion", error_value={"status": "error", "error": "Apply failed"})
async def api_evolve_apply_suggestion(request: Request, body: EvolveApplySuggestionRequest) -> dict[str, Any]:
    """手动应用指定进化建议。"""
    require_admin(request)
    # H-3 fix: 用 Pydantic EvolveApplySuggestionRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（suggestion_id 字段类型）。
    return evolution_service.apply_evolve_suggestion(body.suggestion_id)


# ════════════════════════════════════════════════════════════════════
# v5.2.0 F2-01: Evolution Loop AC-07 Dashboard Endpoints
# ═══════════════════════════════════════════════════════════════════

@router.get("/api/evolution/loop/status")
@handle_api_errors("Evolution loop status", error_value={"status": "error", "error": "Status unavailable"})
async def api_evolution_loop_status(request: Request) -> dict[str, Any]:
    """闭环状态机当前状态 + 最近 cycle 摘要 (AC-07).

    返回：
    - 当前状态机状态 (idle/running/pending_approval/validating/rolling_back)
    - 最近 cycle 摘要 (cycle_id, started_at, errors, suggestions, validation, rolled_back)
    - 待审批数量
    - 开关状态 (MAOP_EVOLUTION_LOOP_ENABLED)
    """
    require_admin(request)
    try:
        return evolution_service.get_evolution_loop_status()
    except Exception as exc:
        # H-2 fix: 异常分支应返回 500，而非 200 + status=error。
        raise HTTPException(status_code=500, detail="Evolution loop status unavailable") from exc


@router.post("/api/evolution/loop/trigger")
@handle_api_errors("Evolution loop trigger", error_value={"status": "error", "error": "Trigger failed"})
async def api_evolution_loop_trigger(request: Request, body: EvolutionLoopTriggerRequest) -> dict[str, Any]:
    """手动触发一轮闭环（支持 dry_run）（AC-07）."""
    # 批次3A: 用 Pydantic EvolutionLoopTriggerRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（dry_run 字段类型）。
    require_admin(request)
    try:
        return await evolution_service.trigger_evolution_loop(body.dry_run)
    except Exception as exc:
        # H-2 fix: 异常分支应返回 500，而非 200 + status=error。
        raise HTTPException(status_code=500, detail="Evolution loop trigger failed, please try again later") from exc


@router.get("/api/evolution/approvals")
@handle_api_errors("Evolution approvals", error_value={"status": "error", "approvals": []})
async def api_evolution_approvals(request: Request) -> dict[str, Any]:
    """待审批改进列表 (AC-07).

    返回所有处于 pending_approval 状态的建议，含建议详情和上下文。
    """
    require_admin(request)
    try:
        return evolution_service.list_evolution_approvals()
    except Exception as exc:
        # H-2 fix: 异常分支应返回 500，而非 200 + status=error。
        raise HTTPException(status_code=500, detail="Evolution approvals unavailable") from exc


@router.post("/api/evolution/approvals/{approval_id}/decision")
@handle_api_errors("Evolution approval decision", error_value={"status": "error", "error": "Decision failed"})
async def api_evolution_approval_decision(approval_id: str, request: Request, body: EvolutionApprovalDecisionRequest) -> dict[str, Any]:
    """审批通过 / 拒绝 (AC-07).

    approval_id 格式：cycle_id:suggestion_id
    body: {"decision": "approve"|"reject", "approved_by": "username", "reason": "..."}
    """
    # 批次3A: 用 Pydantic EvolutionApprovalDecisionRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（decision/approved_by/reason 字段类型）。
    require_admin(request)
    try:
        return evolution_service.decide_evolution_approval(
            approval_id, body.decision, body.approved_by, body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Cycle {exc.args[0]} not found") from exc
    except Exception as exc:
        logger.warning("Evolution approval decision failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Evolution approval decision failed, please try again later") from exc


@router.get("/api/evolution/ab/{cycle_id}")
@handle_api_errors("Evolution A/B results", error_value={"status": "error", "ab_result": None})
async def api_evolution_ab_results(request: Request, cycle_id: str) -> dict[str, Any]:
    """A/B 结果与显著性检验数据 (AC-06/AC-07).

    返回：
    - A/B 实验配置
    - 变体统计
    - Z 检验 p-value
    - SPRT 状态（如适用）
    """
    require_admin(request)
    try:
        return evolution_service.get_evolution_ab_results(cycle_id)
    except Exception as exc:
        # H-2 fix: 异常分支应返回 500，而非 200 + status=error。
        raise HTTPException(status_code=500, detail="Evolution A/B results unavailable") from exc


@router.post("/api/evolution/loop/rollback")
@handle_api_errors("Evolution loop rollback", error_value={"status": "error", "error": "Rollback failed"})
async def api_evolution_loop_rollback(request: Request, body: EvolutionLoopRollbackRequest) -> dict[str, Any]:
    """手动触发回滚 (AC-05).

    body: {"cycle_id": "...", "snapshot_id": "..."} (snapshot_id 可选)
    """
    # 批次3A: 用 Pydantic EvolutionLoopRollbackRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（cycle_id/snapshot_id 字段类型）。
    require_admin(request)
    try:
        return evolution_service.rollback_evolution_loop(body.cycle_id, body.snapshot_id)
    except ValueError as exc:
        # P2 fix: 400 应返回 400 状态码，而非 200。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Evolution rollback failed: %s", exc, exc_info=True)
        # H-2 fix: 异常分支应返回 500，而非 200 + status=error。
        raise HTTPException(status_code=500, detail="Evolution rollback failed, please try again later") from exc
