"""Agent 版本管理 / 灰度发布路由 — 版本 CRUD + 激活 + 回滚 + 灰度 + 指标.

提供 Agent 版本的全生命周期管理:

  POST   /api/agent-versions                       — 创建新版本 (admin)
  GET    /api/agent-versions                       — 列出版本 (已认证, 支持过滤+分页)
  GET    /api/agent-versions/{version_id}          — 获取版本详情 (已认证)
  PUT    /api/agent-versions/{version_id}          — 更新版本信息 (admin)
  DELETE /api/agent-versions/{version_id}          — 删除版本 (admin)
  POST   /api/agent-versions/{version_id}/activate — 激活版本 (全量切换, admin)
  POST   /api/agent-versions/{version_id}/rollback — 回滚到指定版本 (admin)
  POST   /api/agent-versions/{version_id}/canary   — 配置灰度发布 (admin)
  GET    /api/agent-versions/{version_id}/canary   — 查询灰度发布状态 (已认证)
  GET    /api/agent-versions/{version_id}/metrics  — 查询版本指标 (已认证)

数据存储: SQLite ``agent_versions`` 表 + ``agent_version_canaries`` 表
(位于统一 maop.db).

安全:
  - 写操作 (create/update/delete/activate/rollback/canary) 需 admin 角色.
  - 读操作 (list/get/canary 查询/metrics) 需已认证.
  - IDOR 防护: 非 admin 用户只能查看自己创建的版本 (created_by 过滤).
  - 异常脱敏: 由 ``handle_api_errors`` 统一处理.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-versions", tags=["agent-versions"])

# 版本状态白名单 — 防止任意字符串注入.
_VALID_VERSION_STATUSES = ("draft", "active", "retired", "deleted")
# 灰度状态白名单.
_VALID_CANARY_STATUSES = ("active", "paused", "completed", "failed")
# 灰度百分比合法范围.
_CANARY_MIN_PCT = 0
_CANARY_MAX_PCT = 100

# 修复：SELECT * 改为明确列名 —— 避免表新增列后 dict 键漂移破坏 API 契约，
# 同时让查询列显式可审计。与 CREATE TABLE 列定义保持一致。
_AGENT_VERSION_COLUMNS = (
    "version_id, agent_id, version_number, config_snapshot, changelog, "
    "status, created_by, tenant_id, created_at, updated_at, activated_at, "
    "retired_at"
)
_CANARY_COLUMNS = (
    "canary_id, version_id, percentage, target_user_groups, strategy, "
    "status, duration_seconds, started_at, ended_at, created_by"
)


# ── Pydantic 请求模型 ─────────────────────────────────────────────


class VersionCreate(BaseModel):
    """创建版本请求体."""

    agent_id: str = Field(..., min_length=1, max_length=255, description="Agent ID")
    version_number: str = Field(..., min_length=1, max_length=128, description="版本号 (如 1.0.0)")
    config_snapshot: dict[str, Any] = Field(
        default_factory=dict, description="配置快照 (JSON 对象)"
    )
    changelog: str = Field("", max_length=8000, description="变更说明")
    target_user_groups: list[str] = Field(
        default_factory=list, description="目标用户组 (可选, 用于初始灰度定向)"
    )


class VersionUpdate(BaseModel):
    """更新版本请求体 — 所有字段可选."""

    version_number: str | None = Field(None, min_length=1, max_length=128, description="版本号")
    config_snapshot: dict[str, Any] | None = Field(None, description="配置快照")
    changelog: str | None = Field(None, max_length=8000, description="变更说明")


class CanaryConfig(BaseModel):
    """灰度发布配置请求体."""

    percentage: int = Field(
        ..., ge=_CANARY_MIN_PCT, le=_CANARY_MAX_PCT, description="灰度百分比 0-100"
    )
    target_user_groups: list[str] = Field(
        default_factory=list, description="目标用户组列表"
    )
    strategy: str = Field(
        "percentage", pattern="^(percentage|user_group|both)$", description="灰度策略"
    )
    duration_seconds: int | None = Field(
        None, ge=1, le=86400 * 30, description="灰度持续时间(秒, 可选)"
    )


class RollbackRequest(BaseModel):
    """回滚请求体."""

    target_version_id: str = Field(
        ..., min_length=1, max_length=255, description="回滚目标版本 ID"
    )
    reason: str = Field("", max_length=4000, description="回滚原因")


# ── 数据库初始化 (线程安全单例) ───────────────────────────────────

_schema_lock = threading.Lock()
_schema_initialized = False


def _ensure_schema() -> None:
    """幂等创建 agent_versions / agent_version_canaries 表. 使用双重检查锁定."""
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        db_path = get_db_path("agent_versions")
        with sqlite_connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_versions (
                    version_id      TEXT    PRIMARY KEY,
                    agent_id        TEXT    NOT NULL,
                    version_number  TEXT    NOT NULL,
                    config_snapshot TEXT    NOT NULL DEFAULT '{}',
                    changelog       TEXT    NOT NULL DEFAULT '',
                    status          TEXT    NOT NULL DEFAULT 'draft',
                    created_by      TEXT    NOT NULL,
                    tenant_id       TEXT    NOT NULL DEFAULT '',
                    created_at      REAL    NOT NULL,
                    updated_at      REAL    NOT NULL,
                    activated_at    REAL,
                    retired_at      REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_version_canaries (
                    canary_id          TEXT    PRIMARY KEY,
                    version_id         TEXT    NOT NULL,
                    percentage         INTEGER NOT NULL DEFAULT 0,
                    target_user_groups TEXT    NOT NULL DEFAULT '[]',
                    strategy           TEXT    NOT NULL DEFAULT 'percentage',
                    status             TEXT    NOT NULL DEFAULT 'active',
                    duration_seconds   INTEGER,
                    started_at         REAL    NOT NULL,
                    ended_at           REAL,
                    created_by         TEXT    NOT NULL,
                    FOREIGN KEY (version_id) REFERENCES agent_versions(version_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_versions_agent "
                "ON agent_versions(agent_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_versions_status "
                "ON agent_versions(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_versions_created "
                "ON agent_versions(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_version_canaries_version "
                "ON agent_version_canaries(version_id)"
            )
        _schema_initialized = True


def _reset_schema_for_tests() -> None:
    """Reset schema init flag — used by unit tests."""
    global _schema_initialized
    _schema_initialized = False


# ── 请求状态辅助函数 ─────────────────────────────────────────────


def _user_id_from_request(request: Request) -> str:
    """提取当前认证用户身份 (auth_identity)."""
    return getattr(request.state, "auth_identity", "") or ""


def _tenant_id_from_request(request: Request) -> str:
    """提取 tenant_id (多租户隔离)."""
    return getattr(request.state, "tenant_id", "") or ""


def _is_admin(request: Request) -> bool:
    """判断当前用户是否 admin / superadmin."""
    roles = getattr(request.state, "auth_roles", None) or []
    return bool({"admin", "superadmin"} & set(roles))


def _require_authenticated_user(request: Request) -> str:
    """要求请求已认证, 返回 user_id. 未认证 → 401."""
    user_id = _user_id_from_request(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user_id


# ── 行 ↔ dict 转换 ───────────────────────────────────────────────


def _version_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 agent_versions 行转为可 JSON 序列化的 dict."""
    d = dict(row)
    try:
        d["config_snapshot"] = json.loads(d.get("config_snapshot", "{}"))
    except (json.JSONDecodeError, TypeError):
        d["config_snapshot"] = {}
    return d


def _canary_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 agent_version_canaries 行转为可 JSON 序列化的 dict."""
    d = dict(row)
    try:
        d["target_user_groups"] = json.loads(d.get("target_user_groups", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["target_user_groups"] = []
    return d


def _new_version_id() -> str:
    """生成版本 ID."""
    return f"ver_{uuid.uuid4().hex[:16]}"


def _new_canary_id() -> str:
    """生成灰度发布 ID."""
    return f"canary_{uuid.uuid4().hex[:16]}"


def _get_version_row(
    conn: sqlite3.Connection, version_id: str
) -> sqlite3.Row | None:
    """按 ID 读取版本行."""
    return conn.execute(
        f"SELECT {_AGENT_VERSION_COLUMNS} FROM agent_versions WHERE version_id = ?",
        (version_id,),
    ).fetchone()


def _check_version_access(
    row: sqlite3.Row | None, request: Request
) -> dict[str, Any]:
    """加载版本并校验访问权 — IDOR 防护.

    不存在 → 404; 非 admin 且非本人创建 → 404 (不泄露资源存在性).
    """
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    item = _version_row_to_dict(row)
    if _is_admin(request):
        return item
    req_user = _user_id_from_request(request)
    if req_user and item.get("created_by", "") != req_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return item


# ── Endpoints: 创建版本 ──────────────────────────────────────────


@router.post("")
@handle_api_errors
async def create_version(
    body: VersionCreate, request: Request
) -> dict[str, Any]:
    """创建新版本 — admin only. 版本初始状态为 draft."""
    require_admin(request)
    user_id = _user_id_from_request(request)
    tenant_id = _tenant_id_from_request(request)

    _ensure_schema()
    db_path = get_db_path("agent_versions")
    version_id = _new_version_id()
    now = time.time()
    config_json = json.dumps(body.config_snapshot, ensure_ascii=False)

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
                body.agent_id,
                body.version_number,
                config_json,
                body.changelog,
                user_id,
                tenant_id,
                now,
                now,
            ),
        )

    return {"status": "ok", "data": {"version_id": version_id}}


# ── Endpoints: 列出版本 ──────────────────────────────────────────


@router.get("")
@handle_api_errors
async def list_versions(
    request: Request,
    agent_id: str = Query("", description="按 Agent ID 过滤"),
    status_filter: str = Query(
        "", alias="status", description="按状态过滤 (draft/active/retired/deleted)"
    ),
    page: int = Query(1, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
) -> dict[str, Any]:
    """列出版本 — 非 admin 用户只能看自己创建的版本."""
    _require_authenticated_user(request)
    if status_filter and status_filter not in _VALID_VERSION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status; expected one of {_VALID_VERSION_STATUSES}",
        )

    _ensure_schema()
    db_path = get_db_path("agent_versions")

    where_clauses: list[str] = []
    params: list[Any] = []

    # 非 admin 强制按创建者过滤 — IDOR 防护
    if not _is_admin(request):
        where_clauses.append("created_by = ?")
        params.append(_user_id_from_request(request))

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
        "status": "ok",
        "data": [_version_row_to_dict(r) for r in rows],
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


# ── Endpoints: 获取版本详情 ──────────────────────────────────────


@router.get("/{version_id}")
@handle_api_errors
async def get_version(version_id: str, request: Request) -> dict[str, Any]:
    """获取版本详情 — 已认证, IDOR 防护."""
    _require_authenticated_user(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        item = _check_version_access(row, request)

    return {"status": "ok", "data": item}


# ── Endpoints: 更新版本 ──────────────────────────────────────────


@router.put("/{version_id}")
@handle_api_errors
async def update_version(
    version_id: str,
    body: VersionUpdate,
    request: Request,
) -> dict[str, Any]:
    """更新版本信息 — admin only."""
    require_admin(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version not found",
            )

        updates: list[str] = []
        params: list[Any] = []
        if body.version_number is not None:
            updates.append("version_number = ?")
            params.append(body.version_number)
        if body.config_snapshot is not None:
            updates.append("config_snapshot = ?")
            params.append(json.dumps(body.config_snapshot, ensure_ascii=False))
        if body.changelog is not None:
            updates.append("changelog = ?")
            params.append(body.changelog)

        if not updates:
            item = _version_row_to_dict(row)
            return {"status": "ok", "data": item}

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(version_id)
        conn.execute(
            f"UPDATE agent_versions SET {', '.join(updates)} WHERE version_id = ?",
            params,
        )
        row = _get_version_row(conn, version_id)
        item = _version_row_to_dict(row)

    return {"status": "ok", "data": item}


# ── Endpoints: 删除版本 ──────────────────────────────────────────


@router.delete("/{version_id}")
@handle_api_errors
async def delete_version(version_id: str, request: Request) -> dict[str, Any]:
    """删除版本 — admin only. 软删除: 状态置为 deleted."""
    require_admin(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version not found",
            )
        existing = _version_row_to_dict(row)
        # active 版本不允许直接删除
        if existing["status"] == "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete an active version; retire or rollback first",
            )
        now = time.time()
        conn.execute(
            "UPDATE agent_versions SET status = 'deleted', updated_at = ? WHERE version_id = ?",
            (now, version_id),
        )

    return {"status": "ok", "data": {"deleted": True}}


# ── Endpoints: 激活版本 (全量切换) ───────────────────────────────


@router.post("/{version_id}/activate")
@handle_api_errors
async def activate_version(version_id: str, request: Request) -> dict[str, Any]:
    """激活版本 — 全量切换. 同一 agent 的其他 active 版本自动转为 retired."""
    require_admin(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version not found",
            )
        target = _version_row_to_dict(row)
        if target["status"] == "deleted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot activate a deleted version",
            )
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
        item = _version_row_to_dict(row)

    return {"status": "ok", "data": item}


# ── Endpoints: 回滚到指定版本 ────────────────────────────────────


@router.post("/{version_id}/rollback")
@handle_api_errors
async def rollback_version(
    version_id: str,
    body: RollbackRequest,
    request: Request,
) -> dict[str, Any]:
    """回滚到指定版本 — 将 version_id 置为 retired, target_version_id 置为 active."""
    require_admin(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        # 校验当前版本存在
        current_row = _get_version_row(conn, version_id)
        if current_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version not found",
            )
        current = _version_row_to_dict(current_row)
        # 校验目标版本存在且属于同一 agent
        target_row = _get_version_row(conn, body.target_version_id)
        if target_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target version not found",
            )
        target = _version_row_to_dict(target_row)
        if target["agent_id"] != current["agent_id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot rollback to a version of a different agent",
            )
        if target["status"] == "deleted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot rollback to a deleted version",
            )
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
            (now, now, body.target_version_id),
        )
        row = _get_version_row(conn, body.target_version_id)
        item = _version_row_to_dict(row)

    return {
        "status": "ok",
        "data": item,
        "meta": {
            "rolled_back_from": version_id,
            "rolled_back_to": body.target_version_id,
            "reason": body.reason,
        },
    }


# ── Endpoints: 配置灰度发布 ──────────────────────────────────────


@router.post("/{version_id}/canary")
@handle_api_errors
async def configure_canary(
    version_id: str,
    body: CanaryConfig,
    request: Request,
) -> dict[str, Any]:
    """配置灰度发布 — admin only. 同一版本同时只允许一个 active 灰度."""
    require_admin(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")
    user_id = _user_id_from_request(request)

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version not found",
            )
        target = _version_row_to_dict(row)
        if target["status"] == "deleted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot configure canary for a deleted version",
            )
        # 检查是否已有 active 灰度
        existing = conn.execute(
            f"""
            SELECT {_CANARY_COLUMNS} FROM agent_version_canaries
            WHERE version_id = ? AND status = 'active'
            """,
            (version_id,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active canary already exists for this version",
            )
        canary_id = _new_canary_id()
        now = time.time()
        groups_json = json.dumps(body.target_user_groups, ensure_ascii=False)
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
                body.percentage,
                groups_json,
                body.strategy,
                body.duration_seconds,
                now,
                user_id,
            ),
        )
        canary_row = conn.execute(
            f"SELECT {_CANARY_COLUMNS} FROM agent_version_canaries WHERE canary_id = ?",
            (canary_id,),
        ).fetchone()
        canary = _canary_row_to_dict(canary_row)

    return {"status": "ok", "data": canary}


# ── Endpoints: 查询灰度发布状态 ──────────────────────────────────


@router.get("/{version_id}/canary")
@handle_api_errors
async def get_canary(version_id: str, request: Request) -> dict[str, Any]:
    """查询灰度发布状态 — 已认证. 返回该版本所有灰度记录."""
    _require_authenticated_user(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        _check_version_access(row, request)
        canary_rows = conn.execute(
            f"SELECT {_CANARY_COLUMNS} FROM agent_version_canaries "
            f"WHERE version_id = ? ORDER BY started_at DESC",
            (version_id,),
        ).fetchall()

    return {
        "status": "ok",
        "data": [_canary_row_to_dict(r) for r in canary_rows],
        "meta": {"count": len(canary_rows)},
    }


# ── Endpoints: 查询版本指标 ──────────────────────────────────────


@router.get("/{version_id}/metrics")
@handle_api_errors
async def get_version_metrics(version_id: str, request: Request) -> dict[str, Any]:
    """查询版本指标 — 已认证.

    返回该版本与同 agent 当前 active 版本的对比指标
    (成功率、平均延迟、错误率). 个人版无真实指标数据时优雅降级返回零值.
    """
    _require_authenticated_user(request)
    _ensure_schema()
    db_path = get_db_path("agent_versions")

    with sqlite_connect(str(db_path)) as conn:
        row = _get_version_row(conn, version_id)
        item = _check_version_access(row, request)
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

    return {"status": "ok", "data": metrics}