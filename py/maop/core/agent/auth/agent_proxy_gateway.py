"""AgentProxyGateway — 企业内网 Agent 代理网关。

处理 MAOS 企业场景：
    * 内网 Agent 代理（将内网路径映射到外网 Agent）
    * 统一账号（部门级访问控制）
    * 审计合规（只追加不修改的审计日志）
    * 部门预算（月度预算 + 重置日 + 扣减）

设计要点：
    * SQLite 持久化——三张表 ``proxy_rules`` / ``audit_events`` /
      ``department_budgets``，WAL 模式 + busy_timeout。
    * 线程安全——所有公开方法用 ``threading.RLock`` 保护。
    * 审计日志不可篡改——表无 UPDATE/DELETE 路径，只 INSERT + SELECT。
    * 部门预算按 ``(department, agent)`` 复合主键原子 upsert。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── 表名常量 ────────────────────────────────────────────────────────
_PROXY_RULE_TABLE = "proxy_rules"
_AUDIT_EVENT_TABLE = "audit_events"
_DEPT_BUDGET_TABLE = "department_budgets"


# ── Pydantic 模型 ──────────────────────────────────────────────────


class ProxyRule(BaseModel):
    """代理规则——将内网路径映射到外网 Agent。

    Attributes:
        rule_id: 规则唯一 ID（UUID 十六进制）。
        internal_path: 内网路径，如 ``/agents/external/deepseek``。
        external_agent: 外网 Agent 名。
        allowed_departments: 允许访问的部门列表（空 = 全部允许）。
        enabled: 是否启用。
    """

    rule_id: str = Field(default_factory=lambda: secrets.token_hex(16))
    internal_path: str = Field(..., min_length=1)
    external_agent: str = Field(..., min_length=1)
    allowed_departments: list[str] = Field(default_factory=list)
    enabled: bool = True


class AuditEvent(BaseModel):
    """审计事件——只追加不修改。

    Attributes:
        event_id: 事件唯一 ID（UUID 十六进制）。
        timestamp: ISO 8601 时间戳（UTC）。
        user: 触发用户。
        department: 用户所属部门。
        agent: 涉及的 Agent 名。
        action: 动作类型（``proxy`` / ``budget_check`` / ``budget_exceeded``）。
        details: 附加详情（自由文本）。
        success: 是否成功。
    """

    event_id: str = Field(default_factory=lambda: secrets.token_hex(16))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    user: str
    department: str
    agent: str
    action: str
    details: str = ""
    success: bool = True


class DepartmentBudget(BaseModel):
    """部门预算——按 ``(department, agent)`` 维度设置月度预算。

    Attributes:
        department: 部门名。
        agent: Agent 名。
        monthly_budget: 月预算（USD）。
        used: 已用额度（USD）。
        reset_day: 每月重置日（1-31）。
    """

    department: str = Field(..., min_length=1)
    agent: str = Field(..., min_length=1)
    monthly_budget: float = Field(..., ge=0.0)
    used: float = Field(default=0.0, ge=0.0)
    reset_day: int = Field(default=1, ge=1, le=31)


# ── 代理网关 ────────────────────────────────────────────────────────


class AgentProxyGateway:
    """企业内网 Agent 代理网关 — 代理规则 + 审计 + 部门预算。

    线程安全：所有公开方法通过 ``threading.RLock`` 串行化；
    底层依赖 :class:`ConnectionPool`（WAL + busy_timeout）。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """初始化网关。

        Parameters
        ----------
        db_path : Path | None
            SQLite 数据库路径。``None`` 时使用统一默认路径。
        """
        if db_path is None:
            from maop.core.backends.db_utils import get_db_path
            db_path = get_db_path("auth")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        # RLock 允许同线程递归加锁（check_budget → consume_budget 组合调用）。
        self._lock = threading.RLock()
        self._init_db()

    # ── 建表 ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """初始化三张表（幂等）。"""
        conn = self._pool.acquire()
        try:
            # 代理规则表。
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_PROXY_RULE_TABLE} (
                    rule_id              TEXT PRIMARY KEY,
                    internal_path        TEXT NOT NULL,
                    external_agent       TEXT NOT NULL,
                    allowed_departments  TEXT NOT NULL DEFAULT '[]',
                    enabled              INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_PROXY_RULE_TABLE}_path "
                f"ON {_PROXY_RULE_TABLE}(internal_path)"
            )
            # 审计事件表——只追加不修改（无 UPDATE/DELETE 路径）。
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_AUDIT_EVENT_TABLE} (
                    event_id    TEXT PRIMARY KEY,
                    timestamp   TEXT NOT NULL,
                    user        TEXT NOT NULL DEFAULT '',
                    department  TEXT NOT NULL DEFAULT '',
                    agent       TEXT NOT NULL DEFAULT '',
                    action      TEXT NOT NULL,
                    details     TEXT NOT NULL DEFAULT '',
                    success     INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_AUDIT_EVENT_TABLE}_user "
                f"ON {_AUDIT_EVENT_TABLE}(user)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_AUDIT_EVENT_TABLE}_agent "
                f"ON {_AUDIT_EVENT_TABLE}(agent)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_AUDIT_EVENT_TABLE}_ts "
                f"ON {_AUDIT_EVENT_TABLE}(timestamp)"
            )
            # 部门预算表——按 (department, agent) 复合主键。
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_DEPT_BUDGET_TABLE} (
                    department    TEXT NOT NULL,
                    agent         TEXT NOT NULL,
                    monthly_budget REAL NOT NULL DEFAULT 0.0,
                    used          REAL NOT NULL DEFAULT 0.0,
                    reset_day     INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (department, agent)
                )
                """
            )
            conn.commit()
        finally:
            self._pool.release(conn)

    # ── 代理规则管理 ───────────────────────────────────────────────

    def add_proxy_rule(self, rule: ProxyRule) -> None:
        """添加代理规则（``rule_id`` 冲突时替换）。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {_PROXY_RULE_TABLE} "
                    f"(rule_id, internal_path, external_agent, "
                    f"allowed_departments, enabled) "
                    f"VALUES (?, ?, ?, ?, ?)",
                    (
                        rule.rule_id,
                        rule.internal_path,
                        rule.external_agent,
                        json.dumps(rule.allowed_departments, ensure_ascii=False),
                        1 if rule.enabled else 0,
                    ),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            logger.info(
                "[agent_proxy_gateway] 添加代理规则 id=%s path=%s -> agent=%s",
                rule.rule_id, rule.internal_path, rule.external_agent,
            )

    def remove_proxy_rule(self, rule_id: str) -> None:
        """移除代理规则（不存在时静默忽略）。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"DELETE FROM {_PROXY_RULE_TABLE} WHERE rule_id = ?",
                    (rule_id,),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            logger.info("[agent_proxy_gateway] 移除代理规则 id=%s", rule_id)

    def list_proxy_rules(self) -> list[ProxyRule]:
        """列出全部代理规则（按 internal_path 排序）。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                rows = conn.execute(
                    f"SELECT * FROM {_PROXY_RULE_TABLE} "
                    f"ORDER BY internal_path ASC"
                ).fetchall()
            finally:
                self._pool.release(conn)
            return [self._row_to_proxy_rule(r) for r in rows]

    def resolve_proxy(self, internal_path: str) -> ProxyRule | None:
        """解析内网路径到外网 Agent 规则。

        匹配策略：精确匹配 ``internal_path``，返回第一条启用的规则；
        未匹配或全部禁用时返回 ``None``。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT * FROM {_PROXY_RULE_TABLE} "
                    f"WHERE internal_path = ? AND enabled = 1 "
                    f"ORDER BY rule_id ASC LIMIT 1",
                    (internal_path,),
                ).fetchone()
            finally:
                self._pool.release(conn)
            if row is None:
                return None
            return self._row_to_proxy_rule(row)

    @staticmethod
    def _row_to_proxy_rule(row: Any) -> ProxyRule:
        """将 DB 行转换为 ProxyRule。"""
        try:
            deps = json.loads(row["allowed_departments"])
            if not isinstance(deps, list):
                deps = []
        except (json.JSONDecodeError, TypeError):
            deps = []
        return ProxyRule(
            rule_id=row["rule_id"],
            internal_path=row["internal_path"],
            external_agent=row["external_agent"],
            allowed_departments=deps,
            enabled=bool(row["enabled"]),
        )

    # ── 审计日志（只追加不修改）────────────────────────────────────

    def log_audit(self, event: AuditEvent) -> None:
        """记录审计事件（只 INSERT，不修改）。"""
        with self._lock:
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"INSERT INTO {_AUDIT_EVENT_TABLE} "
                    f"(event_id, timestamp, user, department, agent, "
                    f"action, details, success) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.timestamp,
                        event.user,
                        event.department,
                        event.agent,
                        event.action,
                        event.details,
                        1 if event.success else 0,
                    ),
                )
                conn.commit()
            finally:
                self._pool.release(conn)

    def query_audit(
        self, user: str = "", agent: str = "", limit: int = 100
    ) -> list[AuditEvent]:
        """查询审计日志（按时间倒序，支持 user / agent 过滤）。

        Parameters
        ----------
        user : str
            按用户过滤（空 = 不过滤）。
        agent : str
            按 Agent 过滤（空 = 不过滤）。
        limit : int
            返回条数上限（默认 100）。
        """
        if limit < 1:
            limit = 1
        with self._lock:
            conn = self._pool.acquire()
            try:
                clauses: list[str] = []
                params: list[Any] = []
                if user:
                    clauses.append("user = ?")
                    params.append(user)
                if agent:
                    clauses.append("agent = ?")
                    params.append(agent)
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                params.append(limit)
                rows = conn.execute(
                    f"SELECT * FROM {_AUDIT_EVENT_TABLE}{where} "
                    f"ORDER BY timestamp DESC LIMIT ?",
                    params,
                ).fetchall()
            finally:
                self._pool.release(conn)
            return [self._row_to_audit_event(r) for r in rows]

    @staticmethod
    def _row_to_audit_event(row: Any) -> AuditEvent:
        """将 DB 行转换为 AuditEvent。"""
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            user=row["user"],
            department=row["department"],
            agent=row["agent"],
            action=row["action"],
            details=row["details"],
            success=bool(row["success"]),
        )

    # ── 部门预算 ───────────────────────────────────────────────────

    def set_department_budget(self, budget: DepartmentBudget) -> None:
        """设置部门预算（按 ``(department, agent)`` 原子 upsert）。

        若该 ``(department, agent)`` 已有预算，则替换 monthly_budget /
        reset_day，但保留 used（避免重置已扣减额度）。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                # INSERT OR REPLACE 会删除旧行再插入新行——used 会被重置为 0。
                # 为保留 used，先查现有 used，再 upsert。
                existing = conn.execute(
                    f"SELECT used FROM {_DEPT_BUDGET_TABLE} "
                    f"WHERE department = ? AND agent = ?",
                    (budget.department, budget.agent),
                ).fetchone()
                used = existing["used"] if existing is not None else budget.used
                conn.execute(
                    f"INSERT OR REPLACE INTO {_DEPT_BUDGET_TABLE} "
                    f"(department, agent, monthly_budget, used, reset_day) "
                    f"VALUES (?, ?, ?, ?, ?)",
                    (
                        budget.department,
                        budget.agent,
                        budget.monthly_budget,
                        used,
                        budget.reset_day,
                    ),
                )
                conn.commit()
            finally:
                self._pool.release(conn)
            logger.info(
                "[agent_proxy_gateway] 设置部门预算 dept=%s agent=%s monthly=%.2f",
                budget.department, budget.agent, budget.monthly_budget,
            )

    def check_budget(self, department: str, agent: str, cost: float) -> bool:
        """检查部门预算是否足够扣减 ``cost``。

        Returns
        -------
        bool
            ``True`` 表示预算充足（或未设置预算=不限）；``False`` 表示超限。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                row = conn.execute(
                    f"SELECT monthly_budget, used FROM {_DEPT_BUDGET_TABLE} "
                    f"WHERE department = ? AND agent = ?",
                    (department, agent),
                ).fetchone()
            finally:
                self._pool.release(conn)
            if row is None:
                # 未设置预算 = 不限制。
                return True
            return (row["used"] + cost) <= (row["monthly_budget"] + 1e-9)

    def consume_budget(self, department: str, agent: str, cost: float) -> None:
        """扣减部门预算（原子 UPDATE）。

        若未设置预算则静默忽略；若扣减后 used 为负（cost 为负的回退）则截断为 0。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                conn.execute(
                    f"UPDATE {_DEPT_BUDGET_TABLE} "
                    f"SET used = MAX(0.0, used + ?) "
                    f"WHERE department = ? AND agent = ?",
                    (cost, department, agent),
                )
                conn.commit()
            finally:
                self._pool.release(conn)

    def get_budget_summary(self, department: str = "") -> list[DepartmentBudget]:
        """查询预算汇总。

        Parameters
        ----------
        department : str
            按部门过滤（空 = 全部部门）。
        """
        with self._lock:
            conn = self._pool.acquire()
            try:
                if department:
                    rows = conn.execute(
                        f"SELECT * FROM {_DEPT_BUDGET_TABLE} "
                        f"WHERE department = ? "
                        f"ORDER BY department ASC, agent ASC",
                        (department,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT * FROM {_DEPT_BUDGET_TABLE} "
                        f"ORDER BY department ASC, agent ASC"
                    ).fetchall()
            finally:
                self._pool.release(conn)
            return [self._row_to_budget(r) for r in rows]

    @staticmethod
    def _row_to_budget(row: Any) -> DepartmentBudget:
        """将 DB 行转换为 DepartmentBudget。"""
        return DepartmentBudget(
            department=row["department"],
            agent=row["agent"],
            monthly_budget=row["monthly_budget"],
            used=row["used"],
            reset_day=row["reset_day"],
        )

    # ── 生命周期 ───────────────────────────────────────────────────

    def close(self) -> None:
        """关闭底层连接池。"""
        self._pool.close_all()