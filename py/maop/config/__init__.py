"""MAOP Config — YAML configuration loading and validation.

Loads agents.yaml and rules.yaml from the project config/ directory.
Provides typed Pydantic models for all config sections.

Perf opt: ConfigLoader / load_config 使用懒加载，避免 `import maop.config`
时强制触发 pydantic + yaml 的导入开销（~280ms）。仅在真正访问
ConfigLoader 或 load_config 时才加载 loader 模块。
"""

from __future__ import annotations

__all__ = ["ConfigLoader", "load_config"]


def __getattr__(name: str):
    """惰性加载 loader 模块中的符号。

    `import maop.config` 不再触发 pydantic/yaml 导入；只有在
    `maop.config.ConfigLoader` 或 `maop.config.load_config`
    被访问时才加载 loader.py。
    """
    if name in ("ConfigLoader", "load_config"):
        from maop.config import loader as _loader

        value = getattr(_loader, name)
        globals()[name] = value  # 缓存到 globals，后续访问直接命中
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
