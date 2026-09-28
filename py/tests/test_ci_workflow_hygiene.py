"""CI 工作流卫生：不许有"自己吞掉失败"的步骤（按 YAML 结构判定，不按文本匹配）。

起因（2026-09-28 实测）：ci.yml 里曾有一个 codecov 上传步骤写着 `fail_ci_if_error: false`。
本仓没有 `CODECOV_TOKEN`，于是每次 ubuntu/3.13 腿都真实报错：

    error -- Commit creating failed: {"message":"Token required - not valid tokenless upload"}
    error -- Report creating failed / Upload queued for processing failed

但这个开关让 job 恒绿 —— 一道**永远不会红、也永远不产出**的门禁，还会让人以为覆盖率数据在别处被看着。
该步骤已删除（见 CHANGELOG）。本文件防止同形问题回潮。

第一版我用正则在原文里搜 `fail_ci_if_error: false`，结果被自己的解释性注释判红 ——
所以这里改成解析 YAML 后只看**步骤字段**：注释不算数，配置才算数。
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# 允许"看得见但不拦"的步骤，必须逐条写明理由；新增一条就得在这里登记，
# 也就是强制有人在 PR 里为"为什么可以吞"负责。
CONTINUE_ON_ERROR_ALLOWED = {
    ("ci.yml", "Generate bandit report (JSON)"): (
        "报告生产者：生成失败要在步骤状态里可见但不拦 job，"
        "真正的门禁是后面那条 bandit High=0 的 `-lll` 硬门"
    ),
}


def _steps() -> list[tuple[str, str, dict]]:
    out = []
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    out.append((wf.name, f"{job_name} / {step.get('name', '(未命名)')}", step))
    return out


def test_no_step_sets_fail_ci_if_error_false() -> None:
    """任何上传/通报类步骤都不许把自身失败调成"无所谓"。"""
    offenders = [
        label
        for file, label, step in _steps()
        if (step.get("with") or {}).get("fail_ci_if_error") is False
    ]
    assert not offenders, (
        "这些步骤设置了 fail_ci_if_error: false —— 它们失败时 job 照绿，"
        "等于假门禁（codecov 那步就是这样沉默地跑了很久）。"
        f"要么让它真失败，要么删掉：{offenders}"
    )


def test_continue_on_error_steps_are_all_registered() -> None:
    """`continue-on-error: true` 只允许出现在登记过、且写明理由的步骤上。"""
    seen = set()
    unregistered = []
    for file, label, step in _steps():
        if step.get("continue-on-error") is not True:
            continue
        name = label.split(" / ", 1)[1]
        key = (file, name)
        seen.add(key)
        if key not in CONTINUE_ON_ERROR_ALLOWED:
            unregistered.append(f"{file}: {name}")
    assert not unregistered, (
        "未登记理由的 continue-on-error 步骤（新增它等于新增一个可吞失败的口子）："
        f"{unregistered}。若确有必要，请在 CONTINUE_ON_ERROR_ALLOWED 里写明为什么可以吞。"
    )


def test_allowlist_entries_are_not_stale() -> None:
    """登记过但已不存在的条目也要报错，避免豁免清单变成僵尸豁免。"""
    present = {(f, label.split(" / ", 1)[1]) for f, label, s in _steps()
               if s.get("continue-on-error") is True}
    stale = set(CONTINUE_ON_ERROR_ALLOWED) - present
    assert not stale, f"豁免清单里有已不存在于工作流的条目，请删除：{sorted(stale)}"
