"""MAOP Config — Agent 引用校验器（M1 修复）.

启动时验证 agents.yaml 中 routing 段引用的所有 agent 名（primary / fallback /
tertiary）都在 agents 段中有定义，缺失时报错并给出明确指引。

使用方式::

    from maop.config.agents_validator import validate_routing, validate_routing_or_raise

    errors = validate_routing(agents_data, routing_data)
    if errors:
        for e in errors:
            print(e)

    # 或直接抛异常：
    validate_routing_or_raise(agents_data, routing_data)

设计要点
--------
- 纯函数：不读取文件，只接受已解析的 dict，便于单元测试。
- 不依赖 Pydantic 模型：直接操作原始 dict，避免循环导入。
- 错误信息中文，包含路由名、字段名、缺失的 agent 名，便于定位。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ValidationError",
    "validate_routing",
    "validate_routing_or_raise",
]


class ValidationError(Exception):
    """Agent 引用校验失败时抛出。

    Attributes
    ----------
    errors : list[str]
        所有错误的详细描述列表。
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _collect_defined_agents(agents_data: dict[str, Any]) -> set[str]:
    """从 agents 段收集所有已定义的 agent 名。"""
    agents_section = agents_data.get("agents", {})
    if not isinstance(agents_section, dict):
        return set()
    return set(agents_section.keys())


def _collect_route_refs(routing_data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """从 routing 段收集所有 (route_name, field, agent_name) 引用。

    field ∈ {"primary", "fallback", "tertiary"}，空字符串引用被跳过。
    """
    refs: list[tuple[str, str, str]] = []
    if not isinstance(routing_data, dict):
        return refs
    for route_name, route_entry in routing_data.items():
        if not isinstance(route_entry, dict):
            continue
        for field in ("primary", "fallback", "tertiary"):
            agent_name = route_entry.get(field, "")
            # 空字符串表示未设置（合法），跳过
            if agent_name:
                refs.append((route_name, field, agent_name))
    return refs


def validate_routing(agents_data: dict[str, Any], routing_data: dict[str, Any]) -> list[str]:
    """校验 routing 段引用的所有 agent 名在 agents 段中存在定义。

    Parameters
    ----------
    agents_data : dict
        agents.yaml 解析后的顶层 dict（应包含 "agents" 和 "routing" 键）。
    routing_data : dict
        routing 段的 dict。若 agents_data 已含 "routing"，可传 agents_data 本身。

    Returns
    -------
    list[str]
        错误描述列表。空列表表示校验通过。

    Examples
    --------
    >>> agents_data = {"agents": {"codex": {}}, "routing": {"chat": {"primary": "codex"}}}
    >>> validate_routing(agents_data, agents_data["routing"])
    []
    >>> agents_data = {"agents": {"codex": {}}, "routing": {"chat": {"primary": "claude"}}}
    >>> validate_routing(agents_data, agents_data["routing"])
    ['路由 chat.primary 引用未定义 agent: claude（已定义 agent: codex）']
    """
    defined = _collect_defined_agents(agents_data)
    refs = _collect_route_refs(routing_data)
    errors: list[str] = []

    # 已定义 agent 名排序后用于错误信息（便于用户排查）
    defined_display = ", ".join(sorted(defined)) if defined else "(空)"

    for route_name, field, agent_name in refs:
        if agent_name not in defined:
            errors.append(
                f"路由 {route_name}.{field} 引用未定义 agent: {agent_name}"
                f"（已定义 agent: {defined_display}）"
            )
    return errors


def validate_routing_or_raise(
    agents_data: dict[str, Any],
    routing_data: dict[str, Any],
) -> None:
    """校验 routing 引用，失败时抛出 :class:`ValidationError`。

    Parameters
    ----------
    agents_data : dict
        agents.yaml 解析后的顶层 dict。
    routing_data : dict
        routing 段的 dict。

    Raises
    ------
    ValidationError
        当存在未定义的 agent 引用时。
    """
    errors = validate_routing(agents_data, routing_data)
    if errors:
        raise ValidationError(errors)