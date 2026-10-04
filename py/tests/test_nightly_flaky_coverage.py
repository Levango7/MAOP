"""Nightly `flaky-detection` 作业的覆盖面守卫（2026-10-04）。

为什么要有这份守卫：该作业自称"暴露时好时坏的不稳定测试"，但原实现只在
**ubuntu-latest + 3.13** 上串行跑 3 遍 —— 而实测的两家凶（`TestCallSyncFallback`）
只出现在 **macos-latest/3.13** 与 **windows-latest/3.12**。也就是说，**它检测的
平台与真出问题的平台不相交**：一个结构上永远不会红的探针。

本文件把"必须覆盖哪些腿、必须关掉重试、必须复刻 CI 的并发配置"钉进仓库，
防止覆盖面被无声改窄。同类教训见 docs/ci-gates.md §7：门禁本身也要被证明"能红"。

两点设计说明：

* `--reruns=0` 是这套检测的**前提**。ci.yml 的 pytest 腿带 `--reruns=3`，会把
  flaky 重试成绿；只有关掉重试，'时好时坏'才可见。所以这里对每条腿都断言它。
* 渲染出的脚本要做 `bash -n` 语法检查 —— 矩阵值插错位置（例如把 `-n 2` 插进
  引号里）会让整条腿静默跑成别的东西。没装 bash 的平台（Windows 裸机）跳过**这一
  项子断言**，但插值完整性是纯 Python 检查，任何平台都会跑。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
NIGHTLY = WORKFLOWS / "nightly.yml"
CI = WORKFLOWS / "ci.yml"

JOB_ID = "flaky-detection"
# 实测出过问题的腿（macos/windows 各一）＋ 基线腿。
# 历史证据：run 36792737281（macos-latest/3.13）、nightly + windows-latest/3.12。
REQUIRED_LEGS = {
    ("ubuntu-latest", "3.13"),
    ("macos-latest", "3.13"),
    ("windows-latest", "3.12"),
}


def _load(path: Path) -> dict:
    # 工作区在 Windows 上是 CRLF 检出；YAML 块标量会把 \r 一起带进 run 脚本，
    # 所以统一归一化后再断言（提交进 git 的 blob 本来就是 LF，见 core.autocrlf）。
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return yaml.safe_load(text)


def _flaky_job() -> dict:
    return _load(NIGHTLY)["jobs"][JOB_ID]


def _legs() -> list[dict]:
    return _flaky_job()["strategy"]["matrix"]["include"]


def _run_steps() -> list[dict]:
    return [s for s in _flaky_job()["steps"] if "run" in s]


def _render(script: str, leg: dict) -> str:
    for key, value in leg.items():
        script = script.replace("${{ matrix." + key + " }}", str(value))
    return script


def _repeated_step() -> dict:
    for step in _run_steps():
        if "${{ matrix.repeats }}" in step["run"]:
            return step
    raise AssertionError("找不到按矩阵重复执行的步骤 —— 选择器失效，下面会变成空断言")


def _serial_step() -> dict:
    for step in _run_steps():
        if "not slow and serial" in step["run"]:
            return step
    raise AssertionError("找不到 serial 步骤")


def test_matrix_covers_the_platforms_where_flakes_were_observed() -> None:
    """必须覆盖真出过问题的腿，而不只是"某个"平台。"""
    covered = {(leg["os"], str(leg["python-version"])) for leg in _legs()}
    missing = REQUIRED_LEGS - covered
    assert not missing, (
        f"flaky-detection 矩阵没覆盖实测出过问题的平台/版本：{sorted(missing)}。"
        "把出问题的腿排除在外，等于把探针架在看不到症状的地方。"
    )


def test_every_leg_disables_retries() -> None:
    """每条腿都必须 `--reruns=0`：带重试就跑不出"时好时坏"。"""
    script = _repeated_step()["run"]
    assert "--reruns=0" in script, (
        "重复执行步骤没有关掉重试（--reruns=0）——被重试救回来的失败会让整套"
        "检测恒绿，正是本仓 2026-09 之前 `|| true` 的同形问题"
    )
    assert "--reruns=3" not in script


def test_every_leg_repeats_at_least_twice() -> None:
    """至少两遍：只跑一遍区分不了"一直坏"和"偶尔坏"。"""
    for leg in _legs():
        repeats = int(leg["repeats"])
        assert repeats >= 2, f"{leg['os']} 只跑 {repeats} 遍，检测不出 flaky"


def test_windows_leg_is_serial_and_others_are_parallel() -> None:
    """复刻 ci.yml 的真实并发配置：Windows 串行，其余并行。

    ci.yml 用 `-n 0` 跑 Windows，原因是 xdist 3.8.0 在 Windows 上有 execnet worker
    竞态（INTERNALERROR: KeyError: WorkerController）。flaky 作业如果只在并行面
    观察，就漏掉了 Windows 那条腿的真实配置。
    """
    for leg in _legs():
        xdist = str(leg["xdist"])
        if leg["os"] == "windows-latest":
            assert xdist == "-n 0", f"Windows 腿必须是串行（-n 0），实际 {xdist!r}"
        else:
            assert xdist.startswith("-n ") and xdist != "-n 0", (
                f"{leg['os']} 腿必须是并行（CI 用 -n 2），实际 {xdist!r}"
            )


def test_serial_marker_tests_are_also_observed_without_retries() -> None:
    """`serial` 标记那一族也要在无重试下被观察一次。

    ci.yml 把用例拆成 `not slow and not serial` 与 `not slow and serial` 两步；
    flaky 作业原先只跑前者，而 serial 那批（共享全局状态 / 固定端口）恰恰是最容易
    flaky 的一族 —— 它们从没在无重试条件下被重复观察过。
    """
    script = _serial_step()["run"]
    assert "--reruns=0" in script, "serial 步骤必须关掉重试"
    assert "-n 0" in script, "serial 步骤必须串行"


def test_every_leg_is_a_real_ci_leg() -> None:
    """矩阵里的每条腿都必须是 ci.yml 真跑过的组合。

    否则 nightly 会去验证一个 CI 从不执行的组合 —— 在那里发现的问题无处复现，
    而真正发布的组合反而没被 nightly 观察。
    """
    ci = _load(CI)["jobs"]["test"]["strategy"]["matrix"]
    oses = list(ci["os"])
    versions = [str(v) for v in ci["python-version"]]
    excluded = {(e["os"], str(e["python-version"])) for e in ci.get("exclude") or []}

    real = {
        (o, v) for o in oses for v in versions
        if (o, v) not in excluded
    }
    for leg in _legs():
        pair = (leg["os"], str(leg["python-version"]))
        assert pair in real, (
            f"flaky-detection 矩阵里的 {pair} 不在 ci.yml 的 pytest 矩阵内 —— "
            "nightly 不该验证 CI 从不执行的组合"
        )


def test_rendered_scripts_are_fully_interpolated_and_parse() -> None:
    """矩阵值必须全部插进脚本，且渲染结果是真的 bash。

    插错位置（例如把 `-n 2` 塞进引号内）会让整条腿静默跑成别的东西 —— 这正是
    "看不见的失效"。插值完整性是纯 Python 断言；`bash -n` 在没有 bash 的平台上
    跳过（CI 的 ubuntu 腿一定会跑到）。
    """
    bash = shutil.which("bash")
    for step in _run_steps():
        script = step["run"]
        if "${{ matrix" not in script:
            continue
        for leg in _legs():
            rendered = _render(script, leg)
            assert "${{" not in rendered, (
                f"渲染后仍残留占位符（矩阵键与脚本不匹配）：{rendered[:200]!r}"
            )
            assert leg["xdist"] in rendered, (
                f"xdist 值 {leg['xdist']!r} 没出现在渲染结果里：{rendered[:200]!r}"
            )
            if bash is None:
                continue
            proc = subprocess.run(
                [bash, "-n"],
                input=rendered.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            assert proc.returncode == 0, (
                f"{leg['os']}/py{leg['python-version']} 渲染出的脚本过不了 bash -n："
                f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
            )
