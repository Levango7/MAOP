"""shebang 存在 ⇒ git 索引模式必须是 100755（ruff EXE001 的跨平台等价物）。

为什么要有这条：ruff 的 `EXE001`（*Shebang is present but file is not executable*）
**在 Windows 上不报告** —— 可执行位是 POSIX 概念，Windows 文件系统没有这个位，
ruff 直接跳过该规则。于是"本机 ruff 全绿"与"CI Linux 上 11 条红"可以同时成立：
2026-11-17 把 `scripts/` 纳入 lint 范围时正是这样炸的（本地 0 错 / CI 11 错），
而在此之前仓库里 **15 个带 shebang 的 Python 文件无一可执行**，四个月没人发现。

本用例只读 `git ls-files -s` 记录的模式位：不依赖平台，也不依赖 ruff 是否启用
EXE001（该规则随 ruff 版本与平台的可见性都在变，不能当判据）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 只扫这些后缀：仓库内带 shebang 的产物都是 Python / shell 脚本。
_SCANNED_SUFFIXES = (".py", ".sh", ".bash")


def shebang_mode_problems(entries: list[tuple[str, str, str]]) -> list[str]:
    """entries = [(path, mode, first_line)]；返回"有 shebang 但不可执行"的路径。

    抽成纯函数是为了能反向变异：喂合成数据证明判据不是恒绿。
    """
    problems: list[str] = []
    for path, mode, first_line in entries:
        if first_line.startswith("#!") and mode != "100755":
            problems.append(f"{path}（mode {mode}）")
    return problems


def git_index_entries() -> list[tuple[str, str, str]]:
    """从 git 索引读出 (path, mode, 首行)。"""
    try:
        raw = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - 环境缺失
        pytest.skip(f"git 不可用，跳过模式守卫：{exc}")

    entries: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        meta, _, path = line.partition("\t")
        if not path or not path.endswith(_SCANNED_SUFFIXES):
            continue
        mode = meta.split()[0]
        try:
            with (REPO_ROOT / path).open("rb") as fh:
                first_line = fh.readline().decode("utf-8", "replace").strip()
        except OSError:  # pragma: no cover - 索引里有、工作树没有（删除未提交）
            continue
        entries.append((path, mode, first_line))
    return entries


def test_shebang_files_are_executable_in_git_index():
    entries = git_index_entries()
    assert entries, "一个文件都没扫到——判据失效，不要当作通过"
    problems = shebang_mode_problems(entries)
    assert not problems, (
        "以下文件有 shebang 但 git 索引不是 100755（POSIX 上不可直接执行，"
        "且 ruff 的 EXE001 只会在 Linux/macOS 上把它报成红）：\n  "
        + "\n  ".join(problems)
        + "\n修复：git update-index --chmod=+x <path>"
    )


# ── 反向变异：证明判据不是恒绿 ──────────────────────────────────────

def test_missing_exec_bit_is_rejected():
    entries = [
        ("scripts/ok.py", "100755", "#!/usr/bin/env python3"),
        ("scripts/bad.py", "100644", "#!/usr/bin/env python3"),
    ]
    assert shebang_mode_problems(entries) == ["scripts/bad.py（mode 100644）"]


def test_no_shebang_is_not_flagged():
    # 没有 shebang 的文件不需要可执行位——否则会误伤普通模块
    entries = [("maop/core/thing.py", "100644", '"""module docstring"""')]
    assert shebang_mode_problems(entries) == []
