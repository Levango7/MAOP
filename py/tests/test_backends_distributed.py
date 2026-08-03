"""Tests for maop.core.backends_rabbitmq / backends_distributed —
RabbitMQ 队列后端与 etcd KV 后端。

由于 pika / etcd3 未安装（可选依赖），本测试套件主要覆盖：

1. Import 错误降级行为
   - ``MAOP_QUEUE_BACKEND=rabbitmq`` 时 ``get_queue_backend()`` 优雅降级到 SQLite
   - ``MAOP_KV_BACKEND=etcd`` 时 ``get_kv_backend()`` 优雅降级到 SQLite

2. 模块代码结构验证（不实际导入 pika/etcd3）
   - 用 ``ast.parse`` 验证 ``backends_rabbitmq.py`` 与 ``backends_distributed.py``
     的语法正确性，且 class 定义存在

3. 用 ``unittest.mock`` 模拟 pika / etcd3 模块，测试 backend 类的
   实例化与基本方法调用（enqueue / set 等核心交互）。

参考风格：tests/test_backends_redis.py（用 MagicMock 模拟第三方库）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maop.core.backends import (
    SQLiteKVBackend,
    SQLiteQueueBackend,
    get_kv_backend,
    get_queue_backend,
    reset_backends,
)

# 模块源码路径
_BACKENDS_DIR = Path(__file__).resolve().parent.parent / "maop" / "core"
_RABBITMQ_MODULE_PATH = _BACKENDS_DIR / "backends_rabbitmq.py"
_ETCD_MODULE_PATH = _BACKENDS_DIR / "backends_distributed.py"


@pytest.fixture(autouse=True)
def _reset_backends_each_test():
    """每个测试前后清空 backend 缓存，避免相互污染。"""
    reset_backends()
    yield
    reset_backends()


# ═══════════════════════════════════════════════════════════════════════
# 1. Import 错误降级行为
# ═══════════════════════════════════════════════════════════════════════


class TestRabbitMQBackendImportError:
    """RabbitMQ 后端在 pika 未安装时的降级行为。"""

    def test_queue_backend_falls_back_to_sqlite_when_pika_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``MAOP_QUEUE_BACKEND=rabbitmq`` 但 pika 未安装时，降级到 SQLite。

        降级链路：rabbitmq → (ImportError) → redis → (ImportError) → sqlite。
        需 ``MAOP_QUEUE_ALLOW_FALLBACK=1`` 显式启用降级（D1/D2 fail-fast）。
        Mock RedisQueueBackend 也抛 ImportError 以模拟 redis 不可用。
        """
        monkeypatch.setenv("MAOP_QUEUE_BACKEND", "rabbitmq")
        monkeypatch.setenv("MAOP_QUEUE_ALLOW_FALLBACK", "1")
        with patch("maop.core.backends_redis.RedisQueueBackend",
                   side_effect=ImportError("mocked: redis unavailable")):
            backend = get_queue_backend()
        assert isinstance(backend, SQLiteQueueBackend)

    def test_rabbitmq_degradation_recorded(self, monkeypatch: pytest.MonkeyPatch):
        """降级事件应被记录到 degradation_log。"""
        from maop.config.edition import degradation_log

        monkeypatch.setenv("MAOP_QUEUE_BACKEND", "rabbitmq")
        monkeypatch.setenv("MAOP_QUEUE_ALLOW_FALLBACK", "1")
        with patch("maop.core.backends_redis.RedisQueueBackend",
                   side_effect=ImportError("mocked: redis unavailable")):
            get_queue_backend()
        log = degradation_log()
        queue_entries = [e for e in log if e.get("backend") == "queue"]
        assert len(queue_entries) >= 1
        assert any(
            e.get("requested") == "rabbitmq" and e.get("fallback") == "redis"
            for e in queue_entries
        )


class TestEtcdBackendImportError:
    """etcd 后端在 etcd3 未安装时的降级行为。"""

    def test_kv_backend_falls_back_to_sqlite_when_etcd3_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``MAOP_KV_BACKEND=etcd`` 但 etcd3 未安装时，降级到 SQLite。"""
        monkeypatch.setenv("MAOP_KV_BACKEND", "etcd")
        monkeypatch.setenv("MAOP_KV_ALLOW_FALLBACK", "1")
        backend = get_kv_backend()
        assert isinstance(backend, SQLiteKVBackend)

    def test_consul_kv_backend_also_falls_back_to_sqlite(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``MAOP_KV_BACKEND=consul`` 同样走 etcd 实现分支并降级到 SQLite。"""
        monkeypatch.setenv("MAOP_KV_BACKEND", "consul")
        monkeypatch.setenv("MAOP_KV_ALLOW_FALLBACK", "1")
        backend = get_kv_backend()
        assert isinstance(backend, SQLiteKVBackend)

    def test_etcd_degradation_recorded(self, monkeypatch: pytest.MonkeyPatch):
        """降级事件应被记录到 degradation_log。"""
        from maop.config.edition import degradation_log

        monkeypatch.setenv("MAOP_KV_BACKEND", "etcd")
        monkeypatch.setenv("MAOP_KV_ALLOW_FALLBACK", "1")
        get_kv_backend()
        log = degradation_log()
        kv_entries = [e for e in log if e.get("backend") == "kv"]
        assert any(
            e.get("requested") == "etcd" and e.get("fallback") == "sqlite"
            for e in kv_entries
        )


# ═══════════════════════════════════════════════════════════════════════
# 2. 模块代码结构验证（不导入 pika/etcd3）
# ═══════════════════════════════════════════════════════════════════════


def _parse_module_classes(path: Path) -> set[str]:
    """解析 Python 源码文件，返回所有顶层 class 名集合。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


class TestRabbitMQModuleStructure:
    """backends_rabbitmq.py 的源码结构验证。"""

    def test_module_file_exists(self):
        """模块文件存在。"""
        assert _RABBITMQ_MODULE_PATH.exists(), (
            f"backends_rabbitmq.py not found at {_RABBITMQ_MODULE_PATH}"
        )

    def test_rabbitmq_module_has_correct_class(self):
        """backends_rabbitmq.py 定义了 RabbitMQQueueBackend 类。"""
        classes = _parse_module_classes(_RABBITMQ_MODULE_PATH)
        assert "RabbitMQQueueBackend" in classes, (
            f"RabbitMQQueueBackend class not found in backends_rabbitmq.py; "
            f"got: {classes}"
        )

    def test_rabbitmq_module_has_required_methods(self):
        """RabbitMQQueueBackend 应实现 QueueBackend ABC 的全部抽象方法。"""
        source = _RABBITMQ_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_RABBITMQ_MODULE_PATH))
        # 找到 RabbitMQQueueBackend 类
        rabbitmq_cls = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RabbitMQQueueBackend"
        )
        methods = {
            node.name
            for node in rabbitmq_cls.body
            if isinstance(node, ast.FunctionDef)
        }
        # QueueBackend ABC 要求的抽象方法
        required = {"publish", "consume", "ack", "nack", "topic_stats"}
        missing = required - methods
        assert not missing, f"RabbitMQQueueBackend 缺少方法: {missing}"

    def test_rabbitmq_module_imports_pika(self):
        """模块顶部应导入 pika（导致 ImportError 时被工厂函数捕获）。"""
        source = _RABBITMQ_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_RABBITMQ_MODULE_PATH))
        import_names: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.append(alias.name)
            elif isinstance(node, ast.Try):
                # try/except ImportError 块中的 import 也算
                for sub in node.body:
                    if isinstance(sub, ast.Import):
                        for alias in sub.names:
                            import_names.append(alias.name)
        assert "pika" in import_names, (
            "backends_rabbitmq.py 应在顶层导入 pika，"
            "以便 ImportError 被工厂函数捕获并降级"
        )


class TestEtcdModuleStructure:
    """backends_distributed.py 的源码结构验证。"""

    def test_module_file_exists(self):
        """模块文件存在。"""
        assert _ETCD_MODULE_PATH.exists(), (
            f"backends_distributed.py not found at {_ETCD_MODULE_PATH}"
        )

    def test_etcd_module_has_correct_class(self):
        """backends_distributed.py 定义了 EtcdKVBackend 类。"""
        classes = _parse_module_classes(_ETCD_MODULE_PATH)
        assert "EtcdKVBackend" in classes, (
            f"EtcdKVBackend class not found in backends_distributed.py; "
            f"got: {classes}"
        )

    def test_etcd_module_has_required_methods(self):
        """EtcdKVBackend 应实现 KVBackend ABC 的全部抽象方法。"""
        source = _ETCD_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_ETCD_MODULE_PATH))
        etcd_cls = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "EtcdKVBackend"
        )
        methods = {
            node.name
            for node in etcd_cls.body
            if isinstance(node, ast.FunctionDef)
        }
        # KVBackend ABC 要求的抽象方法
        required = {"get", "set", "delete", "list_keys", "cas"}
        missing = required - methods
        assert not missing, f"EtcdKVBackend 缺少方法: {missing}"

    def test_etcd_module_imports_etcd3(self):
        """模块顶部应导入 etcd3（导致 ImportError 时被工厂函数捕获）。"""
        source = _ETCD_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_ETCD_MODULE_PATH))
        import_names: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.append(alias.name)
            elif isinstance(node, ast.Try):
                for sub in node.body:
                    if isinstance(sub, ast.Import):
                        for alias in sub.names:
                            import_names.append(alias.name)
        assert "etcd3" in import_names, (
            "backends_distributed.py 应在顶层导入 etcd3，"
            "以便 ImportError 被工厂函数捕获并降级"
        )


# ═══════════════════════════════════════════════════════════════════════
# 3. Mock pika 测试 RabbitMQQueueBackend 方法调用
# ═══════════════════════════════════════════════════════════════════════


class _FakeAMQPError(Exception):
    """模拟 pika.exceptions.AMQPError。"""


def _build_fake_pika_module() -> Any:
    """构造一个 mock pika 模块，提供 RabbitMQQueueBackend 所需的属性。

    - pika.URLParameters(url) → 任意对象
    - pika.BlockingConnection(params) → MagicMock channel/connection
    - pika.BasicProperties(...) → MagicMock
    - pika.exceptions.AMQPError → 异常类（用于 except 子句匹配）
    """
    fake = MagicMock()
    fake.exceptions.AMQPError = _FakeAMQPError

    # 构造一个 fake channel，所有方法均为 MagicMock
    fake_channel = MagicMock()
    fake_channel.is_closed = False
    fake_connection = MagicMock()
    fake_connection.is_closed = False
    fake_connection.channel.return_value = fake_channel

    fake.URLParameters.return_value = MagicMock()
    fake.BlockingConnection.return_value = fake_connection
    fake.BasicProperties.return_value = MagicMock()

    # 暴露 channel 供测试断言
    fake._channel = fake_channel
    fake._connection = fake_connection
    return fake


@pytest.fixture
def fake_pika_module():
    """注入 mock pika 模块到 sys.modules，测试结束后恢复。"""
    fake = _build_fake_pika_module()
    # 同时注入 pika.exceptions 子模块，因为 backends_rabbitmq.py 顶部
    # 会执行 ``from pika.exceptions import AMQPError``
    fake_exceptions = MagicMock()
    fake_exceptions.AMQPError = _FakeAMQPError
    with patch.dict(
        sys.modules,
        {
            "pika": fake,
            "pika.exceptions": fake_exceptions,
        },
    ):
        yield fake


class TestRabbitMQBackendWithMock:
    """用 mock pika 测试 RabbitMQQueueBackend 的方法调用。"""

    def test_enqueue_publishes_message(self, fake_pika_module):
        """publish() 应通过 channel.basic_publish 投递消息。"""
        # 注意：必须先清理可能已缓存的模块，确保下次 import 时使用 mock pika
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            backend = RabbitMQQueueBackend(url="amqp://guest:guest@localhost:5672/")
            message_id = backend.publish("test_topic", {"hello": "world"})

            assert isinstance(message_id, str)
            assert len(message_id) > 0
            # 验证 channel.basic_publish 被调用
            channel = fake_pika_module._channel
            assert channel.basic_publish.called, (
                "channel.basic_publish 应在 publish() 中被调用"
            )
            # 验证 routing_key 是 topic 名（无 consumer_group 时队列名=topic）
            call_kwargs = channel.basic_publish.call_args.kwargs
            assert call_kwargs.get("routing_key") == "test_topic"
            # 验证 body 是 JSON 字符串
            body = call_kwargs.get("body")
            assert isinstance(body, str)
            assert "hello" in body and "world" in body
        finally:
            # 清理缓存，避免影响后续测试
            sys.modules.pop("maop.core.backends_rabbitmq", None)

    def test_publish_with_delay_uses_delay_queue(self, fake_pika_module):
        """publish(delay>0) 应使用延迟队列（TTL+DLX 模式）。"""
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            backend = RabbitMQQueueBackend(url="amqp://guest:guest@localhost:5672/")
            backend.publish("delayed_topic", {"x": 1}, delay=5.0)

            channel = fake_pika_module._channel
            # 验证 queue_declare 被调用且包含延迟队列名
            queue_declare_calls = channel.queue_declare.call_args_list
            queue_names = [c.kwargs.get("queue") for c in queue_declare_calls]
            assert "delayed_topic.delayed" in queue_names, (
                f"延迟投递应声明 delayed_topic.delayed 队列，实际: {queue_names}"
            )
        finally:
            sys.modules.pop("maop.core.backends_rabbitmq", None)

    def test_consume_returns_messages(self, fake_pika_module):
        """consume() 应返回从 basic_get 拉取到的消息。"""
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            backend = RabbitMQQueueBackend(url="amqp://guest:guest@localhost:5672/")

            # 模拟 basic_get 返回一条消息
            method = MagicMock()
            method.delivery_tag = 1
            properties = MagicMock()
            properties.message_id = "msg-001"
            body = '{"message_id": "msg-001", "topic": "t", "payload": {"k": "v"}}'
            channel = fake_pika_module._channel
            channel.basic_get.return_value = (method, properties, body.encode())

            results = backend.consume("t", limit=1)

            assert len(results) == 1
            assert results[0]["payload"] == {"k": "v"}
            # 验证 basic_get 用了 auto_ack=False
            assert channel.basic_get.call_args.kwargs.get("auto_ack") is False
        finally:
            sys.modules.pop("maop.core.backends_rabbitmq", None)

    def test_ack_removes_pending_entry(self, fake_pika_module):
        """ack() 应调用 channel.basic_ack 并清除 pending 映射。"""
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            backend = RabbitMQQueueBackend(url="amqp://guest:guest@localhost:5672/")

            # 先 consume 一条消息填充 _pending
            method = MagicMock()
            method.delivery_tag = 42
            properties = MagicMock()
            properties.message_id = "msg-ack-1"
            body = '{"message_id": "msg-ack-1", "topic": "t", "payload": {}}'
            channel = fake_pika_module._channel
            channel.basic_get.return_value = (method, properties, body.encode())

            backend.consume("t", limit=1)
            assert "msg-ack-1" in backend._pending

            # ack 应清除 pending 并调用 basic_ack
            ok = backend.ack("t", "msg-ack-1")
            assert ok is True
            assert "msg-ack-1" not in backend._pending
            channel.basic_ack.assert_called_once_with(delivery_tag=42)
        finally:
            sys.modules.pop("maop.core.backends_rabbitmq", None)

    def test_ack_unknown_message_id_returns_false(self, fake_pika_module):
        """ack() 在 message_id 不在 pending 中时应返回 False。"""
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            backend = RabbitMQQueueBackend(url="amqp://guest:guest@localhost:5672/")
            ok = backend.ack("t", "nonexistent-id")
            assert ok is False
        finally:
            sys.modules.pop("maop.core.backends_rabbitmq", None)

    def test_connection_failure_raises_runtime_error(self, fake_pika_module):
        """pika.BlockingConnection 抛出 AMQPError 时应转为 RuntimeError。"""
        sys.modules.pop("maop.core.backends_rabbitmq", None)
        try:
            from maop.core.backends_rabbitmq import RabbitMQQueueBackend

            # 让 BlockingConnection 抛出 AMQPError
            fake_pika_module.BlockingConnection.side_effect = _FakeAMQPError("conn refused")
            with pytest.raises(RuntimeError, match="无法连接 RabbitMQ"):
                RabbitMQQueueBackend(url="amqp://bad:guest@localhost:5672/")
        finally:
            # 恢复 side_effect
            fake_pika_module.BlockingConnection.side_effect = None
            sys.modules.pop("maop.core.backends_rabbitmq", None)


# ═══════════════════════════════════════════════════════════════════════
# 4. Mock etcd3 测试 EtcdKVBackend 方法调用
# ═══════════════════════════════════════════════════════════════════════


def _build_fake_etcd3_module() -> Any:
    """构造一个 mock etcd3 模块。

    - etcd3.client(host, port) → MagicMock client
    - client.status() → None（探活成功）
    - client.put(key, value, lease=None) → None
    - client.get(key) → (value_bytes, meta)
    - client.delete(key) → None
    - client.get_prefix(prefix) → iterator of (value, meta)
    - client.lease(ttl) → lease 对象（带 id 属性）
    - client.transaction(compare, success, failure) → (True, [])
    - client.transactions → 用于构造事务条件的对象
    """
    fake = MagicMock()
    fake_client = MagicMock()
    fake_client.status.return_value = MagicMock()

    # 默认 get 返回 None（key 不存在）
    fake_client.get.return_value = (None, MagicMock())
    # 默认 get_prefix 返回空列表
    fake_client.get_prefix.return_value = iter([])
    # transaction 默认成功
    fake_client.transaction.return_value = (True, [])
    # transactions 子对象（用于构造 compare）
    fake_client.transactions = MagicMock()

    fake.client.return_value = fake_client
    fake._client = fake_client
    return fake


@pytest.fixture
def fake_etcd3_module():
    """注入 mock etcd3 模块到 sys.modules，测试结束后恢复。"""
    fake = _build_fake_etcd3_module()
    with patch.dict(sys.modules, {"etcd3": fake}):
        yield fake


class TestEtcdBackendWithMock:
    """用 mock etcd3 测试 EtcdKVBackend 的方法调用。"""

    def test_set_stores_key_value(self, fake_etcd3_module):
        """set() 应调用 client.put 写入 key/value。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            backend.set("foo", "bar")

            client = fake_etcd3_module._client
            client.put.assert_called_once()
            # 验证 key 带命名空间前缀
            call_args = client.put.call_args.args
            assert call_args[0] == "/maop/foo"
            assert call_args[1] == "bar"
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_set_with_ttl_creates_lease(self, fake_etcd3_module):
        """set(ttl>0) 应创建 lease 并绑定到 put。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            fake_lease = MagicMock()
            fake_lease.id = "lease-123"
            client = fake_etcd3_module._client
            client.lease.return_value = fake_lease

            backend.set("tempkey", "tempval", ttl=60)

            client.lease.assert_called_once_with(60)
            # put 应带 lease 参数
            put_kwargs = client.put.call_args.kwargs
            assert put_kwargs.get("lease") is fake_lease
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_get_returns_decoded_string(self, fake_etcd3_module):
        """get() 应将 bytes 值解码为 str 返回。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            # 模拟 etcd 返回 bytes 值
            client = fake_etcd3_module._client
            client.get.return_value = (b"hello", MagicMock())

            value = backend.get("greet")
            assert value == "hello"
            # 验证调用时使用了带前缀的 key
            client.get.assert_called_once_with("/maop/greet")
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_get_missing_key_returns_none(self, fake_etcd3_module):
        """get() 在 key 不存在时应返回 None。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            client.get.return_value = (None, MagicMock())

            assert backend.get("missing") is None
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_delete_existing_key_returns_true(self, fake_etcd3_module):
        """delete() 在 key 存在时应调用 client.delete 并返回 True。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            # 先 get 探测 key 存在
            client.get.return_value = (b"present", MagicMock())

            ok = backend.delete("k")
            assert ok is True
            client.delete.assert_called_once_with("/maop/k")
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_delete_missing_key_returns_false(self, fake_etcd3_module):
        """delete() 在 key 不存在时应返回 False，且不调用 client.delete。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            client.get.return_value = (None, MagicMock())

            ok = backend.delete("nope")
            assert ok is False
            client.delete.assert_not_called()
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_list_keys_strips_namespace_prefix(self, fake_etcd3_module):
        """list_keys() 应剥离命名空间前缀返回逻辑 key。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            # 模拟 etcd 返回两个 key（带命名空间前缀）
            meta1 = MagicMock()
            meta1.key = b"/maop/a"
            meta2 = MagicMock()
            meta2.key = b"/maop/b"
            client.get_prefix.return_value = iter([
                (b"v1", meta1),
                (b"v2", meta2),
            ])

            keys = backend.list_keys()
            assert keys == ["a", "b"]
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_cas_success_returns_true(self, fake_etcd3_module):
        """cas() 在 etcd 事务成功时应返回 True。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            client.transaction.return_value = (True, [])

            ok = backend.cas("k", "expected", "new")
            assert ok is True
            client.transaction.assert_called_once()
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_cas_failure_returns_false(self, fake_etcd3_module):
        """cas() 在 etcd 事务失败时应返回 False。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379)
            client = fake_etcd3_module._client
            client.transaction.return_value = (False, [])

            ok = backend.cas("k", "wrong", "new")
            assert ok is False
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_connection_failure_raises_runtime_error(self, fake_etcd3_module):
        """etcd3.client 或 status() 抛异常时应转为 RuntimeError。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            # 让 status() 抛异常
            client = fake_etcd3_module._client
            client.status.side_effect = OSError("connection refused")
            with pytest.raises(RuntimeError, match="无法连接 etcd"):
                EtcdKVBackend(host="bad", port=9999)
        finally:
            client.status.side_effect = None
            sys.modules.pop("maop.core.backends_distributed", None)

    def test_namespace_prefix_applied_to_keys(self, fake_etcd3_module):
        """所有 key 应被加上 /maop 命名空间前缀。"""
        sys.modules.pop("maop.core.backends_distributed", None)
        try:
            from maop.core.backends_distributed import EtcdKVBackend

            backend = EtcdKVBackend(host="localhost", port=2379, namespace="custom")
            backend.set("k", "v")

            client = fake_etcd3_module._client
            # 应使用 /custom 前缀而非默认 /maop
            call_args = client.put.call_args.args
            assert call_args[0] == "/custom/k"
        finally:
            sys.modules.pop("maop.core.backends_distributed", None)
