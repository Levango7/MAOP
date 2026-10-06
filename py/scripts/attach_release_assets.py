#!/usr/bin/env python3
"""把构建产物挂到 GitHub Release —— 不需要任何外部账号的分发路径。

为什么需要它：`Publish to PyPI` 虽然触发面已打通，但真发布还需要 PyPI 账号 +
trusted publisher 配置（外部动作）。仓库维护者当下的处境是"没有那个账号、也不便注册"，
于是发布链看起来又是"配了但用不了"。GitHub Release 附件没有这个前置：
runner 用自带的 `GITHUB_TOKEN` 就能传文件，使用者可以
`pip install <release-asset-url>`，仓库内也能给出稳定链接。

刻意只用 GitHub 自带能力（`gh` CLI + `contents: write`），不引入第三方 action。

用法：
    python scripts/attach_release_assets.py --tag v5.2.1 --dist-dir dist
    python scripts/attach_release_assets.py --tag v5.2.1 --dist-dir dist --dry-run

退出：0 = 附件已就位并回读校验通过；1 = 产物缺失 / gh 失败 / 回读校验不过。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys


def expected_files(dist_dir: pathlib.Path) -> list[pathlib.Path]:
    """本轮要上传的产物：wheel + sdist，且必须非空。"""
    files = sorted(p for p in dist_dir.glob("*") if p.suffix in {".whl", ".gz", ".zip"} and p.is_file())
    return [p for p in files if p.stat().st_size > 0]


def build_command(*, tag: str, files: list[pathlib.Path], release_exists: bool) -> list[str]:
    """已存在的 release 用 upload --clobber；不存在则按 tag 创建。"""
    paths = [str(f) for f in files]
    if release_exists:
        return ["gh", "release", "upload", tag, *paths, "--clobber"]
    return [
        "gh", "release", "create", tag, *paths,
        "--verify-tag",
        "--title", f"MAOP {tag}",
        "--notes", f"MAOP {tag} 发行物（wheel + sdist）。变更内容见仓库 CHANGELOG.md 对应版本段。",
    ]


def release_exists(tag: str, repo: str) -> bool:
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def uploaded_asset_names(tag: str, repo: str) -> list[str]:
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [a["name"] for a in data.get("assets", []) if isinstance(a, dict) and a.get("name")]


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="attach built distributions to a GitHub Release")
    ap.add_argument("--tag", required=True, help="release tag, e.g. v5.2.1")
    ap.add_argument("--dist-dir", default="dist", help="directory containing wheel/sdist")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name; defaults to $GITHUB_REPOSITORY")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不改动任何东西")
    args = ap.parse_args(argv)

    if not args.repo:
        print("::error title=Release assets::缺 --repo（也没有 GITHUB_REPOSITORY）")
        return 1
    files = expected_files(pathlib.Path(args.dist_dir))
    if not files:
        # 产物为空是"构建成功但什么都没产出"的同形失效，必须显式拦下
        print(f"::error title=Release assets::{args.dist_dir} 里没有非空产物，拒绝创建空 release")
        return 1
    if not any(p.suffix == ".whl" for p in files):
        print(f"::error title=Release assets::{args.dist_dir} 里缺 wheel，只有 {[p.name for p in files]}")
        return 1

    exists = False if args.dry_run else release_exists(args.tag, args.repo)
    cmd = build_command(tag=args.tag, files=files, release_exists=exists)
    print(f"目标仓库: {args.repo} | release 已存在: {exists}")
    print("产物: " + ", ".join(f"{p.name} ({p.stat().st_size} B)" for p in files))
    print("执行: " + " ".join(cmd))
    if args.dry_run:
        print("dry-run：未做任何改动")
        return 0

    run = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if run.returncode != 0:
        print(f"::error title=Release assets::gh 失败（exit {run.returncode}）：{run.stderr.strip()[:600]}")
        return 1

    # 回读校验：命令成功不等于附件真挂上了（同名文件、大小写、clobber 语义都可能吃掉它）
    names = uploaded_asset_names(args.tag, args.repo)
    missing = [p.name for p in files if p.name not in names]
    if missing:
        print(f"::error title=Release assets::上传后回读缺少附件: {missing}（release 上实有 {names}）")
        return 1
    print(f"OK: {args.tag} 已挂载 {len(names)} 个附件: {names}")
    for p in files:
        print(f"  下载地址: https://github.com/{args.repo}/releases/download/{args.tag}/{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
