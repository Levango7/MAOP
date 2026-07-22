"""MAOP Queue Worker — Consumes background tasks from the message queue.

Processes:
  - Human approval requests (auto-expire, notifications)
  - Scheduled maintenance tasks (backup, cleanup)
  - Async data bridge operations

Environment variables:
  MAOP_ROOT        — Project root directory (default: /app)
  MAOP_DATA_DIR    — Data directory (default: /app/data)
  MAOP_LOG_LEVEL   — Logging level (default: INFO)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

logger = logging.getLogger("maop.worker.queue_worker")

ROOT = Path(os.environ.get("MAOP_ROOT", "/app"))
DATA_DIR = Path(os.environ.get("MAOP_DATA_DIR", str(ROOT / "data")))

_shutdown = False


def _handle_signal(signum: int, frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown = True


def _setup_logging() -> None:
    level = os.environ.get("MAOP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )


def _process_human_approvals() -> int:
    """Auto-expire stale human approval requests. Returns count expired."""
    try:
        from maop.core.human_proxy import HumanProxy
        proxy = HumanProxy(root_dir=ROOT)
        return proxy.expire_old()
    except Exception as exc:
        logger.warning("Failed to process human approvals: %s", exc)
        return 0


def _process_queue_stats() -> None:
    """Log queue statistics."""
    try:
        from maop.core.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(DATA_DIR / "queue.db"))
        stats = mq.stats()
        logger.debug("Queue stats: %s", stats)
    except Exception as exc:
        logger.warning("Failed to get queue stats: %s", exc)


def run() -> None:
    """Main worker loop — process background queue tasks."""
    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Queue Worker starting (root=%s)", ROOT)

    cycle = 0
    while not _shutdown:
        try:
            cycle += 1

            # Every cycle: process human approvals
            expired = _process_human_approvals()
            if expired:
                logger.info("Expired %d human approval requests", expired)

            # Every 10 cycles: log queue stats
            if cycle % 10 == 0:
                _process_queue_stats()

            time.sleep(5)

        except Exception as exc:
            logger.error("Worker error: %s", exc, exc_info=True)
            time.sleep(5)

    logger.info("Queue Worker shut down.")


if __name__ == "__main__":
    run()