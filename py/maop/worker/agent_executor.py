"""MAOP Agent Executor Worker — Consumes agent execution tasks from the queue.

Cloud-native entry point for distributed agent execution.
Reads tasks from the message queue, dispatches via Dispatcher,
and records results.

Environment variables:
  MAOP_ROOT        — Project root directory (default: /app)
  MAOP_DATA_DIR    — Data directory (default: /app/data)
  MAOP_LOG_LEVEL   — Logging level (default: INFO)
  MAOP_BACKEND_QUEUE — Queue backend type (default: local)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

logger = logging.getLogger("maop.worker.agent_executor")

ROOT = Path(os.environ.get("MAOP_ROOT", "/app"))
DATA_DIR = Path(os.environ.get("MAOP_DATA_DIR", str(ROOT / "data")))

_shutdown = False


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown = True


def _setup_logging() -> None:
    level = os.environ.get("MAOP_LOG_LEVEL", "INFO").upper()
    # O-4 fix: honor MAOP_JSON_LOG=1 (parity with maop.cli). Workers
    # running in containers should emit JSON-structured logs so they can
    # be ingested by ELK / Loki / CloudWatch without a regex parser.
    if os.environ.get("MAOP_JSON_LOG", "0") == "1":
        from maop.core.monitoring import setup_json_logging
        setup_json_logging(
            level=level,
            log_file=os.environ.get("MAOP_JSON_LOG_FILE") or None,
        )
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )


def run() -> None:
    """Main worker loop — consume tasks from queue and execute."""
    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Agent Executor Worker starting (root=%s)", ROOT)

    try:
        from maop.config.loader import ConfigLoader
        from maop.core.message_queue import MessageQueue
        from maop.delegate.dispatcher import Dispatcher
    except ImportError as exc:
        logger.error("Failed to import MAOP modules: %s", exc)
        sys.exit(1)

    queue = MessageQueue(db_path=str(DATA_DIR / "queue.db"))
    loader = ConfigLoader(project_root=ROOT)
    config = loader.load()
    dispatcher = Dispatcher(config, root_dir=str(ROOT))

    logger.info("Worker ready — consuming from queue...")

    while not _shutdown:
        msg = None
        try:
            msg = queue.dequeue(
                topic="agent_tasks",
                consumer_group="agent-exec",
                consumer_id="worker-1",
                timeout_s=5,
            )
            if msg is None:
                continue

            logger.info("Executing task: %s (id=%s)", msg.payload.get("task", "")[:80], msg.id)

            try:
                result = asyncio.run(dispatcher.dispatch(
                    agent=msg.payload.get("agent", "claude"),
                    task=msg.payload.get("task", ""),
                    routing_key=msg.payload.get("routing_key", ""),
                    trace_id=msg.id,
                ))

                logger.info(
                    "Task completed: agent=%s exit_code=%s",
                    getattr(result, "agent", "unknown"),
                    getattr(result, "exit_code", -1),
                )
                # P0 fix: ACK on successful dispatch so the message is not
                # reclaimed by _reclaim_unacked and re-executed (previously
                # caused each task to run up to 4 times).
                queue.ack(msg.id, consumer_id="worker-1")
            except Exception as exc:
                # P0 fix: NACK on dispatch failure so the message is re-queued
                # or dead-lettered instead of lingering in 'processing'.
                logger.exception("Dispatch failed for task id=%s", msg.id)
                try:
                    queue.nack(msg.id, error=str(exc))
                except Exception as nack_exc:
                    logger.warning("Failed to NACK message %s: %s", msg.id, nack_exc)
                time.sleep(1)

        except Exception:
            logger.exception("Worker error")
            time.sleep(1)
    logger.info("Agent Executor Worker shut down.")


if __name__ == "__main__":
    run()
