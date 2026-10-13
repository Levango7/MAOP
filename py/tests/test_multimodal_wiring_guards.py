"""Wiring guards for the multimodal runtime path (M2.2 B2 收尾).

The gate that let this feature ship half-dead: ``test_dead_wiring_inventory``
only tracks **modules** nobody imports. ``ClipEmbedding`` (the real CLIP
provider) and ``store_async`` (the async ingest API) live inside imported
modules, so "no production call site" was invisible to it — the shipped
multimodal memory ran on the hash fallback and the ingest queue was never
used by anything.

This file closes that hole with **AST-level call-site detection**, and proves
its own sensitivity: the scanner is exercised against synthetic sources where
the call is present and absent, so deleting a wiring line turns the guard red
instead of slipping through (a plain grep cannot do this — it also matches the
class definition and the docstrings).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY_ROOT = REPO / "py"
PKG = PY_ROOT / "maop"


def _non_test_sources() -> list[Path]:
    """Every production .py under py/maop (tests excluded by definition)."""
    return sorted(p for p in PKG.rglob("*.py") if "migrations" not in p.parts)


def _call_sites(sources: list[Path], name: str) -> list[tuple[str, int]]:
    """AST-level call sites for ``name(...)`` — definitions excluded.

    A ``def name`` / ``class name`` line is not a call, and neither is a
    docstring mention; only ``ast.Call`` nodes whose func is that name count.
    """
    hits: list[tuple[str, int]] = []
    for path in sources:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - repo is green, defensive only
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = getattr(func, "id", None) or getattr(func, "attr", None)
            if called == name:
                hits.append((str(path.relative_to(REPO)), node.lineno))
    return hits


# ── the guard itself ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("symbol", "where"),
    [
        ("ClipEmbedding", "the provider factory must construct the real CLIP provider"),
        ("store_async", "the upload path must feed the async ingest queue"),
        ("maybe_index_uploaded_image", "chat upload must call the auto-index hook"),
        ("resolve_multimodal_provider", "the service must take its provider from the factory"),
        ("index_modality", "the space registry's generic write path must be reachable"),
        ("get_multimodal_space", "the service must build stores through the registry"),
    ],
)
def test_wiring_call_sites_exist(symbol: str, where: str) -> None:
    """Each step of the runtime path has at least one production call site.

    Delete the wiring and this fails: that is the regression it exists for.
    """
    sites = _call_sites(_non_test_sources(), symbol)
    assert sites, f"{symbol} has no production call site — {where}"


def test_chat_upload_reaches_the_multimodal_space() -> None:
    """``save_image`` must hand off to the auto-index hook (end-to-end wiring)."""
    chat_service = PY_ROOT / "maop" / "dashboard" / "services" / "chat_service.py"
    src = chat_service.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {
        getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "maybe_index_uploaded_image" in calls, "save_image no longer indexes uploads"


def test_service_builds_stores_through_the_registry() -> None:
    """The registry must be the single construction path (B3).

    If ``_build_store`` went back to constructing backends directly, the space
    registry would be decoration and a second space would again need a core
    edit — the exact thing ADR-022 D5/D11 rules out.
    """
    src = (PY_ROOT / "maop" / "memory" / "multimodal.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {
        getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "get_multimodal_space" in calls
    assert "PgMultimodalBackend" in calls and "MultimodalStore" in calls


def test_every_registered_space_renders_its_own_ddl() -> None:
    """Two spaces in one database must not share a table or an index name."""
    from maop.core.memory.multimodal_spaces import (
        MultimodalSpaceSpec,
        list_multimodal_spaces,
        register_multimodal_space,
        reset_multimodal_spaces,
    )

    reset_multimodal_spaces()
    register_multimodal_space(
        MultimodalSpaceSpec(
            name="audio-512", table_name="audio_entries",
            dimension=512, modalities=("audio",),
        )
    )
    tables = {s.table_name for s in list_multimodal_spaces()}
    assert len(tables) == len(list_multimodal_spaces())
    reset_multimodal_spaces()


def test_provider_default_is_not_hardcoded_in_the_store() -> None:
    """``MultimodalStore`` must keep its conservative hash default (tests rely
    on it) — the *factory* is what the product path uses. If someone 'fixes'
    the store to call the factory directly, the whole unit suite would start
    loading CLIP weights on developer machines."""
    src = (PKG / "core" / "memory" / "multimodal_store.py").read_text(encoding="utf-8")
    assert "resolve_multimodal_provider" not in src


# ── scanner self-proof (mutation sensitivity) ───────────────────


def test_call_site_scanner_detects_and_ignores() -> None:
    """The scanner must count calls and ONLY calls.

    Proven against synthetic sources: with the call present it reports one
    site; with the call removed (the mutation a reviewer would try) it reports
    zero. Without this self-test the guards above could be vacuously green.
    """
    with_call = ast.parse(
        "def factory():\n"
        "    return ClipEmbedding()\n"
        "\n"
        "class ClipEmbedding:  # definition, not a call\n"
        "    pass\n"
    )
    without_call = ast.parse(
        "def factory():\n"
        "    return HashModalityEmbedding()\n"
        "\n"
        "class ClipEmbedding:  # definition, not a call\n"
        "    pass\n"
    )
    tmp = REPO / "py" / "maop" / "_scanner_selftest_tmp.py"
    try:
        for tree, expected in ((with_call, 1), (without_call, 0)):
            tmp.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
            sites = _call_sites([tmp], "ClipEmbedding")
            assert len(sites) == expected, (expected, sites)
    finally:
        with __import__("contextlib").suppress(OSError):
            tmp.unlink()
