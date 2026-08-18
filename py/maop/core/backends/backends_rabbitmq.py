"""MAOP RabbitMQ 队列后端 —— 基于 pika 的持久化消息队列实现。

本模块提供 ``RabbitMQQueueBackend``，作为 ``backends.QueueBackend`` ABC 的
分布式实现，用于在多实例 / 企业部署场景下替换默认的 SQLite 队列。

设计要点
--------
- 连接：通过 ``MAOP_RABBITMQ_URL`` 环境变量配置，默认
  ``amqp://guest:guest@localhost:5672/``。
- 持久化：消息 ``delivery_mode=2``，队列 ``durable=True``，broker 重启不丢消息。
- Consumer group：通过队列命名实现，``{topic}.{consumer_group}``；同一 group 的
  多个消费者共享队列（RabbitMQ 原生的负载均衡语义）。
- ACK/NACK：消费时使用 ``basic_get(auto_ack=False)``，将 ``delivery_tag`` 暂存到
  ``_pending`` 映射，ack/nack 时按 ``message_id`` 取出对应的 delivery_tag 完成
  确认。连接断开重连后，未确认的 delivery_tag 失效（RabbitMQ 会将消息重新入队）。
- 死信队列（DLX）：每个主队列通过 ``x-dead-letter-exchange`` 指向统一的 DLX
  exchange ``maop.dlx``，对应的死信队列为 ``{queue_name}.dlq``，便于排查失败消息。
- 延迟投递：``delay > 0`` 时通过 TTL + DLX 模式实现（声明 ``{queue_name}.delayed``
  队列并设置 per-message ``expiration``，到期后死信路由到目标队列）。该模式无需
  插件，但存在队头阻塞（head-of-line blocking）问题；若已安装
  ``rabbitmq_delayed_message_exchange`` 插件可获得更优的延迟语义。

线程安全
--------
RabbitMQ channel 非线程安全，所有 channel 操作通过 ``threading.Lock`` 串行化。
对于高吞吐场景，建议在外层使用连接池或异步后端。

依赖
----
需要 ``pika >= 1.3.0``（``pip install pika``）。未安装时模块导入即失败，
``backends.py`` 的工厂函数会捕获 ``ImportError`` 并降级到 Redis / SQLite。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any

# 顶层导入 pika —— 未安装时抛出带清晰提示的 ImportError，
# 由 backends.py 工厂函数的 try/except ImportError 捕获后降级。
try:
    import pika
    from pika.exceptions import AMQPError
except ImportError as _e:  # pragma: no cover - 仅在缺包时触发
    raise ImportError(
        "pika is required for RabbitMQQueueBackend. "
        "Install the optional dependency with: pip install pika>=1.3.0"
    ) from _e

from maop.core.backends.backends import QueueBackend

logger = logging.getLogger(__name__)

# 默认 RabbitMQ 连接 URL
_DEFAULT_RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"
# 统一的死信交换机名（所有主队列共用，按 routing key 区分目标 DLQ）
_DLX_EXCHANGE = "maop.dlx"


class RabbitMQQueueBackend(QueueBackend):
    """基于 RabbitMQ 的持久化消息队列后端。

    Parameters
    ----------
    url : str
        RabbitMQ 连接 URL，形如 ``amqp://user:pass@host:port/vhost``。
        为空时读取 ``MAOP_RABBITMQ_URL`` 环境变量，再为空使用默认值。

    Raises
    ------
    RuntimeError
        连接 RabbitMQ 失败时抛出（包含原始异常链）。
    """

    def __init__(self, url: str = "") -> None:
        self._url = url or os.getenv("MAOP_RABBITMQ_URL", _DEFAULT_RABBITMQ_URL)
        # 消息 ID -> (channel, delivery_tag) 的待确认映射
        self._pending: dict[str, tuple[Any, int]] = {}
        # channel 非线程安全，所有 channel 操作通过该锁串行化
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None
        # 首次即建立连接，便于在初始化阶段暴露配置 / 连接问题
        self._ensure_connection()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def _ensure_connection(self) -> Any:
        """确保当前连接与 channel 可用，断开时自动重连。

        重连会清空 ``_pending``：原 delivery_tag 在新 channel 上无效，
        RabbitMQ 会把未 ack 的消息重新入队并重新投递。
        """
        with self._lock:
            # 已有连接且未关闭则直接复用
            if (
                self._connection is not None
                and not self._connection.is_closed
                and self._channel is not None
                and not self._channel.is_closed
            ):
                return self._channel
            # 需要新建连接
            try:
                params = pika.URLParameters(self._url)
                self._connection = pika.BlockingConnection(params)
                self._channel = self._connection.channel()  # type: ignore
                # 非自动 ack，由业务调用 ack/nack 显式确认
                self._channel.basic_qos(prefetch_count=1)  # type: ignore
            except AMQPError as e:
                logger.error("[rabbitmq] 连接 RabbitMQ 失败 (url=%s): %s", self._url, e)
                raise RuntimeError(
                    f"无法连接 RabbitMQ (url={self._url}): {e}. "
                    "请检查 MAOP_RABBITMQ_URL 配置及 broker 是否可达。"
                ) from e
            # 重连后旧 delivery_tag 失效，清空待确认映射
            self._pending.clear()
            return self._channel

    def _queue_name(self, topic: str, consumer_group: str = "") -> str:
        """根据 topic 与 consumer_group 推导队列名。

        consumer group 通过队列命名实现：``{topic}.{consumer_group}``；
        未指定 consumer_group 时直接使用 topic 作为队列名。
        """
        return f"{topic}.{consumer_group}" if consumer_group else topic

    def _declare_main_queue(self, channel: Any, topic: str, consumer_group: str = "") -> str:
        """声明主队列、DLX 交换机与对应 DLQ，返回队列名。

        主队列通过 ``x-dead-letter-exchange`` / ``x-dead-letter-routing-key``
        将被 reject（requeue=False）、nack 或过期的消息路由到 DLQ。
        """
        queue_name = self._queue_name(topic, consumer_group)
        dlq_name = f"{queue_name}.dlq"
        # 声明统一的 DLX（direct 交换机，按 routing key 路由到各 DLQ）
        channel.exchange_declare(
            exchange=_DLX_EXCHANGE, exchange_type="direct", durable=True
        )
        # 声明 DLQ 并绑定到 DLX
        channel.queue_declare(queue=dlq_name, durable=True)
        channel.queue_bind(
            queue=dlq_name, exchange=_DLX_EXCHANGE, routing_key=queue_name
        )
        # 声明主队列，配置死信路由
        arguments = {
            "x-dead-letter-exchange": _DLX_EXCHANGE,
            "x-dead-letter-routing-key": queue_name,
        }
        channel.queue_declare(queue=queue_name, durable=True, arguments=arguments)
        return queue_name

    # ------------------------------------------------------------------
    # QueueBackend 抽象方法实现
    # ------------------------------------------------------------------
    def publish(self, topic: str, message: dict[str, Any], *, delay: float = 0) -> str:
        """发布消息到指定 topic 的队列。

        Parameters
        ----------
        topic : str
            主题名，对应一个队列。
        message : dict
            消息体，将被 JSON 序列化后作为 body 发送。
        delay : float
            延迟投递秒数。``> 0`` 时通过 TTL+DLX 模式延迟；该模式无需插件，
            但存在队头阻塞问题，生产环境建议安装
            ``rabbitmq_delayed_message_exchange`` 插件。

        Returns
        -------
        str
            生成的 ``message_id``（UUID），可用于后续 ack/nack。
        """
        channel = self._ensure_connection()
        message_id = str(uuid.uuid4())
        body = json.dumps(
            {
                "message_id": message_id,
                "topic": topic,
                "payload": message,
                "timestamp": time.time(),
            },
            ensure_ascii=False,
            default=str,
        )
        with self._lock:
            queue_name = self._declare_main_queue(channel, topic)
            if delay > 0:
                # 延迟投递：声明延迟队列，到期后死信路由到目标队列
                delay_queue = f"{queue_name}.delayed"
                channel.queue_declare(
                    queue=delay_queue,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": queue_name,
                    },
                )
                properties = pika.BasicProperties(
                    delivery_mode=2,
                    message_id=message_id,
                    content_type="application/json",
                    expiration=str(int(delay * 1000)),
                )
                channel.basic_publish(
                    exchange="",
                    routing_key=delay_queue,
                    body=body,
                    properties=properties,
                )
                logger.debug(
                    "[rabbitmq] 发布延迟消息 topic=%s delay=%.2fs message_id=%s",
                    topic, delay, message_id,
                )
            else:
                properties = pika.BasicProperties(
                    delivery_mode=2,
                    message_id=message_id,
                    content_type="application/json",
                )
                channel.basic_publish(
                    exchange="",
                    routing_key=queue_name,
                    body=body,
                    properties=properties,
                )
                logger.debug(
                    "[rabbitmq] 发布消息 topic=%s message_id=%s", topic, message_id
                )
        return message_id

    def consume(
        self, topic: str, consumer_group: str = "", limit: int = 1
    ) -> list[dict[str, Any]]:
        """从队列拉取最多 ``limit`` 条消息（不自动 ack）。

        拉取的消息会暂存 delivery_tag，需调用 ``ack`` / ``nack`` 显式确认；
        未确认前消息对其他消费者不可见（RabbitMQ unacked 语义）。

        Returns
        -------
        list[dict[str, Any]]
            每个元素形如 ``{"message_id", "topic", "payload"}``。
        """
        channel = self._ensure_connection()
        queue_name = self._declare_main_queue(channel, topic, consumer_group)
        results: list[dict[str, Any]] = []
        with self._lock:
            for _ in range(max(limit, 0)):
                method, properties, body = channel.basic_get(
                    queue=queue_name, auto_ack=False
                )
                # basic_get 无消息时返回 (None, None, None)
                if method is None:
                    break
                message_id = (properties.message_id if properties and properties.message_id
                              else str(uuid.uuid4()))
                # 暂存 delivery_tag 供 ack/nack 使用
                self._pending[message_id] = (channel, method.delivery_tag)
                try:
                    data = json.loads(body)
                    # 保证返回结构包含 message_id
                    if isinstance(data, dict) and "message_id" not in data:
                        data["message_id"] = message_id
                    results.append(data)
                except (json.JSONDecodeError, TypeError):
                    # body 非 JSON，原样包装返回
                    results.append(
                        {"message_id": message_id, "topic": topic, "payload": body}
                    )
        logger.debug(
            "[rabbitmq] 消费 topic=%s consumer_group=%s 取得 %d 条",
            topic, consumer_group, len(results),
        )
        return results

    def ack(self, topic: str, message_id: str) -> bool:
        """确认消息已处理完成，从队列移除。

        Returns
        -------
        bool
            成功确认返回 True；message_id 不在待确认映射中返回 False。
        """
        with self._lock:
            entry = self._pending.pop(message_id, None)
            if entry is None:
                logger.warning(
                    "[rabbitmq] ack 失败：message_id=%s 不在待确认映射中", message_id
                )
                return False
            channel, delivery_tag = entry
            try:
                channel.basic_ack(delivery_tag=delivery_tag)
                logger.debug("[rabbitmq] ack message_id=%s", message_id)
                return True
            except AMQPError as e:
                logger.error(
                    "[rabbitmq] ack 异常 message_id=%s: %s", message_id, e
                )
                return False

    def nack(self, topic: str, message_id: str) -> bool:
        """否定确认消息，消息会被重新入队（requeue=True）以重试。

        如需将消息转入死信队列，请直接在业务层用 reject(requeue=False)，
        或重试次数超限后由 DLX 自动路由。

        Returns
        -------
        bool
            成功返回 True；message_id 不在待确认映射中返回 False。
        """
        with self._lock:
            entry = self._pending.pop(message_id, None)
            if entry is None:
                logger.warning(
                    "[rabbitmq] nack 失败：message_id=%s 不在待确认映射中", message_id
                )
                return False
            channel, delivery_tag = entry
            try:
                channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
                logger.debug("[rabbitmq] nack message_id=%s (requeue=True)", message_id)
                return True
            except AMQPError as e:
                logger.error(
                    "[rabbitmq] nack 异常 message_id=%s: %s", message_id, e
                )
                return False

    def topic_stats(self, topic: str) -> dict[str, Any]:
        """查询 topic 对应主队列（无 consumer_group）的统计信息。

        Returns
        -------
        dict[str, Any]
            包含 ``queue_name`` / ``message_count`` / ``consumer_count``。
        """
        channel = self._ensure_connection()
        queue_name = self._queue_name(topic)
        with self._lock:
            try:
                # passive 声明：仅检查队列是否存在并取统计，不创建
                method = channel.queue_declare(queue=queue_name, passive=True)
                return {
                    "queue_name": queue_name,
                    "message_count": method.method.message_count,
                    "consumer_count": method.method.consumer_count,
                }
            except AMQPError as e:
                logger.error(
                    "[rabbitmq] 查询队列统计失败 topic=%s: %s", topic, e
                )
                return {"queue_name": queue_name, "message_count": 0, "consumer_count": 0}

    # ------------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------------
    def close(self) -> None:
        """显式关闭连接与 channel。"""
        with self._lock:
            try:
                if self._channel is not None and not self._channel.is_closed:
                    self._channel.close()
            except AMQPError as exc:
                logger.debug("rabbitmq: close channel failed: %s", exc)
            try:
                if self._connection is not None and not self._connection.is_closed:
                    self._connection.close()
            except AMQPError as exc:
                logger.debug("rabbitmq: close connection failed: %s", exc)
            self._pending.clear()
