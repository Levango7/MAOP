"""Ratchet for the enterprise-overlay collection rule.

``tests/conftest.py`` drops test modules that cannot even be *imported* without
``maop.enterprise``.  That rule used to be a substring search, so any module
that merely mentioned the overlay -- in a comment, a docstring, an
``importorskip`` call, a mock target behind ``skipif`` -- was dropped as well,
and a dropped module is indistinguishable from a passing one in the CI summary.
Measured 2026-10-26 in a simulated clean checkout (overlay blocked in
``sys.meta_path``): 37 files dropped, 17 genuinely unimportable.  The other 20
ran nowhere at all -- ``test_frontend_response_models.py``, the response_model
ratchet, among them.

The cases below pin the structural predicate (``tests/_overlay_rule.py``) and
the two properties that make the gate trustworthy: no file is dropped for text
alone, and the files that must keep running are not dropped.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._overlay_rule import collection_ignores, needs_overlay

TESTS_ROOT = Path(__file__).resolve().parent

#: Gates that have to run in a clean checkout.  A ratchet that is silently
#: skipped reports "clean" forever, which is worse than a red one.
MUST_STAY_COLLECTED = (
    "test_frontend_response_models.py",
    "test_enterprise_response_models.py",
    "test_backends.py",
    "test_edition_switch_guard.py",
    "test_env_example_drift.py",
)

#: Genuinely unimportable without the overlay: module-level import, no guard.
MUST_BE_DROPPED = (
    "test_licenses.py",
    "test_sso.py",
    "test_notifications.py",
    "test_quota_audit.py",
)

OVERLAY = "maop.enterprise"


@pytest.mark.parametrize(
    "source",
    [
        "# maop.enterprise lives in the private MAOS repo\nVALUE = 1\n",
        '"""Docstring mentions maop.enterprise and maop-enterprise."""\n',
        'pytest.importorskip("maop.enterprise")\n',
        "def test_x():\n    from maop.enterprise import sso\n",
        "try:\n    import maop.enterprise\nexcept ImportError:\n    pass\n",
        "def broken(:\n",
    ],
    ids=[
        "comment-only",
        "docstring-only",
        "importorskip",
        "function-level-import",
        "try-guarded-import",
        "syntax-error",
    ],
)
def test_referencing_the_overlay_does_not_drop_the_module(source: str) -> None:
    assert needs_overlay(source) is False


@pytest.mark.parametrize(
    "source",
    [
        "import maop.enterprise\n",
        "from maop.enterprise import sso\n",
        "from maop.enterprise.license import LicenseValidator\n",
        "import maop.enterprise.n8n as n8n\n",
        "import os\nimport maop.enterprise\n",
    ],
    ids=["import", "from-import", "from-submodule", "import-submodule", "after-other-import"],
)
def test_module_level_overlay_import_aborts_collection(source: str) -> None:
    assert needs_overlay(source) is True


def test_the_ignore_set_is_not_vacuous_and_keeps_the_ratchets() -> None:
    ignored = collection_ignores(TESTS_ROOT)
    assert ignored, "no unimportable module found -- did the predicate go blind?"
    for rel in MUST_BE_DROPPED:
        assert rel in ignored, f"{rel} needs the overlay and must be dropped"
    for rel in MUST_STAY_COLLECTED:
        assert rel not in ignored, f"{rel} must run in a clean checkout"


def test_every_dropped_module_really_imports_the_overlay_at_module_level() -> None:
    """Independent re-check: a dropped file must import, not merely mention."""
    for rel in collection_ignores(TESTS_ROOT):
        tree = ast.parse((TESTS_ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Try):
                continue
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert any(name == OVERLAY or name.startswith(OVERLAY + ".") for name in imported), (
            f"{rel} was dropped but has no module-level overlay import"
        )


def test_the_e2e_rule_sees_the_same_empty_set() -> None:
    """e2e/conftest.py shares the predicate; today no e2e module needs dropping."""
    assert collection_ignores(TESTS_ROOT / "e2e") == []
