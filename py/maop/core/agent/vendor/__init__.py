"""厂商生态管理层.

提供厂商→Agent 映射、厂商内协作建议、厂商统一认证（SSO）与厂商统一计费
能力。三个模块相互独立但可组合使用：

    ecosystem = VendorEcosystem(db_path=...)
    sso = VendorSSO(ecosystem, db_path=...)
    billing = VendorBilling(ecosystem, db_path=...)

公共 Pydantic 模型与异常从子模块 re-export，便于上层调用方统一导入。
"""

from __future__ import annotations

from maop.core.agent.vendor.vendor_ecosystem import (
    Vendor,
    VendorEcosystem,
    get_preset_vendors,
)
from maop.core.agent.vendor.vendor_sso import (
    VendorSession,
    VendorSSO,
)
from maop.core.agent.vendor.vendor_billing import (
    AgentUsage,
    BudgetStatus,
    VendorBilling,
    VendorBillingSummary,
)

__all__ = [
    # 厂商生态
    "Vendor",
    "VendorEcosystem",
    "get_preset_vendors",
    # 厂商统一认证
    "VendorSession",
    "VendorSSO",
    # 厂商统一计费
    "AgentUsage",
    "BudgetStatus",
    "VendorBilling",
    "VendorBillingSummary",
]