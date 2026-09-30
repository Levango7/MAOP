#!/usr/bin/env python3
"""required checks 漂移守卫：仓库清单 ↔ GitHub 分支保护实况对账。

为什么需要它（`docs/ci-gates.md` §7.6）：`test_ci_required_checks.py` 只核对
"清单 ↔ ci.yml 作业形状 ↔ 文档散文"三方，**三方都在仓库里**。GitHub 侧那份
required 上下文是带外配置（Settings 里点的），任何仓库内改动都看不见它。2026-10-01
实测抓到的正是这种漂移：清单与文档都写 4 条（含 `CI merge gate`），GitHub 上却还是
PR #53 时代的 5 条（`Lint` + `Frontend Build` 单列、**没有** `CI merge gate`）——
于是聚合作业建好了却从未生效，9 平台 pytest / e2e / 迁移 / 审计全无平台级强制。
仓库侧守卫对此**结构性地盲**：三份文件互相一致，漂移在第四处。

本脚本补上第四处。判定分三态，**关键在于"查不到"不等于"通过"**：

    exit 0  MATCH       实况与清单逐条相等
    exit 1  DRIFT       实况与清单不一致（列出多出/缺失的具体条目）
    exit 3  UNVERIFIED  读不到实况（无 token / 权限不足 / 网络失败）

`UNVERIFIED` 刻意与 `MATCH` 分开：默认 `GITHUB_TOKEN` 没有 admin 作用域，读分支保护
会 403。若把这种情况判成通过，本脚本就成了仓库最鄙视的那种"永远绿的门禁"——
比没有门禁更坏，因为它会让人以为门禁在。调用方应当把 3 当作"守卫当前没在守卫"处理。

用法（本地，已 `gh auth login`）::

    python scripts/check_required_checks_drift.py
    python scripts/check_required_checks_drift.py --repo Levango7/MAOP

CI 里跑请显式接受"验不到"这一态（否则默认 token 一律 403，作业恒红）::

    python scripts/check_required_checks_drift.py --allow-unverified
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / ".github" / "ci-required-checks.json"
API = "https://api.github.com/repos/{repo}/branches/{branch}/protection"

EXIT_MATCH = 0
EXIT_DRIFT = 1
EXIT_UNVERIFIED = 3


def manifest_contexts(path: Path = MANIFEST) -> list[str]:
    """读权威清单里的 required 上下文名（保持文件内顺序，便于逐条对照）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [entry["name"] for entry in data["contexts"]]


def manifest_branch(path: Path = MANIFEST) -> str:
    # json.loads 的返回是 Any，而本仓 mypy 开了 warn_return_any —— 显式收窄，
    # 否则新脚本会把 Lint 作业判红（这正是 §7.6 那类"本该拦住却没拦住"的同类风险）。
    branch = json.loads(path.read_text(encoding="utf-8")).get("branch", "master")
    return branch if isinstance(branch, str) else "master"


def fetch_live(repo: str, branch: str, token: str | None, timeout: int = 20) -> list[str]:
    """读 GitHub 分支保护实况。

    读不到就抛异常 —— 由调用方归入 UNVERIFIED，**绝不返回空列表冒充"没有 required"**
    （空列表与"确实没配 required"在 API 层面无法区分，混淆它会让守卫反向失效）。
    """
    if not token:
        raise RuntimeError("没有可用 token（设 GITHUB_TOKEN 或 --token）")
    req = urllib.request.Request(
        API.format(repo=repo, branch=branch),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "maop-required-checks-drift-guard",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    rsc = payload.get("required_status_checks")
    if rsc is None:
        # 保护存在但没开 required —— 这是"确知的不一致"，不是读不到。
        return []
    return list(rsc.get("contexts") or [])


def compare(expected: list[str], actual: list[str]) -> list[str]:
    """返回人类可读的不一致描述；空列表表示一致（顺序不参与判定）。"""
    problems: list[str] = []
    exp_set, act_set = set(expected), set(actual)
    for name in sorted(act_set - exp_set):
        problems.append(f"GitHub 上多出、清单里没有：{name!r}")
    for name in sorted(exp_set - act_set):
        problems.append(f"清单要求、GitHub 上没有：{name!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="required checks 漂移守卫")
    ap.add_argument("--repo", default="Levango7/MAOP", help="owner/name")
    ap.add_argument("--branch", default=None, help="默认取清单里的 branch 字段")
    ap.add_argument("--token", default=None, help="默认取 $GITHUB_TOKEN")
    ap.add_argument(
        "--allow-unverified",
        action="store_true",
        help="读不到实况时判 0 而非 3（仅供 CI 使用；本地不建议）",
    )
    args = ap.parse_args(argv)

    try:
        expected = manifest_contexts()
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"::error title=required checks drift::读清单失败：{exc}")
        return EXIT_DRIFT

    branch = args.branch or manifest_branch()
    token = args.token or os.environ.get("GITHUB_TOKEN")

    try:
        actual = fetch_live(args.repo, branch, token)
    except urllib.error.HTTPError as exc:
        reason = f"HTTP {exc.code}（默认 GITHUB_TOKEN 通常无 admin 作用域，会是 403）"
        return _unverified(f"{args.repo}@{branch} 分支保护读取失败：{reason}", args.allow_unverified)
    except (urllib.error.URLError, RuntimeError, TimeoutError, OSError) as exc:
        return _unverified(f"{args.repo}@{branch} 分支保护读取失败：{exc}", args.allow_unverified)

    problems = compare(expected, actual)
    print(f"required checks 漂移守卫：{args.repo}@{branch}")
    print(f"  清单（{len(expected)} 条）：{expected}")
    print(f"  实况（{len(actual)} 条）：{actual}")
    if problems:
        for p in problems:
            print(f"::error title=required checks drift::{p}")
        print(f"FAIL: {len(problems)} 处漂移 —— 按 docs/ci-gates.md §7.4 同步 GitHub 侧")
        return EXIT_DRIFT
    print("OK: GitHub 分支保护与仓库清单逐条一致")
    return EXIT_MATCH


def _unverified(detail: str, allow: bool) -> int:
    print(f"::warning title=required checks drift::{detail}")
    print("UNVERIFIED: 本次没能读到实况，**不等于一致**。")
    print("  守卫当前没有在守卫 —— 需要 admin 作用域的 token 才能真正对账。")
    if allow:
        return EXIT_MATCH
    return EXIT_UNVERIFIED


if __name__ == "__main__":
    # 非 UTF-8 控制台（en-US runner 的 cp1252）下中文摘要会抛 UnicodeEncodeError，
    # 把判定崩成非零 —— 本仓在 Windows 腿上栽过一次（见 ci_merge_gate.py 同款处理）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())