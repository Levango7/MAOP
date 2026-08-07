"""Tests for PluginManager and CostTracker (Phase 5)."""

from __future__ import annotations

import json

import pytest

from maop.core.monitoring.cost_tracker import BudgetStatus, CostEntry, CostSummary, CostTracker, ModelPricing
from maop.core.agent.plugins_hooks.plugin import PluginInfo, PluginManager, PluginManifest, PluginState

# ═══════════════════════════════════════════════════════════════════
# PluginManager Tests
# ═══════════════════════════════════════════════════════════════════

class TestPluginManifest:
    def test_defaults(self):
        m = PluginManifest(name="demo")
        assert m.name == "demo"
        assert m.version == "0.1.0"
        assert m.entry_point == "main.py"
        assert m.init_function == "MAOP_plugin_init"
        assert m.hooks == []

    def test_custom(self):
        m = PluginManifest(
            name="my-plug", version="1.0.0", description="test",
            entry_point="plugin.py", init_function="init",
            hooks=[{"event": "agent.pre_dispatch", "callback": "my_hook"}],
        )
        assert m.version == "1.0.0"
        assert len(m.hooks) == 1


class TestPluginInfo:
    def test_defaults(self):
        info = PluginInfo(id="p1", name="test")
        assert info.state == PluginState.DISCOVERED
        assert info.error == ""
        assert info.config == {}


class TestPluginState:
    def test_values(self):
        assert PluginState.DISCOVERED == "discovered"
        assert PluginState.LOADED == "loaded"
        assert PluginState.STARTED == "started"
        assert PluginState.STOPPED == "stopped"
        assert PluginState.ERRORED == "errored"


class TestPluginManagerInit:
    def test_init(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        assert mgr._plugins_dir == tmp_path / "plugins"
        # Unified DB mode (ADR-011): plugins share maop.db
        assert (tmp_path / "data" / "maop.db").exists()


class TestPluginDiscover:
    def test_discover_empty(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        found = mgr.discover()
        assert found == []

    def test_discover_valid_plugin(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo", "version": "0.2.0", "description": "A demo plugin"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(config): pass\ndef MAOP_plugin_shutdown(): pass\n",
            encoding="utf-8",
        )

        mgr = PluginManager(root_dir=str(tmp_path))
        found = mgr.discover()
        assert len(found) == 1
        assert found[0].name == "demo"
        assert found[0].version == "0.2.0"
        assert found[0].state == PluginState.DISCOVERED

    def test_discover_no_manifest(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "broken"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "main.py").write_text("pass", encoding="utf-8")

        mgr = PluginManager(root_dir=str(tmp_path))
        found = mgr.discover()
        assert len(found) == 0


class TestPluginLoad:
    def test_load_valid(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(config): return {'started': True}\n",
            encoding="utf-8",
        )

        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        plugins = mgr.list_plugins()
        pid = plugins[0].id
        info = mgr.load(pid)
        assert info.state == PluginState.LOADED
        assert info.loaded_at != ""

    def test_load_missing_entry_point(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo", "entry_point": "missing.py"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")

        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state == PluginState.ERRORED
        assert "not found" in info.error

    def test_load_missing_init_function(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo", "init_function": "nonexistent"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text("pass\n", encoding="utf-8")

        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state == PluginState.ERRORED
        assert "not found" in info.error


class TestPluginLifecycle:
    def test_start_stop(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "started = False\n"
            "def MAOP_plugin_init(config): global started; started = True\n"
            "def MAOP_plugin_shutdown(): global started; started = False\n",
            encoding="utf-8",
        )

        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        mgr.load(pid)

        info = mgr.start(pid)
        assert info.state == PluginState.STARTED
        assert info.started_at != ""

        info = mgr.stop(pid)
        assert info.state == PluginState.STOPPED

    def test_start_not_loaded_fails(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        info = PluginInfo(id="fake", name="fake", state=PluginState.DISCOVERED)
        mgr._upsert_db(info)
        with pytest.raises(ValueError, match="must be loaded"):
            mgr.start("fake")


class TestPluginQuery:
    def test_list_plugins(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        info = PluginInfo(id="p1", name="test1", state=PluginState.DISCOVERED)
        mgr._upsert_db(info)
        plugins = mgr.list_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "test1"

    def test_list_filter_by_state(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr._upsert_db(PluginInfo(id="p1", name="a", state=PluginState.LOADED))
        mgr._upsert_db(PluginInfo(id="p2", name="b", state=PluginState.ERRORED))
        loaded = mgr.list_plugins(state=PluginState.LOADED)
        assert len(loaded) == 1
        assert loaded[0].name == "a"

    def test_get_plugin(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr._upsert_db(PluginInfo(id="p1", name="test"))
        info = mgr.get_plugin("p1")
        assert info is not None
        assert info.name == "test"

    def test_get_plugin_not_found(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        assert mgr.get_plugin("nonexistent") is None


class TestPluginConfig:
    def test_update_config(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr._upsert_db(PluginInfo(id="p1", name="test"))
        info = mgr.update_config("p1", config={"key": "value"})
        assert info.config == {"key": "value"}


class TestPluginReload:
    def test_reload(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "demo"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(config): pass\n",
            encoding="utf-8",
        )

        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        mgr.load(pid)
        mgr.start(pid)

        info = mgr.reload(pid)
        assert info.state == PluginState.STARTED


# ═══════════════════════════════════════════════════════════════════
# CostTracker Tests
# ═══════════════════════════════════════════════════════════════════

class TestCostEntry:
    def test_defaults(self):
        e = CostEntry(id="e1")
        assert e.prompt_tokens == 0
        assert e.cost_usd == 0.0


class TestCostSummary:
    def test_defaults(self):
        s = CostSummary()
        assert s.total_tokens == 0
        assert s.total_cost_usd == 0.0
        assert s.by_model == {}


class TestBudgetStatus:
    def test_defaults(self):
        b = BudgetStatus()
        assert b.daily_over_budget is False
        assert b.monthly_over_budget is False


class TestModelPricing:
    def test_defaults(self):
        p = ModelPricing()
        assert p.prompt_per_1m == 0.0


class TestCostTrackerInit:
    def test_init(self, tmp_path):
        CostTracker(root_dir=str(tmp_path))
        # Unified DB mode (ADR-011): costs share maop.db
        assert (tmp_path / "data" / "maop.db").exists()


class TestCostRecord:
    def test_record_basic(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        entry = tracker.record(
            session_id="s1", agent="coder", model="gpt-4o",
            prompt_tokens=100, completion_tokens=50, latency_ms=500,
        )
        assert entry.id.startswith("cost-")
        assert entry.session_id == "s1"
        assert entry.total_tokens == 150
        assert entry.cost_usd > 0
        assert entry.latency_ms == 500

    def test_record_auto_total(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        entry = tracker.record(model="gpt-4o", prompt_tokens=200, completion_tokens=100)
        assert entry.total_tokens == 300

    def test_record_unknown_model_zero_cost(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        entry = tracker.record(model="unknown-model", prompt_tokens=1000, completion_tokens=500)
        assert entry.cost_usd == 0.0

    def test_record_gpt4o_cost(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        entry = tracker.record(model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert entry.cost_usd == 12.50

    def test_record_deepseek_cost(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        entry = tracker.record(model="deepseek-chat", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert entry.cost_usd == 0.42


class TestCostQuery:
    def test_get_entries(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(session_id="s1", agent="a1", model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        tracker.record(session_id="s2", agent="a2", model="claude-3.5-sonnet", prompt_tokens=200, completion_tokens=100)

        entries = tracker.get_entries()
        assert len(entries) == 2

    def test_get_entries_filter_session(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(session_id="s1", model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        tracker.record(session_id="s2", model="gpt-4o", prompt_tokens=200, completion_tokens=100)

        entries = tracker.get_entries(session_id="s1")
        assert len(entries) == 1

    def test_get_entries_filter_agent(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(agent="coder", model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        tracker.record(agent="verifier", model="gpt-4o", prompt_tokens=200, completion_tokens=100)

        entries = tracker.get_entries(agent="coder")
        assert len(entries) == 1

    def test_get_entries_filter_model(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        tracker.record(model="claude-3.5-sonnet", prompt_tokens=200, completion_tokens=100)

        entries = tracker.get_entries(model="gpt-4o")
        assert len(entries) == 1


class TestCostSummaryQuery:
    def test_summary_empty(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        s = tracker.summary()
        assert s.total_calls == 0

    def test_summary_with_data(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(session_id="s1", agent="coder", model="gpt-4o", prompt_tokens=100, completion_tokens=50, latency_ms=100)
        tracker.record(session_id="s1", agent="coder", model="gpt-4o", prompt_tokens=200, completion_tokens=100, latency_ms=200)

        s = tracker.summary()
        assert s.total_calls == 2
        assert s.total_tokens == 450
        assert s.total_cost_usd > 0
        assert s.avg_latency_ms == 150.0
        assert "gpt-4o" in s.by_model
        assert "coder" in s.by_agent
        assert "s1" in s.by_session

    def test_summary_filter_agent(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.record(agent="coder", model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        tracker.record(agent="verifier", model="gpt-4o", prompt_tokens=200, completion_tokens=100)

        s = tracker.summary(agent="coder")
        assert s.total_calls == 1


class TestBudgetQuery:
    def test_no_budget(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        status = tracker.budget_status()
        assert status.daily_over_budget is False
        assert status.monthly_over_budget is False

    def test_daily_over_budget(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path), daily_limit_usd=0.001)
        tracker.record(model="gpt-4o", prompt_tokens=100_000, completion_tokens=100_000)
        status = tracker.budget_status()
        assert status.daily_spent_usd > 0
        assert status.daily_over_budget is True

    def test_monthly_over_budget(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path), monthly_limit_usd=0.001)
        tracker.record(model="gpt-4o", prompt_tokens=100_000, completion_tokens=100_000)
        status = tracker.budget_status()
        assert status.monthly_over_budget is True

    def test_within_budget(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path), daily_limit_usd=100.0)
        tracker.record(model="gpt-4o", prompt_tokens=100, completion_tokens=50)
        status = tracker.budget_status()
        assert status.daily_over_budget is False
        assert status.daily_remaining_usd > 0


class TestPricing:
    def test_get_pricing(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        pricing = tracker.get_pricing()
        assert "gpt-4o" in pricing
        assert pricing["gpt-4o"]["prompt_per_1m"] == 2.50

    def test_update_pricing(self, tmp_path):
        tracker = CostTracker(root_dir=str(tmp_path))
        tracker.update_pricing("custom-model", prompt_per_1m=5.0, completion_per_1m=20.0)
        pricing = tracker.get_pricing()
        assert "custom-model" in pricing
        assert pricing["custom-model"]["prompt_per_1m"] == 5.0

    def test_custom_pricing(self, tmp_path):
        pricing = {"my-model": ModelPricing(prompt_per_1m=1.0, completion_per_1m=2.0)}
        tracker = CostTracker(root_dir=str(tmp_path), pricing=pricing)
        entry = tracker.record(model="my-model", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert entry.cost_usd == 3.0
