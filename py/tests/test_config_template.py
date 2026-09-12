"""ConfigTemplate 模块单元测试 — 覆盖模板查询、应用、匹配.

测试策略：
  - **纯逻辑测试**：ConfigTemplateManager 无外部依赖，直接测试。
  - **幂等性验证**：多次 apply_template 不修改模板内部状态。
  - **匹配规则覆盖**：match_template 各类关键字逐一验证。

共 16 个测试用例。
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from maop.core.agent.discovery.config_template import (
    ConfigTemplate,
    ConfigTemplateManager,
)


# ── ConfigTemplate 模型测试 ────────────────────────────────────────

class TestConfigTemplateModel:
    """ConfigTemplate Pydantic 模型测试。"""

    def test_config_template_creation(self) -> None:
        """测试 ConfigTemplate 基本创建。"""
        tpl = ConfigTemplate(
            name="test_template",
            description="测试模板",
            adapter_type="cli",
            default_config={"command": ""},
            required_fields=["command"],
            optional_fields=["timeout"],
        )
        assert tpl.name == "test_template"
        assert tpl.description == "测试模板"
        assert tpl.adapter_type == "cli"
        assert tpl.default_config == {"command": ""}
        assert tpl.required_fields == ["command"]
        assert tpl.optional_fields == ["timeout"]

    def test_config_template_defaults(self) -> None:
        """测试 ConfigTemplate 默认值。"""
        tpl = ConfigTemplate(name="minimal", adapter_type="http")
        assert tpl.description == ""
        assert tpl.default_config == {}
        assert tpl.required_fields == []
        assert tpl.optional_fields == []

    def test_config_template_requires_name(self) -> None:
        """测试 name 为空时抛出校验错误。"""
        with pytest.raises(Exception):
            ConfigTemplate(name="", adapter_type="cli")


# ── ConfigTemplateManager 初始化测试 ───────────────────────────────

class TestTemplateManagerInit:
    """ConfigTemplateManager 初始化测试。"""

    def test_init_creates_lock(self) -> None:
        """测试初始化创建 RLock。"""
        mgr = ConfigTemplateManager()
        assert hasattr(mgr, "_lock")
        assert isinstance(mgr._lock, type(threading.RLock()))

    def test_init_loads_templates(self) -> None:
        """测试初始化加载预定义模板。"""
        mgr = ConfigTemplateManager()
        assert hasattr(mgr, "_templates")
        assert len(mgr._templates) >= 8


# ── list_templates / get_template 测试 ─────────────────────────────

class TestListAndGetTemplates:
    """模板查询测试。"""

    def test_list_templates_returns_eight(self) -> None:
        """测试 list_templates 返回至少 8 个模板。"""
        mgr = ConfigTemplateManager()
        templates = mgr.list_templates()
        assert len(templates) >= 8
        # 验证按名排序
        names = [t.name for t in templates]
        assert names == sorted(names)

    def test_list_templates_contains_expected_names(self) -> None:
        """测试 list_templates 包含所有预期模板名。"""
        mgr = ConfigTemplateManager()
        templates = mgr.list_templates()
        names = {t.name for t in templates}
        expected = {
            "cli_basic",
            "cli_with_auth",
            "desktop_app",
            "http_api",
            "vscode_extension",
            "jetbrains_plugin",
            "mcp_server",
            "vibe_platform",
        }
        assert expected.issubset(names)

    def test_get_template_exists(self) -> None:
        """测试 get_template 获取存在的模板。"""
        mgr = ConfigTemplateManager()
        tpl = mgr.get_template("cli_basic")
        assert tpl is not None
        assert tpl.name == "cli_basic"
        assert tpl.adapter_type == "cli"

    def test_get_template_not_exists(self) -> None:
        """测试 get_template 获取不存在的模板返回 None。"""
        mgr = ConfigTemplateManager()
        tpl = mgr.get_template("nonexistent_template")
        assert tpl is None


# ── apply_template 测试 ────────────────────────────────────────────

class TestApplyTemplate:
    """apply_template 模板应用测试。"""

    def test_apply_template_basic(self) -> None:
        """测试应用 cli_basic 模板。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template("cli_basic", {"command": "aider"})
        assert config["command"] == "aider"
        # 默认值应保留
        assert config["timeout_s"] == 30.0
        assert config["network_enabled"] is False
        assert config["args"] == []

    def test_apply_template_with_overrides(self) -> None:
        """测试 overrides 覆盖默认值。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template(
            "cli_basic",
            {"command": "aider", "timeout_s": 120.0, "network_enabled": True},
        )
        assert config["command"] == "aider"
        assert config["timeout_s"] == 120.0
        assert config["network_enabled"] is True

    def test_apply_template_missing_required(self) -> None:
        """测试缺少必填字段抛出 KeyError。"""
        mgr = ConfigTemplateManager()
        # cli_basic 需要 command，不提供应报错
        with pytest.raises(KeyError, match="command"):
            mgr.apply_template("cli_basic", {})

    def test_apply_template_not_exists(self) -> None:
        """测试应用不存在的模板抛出 ValueError。"""
        mgr = ConfigTemplateManager()
        with pytest.raises(ValueError, match="不存在"):
            mgr.apply_template("nonexistent_template")

    def test_apply_template_http_api(self) -> None:
        """测试应用 http_api 模板。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template(
            "http_api",
            {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "model": "gpt-4",
            },
        )
        assert config["base_url"] == "https://api.openai.com/v1"
        assert config["api_key"] == "sk-test"
        assert config["model"] == "gpt-4"
        assert config["streaming"] is True
        assert config["max_retries"] == 3

    def test_apply_template_deep_copy(self) -> None:
        """测试 apply_template 不修改模板内部状态（深拷贝）。"""
        mgr = ConfigTemplateManager()
        # 第一次应用
        config1 = mgr.apply_template("cli_basic", {"command": "aider"})
        config1["timeout_s"] = 999.0
        config1["args"].append("--verbose")
        # 第二次应用，应不受第一次修改影响
        config2 = mgr.apply_template("cli_basic", {"command": "aider"})
        assert config2["timeout_s"] == 30.0
        assert config2["args"] == []

    def test_apply_template_desktop_app(self) -> None:
        """测试应用 desktop_app 模板。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template(
            "desktop_app",
            {"app_name": "cursor", "cli_command": "cursor"},
        )
        assert config["app_name"] == "cursor"
        assert config["cli_command"] == "cursor"
        assert config["fallback_order"] == ["cli", "ipc", "http"]

    def test_apply_template_mcp_server(self) -> None:
        """测试应用 mcp_server 模板。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template(
            "mcp_server",
            {"transport": "stdio", "command": "python -m my_mcp"},
        )
        assert config["transport"] == "stdio"
        assert config["command"] == "python -m my_mcp"
        assert config["auto_reconnect"] is True

    def test_apply_template_vibe_platform(self) -> None:
        """测试应用 vibe_platform 模板。"""
        mgr = ConfigTemplateManager()
        config = mgr.apply_template(
            "vibe_platform",
            {"platform_url": "https://bolt.new"},
        )
        assert config["platform_url"] == "https://bolt.new"
        assert config["browser"] == "chromium"
        assert config["headless"] is True
        assert config["viewport"] == {"width": 1280, "height": 720}

    def test_apply_template_no_overrides(self) -> None:
        """测试不提供 overrides 时使用纯默认值。"""
        mgr = ConfigTemplateManager()
        # mcp_server 只需要 transport，但默认配置中 transport="stdio"
        config = mgr.apply_template("mcp_server")
        assert config["transport"] == "stdio"


# ── match_template 测试 ────────────────────────────────────────────

class TestMatchTemplate:
    """match_template 模板匹配测试。"""

    def test_match_template_desktop(self) -> None:
        """测试桌面应用匹配 desktop_app 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("cursor", "trae", "qoder", "zed", "pearai"):
            result = mgr.match_template(name)
            assert result == "desktop_app", f"{name} 应匹配 desktop_app"

    def test_match_template_cli_with_auth(self) -> None:
        """测试带认证 CLI 匹配 cli_with_auth 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("claude-code", "codex", "gemini-cli"):
            result = mgr.match_template(name)
            assert result == "cli_with_auth", f"{name} 应匹配 cli_with_auth"

    def test_match_template_cli_basic(self) -> None:
        """测试无认证 CLI 匹配 cli_basic 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("aider", "opencode", "inscode", "codearts"):
            result = mgr.match_template(name)
            assert result == "cli_basic", f"{name} 应匹配 cli_basic"

    def test_match_template_vscode(self) -> None:
        """测试 VS Code 扩展匹配 vscode_extension 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("github.copilot", "continue", "tabnine", "cline"):
            result = mgr.match_template(name)
            assert result == "vscode_extension", f"{name} 应匹配 vscode_extension"

    def test_match_template_http_api(self) -> None:
        """测试 HTTP API 匹配 http_api 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("deepseek", "kimi", "openai", "ollama"):
            result = mgr.match_template(name)
            assert result == "http_api", f"{name} 应匹配 http_api"

    def test_match_template_vibe_platform(self) -> None:
        """测试 Vibe Coding 平台匹配 vibe_platform 模板。"""
        mgr = ConfigTemplateManager()
        for name in ("bolt-new", "v0", "lovable", "replit-agent"):
            result = mgr.match_template(name)
            assert result == "vibe_platform", f"{name} 应匹配 vibe_platform"

    def test_match_template_fallback(self) -> None:
        """测试未知 Agent 兜底匹配 cli_basic 模板。"""
        mgr = ConfigTemplateManager()
        result = mgr.match_template("unknown-agent-xyz")
        assert result == "cli_basic"

    def test_match_template_case_insensitive(self) -> None:
        """测试匹配大小写不敏感。"""
        mgr = ConfigTemplateManager()
        assert mgr.match_template("CURSOR") == "desktop_app"
        assert mgr.match_template("Claude-Code") == "cli_with_auth"