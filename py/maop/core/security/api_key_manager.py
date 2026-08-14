"""API Key Manager — full lifecycle management for MAOP API keys.

Extends the legacy :class:`~maop.core.security.auth.APIKeyStore` with:

* **Structured key format** — ``maop_{key_id}_{secret}`` where ``key_id`` is a
  short stable identifier (used in URLs / logs) and ``secret`` is a
  high-entropy random token. Only the SHA-256 hash of the full key is
  persisted; the plaintext is returned exactly once at creation time.
* **Scopes** — fine-grained permission tokens (e.g. ``["read", "write"]``)
  checked at authentication time via :meth:`ApiKeyManager.check_scope`.
* **IP allow-list** — optional JSON array of CIDR strings; when non-empty,
  only requests from matching IPs are accepted.
* **Sliding-window rate limiting** — per-key request count tracked in
  ``api_key_usage``; a configurable window (default 60 s) and limit
  (``rate_limit`` column, requests-per-window) are enforced in
  :meth:`ApiKeyManager.check_rate_limit`.
* **Usage statistics** — every authenticated request records a row in
  ``api_key_usage`` (timestamp, endpoint, method, IP, status, latency).
* **Revocation** — soft revoke (``enabled = 0`` + ``revoked_at`` /
  ``revoked_by``) preserving audit history.

The manager reuses the existing ``api_keys`` SQLite table (extending it
with additional columns via ``ALTER TABLE``) so the legacy
``APIKeyStore`` continues to work unchanged.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_db_path, get_pool

logger = logging.getLogger(__name__)


# ── Pydantic models ────────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    """Request body for creating a new API key."""

    name: str = Field(..., min_length=1, max_length=100, description="Human-readable key name")
    scopes: list[str] = Field(default_factory=list, description="Permission scopes, e.g. ['read','write']")
    roles: list[str] = Field(default_factory=list, description="Legacy role tags forwarded to AuthMiddleware")
    rate_limit: int = Field(default=0, ge=0, description="Max requests per 60s window; 0 = unlimited")
    ip_whitelist: list[str] = Field(default_factory=list, description="CIDR strings; empty = allow all")
    ttl_s: float | None = Field(default=None, ge=1, description="TTL in seconds; None = never expires")
    description: str = Field(default="", max_length=500)
    tenant_id: str = Field(default="", max_length=64)
    created_by: str = Field(default="", max_length=64)


class ApiKeyUpdate(BaseModel):
    """Request body for updating editable metadata of an API key (all optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    scopes: list[str] | None = Field(default=None)
    rate_limit: int | None = Field(default=None, ge=0)
    ip_whitelist: list[str] | None = Field(default=None)
    description: str | None = Field(default=None, max_length=500)


class ApiKeyResponse(BaseModel):
    """API key metadata returned by list/get endpoints (never includes plaintext)."""

    key_id: str
    name: str
    scopes: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    rate_limit: int = 0
    ip_whitelist: list[str] = Field(default_factory=list)
    created_at: float
    expires_at: float | None = None
    last_used_at: float | None = None
    enabled: bool = True
    description: str = ""
    tenant_id: str = ""
    created_by: str = ""
    revoked_at: float | None = None
    revoked_by: str = ""


class ApiKeyCreateResult(BaseModel):
    """Response immediately after key creation — includes the plaintext key once."""

    key_id: str
    plaintext_key: str
    key: ApiKeyResponse


class ApiKeyUsageRecord(BaseModel):
    """A single usage event row."""

    id: int
    key_id: str
    timestamp: float
    endpoint: str
    method: str
    ip_address: str
    status_code: int
    latency_ms: float


class ApiKeyUsageResponse(BaseModel):
    """Paginated usage statistics for a key."""

    key_id: str
    total: int
    limit: int
    offset: int
    records: list[ApiKeyUsageRecord]
    window_seconds: int
    requests_in_window: int
    rate_limit: int


class ApiKeyValidationResult(BaseModel):
    """Outcome of :meth:`ApiKeyManager.validate_key`."""

    valid: bool = False
    key_id: str = ""
    name: str = ""
    scopes: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    error: str = ""
    rate_limit_exceeded: bool = False


# ── Constants ──────────────────────────────────────────────────────

_KEY_PREFIX = "maop"
_KEY_ID_LEN = 8          # chars in the stable identifier
_SECRET_LEN = 32         # chars in the random secret
_DEFAULT_RATE_WINDOW_S = 60
_MAX_USAGE_ROWS = 100_000  # soft cap; older rows pruned periodically


# ── Manager ────────────────────────────────────────────────────────


class ApiKeyManager:
    """SQLite-backed API key manager with scopes, IP allow-list, rate limit, usage stats.

    Thread-safe via the underlying :class:`ConnectionPool` and an internal
    lock around the in-memory rate-limit windows.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        rate_window_s: int = _DEFAULT_RATE_WINDOW_S,
    ) -> None:
        if db_path is None:
            db_path = get_db_path("auth")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        self.rate_window_s = max(1, int(rate_window_s))
        # In-memory sliding-window cache: key_id -> deque[timestamps].
        # The DB is the source of truth; this cache avoids a COUNT(*) per
        # request under load. Periodically reconciled in _record_usage.
        self._rl_lock = threading.Lock()
        self._rl_windows: dict[str, list[float]] = {}
        self._init_db()

    # ── Schema ─────────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = self._pool.acquire()
        try:
            # Base table (created by legacy APIKeyStore as well; IF NOT EXISTS
            # makes this idempotent).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash     TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    roles        TEXT NOT NULL DEFAULT '[]',
                    created_at   REAL NOT NULL,
                    expires_at   REAL,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    rate_limit   INTEGER NOT NULL DEFAULT 0,
                    key_id       TEXT,
                    scopes       TEXT NOT NULL DEFAULT '[]',
                    ip_whitelist TEXT NOT NULL DEFAULT '[]',
                    last_used_at REAL,
                    tenant_id    TEXT NOT NULL DEFAULT '',
                    created_by   TEXT NOT NULL DEFAULT '',
                    description  TEXT NOT NULL DEFAULT '',
                    revoked_at   REAL,
                    revoked_by   TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Migrate legacy tables: add any missing columns so an existing
            # DB created by APIKeyStore is upgraded in place.
            self._migrate_columns(conn)
            # Unique index on key_id (nullable; legacy rows have NULL).
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id) WHERE key_id IS NOT NULL"
            )
            # Usage table.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_key_usage (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id      TEXT NOT NULL,
                    timestamp   REAL NOT NULL,
                    endpoint    TEXT NOT NULL DEFAULT '',
                    method      TEXT NOT NULL DEFAULT '',
                    ip_address  TEXT NOT NULL DEFAULT '',
                    status_code INTEGER NOT NULL DEFAULT 0,
                    latency_ms  REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON api_key_usage(key_id, timestamp)"
            )
            conn.commit()
        finally:
            self._pool.release(conn)

    @staticmethod
    def _migrate_columns(conn: Any) -> None:
        """Add columns introduced by this manager to a legacy ``api_keys`` table."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        new_cols = [
            ("key_id", "TEXT"),
            ("scopes", "TEXT NOT NULL DEFAULT '[]'"),
            ("ip_whitelist", "TEXT NOT NULL DEFAULT '[]'"),
            ("last_used_at", "REAL"),
            ("tenant_id", "TEXT NOT NULL DEFAULT ''"),
            ("created_by", "TEXT NOT NULL DEFAULT ''"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("revoked_at", "REAL"),
            ("revoked_by", "TEXT NOT NULL DEFAULT ''"),
        ]
        for col, decl in new_cols:
            if col not in existing:
                conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {decl}")

    # ── Key generation & hashing ───────────────────────────────────

    @staticmethod
    def _hash_key(key: str) -> str:
        """SHA-256 hex digest of the plaintext key."""
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def _generate_key(cls) -> tuple[str, str]:
        """Return ``(key_id, plaintext_key)``.

        ``plaintext_key`` has the form ``maop_{key_id}_{secret}``. Both
        ``key_id`` and ``secret`` are hex strings so they never contain
        the ``_`` separator.
        """
        key_id = secrets.token_hex(_KEY_ID_LEN // 2)[:_KEY_ID_LEN]
        secret = secrets.token_hex(_SECRET_LEN // 2)[:_SECRET_LEN]
        return key_id, f"{_KEY_PREFIX}_{key_id}_{secret}"

    # ── CRUD ───────────────────────────────────────────────────────

    def create_key(self, req: ApiKeyCreate) -> ApiKeyCreateResult:
        """Create a new API key. The plaintext key is returned exactly once."""
        key_id, plaintext = self._generate_key()
        key_hash = self._hash_key(plaintext)
        now = time.time()
        expires_at = now + req.ttl_s if req.ttl_s is not None else None

        conn = self._pool.acquire()
        try:
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_hash, name, roles, created_at, expires_at, enabled, rate_limit,
                    key_id, scopes, ip_whitelist, last_used_at, tenant_id, created_by, description
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    key_hash,
                    req.name,
                    json.dumps(req.roles),
                    now,
                    expires_at,
                    req.rate_limit,
                    key_id,
                    json.dumps(req.scopes),
                    json.dumps(req.ip_whitelist),
                    req.tenant_id,
                    req.created_by,
                    req.description,
                ),
            )
            conn.commit()
        finally:
            self._pool.release(conn)

        logger.info("[api_keys] Created key id=%s name=%s scopes=%s", key_id, req.name, req.scopes)
        return ApiKeyCreateResult(
            key_id=key_id,
            plaintext_key=plaintext,
            key=ApiKeyResponse(
                key_id=key_id,
                name=req.name,
                scopes=req.scopes,
                roles=req.roles,
                rate_limit=req.rate_limit,
                ip_whitelist=req.ip_whitelist,
                created_at=now,
                expires_at=expires_at,
                enabled=True,
                description=req.description,
                tenant_id=req.tenant_id,
                created_by=req.created_by,
            ),
        )

    def _row_to_response(self, row: Any) -> ApiKeyResponse:
        return ApiKeyResponse(
            key_id=row["key_id"] or "",
            name=row["name"],
            scopes=json.loads(row["scopes"] or "[]"),
            roles=json.loads(row["roles"] or "[]"),
            rate_limit=row["rate_limit"],
            ip_whitelist=json.loads(row["ip_whitelist"] or "[]"),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            enabled=bool(row["enabled"]),
            description=row["description"] or "",
            tenant_id=row["tenant_id"] or "",
            created_by=row["created_by"] or "",
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"] or "",
        )

    def list_keys(self, *, tenant_id: str = "") -> list[ApiKeyResponse]:
        """List all keys (optionally filtered by tenant)."""
        conn = self._pool.acquire()
        try:
            if tenant_id:
                rows = conn.execute(
                    "SELECT * FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM api_keys ORDER BY created_at DESC"
                ).fetchall()
        finally:
            self._pool.release(conn)
        return [self._row_to_response(r) for r in rows]

    def get_key(self, key_id: str) -> ApiKeyResponse | None:
        conn = self._pool.acquire()
        try:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
        finally:
            self._pool.release(conn)
        return self._row_to_response(row) if row else None

    def revoke_key(self, key_id: str, *, revoked_by: str = "") -> bool:
        """Soft-revoke a key by ``key_id``. Returns True if a row was updated."""
        conn = self._pool.acquire()
        try:
            cur = conn.execute(
                """
                UPDATE api_keys
                   SET enabled = 0, revoked_at = ?, revoked_by = ?
                 WHERE key_id = ? AND enabled = 1
                """,
                (time.time(), revoked_by, key_id),
            )
            conn.commit()
            updated = cur.rowcount > 0
        finally:
            self._pool.release(conn)
        if updated:
            logger.info("[api_keys] Revoked key id=%s by=%s", key_id, revoked_by)
        return updated

    def update_key(self, key_id: str, req: ApiKeyUpdate) -> ApiKeyResponse | None:
        """Update editable metadata of an API key. Returns updated response or None if not found.

        Only fields explicitly set in ``req`` are written; ``None`` means "leave unchanged".
        """
        updates: dict[str, Any] = {}
        if req.name is not None:
            updates["name"] = req.name
        if req.scopes is not None:
            updates["scopes"] = json.dumps(req.scopes)
        if req.rate_limit is not None:
            updates["rate_limit"] = req.rate_limit
        if req.ip_whitelist is not None:
            updates["ip_whitelist"] = json.dumps(req.ip_whitelist)
        if req.description is not None:
            updates["description"] = req.description

        if updates:
            # 字段名来自代码内硬编码的 dict key（非用户输入），无注入风险。
            set_clause = ", ".join(f"{col} = ?" for col in updates)
            conn = self._pool.acquire()
            try:
                cur = conn.execute(
                    f"UPDATE api_keys SET {set_clause} WHERE key_id = ?",
                    (*updates.values(), key_id),
                )
                conn.commit()
                if cur.rowcount == 0:
                    return None
            finally:
                self._pool.release(conn)
            logger.info("[api_keys] Updated key id=%s fields=%s", key_id, list(updates))
        return self.get_key(key_id)

    def delete_key(self, key_id: str) -> bool:
        """Hard-delete a key and its usage rows. Returns True if a row was deleted."""
        conn = self._pool.acquire()
        try:
            cur = conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))
            conn.execute("DELETE FROM api_key_usage WHERE key_id = ?", (key_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            self._pool.release(conn)
        if deleted:
            logger.info("[api_keys] Deleted key id=%s", key_id)
        return deleted

    # ── Validation ─────────────────────────────────────────────────

    def validate_key(
        self,
        plaintext: str,
        *,
        client_ip: str = "",
        required_scope: str = "",
    ) -> ApiKeyValidationResult:
        """Validate a plaintext API key.

        Checks (in order): format → hash lookup → enabled → expiry → IP
        allow-list → scope → rate limit. Does **not** record usage; call
        :meth:`record_usage` separately after the request completes.
        """
        # Format check — rejects clearly malformed keys cheaply.
        if not plaintext or not plaintext.startswith(f"{_KEY_PREFIX}_"):
            return ApiKeyValidationResult(valid=False, error="Invalid key format")

        key_hash = self._hash_key(plaintext)
        conn = self._pool.acquire()
        try:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        finally:
            self._pool.release(conn)

        if row is None:
            return ApiKeyValidationResult(valid=False, error="Invalid API key")
        if not row["enabled"]:
            return ApiKeyValidationResult(valid=False, error="API key revoked", key_id=row["key_id"] or "")
        if row["expires_at"] is not None and time.time() > row["expires_at"]:
            return ApiKeyValidationResult(valid=False, error="API key expired", key_id=row["key_id"] or "")

        key_id = row["key_id"] or ""
        scopes = json.loads(row["scopes"] or "[]")
        roles = json.loads(row["roles"] or "[]")

        # IP allow-list
        ip_list = json.loads(row["ip_whitelist"] or "[]")
        if ip_list and client_ip:
            if not self._ip_allowed(client_ip, ip_list):
                return ApiKeyValidationResult(
                    valid=False, error="IP not allowed", key_id=key_id, scopes=scopes, roles=roles
                )

        # Scope check (supports "*" wildcard via check_scope)
        if required_scope and not self.check_scope(scopes, required_scope):
            return ApiKeyValidationResult(
                valid=False,
                error=f"Missing scope: {required_scope}",
                key_id=key_id,
                scopes=scopes,
                roles=roles,
            )

        # Rate limit (sliding window)
        rate_limit = int(row["rate_limit"] or 0)
        if rate_limit > 0 and not self.check_rate_limit(key_id, rate_limit):
            return ApiKeyValidationResult(
                valid=False,
                error="Rate limit exceeded",
                key_id=key_id,
                scopes=scopes,
                roles=roles,
                rate_limit_exceeded=True,
            )

        return ApiKeyValidationResult(
            valid=True, key_id=key_id, name=row["name"], scopes=scopes, roles=roles
        )

    @staticmethod
    def _ip_allowed(client_ip: str, allow_list: list[str]) -> bool:
        """Check ``client_ip`` against a CIDR/string allow-list."""
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        for entry in allow_list:
            try:
                if "/" in entry:
                    if ip in ipaddress.ip_network(entry, strict=False):
                        return True
                else:
                    if ip == ipaddress.ip_address(entry):
                        return True
            except ValueError:
                continue
        return False

    # ── Rate limiting (sliding window) ─────────────────────────────

    def check_rate_limit(self, key_id: str, rate_limit: int) -> bool:
        """Return True if the key is within its rate limit for the current window.

        Uses an in-memory cache of timestamps; falls back to DB count if the
        cache is cold. Does **not** consume a slot — call :meth:`record_usage`
        to record the actual request which increments the window.
        """
        now = time.monotonic()
        cutoff = now - self.rate_window_s
        with self._rl_lock:
            window = self._rl_windows.get(key_id)
            if window is None:
                # Cold cache: hydrate from DB (best-effort).
                window = self._hydrate_window(key_id, now)
                self._rl_windows[key_id] = window
            # Drop expired entries.
            window[:] = [t for t in window if t > cutoff]
            return len(window) < rate_limit

    def _hydrate_window(self, key_id: str, now_mono: float) -> list[float]:
        """Best-effort hydration of the in-memory window from the DB.

        Converts wall-clock timestamps from ``api_key_usage`` to monotonic
        approximations. Only called on cold cache; precision loss here is
        acceptable because the window is a soft limit.
        """
        conn = self._pool.acquire()
        try:
            rows = conn.execute(
                "SELECT timestamp FROM api_key_usage WHERE key_id = ? ORDER BY timestamp DESC LIMIT ?",
                (key_id, _MAX_USAGE_ROWS),
            ).fetchall()
        finally:
            self._pool.release(conn)
        if not rows:
            return []
        now_wall = time.time()
        offset = now_mono - now_wall
        return [r["timestamp"] + offset for r in rows if r["timestamp"] + offset > now_mono - self.rate_window_s]

    # ── Usage recording & stats ────────────────────────────────────

    def record_usage(
        self,
        key_id: str,
        *,
        endpoint: str,
        method: str,
        ip_address: str,
        status_code: int,
        latency_ms: float,
    ) -> None:
        """Append a usage row and update ``last_used_at`` on the key."""
        now = time.time()
        now_mono = time.monotonic()
        conn = self._pool.acquire()
        try:
            conn.execute(
                """
                INSERT INTO api_key_usage
                    (key_id, timestamp, endpoint, method, ip_address, status_code, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key_id, now, endpoint, method, ip_address, status_code, latency_ms),
            )
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (now, key_id),
            )
            conn.commit()
        finally:
            self._pool.release(conn)

        with self._rl_lock:
            window = self._rl_windows.setdefault(key_id, [])
            window.append(now_mono)
            # Cap in-memory window length to avoid unbounded growth.
            if len(window) > _MAX_USAGE_ROWS:
                cutoff = now_mono - self.rate_window_s
                window[:] = [t for t in window if t > cutoff]

    def get_usage(
        self,
        key_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> ApiKeyUsageResponse:
        """Return paginated usage records plus in-window count."""
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        conn = self._pool.acquire()
        try:
            total_row = conn.execute(
                "SELECT COUNT(*) AS n FROM api_key_usage WHERE key_id = ?", (key_id,)
            ).fetchone()
            total = int(total_row["n"]) if total_row else 0

            rows = conn.execute(
                """
                SELECT id, key_id, timestamp, endpoint, method, ip_address, status_code, latency_ms
                  FROM api_key_usage
                 WHERE key_id = ?
                 ORDER BY timestamp DESC
                 LIMIT ? OFFSET ?
                """,
                (key_id, limit, offset),
            ).fetchall()

            # In-window count (DB source of truth).
            window_cutoff = time.time() - self.rate_window_s
            win_row = conn.execute(
                "SELECT COUNT(*) AS n FROM api_key_usage WHERE key_id = ? AND timestamp >= ?",
                (key_id, window_cutoff),
            ).fetchone()
            requests_in_window = int(win_row["n"]) if win_row else 0

            key_row = conn.execute(
                "SELECT rate_limit FROM api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            rate_limit = int(key_row["rate_limit"]) if key_row else 0
        finally:
            self._pool.release(conn)

        records = [
            ApiKeyUsageRecord(
                id=r["id"],
                key_id=r["key_id"],
                timestamp=r["timestamp"],
                endpoint=r["endpoint"],
                method=r["method"],
                ip_address=r["ip_address"],
                status_code=r["status_code"],
                latency_ms=r["latency_ms"],
            )
            for r in rows
        ]
        return ApiKeyUsageResponse(
            key_id=key_id,
            total=total,
            limit=limit,
            offset=offset,
            records=records,
            window_seconds=self.rate_window_s,
            requests_in_window=requests_in_window,
            rate_limit=rate_limit,
        )

    # ── Scope helpers ──────────────────────────────────────────────

    @staticmethod
    def check_scope(scopes: list[str], required: str) -> bool:
        """Return True if ``required`` is granted by ``scopes``.

        The wildcard ``"*"`` grants every scope.
        """
        if not required:
            return True
        if "*" in scopes:
            return True
        return required in scopes

    # ── Maintenance ────────────────────────────────────────────────

    def prune_usage(self, *, keep_last_n: int = _MAX_USAGE_ROWS) -> int:
        """Delete old usage rows, keeping at most ``keep_last_n`` per key.

        Returns the number of rows deleted. Intended to be called by a
        periodic scheduler.
        """
        conn = self._pool.acquire()
        try:
            # Find the cutoff id per key beyond which rows are pruned.
            rows = conn.execute(
                """
                SELECT key_id, MAX(id) AS max_id, COUNT(*) AS cnt
                  FROM api_key_usage
                 GROUP BY key_id
                HAVING cnt > ?
                """,
                (keep_last_n,),
            ).fetchall()
            deleted = 0
            for r in rows:
                cutoff_id = int(r["max_id"]) - keep_last_n
                cur = conn.execute(
                    "DELETE FROM api_key_usage WHERE key_id = ? AND id <= ?",
                    (r["key_id"], cutoff_id),
                )
                deleted += cur.rowcount
            conn.commit()
        finally:
            self._pool.release(conn)
        if deleted:
            logger.info("[api_keys] Pruned %d old usage rows", deleted)
        return deleted

    def close(self) -> None:
        self._pool.close_all()


# ── Module-level singleton (lazy) ──────────────────────────────────

_manager: ApiKeyManager | None = None
_manager_lock = threading.Lock()


def get_api_key_manager() -> ApiKeyManager:
    """Lazy singleton accessor used by routers and middleware."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                db_path = get_db_path("auth")
                window = int(os.environ.get("MAOP_APIKEY_RATE_WINDOW_S", str(_DEFAULT_RATE_WINDOW_S)))
                _manager = ApiKeyManager(db_path=db_path, rate_window_s=window)
    return _manager


def reset_api_key_manager() -> None:
    """Reset the singleton (test helper)."""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close()
        _manager = None