"""Tests for maop.core.backends — pluggable backend abstraction layer."""

import pytest

from maop.core.backends import (
    LocalSecretBackend,
    MemoryCacheBackend,
    SQLiteKVBackend,
    SQLiteQueueBackend,
    SQLiteStorageBackend,
    get_cache_backend,
    get_kv_backend,
    get_queue_backend,
    get_secret_backend,
    get_storage_backend,
    reset_backends,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_backends()
    yield
    reset_backends()


class TestSQLiteStorageBackend:
    def test_execute_and_fetch(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend = SQLiteStorageBackend(db_path=db)
        backend.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        backend.execute("INSERT INTO t VALUES (?, ?)", (1, "alice"))
        row = backend.fetchone("SELECT * FROM t WHERE id=?", (1,))
        assert row is not None
        assert row["name"] == "alice"

    def test_fetchall(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend = SQLiteStorageBackend(db_path=db)
        backend.execute("CREATE TABLE t (id INTEGER)")
        backend.execute("INSERT INTO t VALUES (1)")
        backend.execute("INSERT INTO t VALUES (2)")
        rows = backend.fetchall("SELECT * FROM t ORDER BY id")
        assert len(rows) == 2

    def test_table_exists(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend = SQLiteStorageBackend(db_path=db)
        assert not backend.table_exists("nope")
        backend.execute("CREATE TABLE nope (id INTEGER)")
        assert backend.table_exists("nope")

    def test_close(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend = SQLiteStorageBackend(db_path=db)
        backend.close()


class TestMemoryCacheBackend:
    def test_set_and_get(self):
        cache = MemoryCacheBackend()
        cache.set("k1", "v1")
        assert cache.get("k1") == "v1"

    def test_get_missing(self):
        cache = MemoryCacheBackend()
        assert cache.get("missing") is None

    def test_delete(self):
        cache = MemoryCacheBackend()
        cache.set("k1", "v1")
        cache.delete("k1")
        assert cache.get("k1") is None

    def test_exists(self):
        cache = MemoryCacheBackend()
        cache.set("k1", "v1")
        assert cache.exists("k1")

    def test_clear(self):
        cache = MemoryCacheBackend()
        cache.set("k1", "v1")
        cache.clear()
        assert cache.get("k1") is None


class TestSQLiteQueueBackend:
    def test_publish_and_consume(self, tmp_path):
        db = str(tmp_path / "q.db")
        queue = SQLiteQueueBackend(db_path=db)
        msg_id = queue.publish("test_topic", {"hello": "world"})
        assert msg_id
        messages = queue.consume("test_topic", limit=10)
        assert len(messages) >= 1

    def test_topic_stats(self, tmp_path):
        db = str(tmp_path / "q.db")
        queue = SQLiteQueueBackend(db_path=db)
        queue.publish("stats_topic", {"x": 1})
        stats = queue.topic_stats("stats_topic")
        assert isinstance(stats, dict)


class TestSQLiteKVBackend:
    def test_set_and_get(self, tmp_path):
        db = str(tmp_path / "kv.db")
        kv = SQLiteKVBackend(db_path=db)
        kv.set("key1", "val1")
        assert kv.get("key1") == "val1"

    def test_get_missing(self, tmp_path):
        db = str(tmp_path / "kv.db")
        kv = SQLiteKVBackend(db_path=db)
        assert kv.get("nope") is None

    def test_delete(self, tmp_path):
        db = str(tmp_path / "kv.db")
        kv = SQLiteKVBackend(db_path=db)
        kv.set("key1", "val1")
        assert kv.delete("key1")

    def test_list_keys(self, tmp_path):
        db = str(tmp_path / "kv.db")
        kv = SQLiteKVBackend(db_path=db)
        kv.set("app:cfg:a", "1")
        kv.set("app:cfg:b", "2")
        kv.set("other:c", "3")
        keys = kv.list_keys(prefix="app:cfg:")
        assert len(keys) == 2

    def test_cas(self, tmp_path):
        db = str(tmp_path / "kv.db")
        kv = SQLiteKVBackend(db_path=db)
        kv.set("cas_key", "old")
        result = kv.cas("cas_key", "old", "new")
        assert isinstance(result, bool)


class TestLocalSecretBackend:
    def test_set_and_get(self, tmp_path):
        vault = LocalSecretBackend(root_dir=str(tmp_path))
        vault.set_secret("sk-test", "abc123")
        val = vault.get_secret("sk-test")
        assert val == "abc123"

    def test_list_secrets(self, tmp_path):
        vault = LocalSecretBackend(root_dir=str(tmp_path))
        vault.set_secret("openai-key", "sk-1")
        vault.set_secret("anthropic-key", "sk-2")
        keys = vault.list_secrets(prefix="openai")
        assert len(keys) >= 1


class TestFactoryFunctions:
    def test_default_storage(self, monkeypatch):
        # Pin to personal edition: on hosts with an enterprise license the
        # edition auto-detects enterprise -> default storage=postgresql,
        # which now fail-fasts when psycopg is missing (fail-closed).
        monkeypatch.setenv("MAOP_EDITION", "personal")
        backend = get_storage_backend()
        assert isinstance(backend, SQLiteStorageBackend)

    def test_default_cache(self):
        backend = get_cache_backend()
        assert isinstance(backend, MemoryCacheBackend)

    def test_default_queue(self):
        backend = get_queue_backend()
        assert isinstance(backend, SQLiteQueueBackend)

    def test_default_kv(self):
        backend = get_kv_backend()
        assert isinstance(backend, SQLiteKVBackend)

    def test_default_secret(self):
        backend = get_secret_backend()
        assert isinstance(backend, LocalSecretBackend)

    def test_fallback_on_missing_pg(self, monkeypatch):
        """Fail-closed: PG requested but unavailable -> RuntimeError unless
        MAOP_STORAGE_ALLOW_FALLBACK=1 explicitly opts in to SQLite degrade."""
        monkeypatch.setenv("MAOP_STORAGE_BACKEND", "postgresql")
        try:
            import psycopg  # noqa: F401
            pytest.skip("psycopg installed; fail-fast path not reachable")
        except ImportError:
            pass
        with pytest.raises(RuntimeError, match="not importable"):
            get_storage_backend()
        # Explicit opt-in restores the legacy degrade behaviour
        reset_backends()
        monkeypatch.setenv("MAOP_STORAGE_ALLOW_FALLBACK", "1")
        backend = get_storage_backend()
        assert isinstance(backend, SQLiteStorageBackend)

    def test_fallback_on_missing_redis(self, monkeypatch):
        monkeypatch.setenv("MAOP_CACHE_BACKEND", "redis")
        backend = get_cache_backend()
        assert isinstance(backend, MemoryCacheBackend)

    def test_fallback_on_missing_rabbitmq(self, monkeypatch):
        monkeypatch.setenv("MAOP_QUEUE_BACKEND", "rabbitmq")
        backend = get_queue_backend()
        assert isinstance(backend, SQLiteQueueBackend)

    def test_fallback_on_missing_vault(self, monkeypatch):
        monkeypatch.setenv("MAOP_SECRET_BACKEND", "vault")
        backend = get_secret_backend()
        assert isinstance(backend, LocalSecretBackend)

    def test_singleton_caching(self):
        b1 = get_cache_backend()
        b2 = get_cache_backend()
        assert b1 is b2

    def test_reset_clears_singletons(self):
        b1 = get_cache_backend()
        reset_backends()
        b2 = get_cache_backend()
        assert b1 is not b2
