"""适配器工厂 — 根据 AgentConfig.driver 构建对应的 AgentAdapter 实例。

本模块是调度主链路（``maop.delegate.drivers``）与适配器实现之间的桥梁。
``build_adapter`` 根据 ``AgentConfig.driver`` 字段分支实例化对应的适配器，
将调度层的扁平配置（``AgentConfig``）转换为各适配器专有的 Config 对象。

支持的 driver → 适配器映射：
  - ``cli`` / ``powershell`` / ``wrapper`` → :class:`CLIAdapter`
  - ``desktop_app`` → :class:`DesktopAppAdapter`
  - ``ide_extension`` → :class:`IDEExtensionAdapter`
  - ``vibe_coding`` → :class:`VibeCodingAdapter`
  - 未知 driver → 回退 :class:`CLIAdapter` + 记录 warning 日志

向后兼容：``driver=cli`` 的行为与改造前完全一致（构建 CLIAdapter）。
"""
from __future__ import annotations

import logging
import shlex
import sys

from maop.core.agent.adapters.cli_adapter import CLIAdapter, CLIAdapterConfig
from maop.core.agent.adapters.desktop_app_adapter import (
    DesktopAppAdapter,
    DesktopAppConfig,
)
from maop.core.agent.adapters.ide_extension_adapter import (
    IDEExtensionAdapter,
    IDEExtensionConfig,
)
from maop.core.agent.adapters.vibe_coding_adapter import (
    VibeCodingAdapter,
    VibeCodingConfig,
)
from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.delegate.models import AgentConfig

logger = logging.getLogger(__name__)


def build_adapter(config: AgentConfig) -> AgentAdapter:
    """根据 ``AgentConfig.driver`` 构建对应的 ``AgentAdapter`` 实例。

    Parameters
    ----------
    config : AgentConfig
        调度主链路的 agent 配置（由 ``AgentResolver`` 从 ``AgentDef`` 转换而来）。

    Returns
    -------
    AgentAdapter
        对应 driver 类型的适配器实例。未知 driver 回退为 ``CLIAdapter``。

    Notes
    -----
    - ``cli`` / ``powershell`` / ``wrapper`` 均构建 ``CLIAdapter``，
      shell / wrapper 的差异在 ``maop.delegate.drivers`` 的 driver 函数层处理，
      工厂只负责创建适配器对象。
    - ``desktop_app`` 从 ``AgentConfig`` 的 ``cli`` / ``process_name`` /
      ``ipc_path`` / ``http_url`` / ``fallback_order`` 字段构建 ``DesktopAppConfig``。
    - ``ide_extension`` 用 ``http_url`` 作为 Companion WebSocket URL（需 ``ws://`` 前缀），
      若不合法则回退默认 ``ws://localhost:7890``。完整配置可通过后续 ``sync_config`` 注入。
    - ``vibe_coding`` 用 ``config.name`` 作为平台标识，``http_url`` 作为平台 URL。
    """
    driver = config.driver

    if driver in ("cli", "powershell", "wrapper", "cmd", "python"):
        # 基础 CLI 驱动：构建 CLIAdapter，复用现有行为
        # powershell / wrapper / cmd / python 的 shell 差异在 driver 函数层处理
        return _build_cli_adapter(config)

    if driver == "desktop_app":
        # 桌面应用驱动：优先 CLI，回退 IPC / 本地 HTTP
        return _build_desktop_app_adapter(config)

    if driver == "ide_extension":
        # IDE 插件驱动：通过 WebSocket 与 Companion 插件通信
        return _build_ide_extension_adapter(config)

    if driver == "vibe_coding":
        # Vibe Coding 平台驱动：对接项目生成型 Web 平台
        return _build_vibe_coding_adapter(config)

    # 未知 driver：回退 CLIAdapter 并记录警告，保证调度不中断
    logger.warning(
        "[adapter_factory] 未知 driver '%s'，回退为 CLIAdapter（agent=%s）",
        driver, config.name,
    )
    return _build_cli_adapter(config)


# ── 各 driver 的适配器构建辅助函数 ──────────────────────────────


def _build_cli_adapter(config: AgentConfig) -> CLIAdapter:
    """构建 CLIAdapter — 从 AgentConfig 提取命令与参数。"""
    # 解析 cli_args 为参数列表（Windows 下 posix=False 保留反斜杠）
    args: list[str] = []
    if config.cli_args:
        try:
            args = shlex.split(config.cli_args, posix=(sys.platform != "win32"))
        except ValueError:
            # 模板解析失败时退化为空列表，由 CLIAdapter 自行处理
            args = []

    cli_config = CLIAdapterConfig(
        command=config.cli,
        args=args,
        timeout_s=float(config.timeout_s),
    )
    return CLIAdapter(cli_config)


def _build_desktop_app_adapter(config: AgentConfig) -> DesktopAppAdapter:
    """构建 DesktopAppAdapter — 从 AgentConfig 提取桌面应用配置。

    将调度层的扁平字段映射到 ``DesktopAppConfig``：
      - ``cli`` → ``cli_command``
      - ``process_name`` → ``process_name``
      - ``ipc_path`` → ``ipc_path``
      - ``http_url`` → ``http_url``
      - ``fallback_order`` → ``fallback_order``（默认 ["cli", "ipc", "http"]）
    """
    # 解析 CLI 参数列表
    cli_args: list[str] = []
    if config.cli_args:
        try:
            cli_args = shlex.split(config.cli_args, posix=(sys.platform != "win32"))
        except ValueError:
            cli_args = []

    app_config = DesktopAppConfig(
        app_name=config.name,
        cli_command=config.cli,
        cli_args=cli_args,
        ipc_path=config.ipc_path or "",
        http_url=config.http_url or "",
        process_name=config.process_name or "",
        fallback_order=config.fallback_order or ["cli", "ipc", "http"],
        cli_timeout_s=float(config.timeout_s),
    )
    return DesktopAppAdapter(app_config)


def _build_ide_extension_adapter(config: AgentConfig) -> IDEExtensionAdapter:
    """构建 IDEExtensionAdapter — 从 AgentConfig 提取 IDE 插件配置。

    ``companion_url`` 需以 ``ws://`` 或 ``wss://`` 开头。
    若 ``http_url`` 不满足此条件，回退默认 ``ws://localhost:7890``。
    完整配置（ide_type / companion_token 等）可通过后续 ``sync_config`` 注入。
    """
    # 推断 Companion WebSocket URL
    companion_url = config.http_url or ""
    if not (companion_url.startswith("ws://") or companion_url.startswith("wss://")):
        companion_url = "ws://localhost:7890"

    ext_config = IDEExtensionConfig(
        extension_id=config.name,
        ide_type="vscode",  # 默认 VSCode，可后续 sync_config 覆盖
        companion_url=companion_url,
        command_timeout_s=float(config.timeout_s),
    )
    return IDEExtensionAdapter(ext_config)


def _build_vibe_coding_adapter(config: AgentConfig) -> VibeCodingAdapter:
    """构建 VibeCodingAdapter — 从 AgentConfig 提取 Vibe Coding 平台配置。

    ``config.name`` 作为平台标识，``http_url`` 作为平台 URL（留空时由
    ``VibeCodingAdapter`` 按 platform 自动填充默认值）。
    """
    vibe_config = VibeCodingConfig(
        platform=config.name,
        base_url=config.http_url or "",
        max_generation_time_s=float(config.timeout_s),
    )
    return VibeCodingAdapter(vibe_config)