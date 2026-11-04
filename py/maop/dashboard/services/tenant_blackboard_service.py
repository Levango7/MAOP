"""MAOP Dashboard — Multi-Tenant Blackboard Admin Service.

Provides tenant management operations that use the M3
``TenantBlackboard`` / ``TenantContext`` / ``RBACPermission`` infrastructure.

Authorization: when the enterprise package is installed, the M3 enforcer comes
from ``maop.enterprise.rbac_m3_adapter.create_m3_enforcer()`` and is injected
explicitly at construction time (option B in the MAOS readiness doc — MAOS
exports the factory and MAOP injects it; MAOP imports the adapter lazily, so
enterprise is never a hard dependency at module scope). Without it,
``TenantBlackboard`` falls back to ``FallbackRBACEnforcer``, which is
fail-closed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from maop.core.reliability.blackboard import get_blackboard
from maop.core.reliability.coordination_tenant import (
    RBACPermission,
    TenantBlackboard,
    TenantContext,
)

logger = logging.getLogger(__name__)

#: Roles that MAOP's own auth layer grants to an operator. Used to derive
#: blackboard permissions in the personal edition, which has no enterprise
#: grant table; the enterprise enforcer ignores this and consults MAOS.
_PRIVILEGED_ROLES = frozenset({"admin", "superadmin"})


def _admin_ctx(tenant_id: str = "system") -> TenantContext:
    """Create a superadmin TenantContext for backend operations."""
    return TenantContext(
        tenant_id=tenant_id,
        user_id="dashboard-admin",
        roles=frozenset({"superadmin"}),
        permissions=frozenset(RBACPermission),
    )


def context_for_caller(
    tenant_id: str,
    user_id: str,
    roles: Iterable[str] = (),
) -> TenantContext:
    """Build the calling user's ``TenantContext`` for a tenant-scoped read.

    ``permissions`` are only meaningful in the personal edition (the fallback
    enforcer reads them); with the enterprise enforcer injected, MAOS's own
    grant table decides. Non-privileged roles therefore get **no permissions at
    all**, so a missing grant denies rather than defaulting open.
    """
    role_set = frozenset(roles)
    permissions = (
        frozenset({RBACPermission.BB_READ})
        if role_set & _PRIVILEGED_ROLES
        else frozenset()
    )
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=role_set,
        permissions=permissions,
    )


def resolve_m3_enforcer() -> Any | None:
    """Return the enterprise M3 enforcer, or ``None`` to use the fallback.

    Reuses the process-wide ``rbac_service.get_rbac_manager()`` singleton — the
    adapter's own default would open a *second* RBAC store handle (PG-first with
    a ``data/rbac.db`` fallback), which is exactly what its docstring warns
    against. The adapter is imported lazily so a personal build stays importable
    without the enterprise package.
    """
    try:
        from maop.dashboard.services import rbac_service
        from maop.enterprise.rbac_m3_adapter import create_m3_enforcer
    except ImportError:
        logger.debug(
            "[tenant-blackboard] 企业版 M3 适配器不可用（个人版），沿用 FallbackRBACEnforcer"
        )
        return None
    try:
        manager = rbac_service.get_rbac_manager()
    except Exception as exc:
        # Feature flag off / store unavailable. Returning None silently degrades
        # authorization, so say it out loud.
        logger.warning(
            "[tenant-blackboard] RBACManager 不可用，M3 鉴权退回 fallback: %s", exc
        )
        return None
    return create_m3_enforcer(manager)


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


def get_tenant(tenant_id: str, *, caller: TenantContext | None = None) -> dict[str, Any]:
    """Get detailed info about a single tenant.

    Includes a per-domain snapshot and recent history.

    ``caller`` is the authenticated user's context; pass ``None`` only when
    there is no identity boundary at all (auth disabled — personal single-user
    mode), in which case the historical superadmin context is kept. With a
    caller, the enterprise enforcer is injected when available, so MAOS's own
    grant table becomes authoritative — see ``resolve_m3_enforcer``.

    Raises ``PermissionError`` when the caller lacks ``blackboard:read`` on
    this tenant.
    """
    bb = get_blackboard()
    if caller is None:
        ctx = _admin_ctx(tenant_id=tenant_id)
        enforcer: Any | None = None
    else:
        ctx = caller
        enforcer = resolve_m3_enforcer()
    tbb = TenantBlackboard(bb, ctx, enforcer=enforcer)

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
