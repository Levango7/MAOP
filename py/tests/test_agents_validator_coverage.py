"""Coverage tests for maop.config.agents_validator — Agent 引用校验器.

该模块在基线测试中覆盖率为 0%。本文件补充核心功能的单元测试。
"""

from __future__ import annotations

import pytest

from maop.config.agents_validator import (
    ValidationError,
    validate_routing,
    validate_routing_or_raise,
)


class TestValidateRouting:
    """测试 validate_routing 函数。"""

    def test_valid_routing_no_errors(self):
        """所有引用的 agent 都已定义时返回空列表。"""
        agents_data = {"agents": {"codex": {}, "claude": {}}}
        routing_data = {"chat": {"primary": "codex", "fallback": "claude"}}
        errors = validate_routing(agents_data, routing_data)
        assert errors == []

    def test_missing_primary_agent(self):
        """primary 引用未定义的 agent 时报错。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "claude"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1
        assert "claude" in errors[0]
        assert "chat.primary" in errors[0]

    def test_missing_fallback_agent(self):
        """fallback 引用未定义的 agent 时报错。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "codex", "fallback": "missing"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1
        assert "missing" in errors[0]
        assert "chat.fallback" in errors[0]

    def test_missing_tertiary_agent(self):
        """tertiary 引用未定义的 agent 时报错。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "codex", "tertiary": "missing"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1
        assert "missing" in errors[0]
        assert "chat.tertiary" in errors[0]

    def test_empty_refs_skipped(self):
        """空字符串引用被跳过（合法）。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "codex", "fallback": "", "tertiary": ""}}
        errors = validate_routing(agents_data, routing_data)
        assert errors == []

    def test_multiple_errors(self):
        """多个未定义引用都报错。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {
            "chat": {"primary": "missing1", "fallback": "missing2"},
            "code": {"primary": "missing3"},
        }
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 3

    def test_empty_agents_section(self):
        """agents 段为空时，任何引用都报错。"""
        agents_data = {"agents": {}}
        routing_data = {"chat": {"primary": "any"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1
        assert "(空)" in errors[0]

    def test_no_agents_section(self):
        """没有 agents 段时，任何引用都报错。"""
        agents_data = {}
        routing_data = {"chat": {"primary": "any"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1

    def test_no_routing_section(self):
        """没有 routing 段时返回空列表。"""
        agents_data = {"agents": {"codex": {}}}
        errors = validate_routing(agents_data, {})
        assert errors == []

    def test_non_dict_routing_entry_skipped(self):
        """非 dict 的 routing 条目被跳过。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": "not a dict"}
        errors = validate_routing(agents_data, routing_data)
        assert errors == []

    def test_error_message_contains_defined_agents(self):
        """错误信息包含已定义的 agent 名。"""
        agents_data = {"agents": {"codex": {}, "kimi": {}}}
        routing_data = {"chat": {"primary": "missing"}}
        errors = validate_routing(agents_data, routing_data)
        assert len(errors) == 1
        # 已定义 agent 名应出现在错误信息中
        assert "codex" in errors[0] or "kimi" in errors[0]


class TestValidateRoutingOrRaise:
    """测试 validate_routing_or_raise 函数。"""

    def test_valid_no_raise(self):
        """校验通过时不抛异常。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "codex"}}
        # 不应抛异常
        validate_routing_or_raise(agents_data, routing_data)

    def test_invalid_raises_validation_error(self):
        """校验失败时抛出 ValidationError。"""
        agents_data = {"agents": {"codex": {}}}
        routing_data = {"chat": {"primary": "missing"}}
        with pytest.raises(ValidationError) as exc_info:
            validate_routing_or_raise(agents_data, routing_data)
        assert len(exc_info.value.errors) == 1
        assert "missing" in exc_info.value.errors[0]

    def test_multiple_errors_in_exception(self):
        """多个错误都包含在异常中。"""
        agents_data = {"agents": {}}
        routing_data = {
            "chat": {"primary": "missing1"},
            "code": {"primary": "missing2"},
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_routing_or_raise(agents_data, routing_data)
        assert len(exc_info.value.errors) == 2


class TestValidationError:
    """测试 ValidationError 异常类。"""

    def test_error_message_joined(self):
        """多个错误用分号连接。"""
        err = ValidationError(["error1", "error2"])
        assert "error1" in str(err)
        assert "error2" in str(err)
        assert ";" in str(err)

    def test_error_attributes(self):
        """errors 属性保存错误列表。"""
        errors = ["e1", "e2"]
        err = ValidationError(errors)
        assert err.errors == errors

    def test_single_error(self):
        """单个错误。"""
        err = ValidationError(["single error"])
        assert err.errors == ["single error"]
        assert "single error" in str(err)