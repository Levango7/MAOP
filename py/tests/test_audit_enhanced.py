"""Tests for audit enhancement — advanced filtering, statistics, alert engine, and router endpoints.

Covers:
  - Pydantic models (AuditEventQuery, AuditAlertRuleCreate, AuditStatsResponse, etc.)
  - AuditAlertEngine: rule CRUD, threshold/pattern/anomaly evaluation,
    acknowledgement, broadcaster callback.
  - Statistics helpers: compute_stats, compute_timeline, compute_heatmap,
    filter_events, export_events_csv, export_events_json.
  - Router endpoints under /api/audit/* (enterprise edition, in-memory store).
  - WebSocket broadcaster hook (no real WS — just verifies the callback path).

All tests run in enterprise edition with an in-memory audit logger (no PG).
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.enterprise.audit import (
    AuditAction,
    AuditEvent,
    AuditRiskLevel,
    AuditSeverity,
    EnterpriseAuditLogger,
)
from maop.enterprise.audit_enhanced import (
    AlertBroadcaster,  # noqa: F401  (type alias import for completeness)
    AlertConditionType,
    AlertSeverity,
    AuditAlertEngine,
    AuditAlertRuleCreate,
    AuditAlertRuleUpdate,
    AuditEventQuery,
    AuditHeatmapCell,
    AuditStatsResponse,
    AuditTimelinePoint,
    compute_heatmap,
    compute_stats,
    compute_timeline,
    export_events_csv,
    export_events_json,
    filter_events,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so FeatureFlag.AUDIT_LOG gates pass."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force in-memory audit storage (no PostgreSQL backend)."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


@pytest.fixture
def fresh_logger(monkeypatch):
    """Return a fresh EnterpriseAuditLogger and patch the router singleton."""
    logger = EnterpriseAuditLogger()
    # Patch the router's singleton so endpoints use this fresh instance.
    from maop.dashboard.routers import audit as audit_router
    monkeypatch.setattr(audit_router, "_enterprise_logger", logger)
    return logger


@pytest.fixture
def fresh_engine(monkeypatch):
    """Return a fresh AuditAlertEngine and patch the router singleton."""
    engine = AuditAlertEngine()
    from maop.dashboard.routers import audit as audit_router
    monkeypatch.setattr(audit_router, "_alert_engine", engine)
    return engine


@pytest.fixture
def client(fresh_logger, fresh_engine):
    """FastAPI TestClient with admin role injected via middleware."""
    from maop.dashboard.routers import audit as audit_router

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "test-admin"
        return await call_next(request)

    app.include_router(audit_router.router)
    return TestClient(app)


def _make_event(
    *,
    action: AuditAction = AuditAction.LOGIN,
    severity: AuditSeverity = AuditSeverity.INFO,
    risk_level: AuditRiskLevel = AuditRiskLevel.LOW,
    actor: str = "alice",
    tenant_id: str = "",
    resource: str = "",
    result: str = "success",
    category: str = "",
    tags: list[str] | None = None,
    detail: str = "",
    timestamp: float | None = None,
) -> AuditEvent:
    """Build an AuditEvent directly (bypassing the logger)."""
    return AuditEvent(
        event_id=f"evt_{int(time.time() * 1000)}_{_make_event._counter}",
        timestamp=timestamp if timestamp is not None else time.time(),
        action=action,
        severity=severity,
        risk_level=risk_level,
        actor=actor,
        tenant_id=tenant_id,
        resource=resource,
        result=result,
        category=category,
        tags=tags or [],
        detail=detail,
    )


_make_event._counter = 0  # type: ignore[attr-defined]


def _next_event_id() -> str:
    _make_event._counter += 1  # type: ignore[attr-defined]
    return f"evt_{_make_event._counter}"  # type: ignore[attr-defined]


# ── Pydantic model tests ──────────────────────────────────────────


class TestPydanticModels:
    def test_audit_event_query_defaults(self):
        q = AuditEventQuery()
        assert q.limit == 100
        assert q.offset == 0
        assert q.sort == "timestamp_desc"
        assert q.actions == []

    def test_audit_event_query_validation(self):
        with pytest.raises(ValueError):
            AuditEventQuery(limit=0)
        with pytest.raises(ValueError):
            AuditEventQuery(limit=10001)
        with pytest.raises(ValueError):
            AuditEventQuery(offset=-1)

    def test_audit_alert_rule_create_minimal(self):
        rule = AuditAlertRuleCreate(name="test rule")
        assert rule.enabled is True
        assert rule.condition_type == AlertConditionType.THRESHOLD
        assert rule.severity == AlertSeverity.WARNING

    def test_audit_alert_rule_create_name_required(self):
        with pytest.raises(ValueError):
            AuditAlertRuleCreate()

    def test_audit_alert_rule_update_all_optional(self):
        u = AuditAlertRuleUpdate()
        assert u.name is None
        assert u.enabled is None

    def test_audit_stats_response_defaults(self):
        s = AuditStatsResponse()
        assert s.total_events == 0
        assert s.by_action == {}

    def test_audit_timeline_point(self):
        p = AuditTimelinePoint(ts=1.0, count=5)
        assert p.severity_breakdown == {}

    def test_audit_heatmap_cell(self):
        c = AuditHeatmapCell(day=0, hour=12, count=10, critical_count=2)
        assert c.day == 0
        assert c.hour == 12


# ── AuditEvent field tests ────────────────────────────────────────


class TestAuditEventFields:
    def test_event_has_new_fields(self):
        e = AuditEvent()
        assert e.risk_level == AuditRiskLevel.LOW
        assert e.category == ""
        assert e.tags == []

    def test_event_with_new_fields(self):
        e = AuditEvent(
            action=AuditAction.LOGIN,
            risk_level=AuditRiskLevel.HIGH,
            category="auth",
            tags=["suspicious", "off-hours"],
        )
        assert e.risk_level == AuditRiskLevel.HIGH
        assert e.category == "auth"
        assert e.tags == ["suspicious", "off-hours"]


# ── EnterpriseAuditLogger.log with new fields ────────────────────


class TestLoggerNewFields:
    def test_log_with_risk_level(self, fresh_logger):
        e = fresh_logger.log(
            AuditAction.LOGIN, actor="alice",
            risk_level=AuditRiskLevel.HIGH,
            category="auth",
            tags=["vpn"],
        )
        assert e.risk_level == AuditRiskLevel.HIGH
        assert e.category == "auth"
        assert e.tags == ["vpn"]

    def test_log_auto_derives_risk_from_severity(self, fresh_logger):
        e = fresh_logger.log(
            AuditAction.SYSTEM_ADMIN, actor="root",
            severity=AuditSeverity.CRITICAL,
        )
        assert e.risk_level == AuditRiskLevel.CRITICAL

    def test_log_warning_severity_maps_medium_risk(self, fresh_logger):
        e = fresh_logger.log(
            AuditAction.CONFIG_CHANGE, actor="bob",
            severity=AuditSeverity.WARNING,
        )
        assert e.risk_level == AuditRiskLevel.MEDIUM

    def test_query_by_risk_level(self, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="a", risk_level=AuditRiskLevel.LOW)
        fresh_logger.log(AuditAction.LOGIN, actor="b", risk_level=AuditRiskLevel.HIGH)
        fresh_logger.log(AuditAction.LOGIN, actor="c", risk_level=AuditRiskLevel.HIGH)
        result = fresh_logger.query(risk_level=AuditRiskLevel.HIGH)
        assert len(result) == 2
        assert all(e.risk_level == AuditRiskLevel.HIGH for e in result)

    def test_query_by_category(self, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="a", category="auth")
        fresh_logger.log(AuditAction.API_CALL, actor="b", category="data")
        result = fresh_logger.query(category="auth")
        assert len(result) == 1
        assert result[0].category == "auth"

    def test_query_by_tags(self, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="a", tags=["suspicious", "vpn"])
        fresh_logger.log(AuditAction.LOGIN, actor="b", tags=["vpn"])
        fresh_logger.log(AuditAction.LOGIN, actor="c", tags=["normal"])
        result = fresh_logger.query(tags=["suspicious"])
        assert len(result) == 1
        assert "suspicious" in result[0].tags

    def test_query_by_resource_and_result(self, fresh_logger):
        fresh_logger.log(AuditAction.API_CALL, actor="a", resource="/api/data", result="success")
        fresh_logger.log(AuditAction.API_CALL, actor="b", resource="/api/data", result="error")
        result = fresh_logger.query(resource="/api/data", result="error")
        assert len(result) == 1
        assert result[0].result == "error"

    def test_summary_includes_risk_and_category(self, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="a", risk_level=AuditRiskLevel.HIGH, category="auth")
        fresh_logger.log(AuditAction.API_CALL, actor="b", risk_level=AuditRiskLevel.LOW, category="data")
        s = fresh_logger.summary()
        assert "by_risk_level" in s
        assert "by_category" in s
        assert s["by_risk_level"].get("high") == 1
        assert s["by_category"].get("auth") == 1


# ── AuditAlertEngine: rule CRUD ───────────────────────────────────


class TestAlertRuleCRUD:
    def test_create_rule(self, fresh_engine):
        create = AuditAlertRuleCreate(
            name="Failed logins",
            description="Alert on 5+ failed logins in 5 min",
            condition_type=AlertConditionType.THRESHOLD,
            condition={"metric": "count", "window_s": 300, "op": ">=", "value": 5,
                       "filter": {"action": "login", "result": "failure"}},
        )
        rule = fresh_engine.create_rule(create, created_by="admin")
        assert rule.rule_id.startswith("rule_")
        assert rule.name == "Failed logins"
        assert rule.enabled is True
        assert rule.created_by == "admin"

    def test_get_rule(self, fresh_engine):
        create = AuditAlertRuleCreate(name="test")
        rule = fresh_engine.create_rule(create)
        fetched = fresh_engine.get_rule(rule.rule_id)
        assert fetched is not None
        assert fetched.rule_id == rule.rule_id

    def test_get_rule_not_found(self, fresh_engine):
        assert fresh_engine.get_rule("nonexistent") is None

    def test_list_rules(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(name="a"))
        fresh_engine.create_rule(AuditAlertRuleCreate(name="b"))
        rules = fresh_engine.list_rules()
        assert len(rules) == 2

    def test_list_rules_enabled_only(self, fresh_engine):
        r1 = fresh_engine.create_rule(AuditAlertRuleCreate(name="a", enabled=True))
        fresh_engine.create_rule(AuditAlertRuleCreate(name="b", enabled=False))
        rules = fresh_engine.list_rules(enabled_only=True)
        assert len(rules) == 1
        assert rules[0].rule_id == r1.rule_id

    def test_update_rule(self, fresh_engine):
        rule = fresh_engine.create_rule(AuditAlertRuleCreate(name="original"))
        updated = fresh_engine.update_rule(
            rule.rule_id,
            AuditAlertRuleUpdate(name="renamed", enabled=False),
        )
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.enabled is False

    def test_update_rule_not_found(self, fresh_engine):
        result = fresh_engine.update_rule("nope", AuditAlertRuleUpdate(name="x"))
        assert result is None

    def test_delete_rule(self, fresh_engine):
        rule = fresh_engine.create_rule(AuditAlertRuleCreate(name="to delete"))
        assert fresh_engine.delete_rule(rule.rule_id) is True
        assert fresh_engine.get_rule(rule.rule_id) is None

    def test_delete_rule_not_found(self, fresh_engine):
        assert fresh_engine.delete_rule("nope") is False


# ── AuditAlertEngine: threshold evaluation ────────────────────────


class TestThresholdEvaluation:
    def test_threshold_triggers_after_n_events(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="5 failed logins",
            condition_type=AlertConditionType.THRESHOLD,
            condition={"window_s": 300, "op": ">=", "value": 3,
                       "filter": {"action": "login", "result": "failure"}},
        ))
        # First two events: no alert
        for _ in range(2):
            e = _make_event(action=AuditAction.LOGIN, result="failure")
            alerts = fresh_engine.evaluate_event(e)
            assert alerts == []
        # Third event: triggers
        e = _make_event(action=AuditAction.LOGIN, result="failure")
        alerts = fresh_engine.evaluate_event(e)
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"

    def test_threshold_does_not_trigger_for_non_matching_filter(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="failed logins only",
            condition_type=AlertConditionType.THRESHOLD,
            condition={"window_s": 300, "op": ">=", "value": 2,
                       "filter": {"result": "failure"}},
        ))
        # Successful logins should not count
        for _ in range(5):
            e = _make_event(action=AuditAction.LOGIN, result="success")
            alerts = fresh_engine.evaluate_event(e)
            assert alerts == []

    def test_threshold_window_expiry(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="short window",
            condition_type=AlertConditionType.THRESHOLD,
            condition={"window_s": 10, "op": ">=", "value": 3,
                       "filter": {"action": "login"}},
        ))
        now = time.time()
        # Two events at t-100 (outside the 10s window)
        for _ in range(2):
            e = _make_event(action=AuditAction.LOGIN, timestamp=now - 100)
            fresh_engine.evaluate_event(e)
        # One event at now — should not trigger (window only has 1 event)
        e = _make_event(action=AuditAction.LOGIN, timestamp=now)
        alerts = fresh_engine.evaluate_event(e)
        assert alerts == []


# ── AuditAlertEngine: pattern evaluation ──────────────────────────


class TestPatternEvaluation:
    def test_pattern_matches_actor_regex(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="anonymous actor",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": "^anonymous"},
        ))
        e = _make_event(actor="anonymous_user")
        alerts = fresh_engine.evaluate_event(e)
        assert len(alerts) == 1

    def test_pattern_no_match(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="anonymous actor",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": "^anonymous"},
        ))
        e = _make_event(actor="alice")
        alerts = fresh_engine.evaluate_event(e)
        assert alerts == []

    def test_pattern_invalid_regex_no_trigger(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="bad regex",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": "["},  # invalid
        ))
        e = _make_event(actor="alice")
        alerts = fresh_engine.evaluate_event(e)
        assert alerts == []


# ── AuditAlertEngine: anomaly evaluation ──────────────────────────


class TestAnomalyEvaluation:
    def test_anomaly_triggers_on_burst(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="action burst",
            condition_type=AlertConditionType.ANOMALY,
            condition={"field": "action", "window_s": 60, "min_occurrences": 3},
        ))
        for _ in range(2):
            e = _make_event(action=AuditAction.API_CALL)
            alerts = fresh_engine.evaluate_event(e)
            assert alerts == []
        # Third event triggers
        e = _make_event(action=AuditAction.API_CALL)
        alerts = fresh_engine.evaluate_event(e)
        assert len(alerts) == 1


# ── AuditAlertEngine: alert history & acknowledgement ─────────────


class TestAlertHistory:
    def test_alert_recorded_in_history(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="always trigger",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        e = _make_event(actor="alice")
        fresh_engine.evaluate_event(e)
        alerts = fresh_engine.list_alerts()
        assert len(alerts) == 1

    def test_acknowledge_alert(self, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="always trigger",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        e = _make_event(actor="alice")
        triggered = fresh_engine.evaluate_event(e)
        alert_id = triggered[0].alert_id
        acked = fresh_engine.acknowledge_alert(alert_id, acknowledged_by="admin")
        assert acked is not None
        assert acked.acknowledged is True
        assert acked.acknowledged_by == "admin"

    def test_acknowledge_not_found(self, fresh_engine):
        result = fresh_engine.acknowledge_alert("nope")
        assert result is None

    def test_list_alerts_filter_by_rule(self, fresh_engine):
        r1 = fresh_engine.create_rule(AuditAlertRuleCreate(
            name="rule1",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="rule2",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        e = _make_event(actor="alice")
        fresh_engine.evaluate_event(e)
        # Should have 2 alerts (one per rule)
        all_alerts = fresh_engine.list_alerts()
        assert len(all_alerts) == 2
        # Filter by r1
        r1_alerts = fresh_engine.list_alerts(rule_id=r1.rule_id)
        assert len(r1_alerts) == 1


# ── AuditAlertEngine: broadcaster ─────────────────────────────────


class TestBroadcaster:
    def test_broadcaster_called_on_trigger(self):
        received: list[dict[str, Any]] = []

        def _bcast(alert: dict[str, Any]) -> Any:
            received.append(alert)
            return None

        engine = AuditAlertEngine(broadcaster=_bcast)
        engine.create_rule(AuditAlertRuleCreate(
            name="always trigger",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        e = _make_event(actor="alice")
        engine.evaluate_event(e)
        assert len(received) == 1
        assert received[0]["metadata"]["rule_name"] == "always trigger"


# ── Statistics helpers ────────────────────────────────────────────


class TestStatisticsHelpers:
    def test_compute_stats(self):
        events = [
            _make_event(action=AuditAction.LOGIN, severity=AuditSeverity.INFO, actor="a", category="auth"),
            _make_event(action=AuditAction.LOGIN, severity=AuditSeverity.CRITICAL, actor="b", category="auth"),
            _make_event(action=AuditAction.API_CALL, severity=AuditSeverity.WARNING, actor="a", category="data"),
        ]
        stats = compute_stats(events, hours=24)
        assert stats.total_events == 3
        assert stats.by_action.get("login") == 2
        assert stats.by_action.get("api_call") == 1
        assert stats.critical_count == 1
        assert stats.by_category.get("auth") == 2
        assert len(stats.top_actors) > 0
        assert stats.top_actors[0]["key"] == "a"  # 'a' has 2 events

    def test_compute_stats_empty(self):
        stats = compute_stats([], hours=24)
        assert stats.total_events == 0

    def test_compute_timeline(self):
        now = time.time()
        events = [
            _make_event(action=AuditAction.LOGIN, timestamp=now - 100),
            _make_event(action=AuditAction.LOGIN, timestamp=now - 50),
            _make_event(action=AuditAction.LOGIN, timestamp=now),
        ]
        points = compute_timeline(events, bucket_s=60, since=now - 200, until=now + 10)
        assert len(points) > 0
        total = sum(p.count for p in points)
        assert total == 3

    def test_compute_timeline_empty(self):
        points = compute_timeline([], bucket_s=60, since=0, until=0)
        assert points == []

    def test_compute_heatmap(self):
        events = [_make_event(action=AuditAction.LOGIN, timestamp=time.time())]
        cells = compute_heatmap(events)
        assert len(cells) == 7 * 24  # 168 cells
        total = sum(c.count for c in cells)
        assert total == 1

    def test_compute_heatmap_empty(self):
        cells = compute_heatmap([])
        assert len(cells) == 168
        assert all(c.count == 0 for c in cells)


# ── filter_events ─────────────────────────────────────────────────


class TestFilterEvents:
    def test_filter_by_action(self):
        events = [
            _make_event(action=AuditAction.LOGIN, actor="a"),
            _make_event(action=AuditAction.LOGOUT, actor="b"),
            _make_event(action=AuditAction.LOGIN, actor="c"),
        ]
        page, total = filter_events(events, AuditEventQuery(actions=["login"]))
        assert total == 2
        assert all(e.action == AuditAction.LOGIN for e in page)

    def test_filter_by_severity_and_risk(self):
        events = [
            _make_event(severity=AuditSeverity.CRITICAL, risk_level=AuditRiskLevel.CRITICAL),
            _make_event(severity=AuditSeverity.INFO, risk_level=AuditRiskLevel.LOW),
        ]
        page, total = filter_events(
            events,
            AuditEventQuery(severities=["critical"], risk_levels=["critical"]),
        )
        assert total == 1
        assert page[0].severity == AuditSeverity.CRITICAL

    def test_filter_by_search(self):
        events = [
            _make_event(detail="user login from 1.2.3.4"),
            _make_event(detail="logout"),
        ]
        page, total = filter_events(events, AuditEventQuery(search="login"))
        assert total == 1
        assert "login" in page[0].detail

    def test_filter_pagination(self):
        events = [_make_event(actor=f"u{i}") for i in range(10)]
        page, total = filter_events(events, AuditEventQuery(limit=3, offset=2))
        assert total == 10
        assert len(page) == 3

    def test_filter_sort_timestamp_asc(self):
        now = time.time()
        events = [
            _make_event(timestamp=now - 10, actor="old"),
            _make_event(timestamp=now, actor="new"),
        ]
        page, _ = filter_events(events, AuditEventQuery(sort="timestamp_asc"))
        assert page[0].actor == "old"
        assert page[1].actor == "new"

    def test_filter_sort_timestamp_desc(self):
        now = time.time()
        events = [
            _make_event(timestamp=now - 10, actor="old"),
            _make_event(timestamp=now, actor="new"),
        ]
        page, _ = filter_events(events, AuditEventQuery(sort="timestamp_desc"))
        assert page[0].actor == "new"


# ── Export helpers ────────────────────────────────────────────────


class TestExportHelpers:
    def test_export_csv(self):
        events = [
            _make_event(action=AuditAction.LOGIN, actor="alice", category="auth", tags=["a", "b"]),
            _make_event(action=AuditAction.LOGOUT, actor="bob"),
        ]
        csv_str = export_events_csv(events)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 3  # header + 2 data
        assert "event_id" in rows[0]
        assert "tags" in rows[0]
        assert rows[1][2] == "login"
        assert rows[1][6] == "alice"
        assert rows[1][13] == "a|b"

    def test_export_json(self):
        events = [_make_event(actor="alice"), _make_event(actor="bob")]
        json_str = export_events_json(events)
        data = json.loads(json_str)
        assert len(data) == 2
        assert data[0]["actor"] == "alice"

    def test_export_csv_empty(self):
        csv_str = export_events_csv([])
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1  # just header

    def test_export_json_empty(self):
        json_str = export_events_json([])
        assert json.loads(json_str) == []


# ── Router endpoint tests ─────────────────────────────────────────


class TestRouterEndpoints:
    def test_legacy_events_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["count"] == 1

    def test_legacy_summary_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/summary")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_advanced_query(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice", category="auth")
        fresh_logger.log(AuditAction.API_CALL, actor="bob", category="data")
        resp = client.post("/api/audit/events/advanced", json={
            "categories": ["auth"],
            "limit": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total"] == 1
        assert data["events"][0]["category"] == "auth"

    def test_export_csv_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "event_id" in resp.text

    def test_export_json_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/export?format=json")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 1

    def test_stats_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice", category="auth")
        fresh_logger.log(AuditAction.API_CALL, actor="bob", category="data")
        resp = client.get("/api/audit/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["stats"]["total_events"] == 2

    def test_timeline_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/timeline?bucket_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["timeline"], list)

    def test_heatmap_endpoint(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.get("/api/audit/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["heatmap"]) == 168


# ── Router: alert rule CRUD ───────────────────────────────────────


class TestRouterAlertRules:
    def test_create_rule_endpoint(self, client):
        resp = client.post("/api/audit/alert/rules", json={
            "name": "test rule",
            "condition_type": "threshold",
            "condition": {"window_s": 300, "op": ">=", "value": 5},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["rule"]["name"] == "test rule"

    def test_list_rules_endpoint(self, client, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(name="existing"))
        resp = client.get("/api/audit/alert/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_get_rule_endpoint(self, client, fresh_engine):
        rule = fresh_engine.create_rule(AuditAlertRuleCreate(name="x"))
        resp = client.get(f"/api/audit/alert/rules/{rule.rule_id}")
        assert resp.status_code == 200
        assert resp.json()["rule"]["rule_id"] == rule.rule_id

    def test_get_rule_not_found(self, client):
        resp = client.get("/api/audit/alert/rules/nonexistent")
        assert resp.status_code == 404

    def test_update_rule_endpoint(self, client, fresh_engine):
        rule = fresh_engine.create_rule(AuditAlertRuleCreate(name="orig"))
        resp = client.put(f"/api/audit/alert/rules/{rule.rule_id}", json={"name": "updated"})
        assert resp.status_code == 200
        assert resp.json()["rule"]["name"] == "updated"

    def test_delete_rule_endpoint(self, client, fresh_engine):
        rule = fresh_engine.create_rule(AuditAlertRuleCreate(name="to delete"))
        resp = client.delete(f"/api/audit/alert/rules/{rule.rule_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == rule.rule_id

    def test_delete_rule_not_found(self, client):
        resp = client.delete("/api/audit/alert/rules/nonexistent")
        assert resp.status_code == 404


# ── Router: alert history & evaluate ──────────────────────────────


class TestRouterAlertHistory:
    def test_alert_history_empty(self, client):
        resp = client.get("/api/audit/alert/history")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_evaluate_no_rules(self, client, fresh_logger):
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.post("/api/audit/alert/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["count"] == 0

    def test_evaluate_with_rule_triggers(self, client, fresh_logger, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="all logins",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        fresh_logger.log(AuditAction.LOGIN, actor="alice")
        resp = client.post("/api/audit/alert/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_acknowledge_alert_endpoint(self, client, fresh_logger, fresh_engine):
        fresh_engine.create_rule(AuditAlertRuleCreate(
            name="all",
            condition_type=AlertConditionType.PATTERN,
            condition={"field": "actor", "regex": ".*"},
        ))
        e = _make_event(actor="alice")
        triggered = fresh_engine.evaluate_event(e)
        alert_id = triggered[0].alert_id
        resp = client.post(f"/api/audit/alert/{alert_id}/acknowledge")
        assert resp.status_code == 200
        assert resp.json()["alert"]["acknowledged"] is True

    def test_acknowledge_not_found(self, client):
        resp = client.post("/api/audit/alert/nope/acknowledge")
        assert resp.status_code == 404


# ── Router: 404 for personal edition ──────────────────────────────


class TestPersonalEditionGuards:
    def test_advanced_query_404_in_personal(self, monkeypatch, fresh_logger):
        from maop.config.edition import Edition, set_edition
        set_edition(Edition.PERSONAL)
        try:
            from maop.dashboard.routers import audit as audit_router
            app = FastAPI()

            @app.middleware("http")
            async def _inject_admin(request, call_next):
                request.state.auth_roles = ["admin"]
                return await call_next(request)

            app.include_router(audit_router.router)
            c = TestClient(app)
            resp = c.post("/api/audit/events/advanced", json={})
            assert resp.status_code == 404
        finally:
            from maop.config.edition import reset_edition
            reset_edition()