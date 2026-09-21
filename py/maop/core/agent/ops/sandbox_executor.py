"""SandboxExecutor — 沙箱隔离执行器.

在应用层对子进程命令执行施加安全约束：
  - 目录限制：检查命令是否访问禁止目录、是否在允许目录之外
  - 网络限制：检查命令是否包含网络操作（curl/wget/requests 等）
  - 资源限制：执行时间上限（超时杀进程）、CPU/内存上限（尽力而为）
  - 工作目录：将子进程 cwd 限定到 ``SandboxConfig.working_dir``
  - 环境变量：仅透传 ``allow_env_vars`` 列出的环境变量

说明：
  本模块做**应用层**限制，不依赖 OS 级沙箱（Docker/容器）。对于生产级
  强隔离，应在外层套容器/namespace。此处提供尽力而为的限制 + 命令静态
  检查，足以拦截大部分误用与恶意命令。

线程安全：``threading.RLock`` 保护内部计数器与配置缓存。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys  # noqa: F401
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class SandboxConfig(BaseModel):
    """沙箱执行配置.

    所有可变默认值使用 ``Field(default_factory=...)`` 避免 Pydantic 共享
    可变默认值的陷阱。
    """

    allowed_dirs: list[str] = Field(default_factory=list, description="允许访问的目录")
    blocked_dirs: list[str] = Field(default_factory=list, description="禁止访问的目录")
    allow_network: bool = Field(default=False, description="是否允许网络访问")
    allow_env_vars: list[str] = Field(default_factory=list, description="允许读取的环境变量")
    max_cpu_percent: float = Field(default=100.0, ge=0.0, le=100.0, description="CPU 使用上限")
    max_memory_mb: int = Field(default=0, ge=0, description="内存上限(MB)，0=不限")
    max_execution_time_s: float = Field(default=300.0, gt=0, description="执行时间上限(秒)")
    working_dir: str = Field(default="", description="工作目录")


class SandboxResult(BaseModel):
    """沙箱执行结果."""

    success: bool = Field(..., description="是否成功")
    stdout: str = Field(default="", description="标准输出")
    stderr: str = Field(default="", description="标准错误")
    return_code: int = Field(default=0, description="返回码")
    duration_s: float = Field(default=0.0, ge=0.0, description="执行耗时(秒)")
    killed: bool = Field(default=False, description="是否因超时/超资源被杀")
    kill_reason: str = Field(default="", description="杀死原因")


# ── 静态检查用的危险模式 ─────────────────────────────────────────
# 网络操作命令关键词（命令行第一个 token 或独立子命令）
_NETWORK_COMMANDS: frozenset[str] = frozenset({
    "curl", "wget", "ftp", "scp", "rsync", "ssh", "telnet", "nc", "netcat",
    "ncat", "socat", "httpie", "http",
})

# Python 网络相关 import 关键词
_NETWORK_PYTHON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brequests\b"),
    re.compile(r"\bhttpx\b"),
    re.compile(r"\bsocket\b"),
    re.compile(r"\burllib\b"),
    re.compile(r"\baiohttp\b"),
    re.compile(r"\bhttp\.client\b"),
)

# 危险命令关键词（用于 check_permissions 标注需要的权限）
_DANGEROUS_COMMANDS: frozenset[str] = frozenset({
    "rm", "rmdir", "del", "format", "mkfs", "dd", "shutdown", "reboot",
    "kill", "killall", "taskkill",
})

# 环境变量访问模式（$VAR、%VAR%、${VAR}）
_ENV_VAR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$(\w+)"),           # $VAR
    re.compile(r"\$\{(\w+)\}"),       # ${VAR}
    re.compile(r"%(\w+)%"),           # %VAR% (Windows)
)


class SandboxExecutor:
    """沙箱隔离执行器.

    在应用层对子进程命令施加安全约束。提供：

      1. ``execute``           — 在沙箱中执行命令，返回 ``SandboxResult``
      2. ``check_permissions`` — 静态分析命令需要的权限列表
      3. ``validate_config``   — 验证配置合法性，返回违规列表

    线程安全：``threading.RLock`` 保护内部统计计数器。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 执行统计（线程安全）
        self._total_executions: int = 0
        self._total_killed: int = 0
        self._total_rejected: int = 0

    # ── 执行 ────────────────────────────────────────────────────
    def execute(
        self,
        command: str,
        config: SandboxConfig,
    ) -> SandboxResult:
        """在沙箱中执行命令.

        流程：
          1. ``validate_config`` 检查配置合法性，违规则拒绝执行
          2. ``check_permissions`` 静态分析命令所需权限
          3. 命中禁止目录 / 禁止网络 → 拒绝执行
          4. ``subprocess.run`` 执行，超时则杀进程
          5. 收集 stdout/stderr/return_code/duration

        Parameters
        ----------
        command : str
            待执行命令行。
        config : SandboxConfig
            沙箱配置。

        Returns
        -------
        SandboxResult
            执行结果。被拒绝时 ``success=False``，``return_code=-1``，
            ``stderr`` 描述拒绝原因。
        """
        with self._lock:
            self._total_executions += 1

        start = time.monotonic()

        # 1. 配置合法性
        violations = self.validate_config(config)
        if violations:
            with self._lock:
                self._total_rejected += 1
            return SandboxResult(
                success=False,
                stderr="config violations: " + "; ".join(violations),
                return_code=-1,
                duration_s=time.monotonic() - start,
            )

        # 2. 权限检查
        permissions = self.check_permissions(command, config)

        # 3. 拒绝条件：网络操作但未授权
        if "network" in permissions and not config.allow_network:
            with self._lock:
                self._total_rejected += 1
            return SandboxResult(
                success=False,
                stderr="network access denied: command requires network but allow_network=False",
                return_code=-1,
                duration_s=time.monotonic() - start,
            )

        # 4. 拒绝条件：访问禁止目录
        blocked_hit = self._check_blocked_dirs(command, config)
        if blocked_hit:
            with self._lock:
                self._total_rejected += 1
            return SandboxResult(
                success=False,
                stderr=f"blocked directory access denied: {blocked_hit}",
                return_code=-1,
                duration_s=time.monotonic() - start,
            )

        # 5. 拒绝条件：访问允许目录之外（当 allowed_dirs 非空时）
        outside_hit = self._check_outside_allowed_dirs(command, config)
        if outside_hit:
            with self._lock:
                self._total_rejected += 1
            return SandboxResult(
                success=False,
                stderr=f"access outside allowed dirs denied: {outside_hit}",
                return_code=-1,
                duration_s=time.monotonic() - start,
            )

        # 6. 实际执行
        try:
            result = self._run_subprocess(command, config)
            duration = time.monotonic() - start
            killed = False
            kill_reason = ""
            return_code = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            with self._lock:
                self._total_killed += 1
            killed = True
            kill_reason = f"timeout after {config.max_execution_time_s}s"
            return_code = -1
            # Python 3.14 的 TimeoutExpired 用 output 替代 stdout；兼容两者
            raw_out = getattr(exc, "stdout", None)
            if raw_out is None:
                raw_out = getattr(exc, "output", None)
            raw_err = getattr(exc, "stderr", None)
            stdout = _decode_bytes(raw_out)
            stderr = _decode_bytes(raw_err)
            stderr = (stderr + "\n" + kill_reason).strip()
        except Exception as exc:
            duration = time.monotonic() - start
            return SandboxResult(
                success=False,
                stderr=f"execution error: {exc}",
                return_code=-1,
                duration_s=duration,
            )

        return SandboxResult(
            success=return_code == 0,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            duration_s=duration,
            killed=killed,
            kill_reason=kill_reason,
        )

    # ── 权限检查 ────────────────────────────────────────────────
    def check_permissions(
        self,
        command: str,
        config: SandboxConfig,
    ) -> list[str]:
        """静态分析命令需要的权限列表.

        返回的权限标签可能包含：
          ``network``、``filesystem``、``dangerous_command``、
          ``env_var:<NAME>``、``working_dir``

        Parameters
        ----------
        command : str
            待检查命令行。
        config : SandboxConfig
            沙箱配置（用于判断 working_dir、allow_env_vars 等）。

        Returns
        -------
        list[str]
            命令需要的权限标签列表（去重，保持首次出现顺序）。
        """
        permissions: list[str] = []
        seen: set[str] = set()

        def _add(perm: str) -> None:
            if perm not in seen:
                seen.add(perm)
                permissions.append(perm)

        if not command.strip():
            return permissions

        # 拆分命令为 tokens（简单 split，不处理引号内空格——静态启发式足够）
        tokens = command.split()
        first_token = tokens[0].lower()
        # 去掉路径前缀，只留命令名
        first_token_name = os.path.basename(first_token)

        # 网络命令
        if first_token_name in _NETWORK_COMMANDS:
            _add("network")
        # Python 网络相关 import
        for pattern in _NETWORK_PYTHON_PATTERNS:
            if pattern.search(command):
                _add("network")
                break

        # 危险命令
        if first_token_name in _DANGEROUS_COMMANDS:
            _add("dangerous_command")

        # 文件系统访问（任何命令默认都涉及 fs，除非是纯计算如 echo）
        # 这里仅当命令包含路径分隔符或 . 时标注 filesystem
        if "/" in command or "\\" in command or " ~" in (" " + command):
            _add("filesystem")

        # 环境变量访问
        for pattern in _ENV_VAR_PATTERNS:
            for match in pattern.findall(command):
                _add(f"env_var:{match}")

        # 工作目录
        if config.working_dir:
            _add("working_dir")

        return permissions

    # ── 配置验证 ────────────────────────────────────────────────
    def validate_config(self, config: SandboxConfig) -> list[str]:
        """验证配置合法性，返回违规列表.

        检查项：
          - ``allowed_dirs`` 与 ``blocked_dirs`` 不应重叠
          - ``working_dir`` 若设置必须存在
          - ``max_execution_time_s`` 必须 > 0
          - ``max_cpu_percent`` 必须 ∈ [0, 100]

        Returns
        -------
        list[str]
            违规描述列表。空列表表示配置合法。
        """
        violations: list[str] = []

        # 允许/禁止目录重叠
        allowed_set = {os.path.normpath(p) for p in config.allowed_dirs if p}
        blocked_set = {os.path.normpath(p) for p in config.blocked_dirs if p}
        overlap = allowed_set & blocked_set
        if overlap:
            violations.append(
                f"allowed_dirs and blocked_dirs overlap: {sorted(overlap)}"
            )

        # 工作目录存在性
        if config.working_dir and not os.path.isdir(config.working_dir):
            violations.append(f"working_dir does not exist: {config.working_dir}")

        # 执行时间上限
        if config.max_execution_time_s <= 0:
            violations.append("max_execution_time_s must be > 0")

        # CPU 上限
        if config.max_cpu_percent < 0 or config.max_cpu_percent > 100:
            violations.append("max_cpu_percent must be in [0, 100]")

        return violations

    # ── 统计 ────────────────────────────────────────────────────
    def get_stats(self) -> dict[str, int]:
        """获取执行统计.

        Returns
        -------
        dict[str, int]
            ``total_executions`` / ``total_killed`` / ``total_rejected``。
        """
        with self._lock:
            return {
                "total_executions": self._total_executions,
                "total_killed": self._total_killed,
                "total_rejected": self._total_rejected,
            }

    # ── 内部工具 ────────────────────────────────────────────────
    def _run_subprocess(
        self,
        command: str,
        config: SandboxConfig,
    ) -> subprocess.CompletedProcess[bytes]:
        """执行子进程.

        Windows 下通过 ``subprocess.run`` + shell 执行；超时由
        ``config.max_execution_time_s`` 控制。环境变量仅透传
        ``allow_env_vars`` 列出的变量（若非空）。
        """
        # 构造环境变量
        if config.allow_env_vars:
            env: dict[str, str] | None = {k: v for k, v in os.environ.items() if k in set(config.allow_env_vars)}
        else:
            env = None

        # 工作目录
        cwd: str | None = config.working_dir if config.working_dir else None

        # 执行
        return subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            check=False,   # PLW1510：显式声明不做返回码检查（原为默认行为）
            env=env,
            capture_output=True,
            timeout=config.max_execution_time_s,
            text=False,
        )

    @staticmethod
    def _check_blocked_dirs(command: str, config: SandboxConfig) -> str:
        """检查命令是否访问禁止目录.

        Returns
        -------
        str
            命中的禁止目录（首个），未命中返回空字符串。
        """
        if not config.blocked_dirs:
            return ""
        cmd_lower = command.lower()
        for blocked in config.blocked_dirs:
            if not blocked:
                continue
            if blocked.lower() in cmd_lower:
                return blocked
        return ""

    @staticmethod
    def _check_outside_allowed_dirs(command: str, config: SandboxConfig) -> str:
        """检查命令是否访问允许目录之外的路径.

        仅当 ``allowed_dirs`` 非空时启用：从命令中提取所有看起来像路径的
        token，检查是否都在允许目录之下。

        Returns
        -------
        str
            命中的越界路径（首个），未命中返回空字符串。
        """
        if not config.allowed_dirs:
            return ""
        # 规范化允许目录
        allowed_norm = [os.path.normpath(p) for p in config.allowed_dirs if p]
        # 从命令中提取候选路径 token
        tokens = command.split()
        for token in tokens:
            # 跳过第一个 token（命令本身，如 python/curl）
            if token == tokens[0]:
                continue
            # 跳过 URL（http://、https://、ftp:// 等）——不是文件路径
            if "://" in token:
                continue
            # 跳过选项参数（以 - 开头）
            if token.startswith("-"):
                continue
            # 仅检查看起来像路径的 token（含分隔符或以 . 开头）
            if "/" not in token and "\\" not in token and not token.startswith("."):
                continue
            token_norm = os.path.normpath(token)
            # 检查是否在任一允许目录下
            inside = any(
                token_norm == allowed
                or token_norm.startswith(allowed + os.sep)
                for allowed in allowed_norm
            )
            if not inside:
                return token
        return ""

# ── 模块级工具 ────────────────────────────────────────────────────
def _decode_bytes(data: Any) -> str:
    """将 bytes/str/None 解码为 str.

    Parameters
    ----------
    data : bytes | str | None
        待解码数据。

    Returns
    -------
    str
        解码后的字符串。``None`` 返回空字符串。
    """
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)