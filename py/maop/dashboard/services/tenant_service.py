"""Enterprise tenant service layer.

Encapsulates business logic for tenant CRUD and lifecycle operations
(list / create / get / suspend / activate / delete / usage). Extracted
from the tenant router so the router only does parameter parsing +
auth + feature-flag guard + service call + response formatting.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.

The ``TenantManager`` singleton is cached module-level with a
double-checked locking pattern (P1-18) to mirror the original router
behaviour.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_tenant_manager: Any = None
_tenant_manager_lock = threading.Lock()


def _get_manager() -> Any:
    """Lazy singleton TenantManager (double-checked locking, P1-18)."""
    global _tenant_manager
    if _tenant_manager is None:
        with _tenant_manager_lock:
            if _tenant_manager is None:
                from maop.enterprise.tenant import TenantManager
                _tenant_manager = TenantManager()
    return _tenant_manager


def try_get_manager() -> Any | None:
    """同 :func:`_get_manager`，但在"这台部署根本没有租户注册表"时返回 ``None``。

    "没有注册表"有**两种**形态，实测踩过只挡一种的版本：

    1. ``maop-enterprise`` 未安装 → ``ImportError``；
    2. **装了包但 edition 未开 ``tenant_isolation``** → ``TenantManager()``
       构造即抛 ``FeatureNotAvailable``。开发机上"MAOS 在 sys.path 里 + 跑在
       个人版"是常见形态，只挡 ① 会把每一次 DAG 执行变成 500。

    返回 ``None`` 的含义是"无从检查"，**不是**"检查通过" —— 调用方必须自己区分。
    """
    from maop.config.edition import FeatureFlag, has_feature

    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        return None
    try:
        return _get_manager()
    except ImportError:
        return None


def execution_block_reason(tenant_id: str) -> str | None:
    """租户生命周期门：返回拒绝原因，``None`` 表示放行。

    三种**故意**放行的情形，每种都不是"检查通过了"而是"无从检查"：

    1. ``tenant_id`` 为空 —— 个人版或认证关闭时不存在租户边界（身份由 JWT claim
       来，见 ``core/tenant/context.py``，所以空串不是"调用方没填"而是"确实没有"）；
    2. 企业租户能力不可用（未装包，或装了但 edition 未开 ``tenant_isolation``）——
       压根没有注册表；
    3. **注册表为空** —— 没配 PG 时 ``TenantManager`` 载入零个租户，此时"查不到"
       不等于"不许"。据此拒绝会把所有未接租户库的部署一律打死（对准空集合的门
       比没有门更糟：它看起来在工作，实际只制造恒拒绝）。

    注册表非空时才做真实判定：未登记的租户拒绝（配置里已有租户表却查不到这个
    身份，说明它不属于任何已知租户）、``suspended`` / ``terminated`` 拒绝。
    """
    if not tenant_id:
        return None
    mgr = try_get_manager()
    if mgr is None:
        return None
    if not mgr.list_tenants():
        return None
    tenant = mgr.get_tenant(tenant_id)
    if tenant is None:
        return f"tenant '{tenant_id}' is not registered"
    status_value = getattr(tenant.status, "value", tenant.status)
    if status_value in ("suspended", "terminated"):
        return f"tenant '{tenant_id}' is {status_value}"
    return None


# ════════════════════════════════════════════════════════════════════════════
# Tenant CRUD + lifecycle
# ════════════════════════════════════════════════════════════════════════════

def list_tenants(status: str = "") -> dict[str, Any]:
    """List all tenants, optionally filtered by status.

    Raises ``ValueError`` if ``status`` is a non-empty string that does
    not match any ``TenantStatus`` value (router maps to 400).
    """
    from maop.enterprise.tenant import TenantStatus

    mgr = _get_manager()
    status_filter = None
    if status:
        try:
            status_filter = TenantStatus(status)
        except ValueError as exc:
            raise ValueError(
                f"Invalid status '{status}'. Valid: {[s.value for s in TenantStatus]}"
            ) from exc

    tenants = mgr.list_tenants(status=status_filter)
    result = []
    for t in tenants:
        d = t.model_dump()
        try:
            d["usage"] = mgr.get_usage(t.tenant_id).model_dump()
        except Exception:
            d["usage"] = {}
        result.append(d)
    return {
        "status": "ok",
        "tenants": result,
        "count": len(tenants),
    }


def create_tenant(
    tenant_id: str,
    name: str,
    plan: str,
    max_api_calls_per_day: int,
    max_storage_mb: int,
) -> dict[str, Any]:
    """Create a new tenant. Returns the created tenant dict."""
    from maop.enterprise.tenant import TenantQuota

    quota = TenantQuota(
        max_api_calls_per_day=max_api_calls_per_day,
        max_storage_mb=max_storage_mb,
    )
    mgr = _get_manager()
    tenant = mgr.create_tenant(tenant_id, name, plan=plan, quota=quota)
    return {"status": "ok", "tenant": tenant.model_dump()}


def get_tenant(tenant_id: str) -> dict[str, Any]:
    """Get a single tenant by ID.

    Raises ``KeyError`` if the tenant does not exist (router maps to 404).
    """
    mgr = _get_manager()
    tenant = mgr.get_tenant(tenant_id)
    if tenant is None:
        raise KeyError(tenant_id)
    return {"status": "ok", "tenant": tenant.model_dump()}


def suspend_tenant(tenant_id: str) -> dict[str, Any]:
    """Suspend a tenant.

    Raises ``KeyError`` if the tenant does not exist (router maps to 404).
    """
    mgr = _get_manager()
    suspended = mgr.suspend_tenant(tenant_id)
    if not suspended:
        raise KeyError(tenant_id)
    return {"status": "ok", "suspended": suspended}


def activate_tenant(tenant_id: str) -> dict[str, Any]:
    """Activate a suspended tenant.

    Raises ``KeyError`` if the tenant does not exist (router maps to 404).
    """
    mgr = _get_manager()
    activated = mgr.activate_tenant(tenant_id)
    if not activated:
        raise KeyError(tenant_id)
    return {"status": "ok", "activated": activated}


def delete_tenant(tenant_id: str) -> dict[str, Any]:
    """Delete a tenant.

    Raises ``KeyError`` if the tenant does not exist (router maps to 404).
    """
    mgr = _get_manager()
    deleted = mgr.delete_tenant(tenant_id)
    if not deleted:
        raise KeyError(tenant_id)
    return {"status": "ok", "deleted": deleted}


def get_tenant_usage(tenant_id: str) -> dict[str, Any]:
    """Get resource usage for a tenant."""
    mgr = _get_manager()
    usage = mgr.get_usage(tenant_id)
    return {"status": "ok", "usage": usage.model_dump()}