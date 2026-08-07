"""MAOP Key-Value Store - Lightweight SQLite-backed KV storage.

Not etcd/Consul - a simple, fast, local KV store for:
  - Agent state persistence
  - Configuration caching
  - Feature flags
  - Session data
  - Coordination metadata

Features:
  - TTL (time-to-live) with automatic expiration
  - Namespaces for key isolation
  - Atomic CAS (compare-and-swap) for coordination
  - Bulk get/set for efficiency
  - Watch/poll for key changes
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import ConnectionPool, get_pool

logger = logging.getLogger(__name__)

# ── Models ──────────────────────────────────────────────────────


class KVEntry(BaseModel):
    """A key-value entry with metadata."""
    key: str = ""
    value: Any = None
    namespace: str = "default"
    ttl: float | None = None       # Seconds until expiration, None = no TTL
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1               # Incremented on each update


class KVStats(BaseModel):
    """Statistics about the KV store."""
    total_keys: int = 0
    namespaces: list[str] = Field(default_factory=list)
    expired_keys: int = 0
    db_size_bytes: int = 0


class CASResult(BaseModel):
    """Result of a compare-and-swap operation."""
    success: bool = False
    current_value: Any = None
    current_version: int = 0


# ── KVStore ─────────────────────────────────────────────────────

class KVStore:
    """SQLite-backed key-value store with TTL and namespaces."""

    def __init__(self, db_path: str | Path = "data/kv_store.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: ConnectionPool = get_pool(self.db_path)
        self._init_db()

    def _init_db(self) -> None:
        conn = self._pool.acquire()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT NOT NULL,
                    namespace TEXT NOT NULL DEFAULT 'default',
                    value TEXT NOT NULL,
                    ttl_expires REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (key, namespace)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kv_namespace
                ON kv_store(namespace)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kv_expires
                ON kv_store(ttl_expires)
                WHERE ttl_expires IS NOT NULL
            """)
            conn.commit()
        finally:
            self._pool.release(conn)

    def close(self) -> None:
        self._pool.close_all()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def _now(self) -> float:
        return time.time()

    def _prune_expired(self) -> int:
        conn = self._pool.acquire()
        try:
            now = self._now()
            cursor = conn.execute(
                "DELETE FROM kv_store WHERE ttl_expires IS NOT NULL AND ttl_expires <= ?",
                (now,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            self._pool.release(conn)

    # ── Core operations ─────────────────────────────────────

    def get(self, key: str, *, namespace: str = "default", default: Any = None) -> Any:
        self._prune_expired()
        conn = self._pool.acquire()
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ? AND namespace = ?",
                (key, namespace),
            ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]
        finally:
            self._pool.release(conn)

    def set(
        self,
        key: str,
        value: Any,
        *,
        namespace: str = "default",
        ttl: float | None = None,
    ) -> KVEntry:
        conn = self._pool.acquire()
        try:
            now = self._now()
            value_json = json.dumps(value, ensure_ascii=False, default=str)
            expires = now + ttl if ttl is not None else None

            existing = conn.execute(
                "SELECT version FROM kv_store WHERE key = ? AND namespace = ?",
                (key, namespace),
            ).fetchone()

            version = (existing["version"] + 1) if existing else 1

            conn.execute("""
                INSERT INTO kv_store (key, namespace, value, ttl_expires, created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key, namespace) DO UPDATE SET
                    value = excluded.value,
                    ttl_expires = excluded.ttl_expires,
                    updated_at = excluded.updated_at,
                    version = excluded.version
            """, (key, namespace, value_json, expires, now, now, version))
            conn.commit()

            return KVEntry(
                key=key,
                value=value,
                namespace=namespace,
                ttl=ttl,
                created_at=now,
                updated_at=now,
                version=version,
            )
        finally:
            self._pool.release(conn)

    def delete(self, key: str, *, namespace: str = "default") -> bool:
        conn = self._pool.acquire()
        try:
            cursor = conn.execute(
                "DELETE FROM kv_store WHERE key = ? AND namespace = ?",
                (key, namespace),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._pool.release(conn)

    def exists(self, key: str, *, namespace: str = "default") -> bool:
        self._prune_expired()
        conn = self._pool.acquire()
        try:
            row = conn.execute(
                "SELECT 1 FROM kv_store WHERE key = ? AND namespace = ?",
                (key, namespace),
            ).fetchone()
            return row is not None
        finally:
            self._pool.release(conn)

    # ── Bulk operations ─────────────────────────────────────

    def get_many(
        self,
        keys: list[str],
        *,
        namespace: str = "default",
    ) -> dict[str, Any]:
        if not keys:
            return {}
        self._prune_expired()
        conn = self._pool.acquire()
        try:
            placeholders = ",".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT key, value FROM kv_store WHERE key IN ({placeholders}) AND namespace = ?",
                (*keys, namespace),
            ).fetchall()
            result: dict[str, Any] = {}
            for row in rows:
                try:
                    result[row["key"]] = json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    result[row["key"]] = row["value"]
            return result
        finally:
            self._pool.release(conn)

    def set_many(
        self,
        items: dict[str, Any],
        *,
        namespace: str = "default",
        ttl: float | None = None,
    ) -> int:
        conn = self._pool.acquire()
        try:
            now = self._now()
            expires = now + ttl if ttl is not None else None
            count = 0

            for key, value in items.items():
                value_json = json.dumps(value, ensure_ascii=False, default=str)
                existing = conn.execute(
                    "SELECT version FROM kv_store WHERE key = ? AND namespace = ?",
                    (key, namespace),
                ).fetchone()
                version = (existing["version"] + 1) if existing else 1

                conn.execute("""
                    INSERT INTO kv_store (key, namespace, value, ttl_expires, created_at, updated_at, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key, namespace) DO UPDATE SET
                        value = excluded.value,
                        ttl_expires = excluded.ttl_expires,
                        updated_at = excluded.updated_at,
                        version = excluded.version
                """, (key, namespace, value_json, expires, now, now, version))
                count += 1

            conn.commit()
            return count
        finally:
            self._pool.release(conn)

    # ── CAS (Compare-And-Swap) ──────────────────────────────

    def cas(
        self,
        key: str,
        expected_version: int,
        new_value: Any,
        *,
        namespace: str = "default",
        ttl: float | None = None,
    ) -> CASResult:
        conn = self._pool.acquire()
        try:
            now = self._now()
            value_json = json.dumps(new_value, ensure_ascii=False, default=str)
            expires = now + ttl if ttl is not None else None

            row = conn.execute(
                "SELECT value, version FROM kv_store WHERE key = ? AND namespace = ?",
                (key, namespace),
            ).fetchone()

            if row is None:
                return CASResult(success=False, current_value=None, current_version=0)

            if row["version"] != expected_version:
                try:
                    current_val = json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    current_val = row["value"]
                return CASResult(
                    success=False,
                    current_value=current_val,
                    current_version=row["version"],
                )

            new_version = expected_version + 1
            conn.execute("""
                UPDATE kv_store
                SET value = ?, ttl_expires = ?, updated_at = ?, version = ?
                WHERE key = ? AND namespace = ? AND version = ?
            """, (value_json, expires, now, new_version, key, namespace, expected_version))
            conn.commit()

            return CASResult(success=True, current_value=new_value, current_version=new_version)
        finally:
            self._pool.release(conn)

    # ── Namespace operations ────────────────────────────────

    def list_keys(self, *, namespace: str = "default", prefix: str = "") -> list[str]:
        self._prune_expired()
        conn = self._pool.acquire()
        try:
            if prefix:
                rows = conn.execute(
                    "SELECT key FROM kv_store WHERE namespace = ? AND key LIKE ? ORDER BY key",
                    (namespace, prefix + "%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key FROM kv_store WHERE namespace = ? ORDER BY key",
                    (namespace,),
                ).fetchall()
            return [row["key"] for row in rows]
        finally:
            self._pool.release(conn)

    def list_namespaces(self) -> list[str]:
        conn = self._pool.acquire()
        try:
            rows = conn.execute(
                "SELECT DISTINCT namespace FROM kv_store ORDER BY namespace"
            ).fetchall()
            return [row["namespace"] for row in rows]
        finally:
            self._pool.release(conn)

    def delete_namespace(self, namespace: str) -> int:
        conn = self._pool.acquire()
        try:
            cursor = conn.execute(
                "DELETE FROM kv_store WHERE namespace = ?",
                (namespace,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            self._pool.release(conn)

    # ── Stats ───────────────────────────────────────────────

    def stats(self) -> KVStats:
        self._prune_expired()
        conn = self._pool.acquire()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM kv_store").fetchone()["c"]
            ns_rows = conn.execute(
                "SELECT DISTINCT namespace FROM kv_store"
            ).fetchall()
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return KVStats(
                total_keys=total,
                namespaces=[r["namespace"] for r in ns_rows],
                db_size_bytes=db_size,
            )
        finally:
            self._pool.release(conn)

