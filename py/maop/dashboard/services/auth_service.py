"""Auth & Security service layer — authentication, SSO, and API key management.

Extracted from the auth / sso / api_keys routers (§ARCH-H2) so the router
layer only does parameter parsing + permission check + service call +
response formatting + error handling.

The service is intentionally framework-agnostic: it does not import
FastAPI and can be unit-tested or invoked from CLI/CI without an HTTP
context.  Business errors are raised as plain Python exceptions
(``UsernameAlreadyExists``, ``UserNotFound``, ``PasswordTooShort``) and
the router layer is responsible for translating them into HTTPException
responses with the appropriate status codes.

Modules covered:
    - auth.py          → password hashing, AuthManager singleton, user CRUD,
                         login rate limiting
    - sso.py           → SSOProviderRegistry / SSOManager singletons
    - api_keys.py      → ApiKeyManager resolution helper
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3  # noqa: F401
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Project root ──────────────────────────────────────────────────
# Resolved dynamically from routers.state so test fixtures that
# monkeypatch ``state.MAOP_ROOT`` are picked up automatically.
def _get_maop_root() -> Path:
    from maop.dashboard.routers.state import MAOP_ROOT
    return MAOP_ROOT


# ════════════════════════════════════════════════════════════════════
# Business exceptions (framework-agnostic — no HTTP status codes here)
# ════════════════════════════════════════════════════════════════════


class AuthError(Exception):
    """Base error for auth service operations."""


class UsernameAlreadyExists(AuthError):
    """Raised when registering a username that already exists (→ 409)."""


class UserNotFound(AuthError):
    """Raised when a user is not found (→ 404)."""


class PasswordTooShort(AuthError):
    """Raised when a password doesn't meet minimum length (→ 400)."""


# ════════════════════════════════════════════════════════════════════
# Auth configuration
# ════════════════════════════════════════════════════════════════════

from maop.core.backends.db_utils import get_db_path, sqlite_connect
from maop.core.security.auth import APIKeyStore, AuthConfig, AuthManager, JWTConfig, load_jwt_secret

# P1-4 fix: JWT TTL from env var, avoid hardcoding 7200.
# M6 fix (Phase R5): OWASP 2023 recommends 600k iterations for PBKDF2-HMAC-SHA256
_AUTH_PBKDF2_ITERATIONS = 600_000
_JWT_TTL_S = float(os.getenv("MAOP_JWT_TTL_S", "7200"))

# P0-4: read auth_enabled / tls_enabled from settings.py (Pydantic Settings)
from maop.config.settings import get_settings as _get_settings

_settings = _get_settings()
auth_enabled = _settings.auth_enabled
tls_enabled = _settings.tls_enabled

_auth_mgr: AuthManager | None = None


# ════════════════════════════════════════════════════════════════════
# Password helpers
# ════════════════════════════════════════════════════════════════════


def hash_password(password: str) -> str:
    """Hash a password for dashboard users using PBKDF2-HMAC-SHA256."""

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _AUTH_PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_AUTH_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
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


def password_needs_rehash(stored_hash: str) -> bool:
    return not stored_hash.startswith("pbkdf2_sha256$")


# ════════════════════════════════════════════════════════════════════
# Auth manager singleton
# ════════════════════════════════════════════════════════════════════


def get_auth_mgr() -> AuthManager:
    """Lazy-init AuthManager singleton. Called by lifespan and endpoints."""
    global _auth_mgr
    if _auth_mgr is None:
        jwt_secret = load_jwt_secret(_get_maop_root() / "data")

        cfg = AuthConfig(
            enabled=True,
            jwt=JWTConfig(secret=jwt_secret, default_ttl_s=_JWT_TTL_S),
        )
        db_path = get_db_path("auth")
        _auth_mgr = AuthManager(
            config=cfg,
            key_store=APIKeyStore(db_path=str(db_path)),
        )
        _ensure_default_user()
    return _auth_mgr


def _users_has_tenant_column(conn: Any) -> bool:
    """``users`` 表当前是否已有 ``tenant_id`` 列（供写路径退化使用）。"""
    try:
        return "tenant_id" in {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
    except Exception:  # pragma: no cover
        return False


def _migrate_users_columns(conn: Any) -> None:
    """给存量 ``users`` 表补上后加的列（幂等）。

    与 :meth:`maop.core.security.api_key_manager.ApiKeyManager._migrate_columns`
    同一模式：读 ``PRAGMA table_info`` 拿已有列，缺的列再 ``ALTER TABLE ADD
    COLUMN``。缺省值必须是空串，这样**存量用户升级后租户仍为 ""**，
    其行为与升级前完全一致（配额 / RBAC / 合规都按"未分配租户"处理）。
    """
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    except Exception as exc:  # pragma: no cover - 极端情况下表不可读
        logger.warning("[auth] 无法读取 users 表结构，跳过列迁移: %s", exc)
        return
    new_cols = [("tenant_id", "TEXT NOT NULL DEFAULT ''")]
    for col, decl in new_cols:
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
                logger.info("[auth] users 表已补充列: %s", col)
            except Exception as exc:
                # 重复列 / 权限不足等：记 warning 而不是让启动崩掉——
                # 缺该列只会让租户链路退化，不应阻断整个认证服务。
                logger.warning("[auth] users 表补充列 %s 失败: %s", col, exc)


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
                    enabled INTEGER NOT NULL DEFAULT 1,
                    tenant_id TEXT NOT NULL DEFAULT ''
                )
            """)
            # 存量库补列。模式照抄 maop.core.security.api_key_manager
            # 的 _migrate_columns——api_keys.tenant_id 就是这么加的，
            # 不另造机制。
            _migrate_users_columns(conn)
            existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if existing == 0:
                # H7 fix: support Docker secrets standard (MAOP_ADMIN_PASSWORD_FILE).
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
                    env = os.environ.get("MAOP_ENV", "").strip().lower()
                    if env == "production":
                        raise RuntimeError(
                            "SECURITY: MAOP_ADMIN_PASSWORD must be set explicitly in "
                            "production (MAOP_ENV=production). Refusing to start with a "
                            "random, non-persisted admin password."
                        )
                    pwd_file_path = db_path.parent / "admin_password_once.txt"
                    pwd_file_path.write_text(admin_pwd, encoding="utf-8")
                    try:
                        pwd_file_path.chmod(0o600)
                    except OSError:
                        pass  # Windows
                    logger.warning(
                        "MAOP_ADMIN_PASSWORD not set — generated a ONE-TIME random "
                        "admin password. It has been written to %s (mode 0o600). "
                        "Set MAOP_ADMIN_PASSWORD to persist it across restarts.",
                        pwd_file_path,
                    )
                pwd_hash = hash_password(admin_pwd)
                conn.execute(
                    "INSERT INTO users (username, password_hash, roles, created_at, enabled) VALUES (?, ?, ?, ?, 1)",
                    ("admin", pwd_hash, '["admin","read","write","execute"]', time.time()),
                )
                logger.info("[auth] Default admin user created (password from MAOP_ADMIN_PASSWORD env)")
    except Exception as exc:
        logger.warning("[auth] Failed to create default user: %s", exc)


# ════════════════════════════════════════════════════════════════════
# Sync DB helpers (for run_in_executor)
# ════════════════════════════════════════════════════════════════════


def db_login_user(db_path_str: str, username: str, password: str) -> Any:
    """Sync: validate user credentials, return result dict."""

    with sqlite_connect(db_path_str) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND enabled = 1",
            (username,),
        ).fetchone()

    if row is None:
        return {"status": "error", "error": "Invalid credentials"}

    stored_hash = row["password_hash"]
    if not verify_password(password, stored_hash):
        return {"status": "error", "error": "Invalid credentials"}

    if password_needs_rehash(stored_hash):
        with sqlite_connect(db_path_str) as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(password), username),
            )

    roles = json.loads(row["roles"])
    # 租户随登录结果返回，供 routers/auth.py 写入 JWT 的 "tenant" claim。
    # 用 keys() 判存在性而非直接取：列迁移失败时（见 _migrate_users_columns）
    # 缺列应退化为"未分配租户"，而不是让登录 500。
    tenant_id = ""
    try:
        if "tenant_id" in row:
            tenant_id = row["tenant_id"] or ""
    except Exception:  # pragma: no cover - 非 Row 类型的兜底
        tenant_id = ""
    return {"status": "ok", "username": username, "roles": roles, "tenant_id": tenant_id}


def db_register_user(
    db_path_str: str,
    username: str,
    password: str,
    roles: list,
    tenant_id: str = "",
) -> dict:
    """Sync: register a new user.

    Args:
        tenant_id: 租户归属。缺省 ``""``（未分配）——保持与存量用户一致，
            未显式指定时不会意外获得隔离。

    Raises ``UsernameAlreadyExists`` if the username is taken.
    Returns a plain business-data dict on success.
    """

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise UsernameAlreadyExists("Username already exists")

        pwd_hash = hash_password(password)
        # 列可能不存在（迁移失败时），此时退化：不带 tenant_id 插入。
        cols = "username, password_hash, roles, created_at, enabled"
        # 注意 enabled 原为 SQL 字面量 1，改为动态列数后必须显式补上，
        # 否则会出现"5 列 4 值"的错位。
        vals: tuple = (username, pwd_hash, json.dumps(roles), time.time(), 1)
        if _users_has_tenant_column(conn):
            cols += ", tenant_id"
            vals = (*vals, tenant_id or "")
        conn.execute(
            f"INSERT INTO users ({cols}) VALUES ({', '.join('?' * len(vals))})",
            vals,
        )

    return {"status": "ok", "username": username, "roles": roles, "tenant_id": tenant_id or ""}


def db_list_users(db_path_str: str) -> list:
    """Sync: list all users."""

    with sqlite_connect(db_path_str) as conn:
        if _users_has_tenant_column(conn):
            rows = conn.execute(
                "SELECT username, roles, created_at, enabled, tenant_id "
                "FROM users ORDER BY created_at"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT username, roles, created_at, enabled FROM users ORDER BY created_at"
            ).fetchall()

    out = []
    for r in rows:
        item = {"username": r["username"], "roles": json.loads(r["roles"]),
                "created_at": r["created_at"], "enabled": bool(r["enabled"])}
        try:
            item["tenant_id"] = r["tenant_id"] or ""
        except Exception:  # pragma: no cover - 缺列时退化为未分配
            item["tenant_id"] = ""
        out.append(item)
    return out


def db_delete_user(db_path_str: str, username: str) -> dict:
    """Sync: delete a user.

    Raises ``UserNotFound`` if the user doesn't exist.
    """

    with sqlite_connect(db_path_str) as conn:
        result = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        deleted = result.rowcount > 0

    if not deleted:
        raise UserNotFound("User not found")
    return {"status": "ok", "message": f"User {username} deleted"}


def db_update_user(db_path_str: str, username: str, body: dict) -> dict:
    """Sync: update user roles, enabled, or password.

    Raises ``UserNotFound`` if the user doesn't exist.
    Raises ``PasswordTooShort`` if password is shorter than 8 chars.
    """

    with sqlite_connect(db_path_str) as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            raise UserNotFound("User not found")
        if "roles" in body:
            conn.execute("UPDATE users SET roles = ? WHERE username = ?", (json.dumps(body["roles"]), username))
        if "enabled" in body:
            conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (1 if body["enabled"] else 0, username))
        if "password" in body:
            if not isinstance(body["password"], str) or len(body["password"]) < 8:
                raise PasswordTooShort("Password must be at least 8 characters")
            pwd_hash = hash_password(body["password"])
            conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pwd_hash, username))

    return {"status": "ok", "message": f"User {username} updated"}


# ════════════════════════════════════════════════════════════════════
# Login rate limiting (SQLite-backed, multi-instance safe)
# ════════════════════════════════════════════════════════════════════

login_failures_lock = threading.Lock()
MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 900.0
_MAX_TRACKED_USERS = 10_000  # P1-18 fix: prevent unbounded growth
_MAX_TRACKED_IPS = 10_000  # H6 fix: prevent unbounded growth for IP tracking
_login_failures_table_ready = False


def login_failures_db_path() -> str:
    """登录限流 SQLite 路径（与 auth.db 同目录，独立文件避免锁竞争）。"""
    return str(get_db_path("auth").parent / "login_failures.db")


def ensure_login_failures_table() -> None:
    """幂等创建登录限流表。首次调用后置位标志，后续跳过。"""
    global _login_failures_table_ready
    if _login_failures_table_ready:
        return
    with sqlite_connect(login_failures_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_failures (
                key TEXT NOT NULL,
                kind TEXT NOT NULL,          -- 'user' | 'ip'
                fail_times TEXT NOT NULL,    -- JSON array of wall-clock timestamps
                updated_at REAL NOT NULL,    -- 最近一次失败时间（LRU 淘汰依据）
                PRIMARY KEY (key, kind)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lf_kind ON login_failures(kind)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lf_updated ON login_failures(updated_at)"
        )
    _login_failures_table_ready = True


def db_get_login_failures(key: str, kind: str, now: float) -> list[float]:
    """从 SQLite 读取并过滤过期的失败时间戳（wall clock）。"""
    with sqlite_connect(login_failures_db_path()) as conn:
        row = conn.execute(
            "SELECT fail_times FROM login_failures WHERE key = ? AND kind = ?",
            (key, kind),
        ).fetchone()
    if row is None:
        return []
    try:
        times = json.loads(row["fail_times"])
    except (json.JSONDecodeError, TypeError):
        return []
    return [t for t in times if now - t < LOCKOUT_SECONDS]


def db_record_login_failure(key: str, kind: str, now: float) -> None:
    """记录一次失败登录，含过期清理与 LRU 淘汰（防止无限增长）。

    P1-7 fix: LRU 淘汰策略——按 updated_at 升序淘汰最久未活动的记录，
    预填的静态记录最先被逐出，而刚登录失败的受害者记录因最新而保留。
    """
    max_tracked = _MAX_TRACKED_USERS if kind == "user" else _MAX_TRACKED_IPS
    with sqlite_connect(login_failures_db_path()) as conn:
        row = conn.execute(
            "SELECT fail_times FROM login_failures WHERE key = ? AND kind = ?",
            (key, kind),
        ).fetchone()
        if row:
            try:
                times = json.loads(row["fail_times"])
            except (json.JSONDecodeError, TypeError):
                times = []
        else:
            times = []
        # 过期过滤 + 追加本次失败
        times = [t for t in times if now - t < LOCKOUT_SECONDS]
        times.append(now)
        conn.execute(
            "INSERT OR REPLACE INTO login_failures (key, kind, fail_times, updated_at) VALUES (?, ?, ?, ?)",
            (key, kind, json.dumps(times), now),
        )
        # 过期清理：删除整个锁定窗口外未再失败的记录
        conn.execute(
            "DELETE FROM login_failures WHERE kind = ? AND updated_at < ?",
            (kind, now - LOCKOUT_SECONDS),
        )
        # LRU 淘汰：超限时按 updated_at 升序删除最旧记录（保留当前 key）
        count = conn.execute(
            "SELECT COUNT(*) FROM login_failures WHERE kind = ?", (kind,)
        ).fetchone()[0]
        if count > max_tracked:
            excess = count - max_tracked
            conn.execute(
                """DELETE FROM login_failures WHERE (key, kind) IN (
                       SELECT key, kind FROM login_failures
                       WHERE kind = ? AND key != ?
                       ORDER BY updated_at ASC LIMIT ?
                   )""",
                (kind, key, excess),
            )


def db_clear_login_failures(key: str, kind: str) -> None:
    """登录成功后清除该 key 的失败记录。"""
    with sqlite_connect(login_failures_db_path()) as conn:
        conn.execute(
            "DELETE FROM login_failures WHERE key = ? AND kind = ?",
            (key, kind),
        )


# ════════════════════════════════════════════════════════════════════
# SSO singletons (registry + legacy single-IdP manager)
# ════════════════════════════════════════════════════════════════════

_registry: Any = None
_sso_manager: Any = None  # 向后兼容：单 IdP 模式
_registry_lock = threading.Lock()
_sso_manager_lock = threading.Lock()


def get_sso_registry() -> Any:
    """获取 SSOProviderRegistry 单例。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                from maop.enterprise.sso_registry import SSOProviderRegistry
                _registry = SSOProviderRegistry()
                # PRD NFR-C03：启动时从环境变量导入单 IdP 配置（向后兼容）
                try:
                    from maop.enterprise.sso_store import import_env_provider_if_present
                    import_env_provider_if_present(_registry.store)
                except Exception as exc:  # pragma: no cover — 防御性
                    logger.warning("[sso] Failed to import env-based provider: %s", exc)
    return _registry


def get_sso_manager() -> Any:
    """向后兼容：单 IdP 模式从环境变量加载 SSOManager。"""
    global _sso_manager
    if _sso_manager is None:
        with _sso_manager_lock:
            if _sso_manager is None:
                from maop.enterprise.sso import SSOConfig, SSOManager, SSOProvider
                provider = SSOProvider(os.getenv("MAOP_SSO_PROVIDER", "oidc"))
                config = SSOConfig(
                    provider=provider,
                    client_id=os.getenv("MAOP_SSO_CLIENT_ID", ""),
                    client_secret=os.getenv("MAOP_SSO_CLIENT_SECRET", ""),
                    authorize_url=os.getenv("MAOP_SSO_AUTHORIZE_URL", ""),
                    token_url=os.getenv("MAOP_SSO_TOKEN_URL", ""),
                    userinfo_url=os.getenv("MAOP_SSO_USERINFO_URL", ""),
                    redirect_uri=os.getenv("MAOP_SSO_REDIRECT_URI", ""),
                    scopes=[s.strip() for s in os.getenv("MAOP_SSO_SCOPES", "openid profile email").split(",")],
                )
                _sso_manager = SSOManager(config=config)
    return _sso_manager


# ════════════════════════════════════════════════════════════════════
# API Key manager resolution helper
# ════════════════════════════════════════════════════════════════════


def resolve_api_key_manager(app_state: Any) -> Any:
    """Return the ApiKeyManager from app.state, falling back to the global singleton.

    ``app_state`` is a FastAPI ``app.state``-like object (only ``getattr``
    is used, so any object with an optional ``api_key_manager`` attribute
    works — including ``None``-ish mocks in tests).
    """
    mgr = getattr(app_state, "api_key_manager", None)
    if mgr is not None:
        return mgr
    from maop.core.security.api_key_manager import get_api_key_manager
    return get_api_key_manager()