# preset_agents: batch registration.
# Split from preset_agents.py into preset_agents/registration.py.

from __future__ import annotations

import logging

from maop.core.agent.registry.agent_catalog import AgentCatalog

logger: logging.Logger = logging.getLogger("maop.preset_agents")

__all__ = ["register_presets"]


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
    # 延迟导入避免循环依赖：registration → __init__ → registration
    from maop.core.agent.registry.preset_agents import get_preset_agents

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
