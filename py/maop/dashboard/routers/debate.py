"""MAOP Dashboard — Debate API endpoints (对抗辩论型 Multi-Agent).

Exposes the :class:`~maop.delegate.dispatch_debate.DebateDispatcher` so
operators can start a debate, query its verdict, and configure debate
parameters from the dashboard.

Endpoints
---------
- ``POST /api/debate/start`` — start a new debate (admin).
- ``GET /api/debate/{debate_id}`` — get debate verdict (read-only).
- ``GET /api/debate/{debate_id}/verdict`` — alias of above, explicit.
- ``POST /api/debate/config`` — configure debate parameters (admin).
- ``GET /api/debate/history`` — recent debate history (read-only).

POST endpoints require the ``admin`` role (mirrors supervisor router's
policy). GET endpoints are read-only and do not require admin auth.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debate", tags=["debate"])


# ── Request / response schemas ───────────────────────────────────


class DebateStartRequest(BaseModel):
    """POST /api/debate/start 请求体。"""

    question: str
    participants: list[str] = Field(min_length=3)
    context: dict[str, Any] = Field(default_factory=dict)
    routing_key: str = ""
    trace_id: str = ""
    max_rounds: int = 3
    consensus_threshold: float = 0.70


class DebateConfigRequest(BaseModel):
    """POST /api/debate/config 请求体。"""

    max_rounds: int = 3
    min_rounds: int = 1
    consensus_threshold: float = 0.70
    agent_timeout_s: float = 60.0
    round_timeout_s: float = 120.0
    max_debate_tokens: int = 50000
    early_exit_on_unanimous: bool = True
    retention_days: int = 30


# ── Singleton accessors ─────────────────────────────────────────


def _get_debate_dispatcher() -> Any:
    """Return the process-wide DebateDispatcher singleton.

    Lazily imports the singleton accessor from the reliability services
    container. When no dispatcher has been configured, raises 404 so the
    dashboard can show a "debate not enabled" message.
    """
    try:
        from maop.config.env import get_root_dir
        from maop.core.reliability.services import ServiceContainer

        root = str(get_root_dir(default="."))
        container = ServiceContainer(root_dir=root)
        dispatcher = container.get("debate_dispatcher", raise_on_failure=False)
        if dispatcher is None:
            raise HTTPException(
                status_code=404,
                detail="DebateDispatcher not configured. "
                       "Instantiate a DebateDispatcher and register it "
                       "with ServiceContainer to enable.",
            )
        return dispatcher
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"DebateDispatcher unavailable: {exc}",
        ) from exc


def _require_admin(request: Request) -> None:
    """Lightweight admin guard — mirrors supervisor router's pattern."""
    roles = getattr(getattr(request, "state", None), "auth_roles", None) or []
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="admin role required")


# ── Endpoints ───────────────────────────────────────────────────


@router.post("/start")
@handle_api_errors(
    "Debate start",
    error_value={"ok": False, "error": "Debate start failed"},
)
async def api_debate_start(
    request: Request,
    body: DebateStartRequest,
) -> dict[str, Any]:
    """Start a new debate (admin only).

    Body shape::

        {
          "question": "将 codegen 路由从 agent_a 切换到 agent_b 是否安全？",
          "participants": ["agent_a", "agent_b", "agent_c"],
          "context": {"metric": "...", "config_snapshot": "..."},
          "routing_key": "codegen",
          "max_rounds": 3,
          "consensus_threshold": 0.70
        }

    Returns the ``DebateVerdict`` as a dict.
    """
    _require_admin(request)
    dispatcher = _get_debate_dispatcher()
    from maop.delegate.dispatch_debate import DebateConfig

    config = DebateConfig(
        max_rounds=body.max_rounds,
        consensus_threshold=body.consensus_threshold,
    )
    try:
        verdict = await dispatcher.run_debate(
            body.question,
            body.participants,
            context=body.context,
            routing_key=body.routing_key,
            trace_id=body.trace_id,
            config_override=config,
        )
    except Exception as exc:
        logger.exception("[debate-api] start failed")
        raise HTTPException(
            status_code=500,
            detail=f"debate execution failed: {exc}",
        ) from exc
    return {"ok": True, "verdict": verdict.model_dump()}


@router.get("/history")
@handle_api_errors(
    "Debate history",
    error_value={"verdicts": [], "error": "Query failed"},
)
async def api_debate_history(limit: int = 20) -> dict[str, Any]:
    """Return recent debate history (read-only)."""
    dispatcher = _get_debate_dispatcher()
    verdicts = dispatcher.get_history(limit=limit)
    return {"verdicts": [v.model_dump() for v in verdicts]}


@router.get("/{debate_id}")
@handle_api_errors(
    "Debate get",
    error_value={"verdict": None, "error": "Debate not found"},
)
async def api_debate_get(debate_id: str) -> dict[str, Any]:
    """Get a debate's full verdict and trajectory (read-only, replayable)."""
    dispatcher = _get_debate_dispatcher()
    verdict = dispatcher.get_verdict(debate_id)
    if verdict is None:
        raise HTTPException(
            status_code=404,
            detail=f"debate {debate_id!r} not found",
        )
    return {"verdict": verdict.model_dump()}


@router.get("/{debate_id}/verdict")
@handle_api_errors(
    "Debate verdict",
    error_value={"verdict": None, "error": "Debate not found"},
)
async def api_debate_verdict(debate_id: str) -> dict[str, Any]:
    """Explicit alias of GET /api/debate/{debate_id}."""
    dispatcher = _get_debate_dispatcher()
    verdict = dispatcher.get_verdict(debate_id)
    if verdict is None:
        raise HTTPException(
            status_code=404,
            detail=f"debate {debate_id!r} not found",
        )
    return {"verdict": verdict.model_dump()}


@router.post("/config")
@handle_api_errors(
    "Debate config",
    error_value={"ok": False, "error": "Config update failed"},
)
async def api_debate_config(
    request: Request,
    body: DebateConfigRequest,
) -> dict[str, Any]:
    """Configure debate parameters (admin only).

    Updates the DebateDispatcher's runtime config in place.
    """
    _require_admin(request)
    dispatcher = _get_debate_dispatcher()
    from maop.delegate.dispatch_debate import DebateConfig

    new_config = DebateConfig(
        max_rounds=body.max_rounds,
        min_rounds=body.min_rounds,
        consensus_threshold=body.consensus_threshold,
        agent_timeout_s=body.agent_timeout_s,
        round_timeout_s=body.round_timeout_s,
        max_debate_tokens=body.max_debate_tokens,
        early_exit_on_unanimous=body.early_exit_on_unanimous,
        retention_days=body.retention_days,
    )
    # 更新 dispatcher 的 config（如果支持）
    try:
        dispatcher._config = new_config  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover
        logger.warning("[debate-api] config update failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"config update failed: {exc}",
        ) from exc
    logger.info(
        "[debate-api] config updated (max_rounds=%d, threshold=%.2f, by=%s)",
        new_config.max_rounds,
        new_config.consensus_threshold,
        getattr(getattr(request, "state", None), "auth_identity", "unknown"),
    )
    return {"ok": True, "config": new_config.model_dump()}


__all__ = ["router"]