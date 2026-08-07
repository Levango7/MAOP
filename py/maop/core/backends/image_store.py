"""MAOP Image Store — Upload, store, and manage images for multimodal chat.

Features:
  - Save uploaded images to disk (data/uploads/)
  - Associate images with chat sessions
  - Generate unique IDs for image references
  - Support base64 and file upload
  - Auto-cleanup expired images
  - SQLite metadata tracking

Usage::

    from maop.core.backends.image_store import ImageStore

    store = ImageStore(root_dir="/path/to/MAOP")
    img_id = store.save(session_id="s1", filename="screenshot.png", data=b"...")
    path = store.get_path(img_id)
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from maop.core.backends.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20 MB


class ImageMeta(BaseModel):
    id: str = ""
    session_id: str = ""
    filename: str = ""
    content_type: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    checksum: str = ""
    created_at: str = ""


class ImageStore:
    """Manage uploaded images for multimodal chat.

    Images are stored on disk under data/uploads/{session_id}/{id}.{ext}.
    Metadata is tracked in SQLite for fast lookup and cleanup.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._upload_dir = self._root / "data" / "uploads"
        self._db_path = get_db_path("image_store")
        self._ensure_dirs()
        self._ensure_db()

    def _ensure_dirs(self) -> None:
        self._upload_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    filename TEXT DEFAULT '',
                    content_type TEXT DEFAULT '',
                    size_bytes INTEGER DEFAULT 0,
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    checksum TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    file_path TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_images_session
                ON images(session_id, created_at)
            """)

    def save(
        self,
        session_id: str,
        filename: str,
        data: bytes,
        *,
        content_type: str = "",
    ) -> str:
        """Save an image and return its ID."""
        if len(data) > MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large: {len(data)} bytes (max {MAX_IMAGE_SIZE})")

        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            ext = ".png"

        img_id = f"img-{uuid.uuid4().hex[:10]}"
        checksum = hashlib.sha256(data).hexdigest()[:16]
        now = datetime.now(timezone.utc).isoformat()

        session_dir = self._upload_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        file_path = session_dir / f"{img_id}{ext}"

        file_path.write_bytes(data)

        if not content_type:
            content_type = self._ext_to_mime(ext)

        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO images (id, session_id, filename, content_type, size_bytes,
                   width, height, checksum, created_at, file_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (img_id, session_id, filename, content_type, len(data),
                 0, 0, checksum, now, str(file_path)),
            )

        logger.info("[image_store] Saved %s (%d bytes) for session %s", img_id, len(data), session_id)
        return img_id

    def save_base64(
        self,
        session_id: str,
        filename: str,
        b64_data: str,
        *,
        content_type: str = "",
    ) -> str:
        """Save a base64-encoded image."""
        data = base64.b64decode(b64_data)
        return self.save(session_id, filename, data, content_type=content_type)

    def get_meta(self, image_id: str) -> ImageMeta | None:
        """Get image metadata by ID."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            return None
        return ImageMeta(
            id=row["id"], session_id=row["session_id"],
            filename=row["filename"], content_type=row["content_type"],
            size_bytes=row["size_bytes"], width=row["width"],
            height=row["height"], checksum=row["checksum"],
            created_at=row["created_at"],
        )

    def get_path(self, image_id: str) -> str | None:
        """Get the file path for an image."""
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT file_path FROM images WHERE id=?", (image_id,)).fetchone()
        return row["file_path"] if row else None

    def get_data(self, image_id: str) -> bytes | None:
        """Read image data from disk."""
        path = self.get_path(image_id)
        if path and Path(path).exists():
            return Path(path).read_bytes()
        return None

    def get_base64(self, image_id: str) -> str | None:
        """Read image data as base64 string."""
        data = self.get_data(image_id)
        if data:
            return base64.b64encode(data).decode("ascii")
        return None

    def list_session_images(self, session_id: str) -> list[ImageMeta]:
        """List all images for a session."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM images WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [ImageMeta(
            id=r["id"], session_id=r["session_id"],
            filename=r["filename"], content_type=r["content_type"],
            size_bytes=r["size_bytes"], width=r["width"],
            height=r["height"], checksum=r["checksum"],
            created_at=r["created_at"],
        ) for r in rows]

    def delete(self, image_id: str) -> bool:
        """Delete an image (metadata + file)."""
        path = self.get_path(image_id)
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM images WHERE id=?", (image_id,))
        if cursor.rowcount > 0 and path:
            with contextlib.suppress(Exception):
                Path(path).unlink(missing_ok=True)
            return True
        return False

    def cleanup_session(self, session_id: str) -> int:
        """Delete all images for a session."""
        images = self.list_session_images(session_id)
        count = 0
        for img in images:
            if self.delete(img.id):
                count += 1
        return count

    def cleanup_expired(self, max_age_days: int = 30) -> int:
        """Delete images older than max_age_days."""
        cutoff = datetime.now(timezone.utc)
        images_meta = []
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM images ORDER BY created_at").fetchall()
            for r in rows:
                images_meta.append(ImageMeta(
                    id=r["id"], session_id=r["session_id"],
                    filename=r["filename"], content_type=r["content_type"],
                    size_bytes=r["size_bytes"], width=r["width"],
                    height=r["height"], checksum=r["checksum"],
                    created_at=r["created_at"],
                ))

        count = 0
        for img in images_meta:
            try:
                created = datetime.fromisoformat(img.created_at)
                if (cutoff - created).days > max_age_days and self.delete(img.id):
                    count += 1
            except (ValueError, TypeError):
                pass
        return count

    @staticmethod
    def _ext_to_mime(ext: str) -> str:
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".svg": "image/svg+xml",
        }
        return mime_map.get(ext.lower(), "application/octet-stream")
