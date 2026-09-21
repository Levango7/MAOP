"""Authentication & user management endpoints.

Extracted from server.py for separation of concerns.
Provides: login, logout, register, user CRUD, auth status.
Uses PBKDF2-HMAC-SHA256 for password hashing, JWT for tokens.

Business logic (password hashing, AuthManager singleton, user CRUD,
login rate limiting) lives in ``maop.dashboard.services.auth_service``.
This router only does request parsing, permission checks, service
calls, response formatting, and error handling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from maop.core.security.middleware import require_admin as _require_admin

# P1-1 fix: 引入统一异常处理装饰器，让所有 auth 端点经 handle_api_errors 兜底，
# 异常（含 HTTPException）统一渲染为 ErrorSchema 响应格式。
from maop.dashboard.error_handler import handle_api_errors

# ── Service layer ──────────────────────────────────────────────────
from maop.dashboard.services import auth_service

from .state import MAOP_ROOT  # noqa: F401

router = APIRouter()


# ── Backward-compat attributes for test fixtures ───────────────────
# Test fixtures (test_router_auth_coverage.py) reset these to force
# re-initialisation:  auth_mod._auth_mgr = None
#                     auth_mod._login_failures_table_ready = False
# We sync them to auth_service before each service call that depends
# on the singleton state.
_auth_mgr: Any = None
_login_failures_table_ready: bool = False


def _login_failures_db_path() -> str:
    """Backward compat — delegates to auth_service."""
    return auth_service.login_failures_db_path()


def _sync_auth_state() -> None:
    """Sync router-level reset flags into auth_service (for test fixtures)."""
    global _auth_mgr, _login_failures_table_ready
    if _auth_mgr is None:
        auth_service._auth_mgr = None
    if not _login_failures_table_ready:
        auth_service._login_failures_table_ready = False


# Backward-compat aliases — test fixtures call these via auth_mod.
_hash_password = auth_service.hash_password
_verify_password = auth_service.verify_password
_password_needs_rehash = auth_service.password_needs_rehash
get_auth_mgr = auth_service.get_auth_mgr

# Backward-compat alias — 保留为真实模块属性（而非仅转发读取），因为：
#   1. ``server.py`` 的 lifespan 与 ``routers/notifications.py`` 的 WS 处理器
#      都按 ``_auth_mod._auth_enabled`` 读取；
#   2. 测试用 monkeypatch.setattr(_auth_mod, "_auth_enabled", ...) 切换认证开关，
#      该 API 要求属性存在且可写。
# 认证状态的权威来源仍是 ``auth_service.auth_enabled``；此处只是导入期快照，
# 与重构前 ``_auth_enabled = _settings.auth_enabled`` 的语义一致。
_auth_enabled = auth_service.auth_enabled


# ── Pydantic 请求模型 (批次3A: 输入校验) ───────────────────────────
class LoginRequest(BaseModel):
    """POST /api/auth/login 请求体。"""
    username: str = ""
    password: str = ""


class RegisterRequest(BaseModel):
    """POST /api/auth/register 请求体。"""
    username: str = ""
    password: str = ""
    roles: list[str] = Field(default_factory=lambda: ["read"])


class UpdateUserRequest(BaseModel):
    """PUT /api/auth/users/{username} 请求体。

    所有字段可选——只更新提供的字段。
    """
    roles: list[str] | None = None
    enabled: bool | None = None
    password: str | None = None


# ── Helpers (request-dependent, stay in router) ────────────────────
def _get_client_ip(request: Request) -> str:
    """Extract client IP for login rate limiting.

    When MAOP_TRUST_PROXY is enabled, use X-Forwarded-For header
    to get the real client IP behind a reverse proxy.
    Mirrors RateLimitMiddleware._default_key logic.
    """
    if os.environ.get("MAOP_TRUST_PROXY", "0") == "1":
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # XFF can contain multiple IPs, take the first (original client)
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Endpoints ──────────────────────────────────────────────────────
@router.get("/api/auth/status")
@handle_api_errors("auth status")
async def auth_status(request: Request) -> Any:
    """Check if auth is enabled and whether user is logged in.

    一号用户实测修复（2026-08-31 登录无限循环根因）：has_token 只查
    Authorization header —— M6 改版后前端登录走 httpOnly cookie（后端
    Set-Cookie），页面 reload 后的 status 请求不带 header → has_token
    恒 false → 前端据此清掉刚存的 token → 又弹登录框，无限循环。
    修复：header 之外同样检查 maop_token cookie（M7 修复已让 AuthMiddleware
    接受 cookie），任一有效即 has_token=true。
    """
    has_token = False
    if auth_service.auth_enabled:
        _sync_auth_state()
        mgr = None
        candidates = []
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            candidates.append(auth_header[7:])
        cookie_token = request.cookies.get("maop_token", "")
        if cookie_token:
            candidates.append(cookie_token)
        for token in candidates:
            if not token:
                continue
            try:
                if mgr is None:
                    mgr = auth_service.get_auth_mgr()
                result = mgr.jwt_handler.validate_token(token)
                if result.authenticated:
                    has_token = True
                    break
            except Exception:
                logger.debug('auth status validate failed', exc_info=True)
    return {
        "auth_enabled": auth_service.auth_enabled,
        "has_token": has_token,
    }


@router.post("/api/auth/login")
@handle_api_errors("auth login")
async def auth_login(request: Request, body: LoginRequest) -> Any:
    """Login with username/password, returns JWT token."""
    # 批次3A: 用 Pydantic LoginRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体并返回 422 而非静默 401。
    username = body.username
    password = body.password
    if not username or not password:
        # H1 fix: 统一错误响应——raise HTTPException 让 handle_api_errors 装饰器处理。
        raise HTTPException(status_code=400, detail="Username and password required")

    try:
        _sync_auth_state()
        # P0-1 fix: 用 wall clock（time.time()）替代 monotonic——跨进程共享
        # 限流状态时，monotonic clock 在不同进程间起点不同、不可比。
        now = time.time()
        # H6 fix: 提取客户端 IP 用于 IP 维度限流
        client_ip = _get_client_ip(request)
        # P0-1 fix: 从 SQLite 读取失败计数（多实例共享同一限流状态）
        with auth_service.login_failures_lock:
            auth_service.ensure_login_failures_table()
            failures = auth_service.db_get_login_failures(username, "user", now)
            ip_failures = auth_service.db_get_login_failures(client_ip, "ip", now)
        if len(failures) >= auth_service.MAX_LOGIN_FAILURES:
            raise HTTPException(status_code=429, detail="Account locked. Try again later.")
        # H6 fix: IP 维度限流 —— 同一 IP 15 分钟内失败超过 5 次则锁定
        if len(ip_failures) >= auth_service.MAX_LOGIN_FAILURES:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts from this IP. Try again later.",
            )

        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("auth")
        if not db_path.exists():
            auth_service.get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, auth_service.db_login_user, str(db_path), username, password
        )

        if result["status"] != "ok":
            with auth_service.login_failures_lock:
                auth_service.db_record_login_failure(username, "user", now)
                auth_service.db_record_login_failure(client_ip, "ip", now)  # H6 fix
            raise HTTPException(status_code=401, detail=result.get("error", "Login failed"))

        mgr = auth_service.get_auth_mgr()
        # 租户写入 JWT：登录后凭证携带归属，中间件据此注入
        # request.state.tenant_id，配额 / RBAC / 合规的隔离才真正生效。
        # result 可能缺该键（列迁移失败时），故用 .get 退化为空串。
        token = mgr.jwt_handler.create_token(
            result["username"],
            roles=result["roles"],
            ttl_s=auth_service._JWT_TTL_S,
            tenant_id=result.get("tenant_id", "") or "",
        )
        # P1-18 fix: clear failures on successful login
        with auth_service.login_failures_lock:
            auth_service.db_clear_login_failures(username, "user")
            auth_service.db_clear_login_failures(client_ip, "ip")  # H6 fix

        # #4 fix: set JWT as httpOnly cookie (XSS-proof) + return token for API clients
        response = JSONResponse({
            "status": "ok",
            "token": token,
            "username": result["username"],
            "roles": result["roles"],
            "expires_in": int(auth_service._JWT_TTL_S),
        })
        response.set_cookie(
            key="maop_token", value=token, max_age=int(auth_service._JWT_TTL_S),
            httponly=True, secure=auth_service.tls_enabled, samesite="strict", path="/",
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Login error")
        # sqlite3.Error（如数据库未初始化、表缺失）视为认证服务不可用，返回 401；
        # 其他异常（如 TypeError、IOError 等代码bug）视为内部错误，返回 500。
        if isinstance(exc, sqlite3.Error):
            raise HTTPException(status_code=401, detail="Login failed")
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/api/auth/refresh")
@handle_api_errors("auth refresh")
async def auth_refresh(request: Request):
    """Refresh an existing JWT token before it expires.

    Requires a valid (non-expired) token in the Authorization header.
    Returns a new token with the same identity and roles, extended TTL.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
        )
    token = auth_header[7:]
    try:
        _sync_auth_state()
        mgr = auth_service.get_auth_mgr()
        result = mgr.jwt_handler.validate_token(token)
        if not result.authenticated:
            raise HTTPException(
                status_code=401,
                detail=result.error or "Token invalid or expired",
            )
        # Issue new token with same identity + roles + tenant。
        # 保留原 token 的租户（可能为空串=未分配），避免续期后租户丢失。
        new_token = mgr.jwt_handler.create_token(
            result.identity,
            roles=result.roles,
            ttl_s=auth_service._JWT_TTL_S,
            tenant_id=getattr(result, "tenant_id", "") or "",
        )
        response = JSONResponse({
            "status": "ok",
            "token": new_token,
            "username": result.identity,
            "roles": result.roles or [],
            "expires_in": int(auth_service._JWT_TTL_S),
        })
        response.set_cookie(
            key="maop_token", value=new_token, max_age=int(auth_service._JWT_TTL_S),
            httponly=True, secure=auth_service.tls_enabled, samesite="strict", path="/",
        )
        # Revoke old token so it can't be used after refresh
        try:
            mgr.jwt_handler.revoke_token(token)
        except Exception as exc:
            # P0-5: 修正 logger.warning 格式，使用 as exc + %s + exc_info=True
            logger.warning('[auth] auth_refresh：吊销旧 token 失败已忽略（best-effort）: %s', exc, exc_info=True)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[auth] Token refresh failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Refresh failed, please try again later",
        )


@router.post("/api/auth/logout")
@handle_api_errors("auth logout")
async def auth_logout(request: Request) -> Any:
    """Logout - revoke JWT token server-side (P1 fix).

    M6 fix: 支持从 httpOnly cookie 读取 token（前端不再通过 Authorization header 传递）。
    登出时清除 httpOnly cookie，确保前端登录态完全清除。
    """
    # M6 fix: 优先从 Authorization header 读取 token（兼容旧客户端），
    # 回退到 httpOnly cookie（新前端通过 cookie 认证）。
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get("maop_token", "")
    if token:
        try:
            _sync_auth_state()
            mgr = auth_service.get_auth_mgr()
            revoked = mgr.jwt_handler.revoke_token(token)
            if revoked:
                logger.info("[auth] Token revoked via logout")
        except Exception as exc:
            logger.warning("[auth] Failed to revoke token: %s", exc)
    # M6 fix: 清除 httpOnly cookie，确保前端登录态完全清除。
    response = JSONResponse({"status": "ok", "message": "Token revoked."})
    response.delete_cookie(key="maop_token", path="/")
    return response


@router.post("/api/auth/register")
@handle_api_errors("auth register")
async def auth_register(request: Request, body: RegisterRequest) -> Any:
    """Register a new user (admin only)."""
    # 批次3A: 用 Pydantic RegisterRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（username/password/roles 字段类型）。
    try:
        _sync_auth_state()
        _require_admin(request)
        username = body.username.strip()
        password = body.password
        roles = body.roles

        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password required")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        # P0-6: roles 白名单校验 — 防止攻击者注入 "superadmin" 等非法角色。
        # 仅允许已知的低/中权限角色通过注册接口分配；高权限角色（admin/superadmin）
        # 必须通过专门的提权接口（受 require_superadmin 保护）授予。
        _ALLOWED_REGISTER_ROLES = frozenset({"read", "write", "operator"})
        invalid_roles = [r for r in roles if r not in _ALLOWED_REGISTER_ROLES]
        if invalid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid roles: {invalid_roles}. Allowed roles for registration: {sorted(_ALLOWED_REGISTER_ROLES)}",
            )

        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("auth")
        if not db_path.exists():
            auth_service.get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, auth_service.db_register_user, str(db_path), username, password, roles
        )
        logger.info("[auth] New user registered: %s (roles: %s)", username, roles)
        # P3-3 fix: 辅助函数失败时已 raise，此处仅成功路径。
        return result
    except HTTPException:
        raise
    except auth_service.UsernameAlreadyExists:
        raise HTTPException(status_code=409, detail="Username already exists")
    except Exception as exc:
        logger.exception("[auth] Registration failed")
        # sqlite3.Error（如数据库未初始化、表缺失）视为注册服务不可用，返回 400；
        # 其他异常（如 TypeError、IOError 等代码bug）视为内部错误，返回 500。
        if isinstance(exc, sqlite3.Error):
            raise HTTPException(status_code=400, detail="Registration failed")
        raise HTTPException(status_code=500, detail="Registration failed")


@router.get("/api/auth/users")
@handle_api_errors("auth list users")
async def auth_users(request: Request) -> Any:
    """List all users (admin only)."""
    try:
        _sync_auth_state()
        _require_admin(request)
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("auth")
        if not db_path.exists():
            auth_service.get_auth_mgr()
        users = await asyncio.get_running_loop().run_in_executor(
            None, auth_service.db_list_users, str(db_path)
        )
        return {"status": "ok", "users": users}
    except HTTPException:
        raise
    except Exception:
        logger.exception("[auth] List users failed")
        raise HTTPException(status_code=500, detail="Failed to list users")


@router.delete("/api/auth/users/{username}")
@handle_api_errors("auth delete user")
async def auth_delete_user(username: str, request: Request) -> Any:
    """Delete a user (admin only, cannot delete admin)."""
    try:
        _sync_auth_state()
        _require_admin(request)
        if username == "admin":
            raise HTTPException(status_code=403, detail="Cannot delete admin user")
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("auth")
        result = await asyncio.get_running_loop().run_in_executor(
            None, auth_service.db_delete_user, str(db_path), username
        )
        # P3-3 fix: 辅助函数失败时已 raise，此处仅成功路径。
        return result
    except HTTPException:
        raise
    except auth_service.UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except Exception:
        logger.exception("[auth] Delete user %s failed", username)
        raise HTTPException(status_code=500, detail="Failed to delete user")


@router.put("/api/auth/users/{username}")
@handle_api_errors("auth update user")
async def auth_update_user(username: str, request: Request, body: UpdateUserRequest) -> Any:
    """Update user roles, enabled status, or password (admin only)."""
    # 批次3A: 用 Pydantic UpdateUserRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（roles/enabled/password 字段类型）。
    try:
        _sync_auth_state()
        _require_admin(request)
        # 构造与 db_update_user 兼容的 dict（只包含显式提供的字段）
        update_payload: dict[str, Any] = {}
        if body.roles is not None:
            update_payload["roles"] = body.roles
        if body.enabled is not None:
            update_payload["enabled"] = body.enabled
        if body.password is not None:
            update_payload["password"] = body.password
        from maop.core.backends.db_utils import get_db_path
        db_path = get_db_path("auth")
        result = await asyncio.get_running_loop().run_in_executor(
            None, auth_service.db_update_user, str(db_path), username, update_payload
        )
        # P3-3 fix: 辅助函数失败时已 raise，此处仅成功路径。
        return result
    except HTTPException:
        raise
    except auth_service.UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except auth_service.PasswordTooShort:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    except Exception:
        logger.exception("[auth] User update failed")
        raise HTTPException(status_code=500, detail="Update failed")
