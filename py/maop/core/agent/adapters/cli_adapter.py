"""通用 CLI 适配器 — 通过 ``subprocess`` 调用外部 CLI 工具。

安全设计（与 MCPHub ``_StdioTransport`` 保持一致）：

  - **命令白名单**：复用 ``_StdioTransport.ALLOWED_COMMANDS``，第一个 token
    必须解析到白名单内的二进制，防止命令注入（如 ``rm -rf /``）。
  - **工作目录沙箱**：``allowed_dirs`` 限制 ``cwd`` 必须在白名单目录子树内。
  - **环境变量屏蔽**：``blocked_env`` 在传递给子进程前从环境中删除。
  - **超时保护**：``subprocess.run(timeout=)`` 防止子进程挂起。
  - **网络隔离**：``network_enabled=False`` 时在文档层面标注禁网
    （实际网络隔离由调用方/容器层保障）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.core.mcp.mcp_hub_transport import _StdioTransport

logger = logging.getLogger(__name__)


def _is_relative_to(child: Path, parent: Path) -> bool:
    """``Path.is_relative_to`` 的 Python 3.8+ 兼容实现。"""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _split_command(command: str) -> list[str]:
    """跨平台 ``shlex.split`` — 在 Windows 上保留路径反斜杠。

    ``shlex.split`` 在 POSIX 模式下把反斜杠当转义字符，会破坏
    Windows 路径（如 ``E:\\dev\\python.exe`` → ``E:devpython.exe``）。
    本函数在 Windows 上用 ``posix=False`` 拆分（保留反斜杠），
    再手动去除每个 token 周围的引号。
    """
    import sys as _sys

    if _sys.platform.startswith("win"):
        parts = shlex.split(command, posix=False)
        result: list[str] = []
        for p in parts:
            if len(p) >= 2 and p[0] in "\"'" and p[-1] == p[0]:
                result.append(p[1:-1])
            else:
                result.append(p)
        return result
    return shlex.split(command)


class CLIAdapterConfig(BaseModel):
    """CLI 适配器配置。

    Parameters
    ----------
    command : str
        要执行的 CLI 命令（如 ``"python -m my_agent"``）。第一个 token
        必须在 ``_StdioTransport.ALLOWED_COMMANDS`` 白名单内。
    args : list[str]
        附加命令行参数（追加到 ``command`` 之后）。
    cwd : str
        子进程工作目录。若 ``allowed_dirs`` 非空，必须在其子树内。
    env : dict[str, str]
        额外环境变量（合并到 ``os.environ`` 之上）。
    timeout_s : float
        子进程超时秒数，超时后 ``subprocess.TimeoutExpired`` 被转为
        ``TimeoutError``。
    task_arg_flag : str
        将任务作为命令行参数传递时的标志（如 ``"--prompt"``）。
        为空则不通过命令行参数传递。
    task_via_stdin : bool
        若为 ``True``，任务通过 stdin 传递而非命令行参数。
    output_format : str
        输出解析格式：``"text"``（原样返回）、``"json"``（解析 JSON）、
        ``"jsonl"``（逐行解析 JSON Lines）。
    output_json_key : str
        当 ``output_format`` 为 ``"json"`` / ``"jsonl"`` 时，从解析结果
        中提取的键。为空则返回整个 JSON 序列化字符串。
    allowed_dirs : list[str]
        工作目录白名单。``cwd`` 必须在其子树内，否则 ``connect()`` 失败。
    blocked_env : list[str]
        屏蔽的环境变量名列表，传递给子进程前从环境中删除。
    network_enabled : bool
        是否允许子进程访问网络（文档/审计层面标注，实际隔离由容器保障）。
    """

    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 30.0
    task_arg_flag: str = ""
    task_via_stdin: bool = False
    output_format: str = "text"  # text | json | jsonl
    output_json_key: str = ""
    allowed_dirs: list[str] = Field(default_factory=list)
    blocked_env: list[str] = Field(default_factory=list)
    network_enabled: bool = False

    model_config = {"extra": "forbid"}


class CLIAdapter(AgentAdapter):
    """通用 CLI 适配器 — 通过 ``subprocess.run`` 执行外部 CLI 工具。

    Parameters
    ----------
    config : CLIAdapterConfig | None
        适配器配置。为 ``None`` 时使用默认配置（空命令，需在
        ``sync_config`` 中设置后才能 ``connect``）。
    """

    def __init__(self, config: CLIAdapterConfig | None = None) -> None:
        self.config: CLIAdapterConfig = config or CLIAdapterConfig()
        self._connected: bool = False
        self._resolved_cmd: str = ""

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _validate_command(self) -> str:
        """验证命令在白名单内并返回解析后的可执行路径。

        Raises ``ValueError`` 如果命令为空或不在白名单内。
        """
        if not self.config.command or not self.config.command.strip():
            raise ValueError("CLIAdapterConfig.command is empty")
        try:
            cmd_parts = _split_command(self.config.command)
        except ValueError as exc:
            raise ValueError(
                f"Invalid command syntax '{self.config.command}': {exc}"
            ) from exc
        if not cmd_parts:
            raise ValueError("command produced empty argv after shlex.split")

        executable = cmd_parts[0]
        resolved = shutil.which(executable) or executable
        basename = Path(resolved).name
        # Windows: 去掉 .exe / .cmd / .bat 后缀以匹配白名单
        basename = re.sub(
            r"\.(?:exe|cmd|bat)$", "", basename, flags=re.IGNORECASE
        )
        if basename not in _StdioTransport.ALLOWED_COMMANDS:
            raise ValueError(
                f"CLI command '{executable}' (resolved to '{basename}') "
                f"is not in the whitelist. Refusing to execute."
            )
        return resolved

    def _validate_cwd(self) -> str | None:
        """验证 ``cwd`` 在 ``allowed_dirs`` 白名单内。

        Returns 解析后的绝对路径，或 ``None``（当 ``cwd`` 为空时）。
        Raises ``ValueError`` 如果 ``cwd`` 不在白名单子树内。
        """
        if not self.config.cwd:
            return None
        cwd_path = Path(self.config.cwd).resolve()
        if self.config.allowed_dirs:
            allowed = [Path(d).resolve() for d in self.config.allowed_dirs]
            if not any(
                cwd_path == a or _is_relative_to(cwd_path, a) for a in allowed
            ):
                raise ValueError(
                    f"cwd '{self.config.cwd}' is not within any allowed_dir "
                    f"in {self.config.allowed_dirs}"
                )
        return str(cwd_path)

    def _build_env(self) -> dict[str, str] | None:
        """构建子进程环境变量，屏蔽 ``blocked_env``。

        Returns ``None`` 表示继承当前环境（``subprocess.run`` 默认行为）。
        """
        if not self.config.env and not self.config.blocked_env:
            return None
        env = {**os.environ, **self.config.env}
        for key in self.config.blocked_env:
            env.pop(key, None)
        return env

    def _build_command(self, task: str) -> list[str]:
        """构建完整命令行 argv 列表。"""
        parts = _split_command(self.config.command)
        cmd = list(parts)
        cmd.extend(self.config.args)
        if self.config.task_arg_flag and not self.config.task_via_stdin:
            cmd.extend([self.config.task_arg_flag, task])
        return cmd

    def _parse_output(self, stdout: str) -> str:
        """按 ``output_format`` 解析子进程 stdout 并返回字符串。"""
        fmt = self.config.output_format
        if fmt == "text":
            return stdout
        if fmt == "json":
            data = json.loads(stdout)
            if self.config.output_json_key:
                return str(data.get(self.config.output_json_key, ""))
            return json.dumps(data, ensure_ascii=False)
        if fmt == "jsonl":
            lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
            objs = [json.loads(ln) for ln in lines]
            if self.config.output_json_key:
                return "\n".join(
                    str(o.get(self.config.output_json_key, "")) for o in objs
                )
            return "\n".join(json.dumps(o, ensure_ascii=False) for o in objs)
        raise ValueError(f"Unknown output_format: {fmt}")

    # ------------------------------------------------------------------
    # AgentAdapter 实现
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """验证命令可用并建立连接。"""
        try:
            self._resolved_cmd = self._validate_command()
            self._validate_cwd()
            self._connected = True
            logger.info(
                "[cli_adapter] Connected: %s (resolved: %s)",
                self.config.command,
                self._resolved_cmd,
            )
            return True
        except Exception as exc:
            logger.error("[cli_adapter] Connect failed: %s", exc)
            self._connected = False
            return False

    def execute(self, task: str, **kwargs: Any) -> str:
        """执行 CLI 命令并返回解析后的输出字符串。

        若未连接会自动调用 ``connect()``。超时抛出 ``TimeoutError``，
        非零退出码抛出 ``RuntimeError``。
        """
        if not self._connected and not self.connect():
            raise RuntimeError("CLIAdapter not connected — connect() failed")

        cmd = self._build_command(task)
        cwd = self._validate_cwd()
        env = self._build_env()
        stdin_input = task if self.config.task_via_stdin else None

        logger.debug("[cli_adapter] Executing: %s", cmd)
        try:
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_s,
                cwd=cwd,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"CLI '{self.config.command}' timed out after "
                f"{self.config.timeout_s}s"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"CLI '{self.config.command}' exited with code "
                f"{result.returncode}: {result.stderr.strip()}"
            )
        return self._parse_output(result.stdout)

    def health_check(self) -> bool:
        """检查命令是否可用（在白名单内且可解析）。"""
        try:
            self._validate_command()
            return True
        except Exception:
            return False

    def sync_config(self, config: dict[str, Any]) -> None:
        """用新配置字典替换当前配置。"""
        self.config = CLIAdapterConfig(**config)
        self._connected = False
        self._resolved_cmd = ""

    def disconnect(self) -> None:
        """断开连接（CLI 适配器无持久连接，仅重置状态）。"""
        self._connected = False
        self._resolved_cmd = ""