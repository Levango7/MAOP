"""MAOP MCP Permission Checker — per-server tool/resource access scoping.

Phase δ-3: Adds a layer between the dashboard's coarse ``require_admin``
check and the actual MCP tool invocation. Each :class:`MCPServerConfig`
may carry four optional restriction dimensions:

  - ``allowed_tools`` / ``denied_tools``: per-tool name scoping
  - ``allowed_users``               : per-caller user_id scoping
  - ``allowed_roles``               : per-caller role scoping

Blacklist (``denied_tools``) takes precedence over whitelist
(``allowed_tools``) — a tool present in both is *denied*.

Usage::

    from maop.core.mcp_permission import MCPPermissionChecker
    from maop.core.mcp_hub import MCPServerConfig

    checker = MCPPermissionChecker()
    cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"], denied_tools=["delete_file"])
    decision = checker.check_tool_permission(cfg, "read_file", {"user_id": "alice", "roles": ["admin"]})
    if not decision.allowed:
        raise PermissionError(decision.reason)

The checker is stateless per call; it reads all scoping from the supplied
:class:`MCPServerConfig` and an optional ``user_context`` dict. A
``user_context_provider`` callable can be injected at construction time
to resolve the current caller (e.g. from request-scoped state); an
explicit ``user_context`` argument to ``check_tool_permission`` overrides
the provider for that single check.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from maop.core.mcp_hub import MCPServerConfig

logger = logging.getLogger(__name__)


@dataclass
class MCPPermissionDecision:
    """Outcome of a single permission check.

    Attributes
    ----------
    allowed:
        Whether the action was permitted.
    reason:
        Human-readable rationale, intended for the audit log so a
        reviewer can reconstruct why a call was rejected.
    matched_rule:
        Identifier of the rule that produced this decision, e.g.
        ``"denied_tools blacklist"`` / ``"allowed_tools whitelist"`` /
        ``"user whitelist"`` / ``"role whitelist"`` / ``"default allow"``.
        Empty string only when no rules were evaluated.
    """

    allowed: bool
    reason: str
    matched_rule: str = ""

    def __bool__(self) -> bool:  # convenience: ``if decision:``
        return self.allowed


@dataclass
class _MCPPermissionDefaults:
    """Sentinel-style namespace for matched-rule label strings.

    Centralised so the audit logger and tests reference the same labels
    that ``MCPPermissionChecker`` writes — drift would otherwise make
    audit records hard to filter by reason.
    """

    DENIED_TOOLS: str = "denied_tools blacklist"
    ALLOWED_TOOLS: str = "allowed_tools whitelist"
    ALLOWED_USERS: str = "user whitelist"
    ALLOWED_ROLES: str = "role whitelist"
    DEFAULT_ALLOW: str = "default allow"


_RULE = _MCPPermissionDefaults()


class MCPPermissionChecker:
    """Per-server permission scope evaluator.

    Parameters
    ----------
    user_context_provider:
        Optional zero-argument callable returning a dict shaped
        ``{"user_id": str, "roles": list[str]}``. When supplied, the
        checker uses it to populate the user/role dimensions whenever
        the caller does not pass an explicit ``user_context``. When
        ``None``, the user/role dimensions are skipped — only the
        tool-name dimensions are enforced.

    Notes
    -----
    The checker is intentionally synchronous: permission decisions must
    not block on I/O, otherwise the MCPHub.call_tool fast path degrades.
    Long-running lookups (e.g. RBAC refresh from PG) should be cached by
    the provider before exposing it here.
    """

    def __init__(
        self,
        user_context_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._user_context_provider = user_context_provider

    # ── Tool invocation checks ──────────────────────────────────

    def check_tool_permission(
        self,
        server_config: MCPServerConfig,
        tool_name: str,
        user_context: dict[str, Any] | None = None,
    ) -> MCPPermissionDecision:
        """Evaluate whether ``tool_name`` may be invoked on ``server_config``.

        Check order (short-circuits on first rejection):

        1. ``denied_tools`` blacklist  → reject if tool listed
        2. ``allowed_tools`` whitelist → reject if non-None and tool absent
        3. ``allowed_users`` whitelist → reject if non-None and user absent
        4. ``allowed_roles`` whitelist → reject if non-None and roles disjoint
        5. default allow
        """
        # 1. Blacklist first (precedence over whitelist).
        if server_config.denied_tools and tool_name in server_config.denied_tools:
            return MCPPermissionDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is on the denied_tools blacklist of server '{server_config.name}'",
                matched_rule=_RULE.DENIED_TOOLS,
            )

        # 2. Tool whitelist.
        if server_config.allowed_tools is not None and tool_name not in server_config.allowed_tools:
            return MCPPermissionDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' not in allowed_tools whitelist of server '{server_config.name}'",
                matched_rule=_RULE.ALLOWED_TOOLS,
            )

        # Resolve caller context — explicit arg wins over provider.
        ctx = self._resolve_user_context(user_context)
        user_id = str((ctx or {}).get("user_id", "") or "")
        roles = list((ctx or {}).get("roles", []) or [])

        # 3. User whitelist (only enforced when we have a user_id to test).
        if server_config.allowed_users is not None:
            if not user_id or user_id not in server_config.allowed_users:
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"User '{user_id or '<anonymous>'}' not in allowed_users "
                        f"whitelist of server '{server_config.name}'"
                    ),
                    matched_rule=_RULE.ALLOWED_USERS,
                )

        # 4. Role whitelist — any overlap with caller roles is sufficient.
        if server_config.allowed_roles is not None:
            caller_roles = set(roles) if roles else set()
            if not caller_roles:
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"Caller has no roles; allowed_roles whitelist of "
                        f"server '{server_config.name}' requires one of "
                        f"{server_config.allowed_roles}"
                    ),
                    matched_rule=_RULE.ALLOWED_ROLES,
                )
            if not (caller_roles & set(server_config.allowed_roles)):
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"Caller roles {sorted(caller_roles)} have no overlap with "
                        f"allowed_roles {server_config.allowed_roles} of server "
                        f"'{server_config.name}'"
                    ),
                    matched_rule=_RULE.ALLOWED_ROLES,
                )

        # 5. Default allow.
        return MCPPermissionDecision(
            allowed=True,
            reason=f"No matching deny rule; default allow on server '{server_config.name}'",
            matched_rule=_RULE.DEFAULT_ALLOW,
        )

    # ── Resource read checks ───────────────────────────────────

    def check_resource_permission(
        self,
        server_config: MCPServerConfig,
        resource_uri: str,
        user_context: dict[str, Any] | None = None,
    ) -> MCPPermissionDecision:
        """Evaluate whether ``resource_uri`` may be read on ``server_config``.

        The four scope dimensions are reused but applied to the URI:

        - ``denied_tools``   → treated as a URI blacklist (exact + glob)
        - ``allowed_tools``  → treated as a URI whitelist (exact + glob)
        - ``allowed_users``  → unchanged (caller-level scope)
        - ``allowed_roles``  → unchanged (caller-level scope)

        Glob support uses :func:`fnmatch.fnmatchcase` so patterns like
        ``file:///tmp/*`` work as expected. ``*`` matches any URI.
        """
        # 1. Deny glob patterns.
        if server_config.denied_tools:
            for pat in server_config.denied_tools:
                if _glob_match(pat, resource_uri):
                    return MCPPermissionDecision(
                        allowed=False,
                        reason=(
                            f"Resource '{resource_uri}' matches denied pattern "
                            f"'{pat}' on server '{server_config.name}'"
                        ),
                        matched_rule=_RULE.DENIED_TOOLS,
                    )

        # 2. Allow glob patterns.
        if server_config.allowed_tools is not None:
            if not any(_glob_match(pat, resource_uri) for pat in server_config.allowed_tools):
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"Resource '{resource_uri}' matches no entry in allowed_tools "
                        f"whitelist of server '{server_config.name}'"
                    ),
                    matched_rule=_RULE.ALLOWED_TOOLS,
                )

        ctx = self._resolve_user_context(user_context)
        user_id = str((ctx or {}).get("user_id", "") or "")
        roles = list((ctx or {}).get("roles", []) or [])

        # 3. User whitelist.
        if server_config.allowed_users is not None:
            if not user_id or user_id not in server_config.allowed_users:
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"User '{user_id or '<anonymous>'}' not in allowed_users "
                        f"whitelist of server '{server_config.name}'"
                    ),
                    matched_rule=_RULE.ALLOWED_USERS,
                )

        # 4. Role whitelist.
        if server_config.allowed_roles is not None:
            caller_roles = set(roles) if roles else set()
            if not caller_roles:
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"Caller has no roles; allowed_roles whitelist of "
                        f"server '{server_config.name}' requires one of "
                        f"{server_config.allowed_roles}"
                    ),
                    matched_rule=_RULE.ALLOWED_ROLES,
                )
            if not (caller_roles & set(server_config.allowed_roles)):
                return MCPPermissionDecision(
                    allowed=False,
                    reason=(
                        f"Caller roles {sorted(caller_roles)} have no overlap with "
                        f"allowed_roles {server_config.allowed_roles} of server "
                        f"'{server_config.name}'"
                    ),
                    matched_rule=_RULE.ALLOWED_ROLES,
                )

        return MCPPermissionDecision(
            allowed=True,
            reason=f"No matching deny rule; default allow on server '{server_config.name}'",
            matched_rule=_RULE.DEFAULT_ALLOW,
        )

    # ── Internals ──────────────────────────────────────────────

    def _resolve_user_context(
        self, override: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Pick the effective user context.

        Explicit per-call ``override`` wins; otherwise we consult the
        injected provider. Provider failures are swallowed and treated
        as "no context" so a misconfigured provider cannot crash the
        permission fast path — at worst it weakens the user/role check
        to a default-allow, which the audit log still records.
        """
        if override is not None:
            return override
        if self._user_context_provider is None:
            return None
        try:
            return self._user_context_provider() or None
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "[mcp_permission] user_context_provider raised %s; treating as no context",
                exc,
            )
            return None


def _glob_match(pattern: str, value: str) -> bool:
    """Case-sensitive glob match with ``*`` wildcard.

    ``fnmatch`` is used because the spec only requires simple ``*`` support
    and the project's existing permission.py module already relies on the
    same primitive — keeping consistency avoids surprising a maintainer.
    """
    if pattern == "*":
        return True
    if pattern == value:
        return True
    return fnmatch.fnmatchcase(value, pattern)
