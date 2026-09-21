"""e2e 专用 conftest —— 只做企业版可选性守卫。

为什么单独一个文件：CI 的 e2e 步骤用 ``--confcutdir=tests/e2e`` 运行
（见 .github/workflows/ci.yml "Run e2e tests"），这是**有意的** —— e2e 需要
模块级 ``MAOP_AUTH=1`` 生效，不能被 tests/conftest.py 里的 auth-off pinning
覆盖。因此 ``tests/conftest.py`` 在此不会被加载，企业版守卫必须在 e2e 目录内
再写一份。

守卫逻辑与 tests/conftest.py 保持一致：maop.enterprise 不可用时，把依赖它的
测试文件加入 collect_ignore（跳过收集），避免收集阶段 ImportError 让整个
e2e 步骤失败。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ENTERPRISE_MARKERS = ("maop.enterprise", "maop-enterprise")
_ENTERPRISE_INDIRECT: tuple[str, ...] = ()

try:
    _ent_spec = importlib.util.find_spec("maop.enterprise")
except (ImportError, ValueError):  # ModuleNotFoundError 是 ImportError 子类
    _ent_spec = None

if _ent_spec is None:
    _e2e_root = Path(__file__).resolve().parent
    _ignored = {
        str(p.relative_to(_e2e_root))
        for p in _e2e_root.rglob("test_*.py")
        if any(
            m in p.read_text(encoding="utf-8", errors="ignore")
            for m in _ENTERPRISE_MARKERS
        )
    }
    _ignored.update(_ENTERPRISE_INDIRECT)
    collect_ignore = sorted(_ignored)
