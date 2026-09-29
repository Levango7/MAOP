"""`CI merge gate`：判定脚本的策略单测 + 工作流形状守卫。

为什么要有这条作业（`docs/ci-gates.md` §7.3）：矩阵作业 `pytest (...)` 的 check 名在 docs-only
时是**未展开的模板字面量**，push-only 作业在 PR 上恒为 skipped —— 两类都不能当 required 上下文，
于是"测试必须绿"只能靠人看。聚合成一条 name 稳定的 check 后，平台才能拦。

两侧都要钉住：
- **判定面**（`evaluate` / CLI）：白名单式，拿不到输入必须 fail closed，且 docs-only 的合法
  skipped 不许变成红（否则这条 required 会卡死所有文档 PR）。
- **形状面**（ci.yml）：`if: always()` 不能掉（掉了上游一红它就不产出 → required 永不上报），
  needs 必须盖住全部重活，且不许把 push-only 作业纳进来。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "ci_merge_gate.py"
CI = REPO / ".github" / "workflows" / "ci.yml"
MANIFEST = REPO / ".github" / "ci-required-checks.json"

sys.path.insert(0, str(SCRIPT.parent))
import ci_merge_gate as gate_mod

GATE_NAME = "CI merge gate"
# 这些作业在 PR 上都可能真实执行（docs-only 时被 scope 跳过），必须被 gate 覆盖。
REQUIRED_UPSTREAM = {
    "lint",
    "test",
    "frontend",
    "e2e",
    "migrations",
    "audit",
    "sast",
    "sbom",
    "perf-smoke",
}
# push-only 作业在 PR 上恒为 skipped，纳进来只会让"required"变成空满足，故明确排除。
TRUNK_ONLY_JOBS = {"docker", "container-scan", "compose-smoke", "publish"}


def _needs(**results: str) -> dict:
    return {k: {"result": v} for k, v in results.items()}


def _ci_jobs() -> dict:
    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return doc["jobs"]


def _gate_needs() -> list:
    return _ci_jobs()["gate"].get("needs") or []


# ── 判定面：evaluate ─────────────────────────────────────────────────


def test_all_success_passes_on_code_changeset() -> None:
    needs = _needs(scope="success", **{j: "success" for j in REQUIRED_UPSTREAM})
    assert gate_mod.evaluate(needs, "true") == []


def test_failure_and_cancellation_are_both_rejected_by_name() -> None:
    needs = _needs(scope="success", lint="success", test="failure", frontend="cancelled")
    reasons = gate_mod.evaluate(needs, "true")
    assert len(reasons) == 2, reasons
    assert any(r.startswith("test: result=failure") for r in reasons), reasons
    assert any(r.startswith("frontend: result=cancelled") for r in reasons), reasons


def test_docs_only_skips_are_satisfied() -> None:
    """docs-only 时全部重活被 `if:` 跳过 —— gate 必须绿，否则这条 required 会卡死文档 PR。"""
    needs = _needs(scope="success", **{j: "skipped" for j in REQUIRED_UPSTREAM})
    assert gate_mod.evaluate(needs, "false") == []


def test_code_changeset_with_a_skipped_job_is_rejected() -> None:
    """分类说"改了代码"却有该跑的作业没跑 = 没有门禁，必须红而不是"看不见"。"""
    needs = _needs(scope="success", lint="success", test="skipped")
    reasons = gate_mod.evaluate(needs, "true")
    assert len(reasons) == 1 and reasons[0].startswith("test:"), reasons
    assert "skipped" in reasons[0] and "该跑" in reasons[0], reasons


@pytest.mark.parametrize("code", ["", None, "nonsense"])
def test_missing_scope_output_is_fail_closed(code) -> None:
    """scope 没输出（作业失败/被取消/表达式拿空串）时按严格面处理：skipped 不算通过。"""
    needs = _needs(scope="success", test="skipped")
    assert gate_mod.evaluate(needs, code or "") != []


def test_empty_or_malformed_needs_is_fail_closed() -> None:
    assert gate_mod.evaluate({}, "true") != []
    assert gate_mod.evaluate({}, "false") != [], "空输入即使 docs-only 也不能判通过"
    assert gate_mod.evaluate({"lint": "success"}, "true") != [], "needs 值必须是对象"
    assert gate_mod.evaluate({"lint": {}}, "true") != [], "缺 result 字段不算通过"


def test_unknown_future_result_never_passes_silently() -> None:
    """白名单式判定：GitHub 将来新增的结论值不能被判成通过。"""
    assert gate_mod.evaluate(_needs(test="action_required"), "true") != []
    assert gate_mod.evaluate(_needs(test="neutrality"), "false") != []


# ── 判定面：CLI ─────────────────────────────────────────────────────


def _run_cli(tmp_path: Path, needs: dict, code: str) -> subprocess.CompletedProcess:
    f = tmp_path / "needs.json"
    f.write_text(json.dumps(needs), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--needs-json", str(f), "--code", code],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def test_cli_exit_codes(tmp_path: Path) -> None:
    ok = _run_cli(tmp_path, _needs(scope="success", test="success"), "true")
    assert ok.returncode == 0, ok.stderr
    assert "OK:" in ok.stdout

    bad = _run_cli(tmp_path, _needs(scope="success", test="failure"), "true")
    assert bad.returncode == 1, bad.stdout
    assert "::error title=CI merge gate::" in bad.stdout, bad.stdout
    assert "test: result=failure" in bad.stdout


def test_cli_reads_missing_file_as_failure_not_silence(tmp_path: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--needs-json", str(tmp_path / "nope.json"), "--code", "true"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert r.returncode == 1, r.stdout
    assert "fail closed" in r.stdout or "FAIL" in r.stdout


def test_cli_stdout_is_valid_utf8_bytes_under_cp1252_console(tmp_path: Path) -> None:
    """中文摘要在非 UTF-8 控制台不能崩（本仓在 Windows 腿上真栽过一次）。

    取原始字节严格按 UTF-8 解码：zh-CN 本机 cp936 会把坏输出吞成乱码却不报错，
    只有字节层断言才在所有平台都成立。
    env 必须继承父进程只覆盖 PYTHONIOENCODING：精简到只剩 PATH 会让 Windows 3.10 的
    子解释器在启动阶段就死于 _Py_HashRandomization_Init（CI 实测，3.11+ 不复现）。
    """
    f = tmp_path / "needs.json"
    f.write_text(json.dumps(_needs(scope="success", test="success")), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--needs-json", str(f), "--code", "true"],
        capture_output=True,
        check=False,
        env={**dict(os.environ), "PYTHONIOENCODING": "cp1252"},
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    text = r.stdout.decode("utf-8")  # 解不出就抛，正是这条用例要的行为
    assert "CI merge gate" in text


# ── 形状面：ci.yml 与清单 ────────────────────────────────────────────


def test_gate_job_exists_with_stable_name_and_always() -> None:
    gate = _ci_jobs().get("gate")
    assert gate is not None, "ci.yml 里必须有 gate 作业"
    assert gate.get("name") == GATE_NAME, (
        f"gate 的 name 必须保持 {GATE_NAME!r} —— 它同时是 required 上下文名，改名会让"
        f"所有 PR 的该 required check 永不上报（§7.1 实测）"
    )
    cond = gate.get("if")
    assert isinstance(cond, str) and cond.strip().startswith("always()"), (
        f"gate 必须 if: always()，否则上游一红它就不产出、required 变成"
        f"'从未上报' → 所有 PR 永久 blocked（实际 {cond!r}）"
    )


def test_gate_covers_every_pr_runnable_heavy_job() -> None:
    needs = set(_gate_needs())
    missing = REQUIRED_UPSTREAM - needs
    assert not missing, f"gate 没覆盖这些重活作业，它们仍处在无人强制的状态：{sorted(missing)}"
    assert "scope" in needs, "gate 要读 scope 的 outputs.code，必须 needs 它"


def test_gate_does_not_need_trunk_only_jobs() -> None:
    """纳进 push-only 作业会让 gate 在 PR 上因它们 skipped 而失去意义（严格面直接恒红）。"""
    needs = set(_gate_needs())
    overlap = needs & TRUNK_ONLY_JOBS
    assert not overlap, f"gate 的 needs 里不许有只在 trunk push 跑的作业：{sorted(overlap)}"


def test_gate_step_invokes_the_aggregation_script() -> None:
    steps = _ci_jobs()["gate"]["steps"]
    bodies = "\n".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
    assert "ci_merge_gate.py" in bodies, "gate 必须真的调用判定脚本，而不是空转"
    assert "--code" in bodies, "gate 必须把 scope 的 code 输出传给脚本（docs-only 的口径来源）"


def test_gate_uses_an_interpreter_that_exists_on_the_runner() -> None:
    """gate 作业**故意不跑** setup-python（越少活动部件，required check 越不容易因 setup
    失败而恒红）。那它就不能写裸 `python` —— ubuntu runner 上不保证有这个别名，只会
    `python3`。写成 `python` 会让这条 required check 在每个 PR 上恒红。
    """
    gate = _ci_jobs()["gate"]
    assert not any(
        "setup-python" in str(s.get("uses", "")) for s in gate["steps"] if isinstance(s, dict)
    ), "gate 若引入 setup-python，下面这条 python3 约束就要一起改"
    bodies = "\n".join(str(s.get("run", "")) for s in gate["steps"] if isinstance(s, dict))
    assert "python3 scripts/ci_merge_gate.py" in bodies, (
        "gate 必须用 python3 调脚本；裸 `python` 在 ubuntu runner 上不保证存在"
    )


def test_manifest_lists_the_gate() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["contexts"]
    hit = [e for e in entries if e["name"] == GATE_NAME]
    assert len(hit) == 1, f"清单里 {GATE_NAME!r} 应恰好一条：{hit}"
    assert hit[0]["job"] == "gate" and hit[0]["kind"] == "always-guard", hit[0]
