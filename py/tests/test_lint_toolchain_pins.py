"""lint 工具链版本口径必须同源：CI 的显式 pin == dev extra 的精确 pin。

为什么要有这条：CI 的 lint 作业手写 `pip install "ruff==X" "mypy==Y"`，而
`pyproject.toml` 的 dev extra 长期写 `ruff>=0.4` / `mypy>=1.10`。两边各说各话时，
本机与 nightly（`pip install -e ./py[dev]`）拿到的是当时最新的 ruff，规则集比 CI
那版更严或更松 —— 2026-10-03 的两次主干红就是这种形状（F401 只在 CI 的老 ruff 上
复现）。把 dev extra 收成精确 pin 只解决一次，抬版本时仍会漂；本用例把"两处相等"
变成可执行事实，漂移当场转红而不是变成一次莫名的 lint 失败。

不引 tomllib：CI 矩阵含 Python 3.10，`tomllib` 是 3.11 才进标准库。这里只用与
`test_requirements_lock_sync.py` 同款的窄解析器（针对 `name = [ ... ]` 数组）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "py" / "pyproject.toml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_TOOLS = ("ruff", "mypy")

_ARRAY_CLOSE = re.compile(r"^\]", re.MULTILINE)
_LINT_INSTALL_RE = re.compile(r"""pip install\s+"(ruff|mypy)==([^"\s]+)"\s+"(ruff|mypy)==([^"\s]+)""")


def _pkg_name_raw(spec: str) -> str:
    match = re.match(r"^([A-Za-z0-9._-]+)", spec.strip())
    return match.group(1) if match else spec.strip()


def _canonical(spec: str) -> str:
    return _pkg_name_raw(spec).lower().replace("_", "-")


def _spec_body(spec: str) -> str:
    return spec.strip()[len(_pkg_name_raw(spec)):].strip()


def _array(name: str, text: str) -> list[str]:
    """取 `name = [ ... ]` 数组里的字符串条目。"""
    start = re.search(r"^" + re.escape(name) + r"\s*=\s*\[\n", text, re.MULTILINE)
    assert start, f"pyproject.toml 里找不到数组 {name!r}"
    rest = text[start.end():]
    end = _ARRAY_CLOSE.search(rest)
    assert end, f"数组 {name!r} 没有闭合的 ]"
    return [m.strip() for m in re.findall(r'"([^"]+)"', rest[:end.start()])]


def dev_pin_problems(dev_specs: list[str]) -> list[str]:
    """dev extra 里 ruff/mypy 必须是**存在**且**精确 ==**。返回问题列表（空=合格）。"""
    problems: list[str] = []
    found: dict[str, str] = {}
    for spec in dev_specs:
        name = _canonical(spec)
        if name in _TOOLS:
            found[name] = _spec_body(spec)
    for tool in _TOOLS:
        if tool not in found:
            problems.append(f"dev extra 未声明 {tool}")
        elif not found[tool].startswith("=="):
            problems.append(
                f"dev extra 的 {tool} 不是精确 pin：{tool}{found[tool]} "
                "（开区间会让本机装到比 CI 新的版本，规则集不同 ⇒ 主干凭空红）"
            )
    return problems


def ci_pin_map(ci_text: str) -> dict[str, str]:
    """从 ci.yml 提取 lint 作业的显式 pin。多处不一致或缺失都算问题（fail closed）。"""
    found: dict[str, str] = {}
    for m in _LINT_INSTALL_RE.finditer(ci_text):
        pairs = {m.group(1): m.group(2), m.group(3): m.group(4)}
        for tool, ver in pairs.items():
            if tool in found and found[tool] != ver:
                raise AssertionError(
                    f"ci.yml 里 {tool} 出现多个不同 pin（{found[tool]} vs {ver}）——"
                    "先把它收敛成一处，否则本守卫无法判定哪份是权威"
                )
            found[tool] = ver
    missing = [t for t in _TOOLS if t not in found]
    if missing:
        raise AssertionError(
            f"ci.yml 里找不到 {missing} 的 `pip install \"<tool>==<ver>\"` 钉版行。"
            "若你改了这一行，请同步改 dev extra 并更新本用例的正则"
        )
    return found


def parity_problems(dev_specs: list[str], ci_pins: dict[str, str]) -> list[str]:
    """dev extra 与 CI pin 的版本一致性。"""
    problems = dev_pin_problems(dev_specs)
    if problems:
        return problems
    dev_versions = {
        _canonical(s): _spec_body(s)[2:] for s in dev_specs if _canonical(s) in _TOOLS
    }
    for tool in _TOOLS:
        if dev_versions[tool] != ci_pins[tool]:
            problems.append(
                f"{tool} 口径漂移：dev extra =={dev_versions[tool]}，"
                f"ci.yml =={ci_pins[tool]}（抬版本请同一笔改两处）"
            )
    return problems


def test_dev_extra_pins_lint_tools_exactly():
    problems = dev_pin_problems(_array("dev", PYPROJECT.read_text(encoding="utf-8")))
    assert not problems, problems


def test_dev_extra_and_ci_pin_the_same_versions():
    dev_specs = _array("dev", PYPROJECT.read_text(encoding="utf-8"))
    ci_pins = ci_pin_map(CI_YML.read_text(encoding="utf-8"))
    assert not parity_problems(dev_specs, ci_pins)


# ── 反向变异：证明上面两条不是恒绿判据 ──────────────────────────────

def test_loose_dev_pin_is_rejected():
    # 回归本用例要防的那件事：dev extra 回到开区间
    problems = dev_pin_problems(["ruff>=0.4", "mypy>=1.10"])
    assert len(problems) == 2, problems
    assert all("不是精确 pin" in p for p in problems), problems


def test_missing_tool_in_dev_extra_is_rejected():
    problems = dev_pin_problems(["ruff==0.16.3"])
    assert problems == ["dev extra 未声明 mypy"], problems


def test_version_drift_between_two_sources_is_rejected():
    dev_specs = ["ruff==0.16.3", "mypy==2.3.1"]
    drifted = {"ruff": "0.16.9", "mypy": "2.3.1"}
    problems = parity_problems(dev_specs, drifted)
    assert len(problems) == 1 and "ruff 口径漂移" in problems[0], problems


def test_ci_without_pin_line_fails_closed():
    # 拿不到输入不等于通过
    try:
        ci_pin_map("      - run: pip install ruff mypy\n")
    except AssertionError as exc:
        assert "找不到" in str(exc), str(exc)
    else:
        raise AssertionError("ci.yml 没有钉版行时本守卫应当 fail closed，而不是放行")


def test_ci_with_conflicting_pins_is_rejected():
    text = (
        '      - run: pip install "ruff==0.16.3" "mypy==2.3.1"\n'
        '      - run: pip install "ruff==0.17.0" "mypy==2.3.1"\n'
    )
    try:
        ci_pin_map(text)
    except AssertionError as exc:
        assert "多个不同 pin" in str(exc), str(exc)
    else:
        raise AssertionError("ci.yml 出现两处不一致的 pin 时应当拒绝判定")
