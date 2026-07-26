"""Tests for maop.enterprise.pg_persist — degradation behavior without a PostgreSQL backend."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so the PostgreSQL feature flag is available."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Ensure no PostgreSQL backend is selected."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


def test_pg_rbac_store_not_available():
    """PgRBACStore is unavailable without a PG backend."""
    from maop.enterprise.pg_persist import PgRBACStore

    store = PgRBACStore()
    assert store.available is False


def test_pg_tenant_store_not_available():
    """PgTenantStore is unavailable without a PG backend."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    assert store.available is False


def test_pg_audit_store_not_available():
    """PgAuditStore is unavailable without a PG backend."""
    from maop.enterprise.pg_persist import PgAuditStore

    store = PgAuditStore()
    assert store.available is False


def test_pg_rbac_store_save_grant_noop():
    """save_grant() does not raise when no backend is available."""
    from maop.enterprise.pg_persist import PgRBACStore

    store = PgRBACStore()
    store.save_grant("u1", "admin", "t1", "root", 0.0, None)


def test_pg_rbac_store_delete_grant_returns_false():
    """delete_grant() returns False when no backend is available."""
    from maop.enterprise.pg_persist import PgRBACStore

    store = PgRBACStore()
    assert store.delete_grant("u1", "admin", "t1") is False


def test_pg_rbac_store_load_grants_empty():
    """load_grants() returns an empty list when no backend is available."""
    from maop.enterprise.pg_persist import PgRBACStore

    store = PgRBACStore()
    assert store.load_grants() == []


def test_pg_tenant_store_save_tenant_noop():
    """save_tenant() does not raise when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    store.save_tenant({"tenant_id": "t1", "name": "Acme"})


def test_pg_tenant_store_delete_tenant_returns_false():
    """delete_tenant() returns False when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    assert store.delete_tenant("t1") is False


def test_pg_tenant_store_load_tenants_empty():
    """load_tenants() returns an empty list when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    assert store.load_tenants() == []


def test_pg_tenant_store_load_tenant_none():
    """load_tenant() returns None when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    assert store.load_tenant("t1") is None


def test_pg_tenant_store_save_usage_noop():
    """save_usage() does not raise when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    store.save_usage("t1", {"api_calls_today": 1})


def test_pg_tenant_store_load_usage_none():
    """load_usage() returns None when no backend is available."""
    from maop.enterprise.pg_persist import PgTenantStore

    store = PgTenantStore()
    assert store.load_usage("t1") is None


def test_pg_audit_store_save_event_noop():
    """save_event() does not raise when no backend is available."""
    from maop.enterprise.pg_persist import PgAuditStore

    store = PgAuditStore()
    store.save_event({"event_id": "e1", "timestamp": 0.0, "action": "login"})


def test_pg_audit_store_query_events_empty():
    """query_events() returns an empty list when no backend is available."""
    from maop.enterprise.pg_persist import PgAuditStore

    store = PgAuditStore()
    assert store.query_events() == []


def test_pg_audit_store_summary_empty():
    """summary() returns a zeroed-out dict when no backend is available."""
    from maop.enterprise.pg_persist import PgAuditStore

    store = PgAuditStore()
    assert store.summary() == {
        "total_events": 0,
        "by_action": {},
        "critical_count": 0,
        "hours": 24,
    }


def test_get_pg_backend_returns_none_no_env(monkeypatch):
    """_get_pg_backend() returns None when MAOP_STORAGE_BACKEND is unset."""
    from maop.enterprise.pg_persist import _get_pg_backend

    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)
    assert _get_pg_backend() is None


def test_get_pg_backend_returns_none_wrong_backend(monkeypatch):
    """_get_pg_backend() returns None when MAOP_STORAGE_BACKEND is not postgresql."""
    from maop.enterprise.pg_persist import _get_pg_backend

    monkeypatch.setenv("MAOP_STORAGE_BACKEND", "sqlite")
    assert _get_pg_backend() is None
