#!/usr/bin/env python3
"""写端点 admin 守护盘点工具（人工审计用，未接入 CI）。

扫描 dashboard 各 router 的 POST/PUT/DELETE/PATCH 端点，报告**函数体内**
没有调用 require_admin 的那一些。产出的每一条都需人工分类后处理。

## 当前发现的分类（2026-09-29 复核，18 条 ≈ 15 个唯一端点）

- 用户级端点（设计如此，非缺陷）：notifications 已读/偏好（走
  ``_require_identity``）、feedback 提交、auth_refresh、sso logout。
- 外部回调（由对方 token 认证，不走 admin 角色）：alertmanager / n8n
  webhook、sso saml_acs。
- **待决策**：relay_platform 的 3 个写端点无任何鉴权（其同级模块
  model_gateway / hooks 均有 require_admin），已作为发现上报，未擅自修改。

## 已知盲区（结论需人工确认）

- 只识别函数体内的 require_admin 调用；router 级 ``dependencies=[...]``
  守护看不见。
- 只匹配 require_admin / _require_admin 两个名字，自定义守护看不见。
- ``PUBLIC_ENDPOINTS`` 为硬编码清单，新增公共端点会先以"违规"形式出现。

若未来要接入 CI 做回归门禁，需先为上述已分类端点引入豁免表
（参考 py/scripts/check_api_contract.py 的 KNOWN_MISSING 模式），
否则会常红。

Usage:
    python scripts/check_admin_coverage.py
Exit: 0 = 无发现；1 = 有需人工分类的写端点
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER_DIR = ROOT / "py" / "maop" / "dashboard" / "routers"

WRITE_METHODS = {"post", "put", "delete", "patch"}
PUBLIC_ENDPOINTS = {"auth_login", "auth_logout", "auth_register"}


def _has_require_admin(func_node: ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            func = getattr(node, "func", None)
            if isinstance(func, ast.Name) and func.id in ("require_admin", "_require_admin"):
                return True
            if isinstance(func, ast.Attribute) and func.attr in ("require_admin", "_require_admin"):
                return True
    return False


def check_router(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = getattr(deco, "func", None)
            if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
                if node.name not in PUBLIC_ENDPOINTS and not _has_require_admin(node):
                    line = node.lineno
                    violations.append(f"{path.name}:{line} {node.name}() — missing require_admin")
                break  # 一个函数挂多个写方法装饰器（如 post("") + post("/")）只报一次
    return violations


def main() -> int:
    if not ROUTER_DIR.exists():
        print(f"Router directory not found: {ROUTER_DIR}")
        return 1

    all_violations = []
    for py_file in sorted(ROUTER_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        all_violations.extend(check_router(py_file))

    if all_violations:
        print("❌ Write endpoints missing require_admin:")
        for v in all_violations:
            print(f"  {v}")
        print(f"\n{len(all_violations)} violation(s) found")
        return 1

    print("✅ All write endpoints have require_admin guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())