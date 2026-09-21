"""IDE 插件调度适配器 — 处理 GitHub Copilot / 通义灵码等 IDE 插件的调度。

IDE 插件不是独立进程，通过 IDE 内 API 调用。MAOP 服务端不能直接调用
IDE 插件 API，需要 IDE 侧安装 MAOP Companion 插件作为桥梁：

    MAOP 服务端 ←WebSocket→ Companion 插件 → IDE 内 AI 插件

本适配器只与 Companion 通信（JSON over WebSocket），不直接调用 IDE API。

命令协议（JSON over WebSocket）：

  请求::

      {"action": "execute"/"health"/"ping",
       "extension_id": "...",
       "task": "...",
       "timeout_s": 30}

  响应::

      {"success": true/false, "result": "...", "error": "..."}

设计要点：
  - **线程安全**：所有公共方法用 ``threading.RLock`` 保护。
  - **同步接口 + 异步 WebSocket**：``websockets`` 是异步库，但 ``AgentAdapter``
    接口是同步的。通过 ``asyncio.run`` 在同步方法中执行异步协程。
  - **自动重连**：``execute`` / ``health_check`` 检测到连接断开时自动
    调用 ``_reconnect``，最多重试 ``max_reconnect`` 次。
  - **超时保护**：每条命令带独立超时，防止 Companion / 插件挂起。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from pydantic import BaseModel, Field, field_validator  # noqa: F401

from maop.core.agent.delegation.agent_proxy import AgentAdapter

logger = logging.getLogger(__name__)

#: 支持的 action 类型
_VALID_ACTIONS: tuple[str, ...] = ("execute", "health", "ping")

#: 支持的 IDE 类型
_VALID_IDE_TYPES: tuple[str, ...] = ("vscode", "jetbrains", "vim")


class IDEExtensionConfig(BaseModel):
    """IDE 插件适配器配置。

    Parameters
    ----------
    extension_id : str
        IDE 插件 ID（如 ``"github.copilot"`` / ``"alibaba.tongyi-lingma"``）。
    ide_type : str
        IDE 类型（``"vscode"`` / ``"jetbrains"`` / ``"vim"``）。
    companion_url : str
        MAOP Companion 的 WebSocket URL（如 ``"ws://localhost:7890"``）。
    companion_token : str
        Companion 认证 token（Bearer），为空表示无认证。
    command_timeout_s : float
        单次命令超时秒数。
    reconnect_interval_s : float
        重连间隔秒数。
    max_reconnect : int
        最大重连次数。
    """

    extension_id: str
    ide_type: str
    companion_url: str
    companion_token: str = ""
    command_timeout_s: float = 30.0
    reconnect_interval_s: float = 5.0
    max_reconnect: int = 3

    model_config = {"extra": "forbid"}

    @field_validator("ide_type")
    @classmethod
    def _validate_ide_type(cls, v: str) -> str:
        """校验 IDE 类型在支持列表内。"""
        if v not in _VALID_IDE_TYPES:
            raise ValueError(
                f"不支持的 IDE 类型 '{v}'，仅支持 {list(_VALID_IDE_TYPES)}"
            )
        return v

    @field_validator("companion_url")
    @classmethod
    def _validate_companion_url(cls, v: str) -> str:
        """校验 Companion URL 以 ws:// 或 wss:// 开头。"""
        if not v:
            raise ValueError("companion_url 不能为空")
        if not (v.startswith("ws://") or v.startswith("wss://")):
            raise ValueError(
                f"companion_url 必须以 'ws://' 或 'wss://' 开头，得到 '{v}'"
            )
        return v

    @field_validator("command_timeout_s")
    @classmethod
    def _validate_command_timeout(cls, v: float) -> float:
        """校验命令超时为正数。"""
        if v <= 0:
            raise ValueError("command_timeout_s 必须为正数")
        return v

    @field_validator("reconnect_interval_s")
    @classmethod
    def _validate_reconnect_interval(cls, v: float) -> float:
        """校验重连间隔为正数。"""
        if v <= 0:
            raise ValueError("reconnect_interval_s 必须为正数")
        return v

    @field_validator("max_reconnect")
    @classmethod
    def _validate_max_reconnect(cls, v: int) -> int:
        """校验最大重连次数非负。"""
        if v < 0:
            raise ValueError("max_reconnect 不能为负数")
        return v


class IDEExtensionAdapter(AgentAdapter):
    """IDE 插件调度适配器 — 通过 WebSocket 与 Companion 插件通信。

    MAOP 服务端通过本适配器将任务发送给 Companion 插件，由 Companion
    插件在 IDE 内调用目标 AI 插件（如 GitHub Copilot）执行任务。

    Parameters
    ----------
    config : IDEExtensionConfig
        IDE 插件适配器配置。
    """

    def __init__(self, config: IDEExtensionConfig) -> None:
        self._config: IDEExtensionConfig = config
        # 可重入锁：方法可能互相调用（如 execute → _reconnect → connect）
        self._lock: threading.RLock = threading.RLock()
        # WebSocket 连接对象（websockets.WebSocketClientProtocol）
        self._ws: Any = None
        # 连接状态标志
        self._connected: bool = False
        # 事件循环：用于在同步接口中运行异步 WebSocket 代码
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # 异步辅助：在同步方法中执行协程
    # ------------------------------------------------------------------

    def _run_async(self, coro: Any) -> Any:
        """在同步上下文中运行异步协程。

        创建（或复用）事件循环执行协程并返回结果。
        协程执行完毕后不关闭循环，以便复用连接。
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("事件循环已关闭")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._loop = loop
        return loop.run_until_complete(coro)

    async def _async_connect(self) -> bool:
        """异步建立 WebSocket 连接。"""
        import websockets

        # 构建请求头（如有 token）
        headers: dict[str, str] = {}
        if self._config.companion_token:
            headers["Authorization"] = f"Bearer {self._config.companion_token}"

        try:
            self._ws = await websockets.connect(
                self._config.companion_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
            )
            return True
        except Exception as exc:
            logger.error("[ide_extension_adapter] WebSocket 连接失败: %s", exc)
            self._ws = None
            return False

    async def _async_send_and_recv(self, message: str, timeout_s: float) -> str:
        """异步发送消息并等待响应。"""
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        await self._ws.send(message)
        response = await asyncio.wait_for(self._ws.recv(), timeout=timeout_s)
        return response

    async def _async_disconnect(self) -> None:
        """异步断开 WebSocket 连接。"""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("[ide_extension_adapter] 关闭 WebSocket 异常: %s", exc)
            finally:
                self._ws = None

    # ------------------------------------------------------------------
    # 命令协议
    # ------------------------------------------------------------------

    def _build_command(
        self, action: str, task: str = "", timeout_s: float | None = None
    ) -> dict[str, Any]:
        """构建符合协议的命令字典。

        Parameters
        ----------
        action : str
            动作类型（``"execute"`` / ``"health"`` / ``"ping"``）。
        task : str
            任务描述（仅 ``action="execute"`` 时需要）。
        timeout_s : float | None
            命令超时，为 ``None`` 时使用配置默认值。
        """
        if action not in _VALID_ACTIONS:
            raise ValueError(f"不支持的 action '{action}'，仅支持 {list(_VALID_ACTIONS)}")

        cmd: dict[str, Any] = {
            "action": action,
            "extension_id": self._config.extension_id,
        }
        if action == "execute":
            cmd["task"] = task
        cmd["timeout_s"] = timeout_s if timeout_s is not None else self._config.command_timeout_s
        return cmd

    def _send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """发送命令到 Companion 并等待响应。

        将命令字典序列化为 JSON 发送到 WebSocket，等待并解析响应。
        响应必须包含 ``success`` 字段。

        Parameters
        ----------
        command : dict
            符合协议的命令字典。

        Returns
        -------
        dict
            Companion 返回的响应字典。

        Raises
        ------
        RuntimeError
            未连接或通信失败。
        TimeoutError
            等待响应超时。
        """
        with self._lock:
            if not self._connected or self._ws is None:
                raise RuntimeError("未连接 Companion，无法发送命令")

            message = json.dumps(command, ensure_ascii=False)
            timeout_s = float(command.get("timeout_s", self._config.command_timeout_s))

            try:
                raw = self._run_async(self._async_send_and_recv(message, timeout_s))
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"等待 Companion 响应超时（{timeout_s}s）"
                ) from exc
            except Exception as exc:
                raise RuntimeError(f"与 Companion 通信失败: {exc}") from exc

            try:
                response = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError(
                    f"Companion 响应不是合法 JSON: {raw!r}"
                ) from exc

            if not isinstance(response, dict):
                raise RuntimeError(
                    f"Companion 响应不是 JSON 对象: {response!r}"
                )

            return response

    # ------------------------------------------------------------------
    # 重连逻辑
    # ------------------------------------------------------------------

    def _reconnect(self) -> bool:
        """重连 Companion — 按配置间隔重试，最多 ``max_reconnect`` 次。

        Returns
        -------
        bool
            重连成功返回 ``True``，全部失败返回 ``False``。
        """
        with self._lock:
            # 先清理旧连接
            if self._ws is not None:
                try:
                    self._run_async(self._async_disconnect())
                except Exception:
                    pass
                self._ws = None
            self._connected = False

            for attempt in range(1, self._config.max_reconnect + 1):
                logger.info(
                    "[ide_extension_adapter] 重连尝试 %d/%d",
                    attempt,
                    self._config.max_reconnect,
                )
                try:
                    if self._run_async(self._async_connect()):
                        self._connected = True
                        logger.info(
                            "[ide_extension_adapter] 重连成功（第 %d 次）",
                            attempt,
                        )
                        return True
                except Exception as exc:
                    logger.warning(
                        "[ide_extension_adapter] 重连异常（第 %d 次）: %s",
                        attempt,
                        exc,
                    )

                # 未成功，等待重连间隔（最后一次不等待）
                if attempt < self._config.max_reconnect:
                    import time

                    time.sleep(self._config.reconnect_interval_s)

            logger.error(
                "[ide_extension_adapter] 重连失败，已达最大次数 %d",
                self._config.max_reconnect,
            )
            return False

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """通过 WebSocket 连接 Companion 插件。

        Returns
        -------
        bool
            连接成功返回 ``True``，失败返回 ``False``。
        """
        with self._lock:
            if self._connected and self._ws is not None:
                # 已连接，直接返回成功
                return True

            try:
                ok = self._run_async(self._async_connect())
            except Exception as exc:
                logger.error("[ide_extension_adapter] 连接异常: %s", exc)
                ok = False

            if ok:
                self._connected = True
                logger.info(
                    "[ide_extension_adapter] 已连接 Companion: %s",
                    self._config.companion_url,
                )
            else:
                self._connected = False
            return ok

    def execute(self, task: str, **kwargs: Any) -> str:
        """通过 Companion 调用 IDE 插件执行任务。

        若未连接会自动调用 ``connect()``。通信失败时尝试重连后重试。

        Parameters
        ----------
        task : str
            要执行的任务描述。
        **kwargs
            额外参数（如 ``timeout_s``）。

        Returns
        -------
        str
            插件执行结果字符串。

        Raises
        ------
        RuntimeError
            未连接且连接失败，或插件返回错误。
        TimeoutError
            命令超时。
        """
        with self._lock:
            # 自动连接
            if not self._connected:
                if not self.connect():
                    raise RuntimeError(
                        f"IDEExtensionAdapter 无法连接 Companion: "
                        f"{self._config.companion_url}"
                    )

            timeout_s = float(kwargs.get("timeout_s", self._config.command_timeout_s))
            command = self._build_command("execute", task=task, timeout_s=timeout_s)

            try:
                response = self._send_command(command)
            except (RuntimeError, TimeoutError) as exc:
                # 通信失败，尝试重连后重试一次
                logger.warning(
                    "[ide_extension_adapter] 执行失败，尝试重连: %s", exc
                )
                if self._reconnect():
                    response = self._send_command(command)
                else:
                    raise RuntimeError(
                        f"执行失败且重连不成功: {exc}"
                    ) from exc

            if not response.get("success", False):
                error_msg = response.get("error", "未知错误")
                raise RuntimeError(
                    f"IDE 插件 '{self._config.extension_id}' 执行失败: {error_msg}"
                )

            return str(response.get("result", ""))

    def health_check(self) -> bool:
        """检查 Companion 连接和插件可用性。

        发送 ``ping`` 命令检测 Companion 连通性，再发送 ``health`` 命令
        检测目标插件是否可用。两者都成功才返回 ``True``。

        Returns
        -------
        bool
            Companion 连接且插件可用返回 ``True``，否则 ``False``。
        """
        with self._lock:
            # 未连接时尝试连接
            if not self._connected:
                if not self.connect():
                    return False

            # 第一步：ping 检测 Companion 连通性
            try:
                ping_cmd = self._build_command("ping")
                ping_resp = self._send_command(ping_cmd)
                if not ping_resp.get("success", False):
                    return False
            except (RuntimeError, TimeoutError) as exc:
                logger.debug("[ide_extension_adapter] ping 失败: %s", exc)
                # ping 失败，尝试重连
                if not self._reconnect():
                    return False
                try:
                    ping_resp = self._send_command(ping_cmd)
                    if not ping_resp.get("success", False):
                        return False
                except (RuntimeError, TimeoutError):
                    return False

            # 第二步：health 检测插件可用性
            try:
                health_cmd = self._build_command("health")
                health_resp = self._send_command(health_cmd)
                return bool(health_resp.get("success", False))
            except (RuntimeError, TimeoutError) as exc:
                logger.debug("[ide_extension_adapter] health 检查失败: %s", exc)
                return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """更新适配器配置。

        断开当前连接，用新配置替换旧配置。若之前已连接则尝试用新配置重连。

        Parameters
        ----------
        config : dict
            新配置字典，字段与 :class:`IDEExtensionConfig` 对应。
        """
        with self._lock:
            was_connected = self._connected
            self.disconnect()
            self._config = IDEExtensionConfig(**config)
            if was_connected:
                self.connect()

    def disconnect(self) -> None:
        """断开 Companion 的 WebSocket 连接。"""
        with self._lock:
            if self._ws is not None:
                try:
                    self._run_async(self._async_disconnect())
                except Exception as exc:
                    logger.debug(
                        "[ide_extension_adapter] 断开连接异常: %s", exc
                    )
            self._ws = None
            self._connected = False
            logger.info("[ide_extension_adapter] 已断开 Companion 连接")