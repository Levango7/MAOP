"""TenantIsolation — 多租户隔离管理器.

管理租户、租户-Agent 分配、租户 Agent 额度、使用统计。所有数据持久化
到 SQLite，线程安全（``threading.RLock`` + WAL）。

核心组件：
  - ``Tenant``       — 租户元数据（Pydantic）
  - ``TenantQuota``  — 租户 Agent 额度（Pydantic）
  - ``TenantUsage``  — 租户 Agent 使用情况（Pydantic）
  - ``TenantIsolation`` — 管理器

表结构：
  - ``tenants``                — 租户主表
  - ``tenant_agent_assignments`` — 租户-Agent 分配关系
  - ``tenant_quotas``          — 租户 Agent 额度
  - ``tenant_usage``           — 租户 Agent 使用记录

线程安全：``threading.RLock`` 保护内存缓存；SQLite 由 WAL + busy_timeout
保证并发。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid  # noqa: F401
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class Tenant(BaseModel):
    """租户元数据."""

    tenant_id: str = Field(..., description="租户唯一标识(UUID)")
    name: str = Field(..., min_length=1, description="租户名")
    display_name: str = Field(default="", description="展示名称")
    max_agents: int = Field(default=0, ge=0, description="最大 Agent 数(0=不限)")
    monthly_budget: float = Field(default=0.0, ge=0.0, description="月预算")
    enabled: bool = Field(default=True, description="是否启用")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class TenantQuota(BaseModel):
    """租户 Agent 额度."""

    tenant_id: str = Field(..., description="租户 ID")
    agent_name: str = Field(..., description="Agent 名称")
    quota_limit: int = Field(..., ge=0, description="额度上限")
    quota_used: int = Field(default=0, ge=0, description="已用额度")
    reset_period: str = Field(default="monthly", description="重置周期")


class TenantUsage(BaseModel):
    """租户 Agent 使用情况."""

    agent_name: str = Field(..., description="Agent 名称")
    calls: int = Field(default=0, ge=0, description="调用次数")
    cost: float = Field(default=0.0, ge=0.0, description="成本")
    quota_used: int = Field(default=0, ge=0, description="已用额度")
    quota_limit: int = Field(default=0, ge=0, description="额度上限")


# ── TenantIsolation 管理器 ──────────────────────────────────────
class TenantIsolation:
    """多租户隔离管理器.

    Usage::

        iso = TenantIsolation()
        tenant = Tenant(tenant_id=str(uuid.uuid4()), name="acme")
        iso.create_tenant(tenant)
        iso.assign_agent_to_tenant(tenant.tenant_id, "claude-code")
        ok = iso.check_tenant_access(tenant.tenant_id, "claude-code")

    Parameters
    ----------
    db_path : Path | None
        SQLite 数据库路径。``None`` 时由 ``get_db_path("tenant")`` 解析。
    """

    def __init__(self, db_path: Any = None) -> None:
        self._db_path = db_path if db_path is not None else get_db_path("tenant")
        self._lock = threading.RLock()
        # 内存缓存：tenant_id -> Tenant
        self._tenants: dict[str, Tenant] = {}
        # 内存缓存：(tenant_id, agent_name) -> TenantQuota
        self._quotas: dict[tuple[str, str], TenantQuota] = {}
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化数据库表（幂等）。"""
        with sqlite_connect(self._db_path) as conn:
            # 租户主表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    max_agents INTEGER DEFAULT 0,
                    monthly_budget REAL DEFAULT 0.0,
                    enabled INTEGER DEFAULT 1,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            # 租户-Agent 分配关系
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_agent_assignments (
                    tenant_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    assigned_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, agent_name),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
                )
            """)
            # 租户 Agent 额度
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_quotas (
                    tenant_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    quota_limit INTEGER NOT NULL DEFAULT 0,
                    quota_used INTEGER NOT NULL DEFAULT 0,
                    reset_period TEXT DEFAULT 'monthly',
                    PRIMARY KEY (tenant_id, agent_name),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
                )
            """)
            # 租户 Agent 使用记录
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenant_usage (
                    tenant_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    quota_used INTEGER DEFAULT 0,
                    quota_limit INTEGER DEFAULT 0,
                    PRIMARY KEY (tenant_id, agent_name),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenant_name ON tenants(name)"
            )

    # ── 租户 CRUD ───────────────────────────────────────────────
    def create_tenant(self, tenant: Tenant) -> None:
        """创建租户.

        若 ``tenant_id`` 已存在则更新（upsert）。

        Parameters
        ----------
        tenant : Tenant
            租户元数据。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO tenants
                       (tenant_id, name, display_name, max_agents, monthly_budget,
                        enabled, metadata)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        tenant.tenant_id,
                        tenant.name,
                        tenant.display_name,
                        tenant.max_agents,
                        tenant.monthly_budget,
                        1 if tenant.enabled else 0,
                        _json_dumps(tenant.metadata),
                    ),
                )
            self._tenants[tenant.tenant_id] = tenant
            logger.info("[tenant] created tenant: %s (%s)", tenant.name, tenant.tenant_id)

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        """获取租户.

        Returns
        -------
        Tenant | None
            租户元数据，不存在返回 None。
        """
        with self._lock:
            # 优先查缓存
            if tenant_id in self._tenants:
                return self._tenants[tenant_id]
            # 查 DB
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM tenants WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            if row is None:
                return None
            tenant = self._row_to_tenant(row)
            self._tenants[tenant_id] = tenant
            return tenant

    def list_tenants(self) -> list[Tenant]:
        """列出所有租户."""
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM tenants ORDER BY name"
                ).fetchall()
            return [self._row_to_tenant(r) for r in rows]

    # ── Agent 分配 ──────────────────────────────────────────────
    def assign_agent_to_tenant(
        self,
        tenant_id: str,
        agent_name: str,
    ) -> None:
        """为租户分配 Agent.

        若租户不存在则抛出 ``ValueError``。若已分配则幂等返回。
        若租户 ``max_agents > 0`` 且已分配数已达上限则抛出 ``ValueError``。

        Parameters
        ----------
        tenant_id : str
            租户 ID。
        agent_name : str
            Agent 名称。
        """
        with self._lock:
            tenant = self.get_tenant(tenant_id)
            if tenant is None:
                raise ValueError(f"tenant not found: {tenant_id}")
            if not tenant.enabled:
                raise ValueError(f"tenant disabled: {tenant_id}")

            # 检查上限
            if tenant.max_agents > 0:
                current = self._count_assignments(tenant_id)
                # 若尚未分配该 agent，则需 +1
                already_assigned = self._is_assigned(tenant_id, agent_name)
                if not already_assigned and current >= tenant.max_agents:
                    raise ValueError(
                        f"tenant {tenant_id} reached max_agents limit ({tenant.max_agents})"
                    )

            import time as _time
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO tenant_agent_assignments
                       (tenant_id, agent_name, assigned_at)
                       VALUES (?,?,?)""",
                    (tenant_id, agent_name, _time.time()),
                )
            logger.info(
                "[tenant] assigned agent %s to tenant %s", agent_name, tenant_id,
            )

    def get_tenant_agents(self, tenant_id: str) -> list[str]:
        """获取租户可用的 Agent 列表.

        Returns
        -------
        list[str]
            Agent 名称列表（按名称排序）。租户不存在时返回空列表。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    """SELECT agent_name FROM tenant_agent_assignments
                       WHERE tenant_id = ? ORDER BY agent_name""",
                    (tenant_id,),
                ).fetchall()
            return [r["agent_name"] for r in rows]

    # ── 额度管理 ────────────────────────────────────────────────
    def set_tenant_quota(
        self,
        tenant_id: str,
        agent_name: str,
        quota: TenantQuota,
    ) -> None:
        """设置租户 Agent 额度.

        Parameters
        ----------
        tenant_id : str
            租户 ID。
        agent_name : str
            Agent 名称。
        quota : TenantQuota
            额度配置。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO tenant_quotas
                       (tenant_id, agent_name, quota_limit, quota_used, reset_period)
                       VALUES (?,?,?,?,?)""",
                    (
                        tenant_id,
                        agent_name,
                        quota.quota_limit,
                        quota.quota_used,
                        quota.reset_period,
                    ),
                )
            self._quotas[(tenant_id, agent_name)] = quota
            logger.info(
                "[tenant] set quota for tenant %s agent %s: limit=%d used=%d",
                tenant_id, agent_name, quota.quota_limit, quota.quota_used,
            )

    def get_tenant_quota(
        self,
        tenant_id: str,
        agent_name: str,
    ) -> TenantQuota | None:
        """获取租户 Agent 额度.

        Returns
        -------
        TenantQuota | None
            额度配置，不存在返回 None。
        """
        with self._lock:
            # 优先查缓存
            key = (tenant_id, agent_name)
            if key in self._quotas:
                return self._quotas[key]
            # 查 DB
            with sqlite_connect(self._db_path) as conn:
                row = conn.execute(
                    """SELECT * FROM tenant_quotas
                       WHERE tenant_id = ? AND agent_name = ?""",
                    (tenant_id, agent_name),
                ).fetchone()
            if row is None:
                return None
            quota = TenantQuota(
                tenant_id=row["tenant_id"],
                agent_name=row["agent_name"],
                quota_limit=row["quota_limit"],
                quota_used=row["quota_used"],
                reset_period=row["reset_period"],
            )
            self._quotas[key] = quota
            return quota

    # ── 访问检查 ────────────────────────────────────────────────
    def check_tenant_access(
        self,
        tenant_id: str,
        agent_name: str,
    ) -> bool:
        """检查租户是否有权访问 Agent.

        判定标准：
          1. 租户存在且 ``enabled=True``
          2. Agent 已分配给该租户
          3. 若设置了额度，则 ``quota_used < quota_limit``

        Returns
        -------
        bool
            是否有权访问。
        """
        with self._lock:
            tenant = self.get_tenant(tenant_id)
            if tenant is None or not tenant.enabled:
                return False
            if not self._is_assigned(tenant_id, agent_name):
                return False
            # 额度检查
            quota = self.get_tenant_quota(tenant_id, agent_name)
            if quota is not None and quota.quota_limit > 0:
                if quota.quota_used >= quota.quota_limit:
                    return False
            return True

    # ── 使用情况 ────────────────────────────────────────────────
    def get_tenant_usage(self, tenant_id: str) -> list[TenantUsage]:
        """获取租户使用情况.

        Returns
        -------
        list[TenantUsage]
            按 Agent 名称排序的使用列表。租户不存在时返回空列表。
        """
        with self._lock:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    """SELECT agent_name, calls, cost, quota_used, quota_limit
                       FROM tenant_usage
                       WHERE tenant_id = ?
                       ORDER BY agent_name""",
                    (tenant_id,),
                ).fetchall()
            return [
                TenantUsage(
                    agent_name=r["agent_name"],
                    calls=r["calls"],
                    cost=r["cost"],
                    quota_used=r["quota_used"],
                    quota_limit=r["quota_limit"],
                )
                for r in rows
            ]

    def record_usage(
        self,
        tenant_id: str,
        agent_name: str,
        *,
        calls: int = 1,
        cost: float = 0.0,
        quota_used: int = 0,
    ) -> None:
        """记录租户 Agent 使用（累加）.

        Parameters
        ----------
        tenant_id : str
            租户 ID。
        agent_name : str
            Agent 名称。
        calls : int
            本次调用次数（累加）。
        cost : float
            本次成本（累加）。
        quota_used : int
            本次消耗额度（累加）。
        """
        with self._lock:
            # 查询当前额度上限（用于同步 quota_limit 字段）
            quota = self.get_tenant_quota(tenant_id, agent_name)
            quota_limit = quota.quota_limit if quota else 0

            with sqlite_connect(self._db_path) as conn:
                # upsert 累加
                conn.execute(
                    """INSERT INTO tenant_usage
                       (tenant_id, agent_name, calls, cost, quota_used, quota_limit)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(tenant_id, agent_name) DO UPDATE SET
                         calls = calls + excluded.calls,
                         cost = cost + excluded.cost,
                         quota_used = quota_used + excluded.quota_used,
                         quota_limit = excluded.quota_limit""",
                    (tenant_id, agent_name, calls, cost, quota_used, quota_limit),
                )
                # 同步更新 tenant_quotas.quota_used
                if quota is not None and quota_used > 0:
                    conn.execute(
                        """UPDATE tenant_quotas
                           SET quota_used = quota_used + ?
                           WHERE tenant_id = ? AND agent_name = ?""",
                        (quota_used, tenant_id, agent_name),
                    )
                    # 更新缓存
                    key = (tenant_id, agent_name)
                    if key in self._quotas:
                        self._quotas[key] = self._quotas[key].model_copy(
                            update={"quota_used": self._quotas[key].quota_used + quota_used},
                        )

    # ── 内部工具 ────────────────────────────────────────────────
    def _count_assignments(self, tenant_id: str) -> int:
        """统计租户已分配的 Agent 数量."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tenant_agent_assignments WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def _is_assigned(self, tenant_id: str, agent_name: str) -> bool:
        """检查 Agent 是否已分配给租户."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT 1 FROM tenant_agent_assignments
                   WHERE tenant_id = ? AND agent_name = ?""",
                (tenant_id, agent_name),
            ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_tenant(row: sqlite3.Row) -> Tenant:
        """sqlite3.Row → Tenant."""
        import json as _json
        try:
            metadata = _json.loads(row["metadata"]) if row["metadata"] else {}
        except (ValueError, TypeError):
            metadata = {}
        return Tenant(
            tenant_id=row["tenant_id"],
            name=row["name"],
            display_name=row["display_name"],
            max_agents=row["max_agents"],
            monthly_budget=row["monthly_budget"],
            enabled=bool(row["enabled"]),
            metadata=metadata,
        )


# ── JSON 工具 ────────────────────────────────────────────────────
def _json_dumps(obj: Any) -> str:
    """安全 JSON 序列化，失败返回 '{}'. """
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"