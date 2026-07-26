"""Tests for maop.enterprise.rbac.RBACManager — role grants, permissions, and hierarchy."""

from __future__ import annotations

import pytest

from maop.enterprise.rbac import (
    Permission,
    PermissionDenied,
    RBACManager,
    Role,
)


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.RBAC) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force in-memory RBAC storage (no PostgreSQL backend)."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


def test_grant_role():
    """grant_role() returns a RoleGrant with the given user_id and role."""
    mgr = RBACManager()
    grant = mgr.grant_role("alice", Role.ADMIN, granted_by="root")
    assert grant.user_id == "alice"
    assert grant.role == Role.ADMIN
    assert grant.granted_by == "root"


def test_revoke_role():
    """revoke_role() returns True when a grant exists, False otherwise."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN)
    assert mgr.revoke_role("alice", Role.ADMIN) is True
    assert mgr.revoke_role("alice", Role.ADMIN) is False
    assert mgr.revoke_role("unknown", Role.VIEWER) is False


def test_user_roles():
    """user_roles() returns all roles granted to a user."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN)
    mgr.grant_role("alice", Role.OPERATOR)
    roles = mgr.user_roles("alice")
    assert Role.ADMIN in roles
    assert Role.OPERATOR in roles
    assert len(roles) == 2


def test_user_permissions():
    """superadmin has all Permissions; viewer has only read permissions."""
    mgr = RBACManager()
    mgr.grant_role("root", Role.SUPERADMIN)
    root_perms = mgr.user_permissions("root")
    assert len(root_perms) == len(list(Permission))

    mgr.grant_role("bob", Role.VIEWER)
    viewer_perms = mgr.user_permissions("bob")
    for p in viewer_perms:
        assert p.value.endswith(":read")
    assert Permission.AGENTS_WRITE not in viewer_perms


def test_has_permission():
    """admin has AGENTS_WRITE but not TENANT_ADMIN."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN)
    assert mgr.has_permission("alice", Permission.AGENTS_WRITE) is True
    assert mgr.has_permission("alice", Permission.TENANT_ADMIN) is False


def test_require_permission_raises():
    """require_permission() raises PermissionDenied when permission is missing."""
    mgr = RBACManager()
    mgr.grant_role("bob", Role.VIEWER)
    with pytest.raises(PermissionDenied):
        mgr.require_permission("bob", Permission.AGENTS_WRITE)


def test_require_permission_passes():
    """require_permission() does not raise when permission is present."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN)
    mgr.require_permission("alice", Permission.AGENTS_WRITE)


def test_role_hierarchy_superadmin():
    """SUPERADMIN has every Permission in the enum."""
    mgr = RBACManager()
    mgr.grant_role("root", Role.SUPERADMIN)
    for p in Permission:
        assert mgr.has_permission("root", p) is True


def test_role_hierarchy_admin():
    """ADMIN lacks TENANT_ADMIN and SYSTEM_ADMIN."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN)
    assert mgr.has_permission("alice", Permission.TENANT_ADMIN) is False
    assert mgr.has_permission("alice", Permission.SYSTEM_ADMIN) is False
    assert mgr.has_permission("alice", Permission.AGENTS_WRITE) is True


def test_role_hierarchy_operator():
    """OPERATOR has AGENTS_EXECUTE but not CONFIG_WRITE."""
    mgr = RBACManager()
    mgr.grant_role("op", Role.OPERATOR)
    assert mgr.has_permission("op", Permission.AGENTS_EXECUTE) is True
    assert mgr.has_permission("op", Permission.CONFIG_WRITE) is False


def test_role_hierarchy_viewer():
    """VIEWER has only read permissions and lacks AGENTS_WRITE."""
    mgr = RBACManager()
    mgr.grant_role("viewer", Role.VIEWER)
    assert mgr.has_permission("viewer", Permission.AGENTS_READ) is True
    assert mgr.has_permission("viewer", Permission.AGENTS_WRITE) is False


def test_list_grants():
    """list_grants() filters by user_id and tenant_id."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN, tenant_id="t1")
    mgr.grant_role("bob", Role.VIEWER, tenant_id="t2")
    mgr.grant_role("alice", Role.OPERATOR, tenant_id="t2")

    by_user = mgr.list_grants(user_id="alice")
    assert len(by_user) == 2
    assert all(g.user_id == "alice" for g in by_user)

    by_tenant = mgr.list_grants(tenant_id="t2")
    assert len(by_tenant) == 2
    assert all(g.tenant_id == "t2" for g in by_tenant)


def test_tenant_scoped_roles():
    """grant_role() with tenant_id scopes user_roles() filtering."""
    mgr = RBACManager()
    mgr.grant_role("alice", Role.ADMIN, tenant_id="t1")
    mgr.grant_role("alice", Role.VIEWER, tenant_id="t2")

    roles_t1 = mgr.user_roles("alice", tenant_id="t1")
    assert Role.ADMIN in roles_t1
    assert Role.VIEWER not in roles_t1

    roles_t2 = mgr.user_roles("alice", tenant_id="t2")
    assert Role.VIEWER in roles_t2
    assert Role.ADMIN not in roles_t2
