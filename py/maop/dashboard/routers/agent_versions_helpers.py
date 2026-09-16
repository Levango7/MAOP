"""Agent 版本管理 — Pydantic 模型 + 辅助函数 (从 agent_versions.py 拆出).

本模块包含:
  - Pydantic 请求模型: VersionCreate, VersionUpdate, CanaryConfig, RollbackRequest
  - 常量: 状态白名单 / 灰度范围 / SQL 列定义
  - 数据库 schema 初始化 (线程安全单例)
  - 请求状态辅助: user_id / tenant_id / admin 校验
  - 行 ↔ dict 转换 + ID 生成 + 版本读取 + 访问校验

路由处理器 (async def) 保留在 ``agent_versions.py`` 中.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect

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