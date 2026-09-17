"""Billing & Quota & License service layer.

Encapsulates business logic for the following routers:

- ``quotas.py``            — Enterprise tenant quota management (QuotaManager)
- ``quota.py``             — Agent quota bucket management (QuotaBucket)
- ``billing_abstraction.py`` — Billing engine (BillingEngine)
- ``licenses.py``          — Enterprise license management (LicenseManager)

Extracted from the router layer so routers only do parameter parsing +
auth + feature-gate check + service call + response formatting + error
handling.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked without an HTTP context.

Singletons use double-checked locking, matching the original router
style. ``_set_*`` functions are provided for test injection (isolated DB).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from maop.config.edition import FeatureFlag, has_feature
from maop.core.backends.db_utils import unified_db_path

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# §1  Enterprise Quota Manager — 租户配额管理 (quotas.py)
# ════════════════════════════════════════════════════════════════════════════

_quota_manager: Any = None
_quota_manager_lock = threading.Lock()


def _get_quota_manager() -> Any:
    """惰性初始化 QuotaManager 单例(共享 unified maop.db)。"""
    global _quota_manager
    if _quota_manager is not None:
        return _quota_manager
    with _quota_manager_lock:
        if _quota_manager is not None:  # double-checked locking
            return _quota_manager
        from maop.enterprise.quota import QuotaManager
        _quota_manager = QuotaManager(unified_db_path())
    return _quota_manager


def _set_quota_manager(mgr: Any) -> None:
    """供测试注入自定义 QuotaManager（隔离 DB）。"""
    global _quota_manager
    with _quota_manager_lock:
        _quota_manager = mgr


def require_tenant_isolation() -> None:
    """企业版特性守卫: Personal 版返回 404.

    Raises
    ------
    HTTPException
        404 if TENANT_ISOLATION feature is not available.
    """
    if not has_feature(FeatureFlag.TENANT_ISOLATION):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail="tenant isolation not available in this edition",
        )


def _quota_history_table_exists(conn: sqlite3.Connection) -> bool:
    """检查 ``quota_history`` 表是否存在于当前数据库.

    使用 ``sqlite_master`` 查询而非 ``PRAGMA table_info`` 以避免在
    事务中产生隐式提交. 失败时返回 ``False`` (fail-open 语义).
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='quota_history' LIMIT 1",
        ).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        logger.warning("[quotas.history] table existence check failed: %s", exc)
        return False


def list_quota_history(
    tenant_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出配额变更历史记录.

    从 unified SQLite 数据库的 ``quota_history`` 表读取. 若该表不存在
    (未启用历史记录或全新数据库), 返回空列表而非 404, 这样前端
    可以平滑降级为 EmptyState.

    Returns
    -------
    dict
        ``{"history": [...], "count": int}``
    """
    from maop.core.backends.db_utils import sqlite_connect

    db_path = unified_db_path()
    try:
        with sqlite_connect(db_path) as conn:
            if not _quota_history_table_exists(conn):
                return {"history": [], "count": 0}
            if tenant_id is not None:
                rows = conn.execute(
                    "SELECT id, tenant_id, resource, field, "
                    "old_value, new_value, changed_by, changed_at "
                    "FROM quota_history WHERE tenant_id = ? "
                    "ORDER BY changed_at DESC LIMIT ? OFFSET ?",
                    (tenant_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, tenant_id, resource, field, "
                    "old_value, new_value, changed_by, changed_at "
                    "FROM quota_history "
                    "ORDER BY changed_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[quotas.history] query failed: %s", exc)
        return {"history": [], "count": 0}
    history: list[dict[str, Any]] = []
    for r in rows:
        history.append({
            "id": r[0],
            "tenant_id": r[1],
            "resource": r[2],
            "field": r[3],
            "old_value": r[4],
            "new_value": r[5],
            "changed_by": r[6],
            "changed_at": r[7],
        })
    return {"history": history, "count": len(history)}


def resolve_quota_alert(alert_id: str) -> bool:
    """标记告警为已解决。返回是否成功。"""
    mgr = _get_quota_manager()
    return mgr.resolve_alert(alert_id)


def list_quota_alerts(
    tenant_id: str,
    resolved: bool | None = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列出配额告警，返回 model_dump 后的 dict 列表。"""
    mgr = _get_quota_manager()
    alerts = mgr.list_alerts(tenant_id, resolved=resolved, limit=limit, offset=offset)
    return [a.model_dump() for a in alerts]


def list_quota_usage(tenant_id: str) -> list[dict[str, Any]]:
    """列出租户所有已设配额资源的使用量，返回 model_dump 后的 dict 列表。"""
    mgr = _get_quota_manager()
    usages = mgr.list_usage(tenant_id)
    return [u.model_dump() for u in usages]


def set_quota(
    tenant_id: str,
    resource: str,
    hard_limit: int,
    soft_limit: int = 0,
    period: str = "total",
) -> dict[str, Any]:
    """设置或更新配额，返回 model_dump 后的 quota dict。"""
    mgr = _get_quota_manager()
    quota = mgr.set_quota(
        tenant_id, resource, hard_limit,
        soft_limit=soft_limit, period=period,
    )
    return quota.model_dump()


def list_quotas(tenant_id: str) -> list[dict[str, Any]]:
    """列出租户的所有配额，返回 model_dump 后的 dict 列表。"""
    mgr = _get_quota_manager()
    quotas = mgr.list_quotas(tenant_id)
    return [q.model_dump() for q in quotas]


def get_quota(tenant_id: str, resource: str) -> dict[str, Any] | None:
    """查询单个配额。返回 model_dump 后的 dict，或 None（未找到）。"""
    mgr = _get_quota_manager()
    quota = mgr.get_quota(tenant_id, resource)
    if quota is None:
        return None
    return quota.model_dump()


def update_quota(
    tenant_id: str,
    resource: str,
    hard_limit: int | None = None,
    soft_limit: int | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    """部分更新配额，返回 model_dump 后的 quota dict。

    Raises
    ------
    KeyError
        配额不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_quota_manager()
    quota = mgr.update_quota(
        tenant_id, resource,
        hard_limit=hard_limit,
        soft_limit=soft_limit,
        period=period,
    )
    return quota.model_dump()


def delete_quota(tenant_id: str, resource: str) -> bool:
    """删除配额。返回是否成功删除。"""
    mgr = _get_quota_manager()
    return mgr.delete_quota(tenant_id, resource)


def get_quota_usage(tenant_id: str, resource: str) -> dict[str, Any]:
    """查询单个资源的使用量，返回 model_dump 后的 usage dict。"""
    mgr = _get_quota_manager()
    usage = mgr.get_usage(tenant_id, resource)
    return usage.model_dump()


def update_quota_usage(
    tenant_id: str,
    resource: str,
    amount: int,
    period: str = "total",
) -> int:
    """增量更新使用量(amount 可正可负)。返回更新后的 used 值。"""
    mgr = _get_quota_manager()
    return mgr.update_usage(tenant_id, resource, amount, period=period)


def set_quota_usage(
    tenant_id: str,
    resource: str,
    value: int,
    period: str = "total",
) -> int:
    """绝对设置使用量。返回设置后的 used 值。"""
    mgr = _get_quota_manager()
    return mgr.set_usage(tenant_id, resource, value, period=period)


def reset_quota_usage(tenant_id: str, resource: str) -> bool:
    """重置单个资源的使用量。返回是否成功删除。"""
    mgr = _get_quota_manager()
    return mgr.reset_usage(tenant_id, resource)


def check_quota(tenant_id: str, resource: str, amount: int = 1) -> dict[str, Any]:
    """检查是否允许消耗 amount 单位的 resource(不消费)。返回 model_dump 后的 result。"""
    mgr = _get_quota_manager()
    result = mgr.check_quota(tenant_id, resource, amount=amount)
    return result.model_dump()


def consume_quota(tenant_id: str, resource: str, amount: int = 1) -> dict[str, Any]:
    """检查并消费 amount 单位的 resource。返回 model_dump 后的 result。"""
    mgr = _get_quota_manager()
    result = mgr.consume(tenant_id, resource, amount=amount)
    return result.model_dump()


# ════════════════════════════════════════════════════════════════════════════
# §2  Agent Quota Bucket — Agent 额度分桶管理 (quota.py)
# ════════════════════════════════════════════════════════════════════════════

_quota_bucket: Any = None
_quota_bucket_lock = threading.Lock()


def _get_quota_bucket() -> Any:
    """惰性初始化 QuotaBucket 单例（双重检查锁定）。"""
    global _quota_bucket
    if _quota_bucket is not None:
        return _quota_bucket
    with _quota_bucket_lock:
        if _quota_bucket is None:  # double-checked locking
            from maop.core.agent.billing.quota_bucket import QuotaBucket
            _quota_bucket = QuotaBucket()
    return _quota_bucket


def _set_quota_bucket(bucket: Any) -> None:
    """供测试注入自定义 QuotaBucket（隔离 DB）。"""
    global _quota_bucket
    with _quota_bucket_lock:
        _quota_bucket = bucket


def get_agent_quota(agent_name: str) -> dict[str, Any]:
    """获取 Agent 额度配置，返回 model_dump(mode="json") 后的 dict。"""
    bucket = _get_quota_bucket()
    entry = bucket.get_quota(agent_name)
    return entry.model_dump(mode="json")


def set_agent_quota(agent_name: str, entry: Any) -> None:
    """设置 Agent 额度配置。``entry`` 为 QuotaEntry 实例。"""
    bucket = _get_quota_bucket()
    bucket.set_quota(agent_name, entry)


def consume_agent_quota(agent_name: str, amount: int) -> dict[str, Any]:
    """消耗 Agent 额度，返回 model_dump(mode="json") 后的 result dict。"""
    bucket = _get_quota_bucket()
    result = bucket.consume(agent_name, amount)
    return result.model_dump(mode="json")


def refund_agent_quota(agent_name: str, amount: int, bucket_name: str = "auto") -> None:
    """退还 Agent 额度。

    Raises
    ------
    ValueError
        无效桶名时抛出（由 router 映射为 400）。
    """
    bucket = _get_quota_bucket()
    bucket.refund(agent_name, amount, bucket_name)


def get_agent_remaining(agent_name: str) -> dict[str, int]:
    """获取 Agent 剩余额度。返回 ``{"free": .., "prepaid": .., "postpaid": ..}``。"""
    bucket = _get_quota_bucket()
    return bucket.get_remaining(agent_name)


def reset_agent_quota(agent_name: str, bucket_name: str = "all") -> None:
    """重置 Agent 额度使用量。

    Raises
    ------
    ValueError
        无效桶名时抛出（由 router 映射为 400）。
    """
    bucket = _get_quota_bucket()
    bucket.reset_quota(agent_name, bucket_name)


# ════════════════════════════════════════════════════════════════════════════
# §3  Billing Engine — 计费抽象引擎 (billing_abstraction.py)
# ════════════════════════════════════════════════════════════════════════════

_billing_engine: Any = None
_billing_engine_lock = threading.Lock()


def _get_billing_engine() -> Any:
    """惰性初始化 BillingEngine 单例（双重检查锁定）。"""
    global _billing_engine
    if _billing_engine is not None:
        return _billing_engine
    with _billing_engine_lock:
        if _billing_engine is None:  # double-checked locking
            from maop.core.agent.billing.billing_abstraction import BillingEngine
            _billing_engine = BillingEngine()
    return _billing_engine


def _set_billing_engine(engine: Any) -> None:
    """供测试注入自定义 BillingEngine（隔离 DB）。"""
    global _billing_engine
    with _billing_engine_lock:
        _billing_engine = engine


def charge_agent(
    agent_name: str,
    tokens: int = 0,
    calls: int = 1,
    session_id: str = "",
    model: str = "",
) -> dict[str, Any]:
    """对 Agent 调用进行计费。

    Returns
    -------
    dict
        ``{"success": bool, "record": {...} | None, "error": str}``
    """
    engine = _get_billing_engine()
    result = engine.charge(
        agent_name,
        tokens=tokens,
        calls=calls,
        session_id=session_id,
        model=model,
    )
    return {
        "success": result.success,
        "record": result.record.model_dump(mode="json") if result.record else None,
        "error": result.error,
    }


def estimate_agent_cost(
    agent_name: str,
    tokens: int = 0,
    calls: int = 1,
) -> float:
    """估算成本（不实际扣减）。返回估算的 USD 成本。"""
    engine = _get_billing_engine()
    return engine.estimate_cost(agent_name, tokens=tokens, calls=calls)


def get_agent_billing_summary(agent_name: str) -> dict[str, Any]:
    """获取 Agent 计费摘要。"""
    engine = _get_billing_engine()
    return engine.get_agent_billing_summary(agent_name)


def get_billing_records(
    agent_name: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """获取计费记录列表，返回 model_dump(mode="json") 后的 dict 列表。"""
    engine = _get_billing_engine()
    records = engine.get_billing_records(agent_name=agent_name, limit=limit)
    return [r.model_dump(mode="json") for r in records]


# ════════════════════════════════════════════════════════════════════════════
# §4  License Manager — 企业许可证管理 (licenses.py)
# ════════════════════════════════════════════════════════════════════════════

_license_manager: Any = None
_license_manager_lock = threading.Lock()


def _get_license_manager() -> Any:
    """Lazy-init the LicenseManager singleton.

    Uses an in-memory Ed25519 keypair by default (auto-generated on first
    call). For production issuance, set ``MAOP_LICENSE_MGR_PRIVATE_KEY``
    to point to the real signing key path.
    """
    global _license_manager
    if _license_manager is not None:
        return _license_manager
    with _license_manager_lock:
        if _license_manager is not None:  # double-checked locking
            return _license_manager
        # P2-17: LicenseManager 为 enterprise 可选依赖，延迟加载
        from maop.enterprise.license_manager import LicenseManager

        priv_path_env = os.getenv("MAOP_LICENSE_MGR_PRIVATE_KEY", "").strip()
        priv_path = Path(priv_path_env) if priv_path_env else None
        _license_manager = LicenseManager(private_key_path=priv_path)
    return _license_manager


def _set_license_manager(mgr: Any) -> None:
    """供测试注入自定义 LicenseManager（隔离 DB / 密钥）。"""
    global _license_manager
    with _license_manager_lock:
        _license_manager = mgr


def require_license_management() -> None:
    """Return 404 if the LICENSE_MANAGEMENT feature is not available.

    Raises
    ------
    HTTPException
        404 if LICENSE_MANAGEMENT feature is not available.
    """
    if not has_feature(FeatureFlag.LICENSE_MANAGEMENT):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail="license management not available in this edition",
        )


def list_licenses(status: str = "") -> list[dict[str, Any]]:
    """List all licenses, optionally filtered by status. Returns model_dump list."""
    mgr = _get_license_manager()
    licenses = mgr.list_licenses(status=status)
    return [lic.model_dump() for lic in licenses]


def create_license(
    customer: str,
    expires_at: str,
    max_users: int | None = None,
    fingerprint: str | None = None,
    features: list[str] | None = None,
    issued_by: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Issue a new license. Returns model_dump of the created record."""
    mgr = _get_license_manager()
    record = mgr.create_license(
        customer=customer,
        expires_at=expires_at,
        max_users=max_users,
        fingerprint=fingerprint,
        features=features,
        issued_by=issued_by,
        notes=notes,
    )
    return record.model_dump()


def validate_license(license_key: str) -> dict[str, Any]:
    """Validate a license key (signature + expiry + revocation)."""
    mgr = _get_license_manager()
    return mgr.validate_license(license_key)


def list_license_audit_logs(
    license_id: str = "",
    action: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List all license audit logs with optional filters. Returns model_dump list."""
    mgr = _get_license_manager()
    entries = mgr.get_audit_logs(license_id=license_id, action=action, limit=limit, offset=offset)
    return [e.model_dump() for e in entries]


def get_license(license_id: str) -> dict[str, Any]:
    """Get a single license by ID. Returns model_dump.

    Raises
    ------
    LicenseNotFoundError
        许可证不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_license_manager()
    record = mgr.get_license(license_id)
    return record.model_dump()


def update_license(
    license_id: str,
    customer: str | None = None,
    max_users: int | None = None,
    fingerprint: str | None = None,
    features: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update editable license metadata. Returns model_dump.

    Raises
    ------
    LicenseNotFoundError
        许可证不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_license_manager()
    record = mgr.update_license(
        license_id,
        customer=customer,
        max_users=max_users,
        fingerprint=fingerprint,
        features=features,
        notes=notes,
    )
    return record.model_dump()


def renew_license(license_id: str, new_expires_at: str, actor: str = "") -> dict[str, Any]:
    """Renew a license: re-sign with a new expiry date. Returns model_dump.

    Raises
    ------
    LicenseNotFoundError
        许可证不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_license_manager()
    record = mgr.renew_license(license_id, new_expires_at=new_expires_at, actor=actor)
    return record.model_dump()


def revoke_license(license_id: str, reason: str = "", actor: str = "") -> dict[str, Any]:
    """Revoke a license. Returns model_dump.

    Raises
    ------
    LicenseNotFoundError
        许可证不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_license_manager()
    record = mgr.revoke_license(license_id, reason=reason, actor=actor)
    return record.model_dump()


def delete_license(license_id: str) -> bool:
    """Delete a license and its audit logs. Returns True on success.

    Raises
    ------
    LicenseNotFoundError
        许可证不存在时抛出（由 router 映射为 404）。
    """
    mgr = _get_license_manager()
    return mgr.delete_license(license_id)


def get_license_audit(
    license_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get the audit log for a specific license. Returns model_dump list."""
    mgr = _get_license_manager()
    entries = mgr.get_audit_logs(license_id=license_id, limit=limit, offset=offset)
    return [e.model_dump() for e in entries]