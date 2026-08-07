"""Tests for MAOP worker modules (agent_executor, queue_worker).

Covers:
  - Signal handling (_handle_signal)
  - Logging setup (_setup_logging)
  - Helper functions
"""

from __future__ import annotations

import os
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from maop.worker import queue_worker

# ── agent_executor tests ─────────────────────────────────────


class TestAgentExecutorSignalHandling:
    def test_handle_signal_sets_shutdown(self):
        from maop.worker import agent_executor

        agent_executor._shutdown = False
        agent_executor._handle_signal(15, None)  # SIGTERM
        assert agent_executor._shutdown is True
        # Reset
        agent_executor._shutdown = False

    def test_handle_signal_sigint(self):
        from maop.worker import agent_executor

        agent_executor._shutdown = False
        agent_executor._handle_signal(2, None)  # SIGINT
        assert agent_executor._shutdown is True
        agent_executor._shutdown = False


class TestAgentExecutorLogging:
    def test_setup_logging_basic(self):
        from maop.worker.agent_executor import _setup_logging

        with patch.dict(os.environ, {"MAOP_LOG_LEVEL": "DEBUG", "MAOP_JSON_LOG": "0"}):
            _setup_logging()
            # Should not raise

    def test_setup_logging_json(self):
        from maop.worker.agent_executor import _setup_logging

        with patch.dict(os.environ, {"MAOP_LOG_LEVEL": "INFO", "MAOP_JSON_LOG": "1"}):
            _setup_logging()
            # Should not raise


class TestAgentExecutorConstants:
    def test_root_default(self):
        from maop.worker.agent_executor import ROOT

        # ROOT is set from env or defaults to /app
        assert ROOT is not None

    def test_data_dir_default(self):
        from maop.worker.agent_executor import DATA_DIR

        assert DATA_DIR is not None


# ── queue_worker tests ───────────────────────────────────────


class TestQueueWorkerSignalHandling:
    def test_handle_signal_sets_shutdown(self):
        from maop.worker import queue_worker

        queue_worker._shutdown = False
        queue_worker._handle_signal(15, None)
        assert queue_worker._shutdown is True
        queue_worker._shutdown = False


class TestQueueWorkerLogging:
    def test_setup_logging_basic(self):
        from maop.worker.queue_worker import _setup_logging

        with patch.dict(os.environ, {"MAOP_LOG_LEVEL": "WARNING", "MAOP_JSON_LOG": "0"}):
            _setup_logging()

    def test_setup_logging_json(self):
        from maop.worker.queue_worker import _setup_logging

        with patch.dict(os.environ, {"MAOP_LOG_LEVEL": "INFO", "MAOP_JSON_LOG": "1"}):
            _setup_logging()


class TestQueueWorkerHelpers:
    def test_process_human_approvals_no_db(self, tmp_path):
        """_process_human_approvals returns 0 when no DB exists."""
        from maop.worker.queue_worker import _process_human_approvals

        with patch("maop.worker.queue_worker.ROOT", tmp_path):
            count = _process_human_approvals()
            assert count == 0

    def test_process_queue_stats_no_db(self, tmp_path):
        """_process_queue_stats runs without error when no DB exists."""
        from maop.worker.queue_worker import _process_queue_stats

        with patch("maop.worker.queue_worker.ROOT", tmp_path):
            _process_queue_stats()  # Should not raise


class TestQueueWorkerConstants:
    def test_root_default(self):
        from maop.worker.queue_worker import ROOT

        assert ROOT is not None

    def test_data_dir_default(self):
        from maop.worker.queue_worker import DATA_DIR

        assert DATA_DIR is not None


class TestQueueWorkerDispatch:
    """OPS-12: an unknown topic must NOT be silently acked and dropped.

    A mis-typed producer topic that still lands on a consumed topic used to
    be logged at INFO and acked, losing the message permanently. It must now
    raise so the caller NACKs it (retry -> dead-letter).
    """

    def test_unknown_topic_raises(self):
        from maop.worker.queue_worker import (
            _UnknownTopicError,
            _dispatch_message,
        )

        msg = types.SimpleNamespace(
            topic="definitely_not_a_real_topic", payload={}
        )
        with pytest.raises(_UnknownTopicError):
            _dispatch_message(msg)

    def test_human_approval_is_noop_not_unknown(self):
        # human_approval is intentionally skipped (handled elsewhere) and must
        # NOT be treated as an unknown topic.
        from maop.worker.queue_worker import _dispatch_message

        msg = types.SimpleNamespace(topic="human_approval", payload={})
        _dispatch_message(msg)  # must not raise


# --- Merged from test_queue_worker_coverage.py ---
# Coverage tests for maop.worker.queue_worker.
#
# Exercises the message dispatch / task execution / maintenance / consume
# loop branches that the base test_worker.py does not reach.

# ── _dispatch_message branches ────────────────────────────────

class TestDispatchMessageTopics:
    def test_task_topic_calls_execute_task(self):
        with patch.object(queue_worker, "_execute_task") as mock_exec:
            msg = types.SimpleNamespace(topic="task", payload={"task": "t", "agent_name": "a"})
            queue_worker._dispatch_message(msg)
            mock_exec.assert_called_once_with({"task": "t", "agent_name": "a"})

    def test_maintenance_topic_calls_run_maintenance(self):
        with patch.object(queue_worker, "_run_maintenance") as mock_maint:
            msg = types.SimpleNamespace(topic="maintenance", payload={"job": "backup"})
            queue_worker._dispatch_message(msg)
            mock_maint.assert_called_once_with({"job": "backup"})

    def test_async_bridge_topic_calls_run_maintenance(self):
        with patch.object(queue_worker, "_run_maintenance") as mock_maint:
            msg = types.SimpleNamespace(topic="async_bridge", payload={"job": "x"})
            queue_worker._dispatch_message(msg)
            mock_maint.assert_called_once_with({"job": "x"})

    def test_empty_topic_raises(self):
        from maop.worker.queue_worker import _UnknownTopicError
        msg = types.SimpleNamespace(topic="", payload={})
        with pytest.raises(_UnknownTopicError):
            queue_worker._dispatch_message(msg)

    def test_msg_without_topic_attr_raises(self):
        from maop.worker.queue_worker import _UnknownTopicError
        msg = types.SimpleNamespace(payload={})
        with pytest.raises(_UnknownTopicError):
            queue_worker._dispatch_message(msg)

    def test_msg_without_payload_attr_uses_empty(self):
        """A message with no payload attribute should default to {} and not crash."""
        with patch.object(queue_worker, "_execute_task") as mock_exec:
            msg = types.SimpleNamespace(topic="task")
            queue_worker._dispatch_message(msg)
            mock_exec.assert_called_once_with({})


# ── _execute_task ─────────────────────────────────────────────

class TestExecuteTask:
    def test_missing_task_returns_early(self):
        """Payload without task should log and return without calling WorkerPool."""
        with patch("maop.core.reliability.worker_pool.WorkerPool") as mock_pool_cls:
            queue_worker._execute_task({"agent_name": "a"})
            mock_pool_cls.assert_not_called()

    def test_missing_agent_name_returns_early(self):
        with patch("maop.core.reliability.worker_pool.WorkerPool") as mock_pool_cls:
            queue_worker._execute_task({"task": "t"})
            mock_pool_cls.assert_not_called()

    def test_empty_task_returns_early(self):
        with patch("maop.core.reliability.worker_pool.WorkerPool") as mock_pool_cls:
            queue_worker._execute_task({"task": "", "agent_name": "a"})
            mock_pool_cls.assert_not_called()

    def test_task_execution_success(self):
        """Full lifecycle: pool.start -> submit -> wait -> stop."""
        pool = MagicMock()
        pool.start = AsyncMock()
        pool.stop = AsyncMock()
        pool.submit = AsyncMock(return_value="task-id-1")
        pool.wait = AsyncMock()

        with patch("maop.core.reliability.worker_pool.WorkerPool", return_value=pool):
            # Should not raise
            queue_worker._execute_task({"task": "do work", "agent_name": "claude", "workdir": "/tmp"})

        pool.start.assert_awaited_once()
        pool.submit.assert_awaited_once_with("do work", workdir="/tmp", agent_name="claude")
        pool.wait.assert_awaited_once_with("task-id-1", timeout=300)
        pool.stop.assert_awaited_once()

    def test_task_execution_pool_start_failure_raises(self):
        pool = MagicMock()
        pool.start = AsyncMock(side_effect=RuntimeError("start failed"))
        pool.stop = AsyncMock()

        with patch("maop.core.reliability.worker_pool.WorkerPool", return_value=pool):
            with pytest.raises(RuntimeError, match="start failed"):
                queue_worker._execute_task({"task": "t", "agent_name": "a"})

        # start() is outside the try/finally, so stop() is NOT called
        pool.stop.assert_not_awaited()

    def test_task_execution_submit_failure_still_stops(self):
        pool = MagicMock()
        pool.start = AsyncMock()
        pool.stop = AsyncMock()
        pool.submit = AsyncMock(side_effect=RuntimeError("submit failed"))

        with patch("maop.core.reliability.worker_pool.WorkerPool", return_value=pool):
            with pytest.raises(RuntimeError, match="submit failed"):
                queue_worker._execute_task({"task": "t", "agent_name": "a"})

        pool.stop.assert_awaited_once()


# ── _run_maintenance ──────────────────────────────────────────

class TestRunMaintenance:
    def test_empty_job_returns_early(self):
        """No job field -> just logs and returns."""
        with patch("maop.core.reliability.message_queue.MessageQueue") as mock_mq_cls:
            queue_worker._run_maintenance({})
            mock_mq_cls.assert_not_called()

    def test_unknown_job_is_noop(self):
        """Unknown job name logs but doesn't raise."""
        with patch("maop.core.reliability.message_queue.MessageQueue") as mock_mq_cls:
            queue_worker._run_maintenance({"job": "frobnicate"})
            mock_mq_cls.assert_not_called()

    def test_purge_acked_success(self):
        mq = MagicMock()
        mq.purge_acked = MagicMock(return_value=5)
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            queue_worker._run_maintenance({"job": "purge_acked", "older_than_s": 100.0})
        mq.purge_acked.assert_called_once_with(older_than_s=100.0)

    def test_purge_acked_default_age(self):
        mq = MagicMock()
        mq.purge_acked = MagicMock(return_value=0)
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            queue_worker._run_maintenance({"job": "purge_acked"})
        mq.purge_acked.assert_called_once_with(older_than_s=3600.0)

    def test_purge_acked_failure_raises(self):
        mq = MagicMock()
        mq.purge_acked = MagicMock(side_effect=RuntimeError("db locked"))
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            with pytest.raises(RuntimeError, match="db locked"):
                queue_worker._run_maintenance({"job": "purge_acked"})

    def test_cleanup_dead_letters_success(self):
        mq = MagicMock()
        mq.cleanup_dead_letters = MagicMock(return_value=3)
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            queue_worker._run_maintenance({"job": "cleanup_dead_letters", "older_than_s": 200.0})
        mq.cleanup_dead_letters.assert_called_once_with(older_than_s=200.0)

    def test_cleanup_dead_letters_default_age(self):
        mq = MagicMock()
        mq.cleanup_dead_letters = MagicMock(return_value=0)
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            queue_worker._run_maintenance({"job": "cleanup_dead_letters"})
        mq.cleanup_dead_letters.assert_called_once_with(older_than_s=86400.0)

    def test_cleanup_dead_letters_failure_raises(self):
        mq = MagicMock()
        mq.cleanup_dead_letters = MagicMock(side_effect=RuntimeError("boom"))
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            with pytest.raises(RuntimeError, match="boom"):
                queue_worker._run_maintenance({"job": "cleanup_dead_letters"})


# ── _consume_messages ─────────────────────────────────────────

class TestConsumeMessages:
    def test_no_queue_connection_returns_zero(self):
        """If MessageQueue construction fails, returns 0."""
        with patch("maop.core.reliability.message_queue.MessageQueue", side_effect=RuntimeError("no db")):
            result = queue_worker._consume_messages()
            assert result == 0

    def test_all_topics_drained_no_messages(self):
        """When dequeue returns None for all topics, returns 0."""
        mq = MagicMock()
        mq.dequeue = MagicMock(return_value=None)
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            result = queue_worker._consume_messages()
        assert result == 0
        # Called once per topic (3 topics)
        assert mq.dequeue.call_count == 3

    def test_successful_message_processing(self):
        """A message that dispatches successfully is acked."""
        msg = types.SimpleNamespace(id="msg-1", topic="task", payload={"task": "t", "agent_name": "a"})
        mq = MagicMock()
        mq.dequeue = MagicMock(side_effect=[msg, None, None, None])
        mq.ack = MagicMock()

        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq), \
             patch.object(queue_worker, "_dispatch_message") as mock_dispatch:
            result = queue_worker._consume_messages()

        assert result == 1
        mock_dispatch.assert_called_once_with(msg)
        mq.ack.assert_called_once_with("msg-1", consumer_id="queue-worker-1")

    def test_dispatch_failure_nacks_message(self):
        """When _dispatch_message raises, the message is NACKed."""
        msg = types.SimpleNamespace(id="msg-1", topic="task", payload={})
        mq = MagicMock()
        mq.dequeue = MagicMock(side_effect=[msg, None, None, None])
        mq.nack = MagicMock()

        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq), \
             patch.object(queue_worker, "_dispatch_message", side_effect=RuntimeError("dispatch failed")):
            result = queue_worker._consume_messages()

        assert result == 0
        mq.nack.assert_called_once_with("msg-1", error="dispatch failed")

    def test_nack_failure_does_not_crash(self):
        """If nack itself fails, the worker logs but continues."""
        msg = types.SimpleNamespace(id="msg-1", topic="task", payload={})
        mq = MagicMock()
        mq.dequeue = MagicMock(side_effect=[msg, None, None, None])
        mq.nack = MagicMock(side_effect=RuntimeError("nack failed"))

        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq), \
             patch.object(queue_worker, "_dispatch_message", side_effect=RuntimeError("dispatch failed")):
            # Should not raise
            result = queue_worker._consume_messages()
        assert result == 0

    def test_dequeue_error_breaks_topic(self):
        """If dequeue raises, that topic is skipped (break)."""
        mq = MagicMock()
        mq.dequeue = MagicMock(side_effect=RuntimeError("dequeue error"))
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            result = queue_worker._consume_messages()
        assert result == 0

    def test_shutdown_stops_processing(self):
        """When _shutdown is True, the loop breaks early."""
        queue_worker._shutdown = True
        try:
            mq = MagicMock()
            mq.dequeue = MagicMock(return_value=None)
            with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
                result = queue_worker._consume_messages()
            assert result == 0
        finally:
            queue_worker._shutdown = False


# ── _process_human_approvals / _process_queue_stats ───────────

class TestProcessHelpers:
    def test_process_human_approvals_success(self, tmp_path):
        """When HumanProxy.expire_old succeeds, returns its count."""
        with patch("maop.core.agent.delegation.human_proxy.HumanProxy") as mock_proxy_cls:
            mock_proxy = MagicMock()
            mock_proxy.expire_old = MagicMock(return_value=3)
            mock_proxy_cls.return_value = mock_proxy
            with patch.object(queue_worker, "ROOT", tmp_path):
                result = queue_worker._process_human_approvals()
            assert result == 3

    def test_process_human_approvals_exception_returns_zero(self, tmp_path):
        with patch("maop.core.agent.delegation.human_proxy.HumanProxy", side_effect=RuntimeError("no db")):
            with patch.object(queue_worker, "ROOT", tmp_path):
                result = queue_worker._process_human_approvals()
            assert result == 0

    def test_process_queue_stats_success(self):
        mq = MagicMock()
        mq.stats = MagicMock(return_value={"depth": 5})
        with patch("maop.core.reliability.message_queue.MessageQueue", return_value=mq):
            queue_worker._process_queue_stats()  # should not raise

    def test_process_queue_stats_exception_does_not_raise(self):
        with patch("maop.core.reliability.message_queue.MessageQueue", side_effect=RuntimeError("no db")):
            queue_worker._process_queue_stats()  # should not raise
