"""T2.2 (2026-09-30): requirements.frozen.txt 真锁守卫。

此前 requirements.lock 只是"参考"（直依赖窄范围 + 传递依赖 ``>=`` 下界），
CHANGELOG 台账自认"不是真锁"——照它装环境的漏洞结论随解析漂移。本文件由
uv 全量解析生成（base + enterprise extra，universal），全部精确 ``==``。

守卫内容：
  1. 文件存在且每条需求都是精确 pin（``name==version``，允许环境标记）；
  2. pyproject ``[project].dependencies`` 与 ``enterprise`` extra 的每个名字
     都被覆盖（不存在"声明了却锁不住"的依赖）。

与 pyproject 的**内容级**漂移（版本选型变化）由 CI 的 ``lock-drift`` 作业
重新编译比对；本文件只锁"形状"与"覆盖面"，两者互补、不重复。
"""
from __future__ import annotations

import re
from pathlib import Path

# tests/ 不是包，跨测试模块 import 不可靠（ModuleNotFoundError 实测）——
# 从 test_requirements_lock_sync.py 复制三个解析帮手（保持口径一致，勿单边改动）。
PY_DIR = Path(__file__).resolve().parent.parent
LOCK = PY_DIR / "requirements.frozen.txt"
PYPROJECT = PY_DIR / "pyproject.toml"

_ARRAY_CLOSE = re.compile(r"^\]", re.MULTILINE)

# name==version（可选环境标记；uv 输出里溯源信息是独立注释行，
# 真正带标记的行形如 `foo==1.0 ; python_version < "3.11"`）
_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)(?:\s*;.*)?$")


def _pkg_name_raw(spec: str) -> str:
    match = re.match(r"^([A-Za-z0-9._-]+)", spec.strip())
    return match.group(1) if match else spec.strip()


def _canonical(name: str) -> str:
    return _pkg_name_raw(name).strip().lower().replace("_", "-")


def _array(name: str, text: str) -> list[str]:
    """取 pyproject.toml 里 `name = [ ... ]` 数组的字符串条目。"""
    start = re.search(r"^" + re.escape(name) + r"\s*=\s*\[\n", text, re.MULTILINE)
    assert start, f"pyproject.toml 里找不到数组 {name!r}"
    rest = text[start.end():]
    end = _ARRAY_CLOSE.search(rest)
    assert end, f"数组 {name!r} 没有闭合的 ]"
    return [m.strip() for m in re.findall(r'"([^"]+)"', rest[:end.start()])]


def _lock_entries() -> dict[str, str]:
    """解析 frozen 锁为 {canonical_name: version}；顺带断言每行都是精确 pin。"""
    entries: dict[str, str] = {}
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _REQ_RE.match(line)
        assert m is not None, (
            f"frozen 锁里出现非精确 pin 行（形状守卫被破坏）: {line!r}"
        )
        entries[_canonical(m.group(1))] = m.group(2)
    return entries


def _declared_names() -> set[str]:
    """pyproject 直依赖 + enterprise extra 的名字集合（canonical 化）。

    ``_array`` 按行首 ``name = [`` 匹配——与 test_requirements_lock_sync.py
    同一口径（该测试已验证 ``enterprise = [`` 在 pyproject 里唯一）。
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    specs = _array("dependencies", text) + _array("enterprise", text)
    return {_canonical(_pkg_name_raw(s)) for s in specs if s.strip()}


def test_frozen_lock_exists_and_nonempty():
    assert LOCK.exists(), (
        "requirements.frozen.txt 不存在——真锁文件被删，SBOM/pip-audit 失去输入。"
        "运行: uv pip compile pyproject.toml --extra enterprise --universal --header -o requirements.frozen.txt"
    )
    assert len(_lock_entries()) >= 20, "frozen 锁条目异常偏少，疑似生成截断"


def test_every_requirement_line_is_exact_pin():
    # _lock_entries 内的断言即本用例主体；这里再确认解析出了条目（防"全注释空文件"假绿）
    entries = _lock_entries()
    assert entries, "frozen 锁没有任何需求行"


def test_frozen_lock_covers_all_declared_names():
    entries = _lock_entries()
    declared = _declared_names()
    missing = declared - set(entries)
    assert not missing, (
        f"pyproject 声明了但 frozen 锁没覆盖的依赖: {sorted(missing)} —— "
        "重新生成: uv pip compile pyproject.toml --extra enterprise --universal --header -o requirements.frozen.txt"
    )
