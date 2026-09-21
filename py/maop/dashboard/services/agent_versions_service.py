"""Agent 版本管理 / 灰度发布 Service 层.

框架无关（不导入 FastAPI），函数接收显式参数，返回普通 dict/object。
从 ``agent_versions.py`` router 提取的业务逻辑：版本 CRUD + 激活 +
回滚 + 灰度 + 指标。

本模块复用 ``agent_versions_helpers`` 中的 Pydantic 模型、常量、
schema 初始化和行↔dict 转换函数，避免重复。Service 函数负责数据库
编排与业务规则校验，router 仅做参数解析 + 权限检查 + service 调用
+ 响应格式化 + 错误处理。

异常约定
--------
Service 函数不抛 HTTPException，而是抛语义化异常：
- ``VersionNotFound`` — 版本不存在（router 转 404）
- ``VersionConflict`` — 业务规则冲突，如激活已删除版本（router 转 400）
- ``CanaryConflict`` — 灰度配置冲突，如已存在 active 灰度（router 转 409）
- ``ValueError`` — 输入校验失败（router 转 400）

IDOR 防护由 router 侧的 ``_check_version_access`` 处理（需 Request），
service 函数接收已校验的 ``is_admin`` / ``user_id`` 标志。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.dashboard.routers.agent_versions_helpers import (
    _AGENT_VERSION_COLUMNS,
    _CANARY_COLUMNS,
    _VALID_VERSION_STATUSES,
    _canary_row_to_dict,
    _ensure_schema,
    _get_version_row,
    _new_canary_id,
    _new_version_id,
    _version_row_to_dict,
)

logger = logging.getLogger(__name__)


# ── 语义化异常 ─────────────────────────────────────────────────────


class VersionNotFound(Exception):
    """版本不存在。"""


class VersionConflict(Exception):
    """版本业务规则冲突（如激活已删除版本、删除 active 版本）。"""


class CanaryConflict(Exception):
    """灰度配置冲突（如已存在 active 灰度）。"""


# ── Service 函数 ───────────────────────────────────────────────────


def create_version(
    *,
    agent_id: str,
    version_number: str,
    config_snapshot: dict[str, Any],
    changelog: str,
    user_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """创建新版本，初始状态为 draft。

    Returns
    -------
    dict
        ``{"version_id": str}``
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")
    version_id = _new_version_id()
    now = time.time()
    config_json = json.dumps(config_snapshot, ensure_ascii=False)

    with sqlite_connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO agent_versions
                (version_id, agent_id, version_number, config_snapshot,
                 changelog, status, created_by, tenant_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                version_id,
                agent_id,
                version_number,
                config_json,
                changelog,
                user_id,
                tenant_id,
                now,
                now,
            ),
        )

    return {"version_id": version_id}


def list_versions(
    *,
    agent_id: str,
    status_filter: str,
    page: int,
    page_size: int,
    is_admin: bool,
    user_id: str,
) -> dict[str, Any]:
    """列出版本 — 非 admin 用户只能看自己创建的版本（IDOR 防护）。

    Returns
    -------
    dict
        ``{"data": [...], "meta": {"total", "page", "page_size"}}``

    Raises
    ------
    ValueError
        当 status_filter 非空且不在 _VALID_VERSION_STATUSES 中。
    """
    if status_filter and status_filter not in _VALID_VERSION_STATUSES:
        raise ValueError(
            f"Invalid status; expected one of {_VALID_VERSION_STATUSES}"
        )

    _ensure_schema()
    db_path = get_db_path("agent_versions")

    where_clauses: list[str] = []
    params: list[Any] = []

    # 非 admin 强制按创建者过滤 — IDOR 防护
    if not is_admin:
        where_clauses.append("created_by = ?")
        params.append(user_id)

    if agent_id:
        where_clauses.append("agent_id = ?")
        params.append(agent_id)
    if status_filter:
        where_clauses.append("status = ?")
        params.append(status_filter)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * page_size

    with sqlite_connect(str(db_path)) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM agent_versions{where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT {_AGENT_VERSION_COLUMNS} FROM agent_versions{where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    return {
        "data": [_version_row_to_dict(r) for r in rows],
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


def get_version(version_id: str, *, is_admin: bool, user_id: str) -> dict[str, Any]:
    """获取版本详情 — IDOR 防护。

    Raises
    ------
    VersionNotFound
        版本不存在，或非 admin 且非本人创建（不泄露存在性）。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)

    if row is None:
        raise VersionNotFound("Version not found")
    item = _version_row_to_dict(row)
    if not is_admin and user_id and item.get("created_by", "") != user_id:
        raise VersionNotFound("Version not found")
    return item


def update_version(
    version_id: str,
    *,
    version_number: str | None,
    config_snapshot: dict[str, Any] | None,
    changelog: str | None,
) -> dict[str, Any]:
    """更新版本信息。

    Raises
    ------
    VersionNotFound
        版本不存在。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")

        updates: list[str] = []
        params: list[Any] = []
        if version_number is not None:
            updates.append("version_number = ?")
            params.append(version_number)
        if config_snapshot is not None:
            updates.append("config_snapshot = ?")
            params.append(json.dumps(config_snapshot, ensure_ascii=False))
        if changelog is not None:
            updates.append("changelog = ?")
            params.append(changelog)

        if not updates:
            item = _version_row_to_dict(row)
            return item

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(version_id)
        conn.execute(
            f"UPDATE agent_versions SET {', '.join(updates)} WHERE version_id = ?",
            params,
        )
        row = _get_version_row(conn, version_id)
        if row is None:  # 写入后立即回读，理论上非空；守卫与函数开头一致
            raise VersionNotFound("Version not found")
        item = _version_row_to_dict(row)

    return item


def delete_version(version_id: str) -> None:
    """软删除版本 — 状态置为 deleted。

    Raises
    ------
    VersionNotFound
        版本不存在。
    VersionConflict
        版本为 active 状态，不允许直接删除。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")
        existing = _version_row_to_dict(row)
        # active 版本不允许直接删除
        if existing["status"] == "active":
            raise VersionConflict(
                "Cannot delete an active version; retire or rollback first"
            )
        now = time.time()
        conn.execute(
            "UPDATE agent_versions SET status = 'deleted', updated_at = ? WHERE version_id = ?",
            (now, version_id),
        )


def activate_version(version_id: str) -> dict[str, Any]:
    """激活版本 — 全量切换。同一 agent 的其他 active 版本自动转为 retired。

    Returns
    -------
    dict
        激活后的版本详情。

    Raises
    ------
    VersionNotFound
        版本不存在。
    VersionConflict
        版本为 deleted 状态，不允许激活。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")
        target = _version_row_to_dict(row)
        if target["status"] == "deleted":
            raise VersionConflict("Cannot activate a deleted version")
        now = time.time()
        # 将同 agent 的其他 active 版本转为 retired
        conn.execute(
            """
            UPDATE agent_versions
            SET status = 'retired', retired_at = ?, updated_at = ?
            WHERE agent_id = ? AND status = 'active' AND version_id != ?
            """,
            (now, now, target["agent_id"], version_id),
        )
        # 激活目标版本
        conn.execute(
            """
            UPDATE agent_versions
            SET status = 'active', activated_at = ?, updated_at = ?
            WHERE version_id = ?
            """,
            (now, now, version_id),
        )
        # 暂停该版本关联的灰度 (全量切换后灰度无意义)
        conn.execute(
            """
            UPDATE agent_version_canaries
            SET status = 'completed', ended_at = ?
            WHERE version_id = ? AND status = 'active'
            """,
            (now, version_id),
        )
        row = _get_version_row(conn, version_id)
        if row is None:  # 写入后立即回读，理论上非空；守卫与函数开头一致
            raise VersionNotFound("Version not found")
        item = _version_row_to_dict(row)

    return item


def rollback_version(
    version_id: str, *, target_version_id: str, reason: str
) -> dict[str, Any]:
    """回滚到指定版本 — 将 version_id 置为 retired, target_version_id 置为 active。

    Returns
    -------
    dict
        ``{"data": <target_version_dict>, "meta": {"rolled_back_from", "rolled_back_to", "reason"}}``

    Raises
    ------
    VersionNotFound
        当前版本或目标版本不存在。
    VersionConflict
        目标版本属于不同 agent，或目标版本为 deleted 状态。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        # 校验当前版本存在
        current_row = _get_version_row(conn, version_id)
        if current_row is None:
            raise VersionNotFound("Version not found")
        current = _version_row_to_dict(current_row)
        # 校验目标版本存在且属于同一 agent
        target_row = _get_version_row(conn, target_version_id)
        if target_row is None:
            raise VersionNotFound("Target version not found")
        target = _version_row_to_dict(target_row)
        if target["agent_id"] != current["agent_id"]:
            raise VersionConflict(
                "Cannot rollback to a version of a different agent"
            )
        if target["status"] == "deleted":
            raise VersionConflict("Cannot rollback to a deleted version")
        now = time.time()
        # 当前 active 版本 → retired
        conn.execute(
            """
            UPDATE agent_versions
            SET status = 'retired', retired_at = ?, updated_at = ?
            WHERE agent_id = ? AND status = 'active'
            """,
            (now, now, current["agent_id"]),
        )
        # 目标版本 → active
        conn.execute(
            """
            UPDATE agent_versions
            SET status = 'active', activated_at = ?, updated_at = ?
            WHERE version_id = ?
            """,
            (now, now, target_version_id),
        )
        row = _get_version_row(conn, target_version_id)
        if row is None:  # 写入后立即回读，理论上非空；守卫与函数开头一致
            raise VersionNotFound("Version not found")
        item = _version_row_to_dict(row)

    return {
        "data": item,
        "meta": {
            "rolled_back_from": version_id,
            "rolled_back_to": target_version_id,
            "reason": reason,
        },
    }


def configure_canary(
    version_id: str,
    *,
    percentage: int,
    target_user_groups: list[str],
    strategy: str,
    duration_seconds: int | None,
    user_id: str,
) -> dict[str, Any]:
    """配置灰度发布 — 同一版本同时只允许一个 active 灰度。

    Returns
    -------
    dict
        灰度配置详情。

    Raises
    ------
    VersionNotFound
        版本不存在。
    VersionConflict
        版本为 deleted 状态。
    CanaryConflict
        已存在 active 灰度。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")
        target = _version_row_to_dict(row)
        if target["status"] == "deleted":
            raise VersionConflict("Cannot configure canary for a deleted version")
        # 检查是否已有 active 灰度
        existing = conn.execute(
            f"""
            SELECT {_CANARY_COLUMNS} FROM agent_version_canaries
            WHERE version_id = ? AND status = 'active'
            """,
            (version_id,),
        ).fetchone()
        if existing is not None:
            raise CanaryConflict("An active canary already exists for this version")
        canary_id = _new_canary_id()
        now = time.time()
        groups_json = json.dumps(target_user_groups, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO agent_version_canaries
                (canary_id, version_id, percentage, target_user_groups,
                 strategy, status, duration_seconds, started_at, created_by)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                canary_id,
                version_id,
                percentage,
                groups_json,
                strategy,
                duration_seconds,
                now,
                user_id,
            ),
        )
        canary_row = conn.execute(
            f"SELECT {_CANARY_COLUMNS} FROM agent_version_canaries WHERE canary_id = ?",
            (canary_id,),
        ).fetchone()
        canary = _canary_row_to_dict(canary_row)

    return canary


def get_canary(version_id: str) -> dict[str, Any]:
    """查询灰度发布状态 — 返回该版本所有灰度记录。

    Raises
    ------
    VersionNotFound
        版本不存在（IDOR 校验由 router 侧完成，此处仅校验存在性）。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")
        canary_rows = conn.execute(
            f"SELECT {_CANARY_COLUMNS} FROM agent_version_canaries "
            f"WHERE version_id = ? ORDER BY started_at DESC",
            (version_id,),
        ).fetchall()

    return {
        "data": [_canary_row_to_dict(r) for r in canary_rows],
        "meta": {"count": len(canary_rows)},
    }


def get_version_metrics(version_id: str) -> dict[str, Any]:
    """查询版本指标 — 返回该版本与同 agent 当前 active 版本的对比指标。

    个人版无真实指标数据时优雅降级返回零值。

    Raises
    ------
    VersionNotFound
        版本不存在。
    """
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise VersionNotFound("Version not found")
        item = _version_row_to_dict(row)
        agent_id = item["agent_id"]
        # 查询同 agent 的 active 版本用于对比
        active_row = conn.execute(
            f"""
            SELECT {_AGENT_VERSION_COLUMNS} FROM agent_versions
            WHERE agent_id = ? AND status = 'active' AND version_id != ?
            ORDER BY activated_at DESC LIMIT 1
            """,
            (agent_id, version_id),
        ).fetchone()
        active_item = _version_row_to_dict(active_row) if active_row else None
        # 查询该版本的灰度配置
        canary_row = conn.execute(
            f"""
            SELECT {_CANARY_COLUMNS} FROM agent_version_canaries
            WHERE version_id = ? AND status = 'active'
            ORDER BY started_at DESC LIMIT 1
            """,
            (version_id,),
        ).fetchone()
        canary = _canary_row_to_dict(canary_row) if canary_row else None

    # 指标计算 — 个人版无真实埋点, 返回结构化零值以便前端渲染骨架.
    # 企业版可通过后续 PRD 接入 Prometheus / CostTracker 真实数据.
    metrics = {
        "version_id": version_id,
        "agent_id": agent_id,
        "version_number": item["version_number"],
        "status": item["status"],
        "success_rate_pct": 0.0,
        "avg_latency_ms": 0.0,
        "error_rate_pct": 0.0,
        "total_requests": 0,
        "canary": canary,
        "comparison": {
            "active_version_id": active_item["version_id"] if active_item else None,
            "active_version_number": active_item["version_number"] if active_item else None,
            "delta_success_rate_pct": 0.0,
            "delta_avg_latency_ms": 0.0,
            "delta_error_rate_pct": 0.0,
        },
        "note": "Metrics require enterprise observability backend; personal edition returns zeros.",
    }

    return metrics