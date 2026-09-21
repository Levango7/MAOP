"""厂商生态管理层.

.. warning::
   **本包未接入主流程**（2026-09-21 核实）。

   全仓库**无任何生产代码 import 本包**（既非显式 import，也不在惰性映射 /
   入口点 / 配置中）；仅由测试文件直接引用。三个模块共约 1,200 行：
   ``vendor_ecosystem`` / ``vendor_sso`` / ``vendor_billing``。

   来源：``423b969``（企业级Agent调度平台10个模块 — 厂商生态+运维+执行安全）
   一次性创建，此后从未被接线；CHANGELOG 亦未记载本包。


提供厂商→Agent 映射、厂商内协作建议、厂商统一认证（SSO）与厂商统一计费
能力。三个模块相互独立但可组合使用：

    ecosystem = VendorEcosystem(db_path=...)
    sso = VendorSSO(ecosystem, db_path=...)
    billing = VendorBilling(ecosystem, db_path=...)

公共 Pydantic 模型与异常从子模块 re-export，便于上层调用方统一导入。
"""

from __future__ import annotations

from maop.core.agent.vendor.vendor_billing import (
    AgentUsage,
    BudgetStatus,
    VendorBilling,
    VendorBillingSummary,
)
from maop.core.agent.vendor.vendor_ecosystem import (
    Vendor,
    VendorEcosystem,
    get_preset_vendors,
)
from maop.core.agent.vendor.vendor_sso import (
    VendorSession,
    VendorSSO,
)

__all__ = [
    # 厂商统一计费
    "AgentUsage",
    "BudgetStatus",
    # 厂商生态
    "Vendor",
    "VendorBilling",
    "VendorBillingSummary",
    "VendorEcosystem",
    "VendorSSO",
    # 厂商统一认证
    "VendorSession",
    "get_preset_vendors",
]