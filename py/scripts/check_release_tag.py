#!/usr/bin/env python3
"""发布前完整性检查：tag 的版本号必须与包内版本号一致。

为什么需要它：`publish` 作业此前不可达（`on.push` 只声明 branches，tag push 被过滤掉），
所以"打了 tag 但发的不是这份代码"这类事故从未被任何检查看过。tag 触发一打通，
这个风险就变成真的 —— 必须先有一道明确命名的关卡，而不是让
`pypa/gh-action-pypi-publish` 抛一句和版本无关的 OIDC 错误。

用法：``python scripts/check_release_tag.py --ref refs/tags/v5.2.1``
退出：0 = 一致；1 = 版本号不一致 / tag 形状不对 / 读不到包版本。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# PyPI 侧还没配置时，上传会失败。把这句话打在成功路径上，
# 免得第一次真发布时从 403 反推前置条件。
TRUSTED_PUBLISHER_HINT = (
    "上传走 OIDC（无 token secret）。若下一步 pypa/gh-action-pypi-publish 报 "
    "401/403 或 'no trusted publisher'，说明 PyPI 项目 "
    "`maop-orchestrator` 尚未把本仓库配成 trusted publisher"
    "（Owner=Levango7、Repository=MAOP、Workflow=ci.yml、PyPI 项目名=maop-orchestrator）。"
    "该配置需要 PyPI 账号，属外部动作，不能由 CI 完成。"
)


def version_from_ref(ref: str) -> str | None:
    """refs/tags/v5.2.1 → '5.2.1'；不是 vX.Y.Z 形状则返回 None。"""
    m = re.fullmatch(r"refs/tags/v(\d+\.\d+\.\d+[\w.]*)", ref.strip())
    return m.group(1) if m else None


def package_version(py_dir: pathlib.Path) -> str | None:
    init = py_dir / "maop" / "__init__.py"
    if not init.is_file():
        # 门禁要"清楚地说为什么不通过"，不是抛一个 traceback ——
        # 后者在 Actions 日志里和"检查没跑"难以区分。
        return None
    m = re.search(
        r"""__version__\s*=\s*["']([^"']+)["']""",
        init.read_text(encoding="utf-8-sig", errors="ignore"),
    )
    return m.group(1) if m else None


def check(ref: str, py_dir: pathlib.Path) -> tuple[bool, str]:
    tag_version = version_from_ref(ref)
    if tag_version is None:
        return False, f"ref 不是 vX.Y.Z 形状的发布 tag: {ref!r}"
    pkg_version = package_version(py_dir)
    if pkg_version is None:
        return False, f"读不到 __version__（{py_dir / 'maop' / '__init__.py'}）"
    if tag_version != pkg_version:
        return False, (
            f"tag 版本 {tag_version} ≠ 包内版本 {pkg_version}。"
            "先按 CHANGELOG 发布 checklist 同步四处版本号再打 tag，"
            "否则会发出一个与 tag 名称不符的发行物。"
        )
    return True, f"tag 版本 {tag_version} == __version__ {pkg_version}"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="verify a release tag matches the package version")
    ap.add_argument("--ref", required=True, help="git ref, e.g. refs/tags/v5.2.1")
    args = ap.parse_args(argv)

    py_dir = pathlib.Path(__file__).resolve().parent.parent
    ok, message = check(args.ref, py_dir)
    if not ok:
        print(f"::error title=Release integrity::{message}")
        return 1
    print(f"OK: {message}")
    print(f"::notice title=PyPI trusted publisher::{TRUSTED_PUBLISHER_HINT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
