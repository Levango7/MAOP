"""MAOP Dashboard — Scheduling API endpoints (F1-02 异常自适应调度).

Exposes the :class:`~maop.core.scheduling.failure_detector.FailurePatternDetector`
state so operators can answer "which agents are drained / recovering and
why?" from the dashboard.

Endpoints
---------
- ``GET /api/scheduling/failure-stats`` — per-agent health snapshot
  (failure rate, weight, status, window size, …) plus detector config.
- ``POST /api/scheduling/failure-stats/reset`` — clear recorded state
  for one agent (body: ``{"agent_id": "..."}``) or all agents (empty
  body). Admin-only.

GET endpoints are read-only and do not require admin auth.
POST endpoints require the ``admin`` role (via ``require_admin`` middleware).

业务逻辑已提取至 ``maop.dashboard.services.scheduling_service``（§1）。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import scheduling_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])


class FailureStatsResetRequest(BaseModel):
    """重置失败检测器状态请求体."""

    agent_id: str | None = Field(default=None, max_length=200)


# ── 单例转发（供测试注入，转发到 service 层）──────────────────────
def _detector() -> Any:
    """Return the process-wide detector singleton (透传到 service)."""
    return scheduling_service._get_detector()


@router.get("/failure-stats")
@handle_api_errors(
    "Scheduling failure stats",
    error_value={"agents": [], "config": {}, "total_agents": 0, "error": "Query failed"},
)
async def api_scheduling_failure_stats(request: Request) -> dict[str, Any]:
    """Return the per-agent failure-detector snapshot.

    Response shape::

        {
          "agents": [
            {"agent_id": "...", "failure_rate": 0.0, "avg_latency": 0.0,
             "timeout_rate": 0.0, "weight": 1.0, "status": "normal",
             "window_size": 0, "total_recorded": 0},
            ...
          ],
          "config": {"window_size": 50, "failure_rate_threshold": 0.3,
                     "timeout_threshold": 30.0,
                     "recovery_consecutive_successes": 5},
          "total_agents": 0
        }
    """
    require_admin(request)
    # M-1 fix: 包裹为 {status, data} 统一响应格式。
    return {"status": "ok", "data": scheduling_service.get_failure_stats()}


@router.post("/failure-stats/reset")
@handle_api_errors(
    "Scheduling failure stats reset",
    error_value={"status": "error", "error": "Reset failed"},
)
async def api_scheduling_failure_stats_reset(
    request: Request,
    body: FailureStatsResetRequest | None = None,
) -> dict[str, Any]:
    """Clear failure-detector state.

    Body ``{"agent_id": "..."}`` resets only the named agent; an empty
    body (or ``agent_id`` omitted) resets all agents. Intended for
    operations — manual recovery without waiting for the grey-probe
    ladder.
    """
    require_admin(request)
    agent_id = (body.agent_id if body and body.agent_id else "").strip() or None
    scheduling_service.reset_failure_stats(agent_id)
    logger.info(
        "[scheduling-api] failure-detector reset (agent_id=%s, by=%s)",
        agent_id or "ALL",
        getattr(getattr(request, "state", None), "auth_identity", "unknown"),
    )
    return {"status": "ok", "reset_agent": agent_id or "ALL"}


__all__ = ["router"]
