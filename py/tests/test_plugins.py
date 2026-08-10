"""Tests for maop.core.plugins — PluginSpec contract, PluginManager lifecycle,
dependency resolution, hook dispatch, and error isolation."""

from __future__ import annotations

import pytest

from maop.core.plugins import (
    PLUGIN_API_VERSION,
    HookPoint,
    PluginContext,
    PluginError,
    PluginManager,
    PluginMetadata,
    PluginSpec,
    PluginState,
)


# ── test fixtures: minimal plugin implementations ─────────────────────


class _BasePlugin(PluginSpec):
    """Minimal plugin with overridable name and a recorded lifecycle."""

    def __init__(self, name: str, version: str = "1.0.0", **meta: object) -> None:
        self._name = name
        self._version = version
        self._meta = dict(meta)
        self.events: list[str] = []
        self.ctx: PluginContext | None = None

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name=self._name, version=self._version, **self._meta)

    def on_load(self, ctx: PluginContext) -> None:
        self.events.append("load")
        self.ctx = ctx

    def on_unload(self) -> None:
        self.events.append("unload")

    def on_start(self) -> None:
        self.events.append("start")

    def on_stop(self) -> None:
        self.events.append("stop")

    def on_error(self, exc: BaseException) -> None:
        self.events.append(f"error:{type(exc).__name__}")


class _HookPlugin(_BasePlugin):
    """Plugin that registers a pre_route hook mutating the payload."""

    def __init__(self, name: str, tag: str = "x") -> None:
        super().__init__(name)
        self.tag = tag

    def get_hooks(self) -> dict[str, object]:
        return {"pre_route": self._pre_route}

    def _pre_route(self, payload: dict) -> dict:
        payload.setdefault("tags", []).append(self.tag)
        return payload


class _BadHookPlugin(_BasePlugin):
    """Plugin whose pre_route hook raises."""

    def get_hooks(self) -> dict[str, object]:
        return {"pre_route": self._boom}

    def _boom(self, payload: dict) -> dict:
        raise RuntimeError("boom")


# ── PluginMetadata / api compatibility ────────────────────────────────


class TestPluginMetadata:
    def test_defaults(self):
        md = PluginMetadata(name="p")
        assert md.name == "p"
        assert md.version == "0.1.0"
        assert md.api_version == PLUGIN_API_VERSION
        assert md.priority == 100
        assert md.is_api_compatible()

    def test_api_compatible_same_major(self):
        md = PluginMetadata(name="p", api_version="1.0")
        assert md.is_api_compatible("1.2")

    def test_api_incompatible_higher_minor(self):
        md = PluginMetadata(name="p", api_version="1.5")
        assert not md.is_api_compatible("1.2")

    def test_api_incompatible_different_major(self):
        md = PluginMetadata(name="p", api_version="2.0")
        assert not md.is_api_compatible("1.0")

    def test_api_unparseable_treated_compatible(self):
        md = PluginMetadata(name="p", api_version="weird")
        assert md.is_api_compatible("1.0")


# ── PluginManager registration ────────────────────────────────────────


class TestRegistration:
    def test_register_and_metadata(self):
        mgr = PluginManager()
        p = _BasePlugin("alpha")
        md = mgr.register(p)
        assert md.name == "alpha"
        assert "alpha" in mgr
        assert len(mgr) == 1

    def test_register_duplicate_raises(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        with pytest.raises(PluginError, match="already registered"):
            mgr.register(_BasePlugin("alpha"))

    def test_register_incompatible_api_strict(self):
        mgr = PluginManager(strict_api=True)
        with pytest.raises(PluginError, match="incompatible"):
            mgr.register(_BasePlugin("alpha", api_version="2.0"))

    def test_register_incompatible_api_non_strict(self):
        mgr = PluginManager(strict_api=False)
        md = mgr.register(_BasePlugin("alpha", api_version="2.0"))
        assert md.name == "alpha"

    def test_unregister(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        assert mgr.unregister("alpha") is True
        assert "alpha" not in mgr

    def test_unregister_missing(self):
        mgr = PluginManager()
        assert mgr.unregister("nope") is False

    def test_unregister_loaded_refuses(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        mgr.load("alpha")
        with pytest.raises(PluginError, match="stop\\(\\) and unload"):
            mgr.unregister("alpha")

    def test_list_plugins(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("a"))
        mgr.register(_BasePlugin("b"))
        names = {md.name for md in mgr.list_plugins()}
        assert names == {"a", "b"}


# ── lifecycle ─────────────────────────────────────────────────────────


class TestLifecycle:
    def test_load_start_stop_unload(self):
        mgr = PluginManager()
        p = _BasePlugin("alpha")
        mgr.register(p)
        mgr.load("alpha")
        assert mgr.state("alpha") == PluginState.LOADED
        mgr.start("alpha")
        assert mgr.state("alpha") == PluginState.STARTED
        mgr.stop("alpha")
        assert mgr.state("alpha") == PluginState.STOPPED
        mgr.unload("alpha")
        assert mgr.state("alpha") == PluginState.UNLOADED
        assert p.events == ["load", "start", "stop", "unload"]

    def test_load_idempotent(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        mgr.load("alpha")
        mgr.load("alpha")  # second call is a no-op
        assert mgr.state("alpha") == PluginState.LOADED

    def test_start_idempotent(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        mgr.load("alpha")
        mgr.start("alpha")
        mgr.start("alpha")
        assert mgr.state("alpha") == PluginState.STARTED

    def test_start_without_load_raises(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        with pytest.raises(PluginError, match="must be LOADED"):
            mgr.start("alpha")

    def test_stop_without_start_is_noop(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        mgr.load("alpha")
        mgr.stop("alpha")  # LOADED → stop is no-op
        assert mgr.state("alpha") == PluginState.LOADED

    def test_unload_idempotent(self):
        mgr = PluginManager()
        mgr.register(_BasePlugin("alpha"))
        mgr.load("alpha")
        mgr.unload("alpha")
        mgr.unload("alpha")  # second call is a no-op
        assert mgr.state("alpha") == PluginState.UNLOADED

    def test_reload(self):
        mgr = PluginManager()
        p = _BasePlugin("alpha")
        mgr.register(p)
        mgr.load("alpha")
        mgr.start("alpha")
        mgr.reload("alpha", config={"k": "v"})
        assert mgr.state("alpha") == PluginState.LOADED
        assert p.ctx.config == {"k": "v"}

    def test_load_unknown_raises(self):
        mgr = PluginManager()
        with pytest.raises(PluginError, match="not registered"):
            mgr.load("nope")

    def test_on_load_failure_marks_errored(self):
        class _FailLoad(_BasePlugin):
            def on_load(self, ctx: PluginContext) -> None:
                raise RuntimeError("nope")

        mgr = PluginManager()
        mgr.register(_FailLoad("alpha"))
        with pytest.raises(PluginError, match="on_load failed"):
            mgr.load("alpha")
        assert mgr.state("alpha") == PluginState.ERRORED

    def test_on_start_failure_marks_errored(self):
        class _FailStart(_BasePlugin):
            def on_start(self) -> None:
                raise RuntimeError("nope")

        mgr = PluginManager()
        mgr.register(_FailStart("alpha"))
        mgr.load("alpha")
        with pytest.raises(PluginError, match="on_start failed"):
            mgr.start("alpha")
        assert mgr.state("alpha") == PluginState.ERRORED


# ── config & context ──────────────────────────────────────────────────


class TestConfigContext:
    def test_config_defaults_merged(self, tmp_path):
        class _CfgPlugin(_BasePlugin):
            def get_config_defaults(self) -> dict:
                return {"theme": "light", "limit": 10}

        mgr = PluginManager(data_dir=tmp_path)
        p = _CfgPlugin("alpha")
        mgr.register(p)
        ctx = mgr.load("alpha", config={"limit": 99})
        assert ctx.config == {"theme": "light", "limit": 99}

    def test_data_dir_created_per_plugin(self, tmp_path):
        mgr = PluginManager(data_dir=tmp_path)
        p = _BasePlugin("alpha")
        mgr.register(p)
        ctx = mgr.load("alpha")
        assert ctx.data_dir.endswith("alpha")
        from pathlib import Path
        assert Path(ctx.data_dir).exists()

    def test_logger_named_after_plugin(self):
        mgr = PluginManager()
        p = _BasePlugin("alpha")
        mgr.register(p)
        ctx = mgr.load("alpha")
        assert ctx.logger.name == "maop.plugin.alpha"

    def test_host_info_passed_through(self):
        mgr = PluginManager(host_info={"edition": "enterprise"})
        p = _BasePlugin("alpha")
        mgr.register(p)
        ctx = mgr.load("alpha")
        assert ctx.host_info == {"edition": "enterprise"}


# ── dependencies ──────────────────────────────────────────────────────


class TestDependencies:
    def test_dependency_loaded_first(self):
        mgr = PluginManager()
        a = _BasePlugin("a")
        b = _BasePlugin("b", dependencies=["a"])
        mgr.register(a)
        mgr.register(b)
        mgr.load("b")  # should auto-load a
        assert mgr.state("a") == PluginState.LOADED
        assert mgr.state("b") == PluginState.LOADED

    def test_missing_dependency_raises(self):
        mgr = PluginManager()
        b = _BasePlugin("b", dependencies=["missing"])
        mgr.register(b)
        with pytest.raises(PluginError, match="not registered"):
            mgr.load("b")

    def test_load_all_resolves_order(self):
        mgr = PluginManager()
        a = _BasePlugin("a")
        b = _BasePlugin("b", dependencies=["a"])
        c = _BasePlugin("c", dependencies=["b"], priority=10)
        for p in (c, b, a):  # register out of order
            mgr.register(p)
        loaded = mgr.load_all()
        assert set(loaded) == {"a", "b", "c"}
        assert loaded.index("a") < loaded.index("b") < loaded.index("c")

    def test_dependency_cycle_detected(self):
        mgr = PluginManager()
        a = _BasePlugin("a", dependencies=["b"])
        b = _BasePlugin("b", dependencies=["a"])
        mgr.register(a)
        mgr.register(b)
        with pytest.raises(PluginError, match="cycle"):
            mgr.load_all()


# ── hook dispatch ─────────────────────────────────────────────────────


class TestHookDispatch:
    def test_dispatch_invokes_started_plugins(self):
        mgr = PluginManager()
        p = _HookPlugin("alpha", tag="A")
        mgr.register(p)
        mgr.load("alpha")
        mgr.start("alpha")
        out = mgr.dispatch(HookPoint.PRE_ROUTE, {"q": "hi"})
        assert out["tags"] == ["A"]

    def test_dispatch_skips_not_started(self):
        mgr = PluginManager()
        p = _HookPlugin("alpha", tag="A")
        mgr.register(p)
        mgr.load("alpha")  # loaded but not started
        out = mgr.dispatch(HookPoint.PRE_ROUTE, {"q": "hi"})
        assert "tags" not in out

    def test_dispatch_chains_multiple(self):
        mgr = PluginManager()
        a = _HookPlugin("a", tag="A")
        b = _HookPlugin("b", tag="B")
        mgr.register(a)
        mgr.register(b)
        mgr.load_all()
        mgr.start_all()
        out = mgr.dispatch("pre_route", {})
        assert out["tags"] == ["A", "B"]

    def test_dispatch_error_isolation(self):
        mgr = PluginManager()
        bad = _BadHookPlugin("bad")
        good = _HookPlugin("good", tag="G")
        mgr.register(bad)
        mgr.register(good)
        mgr.load_all()
        mgr.start_all()
        out = mgr.dispatch("pre_route", {})
        # good plugin still ran despite bad plugin raising
        assert out["tags"] == ["G"]
        assert mgr.state("bad") == PluginState.ERRORED
        assert mgr.state("good") == PluginState.STARTED

    def test_hooks_for_lists_callbacks(self):
        mgr = PluginManager()
        p = _HookPlugin("alpha")
        mgr.register(p)
        mgr.load("alpha")
        mgr.start("alpha")
        pairs = mgr.hooks_for("pre_route")
        assert len(pairs) == 1
        assert pairs[0][0] == "alpha"

    def test_hooks_for_unknown_hook_empty(self):
        mgr = PluginManager()
        p = _HookPlugin("alpha")
        mgr.register(p)
        mgr.load("alpha")
        mgr.start("alpha")
        assert mgr.hooks_for("post_route") == []


# ── bulk lifecycle ────────────────────────────────────────────────────


class TestBulkLifecycle:
    def test_start_all_stop_all(self):
        mgr = PluginManager()
        for n in ("a", "b", "c"):
            mgr.register(_BasePlugin(n))
        mgr.load_all()
        started = mgr.start_all()
        assert set(started) == {"a", "b", "c"}
        for n in ("a", "b", "c"):
            assert mgr.state(n) == PluginState.STARTED
        stopped = mgr.stop_all()
        assert set(stopped) == {"a", "b", "c"}
        for n in ("a", "b", "c"):
            assert mgr.state(n) == PluginState.STOPPED

    def test_unload_all(self):
        mgr = PluginManager()
        for n in ("a", "b"):
            mgr.register(_BasePlugin(n))
        mgr.load_all()
        mgr.start_all()
        unloaded = mgr.unload_all()
        assert set(unloaded) == {"a", "b"}
        for n in ("a", "b"):
            assert mgr.state(n) == PluginState.UNLOADED

    def test_get_returns_spec(self):
        mgr = PluginManager()
        p = _BasePlugin("alpha")
        mgr.register(p)
        assert mgr.get("alpha") is p
        assert mgr.get("nope") is None

    def test_state_missing_returns_none(self):
        mgr = PluginManager()
        assert mgr.state("nope") is None