"""MAOP Multi-Tenancy — Tenant isolation, RLS, quotas, and audit.

This package enhances the legacy single-file ``tenant.py`` with three
subsystems:

* :class:`TenantRLS` — row-level security scoping for tenant-aware tables.
* :class:`ResourceQuotaManager` — multi-resource quotas (storage, agents, …).
* :class:`AuditLogger` — tamper-evident audit trail with hash chaining.

Backward compatibility: ``from maop.core.tenant import TenantManager,
TenantConfig`` continues to work exactly as before; the enhanced APIs are
available as ``mgr.rls``, ``mgr.quota``, ``mgr.audit`` and convenience
methods on :class:`TenantManager`.
"""

from __future__ import annotations

from maop.core.tenant.audit import AuditEntry, AuditLogger
from maop.core.tenant.manager import TenantConfig, TenantManager
from maop.core.tenant.quota import (
    QuotaError,
    ResourceQuota,
    ResourceQuotaManager,
    ResourceUsage,
)
from maop.core.tenant.rls import RLSError, TenantRLS

__all__ = [
    "AuditEntry",
    "AuditLogger",
    "QuotaError",
    "RLSError",
    "ResourceQuota",
    "ResourceQuotaManager",
    "ResourceUsage",
    "TenantConfig",
    "TenantManager",
    "TenantRLS",
]