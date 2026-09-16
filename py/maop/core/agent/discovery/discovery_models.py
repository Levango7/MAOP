"""Agent 发现模块的数据模型与常量定义.

本模块从 ``agent_discovery.py`` 拆分而来，集中存放与 Agent 自动发现相关的
纯数据定义，不包含任何扫描逻辑：

  - ``SCAN_TARGETS``：预定义扫描目标清单（桌面应用 / CLI / VS Code / JetBrains）
  - ``_VENDOR_MAP``：厂商映射（name → display_name, vendor）
  - ``_get_display_info``：根据 name 查询显示名和厂商的辅助函数
  - ``DiscoveredAgent``：发现 Agent 的 Pydantic 数据模型

向后兼容：``from maop.core.agent.discovery.agent_discovery import DiscoveredAgent``
仍然可用（``agent_discovery.py`` 会 re-export 本模块的符号）。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── 预定义扫描目标 ──────────────────────────────────────────────────
#: 桌面应用扫描清单（AI 代码编辑器 / IDE）
SCAN_TARGETS: dict[str, list[str]] = {
    "desktop": [
        "cursor",
        "trae",
        "qoder",
        "zcode",
        "catpaw",
        "codebuddy",
        "marscode",
        "deepseek-harness",
        "goose",
        "zed",
        "void",
        "melty",
        "pearai",
    ],
    #: CLI 工具扫描清单（PATH 中的命令行 Agent）
    "cli": [
        "claude-code",
        "codex",
        "aider",
        "opencode",
        "gemini-cli",
        "amazon-q-cli",
        "cline-cli",
        "goose",
        "inscode",
        "codearts",
    ],
    #: VS Code 扩展扫描清单（扩展 ID 或名称关键字）
    "vscode": [
        "github.copilot",
        "continue",
        "tabnine",
        "cline",
        "refact-ai",
        "tongyi-lingma",
        "codegeex",
        "baidu-comate",
    ],
    #: JetBrains 插件扫描清单（插件 ID 或名称关键字）
    "jetbrains": [
        "github.copilot",
        "tabnine",
        "codeium",
        "continue",
        "tongyi-lingma",
        "codegeex",
        "refact-ai",
    ],
}


# ── 厂商映射（用于补充 display_name 和 vendor）──────────────────────
_VENDOR_MAP: dict[str, tuple[str, str]] = {
    # name -> (display_name, vendor)
    "cursor": ("Cursor", "Anysphere"),
    "trae": ("Trae", "ByteDance"),
    "qoder": ("Qoder", "Alibaba"),
    "zcode": ("ZCode", "ZCode"),
    "catpaw": ("CatPaw", "Independent"),
    "codebuddy": ("CodeBuddy", "Tencent"),
    "marscode": ("MarsCode", "ByteDance"),
    "deepseek-harness": ("DeepSeek Harness", "DeepSeek"),
    "goose": ("Goose", "Block"),
    "zed": ("Zed", "Zed Industries"),
    "void": ("Void", "Independent"),
    "melty": ("Melty", "Independent"),
    "pearai": ("PearAI", "Independent"),
    "claude-code": ("Claude Code", "Anthropic"),
    "codex": ("Codex", "OpenAI"),
    "aider": ("Aider", "OpenSource"),
    "opencode": ("OpenCode", "OpenSource"),
    "gemini-cli": ("Gemini CLI", "Google"),
    "amazon-q-cli": ("Amazon Q CLI", "Amazon"),
    "cline-cli": ("Cline CLI", "OpenSource"),
    "inscode": ("InsCode", "CSDN"),
    "codearts": ("CodeArts", "Huawei"),
    "github.copilot": ("GitHub Copilot", "GitHub"),
    "continue": ("Continue", "OpenSource"),
    "tabnine": ("Tabnine", "Tabnine"),
    "cline": ("Cline", "OpenSource"),
    "refact-ai": ("Refact AI", "Refact"),
    "tongyi-lingma": ("Tongyi Lingma", "Alibaba"),
    "codegeex": ("CodeGeeX", "Zhipu"),
    "baidu-comate": ("Baidu Comate", "Baidu"),
    "codeium": ("Codeium", "Codeium"),
}


def _get_display_info(name: str) -> tuple[str, str]:
    """获取 Agent 的显示名和厂商.

    Parameters
    ----------
    name : str
        Agent 标识名。

    Returns
    -------
    tuple[str, str]
        (display_name, vendor)，未找到时返回 (name, "")。
    """
    info = _VENDOR_MAP.get(name)
    if info is not None:
        return info
    # 未知 Agent：首字母大写作为 display_name，vendor 留空
    return (name.replace("-", " ").replace(".", " ").title(), "")


# ── DiscoveredAgent 数据模型 ────────────────────────────────────────
class DiscoveredAgent(BaseModel):
    """发现的 Agent 信息描述符.

    Attributes
    ----------
    name : str
        Agent 标识名（如 ``"cursor"`` / ``"claude-code"``）。
    display_name : str
        展示名称（如 ``"Cursor"`` / ``"Claude Code"``）。
    vendor : str
        厂商/供应商（如 ``"Anysphere"`` / ``"Anthropic"``）。
    source : str
        发现来源：``"desktop"`` / ``"cli"`` / ``"vscode"`` / ``"jetbrains"``。
    path : str
        安装路径或可执行文件路径。
    version : str
        版本号（若可探测）。
    adapter_type : str
        建议的适配器类型：``"cli"`` / ``"http"`` / ``"mcp"`` / ``"web"``。
    already_registered : bool
        是否已在 AgentCatalog 中注册（由 ``auto_register`` 填充）。
    """

    name: str = Field(..., min_length=1, description="Agent 标识名")
    display_name: str = Field(default="", description="展示名称")
    vendor: str = Field(default="", description="厂商")
    source: str = Field(..., min_length=1, description="发现来源")
    path: str = Field(default="", description="安装路径")
    version: str = Field(default="", description="版本号")
    adapter_type: str = Field(default="cli", description="建议的适配器类型")
    already_registered: bool = Field(default=False, description="是否已注册")