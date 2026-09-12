"""MAOP Ops — 运维监控模块（健康度评分 + SLA 管理）.

公共导出：
  - ``HealthScorer``  — Agent 健康度评分器
  - ``HealthScore``   — 单个 Agent 的健康评分（Pydantic）
  - ``SLAManager``    — SLA 监控与告警管理器
  - ``SLADefinition`` — SLA 定义（Pydantic）
  - ``SLAStatus``     — SLA 检查结果（Pydantic）

Usage::

    from maop.core.agent.ops import HealthScorer, SLAManager, SLADefinition

    scorer = HealthScorer()
    scorer.record_result("agent-a", success=True, response_time_s=0.5)
    score = scorer.get_score("agent-a")

    sla_mgr = SLAManager(health_scorer=scorer)
    sla_mgr.define_sla("agent-a", SLADefinition(agent_name="agent-a"))
    status = sla_mgr.check_sla("agent-a")
"""

from __future__ import annotations

from maop.core.agent.ops.health_scorer import (
    HealthScore,
    HealthScorer,
)
from maop.core.agent.ops.sla_manager import (
    SLADefinition,
    SLAManager,
    SLAStatus,
)

__all__ = [
    "HealthScore",
    "HealthScorer",
    "SLADefinition",
    "SLAManager",
    "SLAStatus",
]