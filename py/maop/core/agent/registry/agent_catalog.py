"""MAOP Agent Catalog — 统一 Agent 元数据注册中心.

管理所有 AI Agent 的元数据：能力声明、计费模式、授权方式、运行时配置、降级链。
与 ``lifecycle.agent_registry``（CLI 发现 + 健康探测）正交：本模块聚焦
**元数据编目**，供编排器/路由器/计费引擎查询决策。

核心组件：
  - ``AgentDescriptor``  — 单个 Agent 的完整元数据（Pydantic 模型）
  - ``AgentCatalog``     — 注册中心：register/get/list_all/search/delete/update_health
  - ``get_catalog``      — 进程级双重检查锁定单例

Usage::

    from maop.core.agent.registry.agent_catalog import (
        AgentCatalog, AgentDescriptor, AgentCapability, BillingModel, AuthMethod,
    )

    catalog = AgentCatalog.default()
    catalog.register(AgentDescriptor(
        name="claude-code",
        display_name="Claude Code",
        vendor="Anthropic",
        capabilities=[AgentCapability.CODE_GENERATION, AgentCapability.TOOL_USE],
        billing_model=BillingModel.PER_TOKEN,
        auth_method=AuthMethod.API_KEY,
    ))
    coders = catalog.search(AgentCapability.CODE_GENERATION)
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from maop.core.agent.registry.agent_catalog_store import AgentCatalogStore

logger = logging.getLogger(__name__)


# ── 枚举 ──────────────────────────────────────────────────────────
class AgentCapability(str, Enum):
    """Agent 能力声明枚举（str mixin 便于 JSON 序列化与比较）。"""

    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CHAT = "chat"
    REASONING = "reasoning"
    MULTIMODAL = "multimodal"
    LONG_CONTEXT = "long_context"
    TOOL_USE = "tool_use"
    WEB_SEARCH = "web_search"
    FILE_EDIT = "file_edit"
    TERMINAL = "terminal"


class BillingModel(str, Enum):
    """计费模式枚举。"""

    PER_TOKEN = "per_token"
    PER_CALL = "per_call"
    SUBSCRIPTION = "subscription"
    CREDIT = "credit"
    BLACKBOX = "blackbox"
    FREE = "free"


class AuthMethod(str, Enum):
    """授权方式枚举。"""

    API_KEY = "api_key"
    OAUTH = "oauth"
    USERNAME_PASSWORD = "username_password"
    LICENSE = "license"
    NONE = "none"


# ── AgentDescriptor ───────────────────────────────────────────────
class AgentDescriptor(BaseModel):
    """单个 Agent 的完整元数据描述符.

    所有可变默认值使用 ``Field(default_factory=...)`` 避免 Pydantic 共享
    可变默认值的陷阱（与 AdapterConfig / RegisteredAgent 保持一致）。
    """

    name: str = Field(..., min_length=1, max_length=256, description="Agent 唯一标识")
    display_name: str = Field(default="", max_length=256, description="展示名称")
    vendor: str = Field(default="", max_length=128, description="供应商")
    version: str = Field(default="", max_length=64, description="版本号")
    # adapter_type: cli | http | mcp | web
    adapter_type: str = Field(default="", max_length=32, description="适配器类型")
    adapter_config: dict[str, Any] = Field(default_factory=dict, description="适配器配置")
    capabilities: list[AgentCapability] = Field(default_factory=list, description="能力声明")
    max_context_length: int = Field(default=0, ge=0, description="最大上下文长度")
    supports_streaming: bool = Field(default=False, description="是否支持流式输出")
    supports_tool_call: bool = Field(default=False, description="是否支持工具调用")
    billing_model: BillingModel = Field(default=BillingModel.BLACKBOX, description="计费模式")
    billing_config: dict[str, Any] = Field(default_factory=dict, description="完整计费配置")
    auth_method: AuthMethod = Field(default=AuthMethod.NONE, description="授权方式")
    # 区域归属：domestic=国产工具，international=国际工具。用于国产优先路由策略。
    region: str = Field(default="international", description="区域：domestic(国产) | international(国际)")
    auth_credentials_ref: str = Field(default="", max_length=512, description="凭据引用")
    max_concurrent: int = Field(default=1, ge=1, description="最大并发数")
    rate_limit_per_min: int = Field(default=0, ge=0, description="每分钟速率限制")
    timeout_s: float = Field(default=30.0, gt=0, description="超时秒数")
    retry_count: int = Field(default=3, ge=0, description="重试次数")
    fallback_agents: list[str] = Field(default_factory=list, description="降级链 Agent 名称列表")
    enabled: bool = Field(default=True, description="是否启用")
    healthy: bool = Field(default=True, description="是否健康")
    last_health_check: float = Field(default=0.0, ge=0, description="上次健康检查时间戳")

    # ── 校验 ──────────────────────────────────────────────────────
    @field_validator("adapter_type")
    @classmethod
    def _validate_adapter_type(cls, v: str) -> str:
        """adapter_type 限定为 cli | http | mcp | web | ''（空允许，表示未指定）。"""
        if v == "":
            return v
        allowed = {"cli", "http", "mcp", "web"}
        if v not in allowed:
            raise ValueError(
                f"adapter_type must be one of {sorted(allowed)} or empty, got {v!r}"
            )
        return v

    @field_validator("fallback_agents")
    @classmethod
    def _validate_no_self_fallback(cls, v: list[str], info: Any) -> list[str]:
        """降级链不应包含自身（会导致无限循环）。

        ``info.data`` 在字段声明顺序下逐字段填充；``name`` 在 fallback_agents
        之前声明，故可访问。若 name 尚未填充（如部分构造场景），跳过检查。
        """
        name = info.data.get("name") if info.data else None
        if name and name in v:
            raise ValueError(
                f"fallback_agents must not contain the agent itself ({name!r})"
            )
        return v

    # ── 便捷方法 ──────────────────────────────────────────────────
    def has_capability(self, capability: AgentCapability) -> bool:
        """检查是否具备指定能力。"""
        return capability in self.capabilities

    def to_store_dict(self) -> dict[str, Any]:
        """转换为 Store 兼容的 dict（枚举转 value 字符串）。"""
        d = self.model_dump()
        d["capabilities"] = [c.value if isinstance(c, Enum) else c for c in d["capabilities"]]
        d["billing_model"] = self.billing_model.value
        d["auth_method"] = self.auth_method.value
        return d

    @classmethod
    def from_store_dict(cls, d: dict[str, Any]) -> AgentDescriptor:
        """从 Store 加载的 dict 构造（容忍枚举为字符串或枚举实例）。"""
        # 去掉 Store 内部字段（created_at / updated_at）
        clean = {k: v for k, v in d.items() if k not in ("created_at", "updated_at")}
        return cls.model_validate(clean)


# ── AgentCatalog 注册中心 ─────────────────────────────────────────
class AgentCatalog:
    """Agent 元数据注册中心.

    提供内存索引 + SQLite 持久化的双重管理。线程安全（读写锁保护
    内存索引；SQLite 由 WAL + busy_timeout 保证并发）。

    Parameters
    ----------
    db_path : str | Path | None
        SQLite 数据库路径。``None`` 时由 AgentCatalogStore 自动解析。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._store = AgentCatalogStore(db_path=db_path)
        self._agents: dict[str, AgentDescriptor] = {}
        self._lock = threading.RLock()
        self._load_from_store()

    def _load_from_store(self) -> None:
        """启动时从 SQLite 加载所有已持久化的 Agent。

        单条记录损坏不会阻止其他记录加载——逐行 try/except 容错。
        """
        try:
            rows = self._store.load_all()
        except Exception as exc:
            logger.warning("[agent_catalog] failed to load from store: %s", exc)
            return
        loaded = 0
        for row in rows:
            try:
                desc = AgentDescriptor.from_store_dict(row)
                self._agents[desc.name] = desc
                loaded += 1
            except Exception as exc:
                logger.warning(
                    "[agent_catalog] skipping corrupt agent row (name=%s): %s",
                    row.get("name", "?"),
                    exc,
                )
        if loaded:
            logger.debug("[agent_catalog] loaded %d agents from store", loaded)

    # ── Register / Update ─────────────────────────────────────────
    def register(self, descriptor: AgentDescriptor) -> None:
        """注册或更新一个 Agent（按 name 幂等 upsert）.

        若同名 Agent 已存在，其所有字段将被新 descriptor 覆盖。

        先持久化到 SQLite，成功后再更新内存索引，保证持久化失败时
        内存与存储一致（不会出现内存已改但未落盘的情况）。
        """
        with self._lock:
            try:
                self._store.upsert(descriptor.to_store_dict())
            except Exception as exc:
                logger.error("[agent_catalog] persist failed for %s: %s", descriptor.name, exc)
                raise
            self._agents[descriptor.name] = descriptor
        logger.info("[agent_catalog] registered agent: %s", descriptor.name)

    # ── Get ───────────────────────────────────────────────────────
    def get(self, name: str) -> AgentDescriptor | None:
        """按 name 获取 Agent，不存在返回 None。"""
        with self._lock:
            return self._agents.get(name)

    # ── List ──────────────────────────────────────────────────────
    def list_all(self) -> list[AgentDescriptor]:
        """列出所有已注册 Agent（按 name 排序，返回副本引用安全）。"""
        with self._lock:
            return [self._agents[k] for k in sorted(self._agents.keys())]

    def list_enabled(self) -> list[AgentDescriptor]:
        """列出所有 enabled=True 的 Agent。"""
        with self._lock:
            return [d for k, d in self._agents.items() if d.enabled]

    # ── Search ────────────────────────────────────────────────────
    def search(self, capability: AgentCapability) -> list[AgentDescriptor]:
        """按能力搜索 Agent，返回具备该能力的所有 Agent（按 name 排序）.

        仅返回 enabled=True 的 Agent（禁用的 Agent 不参与路由决策）。
        """
        with self._lock:
            results = [
                d for d in self._agents.values()
                if d.enabled and capability in d.capabilities
            ]
            results.sort(key=lambda d: d.name)
            return results

    def search_by_vendor(self, vendor: str) -> list[AgentDescriptor]:
        """按供应商搜索 Agent。"""
        with self._lock:
            results = [d for d in self._agents.values() if d.vendor == vendor]
            results.sort(key=lambda d: d.name)
            return results

    # ── Delete ────────────────────────────────────────────────────
    def delete(self, name: str) -> bool:
        """注销一个 Agent。返回是否实际删除了记录。

        先从 SQLite 删除，成功后再更新内存索引，保证持久化失败时
        内存与存储一致。
        """
        with self._lock:
            existed = name in self._agents
            if existed:
                try:
                    self._store.delete(name)
                except Exception as exc:
                    logger.error("[agent_catalog] delete failed for %s: %s", name, exc)
                    raise
                self._agents.pop(name, None)
                logger.info("[agent_catalog] deleted agent: %s", name)
            return existed

    # ── Health ────────────────────────────────────────────────────
    def update_health(self, name: str, healthy: bool) -> None:
        """更新指定 Agent 的健康状态 + 检查时间戳.

        Agent 不存在时静默忽略（no-op），仅记录 debug 日志。

        先持久化到 SQLite，成功后再更新内存索引，保证持久化失败时
        内存与存储一致。
        """
        now = time.time()
        with self._lock:
            desc = self._agents.get(name)
            if desc is None:
                logger.debug("[agent_catalog] update_health: agent %r not found", name)
                return
            # Pydantic v2: model_copy for immutable update
            updated = desc.model_copy(update={
                "healthy": healthy,
                "last_health_check": now,
            })
            try:
                self._store.update_health(name, healthy, now)
            except Exception as exc:
                logger.error("[agent_catalog] persist health failed for %s: %s", name, exc)
                raise
            self._agents[name] = updated

    # ── Fallback chain ────────────────────────────────────────────
    def get_fallback_chain(self, name: str) -> list[AgentDescriptor]:
        """获取指定 Agent 的降级链（按 fallback_agents 顺序解析）.

        跳过不存在或未启用的 Agent。降级链中的 Agent 自身不再展开
        （避免无限递归；如需多层降级，调用方应自行迭代）。
        """
        with self._lock:
            desc = self._agents.get(name)
            if desc is None:
                return []
            chain: list[AgentDescriptor] = []
            for fb_name in desc.fallback_agents:
                fb = self._agents.get(fb_name)
                if fb is not None and fb.enabled:
                    chain.append(fb)
            return chain

    # ── Stats ─────────────────────────────────────────────────────
    def count(self) -> int:
        """返回已注册 Agent 总数。"""
        with self._lock:
            return len(self._agents)

    def count_healthy(self) -> int:
        """返回健康 Agent 数量。"""
        with self._lock:
            return sum(1 for d in self._agents.values() if d.healthy)

    # ── 构造便捷方法 ──────────────────────────────────────────────
    @classmethod
    def default(cls) -> AgentCatalog:
        """使用默认 DB 路径构造（与 AgentProxy(root_dir=...) 风格对齐）."""
        return cls(db_path=None)


# ── 进程级单例（双重检查锁定）─────────────────────────────────────
_catalog_instance: AgentCatalog | None = None
_catalog_lock = threading.Lock()


def get_catalog() -> AgentCatalog:
    """获取进程级 AgentCatalog 单例（双重检查锁定）.

    与 ``routers/agent_proxy.py`` 的 ``_get_bridge()`` 保持一致的单例模式。
    测试中可通过 ``reset_catalog()`` 重置单例以隔离。
    """
    global _catalog_instance
    if _catalog_instance is None:
        with _catalog_lock:
            if _catalog_instance is None:
                _catalog_instance = AgentCatalog.default()
    return _catalog_instance


def reset_catalog() -> None:
    """重置进程级单例（主要供测试隔离使用）."""
    global _catalog_instance
    with _catalog_lock:
        _catalog_instance = None