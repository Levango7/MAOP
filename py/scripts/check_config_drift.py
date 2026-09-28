"""Config drift audit — ensures os.getenv count doesn't grow.

Runs in CI to prevent new os.getenv/os.environ calls from being added
without going through the settings module. The baseline is the current
count; any increase fails the check.

Also checks version sync: ``maop/__init__.py`` 的 ``__version__`` 必须与
``pyproject.toml`` 的 ``version`` 一致（能力自根 scripts/doc_reconcile.py
并入，2026-09-29；该文件本身从未被 CI 执行）。

Usage: python scripts/check_config_drift.py
Exit: 0 = 无漂移；1 = getenv 增长或版本不一致
"""

from __future__ import annotations

import pathlib
import re
import sys

# 2026-08-15: 148→226。8-01 后两周迭代新增的直接 env 读取
# （server.py 31 / sso_store 12 / backends 10 等存量配置读取 + T1 工具白名单
# 有意设计的 MAOP_TOOL_POLICY_* 覆盖接口）。门禁语义：只允许减少，防未来新增。
# 2026-08-17: 226→227。628dd56 的 P2 安全修复（db_backup.py VACUUM INTO 路径
# 白名单）新增 MAOP_BACKUP_DIR 读取 —— 有意配置，非 drift。
BASELINE = 227


def count_getenv_calls(root: pathlib.Path) -> int:
    """Count os.getenv/os.environ calls in non-test Python files."""
    count = 0
    for f in root.rglob("*.py"):
        if "test" in f.name or "test" in str(f.parent):
            continue
        if f.name == "settings.py":
            continue  # settings.py is the canonical config entry point
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "os.getenv" in line or "os.environ" in line:
                    count += 1
        except Exception:
            pass
    return count


def version_drift(py_dir: pathlib.Path) -> str:
    """比对 __init__.py 的 __version__ 与 pyproject.toml 的 version。

    返回 "" = 一致；否则返回描述问题的一行文本（含无法解析的显式告警，
    避免"没跑"与"全绿"同形）。
    """
    init_file = py_dir / "maop" / "__init__.py"
    pyproject = py_dir / "pyproject.toml"
    m_init = re.search(
        r"""__version__\s*=\s*["']([^"']+)["']""",
        init_file.read_text(encoding="utf-8", errors="ignore"),
    )
    m_toml = re.search(
        r"""^version\s*=\s*["']([^"']+)["']""",
        pyproject.read_text(encoding="utf-8", errors="ignore"),
        re.MULTILINE,
    )
    if not m_init or not m_toml:
        return "无法解析 __version__（maop/__init__.py）或 version（pyproject.toml）"
    if m_init.group(1) != m_toml.group(1):
        return (
            f"版本漂移: maop/__init__.py={m_init.group(1)} "
            f"≠ pyproject.toml={m_toml.group(1)}"
        )
    return ""


def main() -> int:
    py_dir = pathlib.Path(__file__).resolve().parent.parent
    root = py_dir / "maop"
    current = count_getenv_calls(root)
    print(f"os.getenv/os.environ calls (excluding tests + settings): {current}")
    print(f"Baseline: {BASELINE}")
    failed = False
    if current > BASELINE:
        print(f"FAIL: +{current - BASELINE} new os.getenv calls detected.")
        print("Use maop.config.settings.get_settings() instead of os.getenv().")
        failed = True
    elif current < BASELINE:
        print(f"GOOD: -{BASELINE - current} calls removed since baseline. Update BASELINE.")
    else:
        print("OK: no drift.")

    drift = version_drift(py_dir)
    if drift:
        print(f"FAIL: {drift}")
        failed = True
    else:
        print("OK: version sync (__init__.py == pyproject.toml).")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())