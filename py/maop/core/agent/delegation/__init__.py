"""Subagent lifecycle, delegation, db, A2A, human proxy, agent proxy subpackage.

P0-2 (2026-08-07): subagent_delegation.py 已合并到 subagent_lifecycle.py。
``SubagentManager`` 现为 ``SubAgentManager`` 的别名（定义在 subagent_lifecycle.py
末尾）。subagent_delegation.py 保留为重定向 shim 以向后兼容旧调用方和
``mock.patch`` 目标。

推荐导入方式::

    from maop.core.agent.delegation.subagent_lifecycle import (
        SubAgentManager, SubagentManager, AgentConfig, SubagentInfo,
    )
"""
