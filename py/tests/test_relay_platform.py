
"""第三方中转平台管理器测试。

覆盖：
  - 平台 CRUD（注册/查询/列表/删除）
  - 模型发现（mock httpx，不实际调用 API）
  - 价格对比
  - 平台推荐（最便宜/优先国内/不可用回退）
  - SQLite 持久化
  - 速率限制跟踪
  - 线程安全并发访问
  - FastAPI 路由端点（TestClient）

所有测试使用 tmp_path 隔离 SQLite，互不影响。
"""

from __future__ import annotations

import threading
import time  # noqa: F401
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from maop.core.agent.llm_chat.relay_platform import (
    PriceComparison,
    RelayModelInfo,
    RelayPlatform,
    RelayPlatformManager,
    reset_relay_platform_manager,
)
from tests.thread_join_guard import join_all

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def manager(tmp_path: Any) -> RelayPlatformManager:
    """每个测试独立的 RelayPlatformManager，使用 tmp_path 隔离 SQLite。"""
    reset_relay_platform_manager()
    mgr = RelayPlatformManager(db_path=tmp_path / "relay.db")
    yield mgr
    mgr.close()
    reset_relay_platform_manager()


@pytest.fixture
def app_with_router(manager: RelayPlatformManager) -> FastAPI:
    """带中转平台路由的 FastAPI 测试应用。"""
    from maop.dashboard.routers.relay_platform import router as relay_router

    app = FastAPI()
    app.state.relay_platform_manager = manager
    # Stub auth state so require_admin passes
    @app.middleware("http")
    async def _stub_auth(request: Request, call_next):
        if not hasattr(request.state, "auth_roles"):
            request.state.auth_roles = ["admin"]
            request.state.auth_identity = "test-admin"
        return await call_next(request)

    app.include_router(relay_router)
    return app


@pytest.fixture
def client(app_with_router: FastAPI) -> TestClient:
    return TestClient(app_with_router)


def _make_platform(
    name: str = "openrouter",
    *,
    base_url: str = "https://openrouter.ai/api/v1",
    region: str = "international",
    rate_limit_rpm: int = 100,
) -> RelayPlatform:
    """构造测试用平台实例。"""
    return RelayPlatform(
        name=name,
        type="relay",
        base_url=base_url,
        api_key="test-key",
        display_name=name.title(),
        description="test platform",
        features=["model_discovery", "streaming"],
        rate_limit_rpm=rate_limit_rpm,
        region=region,
    )


# ═══════════════════════════════════════════════════════════════════
# 1. 平台 CRUD 测试
# ═══════════════════════════════════════════════════════════════════


def test_register_and_get_platform(manager: RelayPlatformManager):
    """注册平台后可通过 get_platform 获取。"""
    platform = _make_platform("openrouter")
    manager.register_platform(platform)

    got = manager.get_platform("openrouter")
    assert got is not None
    assert got.name == "openrouter"
    assert got.base_url == "https://openrouter.ai/api/v1"
    assert got.type == "relay"
    assert got.api_key == "test-key"


def test_list_platforms_all(manager: RelayPlatformManager):
    """list_platforms 无过滤返回所有平台。"""
    manager.register_platform(_make_platform("openrouter"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))
    # 直连供应商
    direct = RelayPlatform(name="agnes", type="direct", base_url="https://api.agnes-ai.cn/v1")
    manager.register_platform(direct)

    all_platforms = manager.list_platforms()
    assert len(all_platforms) == 3
    names = {p.name for p in all_platforms}
    assert names == {"openrouter", "siliconflow", "agnes"}


def test_list_platforms_relay_only(manager: RelayPlatformManager):
    """list_platforms(type='relay') 只返回中转平台。"""
    manager.register_platform(_make_platform("openrouter"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))
    direct = RelayPlatform(name="agnes", type="direct", base_url="https://api.agnes-ai.cn/v1")
    manager.register_platform(direct)

    relay_only = manager.list_platforms(type="relay")
    assert len(relay_only) == 2
    assert all(p.type == "relay" for p in relay_only)


def test_list_platforms_direct_only(manager: RelayPlatformManager):
    """list_platforms(type='direct') 只返回直连供应商。"""
    manager.register_platform(_make_platform("openrouter"))
    direct = RelayPlatform(name="agnes", type="direct", base_url="https://api.agnes-ai.cn/v1")
    manager.register_platform(direct)

    direct_only = manager.list_platforms(type="direct")
    assert len(direct_only) == 1
    assert direct_only[0].name == "agnes"
    assert direct_only[0].type == "direct"


def test_remove_platform(manager: RelayPlatformManager):
    """删除平台后 get_platform 返回 None。"""
    manager.register_platform(_make_platform("openrouter"))
    assert manager.get_platform("openrouter") is not None

    manager.remove_platform("openrouter")
    assert manager.get_platform("openrouter") is None

    # 删除不存在的平台不报错
    manager.remove_platform("nonexistent")


# ═══════════════════════════════════════════════════════════════════
# 2. 模型发现测试
# ═══════════════════════════════════════════════════════════════════


def test_discover_models(manager: RelayPlatformManager):
    """discover_models 通过 mock httpx 返回模型列表。"""
    platform = _make_platform("openrouter")
    manager.register_platform(platform)

    # Mock httpx.Client 的响应
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "gpt-4o", "context_length": 128000},
            {"id": "claude-3.5-sonnet", "context_length": 200000},
            {"id": "gpt-4o-mini", "context_length": 128000},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_response)

    with patch("maop.core.agent.llm_chat.relay_platform.httpx.Client", return_value=mock_client):
        models = manager.discover_models("openrouter", use_cache=False)

    assert len(models) == 3
    assert models[0].model_id == "gpt-4o"
    assert models[0].platform_name == "openrouter"
    assert models[0].context_length == 128000
    assert models[1].model_id == "claude-3.5-sonnet"
    assert models[1].context_length == 200000


def test_discover_models_api_error(manager: RelayPlatformManager):
    """API 请求失败时返回空列表（不抛异常）。"""
    platform = _make_platform("openrouter")
    manager.register_platform(platform)

    # Mock httpx 抛异常
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(side_effect=Exception("API timeout"))

    with patch("maop.core.agent.llm_chat.relay_platform.httpx.Client", return_value=mock_client):
        models = manager.discover_models("openrouter", use_cache=False)

    # API 失败，无缓存，返回空列表
    assert models == []

    # 平台不存在时也返回空列表
    models = manager.discover_models("nonexistent")
    assert models == []


# ═══════════════════════════════════════════════════════════════════
# 3. 价格对比测试
# ═══════════════════════════════════════════════════════════════════


def test_compare_prices(manager: RelayPlatformManager):
    """对比同一模型在不同平台的价格，按 total 升序排列。"""
    # 注册两个平台
    manager.register_platform(_make_platform("openrouter", region="international"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))

    # 手动补充模型价格
    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)
    manager.update_model_price("siliconflow", "gpt-4o", 0.002, 0.006)

    comparisons = manager.compare_prices("gpt-4o")
    assert len(comparisons) == 2
    # siliconflow (0.008) 比 openrouter (0.020) 便宜，排在前
    assert comparisons[0].platform_name == "siliconflow"
    assert comparisons[0].total_per_1k == 0.008
    assert comparisons[0].is_free is False
    assert comparisons[0].region == "domestic"
    assert comparisons[1].platform_name == "openrouter"
    assert comparisons[1].total_per_1k == 0.020


def test_compare_prices_single_platform(manager: RelayPlatformManager):
    """只有一个平台有该模型时返回单条对比。"""
    manager.register_platform(_make_platform("openrouter"))
    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)

    comparisons = manager.compare_prices("gpt-4o")
    assert len(comparisons) == 1
    assert comparisons[0].platform_name == "openrouter"
    assert comparisons[0].total_per_1k == 0.020

    # 不存在的模型返回空列表
    comparisons = manager.compare_prices("nonexistent-model")
    assert comparisons == []


def test_compare_prices_free_model(manager: RelayPlatformManager):
    """价格为 0 的模型标记为 is_free。"""
    manager.register_platform(_make_platform("openrouter"))
    manager.update_model_price("openrouter", "free-model", 0.0, 0.0)

    comparisons = manager.compare_prices("free-model")
    assert len(comparisons) == 1
    assert comparisons[0].is_free is True
    assert comparisons[0].total_per_1k == 0.0


# ═══════════════════════════════════════════════════════════════════
# 4. 平台推荐测试
# ═══════════════════════════════════════════════════════════════════


def test_recommend_platform_cheapest(manager: RelayPlatformManager):
    """推荐价格最低的平台。"""
    manager.register_platform(_make_platform("openrouter", region="international"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))
    manager.register_platform(_make_platform("nvidia", region="international"))

    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)  # 0.020
    manager.update_model_price("siliconflow", "gpt-4o", 0.002, 0.006)  # 0.008
    manager.update_model_price("nvidia", "gpt-4o", 0.003, 0.009)  # 0.012

    recommended = manager.recommend_platform("gpt-4o")
    assert recommended == "siliconflow"  # 最便宜


def test_recommend_platform_prefer_domestic(manager: RelayPlatformManager):
    """prefer_domestic=True 时优先推荐国内平台。"""
    manager.register_platform(_make_platform("openrouter", region="international"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))

    # 国际平台更便宜
    manager.update_model_price("openrouter", "gpt-4o", 0.001, 0.001)  # 0.002
    manager.update_model_price("siliconflow", "gpt-4o", 0.005, 0.005)  # 0.010

    # prefer_domestic=True 应选 siliconflow（国内），尽管更贵
    recommended = manager.recommend_platform("gpt-4o", prefer_domestic=True)
    assert recommended == "siliconflow"

    # prefer_domestic=False 应选 openrouter（最便宜）
    recommended = manager.recommend_platform("gpt-4o", prefer_domestic=False)
    assert recommended == "openrouter"


def test_recommend_platform_unavailable(manager: RelayPlatformManager):
    """无可用平台时返回 None。"""
    # 没有任何平台
    recommended = manager.recommend_platform("gpt-4o")
    assert recommended is None

    # 有平台但无该模型价格
    manager.register_platform(_make_platform("openrouter"))
    recommended = manager.recommend_platform("nonexistent-model")
    assert recommended is None


def test_recommend_platform_no_domestic_fallback(manager: RelayPlatformManager):
    """prefer_domestic=True 但无国内平台时回退到国际平台。"""
    manager.register_platform(_make_platform("openrouter", region="international"))
    manager.register_platform(_make_platform("nvidia", region="international"))

    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)  # 0.020
    manager.update_model_price("nvidia", "gpt-4o", 0.003, 0.009)  # 0.012

    # 无国内平台，回退到最便宜的国际平台
    recommended = manager.recommend_platform("gpt-4o", prefer_domestic=True)
    assert recommended == "nvidia"


# ═══════════════════════════════════════════════════════════════════
# 5. 持久化测试
# ═══════════════════════════════════════════════════════════════════


def test_platform_persistence(tmp_path: Any):
    """平台注册信息持久化到 SQLite，新实例可加载。"""
    db_path = tmp_path / "relay.db"

    # 第一个实例注册平台
    mgr1 = RelayPlatformManager(db_path=db_path)
    mgr1.register_platform(_make_platform("openrouter"))
    mgr1.register_platform(_make_platform("siliconflow", region="domestic"))
    mgr1.close()

    # 第二个实例从同一 DB 加载
    mgr2 = RelayPlatformManager(db_path=db_path)
    platforms = mgr2.list_platforms()
    assert len(platforms) == 2
    names = {p.name for p in platforms}
    assert names == {"openrouter", "siliconflow"}

    got = mgr2.get_platform("openrouter")
    assert got is not None
    assert got.base_url == "https://openrouter.ai/api/v1"
    assert got.api_key == "test-key"
    mgr2.close()


# ═══════════════════════════════════════════════════════════════════
# 6. 速率限制跟踪测试
# ═══════════════════════════════════════════════════════════════════


def test_rate_limit_tracking(manager: RelayPlatformManager):
    """速率限制计数器正确跟踪请求数。"""
    platform = _make_platform("openrouter", rate_limit_rpm=5)
    manager.register_platform(platform)

    # 初始计数为 0
    assert manager.get_request_count("openrouter") == 0
    assert manager.is_rate_limited("openrouter") is False

    # 记录 3 次请求
    for _ in range(3):
        manager.record_request("openrouter")
    assert manager.get_request_count("openrouter") == 3
    assert manager.is_rate_limited("openrouter") is False

    # 再记录 2 次，达到限制
    for _ in range(2):
        manager.record_request("openrouter")
    assert manager.get_request_count("openrouter") == 5
    assert manager.is_rate_limited("openrouter") is True

    # rate_limit_rpm=0 表示无限制
    no_limit_platform = _make_platform("nolimit", rate_limit_rpm=0)
    manager.register_platform(no_limit_platform)
    for _ in range(100):
        manager.record_request("nolimit")
    assert manager.is_rate_limited("nolimit") is False


# ═══════════════════════════════════════════════════════════════════
# 7. 线程安全测试
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.timeout(240)
def test_concurrent_access(manager: RelayPlatformManager):
    """多线程并发注册/查询/删除不产生竞争错误。"""
    num_threads = 10
    ops_per_thread = 50
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(ops_per_thread):
                name = f"platform-{thread_id}-{i}"
                platform = _make_platform(name)
                manager.register_platform(platform)
                got = manager.get_platform(name)
                assert got is not None
                _ = manager.list_platforms()
                manager.remove_platform(name)
                assert manager.get_platform(name) is None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    join_all(threads, 120.0)

    assert errors == [], f"并发访问产生错误: {errors}"
    # 所有平台都已删除
    assert len(manager.list_platforms()) == 0


@pytest.mark.timeout(240)
def test_concurrent_register_and_list(manager: RelayPlatformManager):
    """并发注册 + 列表查询混合操作。"""
    errors: list[Exception] = []

    def registrar() -> None:
        try:
            for i in range(30):
                manager.register_platform(_make_platform(f"reg-{i}"))
        except Exception as exc:
            errors.append(exc)

    def lister() -> None:
        try:
            for _ in range(100):
                _ = manager.list_platforms()
        except Exception as exc:
            errors.append(exc)

    threads = []
    for _ in range(4):
        threads.append(threading.Thread(target=registrar))
        threads.append(threading.Thread(target=lister))
    for t in threads:
        t.start()
    join_all(threads, 120.0)

    assert errors == []
    assert len(manager.list_platforms()) == 30


# ═══════════════════════════════════════════════════════════════════
# 8. Pydantic 模型测试
# ═══════════════════════════════════════════════════════════════════


def test_relay_platform_defaults():
    """RelayPlatform 默认值正确。"""
    p = RelayPlatform(name="test")
    assert p.type == "relay"
    assert p.base_url == ""
    assert p.api_key == ""
    assert p.display_name == ""
    assert p.features == []
    assert p.rate_limit_rpm == 0
    assert p.region == "international"
    assert p.enabled is True


def test_relay_model_info_defaults():
    """RelayModelInfo 默认值正确。"""
    m = RelayModelInfo(model_id="gpt-4o", platform_name="openrouter")
    assert m.price_per_1k_input == 0.0
    assert m.price_per_1k_output == 0.0
    assert m.context_length == 0
    assert m.capabilities == []
    assert m.available is True


def test_price_comparison_defaults():
    """PriceComparison 字段正确计算。"""
    c = PriceComparison(
        model_id="gpt-4o",
        platform_name="openrouter",
        price_per_1k_input=0.005,
        price_per_1k_output=0.015,
        total_per_1k=0.020,
        is_free=False,
        region="international",
    )
    assert c.total_per_1k == 0.020
    assert c.is_free is False


# ═══════════════════════════════════════════════════════════════════
# 9. 从 models.yaml 加载测试
# ═══════════════════════════════════════════════════════════════════


def test_load_from_models_yaml(manager: RelayPlatformManager, tmp_path: Any):
    """从 models.yaml 加载中转平台配置。"""
    import yaml

    yaml_content = {
        "providers": {
            "openrouter": {
                "type": "relay",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "display_name": "OpenRouter",
                "description": "test",
                "features": ["model_discovery"],
                "rate_limit_rpm": 100,
                "region": "international",
            },
            "siliconflow": {
                "type": "relay",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key_env": "SILICONFLOW_API_KEY",
                "display_name": "硅基流动",
                "description": "domestic",
                "features": ["model_discovery"],
                "rate_limit_rpm": 200,
                "region": "domestic",
            },
            "agnes": {
                "base_url": "https://api.agnes-ai.cn/v1",
                "api_key_env": "AGNES_API_KEY",
            },
        }
    }
    yaml_path = tmp_path / "models.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)

    # 设置环境变量
    import os
    old_or = os.environ.get("OPENROUTER_API_KEY")
    old_sf = os.environ.get("SILICONFLOW_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "or-key"
    os.environ["SILICONFLOW_API_KEY"] = "sf-key"
    try:
        manager.load_from_models_yaml(yaml_path)
    finally:
        if old_or is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = old_or
        if old_sf is None:
            os.environ.pop("SILICONFLOW_API_KEY", None)
        else:
            os.environ["SILICONFLOW_API_KEY"] = old_sf

    # 只加载 type=relay 的平台，agnes（无 type）不加载
    platforms = manager.list_platforms()
    names = {p.name for p in platforms}
    assert names == {"openrouter", "siliconflow"}

    or_platform = manager.get_platform("openrouter")
    assert or_platform is not None
    assert or_platform.api_key == "or-key"
    assert or_platform.region == "international"

    sf_platform = manager.get_platform("siliconflow")
    assert sf_platform is not None
    assert sf_platform.api_key == "sf-key"
    assert sf_platform.region == "domestic"


# ═══════════════════════════════════════════════════════════════════
# 10. FastAPI 路由端点测试
# ═══════════════════════════════════════════════════════════════════


def test_api_list_platforms(client: TestClient, manager: RelayPlatformManager):
    """GET /api/relay-platforms 列出平台。"""
    manager.register_platform(_make_platform("openrouter"))

    resp = client.get("/api/relay-platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "openrouter"


def test_api_register_platform(client: TestClient):
    """POST /api/relay-platforms 注册平台。"""
    payload = {
        "name": "newplatform",
        "type": "relay",
        "base_url": "https://api.new.com/v1",
        "api_key": "key123",
        "display_name": "New Platform",
        "description": "test",
        "features": ["model_discovery"],
        "rate_limit_rpm": 50,
        "region": "international",
        "enabled": True,
    }
    resp = client.post("/api/relay-platforms", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "newplatform"
    assert data["base_url"] == "https://api.new.com/v1"


def test_api_delete_platform(client: TestClient, manager: RelayPlatformManager):
    """DELETE /api/relay-platforms/{name} 删除平台。"""
    manager.register_platform(_make_platform("openrouter"))

    resp = client.delete("/api/relay-platforms/openrouter")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # 删除不存在的平台返回 404
    resp = client.delete("/api/relay-platforms/nonexistent")
    assert resp.status_code == 404


def test_api_recommend(client: TestClient, manager: RelayPlatformManager):
    """GET /api/relay-platforms/recommend/{model_id} 推荐平台。"""
    manager.register_platform(_make_platform("openrouter", region="international"))
    manager.register_platform(_make_platform("siliconflow", region="domestic"))
    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)
    manager.update_model_price("siliconflow", "gpt-4o", 0.002, 0.006)

    resp = client.get("/api/relay-platforms/recommend/gpt-4o")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform_name"] == "siliconflow"

    # prefer_domestic=True
    resp = client.get(
        "/api/relay-platforms/recommend/gpt-4o", params={"prefer_domestic": "true"}
    )
    assert resp.status_code == 200
    assert resp.json()["platform_name"] == "siliconflow"


def test_api_compare_prices(client: TestClient, manager: RelayPlatformManager):
    """POST /api/relay-platforms/compare 价格对比。"""
    manager.register_platform(_make_platform("openrouter"))
    manager.update_model_price("openrouter", "gpt-4o", 0.005, 0.015)

    resp = client.post("/api/relay-platforms/compare", json={"model_id": "gpt-4o"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["platform_name"] == "openrouter"
    assert data[0]["total_per_1k"] == 0.020