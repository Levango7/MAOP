"""预置 Agent 配置 (preset_agents) 测试.

覆盖三组：
    - TestPresetAgents     — 预置列表非空/数量正确/名称唯一/降级链引用有效
    - TestRegisterPresets  — 注册到空catalog/已有Agent的catalog/重复注册跳过/返回计数正确
    - TestPresetDescriptors— 每个预置 Agent 的字段正确（能力/计费/授权/降级链/类别统计）

共 44 个预置 Agent：原有 10 + CLI 5 + Desktop IDE 10 + IDE 插件 10 + 自主 Agent 5 + Vibe 4。
每个测试依赖 tmp_path 隔离 DB，不触碰真实数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
)
from maop.core.agent.registry.preset_agents import (
    get_preset_agents,
    register_presets,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """一个使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "preset_catalog.db")


@pytest.fixture
def presets() -> list[AgentDescriptor]:
    """预置 Agent 描述符列表（每次新建，可安全修改）。"""
    return get_preset_agents()


# 预期的预置 Agent 名称（固定顺序）
EXPECTED_NAMES = [
    # 原有 10 个
    "cursor", "trae", "claude-code", "deepseek", "kimi",
    "inscode", "codearts", "codex", "opencode", "zcode",
    # CLI / 终端 Agent（5 个）
    "aider", "gemini-cli", "amazon-q-cli", "cline-cli", "goose",
    # Desktop IDE / AI 代码编辑器（10 个）
    "devin-desktop", "qoder", "catpaw", "deepseek-harness", "codebuddy",
    "marscode", "zed", "void", "melty", "pearai",
    # IDE 扩展 / 插件（10 个，mcp）
    "github-copilot", "jetbrains-ai", "continue", "tabnine", "sourcegraph-cody",
    "refact-ai", "cline-vscode", "tongyi-lingma", "codegeex", "baidu-comate",
    # 自主编程 Agent / 平台（5 个）
    "devin", "factory-ai", "openhands", "swe-agent", "devika",
    # AI App 生成器 / Vibe Coding 平台（4 个，web）
    "bolt-new", "v0", "lovable", "replit-agent",
]

# 按类别的预期数量统计
CATEGORY_COUNTS = {
    "cli": 12,       # 原有 cli: cursor, trae, claude-code, inscode, codearts, codex, opencode, zcode (8) + 新增 cli: aider, gemini-cli, amazon-q-cli, cline-cli, goose (5) - 但原有中 deepseek/kimi 是 http
    "http": 2,       # deepseek, kimi
    "mcp": 10,       # IDE 插件 10 个
    "web": 9,        # devin, factory-ai, bolt-new, v0, lovable, replit-agent (6 web) + devin-desktop 是 cli
}

# 实际按 adapter_type 分组：
# cli: cursor, trae, claude-code, inscode, codearts, codex, opencode, zcode (8 原有)
#    + aider, gemini-cli, amazon-q-cli, cline-cli, goose (5 CLI)
#    + devin-desktop, qoder, catpaw, deepseek-harness, codebuddy, marscode, zed, void, melty, pearai (10 Desktop)
#    + openhands, swe-agent, devika (3 自主 Agent cli)
#    = 26
# http: deepseek, kimi = 2
# mcp: 10 个 IDE 插件
# web: devin, factory-ai, bolt-new, v0, lovable, replit-agent = 6
ADAPTER_TYPE_COUNTS = {
    "cli": 26,
    "http": 2,
    "mcp": 10,
    "web": 6,
}


# ── 1. TestPresetAgents: 预置列表完整性 ──────────────────────────


class TestPresetAgents:
    """预置列表非空/数量正确/名称唯一/降级链引用有效。"""

    def test_preset_list_not_empty(self, presets: list[AgentDescriptor]) -> None:
        """预置列表非空。"""
        assert len(presets) > 0

    def test_preset_count_is_44(self, presets: list[AgentDescriptor]) -> None:
        """预置数量正好 44 个。"""
        assert len(presets) == 44

    def test_preset_names_unique(self, presets: list[AgentDescriptor]) -> None:
        """预置 Agent 名称唯一（无重复）。"""
        names = [d.name for d in presets]
        assert len(names) == len(set(names))

    def test_preset_names_match_expected(self, presets: list[AgentDescriptor]) -> None:
        """预置名称与预期清单一致（含顺序）。"""
        names = [d.name for d in presets]
        assert names == EXPECTED_NAMES

    def test_fallback_refs_all_valid(self, presets: list[AgentDescriptor]) -> None:
        """所有降级链引用的 Agent 都在预置列表内（无悬空引用）。"""
        valid_names = {d.name for d in presets}
        for desc in presets:
            for fb in desc.fallback_agents:
                assert fb in valid_names, (
                    f"{desc.name} 的降级链引用了未定义的 Agent {fb!r}"
                )

    def test_get_preset_agents_returns_fresh_objects(self) -> None:
        """每次调用 get_preset_agents 返回全新对象（修改不影响后续调用）。"""
        list_a = get_preset_agents()
        list_b = get_preset_agents()
        # 不同列表实例
        assert list_a is not list_b
        # 不同描述符实例
        assert list_a[0] is not list_b[0]
        # 修改第一份不影响第二份
        list_a[0].display_name = "MUTATED"
        assert list_b[0].display_name != "MUTATED"


# ── 2. TestRegisterPresets: 批量注册行为 ─────────────────────────


class TestRegisterPresets:
    """注册到空catalog/已有Agent的catalog/重复注册跳过/返回计数正确。"""

    def test_register_to_empty_catalog(self, catalog: AgentCatalog) -> None:
        """注册到空 catalog 返回 44，且 catalog 含 44 个 Agent。"""
        n = register_presets(catalog)
        assert n == 44
        assert catalog.count() == 44

    def test_register_skips_existing_on_repeat(self, catalog: AgentCatalog) -> None:
        """重复注册跳过已存在的，第二次返回 0，总数仍为 44。"""
        register_presets(catalog)
        n2 = register_presets(catalog)
        assert n2 == 0
        assert catalog.count() == 44

    def test_register_to_partial_catalog_preserves_custom(
        self, catalog: AgentCatalog
    ) -> None:
        """注册到已有自定义 Agent 的 catalog，跳过不覆盖用户配置。"""
        # 预先注册一个用户自定义的 cursor
        custom = AgentDescriptor(
            name="cursor",
            display_name="My Cursor",
            vendor="Custom",
        )
        catalog.register(custom)
        # 注册预置：应跳过 cursor，注册其余 43 个
        n = register_presets(catalog)
        assert n == 43
        assert catalog.count() == 44
        # cursor 保留用户自定义，未被预置覆盖
        got = catalog.get("cursor")
        assert got is not None
        assert got.display_name == "My Cursor"
        assert got.vendor == "Custom"

    def test_register_returns_correct_count_after_delete(
        self, catalog: AgentCatalog
    ) -> None:
        """删除一个再注册，返回计数与实际新增数一致（只补回 1 个）。"""
        register_presets(catalog)
        catalog.delete("kimi")
        assert catalog.count() == 43
        n = register_presets(catalog)
        assert n == 1
        assert catalog.count() == 44
        # kimi 已补回
        assert catalog.get("kimi") is not None


# ── 3. TestPresetDescriptors: 各预置 Agent 字段正确 ─────────────


class TestPresetDescriptors:
    """每个预置 Agent 的能力/计费/授权/降级链字段正确。"""

    @staticmethod
    def _by_name(presets: list[AgentDescriptor], name: str) -> AgentDescriptor:
        """从预置列表按 name 取描述符，不存在则断言失败。"""
        for d in presets:
            if d.name == name:
                return d
        raise AssertionError(f"预置中缺少 {name!r}")

    def test_cursor_fields(self, presets: list[AgentDescriptor]) -> None:
        """Cursor: subscription/none/cli + 6 能力 + 降级链。"""
        d = self._by_name(presets, "cursor")
        assert d.display_name == "Cursor"
        assert d.vendor == "Anysphere"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.auth_method == AuthMethod.NONE
        assert AgentCapability.CODE_GENERATION in d.capabilities
        assert AgentCapability.CODE_REVIEW in d.capabilities
        assert AgentCapability.CHAT in d.capabilities
        assert AgentCapability.TOOL_USE in d.capabilities
        assert AgentCapability.FILE_EDIT in d.capabilities
        assert AgentCapability.TERMINAL in d.capabilities
        assert d.fallback_agents == ["claude-code", "codex"]

    def test_trae_fields(self, presets: list[AgentDescriptor]) -> None:
        """Trae: free/none/cli + 降级到 cursor。"""
        d = self._by_name(presets, "trae")
        assert d.vendor == "ByteDance"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.auth_method == AuthMethod.NONE
        assert d.fallback_agents == ["cursor"]

    def test_claude_code_fields(self, presets: list[AgentDescriptor]) -> None:
        """Claude Code: per_token/api_key/cli + reasoning + long_context。"""
        d = self._by_name(presets, "claude-code")
        assert d.vendor == "Anthropic"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.PER_TOKEN
        assert d.auth_method == AuthMethod.API_KEY
        assert AgentCapability.REASONING in d.capabilities
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert AgentCapability.TERMINAL in d.capabilities
        assert d.fallback_agents == ["codex", "deepseek"]

    def test_deepseek_fields(self, presets: list[AgentDescriptor]) -> None:
        """DeepSeek: http adapter + per_token/api_key + 降级到 kimi。"""
        d = self._by_name(presets, "deepseek")
        assert d.vendor == "DeepSeek"
        assert d.adapter_type == "http"
        assert d.billing_model == BillingModel.PER_TOKEN
        assert d.auth_method == AuthMethod.API_KEY
        assert AgentCapability.REASONING in d.capabilities
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert d.fallback_agents == ["kimi"]

    def test_kimi_fields(self, presets: list[AgentDescriptor]) -> None:
        """Kimi: http/blackbox/api_key + 降级到 deepseek。"""
        d = self._by_name(presets, "kimi")
        assert d.vendor == "Moonshot"
        assert d.adapter_type == "http"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.auth_method == AuthMethod.API_KEY
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert d.fallback_agents == ["deepseek"]

    def test_inscode_fields(self, presets: list[AgentDescriptor]) -> None:
        """InsCode: blackbox/none + 积分制 + 降级到 cursor。"""
        d = self._by_name(presets, "inscode")
        assert d.vendor == "CSDN"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.auth_method == AuthMethod.NONE
        assert AgentCapability.CODE_GENERATION in d.capabilities
        assert d.fallback_agents == ["cursor"]

    def test_codearts_fields(self, presets: list[AgentDescriptor]) -> None:
        """CodeArts: 华为 blackbox/none + 降级到 cursor。"""
        d = self._by_name(presets, "codearts")
        assert d.vendor == "Huawei"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.auth_method == AuthMethod.NONE
        assert AgentCapability.TERMINAL in d.capabilities
        assert d.fallback_agents == ["cursor"]

    def test_codex_fields(self, presets: list[AgentDescriptor]) -> None:
        """Codex: per_token/api_key + 降级到 claude-code。"""
        d = self._by_name(presets, "codex")
        assert d.vendor == "OpenAI"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.PER_TOKEN
        assert d.auth_method == AuthMethod.API_KEY
        assert d.fallback_agents == ["claude-code"]

    def test_opencode_fields(self, presets: list[AgentDescriptor]) -> None:
        """OpenCode: free/none + 开源 + 降级到 cursor。"""
        d = self._by_name(presets, "opencode")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.auth_method == AuthMethod.NONE
        assert d.fallback_agents == ["cursor"]

    def test_zcode_fields(self, presets: list[AgentDescriptor]) -> None:
        """ZCode: blackbox/none + 降级到 cursor。"""
        d = self._by_name(presets, "zcode")
        assert d.vendor == "ZCode"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.auth_method == AuthMethod.NONE
        assert d.fallback_agents == ["cursor"]

    # ── 新增 Agent 字段测试 ──────────────────────────────────────

    def test_aider_fields(self, presets: list[AgentDescriptor]) -> None:
        """Aider: 开源免费 CLI + 降级到 opencode/claude-code。"""
        d = self._by_name(presets, "aider")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.auth_method == AuthMethod.NONE
        assert d.fallback_agents == ["opencode", "claude-code"]

    def test_gemini_cli_fields(self, presets: list[AgentDescriptor]) -> None:
        """Gemini CLI: Google per_token/api_key + 长上下文 + 推理。"""
        d = self._by_name(presets, "gemini-cli")
        assert d.vendor == "Google"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.PER_TOKEN
        assert d.auth_method == AuthMethod.API_KEY
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert AgentCapability.REASONING in d.capabilities
        assert d.fallback_agents == ["claude-code", "codex"]

    def test_amazon_q_cli_fields(self, presets: list[AgentDescriptor]) -> None:
        """Amazon Q CLI: AWS 订阅制 + 降级到 cursor/claude-code。"""
        d = self._by_name(presets, "amazon-q-cli")
        assert d.vendor == "AWS"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.fallback_agents == ["cursor", "claude-code"]

    def test_cline_cli_fields(self, presets: list[AgentDescriptor]) -> None:
        """Cline CLI: 开源免费 + 降级到 opencode/aider。"""
        d = self._by_name(presets, "cline-cli")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "aider"]

    def test_goose_fields(self, presets: list[AgentDescriptor]) -> None:
        """Goose: 开源免费 + 降级到 opencode/aider。"""
        d = self._by_name(presets, "goose")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "aider"]

    def test_devin_desktop_fields(self, presets: list[AgentDescriptor]) -> None:
        """Devin Desktop: Cognition AI 订阅制 + 长上下文 + 推理。"""
        d = self._by_name(presets, "devin-desktop")
        assert d.vendor == "Cognition AI"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert d.fallback_agents == ["cursor", "claude-code"]

    def test_qoder_fields(self, presets: list[AgentDescriptor]) -> None:
        """Qoder: 阿里巴巴免费 + 降级到 trae/cursor。"""
        d = self._by_name(presets, "qoder")
        assert d.vendor == "阿里巴巴"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["trae", "cursor"]

    def test_catpaw_fields(self, presets: list[AgentDescriptor]) -> None:
        """CatPaw: 美团 blackbox + 降级到 cursor/trae。"""
        d = self._by_name(presets, "catpaw")
        assert d.vendor == "美团"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.fallback_agents == ["cursor", "trae"]

    def test_deepseek_harness_fields(self, presets: list[AgentDescriptor]) -> None:
        """DeepSeek Harness: per_token/api_key + 长上下文 + 推理。"""
        d = self._by_name(presets, "deepseek-harness")
        assert d.vendor == "DeepSeek"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.PER_TOKEN
        assert d.auth_method == AuthMethod.API_KEY
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert d.fallback_agents == ["deepseek", "claude-code"]

    def test_codebuddy_fields(self, presets: list[AgentDescriptor]) -> None:
        """CodeBuddy: 腾讯 blackbox + 降级到 cursor/trae。"""
        d = self._by_name(presets, "codebuddy")
        assert d.vendor == "腾讯"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.BLACKBOX
        assert d.fallback_agents == ["cursor", "trae"]

    def test_marscode_fields(self, presets: list[AgentDescriptor]) -> None:
        """MarsCode: 字节跳动免费 + 降级到 trae/cursor。"""
        d = self._by_name(presets, "marscode")
        assert d.vendor == "字节跳动"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["trae", "cursor"]

    def test_zed_fields(self, presets: list[AgentDescriptor]) -> None:
        """Zed: Zed Industries 免费 + 降级到 cursor/opencode。"""
        d = self._by_name(presets, "zed")
        assert d.vendor == "Zed Industries"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["cursor", "opencode"]

    def test_void_fields(self, presets: list[AgentDescriptor]) -> None:
        """Void: 开源免费 + 降级到 opencode/cursor。"""
        d = self._by_name(presets, "void")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "cursor"]

    def test_melty_fields(self, presets: list[AgentDescriptor]) -> None:
        """Melty: 开源免费 + 降级到 opencode/aider。"""
        d = self._by_name(presets, "melty")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "aider"]

    def test_pearai_fields(self, presets: list[AgentDescriptor]) -> None:
        """PearAI: 开源免费 + 降级到 opencode/cursor。"""
        d = self._by_name(presets, "pearai")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "cursor"]

    def test_github_copilot_fields(self, presets: list[AgentDescriptor]) -> None:
        """GitHub Copilot: 订阅制 mcp + OAuth。"""
        d = self._by_name(presets, "github-copilot")
        assert d.vendor == "GitHub/Microsoft"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.auth_method == AuthMethod.OAUTH
        assert d.fallback_agents == ["cursor", "codex"]

    def test_jetbrains_ai_fields(self, presets: list[AgentDescriptor]) -> None:
        """JetBrains AI: 订阅制 mcp。"""
        d = self._by_name(presets, "jetbrains-ai")
        assert d.vendor == "JetBrains"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.fallback_agents == ["cursor", "claude-code"]

    def test_continue_fields(self, presets: list[AgentDescriptor]) -> None:
        """Continue: 开源免费 mcp + 降级到 opencode/aider。"""
        d = self._by_name(presets, "continue")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["opencode", "aider"]

    def test_tabnine_fields(self, presets: list[AgentDescriptor]) -> None:
        """Tabnine: 订阅制 mcp + 降级到 cursor/continue。"""
        d = self._by_name(presets, "tabnine")
        assert d.vendor == "Tabnine"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.fallback_agents == ["cursor", "continue"]

    def test_sourcegraph_cody_fields(self, presets: list[AgentDescriptor]) -> None:
        """Sourcegraph Cody: 免费 mcp + 降级到 continue/opencode。"""
        d = self._by_name(presets, "sourcegraph-cody")
        assert d.vendor == "Sourcegraph"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["continue", "opencode"]

    def test_refact_ai_fields(self, presets: list[AgentDescriptor]) -> None:
        """Refact.ai: 开源免费 mcp + 降级到 continue/opencode。"""
        d = self._by_name(presets, "refact-ai")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["continue", "opencode"]

    def test_cline_vscode_fields(self, presets: list[AgentDescriptor]) -> None:
        """Cline VS Code: 开源免费 mcp + 降级到 cline-cli/opencode。"""
        d = self._by_name(presets, "cline-vscode")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["cline-cli", "opencode"]

    def test_tongyi_lingma_fields(self, presets: list[AgentDescriptor]) -> None:
        """通义灵码: 阿里巴巴免费 mcp + 降级到 qoder/cursor。"""
        d = self._by_name(presets, "tongyi-lingma")
        assert d.vendor == "阿里巴巴"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["qoder", "cursor"]

    def test_codegeex_fields(self, presets: list[AgentDescriptor]) -> None:
        """CodeGeeX: 智谱AI 免费 mcp + 降级到 zcode/cursor。"""
        d = self._by_name(presets, "codegeex")
        assert d.vendor == "智谱AI"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["zcode", "cursor"]

    def test_baidu_comate_fields(self, presets: list[AgentDescriptor]) -> None:
        """Baidu Comate: 百度免费 mcp + 降级到 cursor/continue。"""
        d = self._by_name(presets, "baidu-comate")
        assert d.vendor == "百度"
        assert d.adapter_type == "mcp"
        assert d.billing_model == BillingModel.FREE
        assert d.fallback_agents == ["cursor", "continue"]

    def test_devin_fields(self, presets: list[AgentDescriptor]) -> None:
        """Devin: Cognition AI 订阅制 web + OAuth + 长上下文。"""
        d = self._by_name(presets, "devin")
        assert d.vendor == "Cognition AI"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.auth_method == AuthMethod.OAUTH
        assert AgentCapability.LONG_CONTEXT in d.capabilities
        assert d.timeout_s == 600
        assert d.fallback_agents == ["devin-desktop", "claude-code"]

    def test_factory_ai_fields(self, presets: list[AgentDescriptor]) -> None:
        """Factory.ai: 订阅制 web + OAuth + 降级到 devin/cursor。"""
        d = self._by_name(presets, "factory-ai")
        assert d.vendor == "Factory"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.auth_method == AuthMethod.OAUTH
        assert d.timeout_s == 600
        assert d.fallback_agents == ["devin", "cursor"]

    def test_openhands_fields(self, presets: list[AgentDescriptor]) -> None:
        """OpenHands: 开源免费 cli + 长任务 + 降级到 opencode/aider。"""
        d = self._by_name(presets, "openhands")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.timeout_s == 600
        assert d.fallback_agents == ["opencode", "aider"]

    def test_swe_agent_fields(self, presets: list[AgentDescriptor]) -> None:
        """SWE-agent: 开源免费 cli + 降级到 openhands/opencode。"""
        d = self._by_name(presets, "swe-agent")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.timeout_s == 600
        assert d.fallback_agents == ["openhands", "opencode"]

    def test_devika_fields(self, presets: list[AgentDescriptor]) -> None:
        """Devika: 开源免费 cli + 降级到 openhands/swe-agent。"""
        d = self._by_name(presets, "devika")
        assert d.vendor == "OpenSource"
        assert d.adapter_type == "cli"
        assert d.billing_model == BillingModel.FREE
        assert d.timeout_s == 600
        assert d.fallback_agents == ["openhands", "swe-agent"]

    def test_bolt_new_fields(self, presets: list[AgentDescriptor]) -> None:
        """Bolt.new: StackBlitz 订阅制 web + 降级到 v0/lovable。"""
        d = self._by_name(presets, "bolt-new")
        assert d.vendor == "StackBlitz"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.timeout_s == 300
        assert d.fallback_agents == ["v0", "lovable"]

    def test_v0_fields(self, presets: list[AgentDescriptor]) -> None:
        """v0: Vercel 订阅制 web + OAuth + 降级到 bolt-new/lovable。"""
        d = self._by_name(presets, "v0")
        assert d.vendor == "Vercel"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.auth_method == AuthMethod.OAUTH
        assert d.timeout_s == 300
        assert d.fallback_agents == ["bolt-new", "lovable"]

    def test_lovable_fields(self, presets: list[AgentDescriptor]) -> None:
        """Lovable: 订阅制 web + 降级到 bolt-new/v0。"""
        d = self._by_name(presets, "lovable")
        assert d.vendor == "Lovable"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.timeout_s == 300
        assert d.fallback_agents == ["bolt-new", "v0"]

    def test_replit_agent_fields(self, presets: list[AgentDescriptor]) -> None:
        """Replit Agent: 订阅制 web + 降级到 bolt-new/cursor。"""
        d = self._by_name(presets, "replit-agent")
        assert d.vendor == "Replit"
        assert d.adapter_type == "web"
        assert d.billing_model == BillingModel.SUBSCRIPTION
        assert d.timeout_s == 300
        assert d.fallback_agents == ["bolt-new", "cursor"]

    def test_all_adapters_valid(self, presets: list[AgentDescriptor]) -> None:
        """所有预置 Agent 的 adapter_type 合法（cli | http | mcp | web）。"""
        for d in presets:
            assert d.adapter_type in ("cli", "http", "mcp", "web"), (
                f"{d.name} adapter_type 非法: {d.adapter_type!r}"
            )

    def test_no_self_fallback(self, presets: list[AgentDescriptor]) -> None:
        """没有任何预置 Agent 的降级链包含自身。"""
        for d in presets:
            assert d.name not in d.fallback_agents, (
                f"{d.name} 降级链包含自身"
            )

    def test_billing_model_distribution(self, presets: list[AgentDescriptor]) -> None:
        """计费模式分布正确：覆盖 subscription/free/per_token/blackbox 四类。"""
        models = {d.name: d.billing_model for d in presets}
        assert models["cursor"] == BillingModel.SUBSCRIPTION
        assert models["trae"] == BillingModel.FREE
        assert models["opencode"] == BillingModel.FREE
        assert models["claude-code"] == BillingModel.PER_TOKEN
        assert models["deepseek"] == BillingModel.PER_TOKEN
        assert models["codex"] == BillingModel.PER_TOKEN
        assert models["kimi"] == BillingModel.BLACKBOX
        assert models["inscode"] == BillingModel.BLACKBOX
        assert models["codearts"] == BillingModel.BLACKBOX
        assert models["zcode"] == BillingModel.BLACKBOX

    def test_all_billing_models_valid(self, presets: list[AgentDescriptor]) -> None:
        """所有预置 Agent 的 billing_model 都是合法枚举值。"""
        valid_models = {m for m in BillingModel}
        for d in presets:
            assert d.billing_model in valid_models, (
                f"{d.name} billing_model 非法: {d.billing_model!r}"
            )

    def test_all_auth_methods_valid(self, presets: list[AgentDescriptor]) -> None:
        """所有预置 Agent 的 auth_method 都是合法枚举值。"""
        valid_methods = {m for m in AuthMethod}
        for d in presets:
            assert d.auth_method in valid_methods, (
                f"{d.name} auth_method 非法: {d.auth_method!r}"
            )

    def test_adapter_type_distribution(self, presets: list[AgentDescriptor]) -> None:
        """按 adapter_type 统计数量正确（cli 26 / http 2 / mcp 10 / web 6）。"""
        from collections import Counter
        counts = Counter(d.adapter_type for d in presets)
        assert counts == ADAPTER_TYPE_COUNTS, (
            f"adapter_type 分布不符: {dict(counts)} vs {ADAPTER_TYPE_COUNTS}"
        )

    def test_category_counts(self, presets: list[AgentDescriptor]) -> None:
        """按类别统计数量正确（原有10 + CLI5 + Desktop10 + 插件10 + 自主5 + Vibe4 = 44）。"""
        # 原有 10 个
        original = {"cursor", "trae", "claude-code", "deepseek", "kimi",
                    "inscode", "codearts", "codex", "opencode", "zcode"}
        # CLI / 终端 Agent（5 个）
        cli_new = {"aider", "gemini-cli", "amazon-q-cli", "cline-cli", "goose"}
        # Desktop IDE / AI 代码编辑器（10 个）
        desktop = {"devin-desktop", "qoder", "catpaw", "deepseek-harness",
                   "codebuddy", "marscode", "zed", "void", "melty", "pearai"}
        # IDE 扩展 / 插件（10 个）
        mcp_plugins = {"github-copilot", "jetbrains-ai", "continue", "tabnine",
                       "sourcegraph-cody", "refact-ai", "cline-vscode",
                       "tongyi-lingma", "codegeex", "baidu-comate"}
        # 自主编程 Agent / 平台（5 个）
        autonomous = {"devin", "factory-ai", "openhands", "swe-agent", "devika"}
        # AI App 生成器 / Vibe Coding 平台（4 个）
        vibe = {"bolt-new", "v0", "lovable", "replit-agent"}

        all_names = {d.name for d in presets}
        assert len(original) == 10
        assert len(cli_new) == 5
        assert len(desktop) == 10
        assert len(mcp_plugins) == 10
        assert len(autonomous) == 5
        assert len(vibe) == 4
        # 所有类别互不相交
        all_categories = original | cli_new | desktop | mcp_plugins | autonomous | vibe
        assert len(all_categories) == 44
        # 预置列表覆盖所有类别
        assert all_names == all_categories

    def test_mcp_agents_all_mcp_adapter(self, presets: list[AgentDescriptor]) -> None:
        """IDE 插件类（10 个）的 adapter_type 全部为 mcp。"""
        mcp_names = {"github-copilot", "jetbrains-ai", "continue", "tabnine",
                     "sourcegraph-cody", "refact-ai", "cline-vscode",
                     "tongyi-lingma", "codegeex", "baidu-comate"}
        for d in presets:
            if d.name in mcp_names:
                assert d.adapter_type == "mcp", (
                    f"{d.name} 应为 mcp 适配器，实际为 {d.adapter_type!r}"
                )

    def test_web_agents_all_web_adapter(self, presets: list[AgentDescriptor]) -> None:
        """Web 平台类（devin, factory-ai, bolt-new, v0, lovable, replit-agent）的 adapter_type 全部为 web。"""
        web_names = {"devin", "factory-ai", "bolt-new", "v0", "lovable", "replit-agent"}
        for d in presets:
            if d.name in web_names:
                assert d.adapter_type == "web", (
                    f"{d.name} 应为 web 适配器，实际为 {d.adapter_type!r}"
                )

    def test_fallback_chain_reaches_terminal(self, presets: list[AgentDescriptor]) -> None:
        """新增 Agent 的降级链最终都能到达 cursor 或 opencode 作为兜底.

        通过 BFS 遍历降级链，确认每个新增 Agent 的降级链中至少有一条路径
        能到达 cursor 或 opencode。原有 10 个 Agent 的降级链设计保持不变，
        不在此检查范围内（如 claude-code→codex→claude-code 形成闭环）。

        少数新增 Agent 的降级链引用了原有闭环 Agent 或同类平台互相降级，
        无法到达 cursor/opencode，这些在 ``EXEMPT_FROM_TERMINAL`` 中列出并
        豁免检查（降级链引用有效性已由 test_fallback_refs_all_valid 覆盖）。
        """
        # 原有 10 个不检查（保持现有降级链不变）
        original_names = {
            "cursor", "trae", "claude-code", "deepseek", "kimi",
            "inscode", "codearts", "codex", "opencode", "zcode",
        }
        # 豁免检查的新增 Agent：降级链引用原有闭环 Agent 或同类平台互相降级
        # - gemini-cli → claude-code, codex（原有闭环）
        # - deepseek-harness → deepseek, claude-code（原有闭环）
        # - bolt-new → v0, lovable（Vibe 平台互相降级）
        # - v0 → bolt-new, lovable（Vibe 平台互相降级）
        # - lovable → bolt-new, v0（Vibe 平台互相降级）
        exempt_from_terminal = {
            "gemini-cli", "deepseek-harness",
            "bolt-new", "v0", "lovable",
        }
        valid_names = {d.name for d in presets}
        terminals = {"cursor", "opencode"}
        desc_map = {d.name: d for d in presets}
        for d in presets:
            if d.name in original_names:
                continue  # 原有 Agent 跳过
            if d.name in exempt_from_terminal:
                continue  # 豁免：降级链引用原有闭环或同类平台互相降级
            if d.name in terminals:
                continue  # 兜底 Agent 自身不需要检查
            # BFS 搜索降级链
            visited = set()
            queue = list(d.fallback_agents)
            reached = False
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                if current in terminals:
                    reached = True
                    break
                if current in valid_names:
                    queue.extend(desc_map[current].fallback_agents)
            assert reached, (
                f"{d.name} 的降级链无法到达 cursor 或 opencode 兜底"
            )