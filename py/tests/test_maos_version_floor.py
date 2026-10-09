"""MAOP ↔ MAOS 版本兼容地板的守卫（2026-10-09 建）。

为什么要有这份守卫：MAOP 与 MAOS 共享 `maop.enterprise` 命名空间，且 MAOP 的
dashboard 直接 import 它（`maop/dashboard/routers/notifications.py` 导入
`maop.enterprise.notification.models`）——两者是**双向锁步**的，但这个约束
**无法用 pip 依赖表达**（`maop-enterprise` 是私有包，不在 `maop-orchestrator`
的依赖里，也不会从 PyPI 解析）。于是它只能靠文档声明；而无人守的声明会在某次
重构里被静默删掉，"已发布的 MAOP 5.2.0 + MAOS 5.2.3 不兼容"那类事故就会重现。

本文件钉住三件事（都是**跨站点一致性**，不硬编码具体版本号 —— 地板移动时
只需改声明本身，无需改测试）：

1. README 有「版本兼容地板」声明，且其中可解析出 `enterprise-vX.Y.Z`；
2. CHANGELOG 的 5.2.1 段提到同一个最低版本（两处口径必须一致）；
3. `pyproject.toml` 的 `enterprise` extra 带注释说明"为何无法表达该约束"，
   避免有人误以为"extra 里没有 maop-enterprise 就是漏了"。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"
PYPROJECT = REPO / "py" / "pyproject.toml"

_FLOOR_RE = re.compile(r"enterprise-v(\d+\.\d+\.\d+)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _readme_floor() -> str:
    text = _read(README)
    marker = "版本兼容地板"
    assert marker in text, (
        "README 里找不到「版本兼容地板」一节 —— 该声明是 MAOP↔MAOS 锁步关系的唯一"
        "载体（pip 依赖表达不了），删除它等于把兼容性约束从项目里抹掉。"
    )
    section = text.split(marker, 1)[1]
    # 只看本节前 2000 字符，避免误取文中别处的 enterprise-v 引用
    match = _FLOOR_RE.search(section[:2000])
    assert match, "「版本兼容地板」一节里没有 `enterprise-vX.Y.Z` 形式的最低版本"
    return match.group(1)


def test_readme_declares_maos_version_floor() -> None:
    assert _readme_floor()


def test_changelog_states_the_same_floor() -> None:
    """CHANGELOG 与本节的声明必须指向同一个版本，否则读者不知道该信哪个。"""
    floor = _readme_floor()
    changelog = _read(CHANGELOG)
    assert "## [5.2.1]" in changelog, "CHANGELOG 里找不到 5.2.1 段，本用例前提已变"
    assert f"enterprise-v{floor}" in changelog, (
        f"README 声明的最低 MAOS 版本是 enterprise-v{floor}，但 CHANGELOG 里没有出现该版本号。"
    )


def test_pyproject_explains_why_the_floor_is_not_a_dependency() -> None:
    """extra 里没有 `maop-enterprise` 是**有意**的，注释必须说清，否则会被当成遗漏补上。"""
    text = _read(PYPROJECT)
    assert "enterprise = [" in text
    head = text.split("enterprise = [", 1)[1][:600]
    assert "maop-enterprise" in head, (
        "`[enterprise]` extra 顶部注释应说明企业代码来自私有包 `maop-enterprise`，"
        "且无法在此表达依赖约束"
    )
    assert "README" in head, "该注释应指向 README 的「版本兼容地板」节"
