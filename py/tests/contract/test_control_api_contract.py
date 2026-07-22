"""Contract tests for Control Plane + Audit API endpoints."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

EXPECTED_AUDIT_ENDPOINTS = [
    "/api/audit/events",
    "/api/audit/filter",
]


class TestAuditAPIContracts:
    """Verify audit API endpoints exist and return correct schemas."""


    def test_all_audit_endpoints_exist(self, server_routes):
        for ep in EXPECTED_AUDIT_ENDPOINTS:
            assert ep in server_routes, f"Missing endpoint: {ep}"


class TestControlPlaneContracts:
    """Verify ControlPlane action interface contract."""

    def test_all_builtin_actions_registered(self, tmp_path):
        from maop.control.plane import ControlPlane
        plane = ControlPlane(root_dir=str(tmp_path))
        expected_actions = {
            "model.switch", "control.run", "control.pause",
            "control.resume", "control.stop", "config.reload",
            "cache.clear", "memory.prune",
        }
        assert expected_actions.issubset(set(plane._handlers.keys()))

    def test_action_result_schema(self):
        from maop.control.plane import ActionResult, ActionStatus
        r = ActionResult(status=ActionStatus.SUCCESS, action="test")
        d = r.model_dump()
        assert "status" in d
        assert "action" in d
        assert "detail" in d
        assert "error" in d

    def test_audit_event_schema(self):
        from maop.control.audit import AuditEvent, AuditLevel
        e = AuditEvent(action="test", level=AuditLevel.INFO)
        d = e.model_dump()
        assert "event_id" in d
        assert "timestamp" in d
        assert "level" in d
        assert "actor" in d
        assert "action" in d
        assert "target" in d
        assert "detail" in d
        assert "trace_id" in d
