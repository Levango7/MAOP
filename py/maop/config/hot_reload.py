"""MAOP Config Hot-Reload — Watch config files and reload on change.

Monitors agents.yaml and rules.yaml for modifications.
When a change is detected, triggers ConfigLoader.reload() and
emits a "config.reloaded" event on the event bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from maop.config.loader import ConfigLoader, MaopConfig
from maop.core.reliability.event_bus import Event, EventBus, get_event_bus

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────

class ReloadEvent(BaseModel):
    """Event data for config reload."""
    files_changed: list[str]
    timestamp: float
    reload_count: int


class HotReloadState(BaseModel):
    """Current state of the hot-reload watcher."""
    watching: list[str]
    last_check: float = 0.0
    reload_count: int = 0
    running: bool = False


# ── File hash cache ───────────────────────────────────────────

def _file_hash(path: Path) -> str | None:
    """Compute SHA-256 hash of a file for change detection.

    Uses SHA-256 (not MD5) to align with the project-wide integrity
    standard mandated by the plugin manifest checksum constraint and
    ADR-011. MD5 is cryptographically broken and inconsistent with
    the rest of the codebase.
    """
    if not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception as exc:
        logger.warning(
            "[hot_reload] Failed to compute file hash for %s, treating as 'no change' "
            "(change detection skipped, will rely on mtime): %s",
            path, exc, exc_info=True,
        )
        return None


# ── Hot-Reload Watcher ────────────────────────────────────────

class ConfigHotReload:
    """Watch config files and reload on change.

    Usage::

        watcher = ConfigHotReload(root_dir="/path/to/MAOP")
        watcher.start()  # non-blocking, runs in background

        # Later...
        watcher.stop()
    """

    def __init__(
        self,
        root_dir: str | Path,
        config: MaopConfig | None = None,
        loader: ConfigLoader | None = None,
        event_bus: EventBus | None = None,
        poll_interval_s: float = 5.0,
        on_reload: Callable[[MaopConfig], None] | None = None,
    ) -> None:
        self._root = Path(root_dir)
        self._loader = loader or ConfigLoader(project_root=self._root)
        self._config = config
        self._bus = event_bus or get_event_bus()
        self._poll_interval = poll_interval_s
        self._on_reload = on_reload

        # Config files to watch (routing rules live inside agents.yaml).
        # Includes mcp_servers.yaml and tool_whitelist.yaml — both are
        # hot-affecting: MCP server / tool permissions changes should reload
        # without a process restart, otherwise stale config is served until
        # a manual bounce.
        self._watch_files = [
            self._root / "config" / "agents.yaml",
            self._root / "config" / "rules.yaml",
            self._root / "config" / "models.yaml",
            self._root / "config" / "mcp_servers.yaml",
            self._root / "config" / "tool_whitelist.yaml",
        ]

        # State
        self._hashes: dict[str, str | None] = {}
        self._state = HotReloadState(
            watching=[str(f) for f in self._watch_files],
            running=False,
        )
        self._task: asyncio.Task | None = None

        # Initialize hashes
        for f in self._watch_files:
            self._hashes[str(f)] = _file_hash(f)

    @property
    def state(self) -> HotReloadState:
        return self._state

    @property
    def config(self) -> MaopConfig | None:
        return self._config

    def start(self) -> None:
        """Start watching (non-blocking). Must be called from async context."""
        if self._state.running:
            return
        self._state.running = True
        self._task = asyncio.ensure_future(self._watch_loop())

    async def stop(self) -> None:
        """Stop watching and wait for the watch loop to finish."""
        self._state.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def stop_sync(self) -> None:
        """Stop watching (sync, best-effort). Prefer stop() in async contexts."""
        self._state.running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self._state.running:
            try:
                await asyncio.sleep(self._poll_interval)
                changed = self._check_changes()
                if changed:
                    await self._reload(changed)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Hot-reload watch error: %s", exc)

    def _check_changes(self) -> list[str]:
        """Check for file changes. Returns list of changed file paths."""
        changed: list[str] = []
        for f in self._watch_files:
            f_str = str(f)
            current_hash = _file_hash(f)
            if current_hash != self._hashes.get(f_str):
                changed.append(f_str)
                self._hashes[f_str] = current_hash
        return changed

    async def _reload(self, changed_files: list[str]) -> None:
        """Reload config and emit event."""
        try:
            self._config = self._loader.load()
            self._state.reload_count += 1
            self._state.last_check = time.time()

            logger.info("Config reloaded: %s", changed_files)

            # Emit event
            await self._bus.publish(Event(topic="config.reloaded", data={
                "files_changed": changed_files,
                "reload_count": self._state.reload_count,
            }))

            # Callback
            if self._on_reload and self._config:
                self._on_reload(self._config)

        except Exception as exc:
            logger.error("Config reload failed: %s", exc)

    def check_once(self) -> list[str]:
        """Single check for changes (synchronous, for testing)."""
        return self._check_changes()

    def force_reload(self) -> MaopConfig | None:
        """Force a reload regardless of file changes."""
        try:
            self._config = self._loader.load()
            self._state.reload_count += 1
            self._state.last_check = time.time()
            return self._config
        except Exception as exc:
            logger.error("Force reload failed: %s", exc)
            return None
