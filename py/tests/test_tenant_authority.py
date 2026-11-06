"""租户实现的权威归属（阶段三 c）。

背景：``maop.core.tenant.TenantManager``（个人版：SQLite + RLS/quota/audit）
与 ``maop.enterprise.tenant.TenantManager``（企业版：PG 持久化 + FeatureFlag
门禁）同名同职责，各自持有独立的租户字典。dashboard 若混用，API 写进去的
租户在 UI 里看不到（反之亦然）。

本文件把"dashboard 侧权威实现 = 企业版"这条边界钉住，防止有人顺手把个人版
接进 dashboard。它只读源码、不导入企业包，因此个人版环境也能跑。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

MAOP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "maop"
TENANT_SERVICE = MAOP_ROOT / "dashboard" / "services" / "tenant_service.py"

#: dashboard/worker/delegate/control 是"运行面"——它们只能依赖权威实现。
_RUNTIME_DIRS = ("dashboard", "worker", "delegate", "control")


def _imported_modules(path: pathlib.Path) -> set[str]:
    """模块里所有 import 的绝对模块名（相对导入忽略）。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_tenant_service_uses_enterprise_manager_only():
    mods = _imported_modules(TENANT_SERVICE)
    assert "maop.enterprise.tenant" in mods, (
        "dashboard 的租户服务必须用企业版实现（maop.enterprise.tenant）"
    )
    personal = {m for m in mods if m == "maop.core.tenant" or m.startswith("maop.core.tenant.")}
    assert not personal, (
        f"dashboard 租户服务不得导入个人版租户模块，发现：{sorted(personal)}"
    )


@pytest.mark.parametrize("area", _RUNTIME_DIRS)
def test_runtime_surface_never_imports_personal_tenant_manager(area: str):
    """运行面任何模块都不得导入个人版的租户**注册表**。

    注意范围：``maop.core.tenant.context`` / ``gdpr_manager`` / ``compliance``
    属于个人版正常功能（租户上下文、GDPR、合规记录），dashboard 可以导入。
    被禁止的只有注册表本身——``maop.core.tenant``（包根，导出 TenantManager）
    与 ``maop.core.tenant.manager``。
    """
    offenders: list[str] = []
    for path in sorted((MAOP_ROOT / area).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mods = _imported_modules(path)
        if {"maop.core.tenant", "maop.core.tenant.manager"} & mods:
            offenders.append(str(path.relative_to(MAOP_ROOT)))
    assert not offenders, (
        "运行面只允许企业版租户注册表；这些文件导入了个人版：\n  " + "\n  ".join(offenders)
    )


def test_personal_manager_documents_authority():
    """个人版 manager 的 docstring 必须写明它不是 dashboard 的数据源。"""
    source = (MAOP_ROOT / "core" / "tenant" / "manager.py").read_text(encoding="utf-8-sig")
    doc = ast.get_docstring(ast.parse(source)) or ""
    assert "权威归属" in doc
    assert "maop.enterprise.tenant" in doc
