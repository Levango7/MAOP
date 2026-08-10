"""Tests for maop.core.tenant — RLS, resource quotas, audit log, and the
enhanced TenantManager integration. Backward-compatible CRUD is also covered."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.backends.db_utils import sqlite_connect
from maop.core.tenant import (
    AuditLogger,
    QuotaError,
    RLSError,
    ResourceQuotaManager,
    TenantManager,
    TenantRLS,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return an isolated SQLite db path under tmp_path/data."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "maop.db"


@pytest.fixture
def manager(tmp_path: Path) -> TenantManager:
    """TenantManager with RLS + quota + audit enabled, scoped to tmp_path."""
    return TenantManager(root_dir=tmp_path, scoped_tables=["items"])


# ── backward-compatible CRUD ──────────────────────────────────────────


class TestTenantManagerBasics:
    def test_create_and_get(self, manager: TenantManager):
        cfg = manager.create_tenant("acme", display_name="Acme Corp")
        assert cfg.tenant_id == "acme"
        assert cfg.display_name == "Acme Corp"
        got = manager.get_tenant("acme")
        assert got is not None
        assert got.display_name == "Acme Corp"

    def test_get_missing(self, manager: TenantManager):
        assert manager.get_tenant("nope") is None

    def test_list_tenants(self, manager: TenantManager):
        manager.create_tenant("a")
        manager.create_tenant("b")
        ids = {t.tenant_id for t in manager.list_tenants()}
        assert ids == {"a", "b"}

    def test_delete(self, manager: TenantManager):
        manager.create_tenant("acme")
        assert manager.delete_tenant("acme") is True
        assert manager.get_tenant("acme") is None
        assert manager.delete_tenant("acme") is False

    def test_check_quota_within_limit(self, manager: TenantManager):
        manager.create_tenant("acme", quota_tokens=1000, quota_requests=100)
        assert manager.check_quota("acme", tokens_used=100, requests_used=1) is True

    def test_check_quota_exceeds_tokens(self, manager: TenantManager):
        manager.create_tenant("acme", quota_tokens=100)
        manager.check_quota("acme", tokens_used=90)
        assert manager.check_quota("acme", tokens_used=20) is False

    def test_check_quota_disabled_tenant(self, manager: TenantManager):
        manager.create_tenant("acme", enabled=False)
        assert manager.check_quota("acme", tokens_used=1) is False

    def test_check_agent_access(self, manager: TenantManager):
        manager.create_tenant("acme", allowed_agents=["bot1", "bot2"])
        assert manager.check_agent_access("acme", "bot1") is True
        assert manager.check_agent_access("acme", "bot3") is False

    def test_check_agent_access_empty_allows_all(self, manager: TenantManager):
        manager.create_tenant("acme")
        assert manager.check_agent_access("acme", "anything") is True

    def test_check_model_access(self, manager: TenantManager):
        manager.create_tenant("acme", allowed_models=["gpt-4o"])
        assert manager.check_model_access("acme", "gpt-4o") is True
        assert manager.check_model_access("acme", "claude") is False

    @pytest.mark.asyncio
    async def test_check_quota_async(self, manager: TenantManager):
        manager.create_tenant("acme", quota_tokens=1000)
        ok = await manager.check_quota_async("acme", tokens_used=10)
        assert ok is True


# ── TenantRLS ─────────────────────────────────────────────────────────


class TestTenantRLS:
    def _make_items_table(self, db_path: Path) -> None:
        with sqlite_connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
            )

    def test_scoped_select_adds_tenant_filter(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, params = rls.scoped_select("acme", "items")
        assert "tenant_id = ?" in sql
        assert params == ("acme",)

    def test_scoped_select_with_extra_where(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, params = rls.scoped_select("acme", "items", where="id > ?", params=(5,))
        assert "tenant_id = ?" in sql
        assert "(id > ?)" in sql
        assert params == ("acme", 5)

    def test_unscoped_table_passes_through(self, db_path: Path):
        rls = TenantRLS(db_path, scoped_tables=[])
        sql, params = rls.scoped_select("acme", "other", where="x = ?", params=(1,))
        assert "tenant_id" not in sql
        assert params == (1,)

    def test_scoped_insert_appends_tenant(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, params = rls.scoped_insert("acme", "items", ["id", "name"], [1, "widget"])
        assert "tenant_id" in sql
        assert params == (1, "widget", "acme")

    def test_scoped_insert_rejects_explicit_tenant_id(self, db_path: Path):
        rls = TenantRLS(db_path, scoped_tables=["items"])
        with pytest.raises(RLSError, match="must not be passed"):
            rls.scoped_insert("acme", "items", ["tenant_id", "name"], ["acme", "x"])

    def test_enforce_scope_ok(self):
        rls = TenantRLS.__new__(TenantRLS)
        rls._scoped_tables = {"items"}
        rls.enforce_scope("acme", "items", {"tenant_id": "acme"})

    def test_enforce_scope_mismatch(self):
        rls = TenantRLS.__new__(TenantRLS)
        rls._scoped_tables = {"items"}
        with pytest.raises(RLSError, match="cross-tenant"):
            rls.enforce_scope("acme", "items", {"tenant_id": "other"})

    def test_enforce_scope_missing(self):
        rls = TenantRLS.__new__(TenantRLS)
        rls._scoped_tables = {"items"}
        with pytest.raises(RLSError, match="no tenant_id"):
            rls.enforce_scope("acme", "items", {"name": "x"})

    def test_tenant_prefix(self):
        rls = TenantRLS.__new__(TenantRLS)
        rls._scoped_tables = set()
        assert rls.tenant_prefix("acme", "items") == "tenant_acme__items"
        assert rls.tenant_prefix("a-b.c", "items") == "tenant_a_b_c__items"

    def test_register_table_adds_column(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=[])
        rls.register_table("items")
        with sqlite_connect(db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        assert "tenant_id" in cols

    def test_rls_prevents_cross_tenant_read(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        with sqlite_connect(db_path) as conn:
            conn.execute("INSERT INTO items (id, name, tenant_id) VALUES (1, 'a', 'acme')")
            conn.execute("INSERT INTO items (id, name, tenant_id) VALUES (2, 'b', 'other')")
        with sqlite_connect(db_path) as conn:
            sql, params = rls.scoped_select("acme", "items")
            rows = conn.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "a"


# ── ResourceQuotaManager ──────────────────────────────────────────────


class TestResourceQuota:
    def test_set_and_get_quota(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "storage_mb", 500)
        q = qm.get_quota("acme", "storage_mb")
        assert q is not None
        assert q.limit == 500
        assert q.period == "total"

    def test_get_missing_quota(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        assert qm.get_quota("acme", "storage_mb") is None

    def test_list_quotas(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "storage_mb", 500)
        qm.set_quota("acme", "agents", 10)
        names = {q.resource for q in qm.list_quotas("acme")}
        assert names == {"storage_mb", "agents"}

    def test_remove_quota(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 10)
        assert qm.remove_quota("acme", "agents") is True
        assert qm.get_quota("acme", "agents") is None

    def test_check_within_limit(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 5)
        assert qm.check("acme", "agents", 3) is True

    def test_check_exceeds(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 5)
        assert qm.check("acme", "agents", 6) is False

    def test_check_unlimited(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 0)  # 0 = unlimited
        assert qm.check("acme", "agents", 999999) is True

    def test_check_strict_raises(self, db_path: Path):
        qm = ResourceQuotaManager(db_path, strict=True)
        qm.set_quota("acme", "agents", 5)
        with pytest.raises(QuotaError, match="quota exceeded"):
            qm.check("acme", "agents", 6)

    def test_consume_records_usage(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 5)
        assert qm.consume("acme", "agents", 2) is True
        usage = qm.get_usage("acme", "agents")
        assert usage.used == 2
        assert usage.remaining == 3

    def test_consume_until_exhausted(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 3)
        assert qm.consume("acme", "agents", 2) is True
        assert qm.consume("acme", "agents", 1) is True
        assert qm.consume("acme", "agents", 1) is False

    def test_daily_quota_uses_date_key(self, db_path: Path, monkeypatch):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "api_calls", 100, period="daily")
        assert qm.consume("acme", "api_calls", 50) is True
        usage = qm.get_usage("acme", "api_calls")
        assert usage.used == 50
        assert usage.period == "daily"

    def test_reset_usage(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 10)
        qm.consume("acme", "agents", 3)
        deleted = qm.reset_usage("acme", "agents")
        assert deleted == 1
        assert qm.get_usage("acme", "agents").used == 0

    def test_reset_all_usage(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 10)
        qm.set_quota("acme", "storage_mb", 100)
        qm.consume("acme", "agents", 3)
        qm.consume("acme", "storage_mb", 20)
        deleted = qm.reset_usage("acme")
        assert deleted == 2

    def test_all_usage(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 10)
        qm.set_quota("acme", "storage_mb", 100)
        usages = qm.all_usage("acme")
        assert len(usages) == 2

    def test_usage_exceeded_property(self, db_path: Path):
        qm = ResourceQuotaManager(db_path)
        qm.set_quota("acme", "agents", 2)
        qm.consume("acme", "agents", 2)
        usage = qm.get_usage("acme", "agents")
        assert usage.exceeded is True
        assert usage.remaining == 0


# ── AuditLogger ───────────────────────────────────────────────────────


class TestAuditLogger:
    def test_log_returns_entry(self, db_path: Path):
        al = AuditLogger(db_path)
        entry = al.log("acme", "data.read", resource="items", actor="user1")
        assert entry.tenant_id == "acme"
        assert entry.action == "data.read"
        assert entry.seq == 1
        assert entry.hash != ""
        assert entry.prev_hash == ""

    def test_seq_increments(self, db_path: Path):
        al = AuditLogger(db_path)
        e1 = al.log("acme", "a")
        e2 = al.log("acme", "b")
        e3 = al.log("acme", "c")
        assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)

    def test_seq_independent_per_tenant(self, db_path: Path):
        al = AuditLogger(db_path)
        a1 = al.log("acme", "a")
        b1 = al.log("other", "a")
        a2 = al.log("acme", "b")
        assert a1.seq == 1
        assert b1.seq == 1
        assert a2.seq == 2

    def test_hash_chain_links(self, db_path: Path):
        al = AuditLogger(db_path)
        e1 = al.log("acme", "a")
        e2 = al.log("acme", "b")
        assert e2.prev_hash == e1.hash

    def test_verify_chain_intact(self, db_path: Path):
        al = AuditLogger(db_path)
        for i in range(5):
            al.log("acme", "a", detail={"i": i})
        assert al.verify_chain("acme") is True

    def test_verify_chain_detects_tamper(self, db_path: Path):
        al = AuditLogger(db_path)
        al.log("acme", "a")
        al.log("acme", "b")
        # Tamper: corrupt the hash of seq 2.
        with sqlite_connect(db_path) as conn:
            conn.execute(
                "UPDATE tenant_audit_log SET hash = 'tampered' "
                "WHERE tenant_id = ? AND seq = 2",
                ("acme",),
            )
        assert al.verify_chain("acme") is False

    def test_query_filters(self, db_path: Path):
        al = AuditLogger(db_path)
        al.log("acme", "data.read", resource="items")
        al.log("acme", "data.write", resource="items")
        al.log("acme", "data.read", resource="users")
        al.log("other", "data.read", resource="items")
        reads = al.query("acme", action="data.read")
        assert len(reads) == 2
        items_reads = al.query("acme", action="data.read", resource="items")
        assert len(items_reads) == 1

    def test_query_limit_and_offset(self, db_path: Path):
        al = AuditLogger(db_path)
        for i in range(10):
            al.log("acme", "a")
        page = al.query("acme", limit=3, offset=0)
        assert len(page) == 3
        # newest first (ORDER BY seq DESC)
        assert page[0].seq == 10

    def test_count(self, db_path: Path):
        al = AuditLogger(db_path)
        al.log("acme", "a")
        al.log("acme", "b")
        al.log("acme", "a")
        assert al.count("acme") == 3
        assert al.count("acme", action="a") == 2

    def test_get_entry(self, db_path: Path):
        al = AuditLogger(db_path)
        al.log("acme", "a", detail={"k": "v"})
        entry = al.get_entry("acme", 1)
        assert entry is not None
        assert entry.detail == {"k": "v"}
        assert al.get_entry("acme", 999) is None

    def test_detail_truncation(self, db_path: Path):
        al = AuditLogger(db_path, max_detail_bytes=64)
        big = {"data": "x" * 200}
        entry = al.log("acme", "a", detail=big)
        # When the detail exceeds the byte limit it is replaced by a marker.
        assert entry.detail.get("_truncated") is True
        assert "_len" in entry.detail

    def test_cross_tenant_query_isolation(self, db_path: Path):
        al = AuditLogger(db_path)
        al.log("acme", "secret", detail={"k": "acme-val"})
        al.log("other", "secret", detail={"k": "other-val"})
        acme_entries = al.query("acme")
        assert all(e.tenant_id == "acme" for e in acme_entries)
        assert len(acme_entries) == 1


# ── TenantManager integration ─────────────────────────────────────────


class TestTenantIntegration:
    def test_audit_log_via_manager(self, manager: TenantManager):
        manager.create_tenant("acme")
        entry = manager.audit_log("acme", "data.read", resource="items")
        assert entry is not None
        assert entry.tenant_id == "acme"
        log = manager.get_audit_log("acme")
        assert len(log) >= 1

    def test_create_tenant_writes_audit(self, manager: TenantManager):
        manager.create_tenant("acme")
        log = manager.get_audit_log("acme")
        actions = {e.action for e in log}
        assert "tenant.create" in actions

    def test_quota_breach_writes_audit(self, manager: TenantManager):
        manager.create_tenant("acme", quota_tokens=10)
        manager.check_quota("acme", tokens_used=5)
        manager.check_quota("acme", tokens_used=10)  # breach
        log = manager.get_audit_log("acme")
        actions = {e.action for e in log}
        assert "quota.breach" in actions

    def test_check_resource_quota(self, manager: TenantManager):
        manager.create_tenant("acme")
        assert manager.quota is not None
        manager.quota.set_quota("acme", "agents", 5)
        assert manager.check_resource_quota("acme", "agents", 3) is True
        assert manager.check_resource_quota("acme", "agents", 10) is False

    def test_check_resource_quota_breach_audited(self, manager: TenantManager):
        manager.create_tenant("acme")
        assert manager.quota is not None
        manager.quota.set_quota("acme", "agents", 1)
        manager.check_resource_quota("acme", "agents", 5)
        log = manager.get_audit_log("acme")
        assert any(e.action == "quota.breach" for e in log)

    def test_scoped_select_via_manager(self, manager: TenantManager):
        # Create the items table and let RLS attach tenant_id.
        with sqlite_connect(manager._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
            )
        manager.rls.register_table("items")
        sql, params = manager.scoped_select("acme", "items")
        assert "tenant_id = ?" in sql
        assert params == ("acme",)

    def test_scoped_insert_via_manager(self, manager: TenantManager):
        with sqlite_connect(manager._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
            )
        manager.rls.register_table("items")
        sql, params = manager.scoped_insert("acme", "items", ["id", "name"], [1, "x"])
        with sqlite_connect(manager._db_path) as conn:
            conn.execute(sql, params)
            row = conn.execute("SELECT * FROM items WHERE id = 1").fetchone()
        assert row["tenant_id"] == "acme"

    def test_rls_property_exposes_subsystem(self, manager: TenantManager):
        assert isinstance(manager.rls, TenantRLS)
        assert manager.quota is not None
        assert isinstance(manager.quota, ResourceQuotaManager)
        assert isinstance(manager.audit, AuditLogger)

    def test_tenant_isolation_end_to_end(self, manager: TenantManager):
        """Two tenants cannot see each other's rows via scoped_select."""
        with sqlite_connect(manager._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
            )
        manager.rls.register_table("items")
        # Insert rows for two tenants.
        for tenant, idx in [("acme", 1), ("other", 2)]:
            sql, params = manager.scoped_insert(tenant, "items", ["id", "name"], [idx, tenant])
            with sqlite_connect(manager._db_path) as conn:
                conn.execute(sql, params)
        # acme sees only its row.
        sql, params = manager.scoped_select("acme", "items")
        with sqlite_connect(manager._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "acme"
        # other sees only its row.
        sql, params = manager.scoped_select("other", "items")
        with sqlite_connect(manager._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "other"

    def test_audit_disabled(self, tmp_path: Path):
        mgr = TenantManager(root_dir=tmp_path, enable_audit=False)
        assert mgr.audit is None
        # audit_log is a no-op
        assert mgr.audit_log("acme", "x") is None
        assert mgr.get_audit_log("acme") == []

    def test_quota_disabled(self, tmp_path: Path):
        mgr = TenantManager(root_dir=tmp_path, enable_quota=False)
        assert mgr.quota is None
        # check_resource_quota returns True (no enforcement)
        assert mgr.check_resource_quota("acme", "agents", 999) is True