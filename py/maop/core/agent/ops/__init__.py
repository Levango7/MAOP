"""MAOP Ops — 运维监控模块（健康度评分 + SLA 管理）.

.. warning::
   **本包未接入主流程**（2026-09-21 核实）。

   全仓库**无任何生产代码 import 本包**（既非显式 import，也不在惰性映射 /
   入口点 / 配置中）；仅由测试文件直接引用。7 个模块共约 3,199 行：

   ``capability_probe`` / ``health_scorer`` / ``result_cache`` /
   ``sandbox_executor`` / ``sla_manager`` / ``tenant_isolation`` /
   ``usage_statistics``

   逐个类核对（HealthScorer / SLAManager / CapabilityProbe / ResultCache /
   SandboxExecutor / UsageStatistics / TenantIsolation）**生产引用均为 0 处**，
   而测试引用各有 18–44 处 —— 即"测试充分、无调用方"。

   来源：``423b969``（企业级Agent调度平台10个模块 — 厂商生态+运维+执行安全）
   一次性创建，此后从未被接线；CHANGELOG 亦未记载本包。

   .. note::
      ``tenant_isolation`` 与 :mod:`maop.core.tenant` 是**两套并存的租户实现**，
      而 dashboard 生产路径用的是第三套——企业版
      ``maop.enterprise.tenant.TenantManager``。三者关系：
      ``maop.core.tenant`` 是个人版实现（SQLite + RLS/quota/audit，生产引用 0 处，
      仅测试使用），本包 ``tenant_isolation`` 只在测试中，企业版才是 dashboard
      真正的数据源（见 ``core/tenant/manager.py`` 的「权威归属」段与
      ``tests/test_tenant_authority.py``）。不要因为"有代码 + 有测试"就推断该
      能力已交付。


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