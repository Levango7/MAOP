"""Tests for A2A protocol implementation."""
from __future__ import annotations

import pytest

from maop.core.agent.delegation.a2a import (
    A2ACard,
    A2AManager,
    A2AMessage,
    A2AResponse,
    A2ATaskState,
)


class TestA2ACard:
    def test_defaults(self):
        card = A2ACard(name="test-agent")
        assert card.name == "test-agent"
        assert card.capabilities == []
        assert card.provider == "maop"
        assert card.version == "1.0.0"

    def test_with_capabilities(self):
        card = A2ACard(name="reviewer", capabilities=["review", "suggest"], endpoint="http://localhost:8080")
        assert len(card.capabilities) == 2
        assert card.endpoint == "http://localhost:8080"


class TestA2AMessage:
    def test_defaults(self):
        msg = A2AMessage()
        assert msg.jsonrpc == "2.0"
        assert msg.method == "tasks/send"
        assert len(msg.id) == 32

    def test_custom_method(self):
        msg = A2AMessage(method="agent/card", params={"name": "test"})
        assert msg.method == "agent/card"
        assert msg.params["name"] == "test"


class TestA2AResponse:
    def test_success(self):
        resp = A2AResponse(id="1", result={"status": "ok"})
        assert resp.error is None
        assert resp.result["status"] == "ok"

    def test_error(self):
        resp = A2AResponse(id="1", error={"code": -32601, "message": "not found"})
        assert resp.error is not None


class TestA2ATaskState:
    def test_defaults(self):
        task = A2ATaskState()
        assert task.status == "submitted"
        assert task.artifacts == []
        assert len(task.task_id) == 32


class TestA2AManager:
    def test_register_and_get_card(self):
        mgr = A2AManager()
        card = A2ACard(name="coder", capabilities=["code"])
        mgr.register_card(card)
        assert mgr.get_card("coder") is not None
        assert mgr.get_card("nonexistent") is None

    def test_list_cards(self):
        mgr = A2AManager()
        mgr.register_card(A2ACard(name="a"))
        mgr.register_card(A2ACard(name="b"))
        assert len(mgr.list_cards()) == 2

    def test_create_task(self):
        mgr = A2AManager()
        task = mgr.create_task("coder", "write a function")
        assert task.status == "submitted"
        assert len(task.history) == 1
        assert task.history[0]["role"] == "user"

    def test_update_task(self):
        mgr = A2AManager()
        task = mgr.create_task("coder", "test")
        updated = mgr.update_task(task.task_id, "completed", artifact={"output": "done"})
        assert updated is not None
        assert updated.status == "completed"
        assert len(updated.artifacts) == 1

    def test_update_nonexistent_task(self):
        mgr = A2AManager()
        assert mgr.update_task("fake", "completed") is None

    def test_handle_message_agent_card(self):
        mgr = A2AManager()
        mgr.register_card(A2ACard(name="coder", capabilities=["code"]))
        msg = A2AMessage(method="agent/card", params={"name": "coder"})
        resp = mgr.handle_message(msg)
        assert resp.error is None
        assert resp.result["name"] == "coder"

    def test_handle_message_agent_card_not_found(self):
        mgr = A2AManager()
        msg = A2AMessage(method="agent/card", params={"name": "missing"})
        resp = mgr.handle_message(msg)
        assert resp.error is not None

    def test_handle_message_tasks_send(self):
        mgr = A2AManager()
        msg = A2AMessage(method="tasks/send", params={"agent": "coder", "message": "hello"})
        resp = mgr.handle_message(msg)
        assert resp.error is None
        assert "task_id" in resp.result

    def test_handle_message_tasks_send_missing_params(self):
        mgr = A2AManager()
        msg = A2AMessage(method="tasks/send", params={})
        resp = mgr.handle_message(msg)
        assert resp.error is not None

    def test_handle_message_tasks_get(self):
        mgr = A2AManager()
        task = mgr.create_task("coder", "test")
        msg = A2AMessage(method="tasks/get", params={"task_id": task.task_id})
        resp = mgr.handle_message(msg)
        assert resp.error is None
        assert resp.result["task_id"] == task.task_id

    def test_handle_message_tasks_cancel(self):
        mgr = A2AManager()
        task = mgr.create_task("coder", "test")
        msg = A2AMessage(method="tasks/cancel", params={"task_id": task.task_id})
        resp = mgr.handle_message(msg)
        assert resp.error is None
        assert resp.result["status"] == "canceled"

    def test_handle_message_unknown_method(self):
        mgr = A2AManager()
        msg = A2AMessage(method="unknown/method", params={})
        resp = mgr.handle_message(msg)
        assert resp.error is not None
        assert "not supported" in resp.error["message"]



# ── t11: tasks/send worker dispatch ───────────────────────────────


class TestTaskDispatch:
    """Verify that tasks/send dispatches to the injected worker pool."""

    @pytest.mark.asyncio
    async def test_dispatch_task_with_mock_pool(self, tmp_path):
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))

        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(return_value={"output": "ok", "exit_code": 0})
        manager.set_worker_pool(mock_pool)

        task = manager.create_task("coder", "review this code")
        await manager.dispatch_task(task.task_id, "coder", "review this code")

        # Worker was invoked.
        mock_pool.submit.assert_awaited_once()
        # Task state reflects completion.
        updated = manager.get_task(task.task_id)
        assert updated is not None
        assert updated.status == "completed"
        assert len(updated.artifacts) == 1
        assert updated.artifacts[0]["agent"] == "coder"
        assert "result" in updated.artifacts[0]

    @pytest.mark.asyncio
    async def test_dispatch_task_propagates_failure(self, tmp_path):
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))

        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(side_effect=RuntimeError("worker crashed"))
        manager.set_worker_pool(mock_pool)

        task = manager.create_task("coder", "do work")
        await manager.dispatch_task(task.task_id, "coder", "do work")

        updated = manager.get_task(task.task_id)
        assert updated is not None
        assert updated.status == "failed"
        assert "worker crashed" in updated.artifacts[0]["error"]

    @pytest.mark.asyncio
    async def test_dispatch_task_without_pool_is_noop(self, tmp_path):
        manager = A2AManager(root_dir=str(tmp_path))
        task = manager.create_task("coder", "noop")
        # Should not raise; worker_pool is None.
        await manager.dispatch_task(task.task_id, "coder", "noop")
        updated = manager.get_task(task.task_id)
        assert updated is not None
        # Status remains "submitted" because dispatch was a noop.
        assert updated.status == "submitted"

    def test_tasks_send_returns_immediately_with_pool_set(self, tmp_path):
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))
        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(return_value={"output": "ok"})
        manager.set_worker_pool(mock_pool)

        msg = A2AMessage(
            method="tasks/send",
            params={"agent": "coder", "message": "do work"},
        )
        response = manager.handle_message(msg)
        assert response.error is None
        assert "task_id" in response.result
        assert response.result["status"] == "submitted"

    def test_set_worker_pool_logs_injection(self, tmp_path):
        from unittest.mock import MagicMock
        manager = A2AManager(root_dir=str(tmp_path))
        manager.set_worker_pool(MagicMock())
        assert manager._worker_pool is not None


# ── F6a (2026-07-22, Phase F): agent_name routing ──────────────────


class TestAgentNameRouting:
    """F6a: verify dispatch_task forwards agent_name to WorkerPool.submit.

    Previously, dispatch_task only passed `message` and `workdir` to
    `worker_pool.submit()` — the A2A caller's `agent_name` was recorded
    only as artifact metadata, so the worker had no way to actually
    execute with the requested agent. These tests assert the new
    agent_name forwarding contract so A2A dispatch really routes to the
    agent specified by the caller. See ADR-013.
    """

    @pytest.mark.asyncio
    async def test_dispatch_forwards_agent_name_to_submit(self, tmp_path):
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))

        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(return_value={"output": "ok"})
        manager.set_worker_pool(mock_pool)

        task = manager.create_task("claude", "review this code")
        await manager.dispatch_task(task.task_id, "claude", "review this code")

        # F6a: agent_name must reach submit() as a keyword argument.
        mock_pool.submit.assert_awaited_once()
        _, kwargs = mock_pool.submit.call_args
        assert kwargs.get("agent_name") == "claude"

    @pytest.mark.asyncio
    async def test_dispatch_forwards_distinct_agent_names(self, tmp_path):
        """Two different A2A callers must each pin their own agent."""
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))

        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(return_value={"output": "ok"})
        manager.set_worker_pool(mock_pool)

        t1 = manager.create_task("claude", "task A")
        await manager.dispatch_task(t1.task_id, "claude", "task A")
        t2 = manager.create_task("gpt", "task B")
        await manager.dispatch_task(t2.task_id, "gpt", "task B")

        assert mock_pool.submit.await_count == 2
        first_call_kwargs = mock_pool.submit.call_args_list[0].kwargs
        second_call_kwargs = mock_pool.submit.call_args_list[1].kwargs
        assert first_call_kwargs.get("agent_name") == "claude"
        assert second_call_kwargs.get("agent_name") == "gpt"

    @pytest.mark.asyncio
    async def test_dispatch_artifact_records_correct_agent(self, tmp_path):
        """The completed artifact's `agent` field must match the dispatched agent."""
        from unittest.mock import AsyncMock
        manager = A2AManager(root_dir=str(tmp_path))

        mock_pool = AsyncMock()
        mock_pool.submit = AsyncMock(return_value={"output": "done"})
        manager.set_worker_pool(mock_pool)

        task = manager.create_task("codex", "do thing")
        await manager.dispatch_task(task.task_id, "codex", "do thing")

        updated = manager.get_task(task.task_id)
        assert updated is not None
        assert updated.status == "completed"
        assert updated.artifacts[0]["agent"] == "codex"
