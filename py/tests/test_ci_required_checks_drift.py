"""required checks 漂移守卫：判定逻辑单测 + 清单形状约束。

为什么要有这个守卫（`docs/ci-gates.md` §7.6）：`test_ci_required_checks.py` 核对的
三方（清单 / ci.yml / 文档散文）**全都在仓库里**，而 GitHub 分支保护是带外配置。
2026-10-01 实测的漂移正是这个结构：三方一致地写着 4 条含 `CI merge gate`，
GitHub 上还是 PR #53 时代的 5 条（`Lint` + `Frontend Build` 单列、无 merge gate），
聚合作业建好却从未生效。仓库内守卫对此结构性盲 —— 本文件补第四处。

三条判定各自都要钉住，尤其第三条：
- 一致 → 0；
- 不一致 → 1 并**点名**多出/缺失的具体条目（只说"不一致"等于没省调查时间）；
- **读不到 → 3，不许 0**。默认 GITHUB_TOKEN 无 admin 作用域必然 403，
  若把"验不到"判成通过，本脚本就变成仓库最鄙视的"永远绿的门禁"，比没有更坏。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "check_required_checks_drift.py"
MANIFEST = REPO / ".github" / "ci-required-checks.json"

sys.path.insert(0, str(SCRIPT.parent))
import check_required_checks_drift as drift

# 2026-10-01 修复前的实况：清单说 4 条含 gate，GitHub 上是这 5 条。
DRIFTED_LIVE = [
    "CI scope (code vs docs-only)",
    "Secret Scan (gitleaks)",
    "Docs consistency gate",
    "Lint (ruff + mypy)",
    "Frontend Build",
]
EXPECTED_NOW = [
    "CI scope (code vs docs-only)",
    "Secret Scan (gitleaks)",
    "Docs consistency gate",
    "CI merge gate",
]


# ── 判定面：compare ───────────────────────────────────────────────────


def test_identical_sets_compare_equal_regardless_of_order() -> None:
    assert drift.compare(EXPECTED_NOW, list(reversed(EXPECTED_NOW))) == []


def test_the_real_2026_10_01_drift_is_detected() -> None:
    """把当时的真实漂移钉成用例 —— 这是本守卫存在的理由，不能被"顺手改掉"。"""
    problems = drift.compare(EXPECTED_NOW, DRIFTED_LIVE)
    assert len(problems) == 3, problems
    joined = "\n".join(problems)
    # 多出来的两条（单列的 lint/前端）
    assert "Lint (ruff + mypy)" in joined
    assert "Frontend Build" in joined
    # 缺掉的那条（聚合作业 —— 它缺席正是"测试无平台级强制"的根因）
    assert "CI merge gate" in joined
    assert "清单要求、GitHub 上没有" in joined


def test_problems_name_the_direction_of_each_mismatch() -> None:
    """只报"不一致"而不说清哪边多/哪边少，等于把调查成本原样退回给人。"""
    extra = drift.compare(["A"], ["A", "B"])
    assert len(extra) == 1 and "多出" in extra[0] and "'B'" in extra[0]
    missing = drift.compare(["A", "B"], ["A"])
    assert len(missing) == 1 and "没有" in missing[0] and "'B'" in missing[0]


def test_empty_live_is_drift_not_a_pass() -> None:
    """实况为空（保护存在但没开 required）必须判不一致，不能当成"清单为空所以一致"。"""
    problems = drift.compare(EXPECTED_NOW, [])
    assert len(problems) == len(EXPECTED_NOW)
    assert all("清单要求、GitHub 上没有" in p for p in problems)


# ── 清单读取 ──────────────────────────────────────────────────────────


def test_manifest_is_readable_and_branch_defaults_to_master() -> None:
    assert drift.manifest_contexts() == EXPECTED_NOW
    assert drift.manifest_branch() == "master"


def test_manifest_actually_lists_the_gate() -> None:
    """清单必须含聚合作业 —— 否则 required 只剩三条恒跑的，覆盖面反而缩小。"""
    assert "CI merge gate" in drift.manifest_contexts()


# ── "验不到 ≠ 通过"（本守卫最容易做错的地方）─────────────────────────


def test_missing_token_is_unverified_not_match() -> None:
    assert drift.main(["--token", "", "--repo", "Levango7/MAOP"]) == drift.EXIT_UNVERIFIED


def test_allow_unverified_is_opt_in_only() -> None:
    """默认绝不放行；只有显式 --allow-unverified 才返回 0（供 CI 用）。"""
    assert drift.main(["--token", ""]) == drift.EXIT_UNVERIFIED
    assert drift.main(["--token", "", "--allow-unverified"]) == drift.EXIT_MATCH


def test_exit_codes_are_distinct_and_nonzero_for_unverified() -> None:
    assert drift.EXIT_MATCH == 0
    assert drift.EXIT_DRIFT == 1
    assert drift.EXIT_UNVERIFIED == 3, "UNVERIFIED 必须与 MATCH 的 0 区分开"


def test_fetch_without_token_raises_rather_than_returning_empty() -> None:
    """绝不能"读不到就返回空列表" —— 空列表与"确实没配 required"无法区分。"""
    with pytest.raises(RuntimeError):
        drift.fetch_live("Levango7/MAOP", "master", None)


# ── CLI 面 ────────────────────────────────────────────────────────────


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,  # 非零退出码本身就是被断言的对象，不能让 subprocess 抛
    )


def test_cli_unverified_prints_warning_and_exits_3() -> None:
    proc = _run("--token", "")
    assert proc.returncode == drift.EXIT_UNVERIFIED, proc.stderr
    assert "UNVERIFIED" in proc.stdout
    assert "不等于一致" in proc.stdout, "必须明说'验不到'不等于'一致'"


def test_cli_survives_non_utf8_console() -> None:
    """本仓在 Windows 腿上栽过 UnicodeEncodeError 把判定崩成红的那次。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--token", ""],
        capture_output=True,
        env={"PYTHONIOENCODING": "cp1252", "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,  # 同上：退出码本身是被断言对象
    )
    # 要么正常判 3，要么因环境缺解释器而失败，但绝不能是 UnicodeEncodeError 栈
    assert "UnicodeEncodeError" not in proc.stderr.decode("utf-8", "replace")


def test_manifest_path_constant_points_at_the_tracked_file() -> None:
    assert MANIFEST.is_file()
    assert drift.MANIFEST == MANIFEST
    json.loads(MANIFEST.read_text(encoding="utf-8"))  # 必须是合法 JSON
