"""Authentication & user management endpoints.

Extracted from server.py for separation of concerns.
Provides: login, logout, register, user CRUD, auth status.
Uses PBKDF2-HMAC-SHA256 for password hashing, JWT for tokens.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .state import MAOP_ROOT

router = APIRouter()

# ── Auth config ────────────────────────────────────────────────────
from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.security.auth import APIKeyStore, AuthConfig, AuthManager, JWTConfig, load_jwt_secret

_env_is_prod = os.environ.get("MAOP_ENV", "").strip().lower() == "production"
# High 安全修复 (2.3): secure-by-default。只有显式声明本地开发环境
# (dev/development/local/test) 才默认禁用认证；staging/QA/demo/未设置/
# 拼写错误一律默认启用。与 settings._default_auth_enabled 保持一致。
_env_is_dev = os.environ.get("MAOP_ENV", "").strip().lower() in (
    "dev", "development", "local", "test",
)
_auth_enabled = os.environ.get("MAOP_AUTH", "0" if _env_is_dev else "1") == "1"
# M6 fix (Phase R5): OWASP 2023 推荐 600k 迭代 for PBKDF2-HMAC-SHA256
_AUTH_PBKDF2_ITERATIONS = 600_000
_auth_mgr: AuthManager | None = None


# ── Password helpers ───────────────────────────────────────────────
def _hash_password(password: str) -> str:
    """Hash a password for dashboard users using PBKDF2-HMAC-SHA256."""

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _AUTH_PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_AUTH_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify PBKDF2 hashes only. Legacy unsalted SHA-256 is no longer accepted."""

    if not stored_hash.startswith("pbkdf2_sha256$"):
        logger.warning("[auth] Rejected legacy unsalted hash format. User must reset password.")
        return False

    try:
        _, iterations_s, salt_b64, digest_b64 = stored_hash.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.b64decode(salt_b64),
            int(iterations_s),
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


def _password_needs_rehash(stored_hash: str) -> bool:
    return not stored_hash.startswith("pbkdf2_sha256$")


# ── Auth manager singleton ─────────────────────────────────────────
def get_auth_mgr() -> AuthManager:
    """Lazy-init AuthManager singleton. Called by lifespan and endpoints."""
    global _auth_mgr
    if _auth_mgr is None:
        jwt_secret = load_jwt_secret(MAOP_ROOT / "data")

        cfg = AuthConfig(
            enabled=True,
            jwt=JWTConfig(secret=jwt_secret, default_ttl_s=7200.0),
        )
        db_path = get_db_path("auth")
        _auth_mgr = AuthManager(
            config=cfg,
            key_store=APIKeyStore(db_path=str(db_path)),
        )
        _ensure_default_user()
    return _auth_mgr


def _ensure_default_user() -> None:
    """Create default admin user on first run if none exists."""
    try:
        db_path = get_db_path("auth")
        with sqlite_connect(str(db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    roles TEXT NOT NULL DEFAULT '["admin"]',
                    created_at REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
            """)
            existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if existing == 0:
                # H7 fix: 支持 Docker secrets 标准（MAOP_ADMIN_PASSWORD_FILE）。
                # 优先读取 MAOP_ADMIN_PASSWORD_FILE 指向的文件内容作为密码，
                # 回退到 MAOP_ADMIN_PASSWORD 环境变量。
                admin_pwd = ""
                pwd_file = os.environ.get("MAOP_ADMIN_PASSWORD_FILE", "")
                if pwd_file:
                    try:
                        with open(pwd_file, "r", encoding="utf-8") as f:
                            admin_pwd = f.read().strip()
                    except OSError as exc:
                        logger.warning(
                            "[auth] MAOP_ADMIN_PASSWORD_FILE=%s 读取失败: %s",
                            pwd_file, exc,
                        )
                if not admin_pwd:
                    admin_pwd = os.environ.get("MAOP_ADMIN_PASSWORD", "")
                if not admin_pwd:
                    import secrets
                    admin_pwd = secrets.token_urlsafe(16)
                    # S4 fix: DO NOT persist the plaintext password to disk.
                    # Surface it once via the log so the operator can read it
                    # from stdout/logs, and require an explicit
                    # MAOP_ADMIN_PASSWORD in production (fail-fast).
                    env = os.environ.get("MAOP_ENV", "").strip().lower()
                    if env == "production":
                        raise RuntimeError(
                            "SECURITY: MAOP_ADMIN_PASSWORD must be set explicitly in "
                            "production (MAOP_ENV=production). Refusing to start with a "
                            "random, non-persisted admin password."
                        )
                    logger.warning(
                        "MAOP_ADMIN_PASSWORD not set — generated a ONE-TIME random admin "
                        "password (shown below). Set MAOP_ADMIN_PASSWORD to persist it "
                        "across restarts. First-run admin password: %s",
                        admin_pwd,
                    )
                pwd_hash = _hash_password(admin_pwd)
                conn.execute(
                    "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
                    ("admin", pwd_hash, '["admin","read","write","execute"]', time.time()),
                )
                logger.info("[auth] Default admin user created (password from MAOP_ADMIN_PASSWORD env)")
    except Exception as exc:
        logger.warning("[auth] Failed to create default user: %s", exc)


# ── Admin guard ────────────────────────────────────────────────────
from maop.core.security.middleware import require_admin as _require_admin


# ── Sync DB helpers (for run_in_executor) ──────────────────────────
def _db_login_user(db_path_str: str, username: str, password: str) -> Any:
    """Sync: validate user credentials, return result dict."""

    with sqlite_connect(db_path_str) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND enabled = 1",
            (username,),
        ).fetchone()

    if row is None:
        return {"status": "error", "error": "Invalid credentials"}

    stored_hash = row["password_hash"]
    if not _verify_password(password, stored_hash):
        return {"status": "error", "error": "Invalid credentials"}

    if _password_needs_rehash(stored_hash):
        with sqlite_connect(db_path_str) as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (_hash_password(password), username),
            )

    roles = json.loads(row["roles"])
    return {"status": "ok", "username": username, "roles": roles}


def _db_register_user(db_path_str: str, username: str, password: str, roles: list) -> Any:
    """Sync: register a new user."""

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            return JSONResponse({"status": "error", "error": "Username already exists"}, status_code=409)

        pwd_hash = _hash_password(password)
        conn.execute(
            "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
            (username, pwd_hash, json.dumps(roles), time.time()),
        )

    return {"status": "ok", "username": username, "roles": roles}


def _db_list_users(db_path_str: str) -> list:
    """Sync: list all users."""

    with sqlite_connect(db_path_str) as conn:
        rows = conn.execute("SELECT username, roles, created_at, enabled FROM users ORDER BY created_at").fetchall()

    return [{"username": r["username"], "roles": json.loads(r["roles"]),
             "created_at": r["created_at"], "enabled": bool(r["enabled"])} for r in rows]


def _db_delete_user(db_path_str: str, username: str) -> Any:
    """Sync: delete a user."""
    with sqlite_connect(db_path_str) as conn:
        result = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        deleted = result.rowcount > 0

    if not deleted:
        return JSONResponse({"status": "error", "error": "User not found"}, status_code=404)
    return {"status": "ok", "message": f"User {username} deleted"}


def _db_update_user(db_path_str: str, username: str, body: dict) -> Any:
    """Sync: update user roles, enabled, or password."""

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            return JSONResponse({"status": "error", "error": "User not found"}, status_code=404)
        if "roles" in body:
            conn.execute("UPDATE users SET roles = ? WHERE username = ?", (json.dumps(body["roles"]), username))
        if "enabled" in body:
            conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (1 if body["enabled"] else 0, username))
        if "password" in body:
            if not isinstance(body["password"], str) or len(body["password"]) < 8:
                return JSONResponse({"status": "error", "error": "Password must be at least 8 characters"}, status_code=400)
            pwd_hash = _hash_password(body["password"])
            conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pwd_hash, username))

    return {"status": "ok", "message": f"User {username} updated"}


# ── Endpoints ──────────────────────────────────────────────────────
_login_failures: dict[str, list[float]] = {}
# H6 fix: IP 维度限流。攻击者可用单一密码遍历用户名绕过 username lockout，
# 增加 IP 维度记录，对同一 IP 的失败登录次数进行限制。
_login_failures_by_ip: dict[str, list[float]] = {}
_login_failures_lock = threading.Lock()
_MAX_LOGIN_FAILURES = 5
_LOCKOUT_SECONDS = 900.0
_MAX_TRACKED_USERS = 10_000  # P1-18 fix: prevent unbounded growth
_MAX_TRACKED_IPS = 10_000  # H6 fix: prevent unbounded growth for IP tracking


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

@router.get("/api/auth/status")
async def auth_status(request: Request) -> Any:
    """Check if auth is enabled and whether user is logged in."""
    # F-P0-8 fix: check actual token from Authorization header
    has_token = False
    if _auth_enabled:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                mgr = get_auth_mgr()
                result = mgr.jwt_handler.validate_token(token)
                if result.authenticated:
                    has_token = True
            except Exception:
                logger.debug('swallowed exception', exc_info=True)
    return {
        "auth_enabled": _auth_enabled,
        "has_token": has_token,
    }


@router.post("/api/auth/login")
async def auth_login(request: Request) -> Any:
    """Login with username/password, returns JWT token."""
    try:
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            return JSONResponse({"status": "error", "error": "Username and password required"}, status_code=400)

        now = time.monotonic()
        # H6 fix: 提取客户端 IP 用于 IP 维度限流
        client_ip = _get_client_ip(request)
        with _login_failures_lock:
            failures = _login_failures.get(username, [])
            failures = [t for t in failures if now - t < _LOCKOUT_SECONDS]
            _login_failures[username] = failures
            # P1-18 fix: periodic cleanup to prevent unbounded growth.
            # High 安全修复 (2.5): 不再整表 clear() —— 攻击者可用 1 万个不同
            # 用户名触发 clear 来重置目标账户的锁定计数（暴力破解绕过）。
            # 改为：先清理已过期条目；仍超限时仅淘汰"未锁定"的最旧条目，
            # 已锁定账户的计数永不被淘汰。
            if len(_login_failures) > _MAX_TRACKED_USERS:
                for user in list(_login_failures):
                    recent = [
                        t for t in _login_failures[user]
                        if now - t < _LOCKOUT_SECONDS
                    ]
                    if recent:
                        _login_failures[user] = recent
                    else:
                        del _login_failures[user]
                if len(_login_failures) > _MAX_TRACKED_USERS:
                    evictable = sorted(
                        (
                            u for u, ts in _login_failures.items()
                            if len(ts) < _MAX_LOGIN_FAILURES and u != username
                        ),
                        key=lambda u: max(_login_failures[u], default=0.0),
                    )
                    excess = len(_login_failures) - _MAX_TRACKED_USERS
                    for u in evictable[:excess]:
                        del _login_failures[u]
            # H6 fix: IP 维度限流检查与清理（与 username lockout 逻辑一致）
            ip_failures = _login_failures_by_ip.get(client_ip, [])
            ip_failures = [t for t in ip_failures if now - t < _LOCKOUT_SECONDS]
            _login_failures_by_ip[client_ip] = ip_failures
            if len(_login_failures_by_ip) > _MAX_TRACKED_IPS:
                for ip in list(_login_failures_by_ip):
                    recent = [
                        t for t in _login_failures_by_ip[ip]
                        if now - t < _LOCKOUT_SECONDS
                    ]
                    if recent:
                        _login_failures_by_ip[ip] = recent
                    else:
                        del _login_failures_by_ip[ip]
                if len(_login_failures_by_ip) > _MAX_TRACKED_IPS:
                    evictable_ip = sorted(
                        (
                            ip for ip, ts in _login_failures_by_ip.items()
                            if len(ts) < _MAX_LOGIN_FAILURES and ip != client_ip
                        ),
                        key=lambda ip: max(_login_failures_by_ip[ip], default=0.0),
                    )
                    excess_ip = len(_login_failures_by_ip) - _MAX_TRACKED_IPS
                    for ip in evictable_ip[:excess_ip]:
                        del _login_failures_by_ip[ip]
        if len(failures) >= _MAX_LOGIN_FAILURES:
            return JSONResponse({"status": "error", "error": "Account locked. Try again later."}, status_code=429)
        # H6 fix: IP 维度限流 —— 同一 IP 15 分钟内失败超过 5 次则锁定
        if len(ip_failures) >= _MAX_LOGIN_FAILURES:
            return JSONResponse(
                {"status": "error", "error": "Too many login attempts from this IP. Try again later."},
                status_code=429,
            )

        db_path = get_db_path("auth")
        if not db_path.exists():
            get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, _db_login_user, str(db_path), username, password
        )

        if result["status"] != "ok":
            with _login_failures_lock:
                _login_failures.setdefault(username, []).append(now)
                _login_failures_by_ip.setdefault(client_ip, []).append(now)  # H6 fix
            return JSONResponse(result, status_code=401)

        mgr = get_auth_mgr()
        token = mgr.jwt_handler.create_token(result["username"], roles=result["roles"], ttl_s=7200.0)
        # P1-18 fix: clear failures on successful login
        with _login_failures_lock:
            _login_failures.pop(username, None)
            _login_failures_by_ip.pop(client_ip, None)  # H6 fix

        # #4 fix: set JWT as httpOnly cookie (XSS-proof) + return token for API clients
        response = JSONResponse({
            "status": "ok",
            "token": token,
            "username": result["username"],
            "roles": result["roles"],
            "expires_in": 7200,
        })
        response.set_cookie(
            key="maop_token", value=token, max_age=7200,
            httponly=True, secure=True, samesite="strict", path="/",
        )
        return response
    except Exception:
        logger.exception("Login error")
        return JSONResponse({"status": "error", "error": "Login failed"}, status_code=401)


@router.post("/api/auth/refresh")
async def auth_refresh(request: Request):
    """Refresh an existing JWT token before it expires.

    Requires a valid (non-expired) token in the Authorization header.
    Returns a new token with the same identity and roles, extended TTL.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            {"status": "error", "error": "Missing or invalid Authorization header"},
            status_code=401,
        )
    token = auth_header[7:]
    try:
        mgr = get_auth_mgr()
        result = mgr.jwt_handler.validate_token(token)
        if not result.authenticated:
            return JSONResponse(
                {"status": "error", "error": result.error or "Token invalid or expired"},
                status_code=401,
            )
        # Issue new token with same identity + roles
        new_token = mgr.jwt_handler.create_token(
            result.identity,
            roles=result.roles,
            ttl_s=7200.0,
        )
        response = JSONResponse({
            "status": "ok",
            "token": new_token,
            "username": result.identity,
            "roles": result.roles or [],
            "expires_in": 7200,
        })
        response.set_cookie(
            key="maop_token", value=new_token, max_age=7200,
            httponly=True, secure=True, samesite="strict", path="/",
        )
        # Revoke old token so it can't be used after refresh
        try:
            mgr.jwt_handler.revoke_token(token)
        except Exception:
            logger.debug('swallowed exception', exc_info=True)
            # best-effort revocation
        return response
    except Exception as exc:
        logger.exception("[auth] Token refresh failed")
        return JSONResponse(
            {"status": "error", "error": f"Refresh failed: {exc}"},
            status_code=500,
        )


@router.post("/api/auth/logout")
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
            mgr = get_auth_mgr()
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
async def auth_register(request: Request) -> Any:
    """Register a new user (admin only)."""
    try:
        _require_admin(request)
        body = await request.json()
        username = body.get("username", "").strip()
        password = body.get("password", "")
        roles = body.get("roles", ["read"])

        if not username or not password:
            return JSONResponse({"status": "error", "error": "Username and password required"}, status_code=400)
        if len(password) < 8:
            return JSONResponse({"status": "error", "error": "Password must be at least 8 characters"}, status_code=400)

        db_path = get_db_path("auth")
        if not db_path.exists():
            get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, _db_register_user, str(db_path), username, password, roles
        )
        if result["status"] == "ok":
            logger.info("[auth] New user registered: %s (roles: %s)", username, roles)
        return result
    except Exception:
        logger.exception("[auth] Registration failed")
        return JSONResponse({"status": "error", "error": "Registration failed"}, status_code=400)


@router.get("/api/auth/users")
async def auth_users(request: Request) -> Any:
    """List all users (admin only)."""
    try:
        _require_admin(request)
        db_path = get_db_path("auth")
        if not db_path.exists():
            get_auth_mgr()
        users = await asyncio.get_running_loop().run_in_executor(
            None, _db_list_users, str(db_path)
        )
        return {"status": "ok", "users": users}
    except Exception:
        logger.exception("[auth] List users failed")
        return JSONResponse({"status": "error", "error": "Failed to list users"}, status_code=500)


@router.delete("/api/auth/users/{username}")
async def auth_delete_user(username: str, request: Request) -> Any:
    """Delete a user (admin only, cannot delete admin)."""
    try:
        _require_admin(request)
        if username == "admin":
            return JSONResponse({"status": "error", "error": "Cannot delete admin user"}, status_code=403)
        db_path = get_db_path("auth")
        return await asyncio.get_running_loop().run_in_executor(
            None, _db_delete_user, str(db_path), username
        )
    except Exception:
        logger.exception("[auth] Delete user %s failed", username)
        return JSONResponse({"status": "error", "error": "Failed to delete user"}, status_code=500)


@router.put("/api/auth/users/{username}")
async def auth_update_user(username: str, request: Request) -> Any:
    """Update user roles, enabled status, or password (admin only)."""
    try:
        _require_admin(request)
        body = await request.json()
        db_path = get_db_path("auth")
        return await asyncio.get_running_loop().run_in_executor(
            None, _db_update_user, str(db_path), username, body
        )
    except Exception:
        logger.exception("[auth] User update failed")
        return JSONResponse({"status": "error", "error": "Update failed"}, status_code=500)
