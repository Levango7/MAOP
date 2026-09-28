"""`scripts/ci_path_scope.py` 与 `ci.yml` 触发面的守卫。

要防的两件事：

1. **分类逻辑本身判错** —— docs-only 被误判成要跑全量（浪费），或反过来把代码变更判成
   docs-only 而**跳过了本该跑的门禁**（后者是事故）。所以两边都用例外钉住，且空变更集必须
   保守地判"跑"。
2. **`on.*.paths` 白名单回来** —— 白名单不是"少跑一点"，而是"整个 workflow 一条 check 都不产生"，
   于是"全绿"与"没跑"在 API 上不可区分（本会话真被它骗过一次，见 CHANGELOG）。
   所以这里直接断言工作流的结构：PR 一定跑、`scope` 一定无条件存在、重活根作业一定挂在
   `scope` 上、并且至少有一条永远会跑的 check（`secret-scan`）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "ci_path_scope.py"
CI = REPO / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(SCRIPT.parent))
import ci_path_scope as scope_mod


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    return doc.get("on") or doc.get(True) or {}


# ── 分类逻辑 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "paths",
    [
        ["CHANGELOG.md"],
        ["docs/adr/012-routing-refactor.md", "docs/user-guide.md"],
        ["LICENSE"],
        ["README.md.bak", "CHANGELOG.md"],
    ],
)
def test_docs_only_changesets_are_docs_only(paths: list[str]) -> None:
    assert scope_mod.needs_heavy_ci(paths) is False, paths


@pytest.mark.parametrize(
    "paths",
    [
        ["py/maop/core/agent/tools/tool_manager.py"],
        ["py/requirements.lock"],           # 依赖面：审计与镜像都读它
        ["py/Dockerfile"],
        ["dashboard-enterprise/src/views/Monitor.vue"],
        ["dashboard-enterprise/vitest.config.js"],
        ["config/agents.yaml"],
        [".github/workflows/ci.yml"],
        ["docker-compose.prod.yml"],
        [".dockerignore"],
        ["README.md"],                      # Doc↔Code reconcile 门禁会读它
        ["ROADMAP.md"],
        ["CHANGELOG.md", "py/pyproject.toml"],  # 混合变更集必须按"要跑"处理
    ],
)
def test_code_changesets_are_not_docs_only(paths: list[str]) -> None:
    assert scope_mod.needs_heavy_ci(paths) is True, paths


@pytest.mark.parametrize("paths", [[], ["   "], [""]])
def test_empty_changeset_is_conservative(paths: list[str]) -> None:
    """没数据 ≠ 不用跑。基线算不出来时宁可全量。"""
    assert scope_mod.needs_heavy_ci(paths) is True


def test_cli_writes_github_output(tmp_path: Path) -> None:
    out = tmp_path / "gh_output"
    lst = tmp_path / "files.txt"
    lst.write_text("CHANGELOG.md\x00docs/a.md\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--files-from", str(lst), "--github-output", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").strip() == "code=false"
    assert "docs-only" in r.stdout


def test_cli_mixed_changeset_reports_code_true(tmp_path: Path) -> None:
    out = tmp_path / "gh_output"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--files-from", "-", "--github-output", str(out)],
        input="CHANGELOG.md\npy/maop/engine.py\n", capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").strip() == "code=true"


# ── 工作流结构：白名单不许回来 ──────────────────────────────────────


def test_pr_trigger_has_no_paths_whitelist() -> None:
    """`on.pull_request.paths` 会让不匹配的 PR **一条 check 都不产生**，必须用 job 级判断替代。"""
    tr = _triggers(_ci())
    pr = tr.get("pull_request") or {}
    assert "paths" not in pr and "paths-ignore" not in pr, (
        "ci.yml 的 pull_request 又用回了 paths 白名单 —— docs-only PR 将不产生任何 check，"
        "'CI 没跑' 与 'CI 全绿' 在 API 上不可区分（这正是要修的缺陷）"
    )


def test_scope_job_is_unconditional_root() -> None:
    jobs = _ci()["jobs"]
    assert "scope" in jobs, "缺少 scope 作业：路径判断没有落点"
    job = jobs["scope"]
    assert not job.get("needs"), "scope 必须是根作业，否则可能根本不跑"
    assert "if" not in job, "scope 必须无条件运行 —— 它就是用来报告'这次该跑哪些'的"


@pytest.mark.parametrize("root", ["lint", "frontend"])
def test_heavy_root_jobs_gate_on_scope(root: str) -> None:
    job = _ci()["jobs"][root]
    needs = job.get("needs") or []
    needs = needs if isinstance(needs, list) else [needs]
    assert "scope" in needs, f"{root} 必须依赖 scope"
    assert "scope.outputs.code" in str(job.get("if", "")), (
        f"{root} 必须按 scope 的判定决定跑不跑，不能无条件跑"
    )


def test_at_least_one_check_always_runs() -> None:
    """保护规则要有"永远存在的 check"可指，否则 docs-only PR 又回到零状态。"""
    jobs = _ci()["jobs"]
    always = [
        n for n, j in jobs.items()
        if not j.get("needs") and "if" not in j and not str(j.get("if", "")).strip()
    ]
    assert "secret-scan" in always, (
        f"secret-scan 必须无条件跑，给 docs-only PR 留下一条真实 check；当前无条件作业={always}"
    )
