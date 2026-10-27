# preset_agents: descriptor construction helpers + category builders.
# Split from preset_agents.py into preset_agents/_make.py.

from __future__ import annotations

from typing import Any

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
)

__all__ = [
    "_cli_agents",
    "_desktop_ide_agents",
    "_original_agents",
]


def _make_descriptor(
    name: str,
    display_name: str,
    vendor: str,
    adapter_type: str,
    capabilities: list[str],
    billing_model: str,
    auth_method: str,
    **kwargs: Any,
) -> AgentDescriptor:
    """辅助函数：从字符串参数构造 AgentDescriptor.

    将字符串形式的能力/计费/授权转换为对应枚举，其余关键字参数直传
    ``AgentDescriptor``（如 ``max_concurrent`` / ``timeout_s`` / ``fallback_agents``）。
    使用字符串入参是为了让 ``get_preset_agents`` 的声明表更紧凑、可读。

    Parameters
    ----------
    name : str
        Agent 唯一标识。
    display_name : str
        展示名称。
    vendor : str
        供应商。
    adapter_type : str
        适配器类型（cli | http | mcp | web）。
    capabilities : list[str]
        能力字符串列表，逐项转为 ``AgentCapability`` 枚举。
    billing_model : str
        计费模式字符串，转为 ``BillingModel`` 枚举。
    auth_method : str
        授权方式字符串，转为 ``AuthMethod`` 枚举。
    **kwargs : Any
        透传 ``AgentDescriptor`` 的其余字段（``fallback_agents`` /
        ``max_concurrent`` / ``timeout_s`` 等）。

    Returns
    -------
    AgentDescriptor
        构造完成的描述符。
    """
    cap_enums = [AgentCapability(c) for c in capabilities]
    return AgentDescriptor(
        name=name,
        display_name=display_name,
        vendor=vendor,
        adapter_type=adapter_type,
        capabilities=cap_enums,
        billing_model=BillingModel(billing_model),
        auth_method=AuthMethod(auth_method),
        **kwargs,
    )


# ── 预置列表：分类构建器 ───────────────────────────────────────────

def _original_agents() -> list[AgentDescriptor]:
    """原有 10 个预置 Agent（CLI / HTTP 混合）."""
    return [
        # 1. Cursor — Anysphere 订阅制 IDE 内置 AI
        _make_descriptor(
            name="cursor",
            display_name="Cursor",
            vendor="Anysphere",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["claude-code", "codex"],
        ),
        # 2. Trae — ByteDance 免费 IDE 内置 AI
        _make_descriptor(
            name="trae",
            display_name="Trae",
            vendor="ByteDance",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["cursor"],
            region="domestic",  # 国产：字节跳动
        ),
        # 3. Claude Code — Anthropic 按 token 计费 CLI（长上下文 + 推理）
        _make_descriptor(
            name="claude-code",
            display_name="Claude Code",
            vendor="Anthropic",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal", "long_context",
            ],
            billing_model="per_token",
            auth_method="api_key",
            max_concurrent=2,
            timeout_s=180,
            fallback_agents=["codex", "deepseek"],
        ),
        # 4. DeepSeek — 按 token 计费 HTTP API（推理 + 长上下文）
        _make_descriptor(
            name="deepseek",
            display_name="DeepSeek",
            vendor="DeepSeek",
            adapter_type="http",
            capabilities=[
                "code_generation", "code_review", "chat",
                "reasoning", "long_context",
            ],
            billing_model="per_token",
            auth_method="api_key",
            max_concurrent=4,
            timeout_s=90,
            fallback_agents=["kimi"],
        ),
        # 5. Kimi — Moonshot 包月/积分制 HTTP API（长上下文）
        _make_descriptor(
            name="kimi",
            display_name="Kimi",
            vendor="Moonshot",
            adapter_type="http",
            capabilities=["code_generation", "chat", "long_context"],
            billing_model="blackbox",
            auth_method="api_key",
            max_concurrent=2,
            timeout_s=90,
            fallback_agents=["deepseek"],
        ),
        # 6. InsCode — CSDN 积分制 CLI（轻量编码 + 对话）
        _make_descriptor(
            name="inscode",
            display_name="InsCode",
            vendor="CSDN",
            adapter_type="cli",
            capabilities=["code_generation", "chat", "tool_use"],
            billing_model="blackbox",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor"],
        ),
        # 7. CodeArts — 华为云 blackbox CLI（全能力）
        _make_descriptor(
            name="codearts",
            display_name="CodeArts",
            vendor="Huawei",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="blackbox",
            auth_method="none",
            max_concurrent=2,
            timeout_s=120,
            fallback_agents=["cursor"],
            region="domestic",  # 国产：华为云
        ),
        # 8. Codex — OpenAI 按 token 计费 CLI
        _make_descriptor(
            name="codex",
            display_name="Codex",
            vendor="OpenAI",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="per_token",
            auth_method="api_key",
            max_concurrent=2,
            timeout_s=120,
            fallback_agents=["claude-code"],
        ),
        # 9. OpenCode — 开源免费 CLI（全能力）
        _make_descriptor(
            name="opencode",
            display_name="OpenCode",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["cursor"],
        ),
        # 10. ZCode — blackbox CLI（轻量编码 + 对话）
        _make_descriptor(
            name="zcode",
            display_name="ZCode",
            vendor="ZCode",
            adapter_type="cli",
            capabilities=["code_generation", "chat", "tool_use"],
            billing_model="blackbox",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor"],
            region="domestic",  # 国产：ZCode
        ),
    ]


def _cli_agents() -> list[AgentDescriptor]:
    """CLI / 终端 Agent（5 个）."""
    return [
        # 11. Aider — 开源免费 CLI，终端内 pair programming
        _make_descriptor(
            name="aider",
            display_name="Aider",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "claude-code"],
        ),
        # 12. Gemini CLI — Google 按 token 计费 CLI（长上下文 + 推理 + 终端）
        _make_descriptor(
            name="gemini-cli",
            display_name="Gemini CLI",
            vendor="Google",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "long_context", "tool_use", "file_edit", "terminal",
            ],
            billing_model="per_token",
            auth_method="api_key",
            max_concurrent=2,
            timeout_s=120,
            fallback_agents=["claude-code", "codex"],
        ),
        # 13. Amazon Q Developer CLI — AWS 订阅制 CLI
        _make_descriptor(
            name="amazon-q-cli",
            display_name="Amazon Q Developer CLI",
            vendor="AWS",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=2,
            timeout_s=120,
            fallback_agents=["cursor", "claude-code"],
        ),
        # 14. Cline CLI — 开源免费 CLI（终端内自主编程）
        _make_descriptor(
            name="cline-cli",
            display_name="Cline CLI",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "aider"],
        ),
        # 15. Goose — 开源免费 CLI（轻量编码 + 终端）
        _make_descriptor(
            name="goose",
            display_name="Goose",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "chat", "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "aider"],
        ),
    ]


def _desktop_ide_agents() -> list[AgentDescriptor]:
    """Desktop IDE / AI 代码编辑器（10 个）."""
    return [
        # 16. Devin Desktop — Cognition AI 订阅制桌面端（原 Windsurf，全能力 + 长上下文）
        _make_descriptor(
            name="devin-desktop",
            display_name="Devin Desktop",
            vendor="Cognition AI",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal", "long_context",
            ],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=180,
            fallback_agents=["cursor", "claude-code"],
        ),
        # 17. Qoder — 阿里巴巴免费 AI 代码编辑器
        _make_descriptor(
            name="qoder",
            display_name="Qoder",
            vendor="阿里巴巴",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["trae", "cursor"],
            region="domestic",  # 国产：阿里巴巴
        ),
        # 18. CatPaw — 美团 blackbox AI 代码编辑器
        _make_descriptor(
            name="catpaw",
            display_name="CatPaw",
            vendor="美团",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="blackbox",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["cursor", "trae"],
            region="domestic",  # 国产：美团
        ),
        # 19. DeepSeek Harness — DeepSeek 按 token 计费 CLI（推理 + 长上下文 + 终端）
        _make_descriptor(
            name="deepseek-harness",
            display_name="DeepSeek Harness",
            vendor="DeepSeek",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "long_context", "tool_use", "file_edit", "terminal",
            ],
            billing_model="per_token",
            auth_method="api_key",
            max_concurrent=2,
            timeout_s=180,
            fallback_agents=["deepseek", "claude-code"],
            region="domestic",  # 国产：DeepSeek
        ),
        # 20. CodeBuddy — 腾讯 blackbox AI 代码编辑器
        _make_descriptor(
            name="codebuddy",
            display_name="CodeBuddy",
            vendor="腾讯",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="blackbox",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["cursor", "trae"],
            region="domestic",  # 国产：腾讯
        ),
        # 21. MarsCode — 字节跳动免费 AI 代码编辑器
        _make_descriptor(
            name="marscode",
            display_name="MarsCode",
            vendor="字节跳动",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["trae", "cursor"],
            region="domestic",  # 国产：字节跳动
        ),
        # 22. Zed — Zed Industries 免费 AI 代码编辑器
        _make_descriptor(
            name="zed",
            display_name="Zed",
            vendor="Zed Industries",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["cursor", "opencode"],
        ),
        # 23. Void — 开源免费 AI 代码编辑器（Cursor 开源替代）
        _make_descriptor(
            name="void",
            display_name="Void",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "cursor"],
        ),
        # 24. Melty — 开源免费 AI 代码编辑器
        _make_descriptor(
            name="melty",
            display_name="Melty",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "aider"],
        ),
        # 25. PearAI — 开源免费 AI 代码编辑器
        _make_descriptor(
            name="pearai",
            display_name="PearAI",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=120,
            fallback_agents=["opencode", "cursor"],
        ),
    ]
