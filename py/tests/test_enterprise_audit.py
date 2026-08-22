"""Tests for maop.enterprise.audit.EnterpriseAuditLogger — structured audit logging, query, and summary."""

from __future__ import annotations

import time

import pytest

# H4 修复：将 importorskip 改为显式 pytest.skip，让测试报告显式统计跳过数。
pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)

from maop.enterprise.audit import (
    AuditAction,
    AuditSeverity,
    EnterpriseAuditLogger,
)


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.AUDIT_LOG) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force in-memory audit storage (no PostgreSQL backend)."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


def test_log_creates_event():
    """log() returns an AuditEvent with a non-empty event_id and positive timestamp."""
    logger = EnterpriseAuditLogger()
    event = logger.log(AuditAction.LOGIN, actor="alice")
    assert event.event_id != ""
    assert event.timestamp > 0
    assert event.actor == "alice"


def test_log_stores_events():
    """Multiple log() calls grow the internal event list."""
    logger = EnterpriseAuditLogger()
    assert len(logger._events) == 0
    logger.log(AuditAction.LOGIN, actor="a")
    logger.log(AuditAction.LOGOUT, actor="a")
    logger.log(AuditAction.API_CALL, actor="a")
    assert len(logger._events) == 3


def test_log_critical_severity():
    """Logging a CRITICAL event does not raise."""
    logger = EnterpriseAuditLogger()
    event = logger.log(
        AuditAction.SYSTEM_ADMIN, actor="root", severity=AuditSeverity.CRITICAL
    )
    assert event.severity == AuditSeverity.CRITICAL


def test_query_by_actor():
    """query(actor=...) returns only events for that actor."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="alice")
    logger.log(AuditAction.LOGIN, actor="bob")
    logger.log(AuditAction.LOGOUT, actor="alice")
    result = logger.query(actor="alice")
    assert len(result) == 2
    assert all(e.actor == "alice" for e in result)


def test_query_by_tenant():
    """query(tenant_id=...) returns only events for that tenant."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.API_CALL, actor="a", tenant_id="t1")
    logger.log(AuditAction.API_CALL, actor="b", tenant_id="t2")
    logger.log(AuditAction.API_CALL, actor="c", tenant_id="t1")
    result = logger.query(tenant_id="t1")
    assert len(result) == 2
    assert all(e.tenant_id == "t1" for e in result)


def test_query_by_action():
    """query(action=...) filters by action type."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a")
    logger.log(AuditAction.LOGOUT, actor="a")
    logger.log(AuditAction.LOGIN, actor="b")
    result = logger.query(action=AuditAction.LOGIN)
    assert len(result) == 2
    assert all(e.action == AuditAction.LOGIN for e in result)


def test_query_by_severity():
    """query(severity=...) filters by severity."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a", severity=AuditSeverity.INFO)
    logger.log(AuditAction.SYSTEM_ADMIN, actor="b", severity=AuditSeverity.CRITICAL)
    logger.log(AuditAction.CONFIG_CHANGE, actor="c", severity=AuditSeverity.WARNING)
    result = logger.query(severity=AuditSeverity.CRITICAL)
    assert len(result) == 1
    assert result[0].severity == AuditSeverity.CRITICAL


def test_query_since():
    """query(since=future_time) returns no events."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a")
    future = time.time() + 1000
    result = logger.query(since=future)
    assert result == []


def test_query_limit():
    """query(limit=2) returns at most 2 events."""
    logger = EnterpriseAuditLogger()
    for i in range(5):
        logger.log(AuditAction.API_CALL, actor=f"u{i}")
    result = logger.query(limit=2)
    assert len(result) == 2


def test_summary_returns_dict():
    """summary() returns a dict with the expected keys and counts."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a")
    logger.log(AuditAction.SYSTEM_ADMIN, actor="b", severity=AuditSeverity.CRITICAL)
    s = logger.summary()
    assert isinstance(s, dict)
    assert s["total_events"] == 2
    assert "by_action" in s
    assert s["by_action"].get("login") == 1
    assert s["critical_count"] == 1
    assert s["hours"] == 24


def test_summary_filters_tenant():
    """summary(tenant_id=...) only counts events for that tenant."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a", tenant_id="t1")
    logger.log(AuditAction.LOGIN, actor="b", tenant_id="t2")
    logger.log(AuditAction.LOGIN, actor="c", tenant_id="t1")
    s = logger.summary(tenant_id="t1")
    assert s["total_events"] == 2


def test_summary_filters_hours():
    """summary(hours=0) returns 0 events because the window starts at now."""
    logger = EnterpriseAuditLogger()
    logger.log(AuditAction.LOGIN, actor="a")
    # ensure the summary's `now` is strictly after the event's timestamp
    time.sleep(0.01)
    s = logger.summary(hours=0)
    assert s["total_events"] == 0


def test_max_events_trim():
    """Setting _max_events trims the event list to the most recent entries."""
    logger = EnterpriseAuditLogger()
    logger._max_events = 3
    for i in range(5):
        logger.log(AuditAction.API_CALL, actor=f"u{i}")
    assert len(logger._events) == 3
