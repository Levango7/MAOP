#!/usr/bin/env python3
"""文档↔代码自动对账脚本

扫描 `py/maop/core/` 实际文件/子包数量，与 README.md 和 ROADMAP.md 中的描述比对，
发现数字漂移时输出 warning 并 exit code 1。

用法:
    python py/scripts/doc_reconcile.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def count_core_contents() -> tuple[int, int]:
    """统计 py/maop/core/ 下的文件数和子包数（不含 __pycache__）。

    Returns:
        (文件数, 子包数)
    """
    core_path = Path(__file__).resolve().parent.parent / "maop" / "core"
    if not core_path.is_dir():
        print(f"[ERROR] core/ 目录不存在: {core_path}", file=sys.stderr)
        sys.exit(2)

    files = 0
    subpackages = 0
    for entry in core_path.iterdir():
        if entry.name == "__pycache__":
            continue
        if entry.is_file() and entry.name != "__init__.py":
            files += 1
        elif entry.is_dir():
            subpackages += 1
    return files, subpackages


def parse_readme_numbers(readme_path: Path) -> tuple[int, int] | None:
    """从 README.md 提取 core/ 的文件数和子包数。

    Returns:
        (文件数, 子包数) 或 None（如果未找到匹配）
    """
    content = readme_path.read_text(encoding="utf-8")
    # 匹配: `core/` (4 files + 17 subpackages)
    pattern = r"`core/`\s*\(\s*(\d+)\s*files\s*\+\s*(\d+)\s*subpackages\s*\)"
    match = re.search(pattern, content)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def check_roadmap_completed(roadmap_path: Path) -> list[str]:
    """检查 ROADMAP.md 中仍有 [ ] 但实际已完成的规划项。

    Returns:
        发现的问题列表
    """
    content = roadmap_path.read_text(encoding="utf-8")
    issues: list[str] = []

    # 检查 core/ 子包重构项是否仍为待办（排除主题描述行）
    lines = content.splitlines()
    for i, line in enumerate(lines, start=1):
        # 查找包含 "core/ 子包重构" 的行，排除主题描述行（"主题："）
        if ("core/ 子包重构" in line and "✅" not in line
            and "~~" not in line and "主题" not in line):
            issues.append(f"ROADMAP.md 第 {i} 行: 'core/ 子包重构' 仍显示为待办，应标记为已完成")

    return issues


def main() -> int:
    """主入口。

    Returns:
        exit code: 0=全部一致, 1=有漂移, 2=脚本错误
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    readme_path = project_root / "README.md"
    roadmap_path = project_root / "ROADMAP.md"

    if not readme_path.is_file():
        print(f"[ERROR] README.md 不存在: {readme_path}", file=sys.stderr)
        return 2
    if not roadmap_path.is_file():
        print(f"[ERROR] ROADMAP.md 不存在: {roadmap_path}", file=sys.stderr)
        return 2

    # 1. 统计实际文件/子包数
    actual_files, actual_subpkgs = count_core_contents()
    print(f"[INFO] core/ 实际: {actual_files} files + {actual_subpkgs} subpackages")

    # 2. 读取 README.md 中的数字
    readme_numbers = parse_readme_numbers(readme_path)
    if readme_numbers is None:
        print("[WARN] README.md 中未找到 core/ 文件数描述", file=sys.stderr)
        print("       期望格式: `core/` (X files + Y subpackages)", file=sys.stderr)
        return 1
    readme_files, readme_subpkgs = readme_numbers
    print(f"[INFO] README.md: {readme_files} files + {readme_subpkgs} subpackages")

    # 3. 比对
    files_ok = actual_files == readme_files
    subpkgs_ok = actual_subpkgs == readme_subpkgs

    if files_ok and subpkgs_ok:
        print(f"[OK] core/ {actual_files} files + {actual_subpkgs} subpackages (README: {readme_files} files + {readme_subpkgs} subpackages)")
    else:
        if not files_ok:
            print(f"[WARN] core/ {actual_files} files (README: {readme_files} files)", file=sys.stderr)
        if not subpkgs_ok:
            print(f"[WARN] core/ {actual_subpkgs} subpackages (README: {readme_subpkgs} subpackages)", file=sys.stderr)
        return 1

    # 4. 检查 ROADMAP.md 过时规划项
    roadmap_issues = check_roadmap_completed(roadmap_path)
    if roadmap_issues:
        for issue in roadmap_issues:
            print(f"[WARN] {issue}", file=sys.stderr)
        return 1
    else:
        print("[OK] ROADMAP.md 已完成的规划项已正确标记")

    return 0


if __name__ == "__main__":
    sys.exit(main())