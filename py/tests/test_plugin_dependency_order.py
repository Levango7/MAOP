"""插件依赖顺序（P1-3 收敛：core/plugins 的依赖解析能力并入在用实现）。

背景：仓库曾有两套插件实现——``maop.core.agent.plugins_hooks``（在用）与
``maop.core.plugins``（从未接线，已于本次删除）。后者的 ``PluginManager``
提供依赖排序，但它的 ``PluginManifest.dependencies`` 字段在前者中**早已声明
却从未被读取**：``load_all()`` 按目录顺序加载，``start_all()``/``stop_all()``
按数据库顺序。本文件锁定补齐后的行为。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maop.core.agent.plugins_hooks.plugin import (
    PluginInfo,
    PluginManager,
    PluginManifest,
    PluginState,
)


def _write_plugin(plugins_dir: Path, dir_name: str, name: str, deps: list[str]) -> None:
    """Create a minimal loadable plugin directory."""
    d = plugins_dir / dir_name
    d.mkdir(parents=True)
    manifest = f"name: {name}\nversion: '1.0.0'\nentry_point: main.py\ninit_function: MAOP_plugin_init\n"
    if deps:
        manifest += "dependencies:\n" + "".join(f"  - {x}\n" for x in deps)
    (d / "MAOP-plugin.yaml").write_text(manifest, encoding="utf-8")
    (d / "main.py").write_text(
        "def MAOP_plugin_init():\n    return {}\n", encoding="utf-8"
    )


@pytest.fixture
def pm(tmp_path, monkeypatch):
    # 未提供 checksum 时沙箱默认 fail-closed；本文件只关心顺序，故走 dev 模式。
    monkeypatch.setenv("MAOP_PLUGIN_STRICT_CHECKSUM", "0")
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
    return PluginManager(root_dir=str(tmp_path))


# ── 单元：_resolution_order 的排序语义 ────────────────────────────────


def _info(pid: str, name: str, path: str = "") -> PluginInfo:
    return PluginInfo(id=pid, name=name, path=path)


def _register(pm: PluginManager, infos: list[PluginInfo], deps: dict[str, list[str]]) -> None:
    for i in infos:
        pm._manifests[i.id] = PluginManifest(name=i.name, dependencies=deps.get(i.name, []))


def test_resolution_order_puts_dependencies_first(pm):
    infos = [_info("i1", "gamma"), _info("i2", "beta"), _info("i3", "alpha")]
    _register(pm, infos, {"gamma": ["beta"], "beta": ["alpha"]})
    order = [i.name for i in pm._resolution_order(infos)]
    assert order == ["alpha", "beta", "gamma"]


def test_resolution_order_deterministic_without_deps(pm):
    """无依赖时保持传入顺序（discover 按目录名排序），保证加载可复现。"""
    infos = [_info("i1", "c"), _info("i2", "a"), _info("i3", "b")]
    _register(pm, infos, {})
    assert [i.name for i in pm._resolution_order(infos)] == ["c", "a", "b"]


def test_resolution_order_missing_dependency_is_skipped(pm):
    """依赖未安装：跳过该边并告警，不抛异常（否则 load_all 会全盘中断）。"""
    infos = [_info("i1", "solo"), _info("i2", "needy")]
    _register(pm, infos, {"needy": ["ghost"]})
    order = [i.name for i in pm._resolution_order(infos)]
    assert sorted(order) == ["needy", "solo"]


def test_resolution_order_cycle_falls_back_without_raising(pm):
    """依赖成环：记录 ERROR 后按传入顺序降级，保证批量加载不被卡死。"""
    infos = [_info("i1", "a"), _info("i2", "b")]
    _register(pm, infos, {"a": ["b"], "b": ["a"]})
    order = [i.name for i in pm._resolution_order(infos)]
    assert sorted(order) == ["a", "b"]


def test_resolution_order_self_dependency_ignored(pm):
    infos = [_info("i1", "selfish")]
    _register(pm, infos, {"selfish": ["selfish"]})
    assert [i.name for i in pm._resolution_order(infos)] == ["selfish"]


# ── 集成：load_all 真的按依赖顺序加载 ─────────────────────────────────


def test_load_all_loads_dependencies_first(tmp_path, pm):
    """目录名刻意与依赖顺序相反——只按目录序加载会得到 gamma→beta→alpha。"""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(plugins_dir, "a-gamma", "gamma", ["beta"])
    _write_plugin(plugins_dir, "b-beta", "beta", ["alpha"])
    _write_plugin(plugins_dir, "c-alpha", "alpha", [])

    loaded = pm.load_all()
    order = [i.name for i in loaded if i.state == PluginState.LOADED]
    assert order == ["alpha", "beta", "gamma"], order


def test_load_all_still_returns_errored_plugins(tmp_path, pm):
    """坏插件仍出现在返回列表中（契约不变），只是不参与排序。"""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(plugins_dir, "a-good", "good", [])
    broken = plugins_dir / "b-broken"
    broken.mkdir()
    (broken / "MAOP-plugin.yaml").write_text(
        "name: broken\n: : invalid yaml :\n", encoding="utf-8"
    )

    loaded = pm.load_all()
    names = {i.name for i in loaded}
    assert "good" in names
    assert any(i.state == PluginState.ERRORED for i in loaded)


def test_load_all_empty_dir_still_works(tmp_path, pm):
    (tmp_path / "plugins").mkdir()
    assert pm.load_all() == []
