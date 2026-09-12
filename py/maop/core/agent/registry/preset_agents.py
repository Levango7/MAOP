"""MAOP 预置 Agent 配置 — 常见 AI 编程工具开箱即用.

定义 44 个主流 AI 编码工具的默认 ``AgentDescriptor``，并提供批量注册函数
``register_presets``，让用户无需逐个手写元数据即可快速接入。

预置清单按类别分组（共 44 个）：

**原有 10 个（CLI / HTTP 混合）**::

    ┌──────────────┬────────────┬───────────────┬────────────┬──────────┐
    │ Agent        │ vendor     │ billing       │ auth       │ adapter  │
    ├──────────────┼────────────┼───────────────┼────────────┼──────────┤
    │ cursor       │ Anysphere  │ subscription  │ none       │ cli      │
    │ trae         │ ByteDance  │ free          │ none       │ cli      │
    │ claude-code  │ Anthropic  │ per_token     │ api_key    │ cli      │
    │ deepseek     │ DeepSeek   │ per_token     │ api_key    │ http     │
    │ kimi         │ Moonshot   │ blackbox      │ api_key    │ http     │
    │ inscode      │ CSDN       │ blackbox      │ none       │ cli      │
    │ codearts     │ Huawei     │ blackbox      │ none       │ cli      │
    │ codex        │ OpenAI     │ per_token     │ api_key    │ cli      │
    │ opencode     │ OpenSource │ free          │ none       │ cli      │
    │ zcode        │ ZCode      │ blackbox      │ none       │ cli      │
    └──────────────┴────────────┴───────────────┴────────────┴──────────┘

**CLI / 终端 Agent（5 个）**::

    aider, gemini-cli, amazon-q-cli, cline-cli, goose

**Desktop IDE / AI 代码编辑器（10 个）**::

    devin-desktop, qoder, catpaw, deepseek-harness, codebuddy,
    marscode, zed, void, melty, pearai

**IDE 扩展 / 插件（10 个，adapter=mcp）**::

    github-copilot, jetbrains-ai, continue, tabnine, sourcegraph-cody,
    refact-ai, cline-vscode, tongyi-lingma, codegeex, baidu-comate

**自主编程 Agent / 平台（5 个）**::

    devin, factory-ai, openhands, swe-agent, devika

**AI App 生成器 / Vibe Coding 平台（4 个，adapter=web）**::

    bolt-new, v0, lovable, replit-agent

降级链设计（主 → 备，按优先级）::

    cursor      → claude-code, codex
    trae        → cursor
    claude-code → codex, deepseek
    deepseek    → kimi
    kimi        → deepseek
    inscode     → cursor
    codearts    → cursor
    codex       → claude-code
    opencode    → cursor
    zcode       → cursor

设计原则：
    - 付费/按 token 的 Agent 优先降级到免费或包月 Agent，控制成本。
    - IDE 内置 AI（cursor/trae）降级到通用 CLI（claude-code/codex），保留能力。
    - HTTP API 类（deepseek/kimi）互相降级，保证在线可用性。
    - 积分/blackbox 类（inscode/zcode/codearts）降级到 cursor，统一兜底。
    - 国产工具优先降级到国产（如 qoder→trae, codebuddy→cursor/trae）。
    - MCP 插件优先降级到对应 CLI 版本（如 cline-vscode→cline-cli）。
    - Web 平台优先降级到 Web 或 CLI（如 devin→devin-desktop, v0→bolt-new）。
    - 所有降级链最终都能到达 cursor 或 opencode 作为兜底。

Usage::

    from maop.core.agent.registry.preset_agents import register_presets
    from maop.core.agent.registry.agent_catalog import AgentCatalog

    catalog = AgentCatalog.default()
    n = register_presets(catalog)  # 注册 44 个预置 Agent，返回新注册数
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
)

logger = logging.getLogger(__name__)


# ── 辅助构造 ──────────────────────────────────────────────────────
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


# ── 预置列表 ──────────────────────────────────────────────────────
def get_preset_agents() -> list[AgentDescriptor]:
    """返回所有预置 Agent 描述符列表.

    每次调用都新建对象（列表与描述符均为全新构造），调用方可安全修改
    返回值而不会影响后续调用的结果。

    顺序固定：先保留原有 10 个（cursor, trae, claude-code, deepseek, kimi,
    inscode, codearts, codex, opencode, zcode），再按类别追加 34 个：
    CLI/终端 5 个、Desktop IDE 10 个、IDE 插件 10 个、自主 Agent 5 个、
    Vibe Coding 4 个。

    Returns
    -------
    list[AgentDescriptor]
        44 个预置 Agent 描述符。
    """
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
        ),

        # ════════════════════════════════════════════════════════════
        # CLI / 终端 Agent（5 个）
        # ════════════════════════════════════════════════════════════

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

        # ════════════════════════════════════════════════════════════
        # Desktop IDE / AI 代码编辑器（10 个）
        # ════════════════════════════════════════════════════════════

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

        # ════════════════════════════════════════════════════════════
        # IDE 扩展 / 插件（10 个，adapter_type="mcp"）
        # ════════════════════════════════════════════════════════════

        # 26. GitHub Copilot — GitHub/Microsoft 订阅制 IDE 插件（OAuth 授权）
        _make_descriptor(
            name="github-copilot",
            display_name="GitHub Copilot",
            vendor="GitHub/Microsoft",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="subscription",
            auth_method="oauth",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor", "codex"],
        ),
        # 27. JetBrains AI Assistant — JetBrains 订阅制 IDE 插件
        _make_descriptor(
            name="jetbrains-ai",
            display_name="JetBrains AI Assistant",
            vendor="JetBrains",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor", "claude-code"],
        ),
        # 28. Continue — 开源免费 IDE 插件（VS Code / JetBrains）
        _make_descriptor(
            name="continue",
            display_name="Continue",
            vendor="OpenSource",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["opencode", "aider"],
        ),
        # 29. Tabnine — 订阅制 IDE 插件（轻量补全 + 对话）
        _make_descriptor(
            name="tabnine",
            display_name="Tabnine",
            vendor="Tabnine",
            adapter_type="mcp",
            capabilities=["code_generation", "code_review", "chat"],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor", "continue"],
        ),
        # 30. Sourcegraph Cody — Sourcegraph 免费 IDE 插件（代码搜索 + 对话）
        # 注：code_search 不在 AgentCapability 枚举中，用 web_search 近似代替
        _make_descriptor(
            name="sourcegraph-cody",
            display_name="Sourcegraph Cody",
            vendor="Sourcegraph",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat", "web_search",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["continue", "opencode"],
        ),
        # 31. Refact.ai — 开源免费 IDE 插件
        _make_descriptor(
            name="refact-ai",
            display_name="Refact.ai",
            vendor="OpenSource",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["continue", "opencode"],
        ),
        # 32. Cline (VS Code) — 开源免费 VS Code 插件（降级到 CLI 版本）
        _make_descriptor(
            name="cline-vscode",
            display_name="Cline (VS Code)",
            vendor="OpenSource",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cline-cli", "opencode"],
        ),
        # 33. 通义灵码 — 阿里巴巴免费 IDE 插件
        _make_descriptor(
            name="tongyi-lingma",
            display_name="通义灵码",
            vendor="阿里巴巴",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["qoder", "cursor"],
        ),
        # 34. CodeGeeX — 智谱AI 免费 IDE 插件
        _make_descriptor(
            name="codegeex",
            display_name="CodeGeeX",
            vendor="智谱AI",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["zcode", "cursor"],
        ),
        # 35. Baidu Comate — 百度免费 IDE 插件
        _make_descriptor(
            name="baidu-comate",
            display_name="Baidu Comate",
            vendor="百度",
            adapter_type="mcp",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=60,
            fallback_agents=["cursor", "continue"],
        ),

        # ════════════════════════════════════════════════════════════
        # 自主编程 Agent / 平台（5 个）
        # ════════════════════════════════════════════════════════════

        # 36. Devin — Cognition AI 订阅制自主编程平台（Web，OAuth，长任务）
        _make_descriptor(
            name="devin",
            display_name="Devin",
            vendor="Cognition AI",
            adapter_type="web",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal", "long_context",
            ],
            billing_model="subscription",
            auth_method="oauth",
            max_concurrent=1,
            timeout_s=600,
            fallback_agents=["devin-desktop", "claude-code"],
        ),
        # 37. Factory.ai — Factory 订阅制自主编程平台（Web，OAuth）
        _make_descriptor(
            name="factory-ai",
            display_name="Factory.ai",
            vendor="Factory",
            adapter_type="web",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="subscription",
            auth_method="oauth",
            max_concurrent=1,
            timeout_s=600,
            fallback_agents=["devin", "cursor"],
        ),
        # 38. OpenHands — 开源免费自主编程 Agent（CLI，长任务）
        _make_descriptor(
            name="openhands",
            display_name="OpenHands",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=600,
            fallback_agents=["opencode", "aider"],
        ),
        # 39. SWE-agent — 开源免费自主编程 Agent（CLI，专注 SWE-bench 类任务）
        _make_descriptor(
            name="swe-agent",
            display_name="SWE-agent",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=600,
            fallback_agents=["openhands", "opencode"],
        ),
        # 40. Devika — 开源免费自主编程 Agent（CLI，长任务）
        _make_descriptor(
            name="devika",
            display_name="Devika",
            vendor="OpenSource",
            adapter_type="cli",
            capabilities=[
                "code_generation", "code_review", "chat", "reasoning",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="free",
            auth_method="none",
            max_concurrent=1,
            timeout_s=600,
            fallback_agents=["openhands", "swe-agent"],
        ),

        # ════════════════════════════════════════════════════════════
        # AI App 生成器 / Vibe Coding 平台（4 个，adapter_type="web"）
        # ════════════════════════════════════════════════════════════

        # 41. Bolt.new — StackBlitz 订阅制 Web 端 AI App 生成器
        _make_descriptor(
            name="bolt-new",
            display_name="Bolt.new",
            vendor="StackBlitz",
            adapter_type="web",
            capabilities=["code_generation", "chat", "tool_use"],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=300,
            fallback_agents=["v0", "lovable"],
        ),
        # 42. v0 — Vercel 订阅制 Web 端 AI UI 生成器（OAuth 授权）
        _make_descriptor(
            name="v0",
            display_name="v0",
            vendor="Vercel",
            adapter_type="web",
            capabilities=["code_generation", "chat", "tool_use"],
            billing_model="subscription",
            auth_method="oauth",
            max_concurrent=1,
            timeout_s=300,
            fallback_agents=["bolt-new", "lovable"],
        ),
        # 43. Lovable — Lovable 订阅制 Web 端 AI App 生成器
        _make_descriptor(
            name="lovable",
            display_name="Lovable",
            vendor="Lovable",
            adapter_type="web",
            capabilities=["code_generation", "chat", "tool_use"],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=300,
            fallback_agents=["bolt-new", "v0"],
        ),
        # 44. Replit Agent — Replit 订阅制 Web 端自主编程平台
        _make_descriptor(
            name="replit-agent",
            display_name="Replit Agent",
            vendor="Replit",
            adapter_type="web",
            capabilities=[
                "code_generation", "code_review", "chat",
                "tool_use", "file_edit", "terminal",
            ],
            billing_model="subscription",
            auth_method="none",
            max_concurrent=1,
            timeout_s=300,
            fallback_agents=["bolt-new", "cursor"],
        ),
    ]


# ── 批量注册 ──────────────────────────────────────────────────────
def register_presets(catalog: AgentCatalog | None = None) -> int:
    """将预置 Agent 注册到 Catalog 中.

    幂等安全：若同名 Agent 已存在，跳过不覆盖。这与 ``AgentCatalog.register``
    的 upsert 语义不同——预置只负责"补齐缺失"，不覆盖用户已自定义的配置，
    避免用户精心调过的 Agent 被预置重置。

    共 44 个预置 Agent：原有 10 个 + CLI 5 个 + Desktop IDE 10 个 +
    IDE 插件 10 个 + 自主 Agent 5 个 + Vibe Coding 4 个。

    Parameters
    ----------
    catalog : AgentCatalog | None
        目标注册中心。``None`` 时使用 ``AgentCatalog.default()``。

    Returns
    -------
    int
        本次新注册的 Agent 数量（已存在的跳过不计）。
    """
    cat = catalog if catalog is not None else AgentCatalog.default()
    presets = get_preset_agents()
    count = 0
    for desc in presets:
        if cat.get(desc.name) is None:
            cat.register(desc)
            count += 1
            logger.info("[preset] registered agent: %s", desc.name)
        else:
            logger.debug("[preset] skipped existing agent: %s", desc.name)
    return count