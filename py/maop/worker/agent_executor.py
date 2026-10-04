"""MAOP Agent Executor Worker — Consumes agent execution tasks from the queue.

Cloud-native entry point for distributed agent execution.
Reads tasks from the message queue, dispatches via Dispatcher,
and records results.

⚠️ **本仓没有 `agent_tasks` 的生产者**（2026-10-05 全仓核对）：`docker-compose.yml`
部署了 `agent-exec` 服务，但没有一处非测试代码往这个 topic 投消息 —— 所以这个容器
起得来、跑得转，但永远拿不到任务。要么由使用方（MAOS / 外部系统）投递，要么补一个
提交入口；在那之前把"云原生分布式执行"当成已具备能力是言过其实。

两处语义修正见 `_process_message` 的 docstring（失败也 ACK、日志读错对象）——
正是因为这条链没人走，它们才一直没被发现。

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
from pathlib import Path
from typing import Any

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


async def _process_message(
    queue: Any, dispatcher: Any, msg: Any, consumer_id: str,
) -> None:
    """执行一条消息并按结果 ACK / NACK。

    抽成独立函数是为了能被用例直接驱动 —— 原先它内联在 `_consume` 的闭包里，
    只能靠"起真 worker"才能测，实际上等于没测。

    2026-10-05 两处修正（都在原先的"死路径"里，但 MAOS 侧可能真的有生产者，
    所以代码本身得是对的）：

    1. **失败也 ACK**：原实现只在**抛异常**时 NACK，而 `Dispatcher.dispatch` 失败时
       是**正常返回**的 —— `DispatchResult.result.exit_code != 0`。于是失败任务被 ACK 掉、
       永不重试，与"nack 重试到上限再进死信"的设计意图相反。现按 `exit_code` 判定。
    2. **日志读错了对象**：`getattr(result, "agent"/"exit_code", ...)` 取的是
       `DispatchResult` 的属性，而这两个字段在 `result.result`（MaopResult）上，
       所以日志恒为 `agent=unknown exit_code=-1`，排障时等于没有信息。

    注意：权限门拒绝（exit_code=126）也会走 NACK ⇒ 重试到上限后进死信。
    "永久性失败不该重试"是队列层面的另一个设计议题，本函数不擅自区分。
    """
    try:
        dispatch_result = await dispatcher.dispatch(
            agent=msg.payload.get("agent", "claude"),
            task=msg.payload.get("task", ""),
            routing_key=msg.payload.get("routing_key", ""),
            trace_id=msg.id,
        )
    except Exception as exc:
        logger.exception("Dispatch failed for task id=%s", msg.id)
        try:
            await asyncio.to_thread(queue.nack, msg.id, error=str(exc))
        except Exception as nack_exc:
            logger.warning("Failed to NACK message %s: %s", msg.id, nack_exc)
        await asyncio.sleep(1)
        return

    inner = getattr(dispatch_result, "result", None)
    exit_code = getattr(inner, "exit_code", -1)
    logger.info(
        "Task completed: agent=%s exit_code=%s",
        getattr(inner, "agent", "unknown"), exit_code,
    )

    if exit_code == 0:
        # ACK 成功派发：否则会被 _reclaim_unacked 收回并重复执行
        #（历史问题：同一任务最多跑 4 次）。
        await asyncio.to_thread(queue.ack, msg.id, consumer_id=consumer_id)
        return

    error = getattr(inner, "error", "") or f"exit_code={exit_code}"
    logger.warning("Dispatch returned non-zero (%s) for task id=%s — NACK", exit_code, msg.id)
    try:
        await asyncio.to_thread(queue.nack, msg.id, error=error)
    except Exception as nack_exc:
        logger.warning("Failed to NACK message %s: %s", msg.id, nack_exc)


def run() -> None:
    """Main worker loop — consume tasks from queue and execute."""
    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Agent Executor Worker starting (root=%s)", ROOT)

    try:
        from maop.config.loader import ConfigLoader
        from maop.core.reliability.message_queue import MessageQueue
        from maop.delegate.dispatcher import Dispatcher
    except ImportError as exc:
        logger.error("Failed to import MAOP modules: %s", exc)
        sys.exit(1)

    queue = MessageQueue(db_path=str(DATA_DIR / "queue.db"))
    loader = ConfigLoader(project_root=ROOT)
    config = loader.load()
    dispatcher = Dispatcher(config, root_dir=str(ROOT))

    # C11 fix: consumer_id was hard-coded "worker-1" — running multiple
    # workers made them indistinguishable (ack/reclaim confusion, double
    # execution). Derive a unique id from hostname + pid.
    import socket
    consumer_id = f"worker-{socket.gethostname()}-{os.getpid()}"
    logger.info("Worker ready — consuming from queue... (consumer_id=%s)", consumer_id)

    # P2 fix: run ONE event loop for the whole worker lifetime instead of a
    # fresh asyncio.run() per task. Per-task loop churn destroys and rebuilds
    # the loop every message (slow) and reuses a Dispatcher whose internal
    # asyncio primitives were created under a previous, now-closed loop.
    async def _consume() -> None:
        while not _shutdown:
            msg = None
            try:
                msg = await asyncio.to_thread(
                    queue.dequeue,
                    topic="agent_tasks",
                    consumer_group="agent-exec",
                    consumer_id=consumer_id,
                    timeout_s=5,
                )
                if msg is None:
                    continue

                logger.info("Executing task: %s (id=%s)", msg.payload.get("task", "")[:80], msg.id)
                await _process_message(queue, dispatcher, msg, consumer_id)

            except Exception:
                logger.exception("Worker error")
                await asyncio.sleep(1)

    asyncio.run(_consume())
    logger.info("Agent Executor Worker shut down.")


if __name__ == "__main__":
    run()
