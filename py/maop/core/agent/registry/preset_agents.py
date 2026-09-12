"""MAOP 预置 Agent 配置 — 常见桌面 AI 工具开箱即用.

定义 10 个主流桌面 AI 编码工具的默认 ``AgentDescriptor``，并提供批量注册函数
``register_presets``，让用户无需逐个手写元数据即可快速接入。

预置清单（按 vendor 归类）::

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

Usage::

    from maop.core.agent.registry.preset_agents import register_presets
    from maop.core.agent.registry.agent_catalog import AgentCatalog

    catalog = AgentCatalog.default()
    n = register_presets(catalog)  # 注册 10 个预置 Agent，返回新注册数
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

    顺序固定：cursor, trae, claude-code, deepseek, kimi, inscode,
    codearts, codex, opencode, zcode。

    Returns
    -------
    list[AgentDescriptor]
        10 个预置 Agent 描述符。
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
    ]


# ── 批量注册 ──────────────────────────────────────────────────────
def register_presets(catalog: AgentCatalog | None = None) -> int:
    """将预置 Agent 注册到 Catalog 中.

    幂等安全：若同名 Agent 已存在，跳过不覆盖。这与 ``AgentCatalog.register``
    的 upsert 语义不同——预置只负责"补齐缺失"，不覆盖用户已自定义的配置，
    避免用户精心调过的 Agent 被预置重置。

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