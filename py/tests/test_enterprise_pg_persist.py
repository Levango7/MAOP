"""Tests for maop.enterprise.pg_persist — degradation behavior without a PostgreSQL backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# P2 修复：无条件 skip 改为 import 条件化 —— maop.enterprise 可导入
# （企业版）时才真正运行测试；个人版（未安装）时才跳过。
try:
    import maop.enterprise  # noqa: F401
except ImportError:
    pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)


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


# --- Merged from test_pg_persist_coverage.py ---

def _mock_backend():
    """Return a MagicMock that quacks like PostgreSQLStorageBackend."""
    b = MagicMock()
    b.execute = MagicMock()
    b.fetchall = MagicMock(return_value=[])
    b.fetchone = MagicMock(return_value=None)
    return b


# ── PgRBACStore with backend ──────────────────────────────────

class TestPgRBACStoreWithBackend:
    def test_available_true(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            assert store.available is True

    def test_ensure_schema_executes_ddl(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            PgRBACStore()
        # CREATE TABLE + 2 CREATE INDEX = 3 execute calls
        assert backend.execute.call_count == 3

    def test_save_grant_calls_execute(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            backend.execute.reset_mock()
            store.save_grant("u1", "admin", "t1", "root", 100.0, None)
        backend.execute.assert_called_once()
        args = backend.execute.call_args[0]
        assert "INSERT INTO rbac_grants" in args[0]

    def test_delete_grant_returns_true(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            assert store.delete_grant("u1", "admin", "t1") is True

    def test_load_grants_all(self):
        backend = _mock_backend()
        backend.fetchall.return_value = [{"user_id": "u1", "role": "admin"}]
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            result = store.load_grants()
        assert result == [{"user_id": "u1", "role": "admin"}]

    def test_load_grants_by_user_and_tenant(self):
        backend = _mock_backend()
        backend.fetchall.return_value = []
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            store.load_grants(user_id="u1", tenant_id="t1")
        sql = backend.fetchall.call_args[0][0]
        assert "user_id=%s" in sql and "tenant_id" in sql

    def test_load_grants_by_user_only(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            store.load_grants(user_id="u1")
        sql = backend.fetchall.call_args[0][0]
        assert "user_id=%s" in sql

    def test_load_grants_by_tenant_only(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgRBACStore
            store = PgRBACStore()
            store.load_grants(tenant_id="t1")
        sql = backend.fetchall.call_args[0][0]
        assert "tenant_id" in sql


# ── PgTenantStore with backend ────────────────────────────────

class TestPgTenantStoreWithBackend:
    def test_available_true(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.available is True

    def test_ensure_schema_executes_ddl(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            PgTenantStore()
        # 2 CREATE TABLE statements
        assert backend.execute.call_count == 2

    def test_save_tenant_calls_execute(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            backend.execute.reset_mock()
            store.save_tenant({"tenant_id": "t1", "name": "Acme", "quota": {"x": 1}, "metadata": {"y": 2}})
        backend.execute.assert_called_once()

    def test_delete_tenant_returns_true(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            backend.execute.reset_mock()
            assert store.delete_tenant("t1") is True
        # 2 deletes: tenant_usage + tenants
        assert backend.execute.call_count == 2

    def test_load_tenants_all(self):
        backend = _mock_backend()
        backend.fetchall.return_value = [{"tenant_id": "t1"}]
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.load_tenants() == [{"tenant_id": "t1"}]

    def test_load_tenants_by_status(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            store.load_tenants(status="active")
        sql = backend.fetchall.call_args[0][0]
        assert "status=%s" in sql

    def test_load_tenant_found(self):
        backend = _mock_backend()
        backend.fetchone.return_value = {"tenant_id": "t1", "name": "Acme"}
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.load_tenant("t1") == {"tenant_id": "t1", "name": "Acme"}

    def test_load_tenant_not_found(self):
        backend = _mock_backend()
        backend.fetchone.return_value = None
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.load_tenant("nope") is None

    def test_save_usage_calls_execute(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            backend.execute.reset_mock()
            store.save_usage("t1", {"api_calls_today": 100, "storage_mb": 50.0})
        backend.execute.assert_called_once()

    def test_load_usage_found(self):
        backend = _mock_backend()
        backend.fetchone.return_value = {"tenant_id": "t1", "api_calls_today": 100}
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.load_usage("t1") == {"tenant_id": "t1", "api_calls_today": 100}

    def test_load_usage_not_found(self):
        backend = _mock_backend()
        backend.fetchone.return_value = None
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgTenantStore
            store = PgTenantStore()
            assert store.load_usage("nope") is None


# ── PgAuditStore with backend ─────────────────────────────────

class TestPgAuditStoreWithBackend:
    def test_available_true(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            assert store.available is True

    def test_ensure_schema_executes_ddl(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            PgAuditStore()
        # CREATE TABLE + 4 CREATE INDEX (legacy) + 3 ALTER TABLE ADD COLUMN
        # + 2 CREATE INDEX (risk_level, category) = 10
        assert backend.execute.call_count == 10

    def test_save_event_calls_execute(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            backend.execute.reset_mock()
            store.save_event({"event_id": "e1", "timestamp": 0.0, "action": "login", "metadata": {"k": "v"}})
        backend.execute.assert_called_once()

    def test_query_events_no_filters(self):
        backend = _mock_backend()
        backend.fetchall.return_value = [{"action": "login"}]
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            result = store.query_events()
        assert result == [{"action": "login"}]

    def test_query_events_with_all_filters(self):
        backend = _mock_backend()
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            store.query_events(actor="u1", tenant_id="t1", action="login", severity="critical", since=1000.0, limit=50)
        sql = backend.fetchall.call_args[0][0]
        assert "actor=%s" in sql
        assert "tenant_id=%s" in sql
        assert "action=%s" in sql
        assert "severity=%s" in sql
        assert "timestamp >= %s" in sql

    def test_summary_no_backend_data(self):
        backend = _mock_backend()
        backend.fetchall.return_value = []
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            result = store.summary()
        assert result["total_events"] == 0
        assert result["by_action"] == {}
        assert result["critical_count"] == 0

    def test_summary_with_data(self):
        backend = _mock_backend()
        backend.fetchall.return_value = [
            {"action": "login", "severity": "info"},
            {"action": "login", "severity": "critical"},
            {"action": "logout", "severity": "info"},
        ]
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            result = store.summary(tenant_id="t1", hours=12)
        assert result["total_events"] == 3
        assert result["by_action"] == {"login": 2, "logout": 1}
        assert result["critical_count"] == 1
        assert result["hours"] == 12

    def test_summary_with_tenant_filter(self):
        backend = _mock_backend()
        backend.fetchall.return_value = []
        with patch("maop.enterprise.pg_persist._get_pg_backend", return_value=backend):
            from maop.enterprise.pg_persist import PgAuditStore
            store = PgAuditStore()
            store.summary(tenant_id="t1")
        sql = backend.fetchall.call_args[0][0]
        assert "tenant_id=%s" in sql


# ── _get_pg_backend import error path ────────────────────────

class TestGetPgBackendImportError:
    def test_import_error_returns_none_and_records_degradation(self, monkeypatch):
        """When PostgreSQLStorageBackend import fails, returns None and records degradation."""
        monkeypatch.setenv("MAOP_STORAGE_BACKEND", "postgresql")

        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            # 注意：pg_persist.py:36 的延迟 import 是
            # `from maop.core.backends.backends_pg import PostgreSQLStorageBackend`，
            # 模块名是 maop.core.backends.backends_pg（此前多写 backends. 前缀
            # 导致 mock 不生效 → 真实连 PostgreSQL → PoolTimeout，CI 12 平台全挂）。
            if name == "maop.core.backends.backends_pg":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        from maop.enterprise.pg_persist import _get_pg_backend
        result = _get_pg_backend()
        assert result is None