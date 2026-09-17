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