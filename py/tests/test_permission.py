"""Tests for MAOP.core.permission — PermissionManager."""

from __future__ import annotations

from maop.core.security.permission import PermissionManager


class TestPermissionManager:
    def test_add_and_check_allow(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="claude", action="codegen", decision="allow")
        check = pm.check(agent="claude", action="codegen")
        assert check.allowed is True
        assert check.decision == "allow"

    def test_check_deny(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="claude", action="dangerous", decision="deny")
        check = pm.check(agent="claude", action="dangerous")
        assert check.allowed is False
        assert check.decision == "deny"

    def test_check_ask_default(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        check = pm.check(agent="unknown", action="anything")
        assert check.decision == "ask"
        assert check.allowed is False

    def test_wildcard_agent(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="*", action="read", decision="allow")
        check = pm.check(agent="claude", action="read")
        assert check.allowed is True

    def test_priority_ordering(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="claude", action="*", decision="allow", priority=0)
        pm.add_rule(agent="claude", action="dangerous", decision="deny", priority=10)
        check = pm.check(agent="claude", action="dangerous")
        assert check.decision == "deny"

    def test_remove_rule(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        rid = pm.add_rule(agent="test", action="test", decision="allow")
        assert pm.remove_rule(rid) is True
        check = pm.check(agent="test", action="test")
        assert check.decision == "ask"

    def test_list_rules(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="a", action="x", decision="allow")
        pm.add_rule(agent="b", action="y", decision="deny")
        rules = pm.list_rules()
        assert len(rules) == 2

    def test_get_rule(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        rid = pm.add_rule(agent="z", action="w", decision="ask")
        rule = pm.get_rule(rid)
        assert rule is not None
        assert rule.agent == "z"
        assert rule.decision == "ask"

    def test_fnmatch_pattern(self, tmp_path):
        pm = PermissionManager(root_dir=str(tmp_path))
        pm.add_rule(agent="mavis/*", action="codegen", decision="allow")
        check = pm.check(agent="mavis/verifier", action="codegen")
        assert check.decision == "allow"
