"""MAOP Billing — QuotaBucket（额度分桶）+ BillingEngine（计费抽象引擎）.

核心概念：
  - **三桶模型**：每个 Agent 的额度分为免费 / 预付 / 后付三个桶，
    消耗顺序：免费 → 预付 → 后付。
  - **BillingEngine**：根据 Agent 的 ``BillingModel`` 计算消耗单位与成本，
    调用 ``QuotaBucket`` 扣减额度，与 ``CostTracker`` 集成。

公共导出：
  - ``QuotaBucket``    — 额度分桶管理器
  - ``QuotaEntry``     — 单个 Agent 的三桶额度配置（Pydantic）
  - ``ConsumeResult``  — 单次消耗结果（Pydantic）
  - ``BillingEngine``  — 计费抽象引擎
  - ``BillingResult``  — 计费结果（Pydantic）
  - ``BillingRecord``  — 单次计费记录（Pydantic）

Usage::

    from maop.core.agent.billing import (
        QuotaBucket, QuotaEntry, BillingEngine, BillingResult, BillingRecord,
    )

    bucket = QuotaBucket()
    bucket.set_quota("claude-code", QuotaEntry(free_quota=1000, prepaid_quota=5000))
    result = bucket.consume("claude-code", 1500)
"""

from __future__ import annotations

from maop.core.agent.billing.billing_abstraction import (
    BillingEngine,
    BillingRecord,
    BillingResult,
)
from maop.core.agent.billing.quota_bucket import (
    ConsumeResult,
    QuotaBucket,
    QuotaEntry,
)

__all__ = [
    "BillingEngine",
    "BillingRecord",
    "BillingResult",
    "ConsumeResult",
    "QuotaBucket",
    "QuotaEntry",
]