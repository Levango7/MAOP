"""Tests for MAOP.core.tool_manager — ToolManager with SQLite backend.

F7d (2026-07-22, Phase F): all ``mgr.call(...)`` invocations are now
``await mgr.call(...)`` because ``ToolManager.call`` is async (uses
``asyncio.create_subprocess_exec`` instead of blocking
``subprocess.run``). Test methods that call ``mgr.call`` are
``async def`` — pytest-asyncio ``asyncio_mode = "auto"`` in
``pyproject.toml`` auto-detects them without ``@pytest.mark.asyncio``.
See ADR-013.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

import maop.core.agent.tools.tool_manager as _tm
from maop.core.agent.tools.tool_manager import ToolCallResult, ToolDef, ToolManager
from maop.core.agent.tools.tool_policy import ToolPolicy
from maop.core.backends.db_utils import get_db_path, sqlite_connect


@pytest.fixture
def mgr(tmp_path):
    return ToolManager(root_dir=tmp_path)


class TestToolDef:
    def test_defaults(self):
        td = ToolDef()
        assert td.id == ""
        assert td.category == "general"
        assert td.enabled is True
        assert td.call_count == 0
        assert td.last_called is None

    def test_with_values(self):
        td = ToolDef(id="lint", name="lint", command="ruff check", category="quality")
        assert td.command == "ruff check"
        assert td.category == "quality"


class TestToolCallResult:
    def test_defaults(self):
        r = ToolCallResult()
        assert r.ok is True
        assert r.exit_code == 0
        assert r.output == ""

    def test_error_result(self):
        r = ToolCallResult(ok=False, error="bad")
        assert r.ok is False
        assert r.error == "bad"


class TestToolManagerInit:
    def test_creates_db_file(self, tmp_path):
        ToolManager(root_dir=tmp_path)
        # Unified DB mode (ADR-011): tools share maop.db
        assert (tmp_path / "data" / "maop.db").exists()

    def test_idempotent_init(self, tmp_path):
        mgr1 = ToolManager(root_dir=tmp_path)
        mgr1.register("t1", command='python -c "print(1)"')
        mgr2 = ToolManager(root_dir=tmp_path)
        info = mgr2.info("t1")
        assert info is not None
        assert 'python -c' in info.command


class TestRegister:
    def test_register_basic(self, mgr):
        tid = mgr.register("lint", command="ruff check", name="lint", description="Linter", category="quality")
        assert tid == "lint"
        info = mgr.info("lint")
        assert info is not None
        assert info.command == "ruff check"
        assert info.category == "quality"
        assert info.description == "Linter"

    def test_register_defaults_name_to_id(self, mgr):
        mgr.register("mytool", command="echo")
        info = mgr.info("mytool")
        assert info.name == "mytool"

    def test_register_upsert(self, mgr):
        mgr.register("t1", command="echo v1")
        mgr.register("t1", command="echo v2", description="updated")
        info = mgr.info("t1")
        assert info.command == "echo v2"
        assert info.description == "updated"

    def test_register_with_params(self, mgr):
        mgr.register("t1", command="echo", params={"timeout": 10})
        info = mgr.info("t1")
        assert info.params == {"timeout": 10}


class TestList:
    def test_list_empty(self, mgr):
        assert mgr.list() == []

    def test_list_groups_by_category(self, mgr):
        mgr.register("a", command="echo a", category="quality")
        mgr.register("b", command="echo b", category="general")
        mgr.register("c", command="echo c", category="quality")
        result = mgr.list()
        cats = [g["category"] for g in result]
        assert cats == ["general", "quality"]
        quality_tools = next(g["tools"] for g in result if g["category"] == "quality")
        assert len(quality_tools) == 2

    def test_list_filter_by_category(self, mgr):
        mgr.register("a", command="echo a", category="quality")
        mgr.register("b", command="echo b", category="general")
        result = mgr.list(category="quality")
        assert len(result) == 1
        assert result[0]["category"] == "quality"
        assert len(result[0]["tools"]) == 1


class TestFind:
    def test_find_by_id(self, mgr):
        mgr.register("lint", command="ruff", description="Linter")
        results = mgr.find("lint")
        assert len(results) == 1
        assert results[0].id == "lint"

    def test_find_by_description(self, mgr):
        mgr.register("t1", command="echo", description="A linter tool")
        results = mgr.find("linter")
        assert len(results) == 1

    def test_find_no_match(self, mgr):
        mgr.register("t1", command="echo")
        assert mgr.find("nonexistent") == []

    def test_find_by_category(self, mgr):
        mgr.register("t1", command="echo", category="quality")
        results = mgr.find("quality")
        assert len(results) == 1


class TestEnableDisable:
    def test_disable_then_enable(self, mgr):
        mgr.register("t1", command="echo")
        assert mgr.disable("t1") is True
        info = mgr.info("t1")
        assert info.enabled is False
        assert mgr.enable("t1") is True
        info = mgr.info("t1")
        assert info.enabled is True

    def test_disable_nonexistent(self, mgr):
        assert mgr.disable("nope") is False

    def test_enable_nonexistent(self, mgr):
        assert mgr.enable("nope") is False


class TestCall:
    # F7d (2026-07-22, Phase F): all tests below are async because
    # ToolManager.call is now async (asyncio.create_subprocess_exec).

    async def test_call_nonexistent(self, mgr):
        result = await mgr.call("nope")
        assert result.ok is False
        assert "not found" in result.error

    async def test_call_disabled(self, mgr):
        mgr.register("t1", command="echo hi")
        mgr.disable("t1")
        result = await mgr.call("t1")
        assert result.ok is False
        assert "disabled" in result.error

    async def test_call_success(self, mgr):
        mgr.register("echo_tool", command='python -c "print(\'hello\')"')
        result = await mgr.call("echo_tool")
        assert result.ok is True
        assert result.exit_code == 0
        assert "hello" in result.output
        assert result.duration_ms >= 0

    async def test_call_updates_stats(self, mgr):
        mgr.register("t1", command='python -c "print(\'hi\')"')
        await mgr.call("t1")
        await mgr.call("t1")
        info = mgr.info("t1")
        assert info.call_count == 2
        assert info.last_called is not None

    async def test_call_with_args(self, mgr):
        mgr.register("py_tool", command="python -c print(42)")
        result = await mgr.call("py_tool")
        assert result.ok is True

    async def test_call_failure_exit_code(self, mgr):
        mgr.register("fail_tool", command="python -c import sys; sys.exit(1)")
        result = await mgr.call("fail_tool")
        assert result.ok is False
        assert result.exit_code == 1

    async def test_call_timeout(self, mgr):
        mgr.register("slow", command='python -c "import time; time.sleep(10)"')
        result = await mgr.call("slow", timeout_seconds=1)
        assert result.ok is False
        assert "timeout" in result.error

    async def test_call_captures_stderr(self, mgr):
        """F7b (2026-07-22, Phase F): stderr is now captured into result.error."""
        mgr.register(
            "err_tool",
            command='python -c "import sys; sys.stderr.write(\'err-msg\'); sys.exit(1)"',
        )
        result = await mgr.call("err_tool")
        assert result.ok is False
        assert result.exit_code == 1
        assert "err-msg" in result.error


class TestDelete:
    def test_delete_existing(self, mgr):
        mgr.register("t1", command="echo")
        assert mgr.delete("t1") is True
        assert mgr.info("t1") is None

    def test_delete_nonexistent(self, mgr):
        assert mgr.delete("nope") is False


class TestStats:
    def test_stats_empty(self, mgr):
        s = mgr.stats()
        assert s["total"] == 0
        assert s["enabled"] == 0
        assert s["total_calls"] == 0

    async def test_stats_with_tools(self, mgr):
        # F7d (Phase F): now async because mgr.call is async.
        mgr.register("a", command='python -c "print(1)"', category="quality")
        mgr.register("b", command='python -c "print(2)"', category="general")
        mgr.disable("b")
        await mgr.call("a")
        s = mgr.stats()
        assert s["total"] == 2
        assert s["enabled"] == 1
        assert s["disabled"] == 1
        assert s["total_calls"] == 1
        assert s["by_category"]["quality"] == 1
        assert s["by_category"]["general"] == 1


class TestRowToTool:
    def test_invalid_params_json(self, mgr):
        # Insert a row with invalid params JSON directly
        with mgr._connect() as conn:
            conn.execute(
                "INSERT INTO tools (id, name, description, command, category, params, enabled, created) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                ("bad", "bad", "", "echo", "general", "{invalid json}", "2026-01-01T00:00:00"),
            )
        info = mgr.info("bad")
        assert info is not None
        assert info.params == {}


class TestCallSyncWrapper:
    """F7 (2026-07-22, Phase F): verify the backward-compatible sync wrapper."""

    def test_call_sync_works_without_event_loop(self, mgr):
        # When called from a non-async context, call_sync runs the async
        # call() via a fresh event loop.
        mgr.register("echo_tool", command='python -c "print(\'hi\')"')
        result = mgr.call_sync("echo_tool")
        assert result.ok is True
        assert "hi" in result.output

    def test_call_sync_nonexistent(self, mgr):
        result = mgr.call_sync("nope")
        assert result.ok is False
        assert "not found" in result.error


# --- Merged from test_tool_manager_coverage3.py ---
# Coverage tests (round 3) for maop.core.tool_manager.
#
# Targets missing lines: 45-47 (_get_maop_version exc), 60-79
# (_parse_version fallback), 92-113 (_is_version_compatible branches),
# 193/197 (migration ALTER), 225/227/229-233 (register validation),
# 340 (call version incompatible), 393-402 (call exceptions),
# 427-430 (call_sync fallback), 444-482 (_call_sync_fallback).

_get_maop_version = _tm._get_maop_version
_is_version_compatible = _tm._is_version_compatible
_parse_version = _tm._parse_version


# ── _get_maop_version ───────────────────────────────────────────────


class TestGetMaopVersion:
    def test_version_import_failure(self):
        """Cover _get_maop_version exception branch (45-47)."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "maop":
                raise ImportError("boom")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = _get_maop_version()
        assert result == ""


# ── _parse_version ──────────────────────────────────────────────────


class TestParseVersion:
    def test_empty_string(self):
        assert _parse_version("") == (0,)

    def test_packaging_available(self):
        """Normal path: packaging.version.Version succeeds."""
        result = _parse_version("1.2.3")
        # Should be a Version object, not a tuple
        assert str(result) == "1.2.3"

    def test_packaging_import_error(self):
        """Cover ImportError fallback (65-66) → tuple path (70-79)."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "packaging.version":
                raise ImportError("no packaging")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = _parse_version("1.2.3")
        assert result == (1, 2, 3)

    def test_packaging_parse_error(self):
        """Cover parse exception fallback (67-68) → tuple path (70-79)."""
        # Force Version() to raise by mocking the import
        with patch("packaging.version.Version", side_effect=Exception("parse boom")):
            result = _parse_version("1.2.3")
        assert result == (1, 2, 3)

    def test_fallback_with_non_digit_suffix(self):
        """Cover tuple fallback stripping non-digit suffix (73-78)."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "packaging.version":
                raise ImportError("no packaging")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = _parse_version("1.2rc1")
        assert result == (1, 2)

    def test_fallback_all_non_digit(self):
        """Cover segment with no digits → 0 (78)."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "packaging.version":
                raise ImportError("no packaging")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = _parse_version("abc.def")
        assert result == (0, 0)


# ── _is_version_compatible ──────────────────────────────────────────


class TestIsVersionCompatible:
    def test_empty_requirement(self):
        """Cover empty min_platform_version (90-91)."""
        assert _is_version_compatible("") is True

    def test_unknown_maop_version(self):
        """Cover unknown MAOP version branch (93-99)."""
        with patch("maop.core.agent.tools.tool_manager._get_maop_version", return_value=""):
            assert _is_version_compatible("2.0") is True

    def test_compatible(self):
        """Cover compatible version (101-102)."""
        with patch("maop.core.agent.tools.tool_manager._get_maop_version", return_value="2.0"):
            assert _is_version_compatible("1.0") is True

    def test_incompatible(self):
        """Cover incompatible version (103-107)."""
        with patch("maop.core.agent.tools.tool_manager._get_maop_version", return_value="1.0"):
            assert _is_version_compatible("2.0") is False

    def test_comparison_exception(self):
        """Cover comparison exception (108-113)."""
        # Make _parse_version raise to trigger the except branch
        with patch(
            "maop.core.agent.tools.tool_manager._parse_version",
            side_effect=TypeError("cannot compare"),
        ), patch(
            "maop.core.agent.tools.tool_manager._get_maop_version", return_value="1.0"
        ):
            assert _is_version_compatible("2.0") is True


# ── ToolManager migration & register validation ─────────────────────


class TestToolManagerMigrationAndValidation:
    def test_migration_adds_version_columns(self, tmp_path):
        """Cover ALTER TABLE migration (193, 197)."""
        # Create a legacy DB without version/min_platform_version columns
        db_path = get_db_path("tool_manager")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(db_path, foreign_keys=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    command TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    params TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    created TEXT NOT NULL,
                    last_called TEXT,
                    call_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                INSERT INTO tools (id, name, description, command, category, params,
                                   enabled, created, last_called, call_count)
                VALUES ('legacy', 'legacy', '', 'echo hi', 'general', '{}', 1, '2024', NULL, 0)
            """)

        # Now init ToolManager — should trigger migration
        mgr = ToolManager(root_dir=str(tmp_path))
        tool = mgr.info("legacy")
        assert tool is not None
        assert tool.version == "1.0"
        assert tool.min_platform_version == ""

    def test_register_empty_tool_id(self, tmp_path):
        """Cover empty tool_id validation (225)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError, match="tool_id"):
            mgr.register("", command="echo hi")

    def test_register_empty_command(self, tmp_path):
        """Cover empty command validation (227)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError, match="command"):
            mgr.register("t1", command="")

    def test_register_version_incompatible(self, tmp_path):
        """Cover version incompatible rejection (229-233)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        with patch(
            "maop.core.agent.tools.tool_manager._is_version_compatible", return_value=False
        ), pytest.raises(ValueError, match="incompatible"):
            mgr.register("t1", command="echo hi", min_platform_version="99.0")


# ── call() exception branches ───────────────────────────────────────


class TestCallExceptions:
    @pytest.mark.asyncio
    async def test_call_version_incompatible(self, tmp_path):
        """Cover call() version incompatible return (340)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        # Register with a compatible version, then mock to make call fail
        mgr.register("t1", command="echo hi")
        with patch(
            "maop.core.agent.tools.tool_manager._is_version_compatible", return_value=False
        ):
            result = await mgr.call("t1")
        assert result.ok is False
        assert "incompatible" in result.error

    @pytest.mark.asyncio
    async def test_call_file_not_found(self, tmp_path):
        """Cover FileNotFoundError branch (393-399)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="nonexistent_cmd_xyz")
        result = await mgr.call("t1")
        assert result.ok is False
        assert result.exit_code == -2
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_call_generic_exception(self, tmp_path):
        """Cover generic Exception branch (400-405)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo hi")

        # Mock create_subprocess_exec to raise a generic exception
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("boom"),
        ):
            result = await mgr.call("t1")
        assert result.ok is False
        assert result.exit_code == -3

    @pytest.mark.asyncio
    async def test_call_timeout(self, tmp_path):
        """Cover timeout branch (362-371)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        # Use a command that sleeps longer than the timeout
        mgr.register("sleeper", command='python -c "import time; time.sleep(10)"')
        result = await mgr.call("sleeper", timeout_seconds=1)
        assert result.ok is False
        assert result.error == "timeout"

    @pytest.mark.asyncio
    async def test_call_with_stderr(self, tmp_path):
        """Cover stderr capture when returncode != 0 (391)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        # python command that writes to stderr and exits 1
        mgr.register(
            "err",
            command='python -c "import sys; sys.stderr.write(\'err msg\'); sys.exit(1)"',
        )
        result = await mgr.call("err")
        assert result.ok is False
        assert "err msg" in result.error

    @pytest.mark.asyncio
    async def test_call_disabled(self, tmp_path):
        """Cover disabled tool branch (337-338)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo hi")
        mgr.disable("t1")
        result = await mgr.call("t1")
        assert result.ok is False
        assert "disabled" in result.error


# ── call_sync() fallback ────────────────────────────────────────────


class TestCallSyncFallback:
    def test_call_sync_normal(self, tmp_path):
        """Cover call_sync normal path."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="python -c print(42)")
        result = mgr.call_sync("t1")
        assert result.ok is True
        assert "42" in result.output

    def test_call_sync_with_running_loop(self, tmp_path):
        """Cover call_sync RuntimeError → _call_sync_fallback (427-430)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="python -c print(42)")

        # Simulate being inside a running event loop
        async def _run():
            # new_event_loop() will succeed, but run_until_complete
            # inside a coroutine raises RuntimeError
            return mgr.call_sync("t1")

        result = asyncio.run(_run())
        assert result.ok is True
        assert "42" in result.output

    def test_call_sync_fallback_not_found(self, tmp_path):
        """Cover _call_sync_fallback tool not found (444-446)."""
        mgr = ToolManager(root_dir=str(tmp_path))

        # Force the fallback path by mocking asyncio.new_event_loop to
        # raise RuntimeError
        async def _run():
            return mgr.call_sync("nonexistent")

        result = asyncio.run(_run())
        assert result.ok is False
        assert "not found" in result.error

    def test_call_sync_fallback_disabled(self, tmp_path):
        """Cover _call_sync_fallback disabled tool (447-448)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo hi")
        mgr.disable("t1")

        async def _run():
            return mgr.call_sync("t1")

        result = asyncio.run(_run())
        assert result.ok is False
        assert "disabled" in result.error

    def test_call_sync_fallback_version_incompatible(self, tmp_path):
        """Cover _call_sync_fallback version incompatible (449-456)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo hi")

        with patch(
            "maop.core.agent.tools.tool_manager._is_version_compatible", return_value=False
        ):
            async def _run():
                return mgr.call_sync("t1")

            result = asyncio.run(_run())
        assert result.ok is False
        assert "incompatible" in result.error

    def test_call_sync_fallback_timeout(self, tmp_path):
        """Cover _call_sync_fallback subprocess timeout (479-480)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("sleeper", command='python -c "import time; time.sleep(10)"')

        async def _run():
            return mgr.call_sync("sleeper", timeout_seconds=1)

        result = asyncio.run(_run())
        assert result.ok is False
        assert result.error == "timeout"

    def test_call_sync_fallback_exception(self, tmp_path):
        """Cover _call_sync_fallback generic exception (481-482)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="echo hi")

        # Mock subprocess.run to raise a generic exception
        with patch("subprocess.run", side_effect=OSError("boom")):
            async def _run():
                return mgr.call_sync("t1")

            result = asyncio.run(_run())
        assert result.ok is False
        assert "boom" in result.error

    def test_call_sync_fallback_with_stderr(self, tmp_path):
        """Cover _call_sync_fallback stderr capture (477)."""
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register(
            "err",
            command='python -c "import sys; sys.stderr.write(\'err\'); sys.exit(1)"',
        )

        async def _run():
            return mgr.call_sync("err")

        result = asyncio.run(_run())
        assert result.ok is False
        assert "err" in result.error


# ── 工具白名单策略（T1） ───────────────────────────────────────

class TestToolPolicyUnit:
    """ToolPolicy 决策逻辑单测（deny/allow/mode/env/fail-open）。"""

    @staticmethod
    def _write_policy(tmp_path, text: str) -> ToolPolicy:
        cfg = tmp_path / "tool_whitelist.yaml"
        cfg.write_text(text, encoding="utf-8")
        return ToolPolicy(config_path=cfg)

    def test_id_exact_match(self, tmp_path):
        policy = self._write_policy(
            tmp_path, "mode: enforce\nallow:\n  - id: lint\n"
        )
        assert policy.check("lint", "ruff check").allowed is True
        assert policy.check("linter", "ruff check").allowed is False

    def test_pattern_matches_id_and_command(self, tmp_path):
        policy = self._write_policy(
            tmp_path, "mode: enforce\nallow:\n  - pattern: 'ruff*'\n"
        )
        # id 命中
        assert policy.check("ruff-check", "ruff check src/").allowed is True
        # command 命中
        assert policy.check("lint", "ruff check src/").allowed is True
        # 均未命中
        assert policy.check("lint", "pylint src/").allowed is False

    def test_deny_overrides_allow(self, tmp_path):
        policy = self._write_policy(
            tmp_path,
            "mode: enforce\nallow:\n  - id: t1\ndeny:\n  - id: t1\n",
        )
        decision = policy.check("t1", "echo hi")
        assert decision.allowed is False
        assert decision.matched == "deny"

    def test_enforce_denies_unlisted(self, tmp_path):
        policy = self._write_policy(tmp_path, "mode: enforce\nallow: []\n")
        decision = policy.check("t1", "echo hi")
        assert decision.allowed is False
        assert "not in allow list" in decision.reason

    def test_audit_allows_unlisted_with_warning(self, tmp_path, caplog):
        policy = self._write_policy(tmp_path, "mode: audit\nallow: []\n")
        with caplog.at_level(logging.WARNING, logger="maop.core.agent.tools.tool_policy"):
            decision = policy.check("t1", "echo hi")
        assert decision.allowed is True
        assert any("not in whitelist" in r.message for r in caplog.records)

    def test_env_mode_overrides_config(self, tmp_path, monkeypatch):
        policy = self._write_policy(tmp_path, "mode: audit\nallow: []\n")
        assert policy.mode == "audit"
        monkeypatch.setenv("MAOP_TOOL_POLICY_MODE", "enforce")
        assert policy.mode == "enforce"
        assert policy.check("t1", "echo hi").allowed is False

    def test_fail_open_missing_config(self, tmp_path, caplog):
        policy = ToolPolicy(config_path=tmp_path / "nope.yaml")
        assert policy.mode == "audit"
        with caplog.at_level(logging.WARNING, logger="maop.core.agent.tools.tool_policy"):
            decision = policy.check("t1", "echo hi")
        assert decision.allowed is True
        assert any("fail-open" in r.message for r in caplog.records)

    def test_fail_open_invalid_yaml(self, tmp_path):
        cfg = tmp_path / "tool_whitelist.yaml"
        cfg.write_text("mode: [unclosed", encoding="utf-8")
        policy = ToolPolicy(config_path=cfg)
        assert policy.mode == "audit"
        assert policy.check("t1", "echo hi").allowed is True

    def test_invalid_mode_falls_back_to_audit(self, tmp_path):
        policy = self._write_policy(tmp_path, "mode: banana\nallow: []\n")
        assert policy.mode == "audit"
        assert policy.check("t1", "echo hi").allowed is True


class TestToolPolicyIntegration:
    """ToolManager.call() / call_sync() / _call_sync_fallback() 策略拦截。"""

    async def test_enforce_blocks_call_no_subprocess(self, mgr, monkeypatch):
        """enforce 下未放行工具 call() 返回 ok=False 且 create_subprocess_exec 零调用。"""
        monkeypatch.setenv("MAOP_TOOL_POLICY_MODE", "enforce")
        mgr.register("t1", command="python -c print(1)")
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await mgr.call("t1")
        assert result.ok is False
        assert result.error.startswith("tool not allowed: t1:")
        mock_exec.assert_not_called()

    async def test_audit_allows_with_warning(self, mgr, caplog):
        """audit 放行但 warning。"""
        mgr.register("t1", command="python -c print(1)")
        with caplog.at_level(logging.WARNING, logger="maop.core.agent.tools.tool_policy"):
            result = await mgr.call("t1")
        assert result.ok is True
        assert any("not in whitelist" in r.message for r in caplog.records)

    async def test_deny_overrides_allow_through_call(self, tmp_path):
        """deny 优先于 allow（经 call() 全链路）。"""
        cfg = tmp_path / "tool_whitelist.yaml"
        cfg.write_text(
            "mode: enforce\nallow:\n  - id: t1\ndeny:\n  - id: t1\n",
            encoding="utf-8",
        )
        mgr = ToolManager(root_dir=tmp_path, tool_policy=ToolPolicy(config_path=cfg))
        mgr.register("t1", command="python -c print(1)")
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await mgr.call("t1")
        assert result.ok is False
        assert "tool not allowed" in result.error
        mock_exec.assert_not_called()

    def test_call_sync_blocked_enforce(self, mgr, monkeypatch):
        """call_sync 正常路径（内部走 call()）被策略拦。"""
        monkeypatch.setenv("MAOP_TOOL_POLICY_MODE", "enforce")
        mgr.register("t1", command="python -c print(1)")
        result = mgr.call_sync("t1")
        assert result.ok is False
        assert "tool not allowed" in result.error

    def test_call_sync_fallback_blocked(self, tmp_path, monkeypatch):
        """_call_sync_fallback 双路径均被拦（subprocess.run 零调用）。"""
        monkeypatch.setenv("MAOP_TOOL_POLICY_MODE", "enforce")
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("t1", command="python -c print(1)")
        with patch("subprocess.run") as mock_run:
            async def _run():
                return mgr.call_sync("t1")

            result = asyncio.run(_run())
        assert result.ok is False
        assert "tool not allowed" in result.error
        mock_run.assert_not_called()
