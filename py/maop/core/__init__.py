"""MAOP core package — lazy symbol & submodule lookup.

v4.5.0: modules reorganized into 9 subpackages.
P0-3 (2026-08-07): shim modules removed; ``from maop.core import xxx``
now resolves via lazy lookup in subpackages (symbols and submodules).

Subpackages:
    mcp, agent, memory, backends, routing, reliability,
    security, evolution, monitoring
"""
from __future__ import annotations

import importlib

# 9 子包（查找顺序：先子包符号，再子包子模块）
_SUBPACKAGES = (
    "mcp",
    "memory",
    "backends",
    "routing",
    "reliability",
    "security",
    "evolution",
    "monitoring",
    "agent",
)


def __getattr__(name: str):
    """惰性查找符号或子模块，避免循环导入。

    1. 在各子包 ``__all__`` 中查找符号（``from maop.core import Symbol``）
    2. 在本包目录下查找同名子模块/子包（``from maop.core import module_name``）
    3. 在各子包中查找同名子模块（``from maop.core import module_name``）

    修复（取证：test_three_layer_memory.py 5 个 mock 失败）：原实现把
    "各子包同名子模块查找"放在"本包直接子模块/子包查找"之前，导致
    ``maop.core.evolution`` 属性访问被误解析为 ``maop.core.agent.evolution``
    （``agent/evolution/__init__.py`` 存在），而不是 ``maop/core/evolution/``
    子包本身；mock 继续解析 ``evolution_loop`` 时 AttributeError，且错误
    结果被 ``globals()`` 缓存污染。现将本包直接查找提前。
    """
    # 1. 在各子包中查找符号
    for subpkg in _SUBPACKAGES:
        try:
            submod = importlib.import_module(f".{subpkg}", __name__)
        except ImportError:
            continue
        if name in getattr(submod, "__all__", ()):
            value = getattr(submod, name)
            globals()[name] = value  # 缓存
            return value
    # 2. 本包目录下查找同名子模块/子包（如 maop/core/evolution/ 子包本身）
    try:
        mod = importlib.import_module(f".{name}", __name__)
        globals()[name] = mod  # 缓存
        return mod
    except ImportError:
        pass
    # 3. 在各子包中查找同名子模块（如 otel → monitoring.otel）
    for subpkg in _SUBPACKAGES:
        try:
            mod = importlib.import_module(f".{subpkg}.{name}", __name__)
        except ImportError:
            continue
        globals()[name] = mod  # 缓存
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
