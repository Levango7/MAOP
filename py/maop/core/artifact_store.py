"""MAOP Artifact Store — Versioned file snapshots with rollback support.

Provides:
  - Save artifacts (files or content) with version tracking
  - Retrieve any version of an artifact
  - List version history for an artifact
  - Restore/rollback to a previous version
  - Tag versions for easy reference

Usage::

    from maop.core.artifact_store import ArtifactStore

    store = ArtifactStore(root_dir="/path/to/MAOP")
    v1 = store.save("main.py", content="print('hello')")
    v2 = store.save("main.py", content="print('world')")
    history = store.history("main.py")
    restored = store.restore("main.py", version=v1)
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect
from maop.core.safe_writer import safe_write_text

logger = logging.getLogger(__name__)


class ArtifactVersion(BaseModel):
    id: str = ""
    artifact_name: str = ""
    version: int = 1
    content: str = ""
    content_hash: str = ""
    size_bytes: int = 0
    tag: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ArtifactInfo(BaseModel):
    name: str = ""
    latest_version: int = 0
    total_versions: int = 0
    total_size_bytes: int = 0
    created_at: str = ""
    updated_at: str = ""


class ArtifactStore:
    """Versioned artifact storage with SQLite persistence.

    Usage::

        store = ArtifactStore(root_dir="/path/to/MAOP")
        v = store.save("config.yaml", content="key: value")
        content = store.load("config.yaml")
        store.restore("config.yaml", version=1)
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("artifact_store")
        self._blob_dir = self._root / "data" / "artifacts"
        self._ensure_db()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    name TEXT PRIMARY KEY,
                    latest_version INTEGER NOT NULL DEFAULT 0,
                    total_versions INTEGER NOT NULL DEFAULT 0,
                    total_size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    id TEXT PRIMARY KEY,
                    artifact_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    tag TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    blob_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_av_name_version
                ON artifact_versions(artifact_name, version)
            """)

    def _connect(self):
        return sqlite_connect(self._db_path, foreign_keys=False)

    @staticmethod
    def _hash_content(content: str) -> str:
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def save(
        self,
        name: str,
        content: str,
        *,
        tag: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        # 校验 name 防止路径遍历：禁止空值、包含 ".." 或 NUL 字节、以及以路径分隔符开头
        if not name or any(seg in name for seg in ("..", "\x00")) or name.startswith(("/", "\\")):
            raise ValueError(f"Invalid artifact name: {name!r}")
        # 检查解析后路径仍在 blob_dir 内，防止 name 通过符号链接或其他方式逃逸
        candidate = (self._blob_dir / name).resolve()
        try:
            candidate.relative_to(self._blob_dir.resolve())
        except ValueError:
            raise ValueError(f"Artifact name escapes blob dir: {name!r}")

        now = datetime.now(timezone.utc).isoformat()
        content_hash = self._hash_content(content)
        size = len(content.encode("utf-8"))

        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM artifacts WHERE name=?", (name,)).fetchone()
            if existing:
                version = existing["latest_version"] + 1
                conn.execute(
                    "UPDATE artifacts SET latest_version=?, total_versions=total_versions+1, "
                    "total_size_bytes=total_size_bytes+?, updated_at=? WHERE name=?",
                    (version, size, now, name),
                )
            else:
                version = 1
                conn.execute(
                    "INSERT INTO artifacts (name, latest_version, total_versions, total_size_bytes, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (name, 1, 1, size, now, now),
                )

            version_id = f"av-{uuid.uuid4().hex[:10]}"
            blob_path = f"{name}/v{version}"
            blob_file = self._blob_dir / blob_path
            blob_file.parent.mkdir(parents=True, exist_ok=True)
            # 使用 safe_write_text 进行原子写入，防止崩溃导致数据损坏
            safe_write_text(blob_file, content, encoding="utf-8")

            conn.execute(
                "INSERT INTO artifact_versions (id, artifact_name, version, content_hash, size_bytes, tag, metadata, blob_path, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (version_id, name, version, content_hash, size, tag,
                 json.dumps(metadata or {}), str(blob_file), now),
            )

        logger.info("[artifact] Saved %s v%d (hash=%s, tag=%s)", name, version, content_hash, tag)
        return cast(int, version)

    def load(self, name: str, version: int | None = None) -> str | None:
        with self._connect() as conn:
            if version is not None:
                row = conn.execute(
                    "SELECT * FROM artifact_versions WHERE artifact_name=? AND version=?",
                    (name, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM artifact_versions WHERE artifact_name=? ORDER BY version DESC LIMIT 1",
                    (name,),
                ).fetchone()
        if row is None:
            return None
        blob_file = Path(row["blob_path"])
        if blob_file.exists():
            return blob_file.read_text(encoding="utf-8")
        return None

    def history(self, name: str, limit: int = 20) -> list[ArtifactVersion]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, artifact_name, version, content_hash, size_bytes, tag, metadata, created_at "
                "FROM artifact_versions WHERE artifact_name=? ORDER BY version DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        result = []
        for r in rows:
            metadata = {}
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                metadata = json.loads(r["metadata"])
            result.append(ArtifactVersion(
                id=r["id"],
                artifact_name=r["artifact_name"],
                version=r["version"],
                content_hash=r["content_hash"],
                size_bytes=r["size_bytes"],
                tag=r["tag"],
                metadata=metadata,
                created_at=r["created_at"],
            ))
        return result

    def restore(self, name: str, version: int) -> bool:
        content = self.load(name, version=version)
        if content is None:
            return False
        self.save(name, content, tag=f"restore-v{version}")
        logger.info("[artifact] Restored %s to v%d (saved as new version)", name, version)
        return True

    def list_artifacts(self, limit: int = 50) -> list[ArtifactInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ArtifactInfo(**dict(r)) for r in rows]

    def delete_artifact(self, name: str) -> bool:
        # 先收集 blob 路径，再删除 DB 记录，最后清理文件。
        # 顺序：DB 删除 → 文件删除，这样即使文件删除失败也不会产生孤儿 DB 记录
        # （孤儿文件比孤儿 DB 记录安全得多，因为 load() 通过 DB 索引定位文件）。
        with self._connect() as conn:
            versions = conn.execute(
                "SELECT blob_path FROM artifact_versions WHERE artifact_name=?", (name,),
            ).fetchall()
            # 先删 DB 记录
            conn.execute("DELETE FROM artifact_versions WHERE artifact_name=?", (name,))
            cursor = conn.execute("DELETE FROM artifacts WHERE name=?", (name,))
            deleted = cursor.rowcount > 0

        # DB 记录删除成功后再清理 blob 文件（保留 try/except，避免文件系统
        # 错误影响整体删除语义）
        for v in versions:
            try:
                Path(v["blob_path"]).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("[artifact] 清理 blob 文件失败 %s: %s", v["blob_path"], exc)

        return cast(bool, deleted)

    def tag_version(self, name: str, version: int, tag: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE artifact_versions SET tag=? WHERE artifact_name=? AND version=?",
                (tag, name, version),
            )
            return cast(bool, cursor.rowcount > 0)

    def get_by_tag(self, name: str, tag: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT blob_path FROM artifact_versions WHERE artifact_name=? AND tag=? LIMIT 1",
                (name, tag),
            ).fetchone()
        if row is None:
            return None
        blob_file = Path(row["blob_path"])
        if blob_file.exists():
            return blob_file.read_text(encoding="utf-8")
        return None

    def diff_versions(self, name: str, v1: int, v2: int) -> dict[str, Any]:
        c1 = self.load(name, version=v1)
        c2 = self.load(name, version=v2)
        if c1 is None or c2 is None:
            return {"error": "Version not found"}
        return {
            "artifact": name,
            "v1": v1,
            "v2": v2,
            "v1_size": len(c1),
            "v2_size": len(c2),
            "changed": c1 != c2,
            "v1_hash": self._hash_content(c1),
            "v2_hash": self._hash_content(c2),
        }
