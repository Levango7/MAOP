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

from maop.core.backends.db_utils import get_db_path

logger = logging.getLogger("maop.worker.queue_worker")

ROOT = Path(os.environ.get("MAOP_ROOT", "/app"))
DATA_DIR = Path(os.environ.get("MAOP_DATA_DIR", str(ROOT / "data")))

_shutdown = False


class _UnknownTopicError(Exception):
    """Raised when a dequeued message has a topic with no registered handler.

    OPS-12 fix: surfacing this (instead of silently acking) lets the caller
    NACK the message so it follows the normal retry → dead-letter path rather
    than being permanently and silently dropped.
    """


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
        from maop.core.monitoring.monitoring import setup_json_logging
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


def _process_human_approvals() -> int:
    """Auto-expire stale human approval requests. Returns count expired."""
    try:
        from maop.core.agent.delegation.human_proxy import HumanProxy
        proxy = HumanProxy(root_dir=ROOT)
        return proxy.expire_old()
    except Exception as exc:
        logger.warning("Failed to process human approvals: %s", exc)
        return 0


def _process_queue_stats() -> None:
    """Log queue statistics."""
    try:
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(get_db_path("queue")))
        stats = mq.stats()
        logger.debug("Queue stats: %s", stats)
    except Exception as exc:
        logger.warning("Failed to get queue stats: %s", exc)


# Topics that queue_worker is responsible for consuming. Note:
# - "agent_tasks" is intentionally excluded — it is consumed by
#   agent_executor.worker (Dispatcher path) to avoid competing consumers.
# - "human_approval" is excluded — _process_human_approvals already
#   drains approval state via HumanProxy.expire_old().
_CONSUME_TOPICS: tuple[str, ...] = ("task", "maintenance", "async_bridge")
_CONSUMER_GROUP = "queue-worker"
_CONSUMER_ID = "queue-worker-1"
_MAX_MSG_PER_CYCLE = 50


def _dispatch_message(msg) -> None:
    """Dispatch a queued message to the appropriate handler by topic.

    Topics:
    - "human_approval": human approval notifications (already handled by
      _process_human_approvals; skipped here to avoid duplicate work).
    - "task": agent task execution -> delegate to WorkerPool via _execute_task.
    - "maintenance": periodic maintenance jobs -> _run_maintenance.
    - "async_bridge": async data bridge operations (treated like maintenance
      jobs; payload's ``job`` field names the operation).
    - unknown topics: logged and acked (no handler).
    """
    topic = getattr(msg, "topic", "") or ""
    payload = getattr(msg, "payload", {}) or {}

    if topic == "human_approval":
        # Already handled by _process_human_approvals; skip duplicate processing.
        return
    if topic == "task":
        _execute_task(payload)
    elif topic in ("maintenance", "async_bridge"):
        _run_maintenance(payload)
    else:
        # OPS-12 fix: an unrecognized topic must NOT be silently acked and
        # dropped (e.g. a producer typo that still lands on a consumed topic).
        # Raise so the caller NACKs the message, giving the MQ its normal
        # retry -> dead-letter path instead of permanent silent loss.
        logger.warning(
            "[queue-worker] no handler registered for topic %r; NACKing for "
            "retry/dead-letter (possible producer topic typo or missing handler)",
            topic,
        )
        raise _UnknownTopicError(f"no handler for topic {topic!r}")


def _execute_task(payload: dict) -> None:
    """Execute an agent task from the queue via WorkerPool.

    Payload fields:
    - task (str, required): task description
    - agent_name (str, required): pinned agent name
    - workdir (str, optional): working directory
    """
    task = payload.get("task", "")
    agent_name = payload.get("agent_name", "")
    workdir = payload.get("workdir", "")
    if not task or not agent_name:
        logger.warning(
            "[queue-worker] task message missing task/agent_name: %s", payload,
        )
        return

    import asyncio

    from maop.core.reliability.worker_pool import WorkerPool

    # OPS-11 fix: run the whole lifecycle in ONE event loop instead of four
    # separate asyncio.run() calls. Each asyncio.run() creates and destroys
    # its own loop, so the pool's internal asyncio primitives (queues, tasks,
    # locks) were created in one loop but used from another — undefined
    # behaviour plus 4x loop setup/teardown overhead per message.
    async def _lifecycle() -> None:
        pool = WorkerPool(max_workers=1, root_dir=os.environ.get("MAOP_ROOT", ""))
        await pool.start()
        try:
            task_id = await pool.submit(task, workdir=workdir, agent_name=agent_name)
            await pool.wait(task_id, timeout=300)
        finally:
            await pool.stop()

    try:
        asyncio.run(_lifecycle())
    except Exception:
        logger.exception("[queue-worker] task execution failed")
        raise


def _run_maintenance(payload: dict) -> None:
    """Run a maintenance / async-bridge job from the queue.

    Payload fields:
    - job (str): job name (e.g. "memory_prune", "cache_cleanup", "backup",
      "purge_acked", "cleanup_dead_letters")
    - extra fields are passed through to the job if implemented.

    This is a thin dispatcher: actual maintenance functions live elsewhere
    (e.g. memory prune in core.memory, backup in core.backup). Unknown job
    names are logged but not treated as errors so the message still ACKs.
    """
    job = payload.get("job", "")
    logger.info("[queue-worker] running maintenance job: %s", job or "<unnamed>")

    if not job:
        return

    if job == "purge_acked":
        try:
            from maop.core.reliability.message_queue import MessageQueue
            mq = MessageQueue(db_path=str(get_db_path("queue")))
            removed = mq.purge_acked(older_than_s=payload.get("older_than_s", 3600.0))
            logger.info("[queue-worker] purge_acked removed %d messages", removed)
        except Exception:
            logger.exception("[queue-worker] purge_acked failed")
            raise
    elif job == "cleanup_dead_letters":
        try:
            from maop.core.reliability.message_queue import MessageQueue
            mq = MessageQueue(db_path=str(get_db_path("queue")))
            removed = mq.cleanup_dead_letters(
                older_than_s=payload.get("older_than_s", 86400.0)
            )
            logger.info(
                "[queue-worker] cleanup_dead_letters removed %d entries", removed,
            )
        except Exception:
            logger.exception("[queue-worker] cleanup_dead_letters failed")
            raise
    else:
        logger.info(
            "[queue-worker] no implementation for job %r, treating as no-op", job,
        )


def _consume_messages() -> int:
    """Drain pending messages from the queue for the configured topics.

    Returns the number of messages successfully processed and ACKed in this
    cycle. A single message failure does not crash the worker — the message
    is NACKed so the MQ can reclaim / retry it per its retry policy.
    """
    processed = 0
    try:
        from maop.core.reliability.message_queue import MessageQueue
        mq = MessageQueue(db_path=str(get_db_path("queue")))
    except Exception as exc:
        logger.warning("[queue-worker] cannot connect to message queue: %s", exc)
        return 0

    for topic in _CONSUME_TOPICS:
        for _ in range(_MAX_MSG_PER_CYCLE):
            if _shutdown:
                break
            try:
                msg = mq.dequeue(
                    topic=topic,
                    consumer_group=_CONSUMER_GROUP,
                    consumer_id=_CONSUMER_ID,
                    timeout_s=0,  # non-blocking
                )
            except Exception as exc:
                logger.warning(
                    "[queue-worker] dequeue error on topic %r: %s", topic, exc,
                )
                break

            if msg is None:
                break  # topic drained

            try:
                _dispatch_message(msg)
                mq.ack(msg.id, consumer_id=_CONSUMER_ID)
                processed += 1
            except Exception as exc:
                # NACK so the message is re-queued or dead-lettered by the MQ
                # rather than lingering in 'processing' until ack_timeout_s.
                logger.exception(
                    "[queue-worker] failed to process message %s on %s",
                    getattr(msg, "id", "?"), topic,
                )
                try:
                    mq.nack(msg.id, error=str(exc))
                except Exception as nack_exc:
                    logger.warning(
                        "[queue-worker] nack failed for %s: %s",
                        getattr(msg, "id", "?"), nack_exc,
                    )
    return processed


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

            # Every cycle: drain pending queue messages (task / maintenance /
            # async_bridge). agent_tasks and human_approval are handled by
            # dedicated workers / functions — see _CONSUME_TOPICS.
            processed = _consume_messages()
            if processed:
                logger.info("Processed %d queue messages", processed)

            # Every 10 cycles: log queue stats
            if cycle % 10 == 0:
                _process_queue_stats()

            time.sleep(5)

        except Exception:
            logger.exception("Worker error")
            time.sleep(5)

    logger.info("Queue Worker shut down.")


if __name__ == "__main__":
    run()
