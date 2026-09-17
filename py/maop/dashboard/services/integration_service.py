"""Integrations 域 Service 层 — n8n + Relay Platform 子域.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object，
不抛 HTTPException。可独立单元测试，无需 HTTP 上下文。

本模块从以下 2 个 router 提取业务逻辑：

- ``n8n``            — n8n 工作流集成 (webhook / workflows / executions / health)
- ``relay_platform`` — 第三方中转平台管理 (CRUD / 模型发现 / 价格对比 / 推荐)

n8n 子域使用 ``N8nClient`` 单例（env 变化时重建）；relay_platform 子域
的 manager 由调用方从 ``app.state`` 注入或回退到全局单例，service 函数
接收已获取的 manager 作为显式参数（依赖注入），便于测试隔离。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from maop.enterprise.n8n import (
    N8nClient,
    N8nIntegrationError,
    handle_n8n_webhook,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# §1  n8n Integration — 工作流自动化集成
# ══════════════════════════════════════════════════════════════════

# Module-level singleton cache for N8nClient — avoids re-creating the client
# (and re-reading env config) on every request. Invalidated if env changes.
_n8n_client: N8nClient | None = None
_n8n_client_env: tuple[str, str] | None = None
_n8n_client_lock = threading.Lock()


def _get_client() -> N8nClient:
    """Return a cached N8nClient singleton, re-created only when env config changes."""
    global _n8n_client, _n8n_client_env
    base_url = os.getenv("N8N_BASE_URL", "http://localhost:5678")
    api_key = os.getenv("N8N_API_KEY", "")
    current_env = (base_url, api_key)
    if _n8n_client is not None and _n8n_client_env == current_env:
        return _n8n_client
    with _n8n_client_lock:
        if _n8n_client is not None and _n8n_client_env == current_env:  # double-checked locking
            return _n8n_client
        _n8n_client = N8nClient(base_url=base_url, api_key=api_key)
        _n8n_client_env = current_env
    return _n8n_client


def _set_client(client: N8nClient | None) -> None:
    """供测试注入自定义 N8nClient（隔离 n8n 服务）。

    重置 env 缓存以强制下次按新 client 使用。
    """
    global _n8n_client, _n8n_client_env
    with _n8n_client_lock:
        _n8n_client = client
        _n8n_client_env = None


def receive_webhook(
    payload: dict[str, Any],
    *,
    raw_body: bytes,
    signature: str | None,
) -> dict[str, Any]:
    """处理 n8n webhook 载荷。

    Thin wrapper around :func:`maop.enterprise.n8n.handle_n8n_webhook`，
    将 webhook 转发到 MAOP delegate 处理。签名校验由 enterprise 模块内部完成。

    Parameters
    ----------
    payload : dict
        已解析的 webhook 载荷（Pydantic model_dump 后）。
    raw_body : bytes
        原始请求体（用于 HMAC 签名校验）。
    signature : str | None
        请求头中的签名（``X-N8N-Signature`` 或 ``X-MAOP-Signature``）。
    """
    return handle_n8n_webhook(payload, raw_body=raw_body, signature=signature)


def list_workflows() -> dict[str, Any]:
    """列出所有 n8n 工作流。

    Returns
    -------
    dict
        ``{"workflows": [...], "count": int}``

    Raises
    ------
    N8nIntegrationError
        n8n 集成错误（网络不可达 / API 异常），由 router 转换为 502。
    """
    with _get_client() as client:
        workflows = client.list_workflows()
        return {"workflows": workflows, "count": len(workflows)}


def trigger_workflow(
    workflow_id: str,
    *,
    data: dict[str, Any] | None = None,
    wait_for_completion: bool = False,
) -> dict[str, Any]:
    """触发指定 n8n 工作流。

    Returns
    -------
    dict
        执行信息（``execution_id`` / ``status`` / ...）。

    Raises
    ------
    N8nIntegrationError
        n8n 集成错误，由 router 转换为 502。
    """
    with _get_client() as client:
        execution = client.trigger_workflow(
            workflow_id,
            data=data or {},
            wait_for_completion=wait_for_completion,
        )
        return execution.model_dump()


def get_execution(execution_id: str) -> dict[str, Any]:
    """获取 n8n 工作流执行状态。

    Returns
    -------
    dict
        执行详情（``execution_id`` / ``status`` / ...）。

    Raises
    ------
    N8nIntegrationError
        n8n 集成错误，由 router 转换为 502。
    """
    with _get_client() as client:
        execution = client.get_execution(execution_id)
        return execution.model_dump()


def health_check() -> dict[str, Any]:
    """检查 n8n 是否可达。

    Returns
    -------
    dict
        ``{"n8n_reachable": bool, "base_url": str}``
    """
    # 从环境变量读取 base_url，避免访问 client 的内部属性 _base_url
    base_url = os.getenv("N8N_BASE_URL", "http://localhost:5678")
    with _get_client() as client:
        healthy = client.health_check()
        return {"n8n_reachable": healthy, "base_url": base_url}


# ══════════════════════════════════════════════════════════════════
# §2  Relay Platform — 第三方中转平台管理
# ══════════════════════════════════════════════════════════════════

from maop.core.agent.llm_chat.relay_platform import (
    PriceComparison,
    RelayPlatform,
    RelayPlatformManager,
    get_relay_platform_manager,
)


def get_relay_manager(app_state: Any) -> RelayPlatformManager:
    """从 app.state 获取管理器，回退到模块级单例。

    Parameters
    ----------
    app_state : Any
        ``request.app.state`` 对象（Starlette 应用状态）。
    """
    mgr = getattr(app_state, "relay_platform_manager", None)
    if mgr is not None:
        return mgr
    return get_relay_platform_manager()


def list_platforms(manager: RelayPlatformManager, *, type: str = "") -> list[RelayPlatform]:
    """列出所有中转平台，可按类型过滤。

    Parameters
    ----------
    manager : RelayPlatformManager
        已获取的管理器实例（由 router 从 app.state 注入或全局单例）。
    type : str
        类型过滤：``relay`` / ``direct``，空返回全部。
    """
    return manager.list_platforms(type=type)


def register_platform(manager: RelayPlatformManager, platform: RelayPlatform) -> RelayPlatform:
    """注册或更新一个中转平台。返回已注册的平台。"""
    manager.register_platform(platform)
    logger.info(
        "[relay-platforms] 已注册平台: %s (%s)", platform.name, platform.display_name
    )
    return platform


def remove_platform(manager: RelayPlatformManager, name: str) -> bool:
    """删除一个中转平台。

    Returns
    -------
    bool
        True 表示删除成功；False 表示平台不存在。
    """
    existing = manager.get_platform(name)
    if existing is None:
        return False
    manager.remove_platform(name)
    logger.info("[relay-platforms] 已删除平台: %s", name)
    return True


def discover_models(
    manager: RelayPlatformManager,
    name: str,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]] | None:
    """发现指定中转平台的可用模型列表。

    Returns
    -------
    list[dict] | None
        模型 dict 列表；若平台不存在返回 None。
    """
    platform = manager.get_platform(name)
    if platform is None:
        return None
    models = manager.discover_models(name, use_cache=use_cache)
    return [m.model_dump() for m in models]


def compare_prices(manager: RelayPlatformManager, model_id: str) -> list[PriceComparison]:
    """对比同一模型在不同平台的价格。"""
    return manager.compare_prices(model_id)


def recommend_platform(
    manager: RelayPlatformManager,
    model_id: str,
    *,
    prefer_domestic: bool = False,
) -> str | None:
    """推荐最优平台（价格最低 + 可用）。返回平台名或 None。"""
    return manager.recommend_platform(model_id, prefer_domestic=prefer_domestic)