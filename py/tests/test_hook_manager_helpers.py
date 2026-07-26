"""Top-level helpers for HookManager persistence/reload tests.

These functions are intentionally defined at module scope (not closures) so
that their ``__qualname__`` does not contain ``<locals>`` and they can be
re-imported via dotted path after a process restart.
"""

from __future__ import annotations

hook_calls: list[str] = []


def top_level_hook(event: str, data: dict) -> None:
    """A reloadable hook: callable via 'tests.test_hook_manager_helpers.top_level_hook'."""
    hook_calls.append(event)


def top_level_hook_deny(event: str, data: dict) -> dict:
    """A reloadable hook that denies the chain."""
    return {"decision": "deny"}


def top_level_hook_modify(event: str, data: dict) -> dict:
    """A reloadable hook that adds a field to the payload."""
    return {"modified_data": {"injected_by": "top_level_hook_modify"}}
