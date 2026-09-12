"""Agent 注册中心 (AgentCatalog) 白盒测试.

覆盖：注册/获取/列表/搜索/删除/健康更新/参数校验/优雅降级/持久化/单例。
每个测试依赖 conftest.py 的 MAOP_DATA_DIR 隔离，不触碰真实 DB。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from maop.core.agent.registry.agent_catalog import (
    AgentCapability,
    AgentCatalog,
    AgentDescriptor,
    AuthMethod,
    BillingModel,
    get_catalog,
    reset_catalog,
)
from maop.core.agent.registry.agent_catalog_store import AgentCatalogStore


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def catalog(tmp_path: Path) -> AgentCatalog:
    """一个使用隔离 tmp_path DB 的全新 AgentCatalog。"""
    return AgentCatalog(db_path=tmp_path / "agent_catalog.db")


@pytest.fixture
def store(tmp_path: Path) -> AgentCatalogStore:
    """一个使用隔离 tmp_path DB 的全新 AgentCatalogStore。"""
    return AgentCatalogStore(db_path=tmp_path / "agent_catalog.db")


def _desc(name: str, **kwargs: object) -> AgentDescriptor:
    """构造 AgentDescriptor，name 必填，其余可选覆盖。"""
    return AgentDescriptor(name=name, **kwargs)  # type: ignore[arg-type]


# ── 1. 注册 + 获取 ───────────────────────────────────────────────


def test_register_then_get(catalog: AgentCatalog) -> None:
    """注册后可通过 name 获取。"""
    catalog.register(_desc("agent_a", display_name="Agent A"))
    got = catalog.get("agent_a")
    assert got is not None
    assert got.name == "agent_a"
    assert got.display_name == "Agent A"


def test_get_nonexistent_returns_none(catalog: AgentCatalog) -> None:
    """获取不存在的 Agent 返回 None，不抛异常。"""
    assert catalog.get("ghost") is None


# ── 2. 注册幂等 / 更新 ───────────────────────────────────────────


def test_register_same_name_updates(catalog: AgentCatalog) -> None:
    """同名重复注册执行 upsert，覆盖原字段。"""
    catalog.register(_desc("agent_a", timeout_s=60))
    catalog.register(_desc("agent_a", timeout_s=120, vendor="NewVendor"))
    got = catalog.get("agent_a")
    assert got is not None
    assert got.timeout_s == 120
    assert got.vendor == "NewVendor"


# ── 3. 列表 ──────────────────────────────────────────────────────


def test_list_all_returns_sorted(catalog: AgentCatalog) -> None:
    """list_all 返回所有 Agent，按 name 排序。"""
    catalog.register(_desc("zeta"))
    catalog.register(_desc("alpha"))
    catalog.register(_desc("mid"))
    names = [a.name for a in catalog.list_all()]
    assert names == ["alpha", "mid", "zeta"]


def test_list_all_empty(catalog: AgentCatalog) -> None:
    """空注册中心 list_all 返回空列表。"""
    assert catalog.list_all() == []


def test_list_enabled_filters_disabled(catalog: AgentCatalog) -> None:
    """list_enabled 仅返回 enabled=True 的 Agent。"""
    catalog.register(_desc("on", enabled=True))
    catalog.register(_desc("off", enabled=False))
    names = [a.name for a in catalog.list_enabled()]
    assert names == ["on"]


# ── 4. 按能力搜索 ────────────────────────────────────────────────


def test_search_by_capability(catalog: AgentCatalog) -> None:
    """search 按能力过滤，返回具备该能力的 Agent。"""
    catalog.register(_desc("coder", capabilities=[AgentCapability.CODE_GENERATION]))
    catalog.register(_desc("reviewer", capabilities=[AgentCapability.CODE_REVIEW]))
    catalog.register(_desc("both", capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CODE_REVIEW]))
    result = catalog.search(AgentCapability.CODE_GENERATION)
    names = [a.name for a in result]
    assert names == ["both", "coder"]  # 排序


def test_search_excludes_disabled(catalog: AgentCatalog) -> None:
    """search 不返回已禁用的 Agent。"""
    catalog.register(_desc("coder_on", capabilities=[AgentCapability.CODE_GENERATION], enabled=True))
    catalog.register(_desc("coder_off", capabilities=[AgentCapability.CODE_GENERATION], enabled=False))
    result = catalog.search(AgentCapability.CODE_GENERATION)
    assert [a.name for a in result] == ["coder_on"]


def test_search_empty_capability(catalog: AgentCatalog) -> None:
    """无 Agent 具备该能力时返回空列表。"""
    catalog.register(_desc("chat", capabilities=[AgentCapability.CHAT]))
    assert catalog.search(AgentCapability.CODE_GENERATION) == []


def test_search_by_vendor(catalog: AgentCatalog) -> None:
    """search_by_vendor 按供应商过滤。"""
    catalog.register(_desc("a", vendor="Anthropic"))
    catalog.register(_desc("b", vendor="OpenAI"))
    catalog.register(_desc("c", vendor="Anthropic"))
    result = catalog.search_by_vendor("Anthropic")
    assert [a.name for a in result] == ["a", "c"]


# ── 5. 删除 ──────────────────────────────────────────────────────


def test_delete_removes_agent(catalog: AgentCatalog) -> None:
    """delete 移除 Agent，返回 True。"""
    catalog.register(_desc("agent_a"))
    assert catalog.delete("agent_a") is True
    assert catalog.get("agent_a") is None


def test_delete_nonexistent_returns_false(catalog: AgentCatalog) -> None:
    """delete 不存在的 Agent 返回 False，不抛异常。"""
    assert catalog.delete("ghost") is False


# ── 6. 健康更新 ──────────────────────────────────────────────────


def test_update_health(catalog: AgentCatalog) -> None:
    """update_health 更新健康状态和时间戳。"""
    catalog.register(_desc("agent_a"))
    catalog.update_health("agent_a", healthy=False)
    got = catalog.get("agent_a")
    assert got is not None
    assert got.healthy is False
    assert got.last_health_check > 0


def test_update_health_nonexistent_noop(catalog: AgentCatalog) -> None:
    """update_health 对不存在的 Agent 静默忽略（no-op）。"""
    catalog.update_health("ghost", healthy=False)  # 不应抛异常


def test_update_health_then_recover(catalog: AgentCatalog) -> None:
    """健康状态可恢复：False → True。"""
    catalog.register(_desc("agent_a"))
    catalog.update_health("agent_a", healthy=False)
    assert catalog.get("agent_a").healthy is False
    catalog.update_health("agent_a", healthy=True)
    assert catalog.get("agent_a").healthy is True


# ── 7. 参数校验 ──────────────────────────────────────────────────


def test_descriptor_empty_name_rejected() -> None:
    """name 为空字符串被拒绝。"""
    with pytest.raises(ValidationError):
        AgentDescriptor(name="")


def test_descriptor_invalid_adapter_type_rejected() -> None:
    """adapter_type 非法值被拒绝。"""
    with pytest.raises(ValidationError):
        AgentDescriptor(name="x", adapter_type="invalid")


def test_descriptor_valid_adapter_types() -> None:
    """adapter_type 合法值通过。"""
    for t in ("cli", "http", "mcp", "web", ""):
        d = AgentDescriptor(name="x", adapter_type=t)
        assert d.adapter_type == t


def test_descriptor_negative_timeout_rejected() -> None:
    """timeout_s 必须为正。"""
    with pytest.raises(ValidationError):
        AgentDescriptor(name="x", timeout_s=0)
    with pytest.raises(ValidationError):
        AgentDescriptor(name="x", timeout_s=-1)


def test_descriptor_self_fallback_rejected() -> None:
    """降级链不能包含自身。"""
    with pytest.raises(ValidationError):
        AgentDescriptor(name="self", fallback_agents=["self"])


def test_descriptor_max_concurrent_ge1() -> None:
    """max_concurrent 必须 >= 1。"""
    with pytest.raises(ValidationError):
        AgentDescriptor(name="x", max_concurrent=0)


# ── 8. 降级链 ────────────────────────────────────────────────────


def test_fallback_chain(catalog: AgentCatalog) -> None:
    """get_fallback_chain 按顺序解析降级 Agent。"""
    catalog.register(_desc("primary", fallback_agents=["backup1", "backup2"]))
    catalog.register(_desc("backup1"))
    catalog.register(_desc("backup2"))
    chain = catalog.get_fallback_chain("primary")
    assert [a.name for a in chain] == ["backup1", "backup2"]


def test_fallback_chain_skips_nonexistent(catalog: AgentCatalog) -> None:
    """降级链跳过不存在的 Agent（优雅降级）。"""
    catalog.register(_desc("primary", fallback_agents=["real", "ghost"]))
    catalog.register(_desc("real"))
    chain = catalog.get_fallback_chain("primary")
    assert [a.name for a in chain] == ["real"]


def test_fallback_chain_skips_disabled(catalog: AgentCatalog) -> None:
    """降级链跳过已禁用的 Agent。"""
    catalog.register(_desc("primary", fallback_agents=["on", "off"]))
    catalog.register(_desc("on", enabled=True))
    catalog.register(_desc("off", enabled=False))
    chain = catalog.get_fallback_chain("primary")
    assert [a.name for a in chain] == ["on"]


def test_fallback_chain_nonexistent_agent(catalog: AgentCatalog) -> None:
    """对不存在的 Agent 取降级链返回空列表。"""
    assert catalog.get_fallback_chain("ghost") == []


# ── 9. 持久化 ────────────────────────────────────────────────────


def test_persistence_across_instances(tmp_path: Path) -> None:
    """新 AgentCatalog 实例从 SQLite 加载已持久化的 Agent。"""
    db = tmp_path / "agent_catalog.db"
    c1 = AgentCatalog(db_path=db)
    c1.register(_desc("agent_a", vendor="Anthropic", capabilities=[AgentCapability.CHAT]))
    c2 = AgentCatalog(db_path=db)
    got = c2.get("agent_a")
    assert got is not None
    assert got.vendor == "Anthropic"
    assert AgentCapability.CHAT in got.capabilities


def test_persistence_delete(tmp_path: Path) -> None:
    """delete 持久化：新实例看不到已删除的 Agent。"""
    db = tmp_path / "agent_catalog.db"
    c1 = AgentCatalog(db_path=db)
    c1.register(_desc("agent_a"))
    c1.delete("agent_a")
    c2 = AgentCatalog(db_path=db)
    assert c2.get("agent_a") is None


def test_persistence_health(tmp_path: Path) -> None:
    """update_health 持久化：新实例看到更新后的健康状态。"""
    db = tmp_path / "agent_catalog.db"
    c1 = AgentCatalog(db_path=db)
    c1.register(_desc("agent_a"))
    c1.update_health("agent_a", healthy=False)
    c2 = AgentCatalog(db_path=db)
    got = c2.get("agent_a")
    assert got is not None
    assert got.healthy is False


# ── 10. Store 直接测试 ───────────────────────────────────────────


def test_store_ensure_table_idempotent(store: AgentCatalogStore) -> None:
    """ensure_table 幂等：重复调用不报错。"""
    store.ensure_table()
    store.ensure_table()  # 第二次不应抛异常


def test_store_upsert_and_load(store: AgentCatalogStore) -> None:
    """Store upsert + load_one 往返一致。"""
    data = AgentDescriptor(
        name="test_agent",
        display_name="Test",
        vendor="V",
        capabilities=[AgentCapability.CODE_GENERATION],
        billing_model=BillingModel.PER_TOKEN,
        auth_method=AuthMethod.API_KEY,
        fallback_agents=["fb1"],
    ).to_store_dict()
    store.upsert(data)
    loaded = store.load_one("test_agent")
    assert loaded is not None
    assert loaded["name"] == "test_agent"
    assert loaded["vendor"] == "V"
    assert loaded["billing_model"] == "per_token"
    assert loaded["auth_method"] == "api_key"
    assert loaded["capabilities"] == ["code_generation"]
    assert loaded["fallback_agents"] == ["fb1"]


def test_store_load_all(store: AgentCatalogStore) -> None:
    """Store load_all 返回所有记录。"""
    store.upsert(_desc("a").to_store_dict())
    store.upsert(_desc("b").to_store_dict())
    rows = store.load_all()
    names = {r["name"] for r in rows}
    assert names == {"a", "b"}


def test_store_delete(store: AgentCatalogStore) -> None:
    """Store delete 删除记录。"""
    store.upsert(_desc("a").to_store_dict())
    assert store.delete("a") is True
    assert store.load_one("a") is None
    assert store.delete("a") is False  # 再删返回 False


# ── 11. 统计 ─────────────────────────────────────────────────────


def test_count(catalog: AgentCatalog) -> None:
    """count 返回已注册 Agent 总数。"""
    assert catalog.count() == 0
    catalog.register(_desc("a"))
    catalog.register(_desc("b"))
    assert catalog.count() == 2


def test_count_healthy(catalog: AgentCatalog) -> None:
    """count_healthy 返回健康 Agent 数量。"""
    catalog.register(_desc("a", healthy=True))
    catalog.register(_desc("b", healthy=True))
    catalog.register(_desc("c", healthy=False))
    assert catalog.count_healthy() == 2


# ── 12. 单例 ─────────────────────────────────────────────────────


def test_get_catalog_singleton() -> None:
    """get_catalog 返回同一实例（双重检查锁定）。"""
    reset_catalog()
    c1 = get_catalog()
    c2 = get_catalog()
    assert c1 is c2
    reset_catalog()


def test_reset_catalog() -> None:
    """reset_catalog 后 get_catalog 返回新实例。"""
    reset_catalog()
    c1 = get_catalog()
    reset_catalog()
    c2 = get_catalog()
    assert c1 is not c2
    reset_catalog()


# ── 13. 枚举 ─────────────────────────────────────────────────────


def test_capability_enum_values() -> None:
    """AgentCapability 枚举值与设计一致。"""
    assert AgentCapability.CODE_GENERATION.value == "code_generation"
    assert AgentCapability.CODE_REVIEW.value == "code_review"
    assert AgentCapability.CHAT.value == "chat"
    assert AgentCapability.REASONING.value == "reasoning"
    assert AgentCapability.MULTIMODAL.value == "multimodal"
    assert AgentCapability.LONG_CONTEXT.value == "long_context"
    assert AgentCapability.TOOL_USE.value == "tool_use"
    assert AgentCapability.WEB_SEARCH.value == "web_search"
    assert AgentCapability.FILE_EDIT.value == "file_edit"
    assert AgentCapability.TERMINAL.value == "terminal"


def test_billing_model_enum_values() -> None:
    """BillingModel 枚举值与设计一致。"""
    assert BillingModel.PER_TOKEN.value == "per_token"
    assert BillingModel.PER_CALL.value == "per_call"
    assert BillingModel.SUBSCRIPTION.value == "subscription"
    assert BillingModel.CREDIT.value == "credit"
    assert BillingModel.BLACKBOX.value == "blackbox"
    assert BillingModel.FREE.value == "free"


def test_auth_method_enum_values() -> None:
    """AuthMethod 枚举值与设计一致。"""
    assert AuthMethod.API_KEY.value == "api_key"
    assert AuthMethod.OAUTH.value == "oauth"
    assert AuthMethod.USERNAME_PASSWORD.value == "username_password"
    assert AuthMethod.LICENSE.value == "license"
    assert AuthMethod.NONE.value == "none"


# ── 14. has_capability 便捷方法 ──────────────────────────────────


def test_has_capability() -> None:
    """has_capability 正确判断能力归属。"""
    d = _desc("x", capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.CHAT])
    assert d.has_capability(AgentCapability.CODE_GENERATION) is True
    assert d.has_capability(AgentCapability.CODE_REVIEW) is False


# ── 15. 默认值 ───────────────────────────────────────────────────


def test_descriptor_defaults() -> None:
    """AgentDescriptor 默认值符合设计。"""
    d = AgentDescriptor(name="x")
    assert d.display_name == ""
    assert d.vendor == ""
    assert d.version == ""
    assert d.adapter_type == ""
    assert d.adapter_config == {}
    assert d.capabilities == []
    assert d.max_context_length == 0
    assert d.supports_streaming is False
    assert d.supports_tool_call is False
    assert d.billing_model == BillingModel.BLACKBOX
    assert d.billing_config == {}
    assert d.auth_method == AuthMethod.NONE
    assert d.auth_credentials_ref == ""
    assert d.max_concurrent == 1
    assert d.rate_limit_per_min == 0
    assert d.timeout_s == 30.0
    assert d.retry_count == 3
    assert d.fallback_agents == []
    assert d.enabled is True
    assert d.healthy is True
    assert d.last_health_check == 0.0


def test_descriptor_mutable_defaults_not_shared() -> None:
    """可变默认值不共享（Pydantic Field(default_factory) 保证）。"""
    d1 = AgentDescriptor(name="a", capabilities=[AgentCapability.CHAT])
    d2 = AgentDescriptor(name="b")
    assert d1.capabilities == [AgentCapability.CHAT]
    assert d2.capabilities == []
    # 修改 d1 不影响 d2
    d1.adapter_config["key"] = "val"
    assert d2.adapter_config == {}