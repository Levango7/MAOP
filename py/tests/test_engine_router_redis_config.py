"""``engine_router`` 的 Redis 客户端工厂 —— config drift 修复的语义守卫。

把 ``os.environ.get("MAOP_REDIS_URL")`` 换成 ``get_settings().redis_url`` 之后，
"未配置" 与 "显式配置" 必须仍然分得清（否则 config drift 门禁的 229 基线只是
把两个真相源换成一个，语义却悄悄变了）：

- ``redis_url`` 不在 ``model_fields_set`` ⇒ ``None``（Personal 版保持本地引擎，
  不能因为内置默认值非空就去连一个不存在的 Redis）；
- 显式空串 ⇒ ``None``（运维的逃生舱）；
- 显式有值 ⇒ 建客户端；
- 建客户端失败 ⇒ 记 warning 后 ``None``（路由不该因此 500）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from maop.dashboard.routers import engine_router


def _patch_settings(monkeypatch: pytest.MonkeyPatch, url: str, fields: set[str]) -> None:
    fake = SimpleNamespace(redis_url=url, model_fields_set=fields)
    monkeypatch.setattr("maop.config.settings.get_settings", lambda: fake)


def test_unset_redis_url_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认值不算"已配置"——否则没装 Redis 的环境会无故建外部连接。"""
    _patch_settings(
        monkeypatch, "redis://localhost:6379/0", fields=set(),
    )
    assert engine_router._create_redis_client_from_env() is None


def test_explicit_empty_string_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "", fields={"redis_url"})
    assert engine_router._create_redis_client_from_env() is None


def test_explicit_whitespace_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "   ", fields={"redis_url"})
    assert engine_router._create_redis_client_from_env() is None


def test_explicit_url_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "redis://cache.internal:6379/2", fields={"redis_url"})

    sentinel = MagicMock(name="redis-client")
    captured: dict[str, object] = {}

    def _from_url(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return sentinel

    import redis

    monkeypatch.setattr(redis, "from_url", _from_url)

    assert engine_router._create_redis_client_from_env() is sentinel
    assert captured["url"] == "redis://cache.internal:6379/2"
    # decode_responses=False：调度器的 streams 按 bytes 解析，不能在这里转 str
    assert captured["kwargs"] == {"decode_responses": False}


def test_client_construction_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "redis://broken:6379/0", fields={"redis_url"})

    import redis

    def _boom(url: str, **kwargs: object) -> object:
        raise ConnectionError("cannot reach redis")

    monkeypatch.setattr(redis, "from_url", _boom)

    assert engine_router._create_redis_client_from_env() is None
