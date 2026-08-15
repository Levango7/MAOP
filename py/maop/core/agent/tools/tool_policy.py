"""MAOP ToolPolicy — 工具白名单策略（deny/allow/mode 三阶段过渡）。

设计动机
--------
``ToolManager.call()`` 对任意已入库 tool 直接执行（shlex.split +
create_subprocess_exec），此前无命令级权限校验。``ToolPolicy`` 加载
``config/tool_whitelist.yaml``，按以下顺序决策：

1. deny 规则命中        -> 拒绝
2. allow 规则命中        -> 放行
3. 均未命中              -> 按 ``mode``：
   - ``audit``   -> 放行 + warning（三阶段过渡的阶段一：收集调用清单）
   - ``enforce`` -> 拒绝

三阶段过渡（用户已拍板）：
- 阶段一（audit，当前默认）：所有未放行工具可执行，但记录 warning。
- 阶段二：根据审计日志导出初始 allow 列表。
- 阶段三：切 enforce，未放行工具被拒绝。

Fail-open：配置文件缺失 / 解析失败 / YAML 不可用 -> 降级到 audit + warning，
避免策略文件损坏导致全平台工具瘫痪。

环境变量：
- ``MAOP_TOOL_POLICY_MODE``    覆盖 mode（audit | enforce）
- ``MAOP_TOOL_POLICY_CONFIG``  覆盖配置文件路径

Usage::

    from maop.core.agent.tools.tool_policy import ToolPolicy

    policy = ToolPolicy()
    decision = policy.check("lint", "ruff check src/")
    if not decision.allowed:
        # 拒绝执行
        ...
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV_POLICY_MODE = "MAOP_TOOL_POLICY_MODE"
ENV_POLICY_CONFIG = "MAOP_TOOL_POLICY_CONFIG"
DEFAULT_CONFIG_NAME = "tool_whitelist.yaml"
VALID_MODES = ("audit", "enforce")


@dataclass(frozen=True)
class PolicyDecision:
    """一次策略评估的结果。

    Attributes
    ----------
    allowed : bool
        是否允许执行。
    reason : str
        拒绝原因（仅 ``allowed=False`` 时非空）。
    matched : str
        命中来源：``"deny"`` / ``"allow"`` / ``""``（按 mode 兜底）。
    """

    allowed: bool
    reason: str = ""
    matched: str = ""


class ToolPolicy:
    """加载并执行工具白名单策略。

    Parameters
    ----------
    config_path : str | Path, optional
        配置文件路径。默认按 ``MAOP_TOOL_POLICY_CONFIG`` 环境变量，否则
        定位到项目根 ``config/tool_whitelist.yaml``。
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else self._resolve_config_path()
        self._mode = "audit"
        self._allow_rules: list[dict[str, str]] = []
        self._deny_rules: list[dict[str, str]] = []
        self._load()

    # ── 配置解析 ────────────────────────────────────────────────

    @classmethod
    def _resolve_config_path(cls) -> Path:
        """解析配置文件路径：env > 项目根 config/。"""
        env_path = os.getenv(ENV_POLICY_CONFIG, "").strip()
        if env_path:
            return Path(env_path)
        from maop.core.backends.db_utils import find_project_root

        return find_project_root() / "config" / DEFAULT_CONFIG_NAME

    def _load(self) -> None:
        """读取配置文件。失败时 fail-open 到 audit + warning。"""
        try:
            data = _load_yaml(self._config_path)
        except Exception as exc:
            self._mode = "audit"
            self._allow_rules = []
            self._deny_rules = []
            logger.warning(
                "[tool_policy] failed to load %s; fail-open to audit mode: %s",
                self._config_path, exc,
            )
            return

        if data is None:
            self._mode = "audit"
            self._allow_rules = []
            self._deny_rules = []
            logger.warning(
                "[tool_policy] config %s is empty; fail-open to audit mode",
                self._config_path,
            )
            return

        raw_mode = str(data.get("mode", "audit")).strip().lower()
        if raw_mode not in VALID_MODES:
            logger.warning(
                "[tool_policy] invalid mode %r in %s; falling back to 'audit'",
                raw_mode, self._config_path,
            )
            raw_mode = "audit"
        self._mode = raw_mode

        self._allow_rules = [
            self._normalize_rule(r) for r in (data.get("allow") or [])
        ]
        self._deny_rules = [
            self._normalize_rule(r) for r in (data.get("deny") or [])
        ]

    @staticmethod
    def _normalize_rule(rule: Any) -> dict[str, str]:
        """规范化单条规则：``str`` 视为精确 id；``dict`` 取 id/pattern。"""
        if isinstance(rule, str):
            return {"id": rule}
        if isinstance(rule, dict):
            out: dict[str, str] = {}
            if rule.get("id"):
                out["id"] = str(rule["id"])
            if rule.get("pattern"):
                out["pattern"] = str(rule["pattern"])
            return out
        logger.warning("[tool_policy] skipping invalid rule: %r", rule)
        return {}

    # ── 只读属性 ────────────────────────────────────────────────

    @property
    def config_path(self) -> Path:
        """当前生效的配置文件路径。"""
        return self._config_path

    @property
    def mode(self) -> str:
        """当前策略模式：env 覆盖优先，其次配置文件。"""
        env_mode = os.getenv(ENV_POLICY_MODE, "").strip().lower()
        if env_mode in VALID_MODES:
            return env_mode
        return self._mode

    @property
    def allow_rules(self) -> list[dict[str, str]]:
        """allow 规则副本（供审计导出阶段使用）。"""
        return list(self._allow_rules)

    @property
    def deny_rules(self) -> list[dict[str, str]]:
        """deny 规则副本。"""
        return list(self._deny_rules)

    # ── 决策 ────────────────────────────────────────────────────

    def check(self, tool_id: str, command: str = "") -> PolicyDecision:
        """评估给定工具是否允许执行。

        Parameters
        ----------
        tool_id : str
            工具 ID（``ToolDef.id``）。
        command : str
            工具命令（``ToolDef.command``），用于 pattern 匹配。

        Returns
        -------
        PolicyDecision
            ``allowed=False`` 表示应拒绝执行（含 reason）。
        """
        if self._match_rules(self._deny_rules, tool_id, command):
            return PolicyDecision(
                allowed=False,
                reason="denied by deny rule",
                matched="deny",
            )
        if self._match_rules(self._allow_rules, tool_id, command):
            return PolicyDecision(allowed=True, reason="", matched="allow")
        if self.mode == "enforce":
            return PolicyDecision(
                allowed=False,
                reason="not in allow list (mode=enforce)",
                matched="",
            )
        # audit mode：未命中规则 -> 放行但告警（三阶段过渡阶段一）。
        logger.warning(
            "[tool_policy] audit: tool %r not in whitelist (mode=audit, will allow); command=%r",
            tool_id, command,
        )
        return PolicyDecision(allowed=True, reason="", matched="audit")

    @staticmethod
    def _match_rules(
        rules: list[dict[str, str]],
        tool_id: str,
        command: str,
    ) -> bool:
        """判断 tool_id / command 是否命中任一规则。

        ``id`` 精确匹配 tool_id；``pattern`` 用 fnmatch 同时匹配
        tool_id 与 command（命令级拦截如 ``pattern: "rm*"``）。
        """
        for rule in rules:
            rid = rule.get("id")
            if rid and rid == tool_id:
                return True
            pattern = rule.get("pattern")
            if pattern:
                if fnmatch.fnmatchcase(tool_id, pattern):
                    return True
                if command and fnmatch.fnmatchcase(command, pattern):
                    return True
        return False


def _load_yaml(path: Path) -> Any:
    """读取 YAML 文件；缺失 / 解析失败时抛出异常由调用方 fail-open。"""
    if not path.exists():
        raise FileNotFoundError(f"tool policy config not found: {path}")
    import yaml

    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


__all__ = ["ToolPolicy", "PolicyDecision", "ENV_POLICY_MODE", "ENV_POLICY_CONFIG"]
