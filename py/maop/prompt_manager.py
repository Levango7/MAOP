"""MAOP Prompt Manager - Template design, variable injection, and version management.

Prompt template management with versioning. to pure Python with SQLite-backed persistence.
Actions: create, get, list, delete, render, test, search, export, import.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.backends.db_utils import get_db_path

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────

class PromptVersion(BaseModel):
    """A single version of a prompt template."""
    version: str = "1.0"
    content: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)
    created: str = ""


class PromptTemplate(BaseModel):
    """A prompt template with version history."""
    id: str = ""
    name: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    versions: list[PromptVersion] = Field(default_factory=list)
    current_version: str = "1.0"


class RenderResult(BaseModel):
    """Result of rendering a template."""
    ok: bool = True
    content: str = ""
    variables_used: dict[str, Any] = Field(default_factory=dict)
    variables_missing: list[str] = Field(default_factory=list)
    error: str = ""


# ── PromptManager ────────────────────────────────────────────

class PromptManager:
    """Manage prompt templates with version control and variable injection.

    Usage::

        mgr = PromptManager(root_dir="/path/to/MAOP")
        mgr.create("code-review", content="Review {{language}} code: {{snippet}}", variables={"language": "str", "snippet": "str"})
        result = mgr.render("code-review", render_vars={"language": "Python", "snippet": "def foo(): pass"})
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("prompt_manager")
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create tables if not exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    tags TEXT DEFAULT '[]',
                    current_version TEXT DEFAULT '1.0'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_versions (
                    template_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content TEXT NOT NULL,
                    variables TEXT DEFAULT '{}',
                    created TEXT NOT NULL,
                    PRIMARY KEY (template_id, version),
                    FOREIGN KEY (template_id) REFERENCES prompt_templates(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_prompts_category
                ON prompt_templates(category)
            """)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Actions ──────────────────────────────────────────────

    def create(
        self,
        template_id: str,
        content: str,
        name: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        variables: dict[str, Any] | None = None,
        version: str = "1.0",
    ) -> str:
        """Create a new prompt template. Returns template ID."""
        now = datetime.now(timezone.utc).isoformat()
        template_name = name or template_id
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        vars_json = json.dumps(variables or {}, ensure_ascii=False)

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO prompt_templates (id, name, category, tags, current_version) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=?, category=?, tags=?",
                (template_id, template_name, category, tags_json, version,
                 template_name, category, tags_json),
            )
            conn.execute(
                "INSERT INTO prompt_versions (template_id, version, content, variables, created) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(template_id, version) DO UPDATE SET content=?, variables=?",
                (template_id, version, content, vars_json, now, content, vars_json),
            )

        logger.info("Created prompt: %s v%s", template_id, version)
        return template_id

    def get(self, template_id: str, version: str = "") -> PromptVersion | None:
        """Get a specific version of a template."""
        with self._connect() as conn:
            if not version:
                # Get current version
                row = conn.execute(
                    "SELECT current_version FROM prompt_templates WHERE id=?",
                    (template_id,),
                ).fetchone()
                if row is None:
                    return None
                version = row["current_version"]

            row = conn.execute(
                "SELECT * FROM prompt_versions WHERE template_id=? AND version=?",
                (template_id, version),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_version(row)

    def list_templates(self, category: str = "", limit: int = 100) -> list[PromptTemplate]:
        """List templates, optionally filtered by category."""
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM prompt_templates WHERE category=? ORDER BY name LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM prompt_templates ORDER BY category, name LIMIT ?",
                    (limit,),
                ).fetchall()

        result = []
        for r in rows:
            template = self._row_to_template(r)
            # Load versions
            with self._connect() as conn:
                ver_rows = conn.execute(
                    "SELECT * FROM prompt_versions WHERE template_id=? ORDER BY version",
                    (r["id"],),
                ).fetchall()
            template.versions = [self._row_to_version(v) for v in ver_rows]
            result.append(template)

        return result

    def delete(self, template_id: str) -> bool:
        """Delete a template and all its versions."""
        with self._connect() as conn:
            conn.execute("DELETE FROM prompt_versions WHERE template_id=?", (template_id,))
            cursor = conn.execute("DELETE FROM prompt_templates WHERE id=?", (template_id,))
            return cursor.rowcount > 0

    def render(
        self,
        template_id: str,
        render_vars: dict[str, Any] | None = None,
        version: str = "",
    ) -> RenderResult:
        """Render a template with variable injection.

        Variables use {{variable_name}} syntax.
        Missing variables are left as-is and reported.
        """
        ver = self.get(template_id, version)
        if ver is None:
            return RenderResult(ok=False, error=f"template not found: {template_id}")

        content = ver.content
        render_vars = render_vars or {}
        variables_used: dict[str, Any] = {}
        variables_missing: list[str] = []

        # Find all {{var}} placeholders
        placeholders = re.findall(r'\{\{(\w+)\}\}', content)

        for var_name in placeholders:
            if var_name in render_vars:
                content = content.replace(f"{{{{{var_name}}}}}", str(render_vars[var_name]))
                variables_used[var_name] = render_vars[var_name]
            else:
                variables_missing.append(var_name)

        return RenderResult(
            ok=True,
            content=content,
            variables_used=variables_used,
            variables_missing=variables_missing,
        )

    def test(self, template_id: str, version: str = "") -> dict[str, Any]:
        """Test a template: check variable extraction and rendering."""
        ver = self.get(template_id, version)
        if ver is None:
            return {"ok": False, "error": f"template not found: {template_id}"}

        placeholders = re.findall(r'\{\{(\w+)\}\}', ver.content)
        declared_vars = set(ver.variables.keys())
        used_vars = set(placeholders)

        return {
            "ok": True,
            "template_id": template_id,
            "version": ver.version,
            "declared_variables": list(declared_vars),
            "used_variables": list(used_vars),
            "undeclared": list(used_vars - declared_vars),
            "unused": list(declared_vars - used_vars),
            "content_length": len(ver.content),
        }

    def search(self, query: str, limit: int = 20) -> list[PromptTemplate]:
        """Search templates by query (matches id, name, category, tags, content)."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            # Search in templates table
            rows = conn.execute(
                "SELECT DISTINCT t.* FROM prompt_templates t "
                "LEFT JOIN prompt_versions v ON t.id = v.template_id "
                "WHERE t.id LIKE ? OR t.name LIKE ? OR t.category LIKE ? OR t.tags LIKE ? "
                "OR v.content LIKE ? ORDER BY t.name LIMIT ?",
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()

        result = []
        for r in rows:
            template = self._row_to_template(r)
            with self._connect() as conn:
                ver_rows = conn.execute(
                    "SELECT * FROM prompt_versions WHERE template_id=? ORDER BY version",
                    (r["id"],),
                ).fetchall()
            template.versions = [self._row_to_version(v) for v in ver_rows]
            result.append(template)

        return result

    def export_templates(self, template_ids: list[str] | None = None) -> dict[str, Any]:
        """Export templates as a portable dict."""
        with self._connect() as conn:
            if template_ids:
                placeholders = ",".join("?" for _ in template_ids)
                rows = conn.execute(
                    f"SELECT * FROM prompt_templates WHERE id IN ({placeholders})",
                    template_ids,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM prompt_templates").fetchall()

        templates = []
        for r in rows:
            template = self._row_to_template(r)
            with self._connect() as conn:
                ver_rows = conn.execute(
                    "SELECT * FROM prompt_versions WHERE template_id=?",
                    (r["id"],),
                ).fetchall()
            template.versions = [self._row_to_version(v) for v in ver_rows]
            templates.append(template.model_dump())

        return {"prompts": templates, "exported_at": datetime.now(timezone.utc).isoformat()}

    def import_templates(self, data: dict[str, Any]) -> int:
        """Import templates from a portable dict. Returns count imported."""
        count = 0
        for template_data in data.get("prompts", []):
            tid = template_data.get("id", "")
            if not tid:
                continue
            name = template_data.get("name", tid)
            category = template_data.get("category", "general")
            tags = template_data.get("tags", [])
            current_ver = template_data.get("current_version", "1.0")

            with self._connect() as conn:
                tags_json = json.dumps(tags, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO prompt_templates (id, name, category, tags, current_version) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=?, category=?, tags=?",
                    (tid, name, category, tags_json, current_ver, name, category, tags_json),
                )

            for ver_data in template_data.get("versions", []):
                ver = ver_data.get("version", "1.0")
                content = ver_data.get("content", "")
                variables = ver_data.get("variables", {})
                created = ver_data.get("created", datetime.now(timezone.utc).isoformat())

                with self._connect() as conn:
                    vars_json = json.dumps(variables, ensure_ascii=False)
                    conn.execute(
                        "INSERT INTO prompt_versions (template_id, version, content, variables, created) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(template_id, version) DO UPDATE SET content=?, variables=?",
                        (tid, ver, content, vars_json, created, content, vars_json),
                    )

            count += 1

        return count

    def stats(self) -> dict[str, Any]:
        """Get prompt manager statistics."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) as cnt FROM prompt_templates").fetchone()["cnt"]
            by_category = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM prompt_templates GROUP BY category"
            ).fetchall()
            total_versions = conn.execute("SELECT COUNT(*) as cnt FROM prompt_versions").fetchone()["cnt"]

        return {
            "total_templates": total,
            "total_versions": total_versions,
            "by_category": {r["category"]: r["cnt"] for r in by_category},
        }

    # ── Internal ─────────────────────────────────────────────

    def _row_to_template(self, row: sqlite3.Row) -> PromptTemplate:
        tags = []
        with suppress(json.JSONDecodeError, ValueError):
            tags = json.loads(row["tags"] or "[]")
        return PromptTemplate(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            tags=tags,
            current_version=row["current_version"],
        )

    def _row_to_version(self, row: sqlite3.Row) -> PromptVersion:
        variables = {}
        with suppress(json.JSONDecodeError, ValueError):
            variables = json.loads(row["variables"] or "{}")
        return PromptVersion(
            version=row["version"],
            content=row["content"],
            variables=variables,
            created=row["created"],
        )
