"""`scripts/check_docs_consistency.py --gate` 的守卫（2026-09-29 接入 CI）。

要防的三件事：

1. **门禁变成空跑** —— 范围被放得无限宽（或豁免机制被滥用），于是一致性检查
   永远不会红，看起来"有门禁"实则没覆盖。所以用注入假路径的方式断言它真的会拦。
2. **豁免机制静默吞掉真漂移** —— 文件级/行级豁免都必须写理由，且门禁模式逐条
   打印；范围只能由 docs/README.md 的"当前权威文档"章节决定，归档章不能进来。
3. **ci.yml 的接线被改回"跟着 code 跳过"** —— 只删代码会让文档里的旧路径失效，
   只改文档会写进不存在的路径，两边都必须跑，所以 docs-gate 作业不能挂 if。

假阳性分型（后缀简写 / MAOS 跨仓 / 配置占位符 / 排版记号）各有用例钉住：
它们曾被当成 1400+ 条"漂移"，是这套检查此前接不进 CI 的原因。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "py" / "scripts"
CI = REPO / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(SCRIPT_DIR))
import check_docs_consistency as dcs

ALL_PATHS = {
    "py/maop/core/reliability/worker_pool.py",
    "dashboard-enterprise/src/views/Tenants.vue",
    "py/maop/dashboard/routers/agents/crud.py",
    "docs/adr/016-x.md",
}


# ── 1. 假阳性分型：不该报的别报 ──────────────────────────────────────

def test_suffix_shorthand_is_skipped():
    """包内/前端简写按段对齐命中即放行（曾是最大宗假阳性）。"""
    assert dcs.resolve_by_suffix("views/Tenants.vue", ALL_PATHS)
    assert dcs.resolve_by_suffix("agents/crud.py", ALL_PATHS)
    assert dcs.resolve_by_suffix("worker_pool.py", ALL_PATHS)  # 裸文件名


def test_moved_module_is_not_masked_by_suffix():
    """`core/worker_pool.py` 不因存在 `core/reliability/worker_pool.py` 而放行。

    这条是后缀规则的边界：放宽到"尾巴相同就算"会把真漂移全放过。
    """
    assert not dcs.resolve_by_suffix("core/worker_pool.py", ALL_PATHS)


def test_cross_repo_maos_refs_are_skipped():
    assert dcs.is_cross_repo("py/maop/enterprise/license.py")
    assert dcs.is_cross_repo("maop/enterprise/__init__.py")
    assert dcs.is_cross_repo("enterprise/sso.py")
    assert dcs.is_cross_repo("issue_license.py")           # MAOS/scripts 工具
    assert dcs.is_cross_repo("data/crl_cache.json")        # MAOS crl.py 运行时产物
    assert not dcs.is_cross_repo("py/maop/core/security/sandbox.py")


def test_placeholder_config_value_paths_are_skipped():
    assert dcs.is_placeholder_path("root_dir/data")
    assert dcs.is_placeholder_path("data_dir/maop.db")
    assert not dcs.is_placeholder_path("config/agents.yaml")


@pytest.mark.parametrize("token", [
    "✓/✗/○",                      # 排版记号，不是路径
    "py/requirements.lock|txt",   # shell 风格交替写法
    "feature/*",                  # 通配符
])
def test_non_path_tokens_are_not_checked(token: str):
    assert not dcs.looks_like_path(token)


def test_real_path_claims_still_look_like_paths():
    assert dcs.looks_like_path("docs/Nexus统一编排平台_HLD.md")
    assert dcs.looks_like_path("core/security/tenant.py")


def test_gitignore_dir_rule_matches_at_depth():
    """`htmlcov/` 必须命中 `py/htmlcov/index.html`（此前只比仓库根前缀 → 误报）。"""
    patterns = ["htmlcov/", "data/*.db"]
    assert dcs.is_gitignored("py/htmlcov/index.html", patterns)
    assert dcs.is_gitignored("data/maop.db", patterns)
    assert not dcs.is_gitignored("docs/maop.db", patterns)


# ── 2. 计数口径：与 CI 的 doc_reconcile 一致 ─────────────────────────

def test_count_kinds_use_different_semantics(tmp_path: pathlib.Path):
    core = tmp_path / "core"
    (core / "pkg").mkdir(parents=True)
    (core / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (core / "pkg" / "deep.py").write_text("", encoding="utf-8")
    (core / "a.py").write_text("", encoding="utf-8")
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "ARCHITECTURE.md").write_text("", encoding="utf-8")
    assert dcs.count_dir_stat("file", core) == 2          # a.py + ARCHITECTURE.md
    assert dcs.count_dir_stat("subpackage", core) == 1
    assert dcs.count_dir_stat("module", core) == 4        # 递归 *.py（含 __init__.py）


# ── 3. 门禁范围：由文档索引决定，归档章不许进来 ──────────────────────

def test_maintained_docs_come_from_current_chapters():
    docs = dcs.load_maintained_docs()
    assert "README.md" in docs
    assert "docs/configuration.md" in docs            # 第 3 章
    assert "docs/api-reference.md" in docs            # 第 1 章
    assert not any(k.startswith("docs/archive/") for k in docs)   # 第 7 章归档
    assert "docs/archive/README.md" not in docs


def test_empty_index_degrades_to_readme_only(tmp_path: pathlib.Path, monkeypatch):
    """索引缺失时范围收缩到只剩 README —— 宁可少判，不可凭空放大豁免。"""
    monkeypatch.setattr(dcs, "DOC_INDEX", tmp_path / "missing.md")
    assert dcs.load_maintained_docs() == {"README.md"}


# ── 4. 豁免必须写理由 ────────────────────────────────────────────────

def test_file_exempt_marker_requires_reason(tmp_path: pathlib.Path):
    md = tmp_path / "a.md"
    md.write_text("# T\n\n<!-- docs-gate: exempt=时间点快照 -->\n", encoding="utf-8")
    m = dcs.GATE_EXEMPT_RE.search(md.read_text(encoding="utf-8"))
    assert m and m.group(1) == "时间点快照"
    # 无理由的标记不成立：防止"随手贴一个标记就免检"
    assert not dcs.GATE_EXEMPT_RE.search("<!-- docs-gate: exempt= -->")


def test_line_skip_marker_requires_reason():
    assert dcs.GATE_LINE_SKIP_RE.search("x `a.py` <!-- docs-gate: skip=已删除对象 -->")
    assert not dcs.GATE_LINE_SKIP_RE.search("x `a.py` <!-- docs-gate: skip= -->")


# ── 5. 端到端：门禁真会红 ────────────────────────────────────────────

# ── 5b. --gate 端到端（假仓库，不依赖真实 docs/）─────────────────────

@pytest.fixture
def fake_repo(tmp_path: pathlib.Path, monkeypatch):
    """搭一个最小仓库并把手脚本的根常量指过去。

    索引里只把 docs/current.md 列为"当前权威文档"，docs/ghost_doc.md 不列入，
    用来证明范围由索引决定而不是"docs/ 下全都算"。
    """
    repo = tmp_path / "repo"
    (repo / "py" / "maop").mkdir(parents=True)
    (repo / "docs" / "archive").mkdir(parents=True)
    (repo / "dashboard-enterprise").mkdir()
    (repo / "README.md").write_text("# MAOP\n", encoding="utf-8")
    (repo / "py" / "maop" / "real.py").write_text("", encoding="utf-8")
    (repo / "docs" / "README.md").write_text(
        "# 索引\n\n## 第1章 快速入门\n\n"
        "| 文档 | 说明 |\n|---|---|\n| [当前](./current.md) | x |\n\n"
        "## 第7章 归档\n\n历史文档归档于 [archive/](./archive/README.md)。\n",
        encoding="utf-8",
    )
    (repo / "docs" / "archive" / "README.md").write_text("# 归档\n", encoding="utf-8")
    (repo / "docs" / "current.md").write_text("# T\n\n", encoding="utf-8")
    (repo / "docs" / "ghost_doc.md").write_text("# T\n\n", encoding="utf-8")
    monkeypatch.setattr(dcs, "REPO_ROOT", repo)
    monkeypatch.setattr(dcs, "PY_DIR", repo / "py")
    monkeypatch.setattr(dcs, "DASH_DIR", repo / "dashboard-enterprise")
    monkeypatch.setattr(dcs, "DOC_INDEX", repo / "docs" / "README.md")
    return repo


def test_gate_fails_on_dead_path_in_current_doc(fake_repo, capsys):
    (fake_repo / "docs" / "current.md").write_text(
        "# T\n\n引用 `py/maop/ghost.py`。\n", encoding="utf-8"
    )
    assert dcs.main(["--gate"]) == 1
    out = capsys.readouterr().out
    assert "py/maop/ghost.py" in out and "门禁模式" in out


def test_line_skip_marker_is_honoured_and_counted(fake_repo, capsys):
    """同一行加了带理由的行级豁免后不再判定，且跳过计数会被打印（不静默）。"""
    (fake_repo / "docs" / "current.md").write_text(
        "# T\n\n引用 `py/maop/ghost.py`。 <!-- docs-gate: skip=已删除对象的历史记录 -->\n",
        encoding="utf-8",
    )
    assert dcs.main(["--gate"]) == 0
    assert "行级豁免" in capsys.readouterr().out


def test_line_skip_without_reason_does_not_exempt(fake_repo):
    """空理由不构成豁免——否则贴个标记就免检。"""
    (fake_repo / "docs" / "current.md").write_text(
        "# T\n\n引用 `py/maop/ghost.py`。 <!-- docs-gate: skip= -->\n",
        encoding="utf-8",
    )
    assert dcs.main(["--gate"]) == 1


def test_gate_passes_when_path_is_real(fake_repo):
    (fake_repo / "docs" / "current.md").write_text(
        "# T\n\n引用 `py/maop/real.py`。\n", encoding="utf-8"
    )
    assert dcs.main(["--gate"]) == 0


def test_gate_scope_excludes_unlisted_docs(fake_repo):
    """不在当前章节里的文档不判定；但全量模式必须仍然报出来。"""
    (fake_repo / "docs" / "ghost_doc.md").write_text(
        "# T\n\n引用 `py/maop/ghost.py`。\n", encoding="utf-8"
    )
    assert dcs.main(["--gate"]) == 0
    assert dcs.main([]) == 1


def test_file_level_exempt_needs_reason_and_is_printed(fake_repo, capsys):
    (fake_repo / "docs" / "current.md").write_text(
        "# T\n\n<!-- docs-gate: exempt=时间点快照 -->\n\n引用 `py/maop/ghost.py`。\n",
        encoding="utf-8",
    )
    assert dcs.main(["--gate"]) == 0
    out = capsys.readouterr().out
    assert "豁免" in out and "时间点快照" in out

def test_docs_gate_job_exists_and_is_unconditional():
    wf = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = wf["jobs"]["docs-gate"]
    assert job.get("if") in (None, ""), "docs-gate 不得被 if 条件跳过（两边都要跑）"
    assert job["needs"] == "scope"
    cmds = [s.get("run", "") for s in job["steps"]]
    assert any("check_docs_consistency.py --gate" in c for c in cmds)


def test_current_repo_docs_pass_the_gate():
    """本仓当前状态必须过门禁 —— 否则这条 CI 检查一接线就是常红。"""
    assert dcs.main(["--gate"]) == 0
