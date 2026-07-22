"""Tests for MAOP.core.guardrail."""

from pathlib import Path

import pytest
from maop.core.guardrail import Guardrail


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "guardrails.json"


@pytest.fixture
def guardrail(config_path: Path) -> Guardrail:
    return Guardrail(config_path)


class TestGuardrailCheck:
    def test_pass_clean_content(self, guardrail: Guardrail):
        result = guardrail.check(content="hello world", agent="claude", task="codegen")
        assert result.passed is True
        assert result.summary == "PASS"

    def test_block_sensitive_api_key(self, guardrail: Guardrail):
        result = guardrail.check(content="key = sk-abc1234567890123456789012", agent="claude", task="codegen")
        assert result.passed is False
        assert result.summary == "BLOCKED"

    def test_block_private_key(self, guardrail: Guardrail):
        result = guardrail.check(content="-----BEGIN RSA PRIVATE KEY-----", agent="a", task="t")
        assert result.passed is False

    def test_warn_long_input(self, guardrail: Guardrail):
        long_content = "x" * 6000
        result = guardrail.check(content=long_content, agent="claude", task="t")
        # Should have a warn violation but not blocked
        assert any(v.rule == "max-task-length" for v in result.violations)

    def test_block_agent_in_blocklist(self, guardrail: Guardrail):
        # First, add an agent to the blocklist
        for rule in guardrail._config.rules:
            if rule.id == "blocked-agents":
                rule.blocklist = ["evil-agent"]
                rule.enabled = True
        result = guardrail.check(content="ok", agent="evil-agent", task="t")
        assert result.passed is False

    def test_allow_method(self, guardrail: Guardrail):
        assert guardrail.allow(content="safe", agent="claude", task="codegen") is True

    def test_block_method(self, guardrail: Guardrail):
        result = guardrail.block("bad-agent", "bad-task")
        assert result["action"] == "blocked"


class TestGuardrailConfig:
    def test_report(self, guardrail: Guardrail):
        report = guardrail.report()
        assert report["total"] > 0
        assert "rules" in report

    def test_get_config_single_rule(self, guardrail: Guardrail):
        rule = guardrail.get_config(rule_id="sensitive-patterns")
        assert rule["id"] == "sensitive-patterns"

    def test_reset(self, guardrail: Guardrail, config_path: Path):
        guardrail.reset()
        assert config_path.exists()
        # After reset, sensitive-patterns should be back
        rule = guardrail.get_config(rule_id="sensitive-patterns")
        assert rule["enabled"] is True
