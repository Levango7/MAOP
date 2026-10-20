"""Ratchet: nothing new may read the writerless ``delegations`` table.

``insert_delegation`` is only ever called from tests, so every SQL read against
that table returns zeros and the dashboard says "0 tasks" after a real run.
M1 replaced those readers with the ``run_events`` spine; what is left is
registered below and may only shrink.

Counting walks the AST and skips docstrings, so prose that merely *mentions*
the table neither raises nor hides a hit.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MAOP_PKG = Path(__file__).resolve().parents[1] / "maop"
_SQL_PHRASE = re.compile(r"(?:FROM|INTO|UPDATE|JOIN)\s+delegations\b", re.IGNORECASE)

#: Each entry is a reader that still points at the empty table, with the reason
#: it is not converted yet. Deleting an entry is the point of this file.
LEGACY_READERS: dict[str, int] = {
    "core/backends/data.py": 2,  # the table's own writer + reader, tests-only callers
    "dashboard/channels/prompts.py": 1,  # per-agent counts in the prompt feed
    "dashboard/provider.py": 6,  # Overview KPI totals — exported, but never instantiated
    "evolve.py": 1,  # evolution sampling, falls back to logs/delegations.json
    "history_analyzer.py": 1,  # failure clustering
}
BASELINE_TOTAL = 11  # 13 → 11 when failures() and timeseries() moved to run_events


def _docstring_like(tree: ast.AST) -> set[int]:
    """Constant nodes that are docstrings or bare statement strings."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for stmt in body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                skip.add(id(stmt.value))
    return skip


def _sql_hits(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    skip = _docstring_like(tree)
    counts: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ):
            counts.append(len(_SQL_PHRASE.findall(node.value)))
    return [c for c in counts if c]


def _measured() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(_MAOP_PKG.rglob("*.py")):
        hits = _sql_hits(path)
        if hits:
            found[path.relative_to(_MAOP_PKG).as_posix()] = sum(hits)
    return found


def test_delegations_readers_are_registered() -> None:
    """An unregistered reader of the empty table is a silent-zero bug."""
    found = _measured()
    unregistered = sorted(set(found) - set(LEGACY_READERS))
    assert unregistered == [], f"new ``delegations`` readers: {unregistered}"
    assert sum(found.values()) <= BASELINE_TOTAL, (
        f"delegations reads grew to {sum(found.values())} (baseline {BASELINE_TOTAL}); "
        "read run_events instead"
    )


def test_ledger_does_not_presume_fixes() -> None:
    """Every registered exemption must still exist, so the ledger stays honest."""
    found = _measured()
    stale = sorted(f for f in LEGACY_READERS if found.get(f, 0) != LEGACY_READERS[f])
    assert stale == [], (
        f"ledger drifted from reality for {stale}: found={found}, "
        f"ledger={LEGACY_READERS}"
    )


def test_gate_flags_a_new_reader(tmp_path: Path) -> None:
    """Self-test: the detector is alive and reacts to a real read."""
    src = tmp_path / "maop" / "gate_probe.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        '"""Probe with a docstring mention only: FROM delegations should not count."""\n'
        "def fetch():\n"
        '    return db.query("SELECT COUNT(*) FROM delegations")\n',
        encoding="utf-8",
    )
    assert _sql_hits(src) == [1]


def test_gate_counts_the_real_tree() -> None:
    """Non-empty-run proof: the scan finds the known debt, not zero files."""
    assert sum(_measured().values()) == BASELINE_TOTAL
