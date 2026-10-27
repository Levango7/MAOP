# preset_agents: preset Agent descriptor builder + registration.
# Subpackage split of the former preset_agents.py single module.
#
# Structure:
#     _make.py          — _make_descriptor helper + 3 category builders (original/cli/desktop)
#     _data.py          — 3 category builders (plugins/autonomous/vibe)
#     registration.py   — register_presets batch registration
#     __init__.py       — get_preset_agents facade + public re-exports

from __future__ import annotations

import logging

from maop.core.agent.registry.agent_catalog import (
    AgentCatalog,
    AgentDescriptor,
)
from maop.core.agent.registry.preset_agents._data import (
    _autonomous_agents,
    _ide_plugin_agents,
    _vibe_coding_agents,
)
from maop.core.agent.registry.preset_agents._make import (
    _cli_agents,
    _desktop_ide_agents,
    _make_descriptor,
    _original_agents,
)
from maop.core.agent.registry.preset_agents.registration import register_presets

logger: logging.Logger = logging.getLogger("maop.preset_agents")

__all__ = [
    "get_preset_agents",
    "register_presets",
]


def get_preset_agents() -> list[AgentDescriptor]:
    """返回所有预置 Agent 描述符列表.

    每次调用都新建对象（列表与描述符均为全新构造），调用方可安全修改
    返回值而不会影响后续调用的结果。

    顺序固定：先保留原有 10 个（cursor, trae, claude-code, deepseek, kimi,
    inscode, codearts, codex, opencode, zcode），再按类别追加 34 个：
    CLI/终端 5 个、Desktop IDE 10 个、IDE 插件 10 个、自主 Agent 5 个、
    Vibe Coding 4 个。

    Parameters
    ----------
    无.

    Returns
    -------
    list[AgentDescriptor]
        44 个预置 Agent 描述符，顺序与清单一致。
    """
    return (
        _original_agents()
        + _cli_agents()
        + _desktop_ide_agents()
        + _ide_plugin_agents()
        + _autonomous_agents()
        + _vibe_coding_agents()
    )
