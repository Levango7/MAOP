"""MAOP Auth - API Key and JWT authentication middleware.

Provides authentication for the MAOP Dashboard API:
  - API Key: Simple static key validation (for service-to-service)
  - JWT: JSON Web Token validation (for user-facing requests)

Designed as FastAPI dependency injection compatible.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class AuthResult(BaseModel):
    """Result of an authentication check."""
    authenticated: bool = False
    identity: str = ""          # User/service name
    roles: list[str] = Field(default_factory=list)
    error: str = ""
    expires_at: float = 0.0


class APIKey(BaseModel):
    """An API key entry."""
    key_hash: str = ""          # SHA256 hash of the key (never store plaintext)
    name: str = ""              # Service/user name
    roles: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    expires_at: float | None = None  # None = never expires
    enabled: bool = True
    rate_limit: int = 0         # 0 = use default


class JWTConfig(BaseModel):
    """JWT configuration."""
    secret: str = ""            # HMAC secret (auto-generated if empty)
    algorithm: str = "HS256"
    issuer: str = "MAOP"
    default_ttl_s: float = 3600.0  # 1 hour


class AuthConfig(BaseModel):
    """Overall auth configuration."""
    enabled: bool = True
    api_key_header: str = "X-API-Key"
    jwt_header: str = "Authorization"   # Bearer token
    jwt: JWTConfig = Field(default_factory=JWTConfig)


# ── API Key Store ───────────────────────────────────────────────

class APIKeyStore:
    """SQLite-backed API key store."""

    def __init__(self, db_path: str | Path = "data/auth.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        self._init_db()

    def _init_db(self) -> None:
        conn = self._pool.acquire()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    roles TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    rate_limit INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            self._pool.release(conn)

    @staticmethod
    def _hash_key(key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()

    def create_key(
        self,
        name: str,
        *,
        roles: list[str] | None = None,
        ttl_s: float | None = None,
        rate_limit: int = 0,
    ) -> str:
        """Create a new API key. Returns the plaintext key (shown once)."""
        # Generate random key
        raw_key = hashlib.sha256(
            f"{name}:{time.time()}:{os.urandom(16).hex()}".encode()
        ).hexdigest()[:32]

        key_hash = self._hash_key(raw_key)
        now = time.time()
        expires = now + ttl_s if ttl_s else None


        key_hash = self._hash_key(raw_key)
        now = time.time()
        expires = now + ttl_s if ttl_s else None

        conn = self._pool.acquire()
        try:
            conn.execute("""
                INSERT INTO api_keys (key_hash, name, roles, created_at, expires_at, enabled, rate_limit)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (key_hash, name, json.dumps(roles or []), now, expires, rate_limit))
            conn.commit()
        finally:
            self._pool.release(conn)

        logger.info("[auth] Created API key for: %s", name)
        return raw_key

    def validate_key(self, key: str) -> AuthResult:
        key_hash = self._hash_key(key)
        conn = self._pool.acquire()
        try:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        finally:
            self._pool.release(conn)

        if row is None:
            return AuthResult(authenticated=False, error="Invalid API key")

        if not row["enabled"]:
            return AuthResult(authenticated=False, error="API key disabled")

        # Check expiration
        if row["expires_at"] is not None and time.time() > row["expires_at"]:
            return AuthResult(authenticated=False, error="API key expired")

        return AuthResult(
            authenticated=True,
            identity=row["name"],
            roles=json.loads(row["roles"]),
            expires_at=row["expires_at"] or 0.0,
        )

    def revoke_key(self, name: str) -> bool:
        conn = self._pool.acquire()
        try:
            cursor = conn.execute(
                "UPDATE api_keys SET enabled = 0 WHERE name = ?",
                (name,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._pool.release(conn)

    def list_keys(self) -> list[dict[str, Any]]:
        conn = self._pool.acquire()
        try:
            rows = conn.execute(
                "SELECT key_hash, name, roles, created_at, expires_at, enabled, rate_limit FROM api_keys"
            ).fetchall()
        finally:
            self._pool.release(conn)
        return [
            {
                "name": r["name"],
                "roles": json.loads(r["roles"]),
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "enabled": bool(r["enabled"]),
                "rate_limit": r["rate_limit"],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._pool.close_all()


# ── JWT Handler ─────────────────────────────────────────────────

class JWTHandler:
    """Simple JWT creation and validation (HS256 only).

    No external dependencies - uses HMAC-SHA256 directly.
    """

    def __init__(self, config: JWTConfig | None = None):
        self.config = config or JWTConfig()
        if not self.config.secret:
            self.config.secret = load_jwt_secret()
        # P1 fix: in-memory token revocation blacklist (sig_b64 → exp timestamp)
        self._revoked: dict[str, float] = {}
        # P2-2 fix: persist revocation blacklist across restarts so revoked
        # tokens remain invalid after a server restart.
        root = os.environ.get("MAOP_ROOT_DIR", ".")
        self._revoked_file = Path(root) / "data" / "jwt_revoked.json"
        self._load_revoked()

    def _load_revoked(self) -> None:
        """Load the revocation blacklist from disk (best-effort)."""
        try:
            if self._revoked_file.exists():
                data = json.loads(self._revoked_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    now = time.time()
                    # Only load non-expired entries
                    self._revoked = {
                        str(k): float(v)
                        for k, v in data.items()
                        if isinstance(v, (int, float)) and v > now
                    }
                    if self._revoked:
                        logger.info(
                            "[auth] Loaded %d revoked tokens from %s",
                            len(self._revoked), self._revoked_file,
                        )
        except Exception as exc:
            logger.warning("[auth] Could not load revoked tokens: %s", exc)

    def _save_revoked(self) -> None:
        """Persist the revocation blacklist to disk (best-effort)."""
        try:
            self._revoked_file.parent.mkdir(parents=True, exist_ok=True)
            self._revoked_file.write_text(
                json.dumps(self._revoked), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[auth] Could not persist revoked tokens: %s", exc)

    def revoke_token(self, token: str) -> bool:
        """Add a token to the revocation blacklist. Returns True if revoked."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False
            sig_b64 = parts[2]
            payload = json.loads(self._b64url_decode(parts[1]))
            exp = payload.get("exp", 0)
            if time.time() > exp:
                return False  # already expired
            self._revoked[sig_b64] = exp
            self._save_revoked()
            return True
        except Exception:
            return False

    def _cleanup_revoked(self) -> None:
        """Remove expired entries from the revocation blacklist."""
        now = time.time()
        expired = [sig for sig, exp in self._revoked.items() if exp <= now]
        for sig in expired:
            del self._revoked[sig]
        if expired:
            self._save_revoked()

    def _b64url_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _b64url_decode(self, data: str) -> bytes:
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        return base64.urlsafe_b64decode(data)

    def create_token(
        self,
        identity: str,
        *,
        roles: list[str] | None = None,
        ttl_s: float | None = None,
    ) -> str:
        """Create a JWT token."""
        now = time.time()
        exp = now + (ttl_s or self.config.default_ttl_s)

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.config.issuer,
            "sub": identity,
            "roles": roles or [],
            "iat": now,
            "exp": exp,
        }

        header_b64 = self._b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.config.secret.encode(),
            signing_input.encode(),
            hashlib.sha256,
        ).digest()
        sig_b64 = self._b64url_encode(signature)

        return f"{signing_input}.{sig_b64}"

    def validate_token(self, token: str) -> AuthResult:
        """Validate a JWT token."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return AuthResult(authenticated=False, error="Invalid token format")

            header_b64, payload_b64, sig_b64 = parts

            header_json = self._b64url_decode(header_b64)
            header = json.loads(header_json)
            if header.get("alg") != "HS256":
                return AuthResult(authenticated=False, error=f"Unsupported algorithm: {header.get('alg')} — only HS256 is accepted")

            signing_input = f"{header_b64}.{payload_b64}"
            expected_sig = hmac.new(
                self.config.secret.encode(),
                signing_input.encode(),
                hashlib.sha256,
            ).digest()
            actual_sig = self._b64url_decode(sig_b64)

            if not hmac.compare_digest(expected_sig, actual_sig):
                return AuthResult(authenticated=False, error="Invalid signature")

            # Decode payload
            payload = json.loads(self._b64url_decode(payload_b64))

            # Check expiration
            if time.time() > payload.get("exp", 0):
                return AuthResult(authenticated=False, error="Token expired")

            # Check issuer
            if payload.get("iss") != self.config.issuer:
                return AuthResult(authenticated=False, error="Invalid issuer")

            # #7 fix: validate iat (issued-at), nbf (not-before), sub (subject)
            iat = payload.get("iat")
            if iat is None or not isinstance(iat, (int, float)) or iat > time.time() + 60:
                return AuthResult(authenticated=False, error="Invalid iat (token issued in future or missing)")
            nbf = payload.get("nbf")
            if nbf is not None and time.time() < nbf:
                return AuthResult(authenticated=False, error="Token not yet valid (nbf)")
            if not payload.get("sub"):
                return AuthResult(authenticated=False, error="Token missing subject (sub)")

            # P1 fix: check revocation blacklist
            self._cleanup_revoked()
            if sig_b64 in self._revoked:
                return AuthResult(authenticated=False, error="Token revoked")

            return AuthResult(
                authenticated=True,
                identity=payload.get("sub", ""),
                roles=payload.get("roles", []),
                expires_at=payload.get("exp", 0),
            )
        except Exception as e:
            return AuthResult(authenticated=False, error=str(e))


# ── JWT Secret Loading ──────────────────────────────────────────

def load_jwt_secret(data_dir: str | Path = "data") -> str:
    """Load JWT signing secret with a 3-tier priority:

    1. ``MAOP_JWT_SECRET`` environment variable (highest priority — production)
    2. ``<data_dir>/jwt_secret`` file (persisted across restarts)
    3. Auto-generate a cryptographically random secret and persist it

    The auto-generated file is created with mode 0o600 on POSIX systems.

    Args:
        data_dir: Directory for the ``jwt_secret`` file (default: ``data``).

    Returns:
        The JWT signing secret string (64 hex chars when auto-generated).
    """
    import secrets as _secrets

    # 1) Environment variable
    secret = os.environ.get("MAOP_JWT_SECRET", "").strip()
    if secret:
        logger.debug("[auth] JWT secret loaded from MAOP_JWT_SECRET env var")
        return secret

    # 2) Persisted file
    jwt_file = Path(data_dir) / "jwt_secret"
    if jwt_file.exists():
        file_secret = jwt_file.read_text(encoding="utf-8").strip()
        if file_secret:
            logger.info("[auth] JWT secret loaded from %s", jwt_file)
            return file_secret

    # 3) Auto-generate and persist
    if os.environ.get("MAOP_ENV", "").strip().lower() == "production":
        raise RuntimeError(
            "SECURITY: MAOP_JWT_SECRET environment variable is required in production "
            "(MAOP_ENV=production). Auto-generated secrets are not stable across restarts."
        )

    secret = _secrets.token_hex(32)
    logger.warning(
        "SECURITY: JWT secret auto-generated. "
        "Set MAOP_JWT_SECRET environment variable in production! "
        "Auto-generated secrets are not stable across restarts."
    )
    try:
        jwt_file.parent.mkdir(parents=True, exist_ok=True)
        jwt_file.write_text(secret, encoding="utf-8")
        try:
            jwt_file.chmod(0o600)
        except OSError:
            pass  # Windows
        logger.info("[auth] JWT secret generated and saved to %s", jwt_file)
    except Exception as exc:
        logger.warning("[auth] Could not persist JWT secret to file: %s", exc)
    return secret


# ── Unified Auth ────────────────────────────────────────────────

class AuthManager:
    """Unified authentication manager supporting API keys and JWT."""

    def __init__(
        self,
        config: AuthConfig | None = None,
        key_store: APIKeyStore | None = None,
    ):
        self.config = config or AuthConfig()
        self.key_store = key_store or APIKeyStore()
        self.jwt_handler = JWTHandler(self.config.jwt)

    def authenticate(
        self,
        *,
        api_key: str = "",
        bearer_token: str = "",
    ) -> AuthResult:
        """Authenticate using API key or JWT bearer token.

        Tries API key first, then JWT.
        """
        if not self.config.enabled:
            # Security: when auth is disabled (dev mode), grant guest role only.
            # This prevents accidental admin access in misconfigured deployments.
            logger.warning(
                "Auth disabled — granting guest role (not admin). "
                "Enable auth in production via MAOP_AUTH=1."
            )
            return AuthResult(authenticated=True, identity="anonymous", roles=["guest"])

        # Try API key
        if api_key:
            result = self.key_store.validate_key(api_key)
            if result.authenticated:
                return result

        # Try JWT
        if bearer_token:
            # Strip "Bearer " prefix
            token = bearer_token
            if token.lower().startswith("bearer "):
                token = token[7:]
            result = self.jwt_handler.validate_token(token)
            if result.authenticated:
                return result

        if not api_key and not bearer_token:
            return AuthResult(authenticated=False, error="No credentials provided")

        return AuthResult(authenticated=False, error="Authentication failed")
