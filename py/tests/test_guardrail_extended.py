"""Extended tests for MAOP.core.guardrail — all rule types, edge cases, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.security.guardrail import (
    DEFAULT_RULES,
    Guardrail,
    RuleType,
    _default_config,
    fnmatch_simple,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "guardrails.json"


@pytest.fixture
def guardrail(config_path: Path) -> Guardrail:
    return Guardrail(config_path)


class TestRuleTypes:
    def test_content_rule_blocks_aws_key(self, guardrail: Guardrail):
        result = guardrail.check(content="AKIAIOSFODNN7EXAMPLE", agent="a", task="t")
        assert result.passed is False

    def test_content_rule_blocks_ec_private_key(self, guardrail: Guardrail):
        result = guardrail.check(content="-----BEGIN EC PRIVATE KEY-----", agent="a", task="t")
        assert result.passed is False

    def test_content_rule_allows_normal_text(self, guardrail: Guardrail):
        result = guardrail.check(content="def hello(): pass", agent="a", task="t")
        assert result.passed is True

    def test_input_rule_warns_on_long_content(self, guardrail: Guardrail):
        long = "x" * 6000
        result = guardrail.check(content=long, agent="a", task="t")
        assert any(v.rule == "max-task-length" for v in result.violations)
        assert result.summary == "WARN"

    def test_output_rule_truncates_large_output(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "max-output-size":
                rule.enabled = True
                rule.limit = 100
        big = "y" * 200
        result = guardrail.check(content=big, agent="a", task="t")
        assert any(v.rule == "max-output-size" for v in result.violations)

    def test_task_allowlist_blocks_unmatched(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "allowed-tasks":
                rule.enabled = True
                rule.allowlist = ["codegen*", "test*"]
        result = guardrail.check(content="ok", agent="a", task="deploy production")
        assert result.passed is False

    def test_task_allowlist_allows_matched(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "allowed-tasks":
                rule.enabled = True
                rule.allowlist = ["codegen*", "test*"]
        result = guardrail.check(content="ok", agent="a", task="codegen: write function")
        assert result.passed is True

    def test_task_allowlist_wildcard_allows_all(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "allowed-tasks":
                rule.enabled = True
                rule.allowlist = ["*"]
        result = guardrail.check(content="ok", agent="a", task="anything goes")
        assert result.passed is True


class TestAgentBlocklist:
    def test_blocked_agent_is_rejected(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "blocked-agents":
                rule.blocklist = ["malware-gen"]
                rule.enabled = True
        result = guardrail.check(content="ok", agent="malware-gen", task="t")
        assert result.passed is False

    def test_non_blocked_agent_passes(self, guardrail: Guardrail):
        for rule in guardrail._config.rules:
            if rule.id == "blocked-agents":
                rule.blocklist = ["malware-gen"]
                rule.enabled = True
        result = guardrail.check(content="ok", agent="claude", task="t")
        assert result.passed is True


class TestCheckResultSummary:
    def test_pass_summary(self, guardrail: Guardrail):
        result = guardrail.check(content="safe", agent="claude", task="codegen")
        assert result.summary == "PASS"

    def test_warn_summary(self, guardrail: Guardrail):
        long = "z" * 6000
        result = guardrail.check(content=long, agent="claude", task="t")
        assert result.summary == "WARN"

    def test_blocked_summary(self, guardrail: Guardrail):
        result = guardrail.check(content="sk-abc1234567890123456789012", agent="a", task="t")
        assert result.summary == "BLOCKED"


class TestFnmatchSimple:
    def test_exact_match(self):
        assert fnmatch_simple("hello", "hello") is True

    def test_exact_no_match(self):
        assert fnmatch_simple("hello", "world") is False

    def test_wildcard_star(self):
        assert fnmatch_simple("anything", "*") is True

    def test_prefix_wildcard(self):
        assert fnmatch_simple("codegen-foo", "codegen*") is True

    def test_suffix_wildcard(self):
        assert fnmatch_simple("foo-test", "*-test") is True

    def test_middle_wildcard(self):
        assert fnmatch_simple("pre-mid-post", "pre*post") is True

    def test_no_match_middle_wildcard(self):
        assert fnmatch_simple("pre-mid-xxx", "pre*post") is False


class TestGuardrailPersistence:
    def test_save_and_reload(self, config_path: Path):
        g1 = Guardrail(config_path)
        for rule in g1._config.rules:
            if rule.id == "blocked-agents":
                rule.blocklist = ["test-agent"]
        g1._save()

        g2 = Guardrail(config_path)
        found = False
        for rule in g2._config.rules:
            if rule.id == "blocked-agents":
                assert "test-agent" in rule.blocklist
                found = True
        assert found

    def test_reset_restores_defaults(self, config_path: Path):
        g = Guardrail(config_path)
        for rule in g._config.rules:
            if rule.id == "blocked-agents":
                rule.blocklist = ["should-be-gone"]
        g._save()

        g.reset()
        for rule in g._config.rules:
            if rule.id == "blocked-agents":
                assert "should-be-gone" not in rule.blocklist

    def test_corrupted_config_falls_back_to_defaults(self, tmp_path: Path):
        bad_path = tmp_path / "guardrails.json"
        bad_path.write_text("NOT VALID JSON {{{")
        g = Guardrail(bad_path)
        assert len(g._config.rules) > 0


class TestDefaultConfig:
    def test_default_rules_count(self):
        cfg = _default_config()
        assert len(cfg.rules) == len(DEFAULT_RULES)

    def test_all_rule_types_present(self):
        cfg = _default_config()
        types = {r.type for r in cfg.rules}
        assert RuleType.CONTENT in types
        assert RuleType.INPUT in types
        assert RuleType.AGENT in types
        assert RuleType.TASK in types
        assert RuleType.RATE in types
        assert RuleType.OUTPUT in types
