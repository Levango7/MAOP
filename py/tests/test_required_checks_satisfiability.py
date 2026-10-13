"""required checks 可满足性金丝雀：判定逻辑单测 + ci.yml 接线结构约束。

为什么要有这个守卫（`docs/ci-gates.md` §7.7）：仓库内三方对账（`test_ci_required_checks.py`）
看不见 GitHub 带外配置；nightly 的 `check_required_checks_drift.py` 能看见但需要 admin PAT。
本金丝雀用默认 `GITHUB_TOKEN` 读 PR 的可合并性实况，补上"PR 那一刻平台在拦什么"这一路。
它的判定最容易做错的地方是**把"读不到 / 说不清"当成通过**，所以下面把每条结论都钉死。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "check_required_checks_satisfiability.py"
MANIFEST = REPO / ".github" / "ci-required-checks.json"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
DOC = REPO / "docs" / "ci-gates.md"

sys.path.insert(0, str(SCRIPT.parent))
import check_required_checks_satisfiability as canary

MANIFEST_NAMES = [
    "CI scope (code vs docs-only)",
    "Secret Scan (gitleaks)",
    "Docs consistency gate",
    "CI merge gate",
]
CANARY_JOB_ID = "required-checks-canary"


def _all_green() -> dict[str, str]:
    return {name: "SUCCESS" for name in MANIFEST_NAMES}


# ── 判定面：evaluate ──────────────────────────────────────────────────


def test_all_green_and_clean_is_match() -> None:
    verdict, reasons = canary.evaluate(MANIFEST_NAMES, _all_green(), "CLEAN", False)
    assert verdict == "MATCH" and reasons == []


def test_all_green_and_unstable_is_match() -> None:
    """UNSTABLE = 非 required 的 check 在跑/失败，不构成漂移。"""
    verdict, _ = canary.evaluate(MANIFEST_NAMES, _all_green(), "UNSTABLE", False)
    assert verdict == "MATCH"


def test_green_but_blocked_is_drift_extra_required() -> None:
    """清单全绿却被拦 —— 这正是"平台要求的比清单多"，是本金丝雀的核心目标。"""
    verdict, reasons = canary.evaluate(MANIFEST_NAMES, _all_green(), "BLOCKED", False)
    assert verdict == "DRIFT"
    assert any("BLOCKED" in r or "未列出" in r for r in reasons)


def test_absent_context_with_blocked_is_drift_never_reported() -> None:
    """清单里的 required 从未上报（改名/删除）→ 平台当"永不满足"，所有 PR 永久 blocked。"""
    observed = _all_green()
    del observed["CI merge gate"]
    verdict, reasons = canary.evaluate(MANIFEST_NAMES, observed, "BLOCKED", False)
    assert verdict == "DRIFT"
    assert any("CI merge gate" in r for r in reasons), "必须点名缺席的那条上下文"


def test_absent_context_but_not_blocked_is_unverified_not_pass() -> None:
    """缺席却又能合 —— 自相矛盾，不许当通过。"""
    observed = _all_green()
    del observed["Secret Scan (gitleaks)"]
    verdict, _ = canary.evaluate(MANIFEST_NAMES, observed, "CLEAN", False)
    assert verdict == "UNVERIFIED"


def test_unsatisfied_manifest_context_is_not_applicable() -> None:
    """清单内某条自己没过 → PR 本来就被它挡着，金丝雀无从区分内外，不表态（也不冒充绿）。"""
    observed = _all_green()
    observed["CI merge gate"] = "FAILURE"
    verdict, reasons = canary.evaluate(MANIFEST_NAMES, observed, "BLOCKED", False)
    assert verdict == "NOT-APPLICABLE"
    assert any("CI merge gate" in r for r in reasons)


def test_pending_manifest_context_is_not_applicable() -> None:
    observed = _all_green()
    observed["Docs consistency gate"] = "PENDING"
    verdict, _ = canary.evaluate(MANIFEST_NAMES, observed, "BLOCKED", False)
    assert verdict == "NOT-APPLICABLE"


def test_draft_pr_is_not_applicable() -> None:
    verdict, _ = canary.evaluate(MANIFEST_NAMES, _all_green(), "DRAFT", True)
    assert verdict == "NOT-APPLICABLE"


def test_unknown_merge_state_is_unverified_not_pass() -> None:
    verdict, _ = canary.evaluate(MANIFEST_NAMES, _all_green(), "UNKNOWN", False)
    assert verdict == "UNVERIFIED"


def test_skipped_counts_as_satisfied() -> None:
    """§7.1 实测：skipped 满足 required（docs-only 时被 `if:` 跳过的重活）。"""
    observed = {name: "SKIPPED" for name in MANIFEST_NAMES}
    verdict, _ = canary.evaluate(MANIFEST_NAMES, observed, "CLEAN", False)
    assert verdict == "MATCH"


# ── 汇总解析 ─────────────────────────────────────────────────────────


def test_normalize_rollup_handles_checkrun_and_statuscontext() -> None:
    pr = {
        "statusCheckRollup": {
            "contexts": {
                "nodes": [
                    {
                        "__typename": "CheckRun",
                        "name": "CI merge gate",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    },
                    {
                        "__typename": "CheckRun",
                        "name": "Lint (ruff + mypy)",
                        "status": "IN_PROGRESS",
                        "conclusion": None,
                    },
                    {"__typename": "StatusContext", "context": "Old Status", "state": "SUCCESS"},
                    None,
                ]
            }
        }
    }
    observed = canary.normalize_rollup(pr)
    assert observed["CI merge gate"] == "SUCCESS"
    assert observed["Lint (ruff + mypy)"] == "PENDING"  # 未完成折成 PENDING，不是"通过"
    assert observed["Old Status"] == "SUCCESS"


def test_normalize_rollup_empty_is_empty() -> None:
    assert canary.normalize_rollup({}) == {}


# ── "验不到 ≠ 通过" ──────────────────────────────────────────────────


def test_exit_codes_are_distinct() -> None:
    assert canary.EXIT_MATCH == 0
    assert canary.EXIT_DRIFT == 1
    assert canary.EXIT_UNVERIFIED == 3, "UNVERIFIED 必须与 MATCH 的 0 区分开"


def test_missing_token_is_unverified_not_match() -> None:
    assert (
        canary.main(["--token", "", "--pr", "1", "--repo", "Levango7/MAOP"])
        == canary.EXIT_UNVERIFIED
    )


def test_no_pr_number_is_graceful_skip() -> None:
    """非 pull_request 事件（如 push）不该把金丝雀判红 —— 它没东西可查。"""
    assert canary.main(["--token", "", "--repo", "Levango7/MAOP"]) == canary.EXIT_MATCH


def test_fetch_without_token_raises() -> None:
    with pytest.raises(RuntimeError):
        canary.fetch_pr("Levango7/MAOP", 1, None)


# ── CLI 面 ───────────────────────────────────────────────────────────


def _run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env["PR_NUMBER"] = ""
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,  # 退出码本身是被断言对象
        env=env,
    )


def test_cli_unverified_exits_3_and_says_not_equal() -> None:
    proc = _run("--token", "", "--pr", "1")
    assert proc.returncode == canary.EXIT_UNVERIFIED, proc.stderr
    assert "UNVERIFIED" in proc.stdout
    assert "不等于" in proc.stdout, "必须明说'验不到'不等于'通过'"


def test_cli_survives_non_utf8_console() -> None:
    """本仓在 Windows 腿上栽过 UnicodeEncodeError 把判定崩成红的那次。"""
    import os

    env = {"PYTHONIOENCODING": "cp1252", "PATH": os.environ.get("PATH", ""), "PR_NUMBER": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--token", "", "--pr", "1"],
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert "UnicodeEncodeError" not in proc.stderr.decode("utf-8", "replace")


# ── ci.yml 接线 / 清单 / 文档 三方一致 ───────────────────────────────


def _ci_jobs() -> dict:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8")).get("jobs") or {}


def _manifest_entries() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["contexts"]


def _as_list(value) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def test_canary_job_exists_in_ci() -> None:
    assert CANARY_JOB_ID in _ci_jobs(), "金丝雀作业必须接进 ci.yml"


def test_canary_needs_cover_every_manifest_job() -> None:
    """金丝雀靠 needs 保证清单里的 required 都已上报才判定 —— 少一个就会误判"缺席"。"""
    required_jobs = {e["job"] for e in _manifest_entries()}
    needs = set(_as_list(_ci_jobs()[CANARY_JOB_ID].get("needs")))
    missing = required_jobs - needs
    assert not missing, (
        f"金丝雀的 needs 没覆盖这些清单作业：{sorted(missing)}。"
        f"不被 needs 覆盖时，作业可能还没上报就被判'缺席 → DRIFT'（假红）。"
    )


def test_canary_runs_with_always_and_is_pr_only() -> None:
    cond = _ci_jobs()[CANARY_JOB_ID].get("if")
    assert isinstance(cond, str) and cond.strip().startswith("always()"), (
        f"金丝雀必须以 always() 开头（上游一红它也要跑出来给 NOT-APPLICABLE），实际 if={cond!r}"
    )
    assert "pull_request" in cond, (
        f"金丝雀只在 pull_request 上有意义（push 事件没有 PR 可合并性），实际 if={cond!r}"
    )


def test_canary_job_name_is_not_required() -> None:
    """金丝雀是**诊断**作业，不是 required —— 它自己在跑时的 pending 会污染 mergeState 判据。"""
    names = {e["name"] for e in _manifest_entries()}
    canary_name = _ci_jobs()[CANARY_JOB_ID].get("name", CANARY_JOB_ID)
    assert canary_name not in names


def test_canary_permissions_are_valid_and_least_privilege() -> None:
    """`metadata` / `administration` 这类键会让整个 workflow 变成 0 作业（2026-10-13 实测）。"""
    perms = _ci_jobs()[CANARY_JOB_ID].get("permissions") or {}
    assert "metadata" not in perms and "administration" not in perms, (
        f"permissions 里出现了不被接受的键（会让整条 workflow 失效）：{sorted(perms)}"
    )
    for key in ("pull-requests", "checks"):
        assert perms.get(key) == "read", f"金丝雀需要 {key}: read 才能读 PR 汇总，实际 {perms}"


def test_manifest_path_constant_points_at_tracked_file() -> None:
    assert MANIFEST.is_file()
    assert canary.MANIFEST == MANIFEST
    assert canary.read_manifest() == MANIFEST_NAMES


def test_docs_describe_the_canary() -> None:
    """散文（§7.7）要写出作业名，否则文档与接线会各说各话。"""
    text = DOC.read_text(encoding="utf-8")
    assert "### 7.7" in text, "docs/ci-gates.md 必须有小节 §7.7 描述金丝雀"
    section = text.split("### 7.7", 1)[1]
    name = _ci_jobs()[CANARY_JOB_ID].get("name", CANARY_JOB_ID)
    assert name in section, f"§7.7 没写出金丝雀作业名 {name!r}"
    assert "UNVERIFIED" in section
