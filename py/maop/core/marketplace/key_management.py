"""Marketplace key management — distribution, rotation, and revocation.

Provides three capabilities for Marketplace package signing keys:

  * **Key distribution** — developers upload Ed25519 public keys; the
    platform stores them keyed by ``tool_id``.  Multiple active keys
    per tool are supported (for rotation overlap).
  * **Key rotation** — registers a new key and schedules the old key
    for automatic expiry after a configurable grace period.  During
    the grace period both keys remain valid (overlap), allowing a
    smooth transition without invalidating in-flight packages.
  * **Key revocation / blacklisting** — compromised keys are added to
    a blacklist; :meth:`KeyManagement.is_blacklisted` is consulted
    during signature verification to reject signatures from known-bad
    keys.

Storage: SQLite tables ``marketplace_keys`` and
``marketplace_key_blacklist`` via
:func:`maop.core.backends.db_utils.sqlite_connect`.

Usage
-----
::

    from maop.core.marketplace.key_management import KeyManagement

    km = KeyManagement(db_path="data/maop.db")
    kid = km.register_key("tool-1", pem_text)
    keys = km.get_keys("tool-1")
    new_kid = km.rotate_key("tool-1", new_pem, old_key_id=kid, grace_days=30)
    km.blacklist_key("tool-1", kid, reason="private key leaked")
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from maop.core.backends.db_utils import sqlite_connect

logger = logging.getLogger(__name__)

# ── Status constants ───────────────────────────────────────────────

KEY_STATUS_ACTIVE = "active"
KEY_STATUS_REVOKED = "revoked"
KEY_STATUS_EXPIRED = "expired"

# Default grace period (days) for rotated old keys.
DEFAULT_GRACE_DAYS = 30


# ── Data models ────────────────────────────────────────────────────


class PublicKeyInfo(BaseModel):
    """Metadata for a registered marketplace public key.

    Attributes
    ----------
    key_id : str
        Unique key identifier (e.g. ``"mk-..."``).
    tool_id : str
        The tool/plugin this key belongs to.
    public_key : str
        PEM-encoded public key text.
    created_at : str
        ISO-8601 creation timestamp (UTC).
    expires_at : str | None
        ISO-8601 expiry timestamp, or ``None`` for no expiry.
    status : str
        ``"active"``, ``"revoked"``, or ``"expired"``.
    """

    key_id: str = ""
    tool_id: str = ""
    public_key: str = ""
    created_at: str = ""
    expires_at: str | None = None
    status: str = KEY_STATUS_ACTIVE


class BlacklistEntry(BaseModel):
    """A blacklisted (compromised) key entry.

    Attributes
    ----------
    key_id : str
        The revoked key identifier.
    tool_id : str
        The tool the key belonged to.
    reason : str
        Human-readable revocation reason.
    blacklisted_at : str
        ISO-8601 timestamp of blacklisting (UTC).
    """

    key_id: str = ""
    tool_id: str = ""
    reason: str = ""
    blacklisted_at: str = ""


# ── Errors ─────────────────────────────────────────────────────────


class KeyManagementError(ValueError):
    """Raised on key management failures (invalid input, not found, …)."""


# ── KeyManagement ──────────────────────────────────────────────────


class KeyManagement:
    """Manage marketplace signing keys: distribution, rotation, revocation.

    The store is backed by two SQLite tables:

    * ``marketplace_keys`` — one row per registered public key.
    * ``marketplace_key_blacklist`` — one row per blacklisted key.

    Multiple keys per ``tool_id`` are supported to allow rotation
    overlap (both old and new keys valid during a grace period).
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialise the key store, creating tables if needed.

        Parameters
        ----------
        db_path : str | Path
            Path to the SQLite database file. Parent directories are
            created automatically.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Schema ────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables and indices if they do not already exist."""
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS marketplace_keys (
                    key_id      TEXT PRIMARY KEY,
                    tool_id     TEXT NOT NULL,
                    public_key  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT,
                    status      TEXT NOT NULL DEFAULT 'active'
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mk_tool ON marketplace_keys(tool_id)",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS marketplace_key_blacklist (
                    key_id          TEXT NOT NULL,
                    tool_id         TEXT NOT NULL,
                    reason          TEXT NOT NULL,
                    blacklisted_at  TEXT NOT NULL,
                    PRIMARY KEY (key_id, tool_id)
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mkb_tool ON marketplace_key_blacklist(tool_id)",
            )

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _now_iso() -> str:
        """Current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_key_id() -> str:
        """Generate a fresh unique key ID."""
        return f"mk-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _normalise_public_key(public_key: str | bytes) -> str:
        """Normalise *public_key* to a PEM text string.

        Accepts ``str`` or ``bytes``; rejects empty or wrong-typed input.
        """
        if isinstance(public_key, (bytes, bytearray)):
            return public_key.decode("utf-8")
        if not isinstance(public_key, str):
            raise KeyManagementError(
                f"public_key must be str or bytes, got {type(public_key).__name__}",
            )
        return public_key

    # ── Key distribution ──────────────────────────────────────

    def register_key(
        self,
        tool_id: str,
        public_key: str | bytes,
        key_id: str | None = None,
    ) -> str:
        """Register a public key for a tool.

        Parameters
        ----------
        tool_id : str
            The tool/plugin identifier.
        public_key : str | bytes
            PEM-encoded public key.
        key_id : str | None
            Optional explicit key ID; auto-generated if ``None``.

        Returns
        -------
        str
            The key ID of the newly registered key.

        Raises
        ------
        KeyManagementError
            If *tool_id* is empty or *public_key* is empty/invalid.
        """
        if not tool_id or not tool_id.strip():
            raise KeyManagementError("tool_id must not be empty")
        pem = self._normalise_public_key(public_key)
        if not pem.strip():
            raise KeyManagementError("public_key must not be empty")

        kid = key_id or self._new_key_id()
        now = self._now_iso()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO marketplace_keys
                   (key_id, tool_id, public_key, created_at, expires_at, status)
                   VALUES (?, ?, ?, ?, NULL, 'active')""",
                (kid, tool_id, pem, now),
            )
        logger.info("[key-mgmt] registered key %s for tool %s", kid, tool_id)
        return kid

    def get_keys(self, tool_id: str) -> list[PublicKeyInfo]:
        """Return all keys for a tool (any status).

        Use :meth:`get_active_keys` to fetch only non-expired,
        non-revoked keys.

        Parameters
        ----------
        tool_id : str
            The tool identifier.

        Returns
        -------
        list[PublicKeyInfo]
            All registered keys for the tool, ordered by creation time.
        """
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT key_id, tool_id, public_key, created_at, expires_at, status
                   FROM marketplace_keys
                   WHERE tool_id = ?
                   ORDER BY created_at""",
                (tool_id,),
            ).fetchall()
        return [self._row_to_public_key_info(r) for r in rows]

    def get_active_keys(self, tool_id: str) -> list[PublicKeyInfo]:
        """Return only active, non-expired keys for a tool.

        A key is considered active when:

        * ``status = 'active'``, **and**
        * ``expires_at`` is ``NULL`` or in the future.

        Parameters
        ----------
        tool_id : str
            The tool identifier.

        Returns
        -------
        list[PublicKeyInfo]
            Active, non-expired keys, ordered by creation time.
        """
        now = self._now_iso()
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT key_id, tool_id, public_key, created_at, expires_at, status
                   FROM marketplace_keys
                   WHERE tool_id = ? AND status = 'active'
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at""",
                (tool_id, now),
            ).fetchall()
        return [self._row_to_public_key_info(r) for r in rows]

    @staticmethod
    def _row_to_public_key_info(row: Any) -> PublicKeyInfo:
        """Convert a sqlite3.Row to a PublicKeyInfo."""
        return PublicKeyInfo(
            key_id=row["key_id"],
            tool_id=row["tool_id"],
            public_key=row["public_key"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=row["status"],
        )

    # ── Key rotation ──────────────────────────────────────────

    def rotate_key(
        self,
        tool_id: str,
        new_public_key: str | bytes,
        old_key_id: str,
        grace_days: int = DEFAULT_GRACE_DAYS,
    ) -> str:
        """Rotate a tool's signing key.

        Registers *new_public_key* as a new active key and schedules
        *old_key_id* for automatic expiry after *grace_days* days.
        During the grace period both keys are valid (overlap), allowing
        a smooth transition without invalidating in-flight packages.

        Parameters
        ----------
        tool_id : str
            The tool identifier.
        new_public_key : str | bytes
            PEM-encoded new public key.
        old_key_id : str
            The key ID to retire.
        grace_days : int
            Days until the old key expires (default 30).  ``0`` means
            the old key expires immediately.

        Returns
        -------
        str
            The new key ID.

        Raises
        ------
        KeyManagementError
            If *grace_days* is negative, *new_public_key* is empty, or
            the old key is not found / does not belong to *tool_id*.
        """
        if grace_days < 0:
            raise KeyManagementError(f"grace_days must be >= 0, got {grace_days}")

        pem = self._normalise_public_key(new_public_key)
        if not pem.strip():
            raise KeyManagementError("new_public_key must not be empty")
        if not tool_id or not tool_id.strip():
            raise KeyManagementError("tool_id must not be empty")

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=grace_days)).isoformat()
        new_kid = self._new_key_id()

        with sqlite_connect(self.db_path) as conn:
            # Verify old key exists and belongs to this tool.
            row = conn.execute(
                """SELECT 1 FROM marketplace_keys
                   WHERE key_id = ? AND tool_id = ?""",
                (old_key_id, tool_id),
            ).fetchone()
            if row is None:
                raise KeyManagementError(
                    f"old key {old_key_id!r} not found for tool {tool_id!r}",
                )
            # Register the new key (active, no expiry).
            conn.execute(
                """INSERT INTO marketplace_keys
                   (key_id, tool_id, public_key, created_at, expires_at, status)
                   VALUES (?, ?, ?, ?, NULL, 'active')""",
                (new_kid, tool_id, pem, now.isoformat()),
            )
            # Schedule the old key's expiry (status stays 'active' so it
            # remains valid until expires_at passes).
            conn.execute(
                """UPDATE marketplace_keys
                   SET expires_at = ?
                   WHERE key_id = ?""",
                (expires_at, old_key_id),
            )
        logger.info(
            "[key-mgmt] rotated tool %s: old=%s expires %s, new=%s",
            tool_id, old_key_id, expires_at, new_kid,
        )
        return new_kid

    def revoke_key(self, tool_id: str, key_id: str) -> bool:
        """Revoke a key (set status to ``'revoked'``).

        Unlike :meth:`blacklist_key`, this does **not** add the key to
        the blacklist — it simply marks the key as no longer active.
        Use :meth:`blacklist_key` for compromised keys that must be
        rejected during verification.

        Parameters
        ----------
        tool_id : str
            The tool identifier.
        key_id : str
            The key ID to revoke.

        Returns
        -------
        bool
            ``True`` if the key was revoked by this call, ``False`` if
            the key was not found or already revoked.
        """
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE marketplace_keys
                   SET status = 'revoked'
                   WHERE key_id = ? AND tool_id = ? AND status != 'revoked'""",
                (key_id, tool_id),
            )
            revoked = cursor.rowcount > 0
        if revoked:
            logger.info("[key-mgmt] revoked key %s for tool %s", key_id, tool_id)
        return revoked

    # ── Blacklist ─────────────────────────────────────────────

    def blacklist_key(self, tool_id: str, key_id: str, reason: str) -> bool:
        """Add a key to the blacklist (compromised key).

        The blacklist is an **independent** layer from
        :meth:`revoke_key`: a blacklisted key remains ``status='active'``
        in ``marketplace_keys`` but is rejected during signature
        verification via :meth:`is_blacklisted`.  This separation lets
        callers distinguish planned retirement (:meth:`revoke_key`)
        from emergency compromise handling (blacklist).

        Parameters
        ----------
        tool_id : str
            The tool identifier.
        key_id : str
            The key ID to blacklist.
        reason : str
            Human-readable reason (e.g. ``"private key leaked"``).

        Returns
        -------
        bool
            ``True`` if the key was newly blacklisted by this call,
            ``False`` if it was already blacklisted.

        Raises
        ------
        KeyManagementError
            If *reason* is empty.
        """
        if not reason or not reason.strip():
            raise KeyManagementError("reason must not be empty")

        now = self._now_iso()
        with sqlite_connect(self.db_path) as conn:
            # Idempotent: check if already blacklisted.
            existing = conn.execute(
                """SELECT 1 FROM marketplace_key_blacklist
                   WHERE key_id = ? AND tool_id = ?""",
                (key_id, tool_id),
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """INSERT INTO marketplace_key_blacklist
                   (key_id, tool_id, reason, blacklisted_at)
                   VALUES (?, ?, ?, ?)""",
                (key_id, tool_id, reason, now),
            )
        logger.warning(
            "[key-mgmt] blacklisted key %s for tool %s: %s",
            key_id, tool_id, reason,
        )
        return True

    def is_blacklisted(self, tool_id: str, key_id: str) -> bool:
        """Check whether a key is blacklisted.

        Parameters
        ----------
        tool_id : str
            The tool identifier.
        key_id : str
            The key ID to check.

        Returns
        -------
        bool
            ``True`` if the key is in the blacklist.
        """
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT 1 FROM marketplace_key_blacklist
                   WHERE key_id = ? AND tool_id = ?""",
                (key_id, tool_id),
            ).fetchone()
        return row is not None

    def get_blacklist(self, tool_id: str) -> list[BlacklistEntry]:
        """Return all blacklist entries for a tool.

        Parameters
        ----------
        tool_id : str
            The tool identifier.

        Returns
        -------
        list[BlacklistEntry]
            Blacklist entries for the tool, ordered by blacklisted time.
        """
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT key_id, tool_id, reason, blacklisted_at
                   FROM marketplace_key_blacklist
                   WHERE tool_id = ?
                   ORDER BY blacklisted_at""",
                (tool_id,),
            ).fetchall()
        return [
            BlacklistEntry(
                key_id=r["key_id"],
                tool_id=r["tool_id"],
                reason=r["reason"],
                blacklisted_at=r["blacklisted_at"],
            )
            for r in rows
        ]