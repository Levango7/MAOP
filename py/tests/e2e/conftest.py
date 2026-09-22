"""e2e 专用 conftest —— 企业版可选性守卫 + 从 tests/conftest.py 迁移的 fixture。

## 为什么单独一个文件

CI 的 e2e 步骤用 ``--confcutdir=tests/e2e`` 运行（见 .github/workflows/ci.yml
"Run e2e tests"），这是**有意的** —— e2e 需要模块级 ``MAOP_AUTH=1`` 生效，
不能被 tests/conftest.py 里的 auth-off pinning 覆盖。

副作用：``tests/conftest.py`` 在此**不会被加载**，于是
1. 企业版守卫失效（test_delete_operations.py 收集期 ImportError）；
2. 定义在 tests/conftest.py 的 fixture（``evolution_loop_factory``）不可见
   → test_evolution_loop_e2e.py 报 "fixture not found"（4 个 setup error）。

故在此补齐这两类内容。注意**不能直接 import tests.conftest** —— 那会执行它的
模块级副作用（auth-off pinning 等），正是 confcutdir 要隔离的东西。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

# ── 1. 企业版可选性守卫 ────────────────────────────────────────────
# maop.enterprise 由独立的 maop-enterprise 包（MAOS 私有仓库）通过同命名空间
# 注入。本地装了、CI 没有，故需条件跳过。逻辑与 tests/conftest.py 一致。
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


# ── 2. evolution fixture（原定义在 tests/conftest.py，confcutdir 下不可见）──
_EVO_SINGLETON_ATTRS: tuple[str, ...] = (
    "_db_path",
    "_instance",
    "_singleton",
    "_global_loop",
    "_global_history",
    "_global_bus",
    "_global_pool",
    "_global_lb",
    "_metrics",
    "_otel_tracer",
)

_EVOLUTION_MODULES: tuple[str, ...] = (
    "maop.core.evolution.evolution_loop",
    "maop.core.evolution.evolution_loop_types",
    "maop.core.evolution.evolution_collectors",
    "maop.core.evolution.evolution_analyzers",
    "maop.core.evolution.evolution_agent",
    "maop.core.evolution.evolution_phases",
)


def _try_reset_evo_singletons() -> None:
    """对 evolution 子模块的潜在单例属性做 best-effort 重置（不存在则跳过）。"""
    for mod_name in _EVOLUTION_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for attr in _EVO_SINGLETON_ATTRS:
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except (AttributeError, TypeError):
                    pass


@pytest.fixture
def evolution_loop_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """可重置的 EvolutionLoop 工厂：每次返回新实例 + 显式 db_path。

    与 tests/conftest.py 中的同名 fixture 保持一致（e2e 因 confcutdir 看不到
    后者）。显式传 ``root_dir=tmp_path/evolution``，不依赖单例缓存。

    Returns:
        callable: () -> EvolutionLoop（每次新实例）
    """
    db_root = tmp_path / "evolution"
    db_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_EVOLUTION_LOOP_ENABLED", "1")

    def _factory(**kwargs: Any):
        _try_reset_evo_singletons()
        from maop.core.evolution.evolution_loop import EvolutionLoop
        return EvolutionLoop(root_dir=db_root, **kwargs)

    return _factory
