"""Coverage tests for maop.core.agent_proxy — AgentProxy registry/dispatcher."""
from __future__ import annotations


import pytest

from maop.core.agent.delegation.agent_proxy import (
    AdapterConfig,
    AdapterStatus,
    AgentAdapter,
    AgentProxy,
)


class _FakeAdapter(AgentAdapter):
    """Minimal concrete adapter for testing."""

    def __init__(self, *, connect_ok=True, execute_result="ok", healthy=True,
                 raise_execute=None, raise_connect=None, raise_disconnect=None,
                 raise_sync=None, raise_health=None):
        self._connect_ok = connect_ok
        self._execute_result = execute_result
        self._healthy = healthy
        self._raise_execute = raise_execute
        self._raise_connect = raise_connect
        self._raise_disconnect = raise_disconnect
        self._raise_sync = raise_sync
        self._raise_health = raise_health
        self.disconnect_called = False
        self.sync_config_called = False

    def connect(self) -> bool:
        if self._raise_connect:
            raise self._raise_connect
        return self._connect_ok

    def execute(self, task: str, **kwargs) -> str:
        if self._raise_execute:
            raise self._raise_execute
        return self._execute_result

    def health_check(self) -> bool:
        if self._raise_health:
            raise self._raise_health
        return self._healthy

    def sync_config(self, config: dict) -> None:
        if self._raise_sync:
            raise self._raise_sync
        self.sync_config_called = True

    def disconnect(self) -> None:
        self.disconnect_called = True
        if self._raise_disconnect:
            raise self._raise_disconnect


class TestAgentProxy:
    def test_register_and_get(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        adapter = _FakeAdapter()
        proxy.register("a1", adapter)
        assert proxy.get("a1") is adapter
        assert "a1" in proxy.list_adapters()

    def test_get_unregistered_returns_none(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        assert proxy.get("nope") is None
        assert proxy.list_adapters() == []

    def test_call_success(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(execute_result="result"))
        assert proxy.call("a1", "task") == "result"

    def test_call_unregistered_raises_keyerror(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        with pytest.raises(KeyError, match="not registered"):
            proxy.call("nope", "task")

    def test_call_execute_exception_tracked_and_reraised(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_execute=RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            proxy.call("a1", "task")
        # Error count should be incremented
        status = proxy.get_status("a1")
        assert status.error_count == 1

    def test_unregister_existing(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        adapter = _FakeAdapter()
        proxy.register("a1", adapter)
        proxy.unregister("a1")
        assert proxy.get("a1") is None
        assert adapter.disconnect_called

    def test_unregister_nonexistent_no_error(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.unregister("nope")  # should not raise

    def test_unregister_disconnect_exception_handled(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_disconnect=RuntimeError("disconnect failed")))
        proxy.unregister("a1")  # should not raise
        assert proxy.get("a1") is None

    def test_connect_all_success(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(connect_ok=True))
        proxy.register("a2", _FakeAdapter(connect_ok=True))
        results = proxy.connect_all()
        assert results == {"a1": True, "a2": True}

    def test_connect_all_mixed(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(connect_ok=True))
        proxy.register("a2", _FakeAdapter(connect_ok=False))
        results = proxy.connect_all()
        assert results == {"a1": True, "a2": False}

    def test_connect_all_exception(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_connect=RuntimeError("connect failed")))
        results = proxy.connect_all()
        assert results == {"a1": False}

    def test_health_check_all(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(healthy=True))
        proxy.register("a2", _FakeAdapter(healthy=False))
        results = proxy.health_check_all()
        assert results == {"a1": True, "a2": False}

    def test_health_check_all_exception(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_health=RuntimeError("health failed")))
        results = proxy.health_check_all()
        assert results == {"a1": False}

    def test_sync_config_success(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        adapter = _FakeAdapter()
        proxy.register("a1", adapter)
        proxy.sync_config("a1", {"key": "value"})
        assert adapter.sync_config_called

    def test_sync_config_unregistered_raises(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        with pytest.raises(KeyError, match="not registered"):
            proxy.sync_config("nope", {})

    def test_get_status_registered(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(healthy=True))
        status = proxy.get_status("a1")
        assert isinstance(status, AdapterStatus)
        assert status.name == "a1"
        assert status.healthy is True

    def test_get_status_unregistered_returns_empty(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        status = proxy.get_status("nope")
        assert status.name == "nope"
        assert status.connected is False

    def test_get_status_health_exception(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_health=RuntimeError("health failed")))
        status = proxy.get_status("a1")
        assert status.healthy is False

    def test_disconnect_all(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        a1 = _FakeAdapter()
        a2 = _FakeAdapter()
        proxy.register("a1", a1)
        proxy.register("a2", a2)
        proxy.disconnect_all()
        assert a1.disconnect_called
        assert a2.disconnect_called

    def test_disconnect_all_exception_handled(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(raise_disconnect=RuntimeError("disconnect failed")))
        proxy.disconnect_all()  # should not raise

    def test_call_updates_status(self, tmp_path):
        proxy = AgentProxy(root_dir=tmp_path)
        proxy.register("a1", _FakeAdapter(execute_result="r"))
        proxy.call("a1", "task1")
        proxy.call("a1", "task2")
        status = proxy.get_status("a1")
        assert status.call_count == 2
        assert status.connected is True
        assert status.last_call_at > 0


class TestAdapterConfig:
    def test_defaults(self):
        c = AdapterConfig()
        assert c.name == ""
        assert c.adapter_type == ""
        assert c.connection_params == {}
        assert c.max_retries == 3
        assert c.timeout_s == 30.0

    def test_custom(self):
        c = AdapterConfig(name="a", adapter_type="custom", max_retries=5, timeout_s=60.0)
        assert c.name == "a"
        assert c.max_retries == 5
        assert c.timeout_s == 60.0


class TestAdapterStatus:
    def test_defaults(self):
        s = AdapterStatus()
        assert s.connected is False
        assert s.healthy is False
        assert s.call_count == 0
        assert s.error_count == 0