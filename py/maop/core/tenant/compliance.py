"""MAOP Compliance — GDPR/CCPA data deletion, export, DPA, and processing records.

G-04+G-07 security fix: implements real cross-data source cascading
deletion and export for user data. Previously, ``delete_user_data`` and
``export_user_data`` were stubs that only logged. Now they reach into
all MAOP data stores:

  * **Agents** — agent configurations owned by the user.
  * **Memory** — short-term and long-term memory entries.
  * **Sessions** — conversation / execution sessions.
  * **Audit logs** — audit entries (optionally retained for compliance).
  * **RBAC grants** — role grants for the user.

Cascade deletion order (to avoid orphaned references):
  1. Sessions (reference agents + memory)
  2. Memory entries (reference agents)
  3. Agent configurations
  4. RBAC grants
  5. Audit entries (optional — retained by default for compliance)

G-07 fix: the ``tenant_id`` is taken from the JWT-authenticated request
state (``request.state.tenant_id``), never from the request body. This
prevents cross-tenant data access via forged body parameters.

GDPR 增强（Phase 4）
--------------------
* **数据主体权利**（Data Subject Rights, Articles 15-22）：
  - 知情权 / 访问请求（Right of Access, Art. 15）：``access_request``
  - 删除权 / 被遗忘权（Right to Erasure, Art. 17）：``right_to_erasure``
  - 数据可携权（Data Portability, Art. 20）：``data_portability``
  - 请求追踪：``DataSubjectRequest`` 记录每个请求的状态与处理结果。
* **数据处理协议**（Data Processing Agreement, Art. 28）：
  - ``ProcessingAgreement`` 模型 + ``register_dpa`` / ``list_dpas``。
* **数据处理记录**（Records of Processing Activities, Art. 30）：
  - ``ProcessingRecord`` 模型 + ``record_processing_activity`` /
    ``list_processing_records``。

模块拆分（P0-2 refactor）
-------------------------
本文件原为 1050 行单体模块，现拆分为：
* :mod:`maop.core.tenant.compliance_manager` — ``ComplianceManager`` +
  ``DeletionReport`` / ``ExportReport`` 模型（级联删除 / 导出）。
* :mod:`maop.core.tenant.gdpr_manager` — ``GDPRComplianceManager`` +
  GDPR Articles 15-22 / 28 / 30 模型与常量。

本文件仅做 re-export 以保持向后兼容：
``from maop.core.tenant.compliance import ComplianceManager`` 等导入
路径继续工作，无需调用方修改。
"""

from __future__ import annotations

# Re-export for backward compatibility — all public symbols remain
# importable from ``maop.core.tenant.compliance``.  The noqa F401 marker
# on each import suppresses ruff's unused-import warning for re-exports.
from maop.core.tenant.compliance_manager import (  # noqa: F401
    ComplianceManager,
    DeletionReport,
    ExportReport,
)
from maop.core.tenant.gdpr_manager import (  # noqa: F401
    DataPortabilityReport,
    DataSubjectRequest,
    GDPRComplianceManager,
    ProcessingAgreement,
    ProcessingRecord,
)
