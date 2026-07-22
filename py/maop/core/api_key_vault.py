"""MAOP API Key Vault — Encrypted storage for LLM provider API keys.

Uses Fernet symmetric encryption. The encryption key is loaded from:
  1. MAOP_KEY environment variable (preferred)
  2. data/.enc_key file (auto-generated on first use)

Usage::

    from maop.core.api_key_vault import ApiKeyVault

    vault = ApiKeyVault(root_dir="/path/to/MAOP")
    vault.store("openai", "sk-...")
    key = vault.retrieve("openai")
    vault.delete("openai")
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Optional, cast

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class ApiKeyVault:
    """Encrypted API key storage for LLM providers."""

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("api_key_vault")
        self._key_path = self._root / "data" / ".enc_key"
        self._fernet: Any = None
        self._init_db()
        self._init_encryption()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    provider TEXT PRIMARY KEY,
                    encrypted_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT DEFAULT ''
                )
            """)

    def _init_encryption(self) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            logger.error("[api_key_vault] cryptography not installed, keys stored in plaintext")
            self._fernet = None
            return

        key = self._load_or_create_key()
        if key:
            self._fernet = Fernet(key)

    def _load_or_create_key(self) -> bytes | None:
        env_key = os.environ.get("MAOP_KEY", "").strip()
        if env_key:
            try:
                key = base64.urlsafe_b64decode(env_key)
                if len(key) == 32:
                    return base64.urlsafe_b64encode(key)
            except Exception:
                pass
            try:
                _ = base64.urlsafe_b64decode(env_key + "=" * (-len(env_key) % 4))
                return env_key.encode() if len(env_key) == 44 else None
            except Exception:
                pass

        if self._key_path.exists():
            try:
                return self._key_path.read_bytes().strip()
            except Exception:
                pass

        try:
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key_path.write_bytes(key)
            try:
                os.chmod(str(self._key_path), 0o600)
            except Exception:
                pass
            return key
        except Exception:
            logger.warning("[api_key_vault] Failed to generate encryption key")
            return None

    def store(self, provider: str, api_key: str) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        encrypted = self._encrypt(api_key)
        with sqlite_connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT provider FROM api_keys WHERE provider=?", (provider,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE api_keys SET encrypted_key=?, updated_at=? WHERE provider=?",
                    (encrypted, now, provider),
                )
            else:
                conn.execute(
                    "INSERT INTO api_keys (provider, encrypted_key, created_at) VALUES (?,?,?)",
                    (provider, encrypted, now),
                )
        logger.info("[api_key_vault] Stored key for provider '%s'", provider)

    def retrieve(self, provider: str) -> Optional[str]:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_key FROM api_keys WHERE provider=?", (provider,)
            ).fetchone()
        if row is None:
            return None
        return self._decrypt(row["encrypted_key"])

    def delete(self, provider: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM api_keys WHERE provider=?", (provider,))
        return cursor.rowcount > 0

    def list_providers(self) -> list[str]:
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT provider FROM api_keys ORDER BY provider").fetchall()
        return [r["provider"] for r in rows]

    def _encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise RuntimeError("cryptography library required for API key encryption")
        return cast(str, self._fernet.encrypt(plaintext.encode()).decode())

    def _decrypt(self, ciphertext: str) -> str:
        if self._fernet is None:
            raise RuntimeError("cryptography library required for API key encryption")
        return cast(str, self._fernet.decrypt(ciphertext.encode()).decode())
