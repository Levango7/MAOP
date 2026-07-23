"""Authentication & user management endpoints.

Extracted from server.py for separation of concerns.
Provides: login, logout, register, user CRUD, auth status.
Uses PBKDF2-HMAC-SHA256 for password hashing, JWT for tokens.
"""

from __future__ import annotations

from typing import Any

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .state import MAOP_ROOT

router = APIRouter()

# ── Auth config ────────────────────────────────────────────────────
from maop.core.auth import AuthManager, APIKeyStore, AuthConfig, JWTConfig, load_jwt_secret
from maop.core.db_utils import sqlite_connect

_auth_enabled = os.environ.get("MAOP_AUTH", "0") == "1"
_AUTH_PBKDF2_ITERATIONS = 260_000
_auth_mgr: AuthManager | None = None


# ── Password helpers ───────────────────────────────────────────────
def _hash_password(password: str) -> str:
    """Hash a password for dashboard users using PBKDF2-HMAC-SHA256."""
    import base64
    import hashlib

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _AUTH_PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _AUTH_PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify PBKDF2 hashes only. Legacy unsalted SHA-256 is no longer accepted."""
    import base64
    import hashlib
    import hmac

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
        db_path = MAOP_ROOT / "data" / "auth.db"
        _auth_mgr = AuthManager(
            config=cfg,
            key_store=APIKeyStore(db_path=str(db_path)),
        )
        _ensure_default_user()
    return _auth_mgr


def _ensure_default_user() -> None:
    """Create default admin user on first run if none exists."""
    try:
        import time as _t
        db_path = MAOP_ROOT / "data" / "auth.db"
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
                admin_pwd = os.environ.get("MAOP_ADMIN_PASSWORD", "")
                if not admin_pwd:
                    import secrets
                    admin_pwd = secrets.token_urlsafe(16)
                    # P1-14 fix: do not print password to stderr
                    logger.warning(
                        "MAOP_ADMIN_PASSWORD not set — generated random admin password "
                        "(set MAOP_ADMIN_PASSWORD env var for production)"
                    )
                pwd_hash = _hash_password(admin_pwd)
                conn.execute(
                    "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
                    ("admin", pwd_hash, '["admin","read","write","execute"]', _t.time()),
                )
                logger.info("[auth] Default admin user created (password from MAOP_ADMIN_PASSWORD env)")
    except Exception as exc:
        logger.warning("[auth] Failed to create default user: %s", exc)


# ── Admin guard ────────────────────────────────────────────────────
from maop.core.middleware import require_admin as _require_admin


# ── Sync DB helpers (for run_in_executor) ──────────────────────────
def _db_login_user(db_path_str: str, username: str, password: str) -> dict:
    """Sync: validate user credentials, return result dict."""
    import json as _json

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

    roles = _json.loads(row["roles"])
    return {"status": "ok", "username": username, "roles": roles}


def _db_register_user(db_path_str: str, username: str, password: str, roles: list) -> dict:
    """Sync: register a new user."""
    import time as _t
    import json as _json

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            return JSONResponse({"status": "error", "error": "Username already exists"}, status_code=409)

        pwd_hash = _hash_password(password)
        conn.execute(
            "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
            (username, pwd_hash, _json.dumps(roles), _t.time()),
        )

    return {"status": "ok", "username": username, "roles": roles}


def _db_list_users(db_path_str: str) -> list:
    """Sync: list all users."""
    import json as _json

    with sqlite_connect(db_path_str) as conn:
        rows = conn.execute("SELECT username, roles, created_at, enabled FROM users ORDER BY created_at").fetchall()

    return [{"username": r["username"], "roles": _json.loads(r["roles"]),
             "created_at": r["created_at"], "enabled": bool(r["enabled"])} for r in rows]


def _db_delete_user(db_path_str: str, username: str) -> dict:
    """Sync: delete a user."""
    with sqlite_connect(db_path_str) as conn:
        result = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        deleted = result.rowcount > 0

    if not deleted:
        return JSONResponse({"status": "error", "error": "User not found"}, status_code=404)
    return {"status": "ok", "message": f"User {username} deleted"}


def _db_update_user(db_path_str: str, username: str, body: dict) -> dict:
    """Sync: update user roles, enabled, or password."""
    import json as _json

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            return JSONResponse({"status": "error", "error": "User not found"}, status_code=404)
        if "roles" in body:
            conn.execute("UPDATE users SET roles = ? WHERE username = ?", (_json.dumps(body["roles"]), username))
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
_MAX_LOGIN_FAILURES = 5
_LOCKOUT_SECONDS = 900.0

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
                pass
    return {
        "auth_enabled": _auth_enabled,
        "has_token": has_token,
    }


@router.post("/api/auth/login")
async def auth_login(request: Request) -> Any:
    """Login with username/password, returns JWT token."""
    import time as _time
    try:
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            return JSONResponse({"status": "error", "error": "Username and password required"}, status_code=400)

        now = _time.monotonic()
        failures = _login_failures.get(username, [])
        failures = [t for t in failures if now - t < _LOCKOUT_SECONDS]
        _login_failures[username] = failures
        if len(failures) >= _MAX_LOGIN_FAILURES:
            return JSONResponse({"status": "error", "error": "Account locked. Try again later."}, status_code=429)

        db_path = MAOP_ROOT / "data" / "auth.db"
        if not db_path.exists():
            get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, _db_login_user, str(db_path), username, password
        )

        if result["status"] != "ok":
            _login_failures.setdefault(username, []).append(now)
            return JSONResponse(result, status_code=401)

        mgr = get_auth_mgr()
        token = mgr.jwt_handler.create_token(result["username"], roles=result["roles"], ttl_s=7200.0)

        return {
            "status": "ok",
            "token": token,
            "username": result["username"],
            "roles": result["roles"],
            "expires_in": 7200,
        }
    except Exception as e:
        logger.error("Login error: %s", e, exc_info=True)
        return JSONResponse({"status": "error", "error": "Login failed"}, status_code=401)


@router.post("/api/auth/logout")
async def auth_logout(request: Request) -> Any:
    """Logout - revoke JWT token server-side (P1 fix)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            mgr = get_auth_mgr()
            revoked = mgr.jwt_handler.revoke_token(token)
            if revoked:
                logger.info("[auth] Token revoked via logout")
        except Exception as exc:
            logger.warning("[auth] Failed to revoke token: %s", exc)
    return {"status": "ok", "message": "Token revoked."}


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

        db_path = MAOP_ROOT / "data" / "auth.db"
        if not db_path.exists():
            get_auth_mgr()

        result = await asyncio.get_running_loop().run_in_executor(
            None, _db_register_user, str(db_path), username, password, roles
        )
        if result["status"] == "ok":
            logger.info("[auth] New user registered: %s (roles: %s)", username, roles)
        return result
    except Exception as e:
        logger.error("[auth] Registration failed: %s", e, exc_info=True)
        return JSONResponse({"status": "error", "error": "Registration failed"}, status_code=400)


@router.get("/api/auth/users")
async def auth_users(request: Request) -> Any:
    """List all users (admin only)."""
    try:
        _require_admin(request)
        db_path = MAOP_ROOT / "data" / "auth.db"
        if not db_path.exists():
            get_auth_mgr()
        users = await asyncio.get_running_loop().run_in_executor(
            None, _db_list_users, str(db_path)
        )
        return {"status": "ok", "users": users}
    except Exception as e:
        logger.error("[auth] List users failed: %s", e, exc_info=True)
        return JSONResponse({"status": "error", "error": "Failed to list users"}, status_code=500)


@router.delete("/api/auth/users/{username}")
async def auth_delete_user(username: str, request: Request) -> Any:
    """Delete a user (admin only, cannot delete admin)."""
    try:
        _require_admin(request)
        if username == "admin":
            return JSONResponse({"status": "error", "error": "Cannot delete admin user"}, status_code=403)
        db_path = MAOP_ROOT / "data" / "auth.db"
        return await asyncio.get_running_loop().run_in_executor(
            None, _db_delete_user, str(db_path), username
        )
    except Exception as e:
        logger.error("[auth] Delete user %s failed: %s", username, e, exc_info=True)
        return JSONResponse({"status": "error", "error": "Failed to delete user"}, status_code=500)


@router.put("/api/auth/users/{username}")
async def auth_update_user(username: str, request: Request) -> Any:
    """Update user roles, enabled status, or password (admin only)."""
    try:
        _require_admin(request)
        body = await request.json()
        db_path = MAOP_ROOT / "data" / "auth.db"
        return await asyncio.get_running_loop().run_in_executor(
            None, _db_update_user, str(db_path), username, body
        )
    except Exception as e:
        logger.error("[auth] User update failed: %s", e, exc_info=True)
        return JSONResponse({"status": "error", "error": "Update failed"}, status_code=500)
