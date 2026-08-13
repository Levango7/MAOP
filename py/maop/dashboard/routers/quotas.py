"""Enterprise quota router — exposes QuotaManager via FastAPI endpoints.

实现 PRD ``docs/prd-tenant-quota.md`` 的配额管理 API:

  - ``POST   /api/quotas/{tenant_id}/{resource}``         — 设置配额
  - ``GET    /api/quotas/{tenant_id}``                    — 列出所有配额
  - ``GET    /api/quotas/{tenant_id}/{resource}``         — 查询单个配额
  - ``PUT    /api/quotas/{tenant_id}/{resource}``         — 更新配额
  - ``DELETE /api/quotas/{tenant_id}/{resource}``         — 删除配额
  - ``GET    /api/quotas/{tenant_id}/usage``              — 列出使用量
  - ``GET    /api/quotas/{tenant_id}/{resource}/usage``   — 查询单个使用量
  - ``POST   /api/quotas/{tenant_id}/{resource}/usage``   — 增量更新使用量
  - ``POST   /api/quotas/{tenant_id}/{resource}/check``   — 检查配额
  - ``POST   /api/quotas/{tenant_id}/{resource}/consume`` — 检查+消费
  - ``GET    /api/quotas/{tenant_id}/alerts``             — 列出告警
  - ``POST   /api/quotas/alerts/{alert_id}/resolve``      — 解决告警

所有操作要求 admin 角色(``require_admin``) + ``FeatureFlag.TENANT_ISOLATION``.

路由注册顺序注意: 固定段路径(``/alerts/...``, ``/{tenant_id}/usage``,
``/{tenant_id}/alerts``)必须在参数段路径(``/{tenant_id}/{resource}``)之前
注册,否则 ``/t1/usage`` 会被 ``/{tenant_id}/{resource}`` 匹配为
tenant_id=t1, resource=usage.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from maop.config.edition import FeatureFlag, has_feature
from maop.core.backends.db_utils import unified_db_path
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quotas", tags=["quotas"])

_quota_manager: Any = None


def _get_manager() -> Any:
    """惰性初始化 QuotaManager 单例(共享 unified maop.db)."""
    global _quota_manager
    if _quota_manager is None:
        from maop.enterprise.quota import QuotaManager
        _quota_manager = QuotaManager(unified_db_path())
    return _quota_manager


def _require_tenant_isolation() -> None:
    """企业版特性守卫: Personal 版返回 404."""
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )


# ── 请求模型 ──────────────────────────────────────────────────────


class SetQuotaRequest(BaseModel):
    """设置配额请求体."""

    hard_limit: int = Field(ge=0, le=10**12)
    soft_limit: int = Field(default=0, ge=0, le=10**12)
    period: str = Field(default="total", pattern="^(daily|total)$")


class UpdateQuotaRequest(BaseModel):
    """更新配额请求体(所有字段可选)."""

    hard_limit: int | None = Field(default=None, ge=0, le=10**12)
    soft_limit: int | None = Field(default=None, ge=0, le=10**12)
    period: str | None = Field(default=None, pattern="^(daily|total)$")


class UpdateUsageRequest(BaseModel):
    """增量更新使用量请求体."""

    amount: int = Field(ge=-10**12, le=10**12)
    period: str = Field(default="total", pattern="^(daily|total)$")


class SetUsageRequest(BaseModel):
    """绝对设置使用量请求体."""

    value: int = Field(ge=0, le=10**12)
    period: str = Field(default="total", pattern="^(daily|total)$")


class CheckRequest(BaseModel):
    """配额检查请求体."""

    amount: int = Field(default=1, ge=1, le=10**12)


# ── 告警端点(固定段路径,必须先注册) ──────────────────────────────


@router.post("/alerts/{alert_id}/resolve")
@handle_api_errors
async def resolve_alert(
    alert_id: str, request: Request,
) -> dict[str, Any]:
    """标记告警为已解决."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    resolved = mgr.resolve_alert(alert_id)
    return {"status": "ok" if resolved else "not_found", "resolved": resolved}


@router.get("/{tenant_id}/alerts")
@handle_api_errors
async def list_alerts(
    tenant_id: str, request: Request,
    resolved: str | None = Query(
        default="false",
        description="'true'=仅已解决, 'false'=仅未解决, 'all'=全部",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列出配额告警."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    if resolved == "all":
        resolved_filter: bool | None = None
    elif resolved == "true":
        resolved_filter = True
    else:
        resolved_filter = False
    alerts = mgr.list_alerts(
        tenant_id, resolved=resolved_filter, limit=limit, offset=offset,
    )
    return {
        "status": "ok",
        "alerts": [a.model_dump() for a in alerts],
        "count": len(alerts),
    }


# ── 使用量列表端点(固定段路径,必须先注册) ────────────────────────


@router.get("/{tenant_id}/usage")
@handle_api_errors
async def list_usage(
    tenant_id: str, request: Request,
) -> dict[str, Any]:
    """列出租户所有已设配额资源的使用量."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    usages = mgr.list_usage(tenant_id)
    return {
        "status": "ok",
        "usages": [u.model_dump() for u in usages],
        "count": len(usages),
    }


# ── 配额 CRUD 端点(参数段路径) ───────────────────────────────────


@router.post("/{tenant_id}/{resource}")
@handle_api_errors
async def set_quota(
    tenant_id: str, resource: str,
    body: SetQuotaRequest, request: Request,
) -> dict[str, Any]:
    """设置或更新配额."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    quota = mgr.set_quota(
        tenant_id, resource, body.hard_limit,
        soft_limit=body.soft_limit, period=body.period,
    )
    return {"status": "ok", "quota": quota.model_dump()}


@router.get("/{tenant_id}")
@handle_api_errors
async def list_quotas(
    tenant_id: str, request: Request,
) -> dict[str, Any]:
    """列出租户的所有配额."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    quotas = mgr.list_quotas(tenant_id)
    return {
        "status": "ok",
        "quotas": [q.model_dump() for q in quotas],
        "count": len(quotas),
    }


@router.get("/{tenant_id}/{resource}")
@handle_api_errors
async def get_quota(
    tenant_id: str, resource: str, request: Request,
) -> dict[str, Any]:
    """查询单个配额."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    quota = mgr.get_quota(tenant_id, resource)
    if quota is None:
        raise HTTPException(
            status_code=404,
            detail=f"Quota not found for tenant={tenant_id} resource={resource}",
        )
    return {"status": "ok", "quota": quota.model_dump()}


@router.put("/{tenant_id}/{resource}")
@handle_api_errors
async def update_quota(
    tenant_id: str, resource: str,
    body: UpdateQuotaRequest, request: Request,
) -> dict[str, Any]:
    """部分更新配额."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    try:
        quota = mgr.update_quota(
            tenant_id, resource,
            hard_limit=body.hard_limit,
            soft_limit=body.soft_limit,
            period=body.period,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "quota": quota.model_dump()}


@router.delete("/{tenant_id}/{resource}")
@handle_api_errors
async def delete_quota(
    tenant_id: str, resource: str, request: Request,
) -> dict[str, Any]:
    """删除配额."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    deleted = mgr.delete_quota(tenant_id, resource)
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}


# ── 使用量端点(三段路径,不与二段冲突) ────────────────────────────


@router.get("/{tenant_id}/{resource}/usage")
@handle_api_errors
async def get_usage(
    tenant_id: str, resource: str, request: Request,
) -> dict[str, Any]:
    """查询单个资源的使用量."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    usage = mgr.get_usage(tenant_id, resource)
    return {"status": "ok", "usage": usage.model_dump()}


@router.post("/{tenant_id}/{resource}/usage")
@handle_api_errors
async def update_usage(
    tenant_id: str, resource: str,
    body: UpdateUsageRequest, request: Request,
) -> dict[str, Any]:
    """增量更新使用量(amount 可正可负)."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    used = mgr.update_usage(
        tenant_id, resource, body.amount, period=body.period,
    )
    return {"status": "ok", "used": used}


@router.put("/{tenant_id}/{resource}/usage")
@handle_api_errors
async def set_usage(
    tenant_id: str, resource: str,
    body: SetUsageRequest, request: Request,
) -> dict[str, Any]:
    """绝对设置使用量."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    used = mgr.set_usage(
        tenant_id, resource, body.value, period=body.period,
    )
    return {"status": "ok", "used": used}


@router.post("/{tenant_id}/{resource}/reset-usage")
@handle_api_errors
async def reset_usage(
    tenant_id: str, resource: str, request: Request,
) -> dict[str, Any]:
    """重置单个资源的使用量."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    deleted = mgr.reset_usage(tenant_id, resource)
    return {"status": "ok", "deleted": deleted}


# ── 配额检查端点 ──────────────────────────────────────────────────


@router.post("/{tenant_id}/{resource}/check")
@handle_api_errors
async def check_quota(
    tenant_id: str, resource: str,
    body: CheckRequest, request: Request,
) -> dict[str, Any]:
    """检查是否允许消耗 amount 单位的 resource(不消费)."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    result = mgr.check_quota(tenant_id, resource, amount=body.amount)
    return {"status": "ok", "result": result.model_dump()}


@router.post("/{tenant_id}/{resource}/consume")
@handle_api_errors
async def consume_quota(
    tenant_id: str, resource: str,
    body: CheckRequest, request: Request,
) -> dict[str, Any]:
    """检查并消费 amount 单位的 resource."""
    require_admin(request)
    _require_tenant_isolation()
    mgr = _get_manager()
    result = mgr.consume(tenant_id, resource, amount=body.amount)
    return {"status": "ok", "result": result.model_dump()}
