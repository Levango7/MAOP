"""Tests for maop.enterprise.tenant.TenantManager — lifecycle, quotas, and usage."""

from __future__ import annotations

import pytest

from maop.enterprise.tenant import (
    TenantManager,
    TenantQuota,
    TenantStatus,
)


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.TENANT_ISOLATION) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force in-memory tenant storage (no PostgreSQL backend)."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


def test_create_tenant():
    """create_tenant() returns a Tenant with correct id/name and TRIAL status."""
    mgr = TenantManager()
    t = mgr.create_tenant("t1", "Acme Corp")
    assert t.tenant_id == "t1"
    assert t.name == "Acme Corp"
    assert t.status == TenantStatus.TRIAL


def test_create_tenant_duplicate():
    """Creating a duplicate tenant raises ValueError."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    with pytest.raises(ValueError):
        mgr.create_tenant("t1", "Acme Again")


def test_get_tenant():
    """get_tenant() returns the Tenant when found, None otherwise."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    assert mgr.get_tenant("t1") is not None
    assert mgr.get_tenant("nope") is None


def test_update_tenant():
    """update_tenant() changes name and plan."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    t = mgr.update_tenant("t1", name="Acme II", plan="pro")
    assert t.name == "Acme II"
    assert t.plan == "pro"


def test_update_tenant_not_found():
    """update_tenant() on a missing tenant raises KeyError."""
    mgr = TenantManager()
    with pytest.raises(KeyError):
        mgr.update_tenant("nope", name="X")


def test_suspend_tenant():
    """suspend_tenant() sets status to SUSPENDED."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    assert mgr.suspend_tenant("t1") is True
    assert mgr.get_tenant("t1").status == TenantStatus.SUSPENDED


def test_activate_tenant():
    """activate_tenant() sets status to ACTIVE."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    assert mgr.activate_tenant("t1") is True
    assert mgr.get_tenant("t1").status == TenantStatus.ACTIVE


def test_suspend_not_found():
    """suspend_tenant() on a missing tenant returns False."""
    mgr = TenantManager()
    assert mgr.suspend_tenant("nope") is False


def test_delete_tenant():
    """delete_tenant() removes the tenant."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    assert mgr.delete_tenant("t1") is True
    assert mgr.get_tenant("t1") is None


def test_delete_not_found():
    """delete_tenant() on a missing tenant returns False."""
    mgr = TenantManager()
    assert mgr.delete_tenant("nope") is False


def test_check_quota():
    """check_quota() returns True when current < limit, False when >= limit."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    # default max_api_calls_per_day = 10000
    assert mgr.check_quota("t1", "api_calls", 9999) is True
    assert mgr.check_quota("t1", "api_calls", 10000) is False


def test_check_quota_unknown_resource():
    """check_quota() for an unknown resource returns True."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    assert mgr.check_quota("t1", "unknown_resource", 999999) is True


def test_check_quota_not_found():
    """check_quota() for a missing tenant returns False."""
    mgr = TenantManager()
    assert mgr.check_quota("nope", "api_calls", 1) is False


def test_get_usage():
    """get_usage() for a new tenant returns all-zero usage."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "Acme")
    usage = mgr.get_usage("t1")
    assert usage.api_calls_today == 0
    assert usage.storage_mb == 0.0
    assert usage.active_agents == 0
    assert usage.concurrent_tasks == 0
    assert usage.active_users == 0


def test_list_tenants():
    """list_tenants() returns all tenants."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "A")
    mgr.create_tenant("t2", "B")
    mgr.create_tenant("t3", "C")
    assert len(mgr.list_tenants()) == 3


def test_list_tenants_by_status():
    """list_tenants(status=) filters by status."""
    mgr = TenantManager()
    mgr.create_tenant("t1", "A")
    mgr.create_tenant("t2", "B")
    mgr.activate_tenant("t2")
    trial = mgr.list_tenants(status=TenantStatus.TRIAL)
    active = mgr.list_tenants(status=TenantStatus.ACTIVE)
    assert len(trial) == 1
    assert len(active) == 1


def test_custom_quota():
    """create_tenant() with a custom quota applies the quota values."""
    mgr = TenantManager()
    quota = TenantQuota(max_api_calls_per_day=500, max_agents=5, max_users=3)
    t = mgr.create_tenant("t1", "Acme", quota=quota)
    assert t.quota.max_api_calls_per_day == 500
    assert t.quota.max_agents == 5
    assert t.quota.max_users == 3
