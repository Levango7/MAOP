"Global test fixtures — overrides pytest's tmp_path to avoid Windows\nPermissionError on ``C:\\Users\\<user>\\AppData\\Local\\Temp\\pytest-of-<user>``.\n\nThe built-in ``tmp_path`` fixture creates directories under a shared\n``pytest-of-<user>`` base.  On Windows this base can accumulate restrictive\nACLs (e.g. after a run under a different session / elevated prompt), causing\nevery subsequent ``tmp_path`` access to raise ``PermissionError [WinError 5]``.\n\nFix: provide our own ``tmp_path`` that uses ``tempfile.mkdtemp()`` instead.\nEach test gets an isolated temp directory that is cleaned up after the test\nsession ends.\n"

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


# ── Disable sentence_transformers in test environment ──────────────
# Importing sentence_transformers pulls in torch (~30s) and tries to
# download models from HuggingFace Hub. All MAOP code paths catch
# ImportError and fall back to HashEmbedding, so we inject a stub that
# raises ImportError on attribute access.
class _DisabledSentenceTransformers:
    """Stub module that raises ImportError on any attribute access."""

    def __getattr__(self, name: str):
        raise ImportError(
            f"sentence_transformers is disabled in the test environment "
            f"(attribute {name!r} requested)"
        )

    def __dir__(self):
        return []


if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = _DisabledSentenceTransformers()  # type: ignore[assignment]

# Keep track of all created dirs so we can clean up at session end.
_tmp_dirs: list[str] = []


@pytest.fixture
def tmp_path() -> Path:
    """Provide a temporary directory that does **not** rely on pytest's
    ``pytest-of-<user>`` base directory.

    Uses ``tempfile.mkdtemp()`` which creates a fresh directory under the
    system TEMP root with per-process random suffix — no shared base, no
    permission inheritance issues.
    """
    d = tempfile.mkdtemp(prefix="MAOP_test_")
    _tmp_dirs.append(d)
    return Path(d)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set MAOP_DATA_DIR to tmp_path/data so each test gets an isolated DB.

    Also relaxes plugin checksum strictness (MAOP_PLUGIN_STRICT_CHECKSUM=0) so
    that non-checksum-related tests don't need to embed SHA-256 in every
    manifest. Tests that verify the strict default can override this env var
    via ``monkeypatch.delenv`` or ``monkeypatch.setenv``.

    Forces HuggingFace Hub / sentence-transformers into offline mode so that
    ``SentenceTransformer(model)`` fails fast (LocalEntryNotFoundError) instead
    of hanging on a network download attempt — which would exceed the pytest
    per-test timeout.  Code paths catch this and fall back to HashEmbedding.
    """
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAOP_PLUGIN_STRICT_CHECKSUM", "0")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up all temp directories created by our ``tmp_path`` override."""
    for d in _tmp_dirs:
        with contextlib.suppress(Exception):
            shutil.rmtree(d, ignore_errors=True)
    _tmp_dirs.clear()
