#!/usr/bin/env python3
"""个人版安装冒烟：没装 MAOS（`maop.enterprise`）时，dashboard 路由仍必须可导入。

为什么需要这条门禁
------------------
MAOS（企业版）是私有包，与 MAOP 分开发布。个人版用户只装
``maop-orchestrator`` 时，``import maop.enterprise`` 必然失败。
``maop/dashboard/_register_routes/_routers.py`` 对每个 router 都是
``try/except ImportError`` + ``logger.warning`` —— 这是有意的容错设计，
但也意味着"某个 router 偷偷依赖了企业包"在运行期只留下一行 warning：
CI 全绿、启动成功，个人版对应功能却静默 404。本脚本把这种退化变成一条
可失败的断言（见 ADR-017 双仓库物理隔离）。

规则
----
1. 在导入任何 ``maop`` 模块之前屏蔽 ``maop.enterprise*``，精确模拟"只装了
   个人版"的进程。
2. 逐个导入 ``maop.dashboard.routers`` 下的每个模块。
3. 导入失败的模块必须出现在 ``EXPECTED_ENTERPRISE_ONLY`` 白名单中；
   白名单之外的失败 = 个人版被企业依赖污染 → 非零退出。
4. 白名单内的模块若在个人版也能导入 = 白名单已过期（依赖已被移除或改成
   惰性导入）→ 非零退出。否则白名单会逐年腐蚀成"什么都豁免"，门禁失效。
5. 额外断言 ``maop.dashboard._register_routes``（注册层）本身可导入。

退出码：0 = 通过；1 = 有违约；2 = 环境问题（maop 不在当前解释器里）。
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

#: 个人版下允许 ImportError 的 router 模块（企业版专属）。
#: 每一项都在 `maop/dashboard/_register_routes/_routers.py` 里有对应的
#: FeatureFlag / edition gate；这里的名单只是"个人版不可导入"的现状快照。
EXPECTED_ENTERPRISE_ONLY: frozenset[str] = frozenset({
    "licenses",             # FeatureFlag.LICENSE_MANAGEMENT
    "n8n",                  # FeatureFlag.N8N_INTEGRATION
    "notification_schemas", # 模块级 import maop.enterprise.notification.models
    "notifications",        # 同上，且 notification_service 惰性依赖企业包
    "relay_platform",       # 间接经 integration_service → n8n
    "sso",                  # FeatureFlag.SSO
})

_ROUTER_PACKAGE = "maop.dashboard.routers"


class _EnterpriseImportBlocker:
    """让 ``maop.enterprise`` 表现得像没安装（只影响本进程）。"""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == "maop.enterprise" or fullname.startswith("maop.enterprise."):
            raise ImportError(
                f"blocked by personal-install smoke: {fullname} "
                "(simulating a MAOP-only install without MAOS)"
            )
        # 非企业包：返回 None 表示"本 finder 不处理"，交给后续 finder。


def _install_blocker() -> None:
    # 防御：如果调用方（或 sitecustomize）已经导入过企业包，先清掉再装拦截器。
    for name in list(sys.modules):
        if name == "maop.enterprise" or name.startswith("maop.enterprise."):
            del sys.modules[name]
    sys.meta_path.insert(0, _EnterpriseImportBlocker())


def _router_modules() -> list[str]:
    """返回 routers 包下所有模块名（不含包自身）。"""
    pkg = importlib.import_module(_ROUTER_PACKAGE)
    pkg_dir = Path(pkg.__file__).parent
    names = []
    for path in sorted(pkg_dir.glob("*.py")):
        stem = path.stem
        if stem == "__init__":
            continue
        names.append(stem)
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每个模块的导入结果")
    args = ap.parse_args(argv)

    _install_blocker()

    try:
        import maop  # noqa: F401 - 证明 maop 本身仍可导入
    except ImportError as exc:
        print(f"FAIL: 当前解释器里 import maop 失败：{exc}", file=sys.stderr)
        return 2

    try:
        importlib.import_module("maop.dashboard._register_routes")
    except ImportError as exc:
        print(f"FAIL: 个人版注册层不可导入：{exc}", file=sys.stderr)
        return 1

    modules = _router_modules()
    failed: dict[str, str] = {}
    imported: list[str] = []
    for name in modules:
        try:
            importlib.import_module(f"{_ROUTER_PACKAGE}.{name}")
            imported.append(name)
            if args.verbose:
                print(f"  ok    {name}")
        except ImportError as exc:
            failed[name] = str(exc)
            if args.verbose:
                print(f"  fail  {name}: {exc}")

    unexpected = sorted(set(failed) - EXPECTED_ENTERPRISE_ONLY)
    # 白名单里"其实能导入"的模块（说明企业依赖已被移除，名单该更新）
    stale = sorted(EXPECTED_ENTERPRISE_ONLY & set(imported))

    print(
        f"routers 共 {len(modules)} 个：导入成功 {len(imported)}，"
        f"企业版专属失败 {len(failed) - len(unexpected)}，意外失败 {len(unexpected)}"
    )
    if failed:
        print("预期内（企业版专属，个人版不挂载）：")
        for name in sorted(set(failed) & EXPECTED_ENTERPRISE_ONLY):
            print(f"  - {name}: {failed[name]}")

    exit_code = 0
    if unexpected:
        print("\nFAIL: 以下模块在个人版安装下不可导入，且不在企业版白名单里 ——", file=sys.stderr)
        for name in unexpected:
            print(f"  - {name}: {failed[name]}", file=sys.stderr)
        print(
            "  修复方向：把企业依赖改成惰性导入 / try-except 降级，或在 _routers.py "
            "里加 edition gate；若确属企业版功能，请同时更新本脚本白名单与前端路由守卫。",
            file=sys.stderr,
        )
        exit_code = 1

    if stale:
        print("\nFAIL: 白名单里有模块其实可以在个人版导入（名单已过期）——", file=sys.stderr)
        for name in stale:
            print(f"  - {name}", file=sys.stderr)
        print(
            "  请从 scripts/check_personal_imports.py 的 EXPECTED_ENTERPRISE_ONLY 中移除。",
            file=sys.stderr,
        )
        exit_code = 1

    if exit_code == 0:
        print("OK: 个人版（无 maop.enterprise）路由导入面通过")
    return exit_code


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
