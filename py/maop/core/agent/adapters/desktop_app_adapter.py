"""桌面 IDE 应用适配器 — 处理 Cursor / Trae / Qoder 等桌面应用的通信。

桌面 IDE 应用有 GUI 交互，部分场景 CLI 不够用。本适配器优先使用 CLI
接口，CLI 不可用时依次回退到 IPC（Named Pipe / Unix Socket）和本地
HTTP。

通信回退顺序由 ``DesktopAppConfig.fallback_order`` 控制，默认
``["cli", "ipc", "http"]``。

设计要点：
  - **继承 CLIAdapter**：复用 CLI 执行逻辑（命令白名单、沙箱、输出解析），
    不重复造轮子。
  - **线程安全**：所有公共方法和入口用 ``threading.RLock`` 保护。
  - **跨平台 IPC**：Windows 用 Named Pipe（``\\\\.\\pipe\\xxx``），
    Linux/Mac 用 Unix Socket。Python 3.9+ 在 Windows 10 1803+ 上
    支持 ``AF_UNIX``，不支持时 IPC 降级为不可用。
  - **独立超时**：CLI / IPC / HTTP 各有独立超时配置。
"""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
import threading
from typing import Any

from pydantic import BaseModel, Field, field_validator

from maop.core.agent.adapters.cli_adapter import CLIAdapter, CLIAdapterConfig

logger = logging.getLogger(__name__)

#: 支持的通信方式标识符
_VALID_METHODS: tuple[str, ...] = ("cli", "ipc", "http")


class DesktopAppConfig(BaseModel):
    """桌面应用适配器配置。

    Parameters
    ----------
    app_name : str
        应用名称（如 ``"cursor"`` / ``"trae"`` / ``"qoder"``）。
    cli_command : str
        CLI 命令（如 ``"cursor"``）。为空表示无 CLI 接口。
    cli_args : list[str]
        CLI 附加参数。
    ipc_path : str
        IPC 路径。Windows 为 Named Pipe（如 ``"\\\\.\\pipe\\cursor"``），
        Linux/Mac 为 Unix Socket 文件路径。
    http_url : str
        本地 HTTP URL（如 ``"http://localhost:3456"``）。
    http_token : str
        HTTP 认证 token（Bearer）。
    process_name : str
        进程名（用于检测应用是否运行，如 ``"Cursor.exe"``）。
    fallback_order : list[str]
        通信方式优先级，默认 ``["cli", "ipc", "http"]``。
        每个元素必须是 ``"cli"`` / ``"ipc"`` / ``"http"`` 之一。
    cli_timeout_s : float
        CLI 执行超时秒数。
    ipc_timeout_s : float
        IPC 通信超时秒数。
    http_timeout_s : float
        HTTP 通信超时秒数。
    """

    app_name: str
    cli_command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    ipc_path: str = ""
    http_url: str = ""
    http_token: str = ""
    process_name: str = ""
    fallback_order: list[str] = Field(default_factory=lambda: ["cli", "ipc", "http"])
    cli_timeout_s: float = 30.0
    ipc_timeout_s: float = 10.0
    http_timeout_s: float = 15.0

    model_config = {"extra": "forbid"}

    @field_validator("fallback_order")
    @classmethod
    def _validate_fallback_order(cls, v: list[str]) -> list[str]:
        """校验 fallback_order 中每个元素都是支持的通信方式。"""
        if not v:
            raise ValueError("fallback_order 不能为空")
        for item in v:
            if item not in _VALID_METHODS:
                raise ValueError(
                    f"fallback_order 包含不支持的通信方式 '{item}'，"
                    f"仅支持 {list(_VALID_METHODS)}"
                )
        return v


class DesktopAppAdapter(CLIAdapter):
    """桌面 IDE 应用适配器 — 优先 CLI，回退 IPC / 本地 HTTP。

    继承 :class:`CLIAdapter`，复用其命令白名单校验、工作目录沙箱、
    输出解析等逻辑。当 CLI 不可用或执行失败时，依次尝试 IPC 和 HTTP。

    Parameters
    ----------
    config : DesktopAppConfig
        桌面应用配置。
    """

    def __init__(self, config: DesktopAppConfig) -> None:
        # 构建 CLIAdapterConfig 传给父类，复用 CLI 执行逻辑
        cli_config = CLIAdapterConfig(
            command=config.cli_command,
            args=list(config.cli_args),
            timeout_s=config.cli_timeout_s,
        )
        super().__init__(cli_config)
        # 父类的 self.config 是 CLIAdapterConfig；额外保存桌面应用配置
        self._app_config: DesktopAppConfig = config
        # 可重入锁：方法可能互相调用（如 execute → connect）
        self._lock: threading.RLock = threading.RLock()
        # 当前实际使用的通信方式
        self._active_method: str = ""

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _is_cli_available(self) -> bool:
        """检测 CLI 是否可用（命令非空且在白名单内）。"""
        if not self._app_config.cli_command:
            return False
        try:
            self._validate_command()
            return True
        except Exception:
            return False

    def _is_ipc_available(self) -> bool:
        """检测 IPC 是否可用（路径非空且平台支持 AF_UNIX）。"""
        if not self._app_config.ipc_path:
            return False
        # Windows 旧版本可能不支持 AF_UNIX
        return hasattr(socket, "AF_UNIX")

    def _is_http_available(self) -> bool:
        """检测 HTTP 是否可用（URL 非空）。"""
        return bool(self._app_config.http_url)

    def _check_method_available(self, method: str) -> bool:
        """检测指定通信方式是否可用。"""
        if method == "cli":
            return self._is_cli_available()
        if method == "ipc":
            return self._is_ipc_available()
        if method == "http":
            return self._is_http_available()
        return False

    # ------------------------------------------------------------------
    # 三种通信方式的尝试方法
    # ------------------------------------------------------------------

    def _try_cli(self, task: str) -> str | None:
        """尝试 CLI 执行（复用父类 ``CLIAdapter.execute`` 逻辑）。

        成功返回结果字符串，失败返回 ``None``。
        """
        if not self._app_config.cli_command:
            return None
        with self._lock:
            try:
                # 验证命令在白名单内（不通过则抛 ValueError）
                self._validate_command()
                self._validate_cwd()
                # 临时标记父类已连接，复用父类 execute 的完整逻辑
                # （命令构建、subprocess 执行、输出解析、超时处理）
                was_connected = self._connected
                self._connected = True
                try:
                    return CLIAdapter.execute(self, task)
                finally:
                    self._connected = was_connected
            except Exception as exc:
                logger.debug("[desktop_adapter] CLI 执行失败: %s", exc)
                return None

    def _try_ipc(self, task: str) -> str | None:
        """尝试 IPC 通信（Named Pipe on Windows / Unix Socket on Linux）。

        通过 JSON 帧协议发送 ``{"task": task}`` 并接收响应。
        成功返回响应字符串，失败返回 ``None``。
        """
        if not self._app_config.ipc_path:
            return None
        if not hasattr(socket, "AF_UNIX"):
            logger.debug("[desktop_adapter] 当前平台不支持 AF_UNIX，IPC 不可用")
            return None
        try:
            return self._ipc_call(task)
        except Exception as exc:
            logger.debug("[desktop_adapter] IPC 执行失败: %s", exc)
            return None

    def _ipc_call(self, task: str) -> str:
        """执行一次 IPC 调用 — 创建 socket、发送任务、接收响应。"""
        # socket.AF_UNIX 在 Windows 上不存在，但调用方已在 _ipc_available()
        # 与本函数入口用 `hasattr(socket, "AF_UNIX")` 守卫，走到这里时该属性
        # 必然存在。mypy 无法穿透 hasattr 对模块属性做收窄，故行尾定向豁免。
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        sock.settimeout(self._app_config.ipc_timeout_s)
        try:
            sock.connect(self._app_config.ipc_path)
            # 发送 JSON 请求帧
            request = json.dumps({"task": task}, ensure_ascii=False).encode("utf-8")
            sock.sendall(request)
            # 接收全部响应
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            # 修复: 添加 errors="replace" 避免非法 UTF-8 字节触发 UnicodeDecodeError
            return b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            sock.close()

    def _try_http(self, task: str) -> str | None:
        """尝试本地 HTTP 通信（localhost:port）。

        成功返回响应字符串，失败返回 ``None``。
        """
        if not self._app_config.http_url:
            return None
        try:
            return self._http_call(task)
        except Exception as exc:
            logger.debug("[desktop_adapter] HTTP 执行失败: %s", exc)
            return None

    def _http_call(self, task: str) -> str:
        """执行一次 HTTP POST 调用。"""
        import httpx

        headers: dict[str, str] = {}
        if self._app_config.http_token:
            headers["Authorization"] = f"Bearer {self._app_config.http_token}"
        body = {"task": task}
        with httpx.Client(timeout=self._app_config.http_timeout_s) as client:
            resp = client.post(self._app_config.http_url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.text

    def _dispatch(self, method: str, task: str) -> str | None:
        """根据通信方式分发到对应的尝试方法。"""
        if method == "cli":
            return self._try_cli(task)
        if method == "ipc":
            return self._try_ipc(task)
        if method == "http":
            return self._try_http(task)
        return None

    # ------------------------------------------------------------------
    # 进程检测
    # ------------------------------------------------------------------

    def _detect_app_process(self) -> bool:
        """检测桌面应用进程是否在运行。

        Windows 用 ``tasklist``，Linux/Mac 用 ``pgrep``。
        ``process_name`` 为空时返回 ``False``。
        """
        if not self._app_config.process_name:
            return False
        try:
            if sys.platform.startswith("win"):
                # Windows: tasklist 过滤进程名
                result = subprocess.run(
                    [
                        "tasklist",
                        "/FI",
                        f"IMAGENAME eq {self._app_config.process_name}",
                        "/NH",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                return (
                    result.returncode == 0
                    and self._app_config.process_name.lower() in result.stdout.lower()
                )
            # Linux/Mac: pgrep 精确匹配进程名
            result = subprocess.run(
                ["pgrep", "-x", self._app_config.process_name],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            return result.returncode == 0
        except Exception as exc:
            logger.debug("[desktop_adapter] 进程检测失败: %s", exc)
            return False

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """检测桌面应用是否可用（CLI / IPC / HTTP 任一可用即可）。

        按 ``fallback_order`` 顺序检测，任一方式可用即视为连接成功。
        """
        with self._lock:
            for method in self._app_config.fallback_order:
                if self._check_method_available(method):
                    self._connected = True
                    self._active_method = method
                    logger.info(
                        "[desktop_adapter] %s 已连接，通信方式: %s",
                        self._app_config.app_name,
                        method,
                    )
                    return True
            self._connected = False
            self._active_method = ""
            logger.warning(
                "[desktop_adapter] %s 全部通信方式不可用",
                self._app_config.app_name,
            )
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """执行任务 — 按 ``fallback_order`` 依次尝试，全部失败时抛异常。

        优先 CLI 执行，CLI 失败时尝试 IPC，IPC 失败时尝试本地 HTTP。
        任一方式成功即返回结果。
        """
        with self._lock:
            if not self._connected and not self.connect():
                raise RuntimeError(
                    f"DesktopAppAdapter '{self._app_config.app_name}' "
                    f"全部通信方式不可用"
                )

            last_error: Exception | None = None
            for method in self._app_config.fallback_order:
                try:
                    result = self._dispatch(method, task)
                    if result is not None:
                        self._active_method = method
                        logger.debug(
                            "[desktop_adapter] %s 通过 %s 执行成功",
                            self._app_config.app_name,
                            method,
                        )
                        return result
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "[desktop_adapter] %s 通过 %s 执行异常: %s",
                        self._app_config.app_name,
                        method,
                        exc,
                    )

            raise RuntimeError(
                f"DesktopAppAdapter '{self._app_config.app_name}' "
                f"全部通信方式执行失败: {last_error}"
            )

    def health_check(self) -> bool:
        """检查桌面应用进程是否运行。"""
        with self._lock:
            return self._detect_app_process()

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置字典替换当前配置并重连。"""
        with self._lock:
            was_connected = self._connected
            self.disconnect()
            self._app_config = DesktopAppConfig(**config)
            # 重建父类的 CLIAdapterConfig
            self.config = CLIAdapterConfig(
                command=self._app_config.cli_command,
                args=list(self._app_config.cli_args),
                timeout_s=self._app_config.cli_timeout_s,
            )
            if was_connected:
                self.connect()

    def disconnect(self) -> None:
        """清理资源并断开连接。"""
        with self._lock:
            self._connected = False
            self._active_method = ""
            # 调用父类 disconnect 重置 CLI 状态
            CLIAdapter.disconnect(self)