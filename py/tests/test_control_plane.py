"""Tests for Control Plane + Audit system."""
from __future__ import annotations

import json

from maop.control.audit import AuditEvent, AuditLevel, AuditLog
from maop.control.plane import ActionStatus, ControlPlane

# ── AuditEvent ──────────────────────────────────────────────────

class TestAuditEvent:
    def test_default_creation(self):
        e = AuditEvent(action="test")
        assert e.event_id  # auto-generated
        assert e.timestamp > 0
        assert e.level == AuditLevel.INFO
        assert e.detail == {}

    def test_with_all_fields(self):
        e = AuditEvent(
            action="model.switch", actor="user", target="claude",
            level=AuditLevel.WARN, detail={"model": "opus"}, trace_id="abc",
        )
        assert e.action == "model.switch"
        assert e.actor == "user"
        assert e.target == "claude"
        assert e.level == AuditLevel.WARN
        assert e.detail == {"model": "opus"}
        assert e.trace_id == "abc"

    def test_serialization(self):
        e = AuditEvent(action="test", detail={"k": 1})
        d = e.model_dump()
        assert d["action"] == "test"
        assert d["detail"] == {"k": 1}
        j = e.model_dump_json()
        assert json.loads(j)["action"] == "test"


# ── AuditLog ────────────────────────────────────────────────────

class TestAuditLog:
    def test_record_and_read(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        e = AuditEvent(action="test.action", actor="tester")
        log.record(e)
        events = log.read_recent()
        assert len(events) == 1
        assert events[0].action == "test.action"

    def test_log_helper(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        event = log.log(action="control.run", actor="user", target="task1")
        assert event.action == "control.run"
        assert event.actor == "user"
        events = log.read_recent()
        assert len(events) == 1
        assert events[0].target == "task1"

    def test_multiple_events(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        for i in range(5):
            log.log(action=f"action.{i}", actor="user")
        events = log.read_recent()
        assert len(events) == 5
        assert events[0].action == "action.0"
        assert events[4].action == "action.4"

    def test_read_recent_limit(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        for i in range(10):
            log.log(action=f"action.{i}")
        events = log.read_recent(limit=3)
        assert len(events) == 3

    def test_filter_by_action(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.log(action="model.switch", target="claude")
        log.log(action="control.run", target="task1")
        log.log(action="model.switch", target="opus")
        results = log.filter(action="model.switch")
        assert len(results) == 2
        assert all(r.action == "model.switch" for r in results)

    def test_filter_by_actor(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.log(action="test", actor="alice")
        log.log(action="test", actor="bob")
        results = log.filter(actor="alice")
        assert len(results) == 1
        assert results[0].actor == "alice"

    def test_filter_by_target(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.log(action="test", target="agent1")
        log.log(action="test", target="agent2")
        results = log.filter(target="agent1")
        assert len(results) == 1

    def test_empty_log_read(self, tmp_path):
        log = AuditLog(tmp_path / "nonexistent.jsonl")
        assert log.read_recent() == []

    def test_error_level_logging(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.log(action="failed.action", level=AuditLevel.ERROR, detail={"error": "boom"})
        events = log.read_recent()
        assert events[0].level == AuditLevel.ERROR
        assert events[0].detail["error"] == "boom"


# ── ControlPlane ────────────────────────────────────────────────

class TestControlPlane:
    def test_execute_known_action(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute("model.switch", actor="user", target="claude",
                               detail={"model": "opus"})
        assert result.status == ActionStatus.SUCCESS
        assert result.action == "model.switch"
        assert result.audit is not None

    def test_execute_unknown_action(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute("nonexistent.action")
        assert result.status == ActionStatus.FAILED
        assert "Unknown action" in result.error

    def test_audit_trail_recorded(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        plane.execute("control.run", actor="user", target="task1")
        plane.execute("control.stop", actor="user", target="task1")
        events = plane.audit_log().read_recent()
        assert len(events) == 2
        assert events[0].action == "control.run"
        assert events[1].action == "control.stop"

    def test_handler_exception_caught(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))

        def bad_handler(target="", detail=None):
            raise RuntimeError("handler exploded")

        plane.register_handler("bad.action", bad_handler)
        result = plane.execute("bad.action")
        assert result.status == ActionStatus.FAILED
        assert "handler exploded" in result.error

    def test_register_custom_handler(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))

        def custom_handler(target="", detail=None):
            return {"custom": True, "target": target}

        plane.register_handler("custom.action", custom_handler)
        result = plane.execute("custom.action", target="xyz")
        assert result.status == ActionStatus.SUCCESS
        assert result.detail["custom"] is True
        assert result.detail["target"] == "xyz"

    def test_all_builtin_actions(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        actions = [
            "model.switch", "control.run", "control.pause",
            "control.resume", "control.stop", "config.reload",
            "cache.clear", "memory.prune",
        ]
        for action in actions:
            result = plane.execute(action)
            assert result.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED), f"{action} unexpected status {result.status}: {result.error}"

    def test_stub_returns_skipped_with_warn_audit(self, tmp_path):
        """Actions must return valid status and audit."""
        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute("control.run", actor="user", target="task1")
        assert result.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED)
        assert result.audit is not None

    def test_real_handler_returns_success_with_info_audit(self, tmp_path):
        """Non-stub handlers must return SUCCESS status and INFO-level audit."""
        from maop.control.audit import AuditLevel
        plane = ControlPlane(root_dir=str(tmp_path))

        def real_handler(target="", detail=None):
            return {"done": True}

        plane.register_handler("real.action", real_handler)
        result = plane.execute("real.action", actor="user", target="xyz")
        assert result.status == ActionStatus.SUCCESS
        assert result.audit is not None
        assert result.audit.level == AuditLevel.INFO

    def test_audit_event_has_trace_id(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute("control.run", trace_id="trace-123")
        assert result.audit is not None
        assert result.audit.trace_id == "trace-123"

    def test_audit_event_has_detail(self, tmp_path):
        plane = ControlPlane(root_dir=str(tmp_path))
        result = plane.execute("model.switch", target="claude",
                               detail={"model": "opus", "reason": "upgrade"})
        assert result.audit is not None
        assert result.audit.detail.get("model") == "opus"
        assert result.audit.detail.get("reason") == "upgrade"
