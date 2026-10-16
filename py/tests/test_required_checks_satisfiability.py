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


# ── 归因前置：本轮 run 说的话还代表这个 PR 吗 ─────────────────────────


def _entries() -> list[dict]:
    return canary.read_manifest_entries()


def _needs(**overrides: str) -> dict[str, dict]:
    """默认"本轮 run 的清单作业全成功"，按作业 id 覆盖结论（模拟取消/失败/跳过）。"""
    results = {str(e["job"]): "success" for e in _entries()}
    results.update(overrides)
    return {job: {"result": res} for job, res in results.items()}


SAME_SHA = "a" * 40
OTHER_SHA = "b" * 40


def test_parse_needs_uppercases_and_keeps_null_as_empty() -> None:
    """被跳过/未跑的作业 result 是 null —— 必须折成空串，不能变成 "NONE" 混进可接受集合。"""
    raw = json.dumps({"scope": {"result": "success", "outputs": {}}, "gate": {"result": None}})
    parsed = canary.parse_needs(raw)
    assert parsed["scope"] == "SUCCESS"
    assert parsed["gate"] == ""


def test_parse_needs_bad_or_non_object_json_is_empty() -> None:
    assert canary.parse_needs("") == {}
    assert canary.parse_needs("{not json") == {}
    assert canary.parse_needs("[1, 2]") == {}


def test_cancelled_manifest_job_abstains_never_drift() -> None:
    """真实假红（run 38729427212）：本轮 docs-gate 被切成 cancelled ⇒ 归因不成立，弃权。"""
    state, reasons = canary.attribution(
        canary.parse_needs(json.dumps(_needs(**{"docs-gate": "cancelled"}))),
        _entries(),
        SAME_SHA,
        SAME_SHA,
    )
    assert state == "ABSTAIN"
    assert any("docs-gate" in r for r in reasons), "必须点名那个没跑成的作业"


def test_failed_manifest_job_abstains() -> None:
    state, _ = canary.attribution(
        canary.parse_needs(json.dumps(_needs(gate="failure"))),
        _entries(),
        SAME_SHA,
        SAME_SHA,
    )
    assert state == "ABSTAIN"


def test_job_missing_from_needs_abstains() -> None:
    """needs 里查不到清单作业（被改名/删掉 needs）——"不知道"不能算归因成立。"""
    needs = canary.parse_needs(json.dumps({"scope": {"result": "success"}}))
    state, reasons = canary.attribution(needs, _entries(), SAME_SHA, SAME_SHA)
    assert state == "ABSTAIN"
    assert any("<无记录>" in r for r in reasons)


def test_stale_head_sha_abstains_even_when_needs_all_green() -> None:
    """needs 全成功但 PR 现 head 已变（抢占式取消的时序缝隙）：汇总属于新 head，弃权。"""
    state, reasons = canary.attribution(
        canary.parse_needs(json.dumps(_needs())), _entries(), SAME_SHA, OTHER_SHA
    )
    assert state == "ABSTAIN"
    assert any("head" in r for r in reasons)


def test_attribution_ok_is_reachable_positive_control() -> None:
    """守卫不能变成"永远弃权"的门禁：清单作业全成功 + head 未变时归因必须成立。"""
    state, reasons = canary.attribution(
        canary.parse_needs(json.dumps(_needs())), _entries(), SAME_SHA, SAME_SHA
    )
    assert state == "OK" and reasons == []


def test_scope_gated_job_may_legitimately_be_skipped() -> None:
    """docs-only 时被 scope 拦住的作业本就 skipped —— 那是合法缺席，不该弃权。"""
    entry = {"name": "X", "job": "x-job", "kind": "scope-gated"}
    needs = canary.parse_needs(json.dumps({"x-job": {"result": "skipped"}}))
    state, _ = canary.attribution(needs, [entry], SAME_SHA, SAME_SHA)
    assert state == "OK"


def test_needs_without_attribution_basis_is_unverified_not_pass() -> None:
    """没接上 NEEDS_JSON / PR_HEAD_SHA ⇒ 判 UNVERIFIED（可见的红），不许静默退回旧行为。"""
    entries = _entries()
    assert canary.attribution({}, entries, SAME_SHA, SAME_SHA)[0] == "UNVERIFIED"
    assert canary.attribution(
        canary.parse_needs(json.dumps(_needs())), entries, "", SAME_SHA
    )[0] == "UNVERIFIED"
    assert canary.attribution(
        canary.parse_needs(json.dumps(_needs())), entries, SAME_SHA, ""
    )[0] == "UNVERIFIED"


def test_canary_wiring_feeds_the_attribution_inputs() -> None:
    """脚本在 needs / head sha 缺失时判 UNVERIFIED —— 那前提 ci.yml 真的把两个都喂进来。

    这条是"门禁自我失明"的接线守卫：注释掉了 env、或表达式打错，判定端只会看见空串，
    金丝雀就退化成本来要修的那个"永远不表态"的样子。
    """
    steps = _ci_jobs()[CANARY_JOB_ID]["steps"]
    step = next(
        (s for s in steps if "check_required_checks_satisfiability.py" in str(s.get("run", ""))),
        None,
    )
    assert step is not None, "canary 作业里没有调用判定脚本的步骤"
    env = step.get("env") or {}
    assert "toJSON(needs)" in str(env.get("NEEDS_JSON", "")), env
    assert "pull_request.head.sha" in str(env.get("PR_HEAD_SHA", "")), env


def test_manifest_jobs_covered_by_canary_needs_for_attribution() -> None:
    """归因按 `job` 查 needs，清单里的 job 必须就是 ci.yml 的作业 id（否则永远"<无记录>"→永远弃权）。"""
    jobs = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]
    missing = [str(e["job"]) for e in _entries() if e["job"] not in jobs]
    assert not missing, f"清单里的这些 job 在 ci.yml 里不存在：{missing}"


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


# ── 接线：main 的三条归因路径 ─────────────────────────────────────────


def _pr(observed: dict[str, str], merge_state: str, head_sha: str) -> dict:
    nodes = [
        {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": concl}
        for name, concl in observed.items()
    ]
    return {
        "number": 110,
        "isDraft": False,
        "mergeStateStatus": merge_state,
        "headRefOid": head_sha,
        "statusCheckRollup": {"contexts": {"totalCount": len(nodes), "nodes": nodes}},
    }


def _main_args(needs: dict | None, head_sha: str) -> list[str]:
    return [
        "--token",
        "t",
        "--pr",
        "110",
        "--repo",
        "Levango7/MAOP",
        "--needs-json",
        "" if needs is None else json.dumps(needs),
        "--head-sha",
        head_sha,
    ]


def test_main_superseded_run_is_not_a_false_drift(monkeypatch, capsys) -> None:
    """复刻 run 38729427212（head cb67725 被抢占取消后金丝雀仍跑完）：本轮 docs-gate=cancelled、
    gate=failure、head 已过期，而读到的汇总里四条 required 全缺席 + BLOCKED。
    旧实现据此判出 `DRIFT: 4 处`（同 PR 四分钟后在真 head 上判 MATCH）；现在必须弃权。"""
    monkeypatch.setattr(
        canary, "poll_until_settled", lambda *a, **k: _pr({}, "BLOCKED", OTHER_SHA)
    )
    rc = canary.main(
        _main_args(
            _needs(**{"docs-gate": "cancelled", "gate": "failure"}),
            SAME_SHA,
        )
    )
    out = capsys.readouterr().out
    assert rc == canary.EXIT_MATCH, rc
    assert "NOT-APPLICABLE" in out
    assert "DRIFT" not in out, "弃权路径里不许漂出 DRIFT 字样"


def test_main_still_reports_real_drift_when_attribution_holds(monkeypatch, capsys) -> None:
    """正对照：归因守卫不能把真漂移也吃掉。needs 全成功、head 未变，却有一条 required 缺席
    且平台判 BLOCKED ⇒ 必须仍是 DRIFT。"""
    observed = _all_green()
    del observed["CI merge gate"]
    monkeypatch.setattr(
        canary, "poll_until_settled", lambda *a, **k: _pr(observed, "BLOCKED", SAME_SHA)
    )
    rc = canary.main(_main_args(_needs(), SAME_SHA))
    out = capsys.readouterr().out
    assert rc == canary.EXIT_DRIFT, out
    assert "CI merge gate" in out, "必须点名缺席的那条上下文"


def test_main_match_with_full_inputs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        canary, "poll_until_settled", lambda *a, **k: _pr(_all_green(), "CLEAN", SAME_SHA)
    )
    rc = canary.main(_main_args(_needs(), SAME_SHA))
    assert rc == canary.EXIT_MATCH
    assert "OK:" in capsys.readouterr().out


def test_main_without_attribution_inputs_is_unverified(monkeypatch, capsys) -> None:
    """env 里两个归因入参都没给 ⇒ UNVERIFIED（可见），而不是"照旧判绿"。"""
    monkeypatch.setattr(
        canary, "poll_until_settled", lambda *a, **k: _pr(_all_green(), "CLEAN", SAME_SHA)
    )
    monkeypatch.delenv("NEEDS_JSON", raising=False)
    monkeypatch.delenv("PR_HEAD_SHA", raising=False)
    assert canary.main(_main_args(None, "")) == canary.EXIT_UNVERIFIED
    assert "不等于通过" in capsys.readouterr().out


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
    assert "归因" in section, "§7.7 要写出'缺席'有两种成因，否则读者会把过期 run 当成平台漂移"
    assert "toJSON(needs)" in section and "headRefOid" in section, "§7.7 要写出两道归因信号各自是什么"
