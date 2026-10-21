"""配额变更历史与使用量采样的落库/查询。

``quota_history`` 此前只有读路径（``GET /api/quotas/history`` 因表不存在恒返回
空列表），本模块补上建表与写入，并新增 ``quota_usage_samples`` 采样表，供租户详情
里的「配额变更历史」与「使用量趋势（24 小时）」两个面板使用。

写入失败只记日志、不阻塞配额业务：配额本身已经改成功，回滚反而更糟。
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

from maop.core.backends.db_utils import sqlite_connect, unified_db_path

logger = logging.getLogger(__name__)

#: 采样只服务 24h 趋势图，留 7 天足够；历史按审计口径留 90 天。
SAMPLE_RETENTION_DAYS = 7
HISTORY_RETENTION_DAYS = 90

#: 参与「配额总量分布」聚合的租户配额字段（与前端 RESOURCES 的 quotaKey 对齐）。
OVERVIEW_QUOTA_FIELDS: tuple[str, ...] = (
    "max_agents", "max_users", "max_cpu_cores",
    "max_memory_mb", "max_storage_mb", "max_api_calls_per_day",
)

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS quota_history (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        resource TEXT NOT NULL,
        field TEXT NOT NULL,
        old_value TEXT DEFAULT '',
        new_value TEXT DEFAULT '',
        changed_by TEXT DEFAULT '',
        changed_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS quota_usage_samples (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        resource TEXT NOT NULL,
        period TEXT NOT NULL DEFAULT 'total',
        used INTEGER NOT NULL,
        sampled_at REAL NOT NULL
    )""",
    ("CREATE INDEX IF NOT EXISTS idx_quota_history_tenant "
     "ON quota_history(tenant_id, changed_at)"),
    ("CREATE INDEX IF NOT EXISTS idx_quota_samples_tenant "
     "ON quota_usage_samples(tenant_id, sampled_at)"),
)


def _hour_label(bucket_ts: float) -> str:
    return time.strftime("%m-%d %H:00", time.gmtime(bucket_ts))


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA:
        conn.execute(stmt)


def record_quota_change(
    tenant_id: str,
    resource: str,
    field: str,
    old_value: Any,
    new_value: Any,
    changed_by: str = "",
) -> None:
    """写入一条配额变更历史（值统一转成字符串存库）。"""
    try:
        with sqlite_connect(unified_db_path()) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO quota_history "
                "(id, tenant_id, resource, field, old_value, new_value, changed_by, changed_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"qh-{uuid.uuid4().hex[:8]}", tenant_id, resource, field,
                    "" if old_value is None else str(old_value),
                    "" if new_value is None else str(new_value),
                    changed_by, time.time(),
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("[quotas.audit] history write failed (%s/%s/%s): %s",
                       tenant_id, resource, field, exc)


def record_usage_sample(
    tenant_id: str,
    resource: str,
    used: int,
    period: str = "total",
) -> None:
    """记录一次使用量采样，供 24 小时趋势图聚合。"""
    try:
        with sqlite_connect(unified_db_path()) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO quota_usage_samples "
                "(id, tenant_id, resource, period, used, sampled_at) VALUES (?,?,?,?,?,?)",
                (
                    f"qs-{uuid.uuid4().hex[:8]}", tenant_id, resource, period,
                    int(used), time.time(),
                ),
            )
    except sqlite3.Error as exc:
        logger.warning("[quotas.audit] usage sample write failed (%s/%s): %s",
                       tenant_id, resource, exc)


def prune_audit(
    sample_days: int = SAMPLE_RETENTION_DAYS,
    history_days: int = HISTORY_RETENTION_DAYS,
) -> dict[str, int]:
    """删除过期的使用量采样与配额历史（由 ``prune`` 维护动作调用）。"""
    now = time.time()
    deleted = {"samples": 0, "history": 0}
    try:
        with sqlite_connect(unified_db_path()) as conn:
            ensure_schema(conn)
            deleted["samples"] = conn.execute(
                "DELETE FROM quota_usage_samples WHERE sampled_at < ?",
                (now - sample_days * 86400.0,),
            ).rowcount
            deleted["history"] = conn.execute(
                "DELETE FROM quota_history WHERE changed_at < ?",
                (now - history_days * 86400.0,),
            ).rowcount
    except sqlite3.Error as exc:
        logger.warning("[quotas.audit] prune failed: %s", exc)
    return deleted


def usage_trend(tenant_id: str, hours: int = 24) -> dict[str, Any]:
    """把采样按小时分桶，返回 ``{labels, series:[{label, data}]}``。

    每个桶取该资源桶内最后一次采样；没有采样的桶是 ``None``（折线断开），
    不用 0 填充 —— 0 会被读成「用量掉到零」，那是假信息。
    """
    bucket_s = 3600.0
    now_bucket = float(int(time.time() // bucket_s) * bucket_s)
    buckets = [now_bucket - (hours - 1 - i) * bucket_s for i in range(hours)]
    since = buckets[0] - bucket_s

    last_by_resource: dict[str, dict[float, int]] = {}
    try:
        with sqlite_connect(unified_db_path()) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                "SELECT resource, used, sampled_at FROM quota_usage_samples "
                "WHERE tenant_id = ? AND sampled_at >= ? ORDER BY sampled_at ASC",
                (tenant_id, since),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[quotas.audit] trend query failed for %s: %s", tenant_id, exc)
        rows = []

    for resource, used, sampled_at in rows:
        key = float(int(sampled_at // bucket_s) * bucket_s)
        last_by_resource.setdefault(str(resource), {})[key] = int(used)

    series = [
        {
            "label": resource,
            "data": [by_bucket.get(b) for b in buckets],
        }
        for resource, by_bucket in sorted(last_by_resource.items())
    ]
    return {
        "labels": [_hour_label(b) for b in buckets],
        "series": series,
        "hours": hours,
        "sampled": any(s["data"] for s in series),
    }


def quota_overview(tenants: list[dict[str, Any]]) -> dict[str, Any]:
    """按租户汇总配额上限之和，得到「配额总量分布」。

    跨资源单位不可比（agents 与 storage_mb 相加没有物理意义），因此这个聚合
    只用于份额对比，绝对值不代表可比容量 —— 前端把它画成占比饼图正是这个用途。
    """
    labels: list[str] = []
    values: list[int] = []
    for tenant in tenants:
        quota = tenant.get("quota") or {}
        total = 0
        for field in OVERVIEW_QUOTA_FIELDS:
            raw = quota.get(field)
            if isinstance(raw, int) and raw > 0:
                total += raw
        labels.append(str(tenant.get("name") or tenant.get("tenant_id") or "—"))
        values.append(total)
    return {
        "allocation": {"labels": labels, "values": values},
        "tenant_count": len(tenants),
    }
