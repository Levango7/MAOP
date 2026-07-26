"""MAOP Project Context — Automatic project context injection for conversations.

.. deprecated::
    This module is not used by any production code and may be removed in a
    future release.  It is kept only because ``test_session.py`` references
    ``ProjectContext`` directly.

Scans the project directory and builds a structured context summary that
gets prepended to conversation messages, giving the agent awareness of:
  - Project structure (directory tree)
  - Key configuration files (pyproject.toml, package.json, etc.)
  - Recent file changes (git status / recent modifications)
  - Technology stack detection
  - CLAUDE.md / AGENTS.md / .maop-context.md instructions

Usage::

    from maop.core.project_context import ProjectContext

    ctx = ProjectContext(workdir="/path/to/project")
    summary = ctx.build()
    # Inject into conversation:
    mgr.add_message(session_id, role="system", content=summary)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProjectInfo(BaseModel):
    workdir: str = ""
    name: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    structure: str = ""
    config_files: dict[str, str] = Field(default_factory=dict)
    instructions: str = ""
    recent_changes: str = ""
    created_at: str = ""


class ProjectContext:
    """Build and cache project context for agent awareness.

    Usage::

        ctx = ProjectContext(workdir="/path/to/project")
        summary = ctx.build()
    """

    SKIP_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        "dist", "build", ".eggs", "*.egg-info", ".next",
        ".cache", ".sass-cache", "target", "bin", "obj",
    }

    CONFIG_FILES = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "package.json", "Cargo.toml", "go.mod", "pom.xml",
        "Makefile", "Dockerfile", "docker-compose.yml",
        ".env.example", "tsconfig.json",
    ]

    INSTRUCTION_FILES = [
        "CLAUDE.md", "AGENTS.md", ".maop-context.md",
        "CONTRIBUTING.md", ".cursorrules", ".windsurfrules",
    ]

    TECH_MARKERS = {
        "pyproject.toml": "python",
        "setup.py": "python",
        "requirements.txt": "python",
        "package.json": "javascript",
        "tsconfig.json": "typescript",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java",
        "Makefile": "c/c++",
        "Dockerfile": "docker",
    }

    def __init__(self, workdir: str | Path, max_tree_depth: int = 3) -> None:
        self._workdir = Path(workdir).resolve()
        self._max_depth = max_tree_depth
        self._cache: ProjectInfo | None = None
        self._cache_time: float = 0.0

    @property
    def workdir(self) -> Path:
        return self._workdir

    def build(self, *, force_refresh: bool = False) -> ProjectInfo:
        import time as _time
        if self._cache and not force_refresh and (_time.time() - self._cache_time) < 60:
            return self._cache

        info = ProjectInfo(
            workdir=str(self._workdir),
            name=self._workdir.name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        info.tech_stack = self._detect_tech_stack()
        info.structure = self._build_tree()
        info.config_files = self._read_config_files()
        info.instructions = self._read_instructions()
        info.recent_changes = self._detect_recent_changes()

        self._cache = info
        self._cache_time = _time.time()
        return info

    def build_summary(self, *, force_refresh: bool = False) -> str:
        info = self.build(force_refresh=force_refresh)
        parts = [f"# Project: {info.name}"]
        if info.tech_stack:
            parts.append(f"\n## Tech Stack\n{', '.join(info.tech_stack)}")
        if info.structure:
            parts.append(f"\n## Structure\n```\n{info.structure}\n```")
        if info.config_files:
            parts.append("\n## Configuration")
            for name, content in info.config_files.items():
                truncated = content[:500] + ("..." if len(content) > 500 else "")
                parts.append(f"### {name}\n```\n{truncated}\n```")
        if info.instructions:
            parts.append(f"\n## Instructions\n{info.instructions}")
        if info.recent_changes:
            parts.append(f"\n## Recent Changes\n{info.recent_changes}")
        return "\n".join(parts)

    def _detect_tech_stack(self) -> list[str]:
        stack = set()
        for marker, tech in self.TECH_MARKERS.items():
            if (self._workdir / marker).exists():
                stack.add(tech)
        if (self._workdir / "config" / "agents.yaml").exists():
            stack.add("MAOP")
        return sorted(stack)

    def _build_tree(self) -> str:
        lines: list[str] = []
        self._walk(self._workdir, prefix="", depth=0, lines=lines)
        return "\n".join(lines[:80])

    def _walk(self, path: Path, prefix: str, depth: int, lines: list[str]) -> None:
        if depth > self._max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for i, entry in enumerate(entries[:30]):
            if entry.name.startswith(".") and entry.name != ".env.example":
                continue
            if entry.name in self.SKIP_DIRS:
                continue
            if entry.is_dir() and entry.name.endswith(("egg-info", "__pycache__")):
                continue
            is_last = i == min(len(entries) - 1, 29)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}/" if entry.is_dir() else f"{prefix}{connector}{entry.name}")
            if entry.is_dir() and depth < self._max_depth:
                extension = "    " if is_last else "│   "
                self._walk(entry, prefix + extension, depth + 1, lines)

    def _read_config_files(self) -> dict[str, str]:
        result = {}
        for name in self.CONFIG_FILES:
            fpath = self._workdir / name
            if fpath.exists() and fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")[:1000]
                    result[name] = content
                except Exception:
                    pass
        return result

    def _read_instructions(self) -> str:
        parts = []
        for name in self.INSTRUCTION_FILES:
            fpath = self._workdir / name
            if fpath.exists() and fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        parts.append(f"### {name}\n{content[:2000]}")
                except Exception:
                    pass
        return "\n\n".join(parts)

    def _detect_recent_changes(self) -> str:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5,
                cwd=str(self._workdir),
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().splitlines()[:20]
                return "\n".join(lines)
        except Exception:
            pass
        try:
            recent = []
            for f in self._workdir.rglob("*"):
                if f.is_file() and not any(skip in f.parts for skip in self.SKIP_DIRS):
                    try:
                        mtime = f.stat().st_mtime
                        recent.append((mtime, str(f.relative_to(self._workdir))))
                    except Exception:
                        pass
            recent.sort(reverse=True)
            if recent:
                return "\n".join(f"  {name}" for _, name in recent[:10])
        except Exception:
            pass
        return ""
