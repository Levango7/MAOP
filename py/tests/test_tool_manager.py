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


import pytest

from maop.core.tool_manager import ToolCallResult, ToolDef, ToolManager


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
