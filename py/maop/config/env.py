"""MAOP 环境变量集中管理模块。

本模块统一管理 MAOP 所有环境变量的读取逻辑，解决以下问题：
- M2 修复：环境变量名脱节（规范名 MAOP_TLS_ENABLED vs 旧名 MAOP_TLS）
- M3 修复：根目录解析不一致（MAOP_ROOT_DIR vs MAOP_ROOT）

所有环境变量读取应通过本模块提供的函数进行，避免散落在各模块的
``os.environ.get(...)`` 调用导致命名不一致与向后兼容问题。

设计原则：
1. 规范名优先，旧名作为 fallback 并触发 DeprecationWarning
2. 集中管理便于审计与文档化
3. 函数返回值类型明确，便于类型检查
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Final

__all__ = [
    "MAOP_ROOT_DIR_VAR",
    "MAOP_ROOT_LEGACY_VAR",
    "MAOP_TLS_ENABLED_VAR",
    "MAOP_TLS_LEGACY_VAR",
    "get_bool_env",
    "get_env",
    "get_root_dir",
    "get_tls_enabled",
]

# 环境变量名常量定义（便于审计与引用）
MAOP_TLS_ENABLED_VAR: Final[str] = "MAOP_TLS_ENABLED"
MAOP_TLS_LEGACY_VAR: Final[str] = "MAOP_TLS"  # v5.0.0 前的旧名，保留向后兼容

MAOP_ROOT_DIR_VAR: Final[str] = "MAOP_ROOT_DIR"
MAOP_ROOT_LEGACY_VAR: Final[str] = "MAOP_ROOT"  # 旧名，保留向后兼容


def get_env(name: str, default: str | None = None) -> str | None:
    """读取环境变量值。

    Parameters
    ----------
    name : str
        环境变量名。
    default : str | None
        默认值（变量未设置时返回）。

    Returns
    -------
    str | None
        环境变量值或默认值。
    """
    return os.environ.get(name, default)


def get_bool_env(name: str, default: bool = False) -> bool:
    """读取布尔型环境变量。

    支持的真值（大小写不敏感）：``1``、``true``、``yes``、``on``。
    其他值视为假。

    Parameters
    ----------
    name : str
        环境变量名。
    default : bool
        变量未设置时的默认值。

    Returns
    -------
    bool
        解析后的布尔值。
    """
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_tls_enabled() -> bool:
    """读取 TLS 启用配置（M2 修复）。

    优先读取规范名 ``MAOP_TLS_ENABLED``；若未设置则回退到旧名
    ``MAOP_TLS`` 并触发 ``DeprecationWarning``，提醒用户迁移。

    Returns
    -------
    bool
        TLS 是否启用。

    Examples
    --------
    >>> # 推荐用法
    >>> # MAOP_TLS_ENABLED=1 maop start
    >>> # 旧用法（将弃用，会告警）
    >>> # MAOP_TLS=1 maop start
    """
    val = os.environ.get(MAOP_TLS_ENABLED_VAR)
    if val is None:
        val = os.environ.get(MAOP_TLS_LEGACY_VAR)
        if val is not None:
            warnings.warn(
                "环境变量 MAOP_TLS 已弃用，请改用 MAOP_TLS_ENABLED（v5.0.0+ 规范名）。"
                "将在未来版本移除对 MAOP_TLS 的支持。",
                DeprecationWarning,
                stacklevel=2,
            )
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_root_dir(default: str | Path | None = None) -> Path:
    """读取项目根目录（M3 修复）。

    优先读取规范名 ``MAOP_ROOT_DIR``；若未设置则回退到旧名
    ``MAOP_ROOT`` 并触发 ``DeprecationWarning``。

    Parameters
    ----------
    default : str | Path | None
        环境变量均未设置时的默认根目录。若为 ``None`` 则回退到
        当前工作目录 ``.``。

    Returns
    -------
    Path
        解析后的根目录绝对路径（已 ``resolve()``）。

    Examples
    --------
    >>> # 推荐用法
    >>> # MAOP_ROOT_DIR=/app maop start
    >>> # 旧用法（将弃用，会告警）
    >>> # MAOP_ROOT=/app maop start
    """
    val = os.environ.get(MAOP_ROOT_DIR_VAR)
    if val is None:
        val = os.environ.get(MAOP_ROOT_LEGACY_VAR)
        if val is not None:
            warnings.warn(
                "环境变量 MAOP_ROOT 已弃用，请改用 MAOP_ROOT_DIR（v5.0.0+ 规范名）。"
                "将在未来版本移除对 MAOP_ROOT 的支持。",
                DeprecationWarning,
                stacklevel=2,
            )
    if val is None:
        val = str(default) if default is not None else "."
    return Path(val).resolve()