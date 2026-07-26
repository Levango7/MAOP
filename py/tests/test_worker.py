"""Tests for MAOP worker modules (agent_executor, queue_worker).

Covers:
  - Signal handling (_handle_signal)
  - Logging setup (_setup_logging)
  - Helper functions
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest


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
