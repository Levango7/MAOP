"""个人版测试盲区的**单一事实来源**：哪些测试文件真的必须在企业包下才能收集。

背景（ADR-017 双仓物理隔离）：`maop.enterprise` 由独立私有包（MAOS）以命名空间
方式注入。个人版环境（只装 `maop-orchestrator`）里，**模块顶层** `import
maop.enterprise` 的测试文件在收集阶段就会 ImportError，必须整体跳过；其余测试
应当被收集并显式 skip。

旧实现的判据是「文件正文里出现 `maop.enterprise` / `maop-enterprise` 子串」——
于是只在注释或 docstring 里提到企业包的用例（`test_backends.py`、
`test_router_system.py`、`test_notifications_ws_auth.py` …）也被一起静默忽略，
个人版 CI 长期少跑一整批双版可跑的用例，且报告里看不出任何痕迹。

现在改为：清单在**本模块声明**，`tests/conftest.py` 只按清单设置 collect_ignore；
`tests/test_enterprise_test_surface.py` 用 AST 做双向校验：

- 漏登记：真实存在模块顶层企业导入、但不在清单里 → 判红；
- 过期条目：清单里有、但文件已无模块顶层企业导入（或文件已删除）→ 判红。

于是「个人版哪些用例没跑」是一个可核对的数字，而不是一份无声的忽略。
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent

# 模块顶层 import 了 maop.enterprise 的文件：个人版无法收集。
ENTERPRISE_MODULE_IMPORT_TESTS: tuple[str, ...] = (
    "test_audit_enhanced.py",
    "test_enterprise_audit.py",
    "test_enterprise_container.py",
    "test_enterprise_ha.py",
    "test_enterprise_license.py",
    "test_enterprise_n8n.py",
    "test_enterprise_rbac.py",
    "test_enterprise_tenant.py",
    "test_enterprise_tls_auto.py",
    "test_license_crl.py",
    "test_licenses.py",
    "test_module_integrity.py",
    "test_notifications.py",
    "test_quota_audit.py",
    "test_sso.py",
    "test_sso_providers.py",
    "test_tenant_quota.py",
)

# 间接依赖：自身不 import 企业包，但经 fixture / 被导入模块链式引入。
# 例：test_relay_platform.py → routers.relay_platform → services.integration_service
#     → maop.enterprise.n8n（模块级导入）。
# 注：生产侧已有守卫（_register_relay_platform_router 用 try/except 降级为
# warning），故这**不是**产品缺陷，只是这些测试需要企业包。
# 若哪天该链路改成惰性导入，本文件（连同 conftest 的 collect_ignore 条目）应一起删。
ENTERPRISE_INDIRECT: tuple[str, ...] = ("test_relay_platform.py",)

ENTERPRISE_ONLY_TEST_FILES: tuple[str, ...] = (
    ENTERPRISE_MODULE_IMPORT_TESTS + ENTERPRISE_INDIRECT
)


def _module_scope_enterprise_imports(tree: ast.Module) -> list[str]:
    """模块作用域（含顶层 try/if 块体内）出现的 `maop.enterprise*` 导入目标。

    只看模块作用域：函数体内的惰性导入（`def test_x(): from maop.enterprise...`）
    不会阻止收集，这类文件应当自行守卫（importorskip / skipif），不应进清单。
    """
    found: list[str] = []

    def _walk(stmts: list[ast.stmt]) -> None:
        for node in stmts:
            if isinstance(node, ast.Import):
                found.extend(
                    a.name for a in node.names if a.name.startswith("maop.enterprise")
                )
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("maop.enterprise"):
                    found.append(node.module)
            elif isinstance(node, (ast.If, ast.Try)):
                _walk(node.body)
                _walk(node.orelse)
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        _walk(handler.body)
                    _walk(node.finalbody)

    _walk(tree.body)
    return found


def module_scope_importers() -> dict[str, list[str]]:
    """扫描 tests/ 下所有 test_*.py，返回「文件 → 模块顶层企业导入目标」。"""
    out: dict[str, list[str]] = {}
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        # utf-8-sig：个别文件带 BOM，ast.parse 会对 U+FEFF 报 SyntaxError。
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        hits = _module_scope_enterprise_imports(tree)
        if hits:
            out[path.relative_to(TESTS_ROOT).as_posix()] = hits
    return out
