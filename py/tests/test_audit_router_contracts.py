"""HTTP contracts for ``/api/audit/*`` — one wire vocabulary for both editions.

``observability_service`` forks every read on ``has_feature(AUDIT_LOG)``, and
the two producers spelled the same facts differently: personal
``AuditEvent`` used ``level``/``target`` and summary ``total`` where the
enterprise row used ``severity``/``resource`` and ``total_events``. ``Audit.vue``
covered the split with ``e.level || e.severity`` chains, and its vitest mocks
sent the *view-model* names — so both sides agreed while neither matched the
producer.

The personal branch now emits the enterprise spelling (with ``None`` for what
the personal log cannot record), and the envelopes below are checked against
the real enterprise models in a ``importorskip`` test, because CI runs the
personal edition and would otherwise never see those rows.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.control.audit import AuditEvent, AuditLevel, AuditLog
from maop.dashboard.routers import audit as audit_router
from maop.dashboard.routers import state
from maop.dashboard.routers.audit_schemas import (
    AlertRuleOut,
    AuditEventOut,
    AuditSummaryOut,
)
from maop.dashboard.services import observability_service

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/audit/events"),
    ("GET", "/api/audit/summary"),
    ("POST", "/api/audit/alert/rules"),
    ("GET", "/api/audit/alert/rules"),
    ("GET", "/api/audit/alert/rules/{rule_id}"),
    ("PUT", "/api/audit/alert/rules/{rule_id}"),
    ("DELETE", "/api/audit/alert/rules/{rule_id}"),
    ("GET", "/api/audit/alert/history"),
]

UNIFIED_EVENT_KEYS = set(AuditEventOut.model_fields)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(state, "MAOP_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(audit_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(audit_router.router)
    return TestClient(app)


def _log(root: Path, **kw: Any) -> AuditEvent:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return AuditLog(log_dir / "audit.jsonl").log(**kw)


def _record(root: Path, event: AuditEvent) -> AuditEvent:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    AuditLog(log_dir / "audit.jsonl").record(event)
    return event


# ── events: one vocabulary ─────────────────────────────────────────


def test_personal_events_use_the_unified_vocabulary(client: TestClient, root: Path) -> None:
    """Keys, not values: a field the producer omits must not be invented."""
    _log(root, action="model.switch", actor="agent-1", target="gpt-4",
         level=AuditLevel.WARN, trace_id="trace-7")
    body = client.get("/api/audit/events").json()
    row = body["events"][0]
    assert set(row) == UNIFIED_EVENT_KEYS
    assert row["action"] == "model.switch"
    assert row["actor"] == "agent-1"
    assert row["resource"] == "gpt-4", "personal `target` must surface as `resource`"
    assert row["severity"] == "warning", "personal `warn` must surface as `warning`"
    assert row["trace_id"], "personal-only field stays visible"
    # attributes the personal log does not record stay None, not ""
    assert row["tenant_id"] is None
    assert row["risk_level"] is None
    assert row["tags"] is None
    assert "level" not in row and "target" not in row


def test_error_value_is_untouched_by_the_envelope(client: TestClient, monkeypatch) -> None:
    """``audit.py`` passes no ``error_value`` — errors are ErrorSchema JSON."""

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("audit log unreadable")

    monkeypatch.setattr(observability_service, "list_audit_events", _boom)
    resp = client.get("/api/audit/events")
    assert resp.status_code == 500
    assert resp.json()["status"] == "error"


# ── filters that used to be ignored ────────────────────────────────


def test_severity_filter_actually_filters(client: TestClient, root: Path) -> None:
    _log(root, action="a_info", level=AuditLevel.INFO)
    _log(root, action="a_crit", level=AuditLevel.CRITICAL)
    body = client.get("/api/audit/events", params={"severity": "critical"}).json()
    assert [e["action"] for e in body["events"]] == ["a_crit"]
    assert body["count"] == 1


def test_offset_advances_the_personal_page(client: TestClient, root: Path) -> None:
    for i in range(3):
        _log(root, action=f"act-{i}")
    first = client.get("/api/audit/events", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/audit/events", params={"limit": 2, "offset": 2}).json()
    assert len(first["events"]) == 2
    assert len(second["events"]) == 1
    assert not {e["event_id"] for e in first["events"]} & {e["event_id"] for e in second["events"]}


def test_hours_window_excludes_old_events(client: TestClient, root: Path) -> None:
    _log(root, action="recent")
    _record(root, AuditEvent(action="ancient", timestamp=time.time() - 48 * 3600))
    body = client.get("/api/audit/events", params={"hours": 24}).json()
    assert [e["action"] for e in body["events"]] == ["recent"]


# ── summary ────────────────────────────────────────────────────────


def test_personal_summary_uses_total_events(client: TestClient, root: Path) -> None:
    _log(root, action="login", actor="alice")
    _log(root, action="delete", actor="bob", level=AuditLevel.CRITICAL)
    body = client.get("/api/audit/summary").json()
    summary = body["summary"]
    assert set(summary) == set(AuditSummaryOut.model_fields)
    assert summary["total_events"] == 2
    assert summary["by_action"] == {"login": 1, "delete": 1}
    assert summary["by_actor"] == {"alice": 1, "bob": 1}
    assert summary["critical_count"] == 1
    # not computed by the personal edition, and declared as such
    assert summary["by_risk_level"] is None
    assert summary["by_category"] is None


def test_summary_hours_reaches_the_personal_aggregation(client: TestClient, root: Path) -> None:
    _record(root, AuditEvent(action="old", timestamp=time.time() - 72 * 3600))
    assert client.get("/api/audit/summary", params={"hours": 24}).json()["summary"]["total_events"] == 0
    assert client.get("/api/audit/summary", params={"hours": 96}).json()["summary"]["total_events"] == 1


# ── enterprise-only endpoints in the personal edition ──────────────


def test_alert_rules_are_404_without_the_enterprise_overlay(client: TestClient) -> None:
    """The feature guard raises before serialization, so the envelope never runs."""
    resp = client.get("/api/audit/alert/rules")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


# ── envelopes vs the real enterprise models ────────────────────────
#
# Skipped per test (not per module): CI runs the personal edition and the
# tests above must still run there.


def test_event_envelope_accepts_a_real_enterprise_event() -> None:
    """Personal-shaped rows are covered above; this is the other half of the fork."""
    maos_audit = pytest.importorskip("maop.enterprise.audit", reason="overlay is a separate repo")
    row = maos_audit.AuditEvent(action="login", severity="critical", tenant_id="t1").model_dump()
    parsed = AuditEventOut.model_validate(row)
    assert set(parsed.model_dump()) == UNIFIED_EVENT_KEYS
    assert parsed.severity == "critical"
    assert parsed.trace_id is None  # enterprise has no trace_id field


def test_rule_envelope_matches_enterprise_rule_dump() -> None:
    maos_enh = pytest.importorskip("maop.enterprise.audit_enhanced", reason="overlay is a separate repo")
    rule = maos_enh.AuditAlertRule(rule_id="r1", name="n", condition={"op": ">=", "value": 5})
    assert set(AlertRuleOut.model_fields) == set(rule.model_dump())


def test_alert_history_envelope_matches_enterprise_alert_dump() -> None:
    maos_enh = pytest.importorskip("maop.enterprise.audit_enhanced", reason="overlay is a separate repo")
    from maop.dashboard.routers.audit_schemas import AuditAlertOut

    alert = maos_enh.AuditAlert(alert_id="a1", rule_id="r1", triggered_at=1.0)
    parsed = AuditAlertOut.model_validate(alert.model_dump())
    assert parsed.metadata == {}
    assert parsed.acknowledged_at is None


# ── wiring ─────────────────────────────────────────────────────────


def test_all_contract_routes_declare_a_response_model(client: TestClient) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []
