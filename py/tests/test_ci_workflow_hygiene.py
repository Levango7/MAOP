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


# ── 报表生产者与上传者的路径必须同源（2026-10-16 一次"红得查不动"换来的）────
#
# 主干 run 38902904241：`gitleaks git` 打出 `leaks found: 69` 判红，但那 69 条到底是
# 什么 —— 没有任何地方能看到。原因不是扫描没产出报表，而是扫描步写在**仓库根**
# （它没有 working-directory，`gitleaks git .` 必须在根上才覆盖全库），上传步却去找
# `py/gl-history.sarif`，再叠上 `if-no-files-found: ignore` ⇒ 报表被"静默地没上传"，
# 上传步自己还是 success。安全门禁的证据链断在这一环，和门禁本身失灵一样糟。


def _report_producers() -> list[tuple[str, str, str, str, dict]]:
    """声明 `--report-path <file>` 的步骤：(workflow, job, 报表相对路径, 生效工作目录, step)。

    工作目录口径取"步骤级 working-directory 优先，其次作业级 defaults.run"，
    因为报表实际落在哪个目录就是由它决定的。
    """
    import re

    out = []
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job_name, job in (doc.get("jobs") or {}).items():
            job_wd = str(((job.get("defaults") or {}).get("run") or {}).get("working-directory") or "")
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                m = re.search(r"--report-path\s+(\S+)", run)
                if m:
                    wd = str(step.get("working-directory") or job_wd)
                    out.append((wf.name, str(job_name), m.group(1), wd, step))
    return out


def test_every_report_producer_has_an_uploader() -> None:
    """先钉"这条守卫不是空跑"：每个 `--report-path` 都必须有配对的上传步。

    判据本身要非空 —— 一旦有人把 `--report-path` 改名或删掉上传步，这里要红，
    而不是让下面两条一致性检查悄悄变成"零对象全称命题恒真"。
    """
    producers = _report_producers()
    assert producers, "没有任何步骤声明 --report-path：这条守卫已经空转，请确认它还需不需要"
    uploads = {
        (file, job.split(" / ", 1)[0])
        for file, job, step in _steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    }
    orphaned = [f"{f} / {j} -> {p}" for f, j, p, _wd, _s in producers if (f, j) not in uploads]
    assert not orphaned, f"这些扫描产了报表，但同作业里没有上传步：{orphaned}"


def test_report_path_and_upload_path_are_the_same_file() -> None:
    """报表落在哪个目录由扫描步的 working-directory 决定；上传步必须按同一个口径找它。"""
    all_steps = _steps()
    offenders = []
    for file, job, rel_path, wd, _step in _report_producers():
        produced = f"{wd}/{rel_path}" if wd else rel_path
        uploads = [
            str(((up.get("with") or {}).get("path")) or "")
            for f2, label, up in all_steps
            if f2 == file
            and label.startswith(f"{job} /")
            and str(up.get("uses", "")).startswith("actions/upload-artifact")
        ]
        assert uploads, f"{file} {job}: 声明了 --report-path 却没有上传步（配对守卫漏了）"
        if produced.rstrip("/") not in [u.rstrip("/") for u in uploads]:
            offenders.append(f"{file} {job}: 扫描写 {produced!r}，上传步找的是 {uploads}")
    assert not offenders, "报表路径与上传路径不一致 ⇒ 报表永远传不上去：" + "; ".join(offenders)


def test_security_report_upload_must_not_ignore_a_missing_report() -> None:
    """安全类报表不许用 `if-no-files-found: ignore`：报表缺席本身就是一次事故，要看得见。

    只约束"扫描器自己写出的报表"那几条；覆盖率/前端产物之类的可选上传不在此列
    （它们缺席没有取证价值，红一次反而扰民）。
    """
    offenders = []
    covered_jobs = {(f, j) for f, j, _p, _wd, _s in _report_producers()}
    for file, label, step in _steps():
        if not str(step.get("uses", "")).startswith("actions/upload-artifact"):
            continue
        job = label.split(" / ", 1)[0]
        if (file, job) not in covered_jobs:
            continue
        if ((step.get("with") or {}).get("if-no-files-found")) == "ignore":
            offenders.append(f"{file} {label}")
    assert not offenders, f"扫描报表的上传步把'没有文件'调成了无所谓：{offenders}"
