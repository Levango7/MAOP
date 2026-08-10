"""Tests for compliance (G-04 cascading deletion + G-07 tenant_id from JWT).

Tests:
  - ComplianceManager.delete_user_data cascades across data stores
  - ComplianceManager.export_user_data exports from all data stores
  - tenant_id filtering works correctly
  - Audit retention by default
  - RBAC router takes tenant_id from JWT (request.state.tenant_id)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maop.core.tenant.compliance import ComplianceManager


# ── Test fixtures ───────────────────────────────────────────────────


@pytest.fixture
def compliance_db(tmp_path: Path) -> tuple[Path, ComplianceManager]:
    """Create a test database with all compliance-related tables."""
    db_path = tmp_path / "data" / "maop.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    # Create all tables that ComplianceManager touches.
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            data TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE memory_entries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            content TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE long_term_memory (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            content TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE short_term_memory (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            content TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            name TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE rbac_grants (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            role TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE audit_entries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT DEFAULT '',
            action TEXT DEFAULT ''
        )
    """)

    # Insert test data.
    conn.execute("INSERT INTO sessions VALUES ('s1', 'user1', 'tenant1', 'data1')")
    conn.execute("INSERT INTO sessions VALUES ('s2', 'user1', 'tenant2', 'data2')")
    conn.execute("INSERT INTO sessions VALUES ('s3', 'user2', 'tenant1', 'data3')")

    conn.execute("INSERT INTO memory_entries VALUES ('m1', 'user1', 'tenant1', 'mem1')")
    conn.execute("INSERT INTO long_term_memory VALUES ('m2', 'user1', 'tenant1', 'mem2')")
    conn.execute("INSERT INTO short_term_memory VALUES ('m3', 'user1', 'tenant1', 'mem3')")

    conn.execute("INSERT INTO agents VALUES ('a1', 'user1', 'tenant1', 'agent1')")
    conn.execute("INSERT INTO agents VALUES ('a2', 'user2', 'tenant1', 'agent2')")

    conn.execute("INSERT INTO rbac_grants VALUES ('g1', 'user1', 'tenant1', 'admin')")
    conn.execute("INSERT INTO rbac_grants VALUES ('g2', 'user1', 'tenant2', 'viewer')")

    conn.execute("INSERT INTO audit_entries VALUES ('au1', 'user1', 'tenant1', 'login')")
    conn.execute("INSERT INTO audit_entries VALUES ('au2', 'user1', 'tenant2', 'logout')")

    conn.commit()
    conn.close()

    mgr = ComplianceManager(tmp_path)
    return tmp_path, mgr


# ── G-04: Cascading deletion tests ─────────────────────────────────


class TestDeleteUserData:
    """Test cascading user data deletion (G-04)."""

    def test_delete_cascades_all_data_sources(self, compliance_db):
        """delete_user_data removes data from all stores."""
        tmp_path, mgr = compliance_db
        report = mgr.delete_user_data("user1", tenant_id="tenant1")

        assert report.success is True
        assert report.user_id == "user1"
        assert report.tenant_id == "tenant1"
        # Sessions deleted.
        assert report.items_deleted.get("sessions", 0) == 1
        # Memory entries deleted (from all 3 memory tables).
        assert report.items_deleted.get("memory", 0) == 3
        # Agents deleted.
        assert report.items_deleted.get("agents", 0) == 1
        # RBAC grants deleted.
        assert report.items_deleted.get("rbac_grants", 0) == 1
        # Total > 0.
        assert report.total_deleted > 0

    def test_delete_retains_audit_by_default(self, compliance_db):
        """Audit entries are retained by default (compliance requirement)."""
        tmp_path, mgr = compliance_db
        report = mgr.delete_user_data("user1", tenant_id="tenant1")

        assert "audit_logs" in report.items_retained
        assert report.items_retained["audit_logs"] >= 1
        # Audit not in deleted.
        assert "audit_logs" not in report.items_deleted

    def test_delete_removes_audit_when_configured(self, compliance_db):
        """Audit entries are deleted when retain_audit=False."""
        tmp_path, _ = compliance_db
        mgr = ComplianceManager(tmp_path, retain_audit=False)
        report = mgr.delete_user_data("user1", tenant_id="tenant1")

        assert "audit_logs" in report.items_deleted
        assert report.items_deleted["audit_logs"] >= 1

    def test_delete_tenant_filtering(self, compliance_db):
        """delete_user_data only deletes within the specified tenant."""
        tmp_path, mgr = compliance_db
        report = mgr.delete_user_data("user1", tenant_id="tenant1")

        # Only tenant1 sessions deleted (1 session), tenant2 session remains.
        assert report.items_deleted.get("sessions", 0) == 1

        # Verify tenant2 data still exists.
        conn = sqlite3.connect(str(tmp_path / "data" / "maop.db"))
        remaining = conn.execute(
            "SELECT * FROM sessions WHERE user_id='user1' AND tenant_id='tenant2'",
        ).fetchall()
        assert len(remaining) == 1
        conn.close()

    def test_delete_nonexistent_user(self, compliance_db):
        """Deleting a non-existent user returns success with 0 deletions."""
        tmp_path, mgr = compliance_db
        report = mgr.delete_user_data("nonexistent", tenant_id="tenant1")

        assert report.success is True
        assert report.total_deleted == 0

    def test_delete_without_tenant_id(self, compliance_db):
        """delete_user_data without tenant_id deletes across all tenants."""
        tmp_path, mgr = compliance_db
        report = mgr.delete_user_data("user1")

        assert report.success is True
        # Should delete sessions from both tenants.
        assert report.items_deleted.get("sessions", 0) == 2
        # Should delete RBAC grants from both tenants.
        assert report.items_deleted.get("rbac_grants", 0) == 2


# ── G-04: Data export tests ─────────────────────────────────────────


class TestExportUserData:
    """Test user data export (G-04)."""

    def test_export_all_data_sources(self, compliance_db):
        """export_user_data exports from all stores."""
        tmp_path, mgr = compliance_db
        report = mgr.export_user_data("user1", tenant_id="tenant1")

        assert report.success is True
        assert report.user_id == "user1"
        assert report.tenant_id == "tenant1"
        # Sessions exported.
        assert len(report.data.get("sessions", [])) == 1
        # Memory exported.
        assert len(report.data.get("memory", [])) == 3
        # Agents exported.
        assert len(report.data.get("agents", [])) == 1
        # RBAC grants exported.
        assert len(report.data.get("rbac_grants", [])) == 1
        # Audit logs exported.
        assert len(report.data.get("audit_logs", [])) >= 1
        # Total items > 0.
        assert report.total_items > 0

    def test_export_tenant_filtering(self, compliance_db):
        """export_user_data only exports within the specified tenant."""
        tmp_path, mgr = compliance_db
        report = mgr.export_user_data("user1", tenant_id="tenant1")

        # Only tenant1 sessions exported.
        assert len(report.data.get("sessions", [])) == 1
        for session in report.data["sessions"]:
            assert session["tenant_id"] == "tenant1"

    def test_export_without_tenant_id(self, compliance_db):
        """export_user_data without tenant_id exports across all tenants."""
        tmp_path, mgr = compliance_db
        report = mgr.export_user_data("user1")

        assert report.success is True
        # Should export sessions from both tenants.
        assert len(report.data.get("sessions", [])) == 2

    def test_export_nonexistent_user(self, compliance_db):
        """Exporting a non-existent user returns success with empty data."""
        tmp_path, mgr = compliance_db
        report = mgr.export_user_data("nonexistent", tenant_id="tenant1")

        assert report.success is True
        assert report.total_items == 0

    def test_export_memory_has_source_table(self, compliance_db):
        """Exported memory entries include _source_table for traceability."""
        tmp_path, mgr = compliance_db
        report = mgr.export_user_data("user1", tenant_id="tenant1")

        for entry in report.data.get("memory", []):
            assert "_source_table" in entry


# ── G-07: RBAC router tenant_id from JWT tests ─────────────────────


class TestRBACTenantIdFromJWT:
    """Test that RBAC router takes tenant_id from JWT, not body (G-07)."""

    def test_rbac_grant_request_has_no_tenant_id_field(self):
        """GrantRequest model does NOT accept tenant_id from body."""
        from maop.dashboard.routers.rbac import GrantRequest
        # tenant_id should not be a field in the body model.
        assert "tenant_id" not in GrantRequest.model_fields

    def test_rbac_revoke_request_has_no_tenant_id_field(self):
        """RevokeRequest model does NOT accept tenant_id from body."""
        from maop.dashboard.routers.rbac import RevokeRequest
        assert "tenant_id" not in RevokeRequest.model_fields

    def test_tenant_id_from_jwt_helper(self):
        """_tenant_id_from_jwt reads from request.state.tenant_id."""
        from maop.dashboard.routers.rbac import _tenant_id_from_jwt

        request = MagicMock()
        request.state.tenant_id = "tenant-from-jwt"
        assert _tenant_id_from_jwt(request) == "tenant-from-jwt"

    def test_tenant_id_from_jwt_default_empty(self):
        """_tenant_id_from_jwt returns empty string if not set."""
        from maop.dashboard.routers.rbac import _tenant_id_from_jwt

        request = MagicMock()
        # Remove tenant_id attribute to simulate absence.
        del request.state.tenant_id
        with patch.object(MagicMock, "__getattr__", return_value=""):
            result = _tenant_id_from_jwt(request)
            assert result == ""


# ── G-07: Compliance router tenant_id from JWT tests ───────────────


class TestComplianceTenantIdFromJWT:
    """Test that compliance router takes tenant_id from JWT (G-07)."""

    def test_delete_request_has_no_tenant_id_field(self):
        """DeleteUserDataRequest does NOT accept tenant_id from body."""
        from maop.dashboard.routers.compliance import DeleteUserDataRequest
        assert "tenant_id" not in DeleteUserDataRequest.model_fields

    def test_export_request_has_no_tenant_id_field(self):
        """ExportUserDataRequest does NOT accept tenant_id from body."""
        from maop.dashboard.routers.compliance import ExportUserDataRequest
        assert "tenant_id" not in ExportUserDataRequest.model_fields

    def test_tenant_id_from_jwt_helper(self):
        """_tenant_id_from_jwt reads from request.state.tenant_id."""
        from maop.dashboard.routers.compliance import _tenant_id_from_jwt

        request = MagicMock()
        request.state.tenant_id = "tenant-from-jwt"
        assert _tenant_id_from_jwt(request) == "tenant-from-jwt"

    def test_tenant_id_from_jwt_raises_when_missing(self):
        """_tenant_id_from_jwt raises 403 when tenant_id is missing."""
        from maop.dashboard.routers.compliance import _tenant_id_from_jwt
        from fastapi import HTTPException

        request = MagicMock()
        del request.state.tenant_id
        with patch.object(MagicMock, "__getattr__", return_value=""):
            with pytest.raises(HTTPException) as exc_info:
                _tenant_id_from_jwt(request)
            assert exc_info.value.status_code == 403
