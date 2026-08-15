"""Stability tests — boundary conditions and abnormal inputs.

Verifies graceful handling of empty databases, extremely large prompts,
malformed JSON, SQL injection, path traversal, concurrent extreme load,
and zero-byte files. All tests are Windows-compatible.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from maop.core.backends.backends import MemoryCacheBackend
from maop.core.backends.db_utils import sqlite_connect

# ════════════════════════════════════════════════════════════════════
# 1. Empty database — operations return empty results, not exceptions
# ════════════════════════════════════════════════════════════════════


def test_empty_db_operations(tmp_path: Path) -> None:
    """Operations on an empty table return empty results without crashing."""
    db_path = tmp_path / "empty.db"
    with sqlite_connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")

        # Query empty table — returns empty list, not exception
        assert conn.execute("SELECT * FROM t").fetchall() == []

        # Delete with no matching rows — no error
        conn.execute("DELETE FROM t WHERE id = 999")

        # Update with no matching rows — no error
        conn.execute("UPDATE t SET name = 'x' WHERE id = 999")

        # Aggregates on empty table
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        assert conn.execute("SELECT MAX(id) FROM t").fetchone()[0] is None

        # Insert then verify
        conn.execute("INSERT INTO t VALUES (1, 'alice')")
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM t WHERE id = 1").fetchone()[0] == "alice"


# ════════════════════════════════════════════════════════════════════
# 2. Extremely long prompt — no 500 error
# ════════════════════════════════════════════════════════════════════


async def test_extremely_long_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 1 MB prompt does not cause a 500 (server error).

    Auth is bypassed and the chat engine is mocked so the test focuses on
    whether the HTTP layer accepts a very large request body without crashing.
    Acceptable outcomes: 200 (processed), 400/413 (rejected by policy),
    422 (validation). A 500 indicates an unhandled exception — a bug.
    """
    from httpx import ASGITransport, AsyncClient

    import maop.dashboard.routers.chat as chat_mod
    from maop.dashboard.server import app

    # Bypass admin auth check
    monkeypatch.setattr(chat_mod, "require_admin", lambda request: None)
    # Mock chat engine to avoid LLM provider dependency
    fake_response = MagicMock()
    fake_response.model_dump.return_value = {"content": "ok"}
    mock_engine = MagicMock()
    mock_engine.chat = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(chat_mod, "_get_engine", lambda: mock_engine)

    huge_message = "x" * (1024 * 1024)  # 1 MB
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chat", json={"message": huge_message})

    assert resp.status_code != 500, (
        f"Server returned 500 for large input (status={resp.status_code})"
    )
    assert resp.status_code in (200, 400, 401, 413, 422), (
        f"Unexpected status for large input: {resp.status_code}"
    )


# ════════════════════════════════════════════════════════════════════
# 3. Malformed JSON — 400/422, not 500
# ════════════════════════════════════════════════════════════════════


async def test_malformed_json_input() -> None:
    """Malformed JSON body returns 400 or 422, never 500."""
    from httpx import ASGITransport, AsyncClient

    from maop.dashboard.server import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/chat",
            content=b"{invalid json,,, missing braces",
            headers={"content-type": "application/json"},
        )

    assert resp.status_code in (400, 401, 422), (
        f"Malformed JSON should return 400/422 (or 401 if auth middleware "
        f"intercepts first), got {resp.status_code}"
    )
    assert resp.status_code != 500


# ════════════════════════════════════════════════════════════════════
# 4. SQL injection — parameterized queries prevent injection
# ════════════════════════════════════════════════════════════════════


def test_sql_injection_attempt(tmp_path: Path) -> None:
    """Parameterized queries neutralize SQL injection strings.

    Injection strings passed as parameter values (not concatenated into SQL)
    are treated as literal data — no table drop, no auth bypass.
    """
    db_path = tmp_path / "sqli.db"
    with sqlite_connect(db_path) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES (?)", ("alice",))

        injections = [
            "'; DROP TABLE users;--",
            "' OR 1=1--",
            "admin'--",
            "'; DELETE FROM users;--",
            "' UNION SELECT * FROM users;--",
        ]
        for inj in injections:
            # Injection string is a parameter value, not SQL text
            rows = conn.execute(
                "SELECT * FROM users WHERE name = ?", (inj,)
            ).fetchall()
            assert len(rows) == 0, f"Injection unexpectedly matched: {inj!r}"

        # Table and original data survive all injection attempts
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 1, f"Data lost after injection: {count} rows"
        name = conn.execute("SELECT name FROM users").fetchone()[0]
        assert name == "alice"


# ════════════════════════════════════════════════════════════════════
# 5. Path traversal — unsafe paths rejected
# ════════════════════════════════════════════════════════════════════


def test_path_traversal_attempt(tmp_path: Path) -> None:
    """Path traversal attempts are detected and rejected.

    A safe resolve helper checks that the resolved path stays within the
    base directory; traversal sequences that escape are refused.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    def safe_resolve(base: Path, user_input: str) -> Path:
        resolved = (base / user_input).resolve()
        base_resolved = base.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            raise ValueError(f"Path traversal detected: {user_input!r}")
        return resolved

    # Windows 风格反斜杠输入仅在 Windows 上是路径分隔符；POSIX 上反斜杠是
    # 合法文件名字符，`..\..\..\...` 不会触发穿越（resolve 视作普通文件名）。
    # 故反斜杠用例仅 Windows 平台保留，避免 CI(Linux/macOS) 误报 DID NOT RAISE。
    traversal_inputs = ["../../../etc/passwd", "../../../../tmp/evil"]
    if os.name == "nt":
        traversal_inputs.append("..\\..\\..\\windows\\system32\\config\\sam")
    for inp in traversal_inputs:
        with pytest.raises(ValueError, match="traversal"):
            safe_resolve(data_dir, inp)

    # Legitimate path within data_dir is allowed
    legit = safe_resolve(data_dir, "subdir/file.txt")
    assert str(legit).startswith(str(data_dir.resolve()))


# ════════════════════════════════════════════════════════════════════
# 6. Concurrent extreme load — no data corruption
# ════════════════════════════════════════════════════════════════════


async def test_concurrent_extreme_load() -> None:
    """100 concurrent writes to the same key do not corrupt data.

    The final value must be one of the written values (last-writer-wins),
    and no exception should be raised during concurrent access.
    """
    backend = MemoryCacheBackend()
    key = "shared-key"
    values = [f"v{i}" for i in range(100)]

    async def write(val: str) -> None:
        await asyncio.to_thread(backend.set, key, val)

    # 100 concurrent writes
    await asyncio.gather(*(write(v) for v in values))

    final = backend.get(key)
    assert final in values, f"Corrupted value after concurrent writes: {final!r}"


# ════════════════════════════════════════════════════════════════════
# 7. Zero-byte database file — graceful handling
# ════════════════════════════════════════════════════════════════════


def test_zero_byte_file(tmp_path: Path) -> None:
    """A 0-byte SQLite file is opened as an empty database without error."""
    db_path = tmp_path / "zero.db"
    db_path.write_bytes(b"")
    assert db_path.stat().st_size == 0

    # SQLite treats a 0-byte file as a valid empty database
    with sqlite_connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")

    # Reopen and verify data persisted
    with sqlite_connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 1, f"Expected 1 row after reopen, got {count}"