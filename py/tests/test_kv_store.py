"""Tests for MAOP.core.kv_store — Lightweight SQLite-backed KV storage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from maop.core.backends.kv_store import (
    CASResult,
    KVEntry,
    KVStats,
    KVStore,
)

# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def kv(tmp_path: Path) -> KVStore:
    return KVStore(db_path=tmp_path / "kv.db")


# ── Model tests ───────────────────────────────────────────────────

class TestKVEntry:
    def test_defaults(self):
        e = KVEntry()
        assert e.key == ""
        assert e.value is None
        assert e.namespace == "default"
        assert e.ttl is None
        assert e.version == 1

    def test_with_values(self):
        e = KVEntry(key="k", value="v", namespace="ns", ttl=60, version=3)
        assert e.namespace == "ns"
        assert e.ttl == 60
        assert e.version == 3


class TestKVStats:
    def test_defaults(self):
        s = KVStats()
        assert s.total_keys == 0
        assert s.namespaces == []
        assert s.expired_keys == 0
        assert s.db_size_bytes == 0


class TestCASResult:
    def test_defaults(self):
        r = CASResult()
        assert r.success is False
        assert r.current_value is None
        assert r.current_version == 0


# ── Initialization ────────────────────────────────────────────────

class TestInit:
    def test_creates_db(self, tmp_path: Path):
        db = tmp_path / "kv.db"
        KVStore(db_path=db)
        assert db.exists()

    def test_creates_parent_dir(self, tmp_path: Path):
        db = tmp_path / "nested" / "deep" / "kv.db"
        KVStore(db_path=db)
        assert db.exists()


# ── Get / Set ─────────────────────────────────────────────────────

class TestGetSet:
    def test_set_returns_entry(self, kv: KVStore):
        entry = kv.set("key1", "value1")
        assert isinstance(entry, KVEntry)
        assert entry.key == "key1"
        assert entry.value == "value1"
        assert entry.version == 1

    def test_get_returns_value(self, kv: KVStore):
        kv.set("key1", "value1")
        assert kv.get("key1") == "value1"

    def test_get_missing_returns_default(self, kv: KVStore):
        assert kv.get("nope") is None
        assert kv.get("nope", default="fallback") == "fallback"

    def test_set_overwrite_increments_version(self, kv: KVStore):
        e1 = kv.set("k", "v1")
        e2 = kv.set("k", "v2")
        assert e1.version == 1
        assert e2.version == 2
        assert kv.get("k") == "v2"

    def test_set_complex_value(self, kv: KVStore):
        kv.set("k", {"nested": [1, 2, 3], "bool": True})
        assert kv.get("k") == {"nested": [1, 2, 3], "bool": True}

    def test_set_numeric_value(self, kv: KVStore):
        kv.set("k", 42)
        assert kv.get("k") == 42

    def test_set_none_value(self, kv: KVStore):
        kv.set("k", None)
        assert kv.get("k") is None

    def test_namespaced_get_set(self, kv: KVStore):
        kv.set("k", "default_ns")
        kv.set("k", "custom_ns", namespace="custom")
        assert kv.get("k") == "default_ns"
        assert kv.get("k", namespace="custom") == "custom_ns"


# ── TTL ───────────────────────────────────────────────────────────

class TestTTL:
    def test_ttl_expires(self, kv: KVStore):
        kv.set("k", "v", ttl=0.01)
        time.sleep(0.05)
        assert kv.get("k") is None

    def test_ttl_not_expired(self, kv: KVStore):
        kv.set("k", "v", ttl=100)
        assert kv.get("k") == "v"

    def test_ttl_exists_check(self, kv: KVStore):
        kv.set("k", "v", ttl=0.01)
        time.sleep(0.05)
        assert kv.exists("k") is False

    def test_no_ttl_persists(self, kv: KVStore):
        kv.set("k", "v")
        time.sleep(0.05)
        assert kv.exists("k") is True

    def test_ttl_prune_on_get(self, kv: KVStore):
        kv.set("k1", "v1", ttl=0.01)
        kv.set("k2", "v2", ttl=100)
        time.sleep(0.05)
        # Accessing k2 should prune k1
        assert kv.get("k2") == "v2"
        assert kv.get("k1") is None


# ── Delete / Exists ───────────────────────────────────────────────

class TestDeleteExists:
    def test_delete_existing(self, kv: KVStore):
        kv.set("k", "v")
        assert kv.delete("k") is True
        assert kv.get("k") is None

    def test_delete_nonexistent(self, kv: KVStore):
        assert kv.delete("nope") is False

    def test_exists_true(self, kv: KVStore):
        kv.set("k", "v")
        assert kv.exists("k") is True

    def test_exists_false(self, kv: KVStore):
        assert kv.exists("nope") is False

    def test_exists_namespaced(self, kv: KVStore):
        kv.set("k", "v", namespace="ns1")
        assert kv.exists("k", namespace="ns1") is True
        assert kv.exists("k", namespace="ns2") is False


# ── Bulk operations ───────────────────────────────────────────────

class TestBulkOps:
    def test_get_many(self, kv: KVStore):
        kv.set("a", 1)
        kv.set("b", 2)
        kv.set("c", 3)
        result = kv.get_many(["a", "b", "c"])
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_get_many_partial(self, kv: KVStore):
        kv.set("a", 1)
        kv.set("b", 2)
        result = kv.get_many(["a", "nonexistent"])
        assert result == {"a": 1}

    def test_get_many_empty(self, kv: KVStore):
        assert kv.get_many([]) == {}

    def test_get_many_namespaced(self, kv: KVStore):
        kv.set("a", 1, namespace="ns")
        kv.set("b", 2, namespace="ns")
        result = kv.get_many(["a", "b"], namespace="ns")
        assert result == {"a": 1, "b": 2}

    def test_set_many(self, kv: KVStore):
        count = kv.set_many({"a": 1, "b": 2, "c": 3})
        assert count == 3
        assert kv.get("a") == 1
        assert kv.get("b") == 2
        assert kv.get("c") == 3

    def test_set_many_with_ttl(self, kv: KVStore):
        kv.set_many({"a": 1, "b": 2}, ttl=0.01)
        time.sleep(0.05)
        assert kv.get("a") is None
        assert kv.get("b") is None

    def test_set_many_empty(self, kv: KVStore):
        assert kv.set_many({}) == 0


# ── CAS (Compare-And-Swap) ────────────────────────────────────────

class TestCAS:
    def test_cas_success(self, kv: KVStore):
        kv.set("k", "v1")
        result = kv.cas("k", expected_version=1, new_value="v2")
        assert result.success is True
        assert result.current_value == "v2"
        assert result.current_version == 2
        assert kv.get("k") == "v2"

    def test_cas_version_mismatch(self, kv: KVStore):
        kv.set("k", "v1")
        result = kv.cas("k", expected_version=99, new_value="v2")
        assert result.success is False
        assert result.current_value == "v1"
        assert result.current_version == 1
        # Value should not change
        assert kv.get("k") == "v1"

    def test_cas_key_not_found(self, kv: KVStore):
        result = kv.cas("nonexistent", expected_version=1, new_value="v")
        assert result.success is False
        assert result.current_value is None
        assert result.current_version == 0

    def test_cas_chained(self, kv: KVStore):
        kv.set("k", 0)
        r1 = kv.cas("k", 1, 1)
        assert r1.success
        r2 = kv.cas("k", 2, 2)
        assert r2.success
        assert kv.get("k") == 2

    def test_cas_with_ttl(self, kv: KVStore):
        kv.set("k", "v1")
        result = kv.cas("k", 1, "v2", ttl=0.01)
        assert result.success
        time.sleep(0.05)
        assert kv.get("k") is None


# ── Namespace operations ──────────────────────────────────────────

class TestNamespaces:
    def test_list_keys(self, kv: KVStore):
        kv.set("a", 1)
        kv.set("b", 2)
        kv.set("c", 3)
        keys = kv.list_keys()
        assert set(keys) == {"a", "b", "c"}

    def test_list_keys_with_prefix(self, kv: KVStore):
        kv.set("user:1", "a")
        kv.set("user:2", "b")
        kv.set("post:1", "c")
        keys = kv.list_keys(prefix="user:")
        assert set(keys) == {"user:1", "user:2"}

    def test_list_keys_namespaced(self, kv: KVStore):
        kv.set("a", 1, namespace="ns1")
        kv.set("b", 2, namespace="ns1")
        kv.set("c", 3, namespace="ns2")
        keys = kv.list_keys(namespace="ns1")
        assert set(keys) == {"a", "b"}

    def test_list_namespaces(self, kv: KVStore):
        kv.set("a", 1, namespace="ns1")
        kv.set("b", 2, namespace="ns2")
        kv.set("c", 3, namespace="default")
        ns = kv.list_namespaces()
        assert set(ns) == {"ns1", "ns2", "default"}

    def test_delete_namespace(self, kv: KVStore):
        kv.set("a", 1, namespace="ns1")
        kv.set("b", 2, namespace="ns1")
        kv.set("c", 3, namespace="ns2")
        count = kv.delete_namespace("ns1")
        assert count == 2
        assert kv.list_keys(namespace="ns1") == []
        assert kv.exists("c", namespace="ns2") is True

    def test_delete_namespace_empty(self, kv: KVStore):
        assert kv.delete_namespace("nonexistent") == 0


# ── Stats ─────────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, kv: KVStore):
        stats = kv.stats()
        assert isinstance(stats, KVStats)
        assert stats.total_keys == 0
        assert stats.namespaces == []

    def test_stats_with_data(self, kv: KVStore):
        kv.set("a", 1)
        kv.set("b", 2, namespace="ns")
        stats = kv.stats()
        assert stats.total_keys == 2
        assert set(stats.namespaces) == {"default", "ns"}
        assert stats.db_size_bytes > 0

    def test_stats_after_delete(self, kv: KVStore):
        kv.set("a", 1)
        kv.set("b", 2)
        kv.delete("a")
        stats = kv.stats()
        assert stats.total_keys == 1


# ── Close ─────────────────────────────────────────────────────────

class TestClose:
    def test_close(self, kv: KVStore):
        kv.set("k", "v")
        kv.close()
        assert kv._pool._pool == []

    def test_close_idempotent(self, kv: KVStore):
        kv.close()
        kv.close()  # should not raise
