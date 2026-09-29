"""required checks 与 ci.yml 作业形状三方核对（清单 / 工作流 / 文档）。

起因是 2026-09-29 对 GitHub 分支保护语义的实测（`docs/ci-gates.md` §7.1）：

- required 上下文若**永不上报**（作业改名或删掉），所有 PR 停在 `blocked` —— 立刻可见；
- required 上下文若**恒为 skipped**，平台认为它已满足 —— **这是静默的假门禁**，
  看起来一直绿，实际上什么都没拦。危险方向是不对称的，本文件主要拦第二个。

所以要求：清单里每条都必须 ① 唯一对应一个作业名，② 在 `pull_request` 事件上**沿 needs
链递归可达**（自己不挂 trunk-only 条件、祖先也不挂），③ 不是矩阵生成的名字，④ 与
`docs/ci-gates.md` §7.2 的散文逐条一致。

注意 ② 为什么必须递归：`container-scan` 自己没有 `if:`，但它 `needs: docker`，而 `docker`
只在 trunk push 跑 —— 只查作业自身的条件是抓不到这种的。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
LIST_FILE = ROOT / ".github" / "ci-required-checks.json"
WORKFLOWS = ROOT / ".github" / "workflows"
DOC_FILE = ROOT / "docs" / "ci-gates.md"

KINDS = ("always", "scope-gated")
ENTRY_KEYS = {"name", "workflow", "job", "kind"}

# 只在 trunk push / tag / 定时上成立的条件片段 —— 带它的作业在 PR 上永远不会产出 check。
TRUNK_ONLY_PATTERNS = (
    "github.event_name == 'push'",
    'github.event_name == "push"',
    "github.ref == ",
    "startsWith(github.ref",
    "github.event_name == 'schedule'",
    'github.event_name == "schedule"',
    "github.event_name == 'release'",
)

SCOPE_COND = "needs.scope.outputs.code"


def _doc() -> dict:
    return json.loads(LIST_FILE.read_text(encoding="utf-8"))


def _entries() -> list[dict]:
    return _doc()["contexts"]


def _workflow(filename: str) -> tuple[dict, dict]:
    """返回 (on 块, jobs)。YAML 1.1 会把 `on:` 解析成布尔 True，两种键都要认。"""
    data = yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))
    on = data.get("on")
    if on is None:
        on = data.get(True) or {}
    return on, (data.get("jobs") or {})


def _as_list(needs) -> list[str]:
    if not needs:
        return []
    return [needs] if isinstance(needs, str) else list(needs)


def _trunk_only_conditions(jobs: dict, job_id: str, seen: set[str]) -> list[str]:
    """沿 needs 链递归收集"这个作业在 PR 上根本不会产出"的原因。"""
    if job_id in seen:  # needs 成环（GitHub 会直接判错，这里只求不递归爆栈）
        return []
    seen.add(job_id)
    job = jobs.get(job_id) or {}
    reasons: list[str] = []
    cond = job.get("if")
    if isinstance(cond, str) and any(p in cond for p in TRUNK_ONLY_PATTERNS):
        reasons.append(f"{job_id}: if={cond!r}")
    for parent in _as_list(job.get("needs")):
        reasons += [f"{job_id} ← {r}" for r in _trunk_only_conditions(jobs, parent, seen)]
    return reasons


def _section_72() -> str:
    text = DOC_FILE.read_text(encoding="utf-8")
    m = re.search(r"^### 7\.2.*?(?=^### |^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, "docs/ci-gates.md 里必须有小节 `### 7.2`（现行 required 配置的散文面）"
    return m.group(0)


def test_manifest_is_self_consistent() -> None:
    """清单本身要站得住：分支非空、条目非空、名字唯一、字段齐全、kind 合法。"""
    doc = _doc()
    assert doc.get("branch"), "清单必须写明对哪条分支生效"
    entries = _entries()
    assert entries, "required 清单不许为空（为空 = 保护被清空却看不出来）"
    names = [e["name"] for e in entries]
    assert len(names) == len(set(names)), f"清单里有重名 context：{names}"
    for e in entries:
        assert set(e) == ENTRY_KEYS, f"{e.get('name')} 字段不符：{sorted(set(e))}"
        assert e["kind"] in KINDS, f"{e['name']} 的 kind={e['kind']!r} 不在 {KINDS}"


def test_every_context_maps_to_exactly_one_job_name() -> None:
    """改名或删作业会让 required 上下文永不上报 → 所有 PR 卡 blocked，这里提前拦。"""
    all_names: dict[str, list[str]] = {}
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        _, jobs = _workflow(wf.name)
        for jid, job in jobs.items():
            all_names.setdefault(job.get("name", jid), []).append(f"{wf.name}:{jid}")

    for e in _entries():
        _, jobs = _workflow(e["workflow"])
        in_wf = [jid for jid, j in jobs.items() if j.get("name", jid) == e["name"]]
        assert in_wf == [e["job"]], (
            f"清单里的 {e['name']!r} 应恰好对应 {e['workflow']} 的作业 {e['job']!r}，"
            f"实际匹配到 {in_wf}。作业改名/删除会让该 required check 永不上报，"
            f"所有 PR 永久停在 blocked（2026-09-29 实测）。"
        )
        where = all_names[e["name"]]
        assert where == [f"{e['workflow']}:{e['job']}"], (
            f"context 名 {e['name']!r} 在多个 workflow 里重名（{where}），"
            f"required 判定会同时看见它们 —— 必须唯一。"
        )


def test_required_jobs_are_reachable_on_pull_request() -> None:
    """required 作业必须在 PR 上可达（递归查 needs 链）。"""
    for e in _entries():
        on, jobs = _workflow(e["workflow"])
        assert "pull_request" in on, (
            f"{e['workflow']} 没有 pull_request 触发，却把它的作业设为 required："
            f"{e['name']!r} 在 PR 上永远不会存在。"
        )
        reasons = _trunk_only_conditions(jobs, e["job"], set())
        assert not reasons, (
            f"required 上下文 {e['name']!r} 在 PR 上恒不产出（trunk-only 条件）："
            f"{reasons}。它会被平台当成已满足 ⇒ 静默假门禁。"
        )


def test_kind_matches_actual_if_condition() -> None:
    """清单声明的 kind 必须与作业真实条件一致，防止"以为是恒跑、其实是条件跑"。"""
    for e in _entries():
        _, jobs = _workflow(e["workflow"])
        cond = (jobs.get(e["job"]) or {}).get("if")
        if e["kind"] == "always":
            assert cond is None, (
                f"{e['name']!r} 标的是 always，但作业挂了 if={cond!r} —— "
                f"要么改清单 kind，要么去掉条件。"
            )
        else:
            assert isinstance(cond, str) and SCOPE_COND in cond, (
                f"{e['name']!r} 标的是 scope-gated，if 里却没有 {SCOPE_COND!r}（实际 {cond!r}）。"
            )


def test_no_matrix_generated_context() -> None:
    """矩阵作业的 skipped check 名字是未展开的字面量，按名字设 required 会卡死 docs-only PR。"""
    for e in _entries():
        assert "${{" not in e["name"], f"清单里不许出现模板字面量：{e['name']!r}"
        _, jobs = _workflow(e["workflow"])
        matrix = ((jobs.get(e["job"]) or {}).get("strategy") or {}).get("matrix")
        assert not matrix, (
            f"{e['name']!r} 对应作业 {e['job']} 是矩阵作业。矩阵腿在 docs-only 时上报的"
            f"是未展开的名字（实测 `pytest (${{{{ matrix.os }}}}, …)`），"
            f"无法作为单一 required 上下文；要覆盖它请加一条 name 稳定的聚合作业。"
        )


def test_docs_section_agrees_with_manifest() -> None:
    """散文（§7.2）与清单不许各说各话：既不能漏，也不能偷偷多。"""
    section = _section_72()
    cited = set(re.findall(r"`([^`\n]+)`", section))
    names = {e["name"] for e in _entries()}

    missing = names - cited
    assert not missing, f"§7.2 没写出清单里的这些 required 上下文（文档与配置已脱节）：{sorted(missing)}"

    _, jobs = _workflow("ci.yml")
    job_names = {j.get("name", jid) for jid, j in jobs.items()}
    extra = {c for c in cited if c in job_names} - names
    assert not extra, (
        f"§7.2 提到了这些 ci.yml 作业名，但它们不在 required 清单里：{sorted(extra)}。"
        f"要么加进清单并同步 GitHub 保护，要么从散文里去掉。"
    )
