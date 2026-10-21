"""Silent exception gate — 吞异常必须有理由（ADR-023）。

Scans ``py/maop/**/*.py`` for ``except`` handlers whose body silently swallows
the exception (first statement is ``pass`` / ``continue`` / ``...``) without
either a handler/logging call or an explicit justification marker::

    except FileExistsError:  # silent-ok: lock-race — 锁已被其他进程持有
        pass

Why AST + tokenize (not regex): the previous sweep script matched only
``except Exception`` textually, so narrow-type handlers (``except
ValueError:``) and multi-line layouts escaped — and comments are invisible to
AST, so the marker check needs ``tokenize``.

Two ratchets, both bidirectional (a stale entry fails the gate just like a
new violation fails it, so the baseline can only shrink):

* silent handlers not in ``SILENT_EXCEPTION_BASELINE`` → fail
* ``contextlib.suppress(...)`` count above ``SUPPRESS_BASELINE`` → fail
  (``suppress(Exception)`` is exactly "swallow without a trace")

Usage::

    python scripts/check_silent_exceptions.py            # gate (CI)
    python scripts/check_silent_exceptions.py --print-baseline
    python scripts/check_silent_exceptions.py --quiet    # only exit code
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import tokenize
from collections.abc import Iterator

REPO_PY_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_ROOT = REPO_PY_ROOT / "maop"

SILENT_NODES = (ast.Pass, ast.Continue, ast.Expr)  # Expr filtered to Ellipsis below

#: Closed set of justification categories for ``# silent-ok:`` markers.
MARKER_CATEGORIES: frozenset[str] = frozenset({
    "optional-dep",      # optional dependency probe (ImportError only)
    "lock-race",         # expected filesystem lock/cleanup races
    "ws-disconnect",     # client disconnect / socket teardown
    "interrupt",         # KeyboardInterrupt / task cancellation
    "loop-teardown",     # no running loop / loop already closed
    "defensive-probe",   # best-effort probe where failure is the signal
    "scan-continue",     # expected data-shape miss while scanning records
    "platform-limitation",  # OS/FS does not support the call (Windows chmod, fsync)
    "best-effort-cleanup",  # cleanup may fail; the original error is re-raised/logged
    "expected-signal",   # timeout / absence IS the signal the loop waits for
})

MARKER_PREFIX = "silent-ok:"

#: Baseline: silent handlers that predate the gate. Format "path:line:kind".
#: Regenerate with ``--print-baseline``; entries may only be REMOVED.
SILENT_EXCEPTION_BASELINE: frozenset[str] = frozenset({
})

#: Ratchet for ``contextlib.suppress(...)`` (same swallowing semantics).
#: 2026-10-21: 47 → 46 —— dashboard/server.py 关停期的两处
#: ``with suppress(asyncio.CancelledError)`` 合并成 ``drain_cancelled()``
#: 一个带理由标记的处理器（顺带让这条关停路径变得可测）。
SUPPRESS_BASELINE = 46


def _iter_py_files() -> Iterator[pathlib.Path]:
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: pathlib.Path) -> str:
    for base in (REPO_PY_ROOT, SCAN_ROOT.parent):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _is_ellipsis(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    )


def _is_silent_first_stmt(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Pass, ast.Continue)):
        return True
    return _is_ellipsis(node)


def _body_handles_exception(handler: ast.ExceptHandler) -> bool:
    """True when the handler body does anything observable about the error."""
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                    base = func.value
                    if isinstance(base, ast.Name) and base.id in ("logger", "log", "logging"):
                        return True
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in ("print",) or "log" in name.lower() or "warn" in name.lower():
                    return True
    return False


def _comments_by_line(path: pathlib.Path) -> dict[int, str]:
    """Comment text keyed by the line it appears on (BOM-safe)."""
    out: dict[int, str] = {}
    try:
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.COMMENT:
                    out.setdefault(tok.start[0], tok.string)
    except (tokenize.TokenError, SyntaxError, OSError):
        pass
    return out


def _marker_on(handler: ast.ExceptHandler, comments: dict[int, str]) -> str | None:
    """Return the marker category when a valid ``# silent-ok:`` is present.

    Accepted positions: the ``except`` header line, or the first body
    statement's line (so ``pass  # silent-ok: ...`` also counts).
    """
    lines = [handler.lineno]
    if handler.body:
        lines.append(handler.body[0].lineno)
    for line in lines:
        comment = comments.get(line, "")
        idx = comment.find(MARKER_PREFIX)
        if idx == -1:
            continue
        rest = comment[idx + len(MARKER_PREFIX):].strip()
        category = rest.split()[0].split("—")[0].rstrip(":-").strip() if rest else ""
        # accept "cat — reason", "cat - reason", "cat"
        category = category.split()[0].strip(",-—:") if category else ""
        if category in MARKER_CATEGORIES:
            return category
    return None


def find_silent_handlers() -> tuple[list[dict[str, object]], list[str]]:
    """Return (violations, notes). Each violation: path/line/kind/category."""
    violations: list[dict[str, object]] = []
    notes: list[str] = []
    bad_markers: list[str] = []
    for path in _iter_py_files():
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(f"unreadable: {_rel(path)} ({exc})")
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            notes.append(f"syntax error: {_rel(path)}:{exc.lineno}")
            continue
        comments = _comments_by_line(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not node.body:
                continue
            first = node.body[0]
            if not _is_silent_first_stmt(first):
                continue
            if _body_handles_exception(node):
                continue
            marker = _marker_on(node, comments)
            kind = type(first).__name__.lower()
            if isinstance(first, ast.Expr):
                kind = "ellipsis"
            if marker is None:
                # distinguish "no marker at all" from "marker with bad category"
                for line in (node.lineno, first.lineno):
                    comment = comments.get(line, "")
                    if MARKER_PREFIX in comment:
                        bad_markers.append(
                            f"{_rel(path)}:{line}: {comment.strip()}"
                        )
                        break
                violations.append({
                    "path": _rel(path),
                    "line": node.lineno,
                    "kind": kind,
                    "exc": _exc_type_text(node),
                })
    for bad in bad_markers:
        notes.append(f"invalid marker category: {bad}")

    # Assign a per-(path, exc, kind) occurrence index in source order so the
    # stable key is unique even when identical handlers repeat in one file
    # (ast.walk is BFS; sort by line first). Indices depend only on the
    # relative order of identical handlers, so edits elsewhere never shift them.
    counts: dict[tuple[str, str, str], int] = {}
    for entry in sorted(violations, key=lambda v: (str(v["path"]), int(v["line"]))):
        group = (str(entry["path"]), str(entry["exc"]), str(entry["kind"]))
        counts[group] = counts.get(group, 0) + 1
        entry["seq"] = counts[group]
    return violations, notes


def _exc_type_text(handler: ast.ExceptHandler) -> str:
    """Normalised exception-type text of the handler (for the stable key)."""
    if handler.type is None:
        return "bare"
    try:
        return ast.unparse(handler.type).replace(" ", "")
    except Exception:  # pragma: no cover - unparse is stable in supported versions
        return "?"


def _stable_key(entry: dict[str, object]) -> str:
    """Baseline key that survives line shifts above the handler.

    Line numbers change whenever anyone edits a file above a baselined
    handler — keying the baseline on them would red the gate on unrelated
    edits (the 假红 class this repo keeps hitting). The key is therefore
    ``path::exception-type::kind::#seq`` where ``seq`` is the occurrence
    index of that identical handler in source order (unique even when a file
    repeats the same handler twice). ``line`` is kept only for messages.
    """
    return (
        f"{entry['path']}::{entry.get('exc', '?')}::{entry['kind']}::#{entry.get('seq', 1)}"
    )


def count_suppress_calls() -> int:
    total = 0
    for path in _iter_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name == "suppress":
                    total += 1
    return total


def _key(entry: dict[str, object]) -> str:
    return f"{entry['path']}:{entry['line']}:{entry['kind']}"


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    violations, notes = find_silent_handlers()
    keys = sorted(_stable_key(v) for v in violations)
    by_key = {_stable_key(v): f"{v['path']}:{v['line']}" for v in violations}
    suppress = count_suppress_calls()

    if "--print-baseline" in argv:
        print(json.dumps(
            {"silent_exceptions": keys, "suppress_calls": suppress},
            ensure_ascii=False, indent=2,
        ))
        return 0

    failed = False
    for note in notes:
        print(f"WARN: {note}")
        # invalid marker categories are hard failures (abuse guard)
        if note.startswith("invalid marker category"):
            failed = True

    new = sorted(set(keys) - set(SILENT_EXCEPTION_BASELINE))
    stale = sorted(set(SILENT_EXCEPTION_BASELINE) - set(keys))

    if new:
        failed = True
        print(f"FAIL: {len(new)} silent except handler(s) without justification:")
        for item in new:
            print(f"  {by_key.get(item, item)}  [key: {item}]")
        print(
            "  处置：真错误路径按 ADR-018 加 logger.warning；设计性静默加理由标记：\n"
            "    except <T>:  # silent-ok: <category> — <reason>\n"
            f"  （category 闭集：{'/'.join(sorted(MARKER_CATEGORIES))}）"
        )
    if stale:
        failed = True
        print(
            f"FAIL: {len(stale)} baseline entr(ies) no longer silent / removed — "
            "baseline may only shrink:"
        )
        for item in stale:
            print(f"  {item}")
    if suppress > SUPPRESS_BASELINE:
        failed = True
        print(
            f"FAIL: contextlib.suppress count {suppress} > baseline {SUPPRESS_BASELINE} "
            "(suppress(Exception) hides failures exactly like a bare except)"
        )
    elif suppress < SUPPRESS_BASELINE:
        failed = True
        print(
            f"FAIL: contextlib.suppress count {suppress} < baseline {SUPPRESS_BASELINE} — "
            "tighten SUPPRESS_BASELINE in scripts/check_silent_exceptions.py"
        )

    if not quiet:
        print(
            f"silent handlers: {len(keys)} (baseline {len(SILENT_EXCEPTION_BASELINE)}), "
            f"suppress calls: {suppress} (baseline {SUPPRESS_BASELINE})"
        )
    if failed:
        return 1
    print("OK: no un-justified silent exception handling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
