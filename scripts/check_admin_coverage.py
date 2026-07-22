#!/usr/bin/env python3
"""CI anti-regression check: verify all write-endpoint routers use require_admin.

Scans dashboard router modules for POST/PUT/DELETE/PATCH endpoints
and ensures each calls require_admin(request).  Exits non-zero if
any write endpoint is missing the guard.

Usage:
    python scripts/check_admin_coverage.py
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