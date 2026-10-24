"""MAOP Dashboard — Multi-Tenant Blackboard Admin Service.

Provides tenant management operations that use the M3
``TenantBlackboard`` / ``TenantContext`` / ``RBACPermission`` infrastructure.
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.reliability.blackboard import get_blackboard
from maop.core.reliability.coordination_tenant import (
    RBACPermission,
    TenantBlackboard,
    TenantContext,
)

logger = logging.getLogger(__name__)


def _admin_ctx(tenant_id: str = "system") -> TenantContext:
    """Create a superadmin TenantContext for backend operations."""
    return TenantContext(
        tenant_id=tenant_id,
        user_id="dashboard-admin",
        roles=frozenset({"superadmin"}),
        permissions=frozenset(RBACPermission),
    )


def list_tenants() -> list[dict[str, Any]]:
    """Scan the shared blackboard and return a list of distinct tenants.

    Each tenant is represented by its ``_tenant_id`` metadata marker.
    Cross-tenant by construction, so the router gates it behind
    ``require_admin``.
    """
    bb = get_blackboard()
    raw = bb.get_snapshot()
    tenants: dict[str, dict[str, Any]] = {}

    for domain, entries in raw.items():
        for entry_dict in entries:
            meta = entry_dict.get("metadata", {})
            tid = meta.get("_tenant_id")
            if tid is None:
                continue
            if tid not in tenants:
                tenants[tid] = {"tenant_id": tid, "domains": {}, "total_entries": 0}
            dom_entries = tenants[tid]["domains"].setdefault(domain, 0)
            tenants[tid]["domains"][domain] = dom_entries + 1
            tenants[tid]["total_entries"] += 1

    return list(tenants.values())


def get_tenant(tenant_id: str) -> dict[str, Any]:
    """Get detailed info about a single tenant.

    Includes a per-domain snapshot and recent history. The superadmin context
    here reads *another* tenant's data, which is only safe because the router
    puts ``require_admin`` in front of it.
    """
    bb = get_blackboard()
    ctx = _admin_ctx(tenant_id=tenant_id)
    tbb = TenantBlackboard(bb, ctx)

    # Filter snapshot to this tenant
    snap: dict[str, Any] = dict(tbb.get_snapshot())
    snap["_meta"] = {
        "tenant_id": tenant_id,
        "total_entries": tbb.total_entries(),
    }

    # Build per-domain history (limited)
    history = []
    for domain in snap:
        if domain == "_meta":
            continue
        entries = tbb.read(domain)
        for e in entries[-10:]:  # last 10 per domain
            history.append({
                "entry_id": e.entry_id,
                "domain": e.domain,
                "contributor": e.contributor,
                "confidence": e.confidence,
                "content": e.content,
                "metadata": e.metadata,
            })

    return {
        "tenant_id": tenant_id,
        "snapshot": snap,
        "history": history[:50],  # cap to 50 total
    }


async def delete_tenant_data(tenant_id: str) -> dict[str, Any]:
    """Delete **all** data for a tenant. Returns the number of entries removed.

    Deletion goes through ``TenantBlackboard.clear``: the ``BB_ADMIN`` gate and
    the tenant filter live with the class that owns the store. This used to
    reach into ``bb._lock`` / ``bb._entries`` and re-implement that filter by
    hand — same effect, but outside every check the wrapper exists to provide.
    """
    bb = get_blackboard()
    tbb = TenantBlackboard(bb, _admin_ctx(tenant_id=tenant_id))
    total_removed = 0
    for domain in list(bb.get_snapshot()):
        total_removed += await tbb.clear(domain)

    return {"tenant_id": tenant_id, "removed_entries": total_removed}


def create_tenant_context(
    tenant_id: str,
    user_id: str,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> TenantContext:
    """Factory: build a ``TenantContext`` from string-based roles/permissions.

    Validates permission strings against ``RBACPermission`` enum values.
    """
    role_set = frozenset(roles or [])
    perm_set: frozenset[RBACPermission] = frozenset()
    if permissions:
        valid = {p.value for p in RBACPermission}
        perm_set = frozenset(
            RBACPermission(p) for p in permissions if p in valid
        )

    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=role_set,
        permissions=perm_set,
    )


def get_rbac_permissions() -> list[dict[str, str]]:
    """Return all available RBACPermission values for the admin UI."""
    return [
        {"value": p.value, "name": p.name}
        for p in RBACPermission
    ]
