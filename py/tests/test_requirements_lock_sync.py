"""requirements.lock 的分节必须逐条镜像 pyproject.toml。

为什么要有这条测试：lock 文件自己的表头写着「Source of truth for DIRECT deps:
pyproject.toml. This file mirrors it」，但没有任何东西强制它 —— 结果 dependabot #13
抬了 `pydantic-settings / uvicorn / mmh3` 的 pin，只改了 pyproject 与 requirements.txt，
lock 仍停在 2.5.2 / 0.30.6 / 5.2.1；照 lock 装环境的人会拿到旧版本。

分段用显式的 `# END ...` 标记：extras 段的标题只是普通注释，靠"下一条注释"划界会把
enterprise 条目误读进直依赖段。

不用 tomllib：CI 矩阵含 Python 3.10，而 `tomllib` 是 3.11 才进标准库的；这里只用一个
针对 `name = [ ... ]` 数组的窄解析器（不承诺通用 TOML 支持）。
"""

from __future__ import annotations

import re
from pathlib import Path

PY = Path(__file__).resolve().parents[1]
PYPROJECT = PY / "pyproject.toml"
LOCK = PY / "requirements.lock"
REQUIREMENTS_TXT = PY / "requirements.txt"

DIRECT_HEADER = "# ── Direct dependencies (from pyproject.toml)"
DIRECT_END = "# END DIRECT DEPENDENCIES"
ENTERPRISE_HEADER = "# Enterprise / optional backend drivers"
ENTERPRISE_END = "# END ENTERPRISE DEPENDENCIES"

_ARRAY_CLOSE = re.compile(r"^\]", re.MULTILINE)


def _pkg_name_raw(spec: str) -> str:
    match = re.match(r"^([A-Za-z0-9._-]+)", spec.strip())
    return match.group(1) if match else spec.strip()


def _canonical(spec: str) -> str:
    return _pkg_name_raw(spec).lower().replace("_", "-")


def _spec_body(spec: str) -> str:
    """约束部分（去掉包名），用于比较两处声明是否一致。"""
    return spec.strip()[len(_pkg_name_raw(spec)):].strip()


def _array(name: str, text: str) -> list[str]:
    """取 `name = [ ... ]` 数组里的字符串条目。"""
    start = re.search(r"^" + re.escape(name) + r"\s*=\s*\[\n", text, re.MULTILINE)
    assert start, f"pyproject.toml 里找不到数组 {name!r}"
    rest = text[start.end():]
    end = _ARRAY_CLOSE.search(rest)
    assert end, f"数组 {name!r} 没有闭合的 ]"
    return [m.strip() for m in re.findall(r'"([^"]+)"', rest[:end.start()])]


def _as_map(specs: list[str]) -> dict[str, str]:
    return {_canonical(s): s for s in specs}


def _lock_block(lines: list[str], header: str, end_marker: str) -> dict[str, str]:
    start = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
    if start is None:
        raise AssertionError(f"requirements.lock 缺少分节起始标记 {header!r}")
    stop = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                 if line.strip() == end_marker), None)
    if stop is None:
        raise AssertionError(f"requirements.lock 的 {header!r} 段缺少结束标记 {end_marker!r}")
    body = [line.strip() for line in lines[start + 1:stop]
            if line.strip() and not line.strip().startswith("#")]
    return _as_map(body)


def test_lock_direct_section_mirrors_pyproject():
    want = _as_map(_array("dependencies", PYPROJECT.read_text(encoding="utf-8")))
    got = _lock_block(LOCK.read_text(encoding="utf-8").splitlines(), DIRECT_HEADER, DIRECT_END)

    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    assert not missing and not extra, (
        "requirements.lock 的直依赖段与 pyproject 不一致："
        f"lock 缺 {missing}；lock 多 {extra}"
        "（extras / dev 依赖请放到对应分节，不要混进直依赖段）"
    )
    drifted = {n: (want[n], got[n]) for n in want if _spec_body(want[n]) != _spec_body(got[n])}
    assert not drifted, f"requirements.lock 与 pyproject 的约束漂移：{drifted}"


def test_lock_enterprise_section_mirrors_pyproject():
    """extras 也不能漂：enterprise 段同样逐条镜像 pyproject。"""
    want = _as_map(_array("enterprise", PYPROJECT.read_text(encoding="utf-8")))
    got = _lock_block(LOCK.read_text(encoding="utf-8").splitlines(),
                      ENTERPRISE_HEADER, ENTERPRISE_END)
    assert set(want) == set(got), (
        f"lock 的 enterprise 段与 pyproject 不一致：缺 {sorted(set(want) - set(got))}，"
        f"多 {sorted(set(got) - set(want))}"
    )
    drifted = {n: (want[n], got[n]) for n in want if _spec_body(want[n]) != _spec_body(got[n])}
    assert not drifted, f"lock 的 enterprise 段约束漂移：{drifted}"


def test_requirements_txt_covers_every_direct_dependency():
    """部署清单可以比 pyproject 更严（精确 pin），但不能少包。"""
    want = set(_as_map(_array("dependencies", PYPROJECT.read_text(encoding="utf-8"))))
    txt = {
        _canonical(line.strip())
        for line in REQUIREMENTS_TXT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = sorted(want - txt)
    assert not missing, f"requirements.txt 缺少 pyproject 声明的直依赖: {missing}"
