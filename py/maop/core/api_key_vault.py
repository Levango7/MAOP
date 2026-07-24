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
        # P2-11 fix: when root_dir is already "data" (default), don't
        # double-nest to data/data/.enc_key. Use _root directly if it
        # ends with "data", otherwise append "data" subdir.
        if self._root.name == "data":
            self._key_path = self._root / ".enc_key"
        else:
            self._key_path = self._root / "data" / ".enc_key"
        self._fernet: Any = None
        self._master_key: bytes | None = None
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
            self._master_key = key

    def _resolve_key_path(self) -> Path:
        """Resolve the encryption key file path.

        Priority:
          1. MAOP_KEY_FILE env var (explicit key file location, separate from ciphertext)
          2. data/.enc_key (default, backward compatible)

        Allowing the key file to live outside the ciphertext directory is a
        production-safety improvement: the encrypted SQLite DB and the
        master key should not co-reside in the same folder.
        """
        key_file = os.environ.get("MAOP_KEY_FILE", "").strip()
        if key_file:
            return Path(key_file)
        return self._key_path

    def _load_or_create_key(self) -> bytes | None:
        env_key = os.environ.get("MAOP_KEY", "").strip()
        if env_key:
            # Try interpreting MAOP_KEY as a raw 32-byte key encoded with
            # urlsafe base64 (no padding). Re-encode to canonical Fernet form.
            try:
                key = base64.urlsafe_b64decode(env_key)
                if len(key) == 32:
                    return base64.urlsafe_b64encode(key)
            except Exception as exc:
                logger.debug("[api_key_vault] MAOP_KEY is not a 32-byte base64 value: %s", exc)
            # Try interpreting MAOP_KEY as a canonical 44-char Fernet key
            # (urlsafe base64 of 32 bytes; input may omit '=' padding).
            try:
                base64.urlsafe_b64decode(env_key + "=" * (-len(env_key) % 4))
                if len(env_key) == 44:
                    return env_key.encode()
                logger.warning(
                    "[api_key_vault] MAOP_KEY decoded but length %d != 44; falling through to key file",
                    len(env_key),
                )
            except Exception as exc:
                logger.warning(
                    "[api_key_vault] MAOP_KEY set but could not be decoded: %s; falling through to key file",
                    exc,
                )

        key_path = self._resolve_key_path()
        if key_path.exists():
            try:
                return key_path.read_bytes().strip()
            except Exception as exc:
                logger.warning("[api_key_vault] Failed to read key file %s: %s", key_path, exc)

        try:
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            try:
                os.chmod(str(key_path), 0o600)
            except Exception:
                pass
            logger.info("[api_key_vault] Generated new encryption key at %s", key_path)
            return key
        except Exception as exc:
            logger.warning("[api_key_vault] Failed to generate encryption key: %s", exc)
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

    def rotate_master_key(self, new_key: bytes | None = None) -> bool:
        """Rotate the master encryption key.

        Re-encrypts all stored secrets with the new key atomically.
        Returns True on success, False on failure.

        The previous key is backed up to ``<key_path>.bak.<timestamp>`` before
        the new key is persisted. If re-encryption fails, the in-memory Fernet
        and the database are left unchanged (the DB transaction rolls back).
        """
        from datetime import datetime, timezone

        if self._fernet is None:
            logger.error("[api_key_vault] Cannot rotate key: cryptography not initialised")
            return False

        old_fernet = self._fernet
        old_key = self._master_key

        # Generate a new key if one was not supplied.
        if new_key is None:
            try:
                from cryptography.fernet import Fernet as _Fernet
                new_key = _Fernet.generate_key()
            except Exception as exc:
                logger.error("[api_key_vault] Key rotation failed: cannot generate key: %s", exc)
                return False
        elif isinstance(new_key, str):
            new_key = new_key.encode()

        # Validate the new key by constructing a Fernet before touching data.
        try:
            from cryptography.fernet import Fernet
            new_fernet = Fernet(new_key)
        except Exception as exc:
            logger.error("[api_key_vault] Key rotation failed: invalid new key: %s", exc)
            return False

        # Read all existing rows.
        try:
            with sqlite_connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT provider, encrypted_key FROM api_keys"
                ).fetchall()
        except Exception as exc:
            logger.error("[api_key_vault] Key rotation failed: cannot read secrets: %s", exc)
            return False

        # Decrypt with the old key and re-encrypt with the new key.
        # Any failure aborts the whole rotation (no partial state).
        reencrypted: list[tuple[str, str]] = []
        for row in rows:
            provider = row["provider"]
            try:
                plaintext = old_fernet.decrypt(row["encrypted_key"].encode()).decode()
            except Exception as exc:
                logger.error(
                    "[api_key_vault] Key rotation aborted: cannot decrypt provider '%s': %s",
                    provider, exc,
                )
                return False
            new_cipher = new_fernet.encrypt(plaintext.encode()).decode()
            reencrypted.append((provider, new_cipher))

        # Persist re-encrypted rows in a single atomic transaction.
        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite_connect(self._db_path) as conn:
                for provider, cipher in reencrypted:
                    conn.execute(
                        "UPDATE api_keys SET encrypted_key=?, updated_at=? WHERE provider=?",
                        (cipher, now, provider),
                    )
        except Exception as exc:
            logger.error("[api_key_vault] Key rotation failed: DB update error: %s", exc)
            return False

        # Back up the previous key, then persist the new key to the key file.
        key_path = self._resolve_key_path()
        if old_key is not None:
            try:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = key_path.with_suffix(key_path.suffix + f".bak.{stamp}")
                backup_path.write_bytes(old_key)
                logger.info("[api_key_vault] Backed up previous key to %s", backup_path)
            except Exception as exc:
                logger.warning("[api_key_vault] Could not back up previous key: %s", exc)

        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(new_key)
            try:
                os.chmod(str(key_path), 0o600)
            except Exception:
                pass
        except Exception as exc:
            # DB is already committed with the new key, so we must keep the
            # new Fernet in memory or the DB becomes unreadable to this process.
            logger.warning(
                "[api_key_vault] Key rotation: DB re-encrypted but could not persist "
                "new key file (%s). The new key is in memory only; update MAOP_KEY_FILE "
                "or restore from backup before restart.",
                exc,
            )
            self._fernet = new_fernet
            self._master_key = new_key
            return True

        # If MAOP_KEY env var is set, the persisted file is shadowed on restart.
        if os.environ.get("MAOP_KEY", "").strip():
            logger.warning(
                "[api_key_vault] MAOP_KEY env var is set; the rotated key has been "
                "written to %s but will be ignored on restart until MAOP_KEY is updated.",
                key_path,
            )

        self._fernet = new_fernet
        self._master_key = new_key
        logger.info(
            "[api_key_vault] Master key rotated successfully (%d secrets re-encrypted)",
            len(reencrypted),
        )
        return True
