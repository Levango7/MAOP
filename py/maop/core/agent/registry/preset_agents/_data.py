# preset_agents: remaining category builders (plugins / autonomous / vibe).
# Split from preset_agents.py into preset_agents/_data.py.

from __future__ import annotations

from maop.core.agent.registry.agent_catalog import AgentDescriptor
from maop.core.agent.registry.preset_agents._make import _make_descriptor

__all__ = [
    "_autonomous_agents",
    "_ide_plugin_agents",
    "_vibe_coding_agents",
]


def _ide_plugin_agents() -> list[AgentDescriptor]:
    """IDE 扩展 / 插件（10 个，adapter_type="mcp"）."""
    return [
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
            region="domestic",  # 国产：阿里巴巴
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
            region="domestic",  # 国产：智谱AI
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
            region="domestic",  # 国产：百度
        ),
    ]


def _autonomous_agents() -> list[AgentDescriptor]:
    """自主编程 Agent / 平台（5 个）."""
    return [
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
    ]


def _vibe_coding_agents() -> list[AgentDescriptor]:
    """AI App 生成器 / Vibe Coding 平台（4 个，adapter_type="web"）."""
    return [
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
