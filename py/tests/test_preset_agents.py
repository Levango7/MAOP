"""预置 Agent 配置 (preset_agents) 测试.

覆盖三组：
    - TestPresetAgents     — 预置列表非空/数量正确/名称唯一/降级链引用有效
    - TestRegisterPresets  — 注册到空catalog/已有Agent的catalog/重复注册跳过/返回计数正确
    - TestPresetDescriptors— 每个预置 Agent 的字段正确（能力/计费/授权/降级链）

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
    "cursor", "trae", "claude-code", "deepseek", "kimi",
    "inscode", "codearts", "codex", "opencode", "zcode",
]


# ── 1. TestPresetAgents: 预置列表完整性 ──────────────────────────


class TestPresetAgents:
    """预置列表非空/数量正确/名称唯一/降级链引用有效。"""

    def test_preset_list_not_empty(self, presets: list[AgentDescriptor]) -> None:
        """预置列表非空。"""
        assert len(presets) > 0

    def test_preset_count_is_ten(self, presets: list[AgentDescriptor]) -> None:
        """预置数量正好 10 个。"""
        assert len(presets) == 10

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
        """注册到空 catalog 返回 10，且 catalog 含 10 个 Agent。"""
        n = register_presets(catalog)
        assert n == 10
        assert catalog.count() == 10

    def test_register_skips_existing_on_repeat(self, catalog: AgentCatalog) -> None:
        """重复注册跳过已存在的，第二次返回 0，总数仍为 10。"""
        register_presets(catalog)
        n2 = register_presets(catalog)
        assert n2 == 0
        assert catalog.count() == 10

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
        # 注册预置：应跳过 cursor，注册其余 9 个
        n = register_presets(catalog)
        assert n == 9
        assert catalog.count() == 10
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
        assert catalog.count() == 9
        n = register_presets(catalog)
        assert n == 1
        assert catalog.count() == 10
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

    def test_all_adapters_valid(self, presets: list[AgentDescriptor]) -> None:
        """所有预置 Agent 的 adapter_type 合法（cli 或 http）。"""
        for d in presets:
            assert d.adapter_type in ("cli", "http"), (
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