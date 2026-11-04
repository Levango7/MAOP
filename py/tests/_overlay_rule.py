"""Which test modules cannot even be *imported* without the commercial overlay.

``maop.enterprise`` ships in the private MAOS repository and injects into the
``maop`` namespace (ADR-017), so a clean checkout does not have it. A test
module that imports it at module level aborts collection before any
``pytest.skip`` inside it can run -- and one such module takes the whole pytest
session with it.

The predicate here is structural on purpose. It used to be a substring search
over the file text, which also dropped every module that merely *mentioned* the
overlay: comments explaining the edition split, docstrings, ``importorskip``
calls, mock targets behind ``skipif``. Measured 2026-10-26 in a simulated clean
checkout (``maop.enterprise`` blocked in ``sys.meta_path``): 37 files matched
the text, 17 are genuinely unimportable, and the other 20 collect fine -- they
simply ran nowhere, because the personal-edition CI leg was the only leg that
would have run them. ``tests/test_frontend_response_models.py`` was one of
them, i.e. the response_model ratchet had never executed in CI at all.

Runtime-only dependencies (a lazy ``from maop.enterprise... import`` inside a
test, or a module that pulls the overlay in transitively) are *not* collection
failures. Those tests carry their own ``pytest.importorskip`` /
``skipif(enterprise_available())`` so CI reports an explicit skip instead of
silently dropping the file.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

OVERLAY_ROOT = "maop.enterprise"


def _imports_overlay(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module == OVERLAY_ROOT or module.startswith(OVERLAY_ROOT + ".")
    if isinstance(node, ast.Import):
        return any(
            alias.name == OVERLAY_ROOT or alias.name.startswith(OVERLAY_ROOT + ".")
            for alias in node.names
        )
    return False


def needs_overlay(source: str) -> bool:
    """True when a module-level import of the overlay would abort collection.

    ``try: import maop.enterprise`` does not count -- the module survives the
    ImportError by design. Neither does an import nested in a function: it runs
    after collection and is the test's own business.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Keep the file collectable: a syntax error should surface as a failure
        # with a line number, not vanish from the run.
        return False
    return any(
        _imports_overlay(node) for node in tree.body if not isinstance(node, ast.Try)
    )


def needs_overlay_file(path: Path) -> bool:
    return needs_overlay(Path(path).read_text(encoding="utf-8", errors="ignore"))


def collection_ignores(root: Path) -> list[str]:
    """Test files under ``root`` that pytest cannot collect without the overlay."""
    root = Path(root)
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("test_*.py")
        if needs_overlay_file(path)
    )


def enterprise_available() -> bool:
    """Whether the overlay is importable here.

    ``find_spec`` raises instead of returning None when importing the parent
    package hits the missing submodule; both outcomes mean "absent".
    """
    try:
        return importlib.util.find_spec(OVERLAY_ROOT) is not None
    except (ImportError, ValueError):
        return False
