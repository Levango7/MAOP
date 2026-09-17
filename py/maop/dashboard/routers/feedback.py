"""Feedback router — 用户反馈评价系统.

提供对 agent / task / result 三类目标的评分、评论与标签管理.
所有已认证用户均可提交反馈; 查询/更新/删除按归属做 IDOR 防护,
导出与摘要聚合对 admin 放开.

Endpoints:
  POST   /api/feedback                — 提交反馈 (任何已认证用户)
  GET    /api/feedback                — 查询反馈列表 (非 admin 仅看自己)
  GET    /api/feedback/summary        — 反馈摘要 (平均评分/分布/标签词频)
  GET    /api/feedback/export         — 导出反馈 (admin only, csv/json)
  PUT    /api/feedback/{feedback_id}  — 更新反馈 (仅本人)
  DELETE /api/feedback/{feedback_id}  — 删除反馈 (本人或 admin)

数据存储: SQLite ``feedback`` 表 (位于统一 maop.db).

Business logic (schema init, CRUD, IDOR checks, CSV/JSON export) lives
in :mod:`maop.dashboard.services.chat_service`; this module only does
request parsing, auth, service dispatch, and response formatting.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Backward-compat aliases — tests import these names from the router
# module (e.g. ``feedback_router._reset_schema_for_tests()``). They
# delegate to the service so there is no duplicated state.
_VALID_TARGET_TYPES = chat_service.VALID_TARGET_TYPES
_FEEDBACK_COLUMNS = chat_service.FEEDBACK_COLUMNS


# ── Pydantic 请求模型 ─────────────────────────────────────────────


class FeedbackCreate(BaseModel):
    """提交反馈请求体."""

    target_type: str = Field(..., description="目标类型: agent|task|result")
    target_id: str = Field(..., min_length=1, max_length=255, description="目标 ID")
    rating: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: str = Field("", max_length=4000, description="评论(可选)")
    tags: list[str] = Field(default_factory=list, description="标签列表(可选)")


class FeedbackUpdate(BaseModel):
    """更新反馈请求体 — 所有字段可选."""

    rating: int | None = Field(None, ge=1, le=5, description="评分 1-5")
    comment: str | None = Field(None, max_length=4000, description="评论")
    tags: list[str] | None = Field(None, description="标签列表")


# ── 请求状态辅助函数 ─────────────────────────────────────────────


def _user_id_from_request(request: Request) -> str:
    """提取当前认证用户身份 (auth_identity)."""
    return getattr(request.state, "auth_identity", "") or ""


def _tenant_id_from_request(request: Request) -> str:
    """提取 tenant_id (多租户隔离)."""
    return getattr(request.state, "tenant_id", "") or ""


def _is_admin(request: Request) -> bool:
    """判断当前用户是否 admin / superadmin."""
    roles = getattr(request.state, "auth_roles", None) or []
    return bool({"admin", "superadmin"} & set(roles))


def _require_authenticated_user(request: Request) -> str:
    """要求请求已认证, 返回 user_id. 未认证 → 401."""
    user_id = _user_id_from_request(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user_id


def _validate_target_type(target_type: str) -> str:
    """校验 target_type 白名单."""
    if target_type not in _VALID_TARGET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target_type; expected one of {_VALID_TARGET_TYPES}",
        )
    return target_type


def _reset_schema_for_tests() -> None:
    """Reset schema init flag — used by unit tests.

    Thin shim over :func:`chat_service.reset_schema_for_tests` kept on
    the router module so tests can call ``feedback_router._reset_schema_for_tests()``
    without importing the service directly.
    """
    chat_service.reset_schema_for_tests()


def _ensure_schema() -> None:
    """Idempotent schema creation — delegates to the service."""
    chat_service.ensure_schema()


# ── Endpoints: 提交反馈 ──────────────────────────────────────────


@router.post("")
@handle_api_errors
async def create_feedback(body: FeedbackCreate, request: Request) -> dict[str, Any]:
    """提交反馈 — 任何已认证用户均可调用."""
    user_id = _require_authenticated_user(request)
    _validate_target_type(body.target_type)
    tenant_id = _tenant_id_from_request(request)

    feedback_id = chat_service.create_feedback(
        user_id=user_id,
        tenant_id=tenant_id,
        target_type=body.target_type,
        target_id=body.target_id,
        rating=body.rating,
        comment=body.comment,
        tags=body.tags,
    )
    return {"status": "ok", "feedback_id": feedback_id}


# ── Endpoints: 查询反馈列表 ──────────────────────────────────────


@router.get("")
@handle_api_errors
async def list_feedback(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    target_id: str = Query("", description="按目标 ID 过滤"),
    rating: int | None = Query(None, ge=1, le=5, description="按评分过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
    page: int = Query(1, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
) -> dict[str, Any]:
    """查询反馈列表 — 非 admin 用户只能看自己提交的反馈."""
    _require_authenticated_user(request)
    if target_type:
        _validate_target_type(target_type)

    result = chat_service.list_feedback(
        user_id=_user_id_from_request(request),
        is_admin=_is_admin(request),
        target_type=target_type,
        target_id=target_id,
        rating=rating,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return {"status": "ok", **result}


# ── Endpoints: 反馈摘要 ──────────────────────────────────────────


@router.get("/summary")
@handle_api_errors
async def get_summary(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    target_id: str = Query("", description="按目标 ID 过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
) -> dict[str, Any]:
    """反馈摘要 — 平均评分、评分分布、标签词频、评论数."""
    _require_authenticated_user(request)
    if target_type:
        _validate_target_type(target_type)

    summary = chat_service.get_feedback_summary(
        user_id=_user_id_from_request(request),
        is_admin=_is_admin(request),
        target_type=target_type,
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
    )
    return {"status": "ok", "summary": summary}


# ── Endpoints: 导出反馈 (admin only) ─────────────────────────────


@router.get("/export")
@handle_api_errors
async def export_feedback(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
    format: str = Query("csv", pattern="^(csv|json)$", description="导出格式"),
) -> Response:
    """导出反馈 — admin only. 支持 csv / json."""
    require_admin(request)
    if target_type:
        _validate_target_type(target_type)

    items = chat_service.export_feedback_rows(
        target_type=target_type,
        date_from=date_from,
        date_to=date_to,
    )

    if format == "json":
        payload = json.dumps(
            {"status": "ok", "count": len(items), "data": items},
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=feedback.json"},
        )

    # CSV 导出
    csv_text = chat_service.format_feedback_csv(items)
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"},
    )


# ── Endpoints: 更新 / 删除 (动态路由, 必须在静态路由之后) ────────
# WARNING: 路由顺序 — /summary 与 /export 必须在 /{feedback_id} 之前注册,
# 否则 FastAPI 会把 "summary" / "export" 当作 feedback_id 匹配.


@router.put("/{feedback_id}")
@handle_api_errors
async def update_feedback(
    feedback_id: str,
    body: FeedbackUpdate,
    request: Request,
) -> dict[str, Any]:
    """更新反馈 — 仅本人可更新自己的反馈."""
    _require_authenticated_user(request)
    try:
        item = chat_service.update_feedback(
            feedback_id,
            user_id=_user_id_from_request(request),
            is_admin=_is_admin(request),
            rating=body.rating,
            comment=body.comment,
            tags=body.tags,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found",
        )
    return {"status": "ok", "feedback": item}


@router.delete("/{feedback_id}")
@handle_api_errors
async def delete_feedback(feedback_id: str, request: Request) -> dict[str, Any]:
    """删除反馈 — 本人或 admin 可删除."""
    _require_authenticated_user(request)
    try:
        chat_service.delete_feedback(
            feedback_id,
            user_id=_user_id_from_request(request),
            is_admin=_is_admin(request),
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found",
        )
    return {"status": "ok", "deleted": True}
