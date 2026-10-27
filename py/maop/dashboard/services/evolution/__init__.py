"""Evolution 子包 — 从 ``evolution_service`` 拆分（file-size ratchet）.

包含：

- ``loop`` — F2-01 Evolution Loop AC-07 端点（闭环状态/触发/审批/AB/回滚）

子模块的 public 名称由父 ``evolution_service`` 转发 import，
``__all__`` 不变，调用方和测试无需改动。
"""

from __future__ import annotations

from maop.dashboard.services.evolution.loop import (
    decide_evolution_approval,
    get_evolution_ab_results,
    get_evolution_loop_status,
    list_evolution_approvals,
    rollback_evolution_loop,
    trigger_evolution_loop,
)

__all__ = [
    "decide_evolution_approval",
    "get_evolution_ab_results",
    "get_evolution_loop_status",
    "list_evolution_approvals",
    "rollback_evolution_loop",
    "trigger_evolution_loop",
]
