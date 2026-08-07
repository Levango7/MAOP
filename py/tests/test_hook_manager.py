"""Tests for MAOP.core.hook_manager — Unified lifecycle hook framework."""

import asyncio
from unittest.mock import patch

import pytest

from maop.core.agent.plugins_hooks.hook_manager import (
    HookManager,
    HookType,
    LifecycleEvent,
)


@pytest.fixture
def mgr(tmp_path):
    return HookManager(root_dir=str(tmp_path))


class TestHookRegister:
    def test_register_callback(self, mgr):
        def my_cb(event, data):
            pass
        hdef = mgr.register(event="agent.pre_dispatch", callback=my_cb, description="test")
        assert hdef.id.startswith("hk-")
        assert hdef.hook_type == HookType.CALLBACK
        assert hdef.enabled

    def test_register_webhook(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook")
        assert hdef.hook_type == HookType.WEBHOOK
        assert hdef.url == "https://example.com/hook"

    def test_register_no_callback_or_url(self, mgr):
        with pytest.raises(ValueError, match="Must provide"):
            mgr.register(event="agent.pre_dispatch")

    def test_register_custom_id(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook", hook_id="my-custom-id")
        assert hdef.id == "my-custom-id"

    def test_register_priority(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook", priority=10)
        assert hdef.priority == 10


class TestHookUnregister:
    def test_unregister_existing(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook")
        result = mgr.unregister(hdef.id)
        assert result is True

    def test_unregister_not_found(self, mgr):
        result = mgr.unregister("hk-nonexistent")
        assert result is False


class TestHookEnableDisable:
    def test_enable(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook")
        mgr.disable(hdef.id)
        result = mgr.enable(hdef.id)
        assert result is True

    def test_disable(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook")
        result = mgr.disable(hdef.id)
        assert result is True

    def test_enable_not_found(self, mgr):
        assert mgr.enable("nonexistent") is False


class TestHookTrigger:
    def test_trigger_callback(self, mgr):
        received = []
        def my_cb(event, data):
            received.append({"event": event, "data": data})

        mgr.register(event="agent.pre_dispatch", callback=my_cb)
        results = asyncio.run(mgr.trigger("agent.pre_dispatch", {"agent": "coder"}))
        assert len(results) == 1
        assert results[0].success
        assert len(received) == 1
        assert received[0]["data"]["agent"] == "coder"

    def test_trigger_async_callback(self, mgr):
        received = []
        async def my_async_cb(event, data):
            received.append({"event": event, "data": data})

        mgr.register(event="loop.complete", callback=my_async_cb)
        results = asyncio.run(mgr.trigger("loop.complete", {"status": "ok"}))
        assert len(results) == 1
        assert results[0].success
        assert len(received) == 1

    def test_trigger_no_hooks(self, mgr):
        results = asyncio.run(mgr.trigger("system.start", {}))
        assert results == []

    def test_trigger_wildcard_match(self, mgr):
        received = []
        def my_cb(event, data):
            received.append(event)

        mgr.register(event="agent.*", callback=my_cb)
        results = asyncio.run(mgr.trigger("agent.pre_dispatch", {"agent": "coder"}))
        assert len(results) == 1
        assert results[0].success
        assert len(received) == 1

    def test_trigger_priority_order(self, mgr):
        order = []
        def cb_low(event, data):
            order.append("low")
        def cb_high(event, data):
            order.append("high")

        mgr.register(event="loop.complete", callback=cb_low, priority=0)
        mgr.register(event="loop.complete", callback=cb_high, priority=10)
        asyncio.run(mgr.trigger("loop.complete", {}))
        assert order == ["high", "low"]

    def test_trigger_disabled_hook(self, mgr):
        received = []
        def my_cb(event, data):
            received.append(event)

        hdef = mgr.register(event="loop.complete", callback=my_cb)
        mgr.disable(hdef.id)
        results = asyncio.run(mgr.trigger("loop.complete", {}))
        assert len(results) == 0
        assert len(received) == 0


class TestHookTriggerWebhook:
    def test_trigger_webhook_no_httpx(self, mgr):
        mgr.register(event="loop.complete", url="https://example.com/hook")
        with patch.dict("sys.modules", {"httpx": None}):
            results = asyncio.run(mgr.trigger("loop.complete", {}))
            assert len(results) == 1
            assert not results[0].success
            assert "httpx" in results[0].error


class TestHookQuery:
    def test_list_hooks(self, mgr):
        mgr.register(event="agent.pre_dispatch", url="https://a.com/hook")
        mgr.register(event="loop.complete", url="https://b.com/hook")
        hooks = mgr.list_hooks()
        assert len(hooks) >= 2

    def test_list_hooks_by_event(self, mgr):
        mgr.register(event="agent.pre_dispatch", url="https://a.com/hook")
        mgr.register(event="loop.complete", url="https://b.com/hook")
        hooks = mgr.list_hooks(event="agent.pre_dispatch")
        assert all(h.event == "agent.pre_dispatch" for h in hooks)

    def test_get_hook(self, mgr):
        hdef = mgr.register(event="loop.complete", url="https://example.com/hook")
        fetched = mgr.get_hook(hdef.id)
        assert fetched is not None
        assert fetched.id == hdef.id

    def test_get_hook_not_found(self, mgr):
        assert mgr.get_hook("nonexistent") is None

    def test_get_logs(self, mgr):
        def my_cb(event, data):
            pass
        mgr.register(event="loop.complete", callback=my_cb)
        asyncio.run(mgr.trigger("loop.complete", {}))
        logs = mgr.get_logs()
        assert len(logs) >= 1


class TestLifecycleEvent:
    def test_all_events_have_domain_and_phase(self):
        for e in LifecycleEvent:
            parts = e.value.split(".")
            assert len(parts) == 2, f"Event {e.value} should be '<domain>.<phase>'"

    def test_agent_events(self):
        assert LifecycleEvent.AGENT_PRE_DISPATCH.value == "agent.pre_dispatch"
        assert LifecycleEvent.AGENT_POST_DISPATCH.value == "agent.post_dispatch"
        assert LifecycleEvent.AGENT_ON_ERROR.value == "agent.on_error"

    def test_loop_events(self):
        assert LifecycleEvent.LOOP_COMPLETE.value == "loop.complete"
        assert LifecycleEvent.LOOP_PRE_EXECUTE.value == "loop.pre_execute"

    def test_system_events(self):
        assert LifecycleEvent.SYSTEM_START.value == "system.start"
        assert LifecycleEvent.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker.open"

    def test_memory_events(self):
        assert LifecycleEvent.MEMORY_WRITE.value == "memory.write"
        assert LifecycleEvent.MEMORY_READ.value == "memory.read"
        assert LifecycleEvent.MEMORY_CONSOLIDATE.value == "memory.consolidate"

    def test_agent_lifecycle_events(self):
        assert LifecycleEvent.AGENT_SPAWN.value == "agent.spawn"
        assert LifecycleEvent.AGENT_COMPLETE.value == "agent.complete"
        assert LifecycleEvent.AGENT_EVOLVE.value == "agent.evolve"

    def test_model_events(self):
        assert LifecycleEvent.MODEL_REQUEST_BEFORE.value == "model.request_before"
        assert LifecycleEvent.MODEL_RESPONSE_AFTER.value == "model.response_after"


class TestWildcardMatch:
    def test_exact_match(self):
        assert HookManager._wildcard_match("agent.*", "agent.pre_dispatch") is True

    def test_no_match(self):
        assert HookManager._wildcard_match("agent.*", "loop.complete") is False

    def test_non_wildcard_pattern(self):
        assert HookManager._wildcard_match("agent.pre_dispatch", "agent.pre_dispatch") is False


class TestYAMLLoading:
    def test_load_from_yaml(self, mgr, tmp_path):
        yaml_content = """
hooks:
  - event: agent.pre_dispatch
    url: https://example.com/guard
    priority: 10
  - event: loop.complete
    url: https://example.com/audit
    description: "Post-loop audit"
"""
        yaml_file = tmp_path / "test_hooks.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")
        loaded = mgr.load_from_yaml(str(yaml_file))
        assert loaded == 2
        hooks = mgr.list_hooks()
        assert len(hooks) >= 2

    def test_load_from_nonexistent_yaml(self, mgr):
        loaded = mgr.load_from_yaml("/nonexistent/path.yaml")
        assert loaded == 0

    def test_load_yaml_no_hooks_section(self, mgr, tmp_path):
        yaml_file = tmp_path / "no_hooks.yaml"
        yaml_file.write_text("agents: {}\n", encoding="utf-8")
        loaded = mgr.load_from_yaml(str(yaml_file))
        assert loaded == 0


class TestEventBusBridge:
    def test_bridge_event_bus(self, mgr):
        from maop.core.reliability.event_bus import EventBus
        bus = EventBus()
        mgr.bridge_event_bus(bus)
        assert mgr._event_bus is bus


class TestHookChainPropagation:
    def test_modified_data_propagates(self, mgr):
        received = []

        def hook_a(event, data):
            return {"decision": "allow", "modified_data": {"added_by": "a", "value": 10}}

        def hook_b(event, data):
            received.append(dict(data))
            return {"decision": "allow", "modified_data": {"added_by": "b"}}

        mgr.register(event="test.chain", callback=hook_a, priority=10)
        mgr.register(event="test.chain", callback=hook_b, priority=5)

        results = asyncio.run(mgr.trigger("test.chain", {"original": True}))
        assert len(results) == 2
        assert results[0].modified_data.get("added_by") == "a"
        assert len(received) == 1
        assert received[0]["original"] is True
        assert received[0]["added_by"] == "a"
        assert received[0]["value"] == 10

    def test_deny_breaks_chain(self, mgr):
        call_count = {"n": 0}

        def hook_deny(event, data):
            call_count["n"] += 1
            return {"decision": "deny", "modified_data": {"blocked": True}}

        def hook_after(event, data):
            call_count["n"] += 1
            return {"decision": "allow"}

        mgr.register(event="test.deny", callback=hook_deny, priority=10)
        mgr.register(event="test.deny", callback=hook_after, priority=5)

        results = asyncio.run(mgr.trigger("test.deny", {}))
        assert len(results) == 1
        assert results[0].decision == "deny"
        assert call_count["n"] == 1

    def test_chain_preserves_original_data(self, mgr):
        received = []

        def hook_modify(event, data):
            return {"decision": "allow", "modified_data": {"extra": "value"}}

        def hook_read(event, data):
            received.append(dict(data))

        mgr.register(event="test.preserve", callback=hook_modify, priority=10)
        mgr.register(event="test.preserve", callback=hook_read, priority=5)

        asyncio.run(mgr.trigger("test.preserve", {"base": 42}))
        assert received[0]["base"] == 42
        assert received[0]["extra"] == "value"

    def test_chain_no_modification(self, mgr):
        def hook_passthrough(event, data):
            return None

        def hook_read(event, data):
            return {"decision": "allow", "modified_data": {"seen": data}}

        mgr.register(event="test.pass", callback=hook_passthrough, priority=10)
        mgr.register(event="test.pass", callback=hook_read, priority=5)

        results = asyncio.run(mgr.trigger("test.pass", {"key": "val"}))
        assert len(results) == 2
        assert results[1].modified_data["seen"]["key"] == "val"



# ── Fail-open / Fail-closed policy (t09) ──────────────────────────


class TestFailOpenPolicy:
    """Verify that callback failures do not break the chain by default."""

    def test_fail_open_default_continues_chain(self, mgr):
        """In fail-open mode (default), a faulty callback does not stop
        subsequent hooks; the failed hook is logged with success=False."""

        def good_cb(event, data):
            data["good_called"] = True
            return {"modified_data": {"good_called": True}}

        def bad_cb(event, data):
            raise RuntimeError("boom")

        call_log = []

        def after_cb(event, data):
            call_log.append("after")

        # bad_cb runs first (priority 10), then good_cb (priority 5), then after_cb (priority 0).
        mgr.register(event="agent.pre_dispatch", callback=bad_cb, priority=10)
        mgr.register(event="agent.pre_dispatch", callback=good_cb, priority=5)
        mgr.register(event="agent.pre_dispatch", callback=after_cb, priority=0)

        assert mgr._fail_open is True  # default
        results = asyncio.run(mgr.trigger("agent.pre_dispatch", {}))

        # All three hooks were attempted; bad_cb has success=False.
        assert len(results) == 3
        bad_result = next(r for r in results if r.hook_id == results[0].hook_id)
        assert bad_result.success is False
        assert "boom" in bad_result.error
        # Chain continued: after_cb ran.
        assert call_log == ["after"]

    def test_fail_closed_stops_chain_on_exception(self, mgr):
        """In fail-closed mode, a faulty callback raises and aborts."""

        def bad_cb(event, data):
            raise RuntimeError("boom")

        def never_called(event, data):
            return {"decision": "allow"}

        # Same event; bad_cb runs first (priority 10).
        mgr.register(event="loop.complete", callback=bad_cb, priority=10)
        mgr.register(event="loop.complete", callback=never_called, priority=0)

        mgr.set_fail_open(False)
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(mgr.trigger("loop.complete", {}))

    def test_fail_closed_via_env_var(self, tmp_path, monkeypatch):
        """MAOP_HOOK_FAIL_MODE=closed sets the default policy to fail-closed."""
        monkeypatch.setenv("MAOP_HOOK_FAIL_MODE", "closed")
        mgr = HookManager(root_dir=str(tmp_path))
        assert mgr._fail_open is False

    def test_fail_open_explicit_env_var(self, tmp_path, monkeypatch):
        """MAOP_HOOK_FAIL_MODE=open (default) keeps fail-open policy."""
        monkeypatch.delenv("MAOP_HOOK_FAIL_MODE", raising=False)
        mgr = HookManager(root_dir=str(tmp_path))
        assert mgr._fail_open is True

    def test_failed_hook_result_logged_even_in_fail_open(self, mgr):
        """In fail-open mode, failed hook results are still persisted to logs."""
        import sqlite3

        def bad_cb(event, data):
            raise RuntimeError("logged boom")

        mgr.register(event="agent.pre_dispatch", callback=bad_cb)
        asyncio.run(mgr.trigger("agent.pre_dispatch", {}))

        # Check hook_logs table.
        with sqlite3.connect(str(mgr._db_path)) as conn:
            row = conn.execute(
                "SELECT success, error FROM hook_logs WHERE event=? ORDER BY created_at DESC LIMIT 1",
                ("agent.pre_dispatch",),
            ).fetchone()
        assert row is not None
        assert row[0] == 0  # success=0
        assert "logged boom" in row[1]


# ── Persisted callback reload (t09) ───────────────────────────────


class TestPersistedCallbackReload:
    """Verify that hooks registered with a top-level callable can be
    rehydrated after the HookManager is reconstructed (simulating process
    restart)."""

    def test_callback_path_persisted_for_top_level_function(self, mgr):
        """Top-level functions get a non-empty callback_path for reload."""
        from tests.test_hook_manager_helpers import top_level_hook

        hdef = mgr.register(event="agent.pre_dispatch", callback=top_level_hook)
        assert hdef.callback_path == "tests.test_hook_manager_helpers.top_level_hook"

    def test_callback_path_empty_for_closure(self, mgr):
        """Closures cannot be reloaded; callback_path should be empty."""

        def closure_hook(event, data):
            return None

        hdef = mgr.register(event="agent.pre_dispatch", callback=closure_hook)
        assert hdef.callback_path == ""

    def test_persisted_callback_reloads_after_recreate(self, tmp_path):
        """A registered top-level hook should be callable after HookManager
        is rebuilt from the same DB."""
        from tests.test_hook_manager_helpers import hook_calls, top_level_hook

        # Create manager, register top-level hook, trigger it once.
        mgr1 = HookManager(root_dir=str(tmp_path))
        mgr1.register(event="agent.pre_dispatch", callback=top_level_hook)
        asyncio.run(mgr1.trigger("agent.pre_dispatch", {"run": 1}))
        assert hook_calls.count("agent.pre_dispatch") == 1

        # Recreate manager (simulating process restart) and verify reload.
        hook_calls.clear()
        mgr2 = HookManager(root_dir=str(tmp_path))
        # The persisted hook should have been reloaded into mgr2._callbacks.
        results = asyncio.run(mgr2.trigger("agent.pre_dispatch", {"run": 2}))
        assert len(results) == 1
        assert results[0].success is True
        assert hook_calls.count("agent.pre_dispatch") == 1
