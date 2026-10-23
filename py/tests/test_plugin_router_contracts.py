"""HTTP contracts for the plugin router's 10 frontend-used endpoints.

``plugin_service`` returns ``PluginInfo.model_dump()`` everywhere, so these
tests pin one thing that status-code assertions cannot: every field the manager
produces must survive the response model, and the state vocabulary on the wire
must be the five ``PluginState`` values (the old fixtures invented
``"reloaded"``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.core.agent.plugins_hooks.plugin_manager import PluginInfo, PluginState
from maop.dashboard.routers import plugin as plugin_router
from maop.dashboard.services import plugin_service

PLUGIN_FIELDS = {
    "id", "name", "version", "description", "author", "state", "path",
    "error", "loaded_at", "started_at", "config",
}

CONTRACT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/plugins"),
    ("POST", "/api/plugins/discover"),
    ("POST", "/api/plugins/load-all"),
    ("POST", "/api/plugins/start-all"),
    ("POST", "/api/plugins/stop-all"),
    ("PUT", "/api/plugins/{plugin_id}/config"),
    ("POST", "/api/plugins/{plugin_id}/load"),
    ("POST", "/api/plugins/{plugin_id}/reload"),
    ("POST", "/api/plugins/{plugin_id}/start"),
    ("POST", "/api/plugins/{plugin_id}/stop"),
]


def _info(state: PluginState = PluginState.STARTED, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "weather", "name": "Weather Plugin", "version": "1.2.0",
        "description": "hook-based weather", "author": "maop", "state": state.value,
        "path": "/plugins/weather", "error": "", "loaded_at": "2026-10-23T00:00:00+00:00",
        "started_at": "2026-10-23T00:00:01+00:00", "config": {"units": "metric"},
    }
    base.update(kw)
    return base


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(plugin_router, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(plugin_router.router)
    return TestClient(app)


def test_plugin_list_returns_every_produced_field(client, monkeypatch):
    row = _info()
    monkeypatch.setattr(plugin_service, "list_plugins", lambda state="": {"plugins": [row]})
    body = client.get("/api/plugins").json()
    assert body == {"status": "ok", "plugins": [row]}
    assert set(body["plugins"][0]) == PLUGIN_FIELDS


def test_discover_uses_the_discovered_key(client, monkeypatch):
    row = _info(state=PluginState.DISCOVERED, loaded_at="", started_at="")
    monkeypatch.setattr(plugin_service, "discover_plugins", lambda: {"discovered": [row]})
    assert client.post("/api/plugins/discover").json() == {"status": "ok", "discovered": [row]}


@pytest.mark.parametrize(
    ("fn", "path", "key"),
    [
        ("load_all_plugins", "/api/plugins/load-all", "plugins"),
        ("start_all_plugins", "/api/plugins/start-all", "plugins"),
        ("stop_all_plugins", "/api/plugins/stop-all", "plugins"),
    ],
)
def test_bulk_actions_return_plugin_lists(client, monkeypatch, fn: str, path: str, key: str) -> None:
    rows = [_info(state=PluginState.LOADED), _info(id="ocr", name="OCR", state=PluginState.STOPPED)]
    monkeypatch.setattr(plugin_service, fn, lambda: {key: rows})
    assert client.post(path).json() == {"status": "ok", key: rows}


@pytest.mark.parametrize(
    ("fn", "path"),
    [
        ("load_plugin", "/api/plugins/weather/load"),
        ("start_plugin", "/api/plugins/weather/start"),
        ("stop_plugin", "/api/plugins/weather/stop"),
        ("reload_plugin", "/api/plugins/weather/reload"),
    ],
)
def test_lifecycle_actions_echo_the_plugin(client, monkeypatch, fn: str, path: str) -> None:
    row = _info()
    monkeypatch.setattr(plugin_service, fn, lambda *a, **kw: row)
    body = client.post(path, json={"config": {"units": "imperial"}}).json()
    assert body == {"status": "ok", "plugin": row}


def test_update_config_echoes_the_new_config(client, monkeypatch):
    row = _info(config={"units": "imperial"})
    monkeypatch.setattr(plugin_service, "update_plugin_config", lambda pid, config=None: row)
    body = client.put("/api/plugins/weather/config", json={"config": {"units": "imperial"}}).json()
    assert body["plugin"]["config"] == {"units": "imperial"}


def test_state_is_restricted_to_the_enum(client, monkeypatch):
    """``reloaded`` is not a PluginState — reload lands on started."""
    assert {s.value for s in PluginState} == {
        "discovered", "loaded", "started", "stopped", "errored",
    }
    row = _info(state=PluginState.ERRORED, error="boom")
    monkeypatch.setattr(plugin_service, "reload_plugin", lambda pid: row)
    body = client.post("/api/plugins/weather/reload").json()
    assert body["plugin"]["state"] == "errored"
    assert body["plugin"]["error"] == "boom"


def test_real_manager_payload_passes_the_model(client, monkeypatch) -> None:
    """Round-trip the actual ``PluginInfo`` dump, not a hand-written dict."""
    dumped = PluginInfo(id="weather", name="Weather", state=PluginState.STARTED).model_dump()
    monkeypatch.setattr(plugin_service, "list_plugins", lambda state="": {"plugins": [dumped]})
    body = client.get("/api/plugins").json()
    assert body["plugins"][0] == {**dumped, "state": "started"}


def test_all_contract_routes_declare_a_response_model(client) -> None:
    schema = client.app.openapi()
    problems: list[str] = []
    for method, template in CONTRACT_ROUTES:
        op = (schema["paths"].get(template) or {}).get(method.lower())
        if op is None:
            problems.append(f"MISSING {method} {template}")
            continue
        body = ((op.get("responses") or {}).get("200", {}).get("content") or {}).get(
            "application/json", {},
        ).get("schema") or {}
        if "$ref" not in body and "properties" not in body:
            problems.append(f"UNtyped {method} {template}")
    assert problems == []
