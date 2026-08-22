#!/usr/bin/env python3
"""Document reconciliation script.

Checks that README.md API examples reference endpoints that actually exist
in the codebase. Run by CI lint job (ci.yml:66).

Exit code 0 = all checks pass, 1 = discrepancies found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _extract_api_endpoints_from_readme() -> list[str]:
    """Extract /api/... paths from README.md curl examples."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return []
    text = readme.read_text(encoding="utf-8")
    return re.findall(r'(?:curl\s+\S+\s+)?http[s]?://\S+(/api/[^\s`"\']+)', text)


def _check_endpoint_exists(endpoint: str) -> bool:
    """Check if an endpoint path is referenced in the routers directory."""
    routers_dir = ROOT / "py" / "maop" / "dashboard" / "routers"
    if not routers_dir.exists():
        return True
    for py_file in routers_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Skip files that cannot be read (permission/encoding issues).
            pass
        else:
            if endpoint in content:
                return True
    return False


def main() -> int:
    """Run document reconciliation checks."""
    issues: list[str] = []

    endpoints = _extract_api_endpoints_from_readme()
    for ep in endpoints:
        if not _check_endpoint_exists(ep):
            issues.append(f"README references {ep} but no matching route found in routers/")

    init_file = ROOT / "py" / "maop" / "__init__.py"
    if init_file.exists():
        content = init_file.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            version = match.group(1)
            pyproject = ROOT / "py" / "pyproject.toml"
            if pyproject.exists():
                pyproject_text = pyproject.read_text(encoding="utf-8")
                if f'version = "{version}"' not in pyproject_text and f"version = '{version}'" not in pyproject_text:
                    issues.append(f"Version mismatch: __init__.py={version} but pyproject.toml may differ")

    if issues:
        print("Document reconciliation issues found:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    else:
        print("Document reconciliation: all checks passed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())