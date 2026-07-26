"""Tests for maop.core.services.ServiceContainer."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


class TestServiceContainer:
    def test_register_and_get(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        sc.register("demo", lambda: 42)
        assert sc.get("demo") == 42

    def test_get_caches_instance(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        obj = object()
        sc.register("demo", lambda: obj)
        assert sc.get("demo") is obj
        assert sc.get("demo") is obj

    def test_get_unknown_returns_none(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        assert sc.get("nonexistent", raise_on_failure=False) is None

    def test_get_unknown_raises(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        sc.register("bad", lambda: (_ for _ in ()).throw(ValueError("oops")))
        with pytest.raises(RuntimeError, match="bad"):
            sc.get("bad", raise_on_failure=True)

    def test_factory_failure_raises(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        def boom():
            raise ValueError("boom")

        sc = ServiceContainer(root_dir=fake_root)
        sc.register("boom_svc", boom)
        with pytest.raises(RuntimeError, match="boom_svc"):
            sc.get("boom_svc")

    def test_factory_failure_silent(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        def boom():
            raise ValueError("silent boom")

        sc = ServiceContainer(root_dir=fake_root)
        sc.register("boom_svc", boom)
        result = sc.get("boom_svc", raise_on_failure=False)
        assert result is None

    def test_set_override(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        sc.register("demo", lambda: "original")
        assert sc.get("demo") == "original"
        sc.set("demo", "overridden")
        assert sc.get("demo") == "overridden"

    def test_has(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        assert not sc.has("missing")
        sc.register("demo", lambda: 1)
        assert sc.has("demo")
        sc.get("demo")
        assert sc.has("demo")

    def test_defaults_registered(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        expected = [
            "circuit_breaker", "dispatcher", "guardrail", "verify_engine",
            "memory_store", "worker_pool", "load_balancer", "cache_guard",
            "result_cache", "timeseries", "message_queue", "hot_reload",
            "kv_store", "prompt_manager", "migration", "consolidator",
            "hook_manager",
        ]
        for name in expected:
            assert sc.has(name), f"Missing default service: {name}"

    def test_make_circuit_breaker(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        cb = sc.get("circuit_breaker")
        assert cb is not None

    def test_make_timeseries(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        ts = sc.get("timeseries")
        assert ts is not None

    def test_make_kv_store(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        kv = sc.get("kv_store")
        assert kv is not None

    def test_make_message_queue(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        mq = sc.get("message_queue")
        assert mq is not None

    def test_make_result_cache(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        rc = sc.get("result_cache")
        assert rc is not None

    def test_make_cache_guard(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        cg = sc.get("cache_guard")
        assert cg is not None

    def test_make_load_balancer(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        lb = sc.get("load_balancer")
        assert lb is not None

    def test_make_guardrail(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        gr = sc.get("guardrail")
        assert gr is not None

    def test_make_worker_pool(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        wp = sc.get("worker_pool")
        assert wp is not None

    def test_make_migration(self, fake_root: Path):
        from maop.core.services import ServiceContainer

        sc = ServiceContainer(root_dir=fake_root)
        mg = sc.get("migration")
        assert mg is not None
