"""Tests for maop.memory.shared_db — DB path unification + layer name mapping + legacy migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path


from maop.memory.shared_db import (
    LAYER_ALIASES,
    MEMORY_DB_NAME,
    denormalize_layer_name,
    get_memory_db_path,
    migrate_legacy_episodic_db,
    normalize_layer_name,
)


# ═══════════════════════════════════════════════════════════════════════
# Layer name normalization
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeLayerName:
    def test_known_aliases(self):
        assert normalize_layer_name("episodic") == "short_term"
        assert normalize_layer_name("semantic") == "long_term"
        assert normalize_layer_name("working") == "working"
        assert normalize_layer_name("short_term") == "short_term"
        assert normalize_layer_name("long_term") == "long_term"

    def test_case_insensitive(self):
        assert normalize_layer_name("Episodic") == "short_term"
        assert normalize_layer_name("SEMANTIC") == "long_term"
        assert normalize_layer_name("Working") == "working"
        assert normalize_layer_name("Short_Term") == "short_term"

    def test_unknown_passthrough_lowercased(self):
        # Unknown names fall through, lowercased.
        assert normalize_layer_name("custom") == "custom"
        assert normalize_layer_name("FooBar") == "foobar"


class TestDenormalizeLayerName:
    def test_known_mapping(self):
        assert denormalize_layer_name("working") == "working"
        assert denormalize_layer_name("short_term") == "episodic"
        assert denormalize_layer_name("long_term") == "semantic"

    def test_case_insensitive(self):
        assert denormalize_layer_name("Short_Term") == "episodic"
        assert denormalize_layer_name("LONG_TERM") == "semantic"

    def test_unknown_passthrough_lowercased(self):
        assert denormalize_layer_name("custom") == "custom"
        assert denormalize_layer_name("FooBar") == "foobar"


class TestLayerAliasesConstant:
    def test_memory_db_name(self):
        assert MEMORY_DB_NAME == "memory"

    def test_aliases_cover_canonical_layers(self):
        for canonical in ("working", "short_term", "long_term"):
            assert canonical in LAYER_ALIASES.values()


# ═══════════════════════════════════════════════════════════════════════
# Memory DB path
# ═══════════════════════════════════════════════════════════════════════

class TestMemoryDbPath:
    def test_returns_path_instance(self):
        p = get_memory_db_path()
        assert isinstance(p, Path)


# ═══════════════════════════════════════════════════════════════════════
# Legacy episodic DB migration
# ═══════════════════════════════════════════════════════════════════════

def _make_legacy_db(root: Path, *, with_table: bool = True, rows: int = 0) -> Path:
    """Create a legacy <root>/data/episodic.db with optional schema + rows."""
    legacy = root / "data" / "episodic.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(legacy) as conn:
        if with_table:
            conn.execute(
                "CREATE TABLE episodic_memory (id TEXT PRIMARY KEY, content TEXT, layer TEXT)"
            )
            for i in range(rows):
                conn.execute(
                    "INSERT INTO episodic_memory VALUES (?, ?, ?)",
                    (f"id{i}", f"content{i}", "episodic"),
                )
            conn.commit()
        else:
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
    return legacy


def _make_new_db(path: Path) -> None:
    """Create the target DB with an empty episodic_memory table."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE episodic_memory (id TEXT PRIMARY KEY, content TEXT, layer TEXT)"
        )
        conn.commit()


class TestMigrateLegacyEpisodicDb:
    def test_no_legacy_file_returns_zero(self, tmp_path: Path):
        assert migrate_legacy_episodic_db(tmp_path) == 0

    def test_same_path_returns_zero(self, tmp_path: Path, monkeypatch):
        # Legacy path resolves to the same file as the new path → no-op.
        legacy = _make_legacy_db(tmp_path, rows=1)
        monkeypatch.setattr("maop.memory.shared_db.get_memory_db_path", lambda: legacy)
        assert migrate_legacy_episodic_db(tmp_path) == 0

    def test_legacy_without_table_returns_zero(self, tmp_path: Path, monkeypatch):
        _make_legacy_db(tmp_path, with_table=False)
        new_db = tmp_path / "new.db"
        monkeypatch.setattr("maop.memory.shared_db.get_memory_db_path", lambda: new_db)
        assert migrate_legacy_episodic_db(tmp_path) == 0

    def test_empty_table_returns_zero(self, tmp_path: Path, monkeypatch):
        _make_legacy_db(tmp_path, rows=0)
        new_db = tmp_path / "new.db"
        monkeypatch.setattr("maop.memory.shared_db.get_memory_db_path", lambda: new_db)
        assert migrate_legacy_episodic_db(tmp_path) == 0

    def test_migrates_rows(self, tmp_path: Path, monkeypatch):
        _make_legacy_db(tmp_path, rows=2)
        new_db = tmp_path / "new.db"
        _make_new_db(new_db)
        monkeypatch.setattr("maop.memory.shared_db.get_memory_db_path", lambda: new_db)

        migrated = migrate_legacy_episodic_db(tmp_path)
        assert migrated == 2

        with sqlite3.connect(new_db) as conn:
            ids = conn.execute("SELECT id FROM episodic_memory ORDER BY id").fetchall()
        assert ids == [("id0",), ("id1",)]

    def test_idempotent_second_run_migrates_zero(self, tmp_path: Path, monkeypatch):
        _make_legacy_db(tmp_path, rows=1)
        new_db = tmp_path / "new.db"
        _make_new_db(new_db)
        monkeypatch.setattr("maop.memory.shared_db.get_memory_db_path", lambda: new_db)

        first = migrate_legacy_episodic_db(tmp_path)
        second = migrate_legacy_episodic_db(tmp_path)
        assert first == 1
        # INSERT OR IGNORE → duplicate keys skipped on second run.
        assert second == 0