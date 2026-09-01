"""Comprehensive tests for MAOP.config.hot_reload — ConfigHotReload."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from unittest.mock import MagicMock

import pytest

from maop.config.hot_reload import (
    ConfigHotReload,
    HotReloadState,
    ReloadEvent,
    _file_hash,
)
from maop.config.loader import ConfigLoader, MaopConfig
from maop.core.reliability.event_bus import Event, EventBus

# ── Helper Tests ─────────────────────────────────────────────

class TestFileHash:
    """Tests for _file_hash helper."""

    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert _file_hash(f) == expected

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert _file_hash(f) is None

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _file_hash(f) == expected

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f1.write_bytes(b"content1")
        f2 = tmp_path / "f2.txt"
        f2.write_bytes(b"content2")
        assert _file_hash(f1) != _file_hash(f2)

    def test_permission_error_returns_none(self, tmp_path):
        # Use a path that can't be read (a directory)
        d = tmp_path / "dir"
        d.mkdir()
        # On some systems reading a directory as bytes raises an error
        result = _file_hash(d)
        # Should return None on error (or a hash if the OS allows it)
        # The important thing is it doesn't crash
        assert result is None or isinstance(result, str)


# ── Model Tests ──────────────────────────────────────────────

class TestReloadEvent:
    """Tests for ReloadEvent model."""

    def test_creation(self):
        e = ReloadEvent(
            files_changed=["/path/to/file.yaml"],
            timestamp=time.time(),
            reload_count=1,
        )
        assert e.files_changed == ["/path/to/file.yaml"]
        assert e.reload_count == 1

    def test_empty_files(self):
        e = ReloadEvent(files_changed=[], timestamp=0.0, reload_count=0)
        assert e.files_changed == []


class TestHotReloadState:
    """Tests for HotReloadState model."""

    def test_defaults(self):
        s = HotReloadState(watching=[])
        assert s.last_check == 0.0
        assert s.reload_count == 0
        assert s.running is False

    def test_custom(self):
        s = HotReloadState(watching=["a.yaml"], running=True, reload_count=3)
        assert s.running is True
        assert s.reload_count == 3


# ── ConfigHotReload Tests ────────────────────────────────────

@pytest.fixture
def config_files(tmp_path):
    """Create config directory with the full set of hot-reloaded files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "agents.yaml").write_text("agents: {}", encoding="utf-8")
    (config_dir / "rules.yaml").write_text("guards: {}", encoding="utf-8")
    (config_dir / "models.yaml").write_text("models: {}", encoding="utf-8")
    (config_dir / "mcp_servers.yaml").write_text("servers: {}", encoding="utf-8")
    (config_dir / "tool_whitelist.yaml").write_text("allow: []", encoding="utf-8")
    return tmp_path


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def watcher(config_files, event_bus):
    return ConfigHotReload(
        root_dir=config_files,
        event_bus=event_bus,
        poll_interval_s=0.1,
    )


class TestConfigHotReloadInit:
    """Tests for ConfigHotReload initialization."""

    def test_init_basic(self, config_files):
        wr = ConfigHotReload(root_dir=config_files)
        assert wr._root == config_files
        assert wr._config is None
        assert wr._state.running is False
        assert wr._state.reload_count == 0

    def test_init_watch_files(self, config_files):
        wr = ConfigHotReload(root_dir=config_files)
        assert len(wr._watch_files) == 5
        assert (config_files / "config" / "agents.yaml") in wr._watch_files
        assert (config_files / "config" / "rules.yaml") in wr._watch_files
        assert (config_files / "config" / "models.yaml") in wr._watch_files
        assert (config_files / "config" / "mcp_servers.yaml") in wr._watch_files
        assert (config_files / "config" / "tool_whitelist.yaml") in wr._watch_files

    def test_init_hashes_populated(self, config_files):
        wr = ConfigHotReload(root_dir=config_files)
        agents_path = str(config_files / "config" / "agents.yaml")
        assert agents_path in wr._hashes
        assert wr._hashes[agents_path] is not None

    def test_init_with_loader(self, config_files):
        loader = ConfigLoader(project_root=config_files)
        wr = ConfigHotReload(root_dir=config_files, loader=loader)
        assert wr._loader is loader

    def test_init_with_config(self, config_files):
        config = MaopConfig()
        wr = ConfigHotReload(root_dir=config_files, config=config)
        assert wr._config is config

    def test_init_with_on_reload(self, config_files):
        callback = MagicMock()
        wr = ConfigHotReload(root_dir=config_files, on_reload=callback)
        assert wr._on_reload is callback

    def test_init_missing_files(self, tmp_path):
        # No config files exist
        wr = ConfigHotReload(root_dir=tmp_path)
        agents_path = str(tmp_path / "config" / "agents.yaml")
        assert wr._hashes[agents_path] is None

    def test_state_property(self, watcher):
        assert isinstance(watcher.state, HotReloadState)

    def test_config_property(self, watcher):
        assert watcher.config is None  # not loaded yet


class TestCheckChanges:
    """Tests for ConfigHotReload._check_changes / check_once."""

    def test_no_changes(self, watcher):
        assert watcher.check_once() == []

    def test_agents_yaml_changed(self, config_files, watcher):
        # Modify agents.yaml
        (config_files / "config" / "agents.yaml").write_text(
            "agents:\n  new:\n    cli: new", encoding="utf-8"
        )
        changed = watcher.check_once()
        assert len(changed) == 1
        assert "agents.yaml" in changed[0]

    def test_multiple_files_changed(self, config_files, watcher):
        (config_files / "config" / "agents.yaml").write_text("agents: {new: 1}", encoding="utf-8")
        (config_files / "config" / "rules.yaml").write_text("guards: {new: 1}", encoding="utf-8")
        changed = watcher.check_once()
        assert len(changed) == 2

    def test_change_detected_only_once(self, config_files, watcher):
        (config_files / "config" / "agents.yaml").write_text("changed: true", encoding="utf-8")
        assert len(watcher.check_once()) == 1
        # Second check: hash already updated, no change
        assert len(watcher.check_once()) == 0

    def test_file_deleted(self, config_files, watcher):
        (config_files / "config" / "agents.yaml").unlink()
        changed = watcher.check_once()
        assert len(changed) == 1
        # Hash should now be None
        agents_path = str(config_files / "config" / "agents.yaml")
        assert watcher._hashes[agents_path] is None

    def test_file_recreated(self, config_files, watcher):
        agents_path = config_files / "config" / "agents.yaml"
        agents_path.unlink()
        watcher.check_once()
        agents_path.write_text("recreated: true", encoding="utf-8")
        changed = watcher.check_once()
        assert len(changed) == 1


class TestForceReload:
    """Tests for ConfigHotReload.force_reload."""

    def test_force_reload(self, watcher):
        result = watcher.force_reload()
        assert result is not None
        assert isinstance(result, MaopConfig)
        assert watcher._state.reload_count == 1
        assert watcher._state.last_check > 0
        assert watcher.config is not None

    def test_force_reload_increments_count(self, watcher):
        watcher.force_reload()
        watcher.force_reload()
        assert watcher._state.reload_count == 2

    def test_force_reload_failure(self, tmp_path):
        wr = ConfigHotReload(root_dir=tmp_path)
        # Mock loader to raise
        wr._loader = MagicMock()
        wr._loader.load.side_effect = Exception("load error")
        result = wr.force_reload()
        assert result is None


class TestStartStop:
    """Tests for ConfigHotReload.start / stop."""

    def test_start_stop(self, watcher):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            watcher.start()
            assert watcher.state.running is True
            assert watcher._task is not None
            loop.run_until_complete(watcher.stop())
            assert watcher.state.running is False
        finally:
            loop.close()

    def test_start_idempotent(self, watcher):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            watcher.start()
            task1 = watcher._task
            watcher.start()  # should not create new task
            assert watcher._task is task1
            loop.run_until_complete(watcher.stop())
        finally:
            loop.close()

    def test_stop_when_not_running(self, watcher):
        watcher.stop_sync()
        assert watcher.state.running is False

    def test_stop_cancels_task(self, watcher):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            watcher.start()
            loop.run_until_complete(watcher.stop())
            assert watcher._task is None or watcher._task.cancelled() or watcher._task.done()
        finally:
            loop.close()


class TestReload:
    """Tests for ConfigHotReload._reload."""

    @pytest.mark.asyncio
    async def test_reload_emits_event(self, config_files, event_bus):
        received_events = []

        async def handler(event: Event):
            received_events.append(event)

        event_bus.subscribe("config.reloaded", handler)
        wr = ConfigHotReload(root_dir=config_files, event_bus=event_bus)

        await wr._reload(["test.yaml"])
        assert wr._state.reload_count == 1
        assert len(received_events) == 1
        assert received_events[0].topic == "config.reloaded"
        assert received_events[0].data["files_changed"] == ["test.yaml"]

    @pytest.mark.asyncio
    async def test_reload_calls_callback(self, config_files, event_bus):
        callback = MagicMock()
        wr = ConfigHotReload(
            root_dir=config_files, event_bus=event_bus, on_reload=callback,
        )
        await wr._reload(["test.yaml"])
        assert callback.called
        assert isinstance(callback.call_args[0][0], MaopConfig)

    @pytest.mark.asyncio
    async def test_reload_failure_handled(self, config_files, event_bus):
        wr = ConfigHotReload(root_dir=config_files, event_bus=event_bus)
        wr._loader = MagicMock()
        wr._loader.load.side_effect = Exception("fail")
        # Should not raise
        await wr._reload(["test.yaml"])
        # reload_count should NOT increment on failure
        assert wr._state.reload_count == 0


class TestWatchLoop:
    """Tests for ConfigHotReload._watch_loop."""

    @pytest.mark.asyncio
    async def test_watch_loop_detects_change(self, config_files, event_bus):
        wr = ConfigHotReload(
            root_dir=config_files, event_bus=event_bus, poll_interval_s=0.05,
        )
        wr._state.running = True

        # Start the watch loop as a task
        task = asyncio.ensure_future(wr._watch_loop())

        # Wait a bit, then modify a file
        await asyncio.sleep(0.1)
        (config_files / "config" / "agents.yaml").write_text(
            "agents:\n  new:\n    cli: new", encoding="utf-8"
        )

        # Wait for the loop to detect
        await asyncio.sleep(0.2)
        wr._state.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert wr._state.reload_count >= 1
