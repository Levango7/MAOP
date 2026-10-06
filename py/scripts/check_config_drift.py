"""Config drift audit — ensures os.getenv count doesn't grow.

Runs in CI to prevent new os.getenv/os.environ calls from being added
without going through the settings module. The baseline is the current
count; any increase fails the check.

Also checks version sync: 版本号必须在 **四处** 站点一致 —— ``maop/__init__.py``、
``pyproject.toml``、``py/Dockerfile`` 的 ``LABEL version``、
``dashboard-enterprise/package.json``（能力自根 scripts/doc_reconcile.py
并入，2026-09-29；该文件本身从未被 CI 执行。四处站点清单来自 CHANGELOG.md
发布前 checklist 第 3 条，2026-10-06 补齐此前只比前两处的盲区）。

Usage: python scripts/check_config_drift.py
Exit: 0 = 无漂移；1 = getenv 增长或版本不一致
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

# 2026-08-15: 148→226。8-01 后两周迭代新增的直接 env 读取
# （server.py 31 / sso_store 12 / backends 10 等存量配置读取 + T1 工具白名单
# 有意设计的 MAOP_TOOL_POLICY_* 覆盖接口）。门禁语义：只允许减少，防未来新增。
# 2026-08-17: 226→227。628dd56 的 P2 安全修复（db_backup.py VACUUM INTO 路径
# 白名单）新增 MAOP_BACKUP_DIR 读取 —— 有意配置，非 drift。
# 2026-10-04: 227→228。drivers.py::_agent_env() 把 AgentConfig.env 接到 delegate 链的
# 5 个 driver 上。合并口径 `{**os.environ, **config.env}` 是**既有约定**（cli_adapter.py、
# lifecycle/runtime.py ×2、mcp_hub_transport.py 已是同款 4 处）—— 本次只是让漏掉的这条
# 路对齐，不是新模式。这是**向子进程传环境**的 OS 级操作，不是配置读取，未走 settings
# 是有意为之（subprocess 没有"继承 + 覆盖"的 API，只能自己拼映射）。
# 另外把 `_agent_env` 说明文字里的字面量 os.environ 改成了"进程环境"：计数器是逐行文本
# 匹配，散文里的提法不该占额度（本仓在 test_ci_workflow_hygiene 栽过同形）。
BASELINE = 228


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


# 版本站点清单（仓库根相对路径 → 提取正则）。新增站点只改这里：
# version_drift 与守卫用例 py/tests/test_version_sync_guard.py 都读这一份。
VERSION_SITES: tuple[tuple[str, str], ...] = (
    ("py/maop/__init__.py", r"""__version__\s*=\s*["']([^"']+)["']"""),
    ("py/pyproject.toml", r"""^version\s*=\s*["']([^"']+)["']"""),
    ("py/Dockerfile", r"""^LABEL\s+version=["']?([0-9][^"'\s]*)"""),
    ("dashboard-enterprise/package.json", ""),  # 空正则 = 按 JSON 取 version 字段
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def _site_version(root: pathlib.Path, rel: str, pattern: str) -> str | None:
    path = root / rel
    if not path.is_file():
        return None
    if pattern:
        m = re.search(pattern, _read(path), re.MULTILINE)
        return m.group(1) if m else None
    return _package_json_version(path)


def version_drift(py_dir: pathlib.Path) -> str:
    """比对全部版本站点，返回 "" = 一致。

    站点清单来自 CHANGELOG.md 发布前 checklist 的第 3 条（"版本号在 pyproject.toml /
    __init__.py / Dockerfile / package.json 同步"）。此前本函数只比前两个，
    后两处（``py/Dockerfile`` 的 ``LABEL version``、``dashboard-enterprise/package.json``
    的 ``version``）半抬不会有任何检查发现 —— 发版时会出现"装出来的包和跑起来的镜像
    不是同一个版本"这类难查故障。

    解析不到某站点也返回非空串，避免"门禁没跑"与"门禁全绿"同形。
    """
    root = py_dir.parent
    found: dict[str, str] = {}
    missing: list[str] = []
    for rel, pattern in VERSION_SITES:
        if (value := _site_version(root, rel, pattern)) is None:
            missing.append(rel)
        else:
            found[rel] = value

    if missing:
        return "无法解析版本站点: " + ", ".join(missing)
    distinct = set(found.values())
    if len(distinct) > 1:
        rendered = "  ".join(f"{name}={value}" for name, value in found.items())
        return f"版本漂移（{len(distinct)} 个不同值）: {rendered}"
    return ""


def _package_json_version(path: pathlib.Path) -> str | None:
    try:
        data = json.loads(_read(path))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


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
        print("OK: version sync across all 4 sites (__init__ / pyproject / Dockerfile / package.json).")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())