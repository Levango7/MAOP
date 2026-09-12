"""Feedback router — 用户反馈评价系统.

提供对 agent / task / result 三类目标的评分、评论与标签管理.
所有已认证用户均可提交反馈; 查询/更新/删除按归属做 IDOR 防护,
导出与摘要聚合对 admin 放开.

Endpoints:
  POST   /api/feedback                — 提交反馈 (任何已认证用户)
  GET    /api/feedback                — 查询反馈列表 (非 admin 仅看自己)
  GET    /api/feedback/summary        — 反馈摘要 (平均评分/分布/标签词频)
  GET    /api/feedback/export         — 导出反馈 (admin only, csv/json)
  PUT    /api/feedback/{feedback_id}  — 更新反馈 (仅本人)
  DELETE /api/feedback/{feedback_id}  — 删除反馈 (本人或 admin)

数据存储: SQLite ``feedback`` 表 (位于统一 maop.db).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# 允许的目标类型白名单 — 防止任意字符串注入.
_VALID_TARGET_TYPES = ("agent", "task", "result")


# ── Pydantic 请求模型 ─────────────────────────────────────────────


class FeedbackCreate(BaseModel):
    """提交反馈请求体."""

    target_type: str = Field(..., description="目标类型: agent|task|result")
    target_id: str = Field(..., min_length=1, max_length=255, description="目标 ID")
    rating: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: str = Field("", max_length=4000, description="评论(可选)")
    tags: list[str] = Field(default_factory=list, description="标签列表(可选)")


class FeedbackUpdate(BaseModel):
    """更新反馈请求体 — 所有字段可选."""

    rating: int | None = Field(None, ge=1, le=5, description="评分 1-5")
    comment: str | None = Field(None, max_length=4000, description="评论")
    tags: list[str] | None = Field(None, description="标签列表")


# ── 数据库初始化 (线程安全单例) ───────────────────────────────────

_schema_lock = threading.Lock()
_schema_initialized = False


def _ensure_schema() -> None:
    """幂等创建 feedback 表. 使用双重检查锁定避免重复 DDL."""
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        db_path = get_db_path("feedback")
        with sqlite_connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id  TEXT    PRIMARY KEY,
                    target_type  TEXT    NOT NULL,
                    target_id    TEXT    NOT NULL,
                    user_id      TEXT    NOT NULL,
                    tenant_id    TEXT    NOT NULL DEFAULT '',
                    rating       INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment      TEXT    NOT NULL DEFAULT '',
                    tags         TEXT    NOT NULL DEFAULT '[]',
                    created_at   REAL    NOT NULL,
                    updated_at   REAL    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_target "
                "ON feedback(target_type, target_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_user "
                "ON feedback(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_created "
                "ON feedback(created_at)"
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


def _validate_target_type(target_type: str) -> str:
    """校验 target_type 白名单."""
    if target_type not in _VALID_TARGET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target_type; expected one of {_VALID_TARGET_TYPES}",
        )
    return target_type


# ── 行 ↔ dict 转换 ───────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为可 JSON 序列化的 dict."""
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


def _new_feedback_id() -> str:
    """生成反馈 ID."""
    return f"fb_{uuid.uuid4().hex[:16]}"


# ── Endpoints: 提交反馈 ──────────────────────────────────────────


@router.post("")
@handle_api_errors
async def create_feedback(body: FeedbackCreate, request: Request) -> dict[str, Any]:
    """提交反馈 — 任何已认证用户均可调用."""
    user_id = _require_authenticated_user(request)
    _validate_target_type(body.target_type)
    tenant_id = _tenant_id_from_request(request)

    _ensure_schema()
    db_path = get_db_path("feedback")
    feedback_id = _new_feedback_id()
    now = time.time()
    tags_json = json.dumps(body.tags, ensure_ascii=False)

    with sqlite_connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO feedback
                (feedback_id, target_type, target_id, user_id, tenant_id,
                 rating, comment, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                body.target_type,
                body.target_id,
                user_id,
                tenant_id,
                body.rating,
                body.comment,
                tags_json,
                now,
                now,
            ),
        )

    return {"status": "ok", "feedback_id": feedback_id}


# ── Endpoints: 查询反馈列表 ──────────────────────────────────────


@router.get("")
@handle_api_errors
async def list_feedback(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    target_id: str = Query("", description="按目标 ID 过滤"),
    rating: int | None = Query(None, ge=1, le=5, description="按评分过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
    page: int = Query(1, ge=1, description="页码(从 1 开始)"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
) -> dict[str, Any]:
    """查询反馈列表 — 非 admin 用户只能看自己提交的反馈."""
    _require_authenticated_user(request)
    if target_type:
        _validate_target_type(target_type)

    _ensure_schema()
    db_path = get_db_path("feedback")

    # 构造 WHERE 子句与参数
    where_clauses: list[str] = []
    params: list[Any] = []

    if not _is_admin(request):
        # 非 admin 强制按当前用户过滤 — IDOR 防护
        where_clauses.append("user_id = ?")
        params.append(_user_id_from_request(request))

    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        where_clauses.append("target_id = ?")
        params.append(target_id)
    if rating is not None:
        where_clauses.append("rating = ?")
        params.append(rating)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * page_size

    with sqlite_connect(str(db_path)) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM feedback{where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT * FROM feedback{where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    return {
        "status": "ok",
        "data": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── Endpoints: 反馈摘要 ──────────────────────────────────────────


@router.get("/summary")
@handle_api_errors
async def get_summary(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    target_id: str = Query("", description="按目标 ID 过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
) -> dict[str, Any]:
    """反馈摘要 — 平均评分、评分分布、标签词频、评论数."""
    _require_authenticated_user(request)
    if target_type:
        _validate_target_type(target_type)

    _ensure_schema()
    db_path = get_db_path("feedback")

    where_clauses: list[str] = []
    params: list[Any] = []

    # 非 admin 仅统计自己的反馈
    if not _is_admin(request):
        where_clauses.append("user_id = ?")
        params.append(_user_id_from_request(request))

    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        where_clauses.append("target_id = ?")
        params.append(target_id)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with sqlite_connect(str(db_path)) as conn:
        # 平均评分 + 总数 + 评论数
        agg = conn.execute(
            f"""
            SELECT
                COUNT(*)                                AS total,
                AVG(rating)                             AS avg_rating,
                SUM(CASE WHEN comment != '' THEN 1 ELSE 0 END) AS comment_count
            FROM feedback{where_sql}
            """,
            params,
        ).fetchone()

        # 评分分布 (1-5 各多少条)
        dist_rows = conn.execute(
            f"""
            SELECT rating, COUNT(*) AS c
            FROM feedback{where_sql}
            GROUP BY rating
            """,
            params,
        ).fetchall()
        rating_distribution = {int(r["rating"]): int(r["c"]) for r in dist_rows}

        # 标签词频 — tags 存为 JSON 数组, 在 Python 侧聚合
        tag_rows = conn.execute(
            f"SELECT tags FROM feedback{where_sql}",
            params,
        ).fetchall()
    tag_freq: dict[str, int] = {}
    for tr in tag_rows:
        try:
            for tag in json.loads(tr["tags"]):
                if tag:
                    tag_freq[tag] = tag_freq.get(tag, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue

    total = int(agg["total"])
    avg_rating = float(agg["avg_rating"]) if agg["avg_rating"] is not None else 0.0

    return {
        "status": "ok",
        "summary": {
            "total": total,
            "average_rating": round(avg_rating, 2),
            "rating_distribution": {str(k): v for k, v in sorted(rating_distribution.items())},
            "comment_count": int(agg["comment_count"] or 0),
            "tag_frequency": dict(sorted(tag_freq.items(), key=lambda x: (-x[1], x[0]))),
        },
    }


# ── Endpoints: 导出反馈 (admin only) ─────────────────────────────


@router.get("/export")
@handle_api_errors
async def export_feedback(
    request: Request,
    target_type: str = Query("", description="按目标类型过滤"),
    date_from: float | None = Query(None, description="起始时间戳(秒)"),
    date_to: float | None = Query(None, description="截止时间戳(秒)"),
    format: str = Query("csv", pattern="^(csv|json)$", description="导出格式"),
) -> Response:
    """导出反馈 — admin only. 支持 csv / json."""
    require_admin(request)
    if target_type:
        _validate_target_type(target_type)

    _ensure_schema()
    db_path = get_db_path("feedback")

    where_clauses: list[str] = []
    params: list[Any] = []
    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)
    if date_from is not None:
        where_clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("created_at <= ?")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with sqlite_connect(str(db_path)) as conn:
        rows = conn.execute(
            f"SELECT * FROM feedback{where_sql} ORDER BY created_at DESC",
            params,
        ).fetchall()
    items = [_row_to_dict(r) for r in rows]

    if format == "json":
        payload = json.dumps(
            {"status": "ok", "count": len(items), "data": items},
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=feedback.json"},
        )

    # CSV 导出
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "feedback_id",
            "target_type",
            "target_id",
            "user_id",
            "tenant_id",
            "rating",
            "comment",
            "tags",
            "created_at",
            "updated_at",
        ]
    )
    for it in items:
        writer.writerow(
            [
                it["feedback_id"],
                it["target_type"],
                it["target_id"],
                it["user_id"],
                it.get("tenant_id", ""),
                it["rating"],
                it["comment"],
                json.dumps(it["tags"], ensure_ascii=False),
                it["created_at"],
                it["updated_at"],
            ]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"},
    )


# ── Endpoints: 更新 / 删除 (动态路由, 必须在静态路由之后) ────────
# WARNING: 路由顺序 — /summary 与 /export 必须在 /{feedback_id} 之前注册,
# 否则 FastAPI 会把 "summary" / "export" 当作 feedback_id 匹配.


def _check_feedback_ownership(row: sqlite3.Row | None, request: Request) -> dict[str, Any]:
    """加载反馈并校验归属权 — IDOR 防护.

    不存在 → 404; 非 admin 且非本人 → 404 (不泄露资源存在性).
    """
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found",
        )
    item = _row_to_dict(row)
    if _is_admin(request):
        return item
    req_user = _user_id_from_request(request)
    if req_user and item.get("user_id", "") != req_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found",
        )
    return item


@router.put("/{feedback_id}")
@handle_api_errors
async def update_feedback(
    feedback_id: str,
    body: FeedbackUpdate,
    request: Request,
) -> dict[str, Any]:
    """更新反馈 — 仅本人可更新自己的反馈."""
    _require_authenticated_user(request)
    _ensure_schema()
    db_path = get_db_path("feedback")

    with sqlite_connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        item = _check_feedback_ownership(row, request)

        # 构造动态 UPDATE — 仅更新提供的字段
        updates: list[str] = []
        params: list[Any] = []
        if body.rating is not None:
            updates.append("rating = ?")
            params.append(body.rating)
        if body.comment is not None:
            updates.append("comment = ?")
            params.append(body.comment)
        if body.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(body.tags, ensure_ascii=False))

        if not updates:
            # 无字段需要更新 — 直接返回原值
            return {"status": "ok", "feedback": item}

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(feedback_id)
        conn.execute(
            f"UPDATE feedback SET {', '.join(updates)} WHERE feedback_id = ?",
            params,
        )
        # 重新读取更新后的行
        row = conn.execute(
            "SELECT * FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        item = _row_to_dict(row)

    return {"status": "ok", "feedback": item}


@router.delete("/{feedback_id}")
@handle_api_errors
async def delete_feedback(feedback_id: str, request: Request) -> dict[str, Any]:
    """删除反馈 — 本人或 admin 可删除."""
    _require_authenticated_user(request)
    _ensure_schema()
    db_path = get_db_path("feedback")

    with sqlite_connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
        # 先校验归属权再删除 — IDOR 防护
        _check_feedback_ownership(row, request)
        conn.execute(
            "DELETE FROM feedback WHERE feedback_id = ?",
            (feedback_id,),
        )

    return {"status": "ok", "deleted": True}