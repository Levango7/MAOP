"""RBAC, Permission & Compliance service layer.

Extracted from the rbac / permission / compliance routers (§ARCH-H2) so
the router layer only does parameter parsing + permission check +
service call + response formatting + error handling.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.

Modules covered:
    - rbac.py        → RBACManager singleton
    - permission.py  → PermissionManager + HumanProxy operations
    - compliance.py  → ComplianceManager singleton
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Project root (computed relative to this file) ──────────────────
# rbac_service.py → services/ → dashboard/ → maop/ → py/ → MAOP_ROOT
_MAOP_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


# ════════════════════════════════════════════════════════════════════
# RBAC manager singleton
# ════════════════════════════════════════════════════════════════════

_rbac_manager: Any = None
_rbac_manager_lock = threading.Lock()


def get_rbac_manager() -> Any:
    """Lazy-init RBACManager singleton (enterprise edition only).

    The manager's ``__init__`` calls ``require_feature(FeatureFlag.RBAC)``,
    so in personal edition this will raise.  Callers (router) should
    guard with ``has_feature(FeatureFlag.RBAC)`` first.
    """
    global _rbac_manager
    if _rbac_manager is None:
        with _rbac_manager_lock:
            if _rbac_manager is None:
                from maop.enterprise.rbac import RBACManager
                _rbac_manager = RBACManager()
    return _rbac_manager


# ════════════════════════════════════════════════════════════════════
# Permission & Approval operations
# ════════════════════════════════════════════════════════════════════


def add_permission_rule(
    root_dir: str,
    *,
    agent: str = "*",
    action: str = "*",
    decision: str = "ask",
    reason: str = "",
    priority: int = 0,
) -> str:
    """Add a permission rule. Returns the new rule_id."""
    from maop.core.security.permission import PermissionManager
    pm = PermissionManager(root_dir=root_dir)
    return pm.add_rule(
        agent=agent, action=action, decision=decision,
        reason=reason, priority=priority,
    )


def remove_permission_rule(root_dir: str, rule_id: str) -> bool:
    """Remove a permission rule. Returns True if removed, False if not found."""
    from maop.core.security.permission import PermissionManager
    pm = PermissionManager(root_dir=root_dir)
    return pm.remove_rule(rule_id)


def list_permission_rules(root_dir: str, limit: int = 100) -> list:
    """List permission rules. Returns list of model dicts."""
    from maop.core.security.permission import PermissionManager
    pm = PermissionManager(root_dir=root_dir)
    rules = pm.list_rules(limit=limit)
    return [r.model_dump() for r in rules]


def check_permission(root_dir: str, *, agent: str, action: str = "*") -> dict:
    """Check permission for an agent/action. Returns the check result dict."""
    from maop.core.security.permission import PermissionManager
    pm = PermissionManager(root_dir=root_dir)
    check = pm.check(agent=agent, action=action)
    return check.model_dump()


def list_pending_approvals(root_dir: str, limit: int = 50) -> list:
    """List pending human-proxy approval requests. Returns list of model dicts."""
    from maop.core.agent.delegation.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=root_dir)
    pending = hp.pending(limit=limit)
    return [p.model_dump() for p in pending]


def approve_request(root_dir: str, request_id: str) -> bool:
    """Approve a pending request. Returns True if approved, False if not found."""
    from maop.core.agent.delegation.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=root_dir)
    return hp.approve(request_id)


def reject_request(root_dir: str, request_id: str, reason: str = "") -> bool:
    """Reject a pending request. Returns True if rejected, False if not found."""
    from maop.core.agent.delegation.human_proxy import HumanProxy
    hp = HumanProxy(root_dir=root_dir)
    return hp.reject(request_id, reason=reason)


# ════════════════════════════════════════════════════════════════════
# Compliance manager singleton
# ════════════════════════════════════════════════════════════════════

_compliance_mgr: Any = None
_compliance_mgr_lock = threading.Lock()


class ComplianceNotConfigured(Exception):
    """Raised when root_dir is not configured (→ 500)."""


def get_compliance_manager(root_dir: str | None) -> Any:
    """Lazy-init ComplianceManager singleton.

    ``root_dir`` is typically ``request.app.state.root_dir``; if not
    configured, raises ``ComplianceNotConfigured``.
    """
    global _compliance_mgr
    if _compliance_mgr is not None:
        return _compliance_mgr
    with _compliance_mgr_lock:
        if _compliance_mgr is not None:  # double-checked locking
            return _compliance_mgr
        if not root_dir:
            logger.error("[compliance] root_dir not configured")
            raise ComplianceNotConfigured("Compliance service not configured")
        from maop.core.tenant.compliance import ComplianceManager
        _compliance_mgr = ComplianceManager(root_dir)
    return _compliance_mgr


def delete_user_data(user_id: str, *, tenant_id: str, root_dir: str | None) -> dict:
    """Delete all data for a user (GDPR right-to-erasure). Returns report dict."""
    mgr = get_compliance_manager(root_dir)
    report = mgr.delete_user_data(user_id, tenant_id=tenant_id)
    return report.model_dump()


def export_user_data(user_id: str, *, tenant_id: str, root_dir: str | None) -> dict:
    """Export all data for a user (GDPR data portability). Returns report dict."""
    mgr = get_compliance_manager(root_dir)
    report = mgr.export_user_data(user_id, tenant_id=tenant_id)
    return report.model_dump()