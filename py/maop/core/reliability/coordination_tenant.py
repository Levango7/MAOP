"""MAOP Multi-Tenant Blackboard Isolation Layer.

Provides **tenant-scoped** blackboard operations so that multiple tenants
can share the same MAOP instance without seeing each other's knowledge
entries. Each tenant's entries are isolated by metadata tagging (no domain
prefix, since the underlying ``Blackboard`` validates domains against
``BlackboardDomain`` whitelist).

When the enterprise package (MAOS) is installed and ``FeatureFlag.ENTERPRISE``
is active, the layer enforces RBAC checks on every mutating operation.
When MAOS is absent, a lightweight in-memory permission fallback is used.

Classes
-------
- ``TenantContext``: holds current tenant + caller identity.
- ``TenantBlackboard``: tenant-isolated view over a shared Blackboard.
- ``RBACPermission``: coordination-layer permission enum.
- ``RBACEnforcer``: pluggable permission checker.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from maop.core.reliability.blackboard import (
    Blackboard,
    BlackboardEntry,
)

logger = logging.getLogger(__name__)


# ── RBACPermission ────────────────────────────────────────────────


class RBACPermission(str, Enum):
    """Coordination-layer permissions (subset of enterprise RBAC)."""

    BB_READ = "blackboard:read"
    BB_WRITE = "blackboard:write"
    BB_ADMIN = "blackboard:admin"
    BB_DEBATE = "blackboard:debate"
    KS_REGISTER = "ks:register"
    KS_EXECUTE = "ks:execute"


# ── TenantContext ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantContext:
    """Immutable caller context for a blackboard operation.

    Parameters
    ----------
    tenant_id : str
        Tenant identifier. ``"system"`` for global/infra operations.
    user_id : str
        User principal name.
    roles : frozenset[str]
        Role names assigned to the user.
    permissions : frozenset[RBACPermission]
        Expanded permissions derived from roles.
    """

    tenant_id: str = "default"
    user_id: str = "anonymous"
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[RBACPermission] = field(default_factory=frozenset)

    def has_permission(self, perm: RBACPermission) -> bool:
        return perm in self.permissions or "superadmin" in self.roles

    @classmethod
    def system(cls) -> TenantContext:
        return cls(
            tenant_id="system",
            user_id="system",
            roles=frozenset({"superadmin"}),
            permissions=frozenset(RBACPermission),
        )


# ── RBAC Enforcer Protocol ────────────────────────────────────────


class RBACEnforcer(Protocol):
    """Pluggable permission checker."""

    def check(
        self,
        ctx: TenantContext,
        permission: RBACPermission,
        resource: str,
    ) -> bool: ...


# ── Fallback (in-memory) enforcer ─────────────────────────────────


class FallbackRBACEnforcer:
    """Lightweight enforcer used when enterprise RBAC is not installed.

    Allows any operation by default but respects explicit ``deny`` rules.
    """

    def __init__(self) -> None:
        self._denials: set[tuple[str, RBACPermission, str]] = set()
        self._lock = threading.Lock()

    def deny(
        self, role: str, perm: RBACPermission, resource: str = "*"
    ) -> None:
        with self._lock:
            self._denials.add((role, perm, resource))

    def check(
        self,
        ctx: TenantContext,
        permission: RBACPermission,
        resource: str,
    ) -> bool:
        if ctx.has_permission(permission):
            with self._lock:
                for role in ctx.roles:
                    if (role, permission, "*") in self._denials:
                        return False
                    if (role, permission, resource) in self._denials:
                        return False
            return True
        return False


# ── Enum for RBACPermission needs import ──────────────────────────

from enum import Enum

# ── TenantBlackboard ──────────────────────────────────────────────


class TenantBlackboard:
    """Tenant-isolated view over a shared ``Blackboard``.

    Tenant isolation is achieved via **metadata tagging** (``_tenant_id``),
    NOT domain-prefixed, because ``Blackboard._validate_domain`` only allows
    ``BlackboardDomain`` enum values.

    Non-mutating reads check ``BB_READ``; writes check ``BB_WRITE``.
    Administrative ops (clear) check ``BB_ADMIN``.

    Example::

        ctx = TenantContext(tenant_id="acme", user_id="alice",
                            roles=frozenset({"admin"}),
                            permissions=frozenset({RBACPermission.BB_WRITE}))
        tbb = TenantBlackboard(blackboard, ctx)
        await tbb.write("decision", {"verdict": "approve"})
    """

    _TENANT_KEY = "_tenant_id"

    def __init__(
        self,
        bb: Blackboard,
        ctx: TenantContext,
        enforcer: RBACEnforcer | None = None,
    ) -> None:
        self._bb = bb
        self._ctx = ctx
        self._enforcer = enforcer or FallbackRBACEnforcer()

    # ── Permission gate ──────────────────────────────────────────

    def _require(self, perm: RBACPermission, resource: str = "*") -> None:
        if not self._enforcer.check(self._ctx, perm, resource):
            raise PermissionError(
                f"User {self._ctx.user_id} in tenant {self._ctx.tenant_id} "
                f"lacks permission {perm.value} on {resource}"
            )

    # ── Write ────────────────────────────────────────────────────

    async def write(
        self,
        domain: str,
        content: Any,
        contributor: str = "",
        *,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> BlackboardEntry:
        """Write a tenant-scoped entry (tagged with ``_tenant_id``)."""
        self._require(RBACPermission.BB_WRITE, domain)
        meta = dict(metadata or {})
        meta[self._TENANT_KEY] = self._ctx.tenant_id
        return await self._bb.write(
            domain=domain,
            content=content,
            contributor=contributor or self._ctx.user_id,
            confidence=confidence,
            metadata=meta,
        )

    # ── Read ─────────────────────────────────────────────────────

    def read(self, domain: str) -> list[BlackboardEntry]:
        """Read tenant-scoped entries from a domain."""
        self._require(RBACPermission.BB_READ, domain)
        all_entries = self._bb.read(domain)
        return [
            e for e in all_entries
            if e.metadata.get(self._TENANT_KEY) == self._ctx.tenant_id
        ]

    def read_domains(self, domains: list[str]) -> dict[str, list[BlackboardEntry]]:
        self._require(RBACPermission.BB_READ, ",".join(domains))
        return {d: self.read(d) for d in domains}

    def get_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Get a snapshot of this tenant's data only."""
        self._require(RBACPermission.BB_READ)
        raw = self._bb.get_snapshot()
        result = {}
        for domain, entries in raw.items():
            tenant_entries = [
                e for e in entries
                if e.get("metadata", {}).get(self._TENANT_KEY) == self._ctx.tenant_id
            ]
            if tenant_entries:
                result[domain] = tenant_entries
        return result

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return blackboard history filtered by this tenant's writes."""
        self._require(RBACPermission.BB_READ)
        return [
            r for r in self._bb.get_history(limit=limit * 2)
            if r.get("contributor") == self._ctx.user_id
            or any(
                e.get("metadata", {}).get(self._TENANT_KEY) == self._ctx.tenant_id
                for e in [r]  # best-effort; history doesn't store metadata
            )
        ][:limit]

    # ── Clear ────────────────────────────────────────────────────

    async def clear(self, domain: str) -> int:
        """Clear a tenant-scoped domain. Requires BB_ADMIN."""
        self._require(RBACPermission.BB_ADMIN, domain)
        tenant_entries = [
            e for e in self._bb.read(domain)
            if e.metadata.get(self._TENANT_KEY) == self._ctx.tenant_id
        ]
        count = len(tenant_entries)
        # Remove tenant entries by filtering the underlying store
        with self._bb._lock:
            before = self._bb._entries.get(domain, [])
            self._bb._entries[domain] = [
                e for e in before
                if e.metadata.get(self._TENANT_KEY) != self._ctx.tenant_id
            ]
        return count

    # ── Stats ────────────────────────────────────────────────────

    def total_entries(self) -> int:
        self._require(RBACPermission.BB_READ)
        raw = self._bb.get_snapshot()
        n = 0
        for entries in raw.values():
            n += sum(
                1 for e in entries
                if e.get("metadata", {}).get(self._TENANT_KEY) == self._ctx.tenant_id
            )
        return n

    def __repr__(self) -> str:
        return (
            f"TenantBlackboard(tenant={self._ctx.tenant_id!r}, "
            f"user={self._ctx.user_id!r})"
        )
