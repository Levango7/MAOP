"""Personal local owner mode and task-text validation."""

from __future__ import annotations

import pytest

from maop.core.security.middleware import _disabled_auth_roles, _is_loopback_host
from maop.dashboard.services.system_service import is_valid_task_name


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_loopback_hosts_are_owner_capable(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAOP_OWNER_MODE", "1")
    roles, identity = _disabled_auth_roles(host)
    assert identity == "owner"
    assert {"owner", "admin", "superadmin"} <= set(roles)


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.1.8", "", "testclient"])
def test_remote_clients_remain_read_only(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAOP_OWNER_MODE", "1")
    roles, identity = _disabled_auth_roles(host)
    assert identity == "anonymous"
    assert roles == ["read"]
    assert not _is_loopback_host(host)


def test_owner_mode_off_keeps_loopback_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAOP_OWNER_MODE", raising=False)
    roles, identity = _disabled_auth_roles("127.0.0.1")
    assert roles == ["read"]
    assert identity == "anonymous"


@pytest.mark.parametrize(
    "task",
    [
        "refactor auth module",
        "重构 auth 模块，改用 JWT。",
        "fix bug #42: don't panic",
    ],
)
def test_task_text_accepts_unicode_and_punctuation(task: str) -> None:
    assert is_valid_task_name(task)


@pytest.mark.parametrize("task", ["", "a\x00b", "x" * 10_001])
def test_task_text_rejects_only_nul_empty_and_oversize(task: str) -> None:
    assert not is_valid_task_name(task)
