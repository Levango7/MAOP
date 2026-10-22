"""Connection hygiene: one PRAGMA truth source, and no leaked handles.

Why this gate exists
--------------------
``with sqlite3.connect(...)`` is a **transaction** context manager — it commits
and rolls back, but never closes. Four call sites in this repo were written as
if it closed the connection, so every periodic health probe and every promote
left a live handle on the DB file (on Windows that is exactly what makes
"delete the corrupt DB and rebuild" fail with WinError 32).

Separately, raw ``sqlite3.connect`` bypasses the project's WAL/busy_timeout
口径 — which is how a module looks fine in single-threaded tests and then
throws ``database is locked`` in production.

Detection is AST-based on purpose: an earlier line-matching version of this
test counted the docstrings that *describe* the old bug as violations.
"""

from __future__ import annotations

import ast
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from maop.core.backends.db_utils import open_connection, sqlite_connect

MAOP = Path(__file__).resolve().parents[1] / "maop"

#: Every remaining raw ``sqlite3.connect`` call, with the reason it stays.
#: A new entry means a caller decided to re-implement the PRAGMAs — either make
#: it explicit here, or route it through ``sqlite_connect``/``open_connection``.
RAW_CONNECT_ALLOWLIST: dict[str, int] = {
    # The implementations themselves (open_connection + ConnectionPool).
    "core/backends/db_utils.py": 2,
    # Own context manager: sets WAL + foreign_keys + busy_timeout, closes in a
    # finally. Duplicates the 口径, but is not unsafe.
    "core/backends/backends.py": 1,
    "prompt_manager.py": 1,
    # Long-lived, cross-thread, autocommit connection owned by the instance.
    "core/config/config_history.py": 1,
    # In-memory DB — there is no file to apply the shared 口径 to.
    "core/agent/router/long_running_task.py": 1,
}


def _is_sqlite_connect(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sqlite3")


def _scan() -> tuple[Counter[str], list[str]]:
    """(per-file raw connect counts, list of leak-idiom locations)."""
    counts: Counter[str] = Counter()
    leaks: list[str] = []
    for path in sorted(MAOP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # utf-8-sig: several modules in this repo carry a UTF-8 BOM (concurrency.py
        # does). Python's own importer tolerates it; a gate that crashed on it
        # would stop covering every file after the first BOM.
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"),
                        filename=str(path))
        rel = path.relative_to(MAOP).as_posix()
        for node in ast.walk(tree):
            if _is_sqlite_connect(node):
                counts[rel] += 1
            if isinstance(node, ast.With):
                for item in node.items:
                    if _is_sqlite_connect(item.context_expr):
                        leaks.append(f"{rel}:{node.lineno}")
    return counts, leaks


class TestNoLeakedHandles:
    def test_leak_idiom_is_gone(self) -> None:
        """`with sqlite3.connect(...)` never closes — zero tolerance."""
        _, leaks = _scan()
        assert not leaks, f"leaked-handle connect sites: {leaks}"

    def test_raw_connects_are_the_allowlisted_ones(self) -> None:
        counts, _ = _scan()
        assert dict(counts) == RAW_CONNECT_ALLOWLIST, (
            f"raw sqlite3.connect sites drifted: got {dict(counts)}"
        )

    def test_allowlist_is_not_stale(self) -> None:
        """Every allowed file must still exist — an entry nobody needs is a lie."""
        for rel in RAW_CONNECT_ALLOWLIST:
            assert (MAOP / rel).exists(), f"allowlisted file is gone: {rel}"


class TestHelperDoesWhatItClaims:
    def test_sqlite_connect_applies_pragmas_and_closes(self, tmp_path: Path) -> None:
        db = tmp_path / "hygiene.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t (v) VALUES ('x')")
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
        # The handle is released: on Windows an open handle makes unlink fail
        # (WinError 32), the exact failure db_utils documents.
        db.unlink()
        assert not db.exists()

    def test_sqlite_connect_rolls_back_on_error(self, tmp_path: Path) -> None:
        db = tmp_path / "rb.db"
        with sqlite_connect(db) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        with pytest.raises(RuntimeError), sqlite_connect(db) as conn:
            conn.execute("INSERT INTO t (id) VALUES (1)")
            raise RuntimeError("boom")
        with sqlite_connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0

    def test_open_connection_leaves_ownership_to_caller(self, tmp_path: Path) -> None:
        db = tmp_path / "own.db"
        conn = open_connection(db)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()
        db.unlink()

    def test_check_same_thread_opt_out(self, tmp_path: Path) -> None:
        """relay_platform hands connections across threads; the helper must say
        so without dropping the PRAGMAs."""
        db = tmp_path / "smt.db"
        conn = open_connection(db, check_same_thread=False)
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
        finally:
            conn.close()

    def test_wal_false_leaves_journal_alone(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        seed = sqlite3.connect(db)
        seed.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        seed.commit()
        seed.close()
        with sqlite_connect(db, wal=False) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"


class TestConvertedCallers:
    def test_relay_platform_uses_the_shared_pragma_口径(self, tmp_path: Path) -> None:
        from maop.core.agent.llm_chat.relay_platform import RelayPlatformManager

        db = tmp_path / "relay.db"
        RelayPlatformManager(db_path=db)
        probe = sqlite3.connect(db)
        try:
            mode = str(probe.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            probe.close()
        assert mode == "wal", "relay_platform 仍在绕过中心 PRAGMA 口径"
        db.unlink()  # WinError 32 on Windows if a handle is still held
