"""Unit tests for MAOP.dashboard.routers.state module."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maop.dashboard.routers import state as st


# ── get_bridge singleton ──────────────────────────────────────────
def test_get_bridge_lazy_singleton(monkeypatch):
    """get_bridge() should return the same instance on repeated calls."""
    st._bridge = None  # reset
    fake = MagicMock(name="DataBridge")
    with patch.object(st, "DataBridge", return_value=fake):
        b1 = st.get_bridge()
        b2 = st.get_bridge()
    assert b1 is b2 is fake


def test_get_bridge_constructs_with_maop_root(monkeypatch):
    """First call constructs DataBridge(root_dir=MAOP_ROOT)."""
    st._bridge = None
    fake = MagicMock()
    captured = {}
    def fake_ctor(root_dir=None):
        captured["root_dir"] = root_dir
        return fake
    with patch.object(st, "DataBridge", side_effect=fake_ctor):
        st.get_bridge()
    assert captured["root_dir"] is st.MAOP_ROOT
    st._bridge = None  # cleanup


# ── init_subsystems / get_subsystems ──────────────────────────────
def test_init_subsystems_populates_dict():
    """init_subsystems() should populate _SUBSYSTEMS with many entries."""
    st._SUBSYSTEMS.clear()
    st.init_subsystems()
    subs = st.get_subsystems()
    assert isinstance(subs, dict)
    # Should have a sizable number of subsystem entries
    assert len(subs) >= 15
    # Each entry should have 'available' key
    for info in subs.values():
        assert "available" in info
        assert "module" in info


def test_init_subsystems_idempotent():
    """Calling init_subsystems twice should not re-import (dict already full)."""
    st._SUBSYSTEMS.clear()
    st.init_subsystems()
    first_keys = set(st._SUBSYSTEMS.keys())
    st.init_subsystems()
    second_keys = set(st._SUBSYSTEMS.keys())
    assert first_keys == second_keys


def test_get_subsystems_returns_dict():
    """get_subsystems() returns the _SUBSYSTEMS dict."""
    st._SUBSYSTEMS.clear()
    st._SUBSYSTEMS["test_entry"] = {"available": True}
    result = st.get_subsystems()
    assert "test_entry" in result
    assert result["test_entry"]["available"] is True


def test_lazy_import_records_error_on_failure():
    """_lazy_import should record error info when import fails."""
    st._SUBSYSTEMS.clear()
    st._lazy_import("nonexistent", "maop.nonexistent.moduleXYZ", "NoClass")
    entry = st._SUBSYSTEMS["nonexistent"]
    assert entry["available"] is False
    assert "error" in entry
    assert entry["class"] is None


def test_lazy_import_records_success():
    """_lazy_import should record class when import succeeds."""
    st._SUBSYSTEMS.clear()
    # Use a real importable module/class
    st._lazy_import("pathlib_cls", "pathlib", "Path")
    entry = st._SUBSYSTEMS["pathlib_cls"]
    assert entry["available"] is True
    assert entry["class"] is Path


# ── cache and cache_lock ──────────────────────────────────────────
def test_cache_is_dict():
    assert isinstance(st.cache, dict)


def test_cache_lock_is_asyncio_lock():
    assert isinstance(st.cache_lock, asyncio.Lock)


def test_cache_usable():
    """cache can store and retrieve (timestamp, value) tuples."""
    st.cache.clear()
    st.cache["k1"] = (time.time(), {"data": 1})
    assert st.cache["k1"][1]["data"] == 1
    st.cache.clear()


@pytest.mark.asyncio
async def test_cache_lock_acquirable():
    """cache_lock can be acquired and released."""
    async with st.cache_lock:
        st.cache["locked"] = (time.time(), "val")
    assert st.cache["locked"][1] == "val"
    st.cache.clear()


# ── active_jobs ───────────────────────────────────────────────────
def test_active_jobs_is_dict():
    assert isinstance(st.active_jobs, dict)


def test_active_jobs_usable():
    st.active_jobs.clear()
    st.active_jobs["job1"] = {"status": "running"}
    assert st.active_jobs["job1"]["status"] == "running"
    st.active_jobs.clear()


# ── start_time ────────────────────────────────────────────────────
def test_start_time_is_float():
    assert isinstance(st.start_time, float)
    assert st.start_time > 0
    assert st.start_time <= time.time()


# ── Config flags ──────────────────────────────────────────────────
def test_tls_enabled_default():
    """tls_enabled reads MAOP_TLS env var."""
    # The value is set at import time; just verify it's a bool
    assert isinstance(st.tls_enabled, bool)


def test_auth_enabled_default():
    assert isinstance(st.auth_enabled, bool)


def test_rl_enabled_default():
    assert isinstance(st.rl_enabled, bool)


def test_config_flags_logic(monkeypatch):
    """Verify the env-var logic by re-evaluating expressions."""
    monkeypatch.setenv("MAOP_TLS", "1")
    assert os.environ.get("MAOP_TLS", "0") == "1"
    monkeypatch.setenv("MAOP_TLS", "0")
    assert os.environ.get("MAOP_TLS", "0") == "0"


# ── Path constants ────────────────────────────────────────────────
def test_maop_root_is_path():
    assert isinstance(st.MAOP_ROOT, Path)
    assert st.MAOP_ROOT.exists()


def test_src_dir_under_maop_root():
    assert st.SRC_DIR == st.MAOP_ROOT / "src"


def test_dash_dir_under_maop_root():
    assert st.DASH_DIR == st.MAOP_ROOT / "dashboard"
