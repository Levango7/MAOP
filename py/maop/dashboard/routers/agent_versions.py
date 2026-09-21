"""Agent 版本管理 / 灰度发布路由 — 版本 CRUD + 激活 + 回滚 + 灰度 + 指标.

提供 Agent 版本的全生命周期管理:

  POST   /api/agent-versions                       — 创建新版本 (admin)
  GET    /api/agent-versions                       — 列出版本 (已认证, 支持过滤+分页)
  GET    /api/agent-versions/{version_id}          — 获取版本详情 (已认证)
  PUT    /api/agent-versions/{version_id}          — 更新版本信息 (admin)
  DELETE /api/agent-versions/{version_id}          — 删除版本 (admin)
  POST   /api/agent-versions/{version_id}/activate — 激活版本 (全量切换, admin)
  POST   /api/agent-versions/{version_id}/rollback — 回滚到指定版本 (admin)
  POST   /api/agent-versions/{version_id}/canary   — 配置灰度发布 (admin)
  GET    /api/agent-versions/{version_id}/canary   — 查询灰度发布状态 (已认证)
  GET    /api/agent-versions/{version_id}/metrics  — 查询版本指标 (已认证)

数据存储: SQLite ``agent_versions`` 表 + ``agent_version_canaries`` 表
(位于统一 maop.db).

安全:
  - 写操作 (create/update/delete/activate/rollback/canary) 需 admin 角色.
  - 读操作 (list/get/canary 查询/metrics) 需已认证.
  - IDOR 防护: 非 admin 用户只能查看自己创建的版本 (created_by 过滤).
  - 异常脱敏: 由 ``handle_api_errors`` 统一处理.

业务逻辑已提取至 ``maop.dashboard.services.agent_versions_service``。
本 router 仅保留：路由定义 / 请求参数解析 / 权限检查 /
service 调用 / 响应格式化 / 错误处理。Pydantic 模型与辅助函数
（schema 初始化 / 行↔dict 转换 / 请求状态提取）保留在
``agent_versions_helpers`` 中。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.routers.agent_versions_helpers import (
    CanaryConfig,
    RollbackRequest,
    VersionCreate,
    VersionUpdate,
    _is_admin,
    _require_authenticated_user,
    _reset_schema_for_tests,  # noqa: F401
    _tenant_id_from_request,
    _user_id_from_request,
)
from maop.dashboard.services import agent_versions_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-versions", tags=["agent-versions"])


# ── Endpoints: 创建版本 ──────────────────────────────────────────


@router.post("")
@handle_api_errors
async def create_version(
    body: VersionCreate, request: Request
) -> dict[str, Any]:
    """创建新版本 — admin only. 版本初始状态为 draft."""
    require_admin(request)
    user_id = _user_id_from_request(request)
    tenant_id = _tenant_id_from_request(request)

    result = agent_versions_service.create_version(
        agent_id=body.agent_id,
        version_number=body.version_number,
        config_snapshot=body.config_snapshot,
        changelog=body.changelog,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    return {"status": "ok", "data": result}


# ── Endpoints: 列出版本 ──────────────────────────────────────────


@router.get("")
@handle_api_errors
async def list_versions(
    request: Request,
    agent_id: str = Query("", description="按 Agent ID 过滤"),
    status_filter: str = Query(
        "", alias="status", description="按状态过滤 (draft/active/retired/deleted)"
    ),
    page: int = Query(1, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
) -> dict[str, Any]:
    """列出版本 — 非 admin 用户只能看自己创建的版本."""
    _require_authenticated_user(request)

    try:
        result = agent_versions_service.list_versions(
            agent_id=agent_id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            is_admin=_is_admin(request),
            user_id=_user_id_from_request(request),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"status": "ok", **result}


# ── Endpoints: 获取版本详情 ──────────────────────────────────────


@router.get("/{version_id}")
@handle_api_errors
async def get_version(version_id: str, request: Request) -> dict[str, Any]:
    """获取版本详情 — 已认证, IDOR 防护."""
    _require_authenticated_user(request)
    try:
        item = agent_versions_service.get_version(
            version_id,
            is_admin=_is_admin(request),
            user_id=_user_id_from_request(request),
        )
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return {"status": "ok", "data": item}


# ── Endpoints: 更新版本 ──────────────────────────────────────────


@router.put("/{version_id}")
@handle_api_errors
async def update_version(
    version_id: str,
    body: VersionUpdate,
    request: Request,
) -> dict[str, Any]:
    """更新版本信息 — admin only."""
    require_admin(request)
    try:
        item = agent_versions_service.update_version(
            version_id,
            version_number=body.version_number,
            config_snapshot=body.config_snapshot,
            changelog=body.changelog,
        )
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return {"status": "ok", "data": item}


# ── Endpoints: 删除版本 ──────────────────────────────────────────


@router.delete("/{version_id}")
@handle_api_errors
async def delete_version(version_id: str, request: Request) -> dict[str, Any]:
    """删除版本 — admin only. 软删除: 状态置为 deleted."""
    require_admin(request)
    try:
        agent_versions_service.delete_version(version_id)
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    except agent_versions_service.VersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"status": "ok", "data": {"deleted": True}}


# ── Endpoints: 激活版本 (全量切换) ───────────────────────────────


@router.post("/{version_id}/activate")
@handle_api_errors
async def activate_version(version_id: str, request: Request) -> dict[str, Any]:
    """激活版本 — 全量切换. 同一 agent 的其他 active 版本自动转为 retired."""
    require_admin(request)
    try:
        item = agent_versions_service.activate_version(version_id)
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    except agent_versions_service.VersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"status": "ok", "data": item}


# ── Endpoints: 回滚到指定版本 ────────────────────────────────────


@router.post("/{version_id}/rollback")
@handle_api_errors
async def rollback_version(
    version_id: str,
    body: RollbackRequest,
    request: Request,
) -> dict[str, Any]:
    """回滚到指定版本 — 将 version_id 置为 retired, target_version_id 置为 active."""
    require_admin(request)
    try:
        result = agent_versions_service.rollback_version(
            version_id,
            target_version_id=body.target_version_id,
            reason=body.reason,
        )
    except agent_versions_service.VersionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except agent_versions_service.VersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"status": "ok", **result}


# ── Endpoints: 配置灰度发布 ──────────────────────────────────────


@router.post("/{version_id}/canary")
@handle_api_errors
async def configure_canary(
    version_id: str,
    body: CanaryConfig,
    request: Request,
) -> dict[str, Any]:
    """配置灰度发布 — admin only. 同一版本同时只允许一个 active 灰度."""
    require_admin(request)
    user_id = _user_id_from_request(request)
    try:
        canary = agent_versions_service.configure_canary(
            version_id,
            percentage=body.percentage,
            target_user_groups=body.target_user_groups,
            strategy=body.strategy,
            duration_seconds=body.duration_seconds,
            user_id=user_id,
        )
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    except agent_versions_service.VersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except agent_versions_service.CanaryConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return {"status": "ok", "data": canary}


# ── Endpoints: 查询灰度发布状态 ──────────────────────────────────


@router.get("/{version_id}/canary")
@handle_api_errors
async def get_canary(version_id: str, request: Request) -> dict[str, Any]:
    """查询灰度发布状态 — 已认证. 返回该版本所有灰度记录."""
    _require_authenticated_user(request)
    # IDOR 校验：先通过 get_version 确认访问权
    try:
        agent_versions_service.get_version(
            version_id,
            is_admin=_is_admin(request),
            user_id=_user_id_from_request(request),
        )
        result = agent_versions_service.get_canary(version_id)
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return {"status": "ok", **result}


# ── Endpoints: 查询版本指标 ──────────────────────────────────────


@router.get("/{version_id}/metrics")
@handle_api_errors
async def get_version_metrics(version_id: str, request: Request) -> dict[str, Any]:
    """查询版本指标 — 已认证.

    返回该版本与同 agent 当前 active 版本的对比指标
    (成功率、平均延迟、错误率). 个人版无真实指标数据时优雅降级返回零值.
    """
    _require_authenticated_user(request)
    try:
        # IDOR 校验 + 指标查询合并：get_version_metrics 内部会校验版本存在性，
        # 但 IDOR 校验需要 request 上下文，因此先调用 get_version 确认访问权。
        agent_versions_service.get_version(
            version_id,
            is_admin=_is_admin(request),
            user_id=_user_id_from_request(request),
        )
        metrics = agent_versions_service.get_version_metrics(version_id)
    except agent_versions_service.VersionNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return {"status": "ok", "data": metrics}
