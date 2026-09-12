"""Agent 配置模板模块 — 预定义适配器配置模板，一键生成 adapter_config.

提供 8 个开箱即用的配置模板，覆盖主流 AI Agent 的集成场景：

  - ``cli_basic``         — 基础 CLI 适配器（无认证）
  - ``cli_with_auth``     — 带 API Key 认证的 CLI
  - ``desktop_app``       — 桌面应用（CLI + IPC + HTTP 三路回退）
  - ``http_api``          — HTTP API 适配器（OpenAI 兼容等）
  - ``vscode_extension``  — VS Code 插件（WebSocket 通信）
  - ``jetbrains_plugin``  — JetBrains 插件（本地 HTTP 桥接）
  - ``mcp_server``        — MCP 服务器（stdio / SSE 传输）
  - ``vibe_platform``     — Vibe Coding 平台（Web 浏览器自动化）

设计要点：
  - **线程安全**：所有方法用 ``threading.RLock`` 保护。
  - **幂等**：``apply_template`` 每次返回全新 dict，不修改模板内部状态。
  - **覆盖优先**：``overrides`` 中的字段覆盖 ``default_config`` 中的同名字段。
  - **自动匹配**：``match_template`` 根据 Agent 名关键字匹配最佳模板。

Usage::

    from maop.core.agent.discovery.config_template import ConfigTemplateManager

    mgr = ConfigTemplateManager()
    template = mgr.get_template("cli_basic")
    config = mgr.apply_template("cli_with_auth", {"command": "claude-code", "api_key": "sk-xxx"})
    best = mgr.match_template("cursor")
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── ConfigTemplate 数据模型 ────────────────────────────────────────
class ConfigTemplate(BaseModel):
    """配置模板描述符.

    Attributes
    ----------
    name : str
        模板唯一标识名（如 ``"cli_basic"``）。
    description : str
        模板用途描述。
    adapter_type : str
        适配器类型：``"cli"`` / ``"http"`` / ``"mcp"`` / ``"web"``。
    default_config : dict
        默认配置字典，作为生成 ``adapter_config`` 的基础。
    required_fields : list[str]
        必填字段名列表——调用方必须通过 ``overrides`` 提供这些字段。
    optional_fields : list[str]
        可选字段名列表——有默认值，调用方可按需覆盖。
    """

    name: str = Field(..., min_length=1, description="模板名")
    description: str = Field(default="", description="模板描述")
    adapter_type: str = Field(..., description="适配器类型")
    default_config: dict[str, Any] = Field(
        default_factory=dict, description="默认配置"
    )
    required_fields: list[str] = Field(
        default_factory=list, description="必填字段"
    )
    optional_fields: list[str] = Field(
        default_factory=list, description="可选字段"
    )


# ── 预定义模板构造 ──────────────────────────────────────────────────
def _build_templates() -> dict[str, ConfigTemplate]:
    """构造所有预定义模板.

    Returns
    -------
    dict[str, ConfigTemplate]
        模板名到模板对象的映射。
    """
    templates: dict[str, ConfigTemplate] = {}

    # 1. cli_basic — 基础 CLI 适配器（无认证）
    templates["cli_basic"] = ConfigTemplate(
        name="cli_basic",
        description="基础 CLI 适配器，通过 subprocess 调用外部命令行工具，无需认证",
        adapter_type="cli",
        default_config={
            "command": "",
            "args": [],
            "cwd": "",
            "env": {},
            "timeout_s": 30.0,
            "network_enabled": False,
        },
        required_fields=["command"],
        optional_fields=["args", "cwd", "env", "timeout_s", "network_enabled"],
    )

    # 2. cli_with_auth — 带 API Key 认证的 CLI
    templates["cli_with_auth"] = ConfigTemplate(
        name="cli_with_auth",
        description="带 API Key 认证的 CLI 适配器，通过环境变量注入密钥",
        adapter_type="cli",
        default_config={
            "command": "",
            "args": [],
            "cwd": "",
            "env": {},
            "timeout_s": 60.0,
            "network_enabled": True,
            "api_key_env": "API_KEY",
            "api_key": "",
        },
        required_fields=["command", "api_key"],
        optional_fields=[
            "args", "cwd", "env", "timeout_s",
            "network_enabled", "api_key_env",
        ],
    )

    # 3. desktop_app — 桌面应用（CLI + IPC + HTTP 三路回退）
    templates["desktop_app"] = ConfigTemplate(
        name="desktop_app",
        description="桌面应用适配器，优先 CLI，回退到 IPC（Named Pipe/Unix Socket）和本地 HTTP",
        adapter_type="cli",
        default_config={
            "app_name": "",
            "cli_command": "",
            "cli_args": [],
            "ipc_path": "",
            "http_url": "",
            "http_token": "",
            "process_name": "",
            "fallback_order": ["cli", "ipc", "http"],
            "cli_timeout_s": 30.0,
            "ipc_timeout_s": 10.0,
            "http_timeout_s": 15.0,
        },
        required_fields=["app_name"],
        optional_fields=[
            "cli_command", "cli_args", "ipc_path", "http_url",
            "http_token", "process_name", "fallback_order",
            "cli_timeout_s", "ipc_timeout_s", "http_timeout_s",
        ],
    )

    # 4. http_api — HTTP API 适配器（OpenAI 兼容等）
    templates["http_api"] = ConfigTemplate(
        name="http_api",
        description="HTTP API 适配器，支持 OpenAI 兼容接口、自定义端点、流式输出",
        adapter_type="http",
        default_config={
            "base_url": "",
            "api_key": "",
            "model": "",
            "timeout_s": 120.0,
            "max_retries": 3,
            "streaming": True,
            "headers": {},
            "response_path": "choices.0.message.content",
        },
        required_fields=["base_url", "api_key", "model"],
        optional_fields=[
            "timeout_s", "max_retries", "streaming",
            "headers", "response_path",
        ],
    )

    # 5. vscode_extension — VS Code 插件（WebSocket 通信）
    templates["vscode_extension"] = ConfigTemplate(
        name="vscode_extension",
        description="VS Code 插件适配器，通过 WebSocket 与扩展进程通信",
        adapter_type="mcp",
        default_config={
            "extension_id": "",
            "extension_path": "",
            "ws_port": 0,
            "ws_url": "",
            "command": "code",
            "launch_args": ["--extensionDevelopmentPaths"],
            "timeout_s": 30.0,
            "auto_launch": False,
        },
        required_fields=["extension_id"],
        optional_fields=[
            "extension_path", "ws_port", "ws_url", "command",
            "launch_args", "timeout_s", "auto_launch",
        ],
    )

    # 6. jetbrains_plugin — JetBrains 插件（本地 HTTP 桥接）
    templates["jetbrains_plugin"] = ConfigTemplate(
        name="jetbrains_plugin",
        description="JetBrains 插件适配器，通过本地 HTTP 桥接与 IDE 插件通信",
        adapter_type="mcp",
        default_config={
            "plugin_id": "",
            "plugin_path": "",
            "http_port": 0,
            "http_url": "",
            "ide_name": "",
            "timeout_s": 30.0,
            "auto_connect": False,
        },
        required_fields=["plugin_id"],
        optional_fields=[
            "plugin_path", "http_port", "http_url", "ide_name",
            "timeout_s", "auto_connect",
        ],
    )

    # 7. mcp_server — MCP 服务器（stdio / SSE 传输）
    templates["mcp_server"] = ConfigTemplate(
        name="mcp_server",
        description="MCP 服务器适配器，支持 stdio 和 SSE 两种传输方式",
        adapter_type="mcp",
        default_config={
            "transport": "stdio",
            "command": "",
            "args": [],
            "env": {},
            "sse_url": "",
            "cwd": "",
            "timeout_s": 60.0,
            "auto_reconnect": True,
        },
        required_fields=["transport"],
        optional_fields=[
            "command", "args", "env", "sse_url",
            "cwd", "timeout_s", "auto_reconnect",
        ],
    )

    # 8. vibe_platform — Vibe Coding 平台（Web 浏览器自动化）
    templates["vibe_platform"] = ConfigTemplate(
        name="vibe_platform",
        description="Vibe Coding 平台适配器，通过浏览器自动化与 Web 端 AI 平台交互",
        adapter_type="web",
        default_config={
            "platform_url": "",
            "browser": "chromium",
            "headless": True,
            "session_token": "",
            "workspace_id": "",
            "timeout_s": 300.0,
            "viewport": {"width": 1280, "height": 720},
        },
        required_fields=["platform_url"],
        optional_fields=[
            "browser", "headless", "session_token", "workspace_id",
            "timeout_s", "viewport",
        ],
    )

    return templates


# ── Agent 名 → 模板名匹配规则 ──────────────────────────────────────
#: 桌面应用关键字 → desktop_app 模板
_DESKTOP_KEYWORDS: tuple[str, ...] = (
    "cursor", "trae", "qoder", "zcode", "catpaw", "codebuddy",
    "marscode", "zed", "void", "melty", "pearai", "windsurf",
    "deepseek-harness",
)

#: CLI 工具关键字 → cli_with_auth 或 cli_basic 模板
_CLI_AUTH_KEYWORDS: tuple[str, ...] = (
    "claude-code", "codex", "gemini-cli", "amazon-q",
)

#: CLI 无认证关键字 → cli_basic 模板
_CLI_BASIC_KEYWORDS: tuple[str, ...] = (
    "aider", "opencode", "goose", "inscode", "codearts",
    "cline-cli",
)

#: VS Code 扩展关键字 → vscode_extension 模板
_VSCODE_KEYWORDS: tuple[str, ...] = (
    "copilot", "continue", "tabnine", "cline", "refact",
    "lingma", "codegeex", "comate",
)

#: JetBrains 插件关键字 → jetbrains_plugin 模板
_JETBRAINS_KEYWORDS: tuple[str, ...] = (
    "codeium", "tabnine", "copilot-jetbrains",
)

#: HTTP API 关键字 → http_api 模板
_HTTP_API_KEYWORDS: tuple[str, ...] = (
    "deepseek", "kimi", "moonshot", "openai", "ollama",
    "zhipu", "qwen", "baichuan", "minimax",
)

#: Vibe Coding 平台关键字 → vibe_platform 模板
_VIBE_KEYWORDS: tuple[str, ...] = (
    "bolt", "v0", "lovable", "replit", "devin", "factory",
    "openhands", "swe-agent", "devika",
)

#: MCP 服务器关键字 → mcp_server 模板
_MCP_KEYWORDS: tuple[str, ...] = (
    "mcp", "server",
)


# ── ConfigTemplateManager 管理器 ───────────────────────────────────
class ConfigTemplateManager:
    """配置模板管理器 — 提供模板查询、应用、匹配功能.

    线程安全：所有方法用 ``threading.RLock`` 保护。
    模板在构造时一次性初始化，运行时不可变（只读）。

    Usage::

        mgr = ConfigTemplateManager()
        templates = mgr.list_templates()
        config = mgr.apply_template("cli_basic", {"command": "aider"})
        best = mgr.match_template("cursor")  # → "desktop_app"
    """

    def __init__(self) -> None:
        # RLock 允许同线程内嵌套加锁
        self._lock = threading.RLock()
        self._templates: dict[str, ConfigTemplate] = _build_templates()

    # ── 查询 ───────────────────────────────────────────────────────
    def get_template(self, template_name: str) -> ConfigTemplate | None:
        """按名获取模板.

        Parameters
        ----------
        template_name : str
            模板名（如 ``"cli_basic"``）。

        Returns
        -------
        ConfigTemplate | None
            模板对象，不存在返回 ``None``。
        """
        with self._lock:
            return self._templates.get(template_name)

    def list_templates(self) -> list[ConfigTemplate]:
        """列出所有可用模板.

        Returns
        -------
        list[ConfigTemplate]
            模板列表，按模板名排序。
        """
        with self._lock:
            return [
                self._templates[k]
                for k in sorted(self._templates.keys())
            ]

    # ── 应用 ───────────────────────────────────────────────────────
    def apply_template(
        self,
        template_name: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """应用模板生成 adapter_config.

        将 ``default_config`` 与 ``overrides`` 合并——``overrides`` 中的
        字段覆盖默认值。每次返回全新 dict，不修改模板内部状态。

        Parameters
        ----------
        template_name : str
            模板名。
        overrides : dict | None
            覆盖字段字典，默认为空 dict。

        Returns
        -------
        dict
            生成的 adapter_config 字典。

        Raises
        ------
        ValueError
            模板不存在时抛出。
        KeyError
            缺少必填字段时抛出。
        """
        if overrides is None:
            overrides = {}

        with self._lock:
            template = self._templates.get(template_name)
            if template is None:
                raise ValueError(
                    f"配置模板不存在: {template_name!r}，"
                    f"可用模板: {sorted(self._templates.keys())}"
                )

            # 深拷贝默认配置，避免修改模板内部状态
            config: dict[str, Any] = _deep_copy_dict(template.default_config)

            # 应用覆盖
            for key, value in overrides.items():
                config[key] = value

            # 校验必填字段
            missing: list[str] = []
            for field_name in template.required_fields:
                val = config.get(field_name)
                if val is None or (isinstance(val, str) and val == "") or (
                    isinstance(val, (list, dict)) and len(val) == 0
                ):
                    missing.append(field_name)

            if missing:
                raise KeyError(
                    f"模板 {template_name!r} 缺少必填字段: {missing}"
                )

            logger.debug(
                "[config_template] 应用模板 %s，生成 %d 个配置项",
                template_name,
                len(config),
            )
            return config

    # ── 匹配 ───────────────────────────────────────────────────────
    def match_template(self, agent_name: str) -> str | None:
        """根据 Agent 名匹配最佳模板.

        匹配规则（按优先级）：
          1. 桌面应用关键字 → ``desktop_app``
          2. VS Code 扩展关键字 → ``vscode_extension``
          3. JetBrains 插件关键字 → ``jetbrains_plugin``
          4. Vibe Coding 平台关键字 → ``vibe_platform``
          5. HTTP API 关键字 → ``http_api``
          6. CLI 带认证关键字 → ``cli_with_auth``
          7. CLI 无认证关键字 → ``cli_basic``
          8. MCP 关键字 → ``mcp_server``
          9. 兜底 → ``cli_basic``

        Parameters
        ----------
        agent_name : str
            Agent 标识名。

        Returns
        -------
        str | None
            匹配的模板名，无法匹配返回 ``None``。
        """
        with self._lock:
            lower_name = agent_name.lower()

            # 1. 桌面应用
            for kw in _DESKTOP_KEYWORDS:
                if kw in lower_name:
                    return "desktop_app"

            # 2. VS Code 扩展
            for kw in _VSCODE_KEYWORDS:
                if kw in lower_name:
                    return "vscode_extension"

            # 3. JetBrains 插件
            for kw in _JETBRAINS_KEYWORDS:
                if kw in lower_name:
                    return "jetbrains_plugin"

            # 4. Vibe Coding 平台
            for kw in _VIBE_KEYWORDS:
                if kw in lower_name:
                    return "vibe_platform"

            # 5. HTTP API
            for kw in _HTTP_API_KEYWORDS:
                if kw in lower_name:
                    return "http_api"

            # 6. CLI 带认证
            for kw in _CLI_AUTH_KEYWORDS:
                if kw in lower_name:
                    return "cli_with_auth"

            # 7. CLI 无认证
            for kw in _CLI_BASIC_KEYWORDS:
                if kw in lower_name:
                    return "cli_basic"

            # 8. MCP 服务器
            for kw in _MCP_KEYWORDS:
                if kw in lower_name:
                    return "mcp_server"

            # 9. 兜底：返回 cli_basic
            return "cli_basic"


# ── 辅助函数 ────────────────────────────────────────────────────────
def _deep_copy_dict(d: dict[str, Any]) -> dict[str, Any]:
    """深拷贝字典.

    对嵌套 dict / list 递归拷贝，确保返回值与原字典完全独立。

    Parameters
    ----------
    d : dict
        源字典。

    Returns
    -------
    dict
        深拷贝后的新字典。
    """
    result: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = _deep_copy_dict(value)
        elif isinstance(value, list):
            # 列表元素可能是 dict，需要递归拷贝
            result[key] = [
                _deep_copy_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result