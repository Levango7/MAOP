"""DEPRECATED: re-export shim, will be removed in v5.0.0.

P0-2 (2026-08-07): ``SubagentManager`` 已合并到 ``subagent_lifecycle.py`` 的
``SubAgentManager`` 中（``SubagentManager`` 现为 ``SubAgentManager`` 的别名）。
本模块将 ``sys.modules`` 重定向到 ``subagent_lifecycle``，使旧调用方
``from maop.core.agent.delegation.subagent_delegation import SubagentManager``
和 ``mock.patch('maop.core.agent.delegation.subagent_delegation.xxx')``
继续工作。

请使用 ``from maop.core.agent.delegation.subagent_lifecycle import SubAgentManager``
或 ``from maop.core.agent.delegation.subagent_lifecycle import SubagentManager``
（别名）。
"""
from __future__ import annotations

# mypy: ignore-errors
import sys
from typing import TYPE_CHECKING

from maop.core.agent.delegation import subagent_lifecycle as _real_mod

# 将本 shim 模块在 sys.modules 中替换为真正模块对象，
# 使 mock.patch('maop.core.agent.delegation.subagent_delegation.xxx') 能 patch 真正模块
sys.modules[__name__] = _real_mod

if TYPE_CHECKING:
    from maop.core.agent.delegation.subagent_lifecycle import *
