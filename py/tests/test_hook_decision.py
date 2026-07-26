"""Tests for Hook decision influence — deny, modify, and new lifecycle events."""

from __future__ import annotations

import pytest

from maop.core.hook_manager import HookManager, HookResult, LifecycleEvent


def _fresh_mgr(tmp_path):
    return HookManager(root_dir=str(tmp_path / "MAOP_test"))


class TestHookDecision:
    @pytest.mark.asyncio
    async def test_callback_returns_allow(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        mgr.register(event="agent.pre_dispatch", callback=lambda e, d: None)
        results = await mgr.trigger("agent.pre_dispatch", {"agent": "test"})
        assert len(results) == 1
        assert results[0].decision == "allow"

    @pytest.mark.asyncio
    async def test_callback_returns_deny(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        def deny_hook(event, data):
            return {"decision": "deny", "modified_data": {}}
        mgr.register(event="agent.pre_dispatch", callback=deny_hook)
        results = await mgr.trigger("agent.pre_dispatch", {"agent": "test"})
        assert len(results) == 1
        assert results[0].decision == "deny"

    @pytest.mark.asyncio
    async def test_callback_returns_modify(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        def modify_hook(event, data):
            return {"decision": "modify", "modified_data": {"task": "modified task"}}
        mgr.register(event="agent.pre_dispatch", callback=modify_hook)
        results = await mgr.trigger("agent.pre_dispatch", {"agent": "test"})
        assert results[0].decision == "modify"
        assert results[0].modified_data["task"] == "modified task"

    @pytest.mark.asyncio
    async def test_callback_returns_hook_result(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        def result_hook(event, data):
            return HookResult(hook_id="custom", event=event, decision="deny")
        mgr.register(event="agent.pre_dispatch", callback=result_hook)
        results = await mgr.trigger("agent.pre_dispatch", {"agent": "test"})
        assert results[0].decision == "deny"
        assert results[0].hook_id == "custom"

    @pytest.mark.asyncio
    async def test_async_callback_returns_deny(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        async def async_deny(event, data):
            return {"decision": "deny"}
        mgr.register(event="agent.pre_dispatch", callback=async_deny)
        results = await mgr.trigger("agent.pre_dispatch", {"agent": "test"})
        assert results[0].decision == "deny"


class TestNewLifecycleEvents:
    def test_verify_events_exist(self):
        assert hasattr(LifecycleEvent, "LOOP_PRE_VERIFY")
        assert hasattr(LifecycleEvent, "LOOP_POST_VERIFY")
        assert LifecycleEvent.LOOP_PRE_VERIFY.value == "loop.pre_verify"
        assert LifecycleEvent.LOOP_POST_VERIFY.value == "loop.post_verify"

    @pytest.mark.asyncio
    async def test_trigger_verify_events(self, tmp_path):
        mgr = _fresh_mgr(tmp_path)
        triggered = []
        def capture(event, data):
            triggered.append(event)
        mgr.register(event="loop.pre_verify", callback=capture)
        mgr.register(event="loop.post_verify", callback=capture)
        await mgr.trigger("loop.pre_verify", {})
        await mgr.trigger("loop.post_verify", {})
        assert "loop.pre_verify" in triggered
        assert "loop.post_verify" in triggered
