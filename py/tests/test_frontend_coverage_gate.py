"""前端覆盖率门禁的守卫（为什么存在、被改坏要当场红）。

背景（2026-09-27 实测）：

1. `package.json` 里有 `test:coverage`，但 CI 的 `Frontend Build` 只跑
   `npm ci` / `lint` / `typecheck` / `npm test` / `vite build` —— **从不跑覆盖率**。
   于是阈值达标与否从来没人验证过（2026-09-17 抬到 60/60/50/60 时没量过，
   2026-09-27 首次实测即 exit=1）：一道不会执行的门禁。
2. 首次实测它：statements 61.09 / branches 46.2 / functions 56.1 / lines 64.03，
   exit=1（functions、branches 根本达不到）。现在的阈值是"实测值留 0.5pp 抖动余量"。
3. `dangerouslyIgnoreUnhandledErrors: true` 会把"worker 起不来"吞成绿灯：一次冷启动
   31 条 `[vitest-pool]: Failed to start forks worker`，实际只跑 25/56 文件、132/478 测试，
   vitest 自己都打印 "This might cause false positive tests"。所以这一步同时是
   "测试确实都跑了"的机械哨兵 —— 漏跑必然把覆盖率打穿下限。

这些事实都要能被用例钉住，否则下一次有人"顺手改回去"没人拦。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VITEST_CFG = REPO_ROOT / "dashboard-enterprise" / "vitest.config.js"

# 2026-09-27 实测的下限（含 0.5pp 抖动余量）。只许往上抬。
FLOORS = {"lines": 63.5, "functions": 55.5, "branches": 45.5, "statements": 60.5}


def _thresholds() -> dict[str, float]:
    src = VITEST_CFG.read_text(encoding="utf-8")
    block = re.search(r"thresholds:\s*\{(.*?)\}", src, re.DOTALL)
    assert block, "vitest.config.js 里找不到 coverage.thresholds —— 门禁被整段删掉了？"
    found = {k: float(v) for k, v in re.findall(r"(\w+):\s*([0-9.]+)", block.group(1))}
    missing = set(FLOORS) - set(found)
    assert not missing, f"thresholds 少了维度：{missing}"
    return found


def test_ci_runs_the_frontend_coverage_gate() -> None:
    """门禁必须真的在 CI 里，否则阈值就是一行注释。"""
    ci = CI.read_text(encoding="utf-8")
    assert "npm run test:coverage" in ci, (
        "ci.yml 不再跑 `npm run test:coverage` —— 前端覆盖率门禁退化成'配了但不执行'"
    )
    step = re.search(
        r"- name: Frontend coverage gate\n(?:.*\n)*?\s*run: npm run test:coverage", ci
    )
    assert step, "`npm run test:coverage` 必须待在名为 'Frontend coverage gate' 的显式步骤里"


def test_thresholds_are_not_lowered_below_measured_floors() -> None:
    """阈值只许升。要降必须连带改写本文件的 FLOORS 并说明理由（等于强制留痕）。"""
    th = _thresholds()
    below = {k: (th[k], FLOORS[k]) for k in FLOORS if th[k] < FLOORS[k]}
    assert not below, f"这些覆盖率阈值被调到实测下限之下（不许为变绿降强度）：{below}"


def test_unhandled_errors_are_not_globally_ignored() -> None:
    """`dangerouslyIgnoreUnhandledErrors: true` 会把"半数测试文件没跑"变成绿灯，禁止再引入。

    注意按**配置键**匹配，不按裸字符串 —— 配置文件里有一段解释这个键为什么被移除的注释，
    用裸串会误伤（写这条时先踩了自己一次）。
    """
    src = VITEST_CFG.read_text(encoding="utf-8")
    enabled = re.search(r"^\s*dangerouslyIgnoreUnhandledErrors\s*:\s*true\s*,?", src, re.MULTILINE)
    assert not enabled, (
        "vitest.config.js 又开了 dangerouslyIgnoreUnhandledErrors: true —— 冷启动 worker 起不来时"
        "（实测 31 条 Failed to start forks worker、只跑 25/56 文件）它会让 CI 报绿"
    )


def test_coverage_command_exists_in_package_json() -> None:
    import json

    pkg = json.loads((REPO_ROOT / "dashboard-enterprise" / "package.json").read_text(encoding="utf-8"))
    script = pkg.get("scripts", {}).get("test:coverage")
    assert script and "--coverage" in script, (
        "CI 引用的 `npm run test:coverage` 脚本不存在或没开 --coverage"
    )
