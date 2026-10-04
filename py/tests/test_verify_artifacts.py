"""`expected_files` 工件门 —— 让 verify 真的看磁盘。

背景：在此之前**七个内置 gate 全部只看 `exit_code` 与 stdout 的正则**，`workdir`
一路传到 `VerifyEngine.verify()` 却从不下传给 gate。于是"验证通过"的真实含义只是
"进程退出 0 且打印了点什么"：agent 一个文件都没产出也算通过。

本文件钉三件事：

1. 门的判定（缺文件 / 大小不足 / 类型不符 / 越界路径）；
2. **不声明就不跑**（对既有 plan 零行为变化）；
3. **workdir 真的下传**（`verify()` → gate）——只测第 1 条的话，把 `_call_gate`
   换回 `gate_fn(plan, result)` 照样全绿，而门会因为拿不到 workdir 恒 fail-closed。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from maop.core.reliability.error_schema import new_result
from maop.maop_verify import GateResult, VerifyEngine, _call_gate, _gate_expected_files


def _exec_ok() -> object:
    return new_result(agent="a", task="t", exit_code=0, stdout="done")


def _verify(plan: dict, workdir: str = ""):
    return VerifyEngine().verify(plan=plan, result=_exec_ok(), workdir=workdir)


# ── 门本体 ───────────────────────────────────────────────────────────


def test_not_declared_means_gate_does_not_apply() -> None:
    """未声明 = 跳过（passed），而不是失败 —— 否则所有历史 plan 一夜全红。"""
    gr = _gate_expected_files({"gates": ["exit_code"]}, None, workdir=".")
    assert gr.passed is True
    assert "跳过" in gr.reason


def test_declared_but_no_workdir_fails_closed(tmp_path: Path) -> None:
    gr = _gate_expected_files({"expected_files": ["a.txt"]}, None, workdir="")
    assert gr.passed is False
    assert "fail-closed" in gr.reason


def test_existing_file_passes(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.md").write_text("hello", encoding="utf-8")
    gr = _gate_expected_files({"expected_files": ["out/report.md"]}, None, workdir=str(tmp_path))
    assert gr.passed is True, gr.reason


def test_missing_file_fails_with_the_path_named(tmp_path: Path) -> None:
    gr = _gate_expected_files({"expected_files": ["out/nope.md"]}, None, workdir=str(tmp_path))
    assert gr.passed is False
    assert "out/nope.md" in gr.reason, gr.reason


def test_min_bytes_rejects_placeholder(tmp_path: Path) -> None:
    """占位文件（写了个空文件交差）必须被拦下 —— 这是本门存在的意义之一。"""
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    plan = {"expected_files": [{"path": "data.json", "min_bytes": 1024}]}
    gr = _gate_expected_files(plan, None, workdir=str(tmp_path))
    assert gr.passed is False
    assert "过小" in gr.reason, gr.reason


def test_min_bytes_accepts_big_enough_file(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text("x" * 2048, encoding="utf-8")
    plan = {"expected_files": [{"path": "data.json", "min_bytes": 1024}]}
    assert _gate_expected_files(plan, None, workdir=str(tmp_path)).passed is True


def test_dir_kind_and_type_mismatch(tmp_path: Path) -> None:
    (tmp_path / "dist").mkdir()
    assert _gate_expected_files(
        {"expected_files": [{"path": "dist", "kind": "dir"}]}, None, workdir=str(tmp_path)
    ).passed is True
    # 声明是目录、实际是文件 → 红
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    gr = _gate_expected_files(
        {"expected_files": [{"path": "file.txt", "kind": "dir"}]}, None, workdir=str(tmp_path)
    )
    assert gr.passed is False and "目录" in gr.reason


def test_relative_escape_is_rejected(tmp_path: Path) -> None:
    """`../` 越界必须拒绝：声明来自 plan（可能由 LLM 产出），放行等于让 plan 探宿主盘。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()

    gr = _gate_expected_files({"expected_files": ["../outside.txt"]}, None, workdir=str(work))
    assert gr.passed is False
    assert "越出 workdir" in gr.reason, gr.reason


def test_absolute_path_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "abs.txt"
    outside.write_text("secret", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    gr = _gate_expected_files({"expected_files": [str(outside)]}, None, workdir=str(work))
    assert gr.passed is False
    assert "越出 workdir" in gr.reason, gr.reason


@pytest.mark.skipif(os.name == "nt", reason="Windows 建符号链接需要特权/开发者模式")
def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    """符号链接指向 workdir 外：字符串前缀判据会漏，resolve() 之后比较才拦得住。"""
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / "link.txt").symlink_to(outside)

    gr = _gate_expected_files({"expected_files": ["link.txt"]}, None, workdir=str(work))
    assert gr.passed is False
    assert "越出 workdir" in gr.reason, gr.reason


def test_workdir_that_is_not_a_directory_fails_closed(tmp_path: Path) -> None:
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    gr = _gate_expected_files({"expected_files": ["x.txt"]}, None, workdir=str(f))
    assert gr.passed is False and "不是目录" in gr.reason


def test_malformed_declarations_fail_loudly(tmp_path: Path) -> None:
    """声明写歪了要报错，不许静默放过。"""
    assert _gate_expected_files({"expected_files": 123}, None, workdir=str(tmp_path)).passed is False
    assert _gate_expected_files({"expected_files": [{"nopath": 1}]}, None, workdir=str(tmp_path)).passed is False
    assert _gate_expected_files(
        {"expected_files": [{"path": "a", "min_bytes": "not-a-number"}]}, None, workdir=str(tmp_path)
    ).passed is False


# ── 接线：workdir 必须真的下传 ───────────────────────────────────────


def test_verify_forwards_workdir_to_the_gate(tmp_path: Path) -> None:
    """核心接线证明：`verify(workdir=...)` 时门必须拿到真实目录并通过。

    若 `_call_gate` 被换回 `gate_fn(plan, result)`，门拿到空 workdir → fail-closed →
    本用例红。只测门本体是测不到这一点的。
    """
    (tmp_path / "artifact.txt").write_text("real content", encoding="utf-8")
    plan = {"gates": ["exit_code", "expected_files"], "expected_files": ["artifact.txt"]}
    res = _verify(plan, workdir=str(tmp_path))
    gate = next(g for g in res.gates if g.name == "expected_files")
    assert gate.passed is True, gate.reason
    assert res.passed is True


def test_verify_reports_missing_artifact_as_failure(tmp_path: Path) -> None:
    """端到端：产物缺失时整次 verify 判不通过（这是本功能的意义所在）。"""
    plan = {"gates": ["exit_code", "expected_files"], "expected_files": ["never-created.md"]}
    res = _verify(plan, workdir=str(tmp_path))
    assert res.passed is False
    gate = next(g for g in res.gates if g.name == "expected_files")
    assert gate.passed is False and "never-created.md" in gate.reason


def test_legacy_two_arg_custom_gate_still_works(tmp_path: Path) -> None:
    """`custom_gates` 是公开构造参数，既有外部 gate 是两参数的 —— 不许被打挂。"""
    seen: list[tuple] = []

    def legacy_gate(plan, result):
        seen.append((plan, result))
        return GateResult(name="legacy", passed=True)

    engine = VerifyEngine(custom_gates={"legacy": legacy_gate})
    res = engine.verify(plan={"gates": ["legacy"]}, result=_exec_ok(), workdir=str(tmp_path))
    assert res.passed is True
    assert len(seen) == 1, "两参数的既有自定义 gate 没被调用"


def test_three_arg_custom_gate_receives_workdir(tmp_path: Path) -> None:
    """新写的外部 gate 也能拿到 workdir（三参数签名）。"""
    got: list[str] = []

    def modern_gate(plan, result, workdir=""):
        got.append(workdir)
        return GateResult(name="modern", passed=True)

    engine = VerifyEngine(custom_gates={"modern": modern_gate})
    engine.verify(plan={"gates": ["modern"]}, result=_exec_ok(), workdir=str(tmp_path))
    assert got == [str(tmp_path)], got


def test_call_gate_does_not_swallow_type_errors_from_inside_the_gate() -> None:
    """`_call_gate` 不许把 gate 内部抛的 TypeError 当成"签名不匹配"重试。"""

    def broken_gate(plan, result, workdir=""):
        raise TypeError("a real bug inside the gate")

    with pytest.raises(TypeError, match="a real bug"):
        _call_gate(broken_gate, {}, None, ".")


def test_existing_default_gates_unchanged_without_declaration(tmp_path: Path) -> None:
    """默认 plan（不声明 expected_files）行为与历史一致。"""
    res = _verify({"gates": ["exit_code", "output"]}, workdir=str(tmp_path))
    assert res.passed is True
    assert {g.name for g in res.gates} == {"exit_code", "output"}
