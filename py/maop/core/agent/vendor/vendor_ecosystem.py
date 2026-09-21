"""VendorEcosystem — 厂商生态管理：厂商→Agent 映射与厂商内协作建议.

本模块维护"厂商"（vendor）与"Agent"之间的归属关系。一个厂商可拥有多个
Agent，同一厂商下的 Agent 互为"可协作"候选（``suggest_collaboration``）。

设计要点：
    * 厂商信息持久化到 SQLite 表 ``vendor_registry``。
    * Agent→厂商映射持久化到 SQLite 表 ``vendor_agent_mapping``。
    * 全程使用 ``threading.RLock`` 保护内存状态；SQLite 由 WAL +
      busy_timeout 保证并发安全。
    * ``get_preset_vendors()`` 返回 10 个预置厂商，覆盖国内主流、开源与
      国际厂商，便于开箱即用。

线程安全策略：所有公开方法在 ``with self._lock`` 块内完成"读-改-写"，
RLock 允许同一线程嵌套加锁，避免内部方法互调时死锁。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


# ── Pydantic 模型 ────────────────────────────────────────────────
class Vendor(BaseModel):
    """厂商描述.

    Attributes
    ----------
    name : str
        厂商唯一标识（如 ``"alibaba"`` / ``"tencent"`` / ``"bytedance"``）。
    display_name : str
        人类可读的显示名（如 ``"阿里巴巴"``）。
    region : str
        所属区域，``"domestic"``（国内）或 ``"international"``（国际）。
    sso_enabled : bool
        是否支持统一认证（SSO）。
    unified_billing : bool
        是否支持统一计费。
    metadata : dict
        扩展元数据（如官网、联系方式等）。
    """

    name: str = Field(..., description="厂商唯一标识")
    display_name: str = Field(..., description="厂商显示名")
    region: str = Field(default="domestic", description="domestic/international")
    sso_enabled: bool = Field(default=False, description="是否支持统一认证")
    unified_billing: bool = Field(default=False, description="是否支持统一计费")
    metadata: dict = Field(default_factory=dict, description="扩展元数据")


# ── 预置厂商数据 ──────────────────────────────────────────────────
def get_preset_vendors() -> list[Vendor]:
    """返回 10 个预置厂商.

    覆盖国内主流（阿里、腾讯、字节、华为、智谱、美团、CSDN、DeepSeek）、
    开源社区与国际厂商，便于开箱即用。

    Returns
    -------
    list[Vendor]
        10 个预置厂商实例，顺序固定。
    """
    return [
        Vendor(
            name="alibaba",
            display_name="阿里巴巴",
            region="domestic",
            sso_enabled=True,
            unified_billing=True,
            metadata={"website": "https://www.alibaba.com"},
        ),
        Vendor(
            name="tencent",
            display_name="腾讯",
            region="domestic",
            sso_enabled=True,
            unified_billing=True,
            metadata={"website": "https://www.tencent.com"},
        ),
        Vendor(
            name="bytedance",
            display_name="字节跳动",
            region="domestic",
            sso_enabled=True,
            unified_billing=True,
            metadata={"website": "https://www.bytedance.com"},
        ),
        Vendor(
            name="huawei",
            display_name="华为",
            region="domestic",
            sso_enabled=True,
            unified_billing=True,
            metadata={"website": "https://www.huawei.com"},
        ),
        Vendor(
            name="zhipu",
            display_name="智谱AI",
            region="domestic",
            sso_enabled=False,
            unified_billing=True,
            metadata={"website": "https://www.zhipuai.cn"},
        ),
        Vendor(
            name="meituan",
            display_name="美团",
            region="domestic",
            sso_enabled=False,
            unified_billing=True,
            metadata={"website": "https://www.meituan.com"},
        ),
        Vendor(
            name="csdn",
            display_name="CSDN",
            region="domestic",
            sso_enabled=False,
            unified_billing=False,
            metadata={"website": "https://www.csdn.net"},
        ),
        Vendor(
            name="deepseek",
            display_name="DeepSeek",
            region="domestic",
            sso_enabled=False,
            unified_billing=True,
            metadata={"website": "https://www.deepseek.com"},
        ),
        Vendor(
            name="opensource",
            display_name="开源社区",
            region="domestic",
            sso_enabled=False,
            unified_billing=False,
            metadata={"website": "https://opensource.org"},
        ),
        Vendor(
            name="international",
            display_name="国际厂商",
            region="international",
            sso_enabled=False,
            unified_billing=False,
            metadata={"website": "https://example.com"},
        ),
    ]


# ── VendorEcosystem ─────────────────────────────────────────────
class VendorEcosystem:
    """厂商生态管理器.

    维护厂商注册表与 Agent→厂商映射，提供厂商内协作建议。

    Parameters
    ----------
    db_path : Path | None
        SQLite 持久化路径。``None`` 时由 ``get_db_path("vendor")`` 解析。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path else get_db_path("vendor")
        self._lock = threading.RLock()
        self._init_db()

    # ── 初始化 ──────────────────────────────────────────────────
    def _init_db(self) -> None:
        """初始化厂商表与映射表（幂等）。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            # 厂商注册表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vendor_registry (
                    name TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    region TEXT DEFAULT 'domestic',
                    sso_enabled INTEGER DEFAULT 0,
                    unified_billing INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            # Agent→厂商映射表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vendor_agent_mapping (
                    agent_name TEXT PRIMARY KEY,
                    vendor_name TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_mapping "
                "ON vendor_agent_mapping(vendor_name)"
            )

    # ── 厂商注册 ────────────────────────────────────────────────
    def register_vendor(self, vendor: Vendor) -> None:
        """注册或更新厂商.

        若厂商名已存在则覆盖更新（upsert 语义）。

        Parameters
        ----------
        vendor : Vendor
            厂商描述对象。
        """
        with self._lock, sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO vendor_registry
                       (name, display_name, region, sso_enabled,
                        unified_billing, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET
                         display_name=excluded.display_name,
                         region=excluded.region,
                         sso_enabled=excluded.sso_enabled,
                         unified_billing=excluded.unified_billing,
                         metadata=excluded.metadata""",
                (
                    vendor.name,
                    vendor.display_name,
                    vendor.region,
                    int(vendor.sso_enabled),
                    int(vendor.unified_billing),
                    vendor.model_dump_json(),
                ),
            )
        logger.debug("[vendor] 注册厂商 %s", vendor.name)

    def register_agent_to_vendor(self, agent_name: str, vendor_name: str) -> None:
        """将 Agent 归属到厂商.

        若 Agent 已归属其他厂商，则更新为新厂商。厂商必须已注册。

        Parameters
        ----------
        agent_name : str
            Agent 名称。
        vendor_name : str
            厂商名称（必须已通过 ``register_vendor`` 注册）。

        Raises
        ------
        ValueError
            厂商不存在时抛出。
        """
        with self._lock:
            # 校验厂商存在
            if self.get_vendor(vendor_name) is None:
                raise ValueError(
                    f"厂商 {vendor_name!r} 未注册，请先调用 register_vendor"
                )
            with sqlite_connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO vendor_agent_mapping (agent_name, vendor_name)
                       VALUES (?, ?)
                       ON CONFLICT(agent_name) DO UPDATE SET
                         vendor_name=excluded.vendor_name""",
                    (agent_name, vendor_name),
                )
        logger.debug("[vendor] Agent %s 归属到厂商 %s", agent_name, vendor_name)

    # ── 查询 ────────────────────────────────────────────────────
    def get_vendor(self, vendor_name: str) -> Vendor | None:
        """获取厂商信息.

        Returns
        -------
        Vendor | None
            厂商不存在时返回 ``None``。
        """
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM vendor_registry WHERE name = ?",
                (vendor_name,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_vendor(row)

    def list_vendors(self) -> list[Vendor]:
        """列出所有已注册厂商."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM vendor_registry ORDER BY name"
            ).fetchall()
        return [self._row_to_vendor(r) for r in rows]

    def get_agents_by_vendor(self, vendor_name: str) -> list[str]:
        """获取厂商下所有 Agent 名称.

        Returns
        -------
        list[str]
            Agent 名称列表，按字母序排列。厂商不存在或无 Agent 时返回空列表。
        """
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT agent_name FROM vendor_agent_mapping "
                "WHERE vendor_name = ? ORDER BY agent_name",
                (vendor_name,),
            ).fetchall()
        return [r["agent_name"] for r in rows]

    def get_vendor_of_agent(self, agent_name: str) -> str | None:
        """获取 Agent 所属厂商.

        Returns
        -------
        str | None
            厂商名称；Agent 未归属任何厂商时返回 ``None``。
        """
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT vendor_name FROM vendor_agent_mapping WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
        return row["vendor_name"] if row else None

    def suggest_collaboration(self, agent_name: str) -> list[str]:
        """建议同厂商可协作的 Agent.

        返回与给定 Agent 同属一个厂商的其他 Agent 名称列表。协作建议基于
        "同厂商的 Agent 共享认证、计费与运维体系，协作成本更低"的假设。

        Parameters
        ----------
        agent_name : str
            目标 Agent 名称。

        Returns
        -------
        list[str]
            同厂商其他 Agent 名称列表（不含自身），按字母序排列。
            Agent 未归属任何厂商时返回空列表。
        """
        vendor_name = self.get_vendor_of_agent(agent_name)
        if vendor_name is None:
            return []
        siblings = self.get_agents_by_vendor(vendor_name)
        # 排除自身
        return [a for a in siblings if a != agent_name]

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _row_to_vendor(row: sqlite3.Row) -> Vendor:
        """sqlite3.Row → Vendor.

        优先从 metadata 列恢复完整 Vendor（含扩展字段），回退到列字段重建。
        """
        metadata_json = row["metadata"] or "{}"
        try:
            return Vendor.model_validate_json(metadata_json)
        except Exception:
            # metadata 损坏时回退到列字段重建
            logger.warning(
                "[vendor] 厂商 %s 的 metadata 损坏，回退到列字段重建",
                row["name"],
            )
            return Vendor(
                name=row["name"],
                display_name=row["display_name"],
                region=row["region"],
                sso_enabled=bool(row["sso_enabled"]),
                unified_billing=bool(row["unified_billing"]),
            )