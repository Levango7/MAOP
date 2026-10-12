"""Quality gates from ADR-023 — guards for the three new ratchets.

Covers 復評 disposal (2026-10-12):

* silent-exception gate (``scripts/check_silent_exceptions.py``)
* file-size gate (``scripts/check_file_size.py``)
* CHANGELOG structure check (``check_docs_consistency.check_changelog``)
* CI wiring: the three commands must run in the ``docs-gate`` job
* version-anchor guard for ``config/agents.yaml`` / ``docs/troubleshooting.md``

Each gate gets a mutation self-proof (a synthetic violation must be caught)
plus a real-tree assertion (the shipped baselines must actually hold).
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "py" / "scripts"
CI = REPO / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(SCRIPT_DIR))
import check_docs_consistency as dcs
import check_file_size as cfs
import check_silent_exceptions as cse

# ── silent exception gate ───────────────────────────────────


def _write(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text("from __future__ import annotations\n\n" + body, encoding="utf-8")
    return path


@pytest.fixture()
def scan_root(tmp_path, monkeypatch):
    pkg = tmp_path / "maop"
    (pkg / "sub").mkdir(parents=True)
    monkeypatch.setattr(cse, "SCAN_ROOT", pkg)
    monkeypatch.setattr(cfs, "SCAN_ROOT", pkg)
    monkeypatch.setattr(cse, "SILENT_EXCEPTION_BASELINE", frozenset())
    monkeypatch.setattr(cse, "SUPPRESS_BASELINE", 0)
    return pkg


def test_silent_handler_is_detected(scan_root, tmp_path):
    _write(scan_root, "a.py", "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n")
    violations, notes = cse.find_silent_handlers()
    assert [v["path"] for v in violations] == ["maop/a.py"]
    assert violations[0]["kind"] == "pass"
    assert notes == []


def test_marker_with_valid_category_passes(scan_root):
    _write(scan_root, "b.py", (
        "def f():\n    try:\n        pass\n"
        "    except FileExistsError:  # silent-ok: lock-race — 锁被他人持有\n        pass\n"
    ))
    violations, notes = cse.find_silent_handlers()
    assert violations == [] and notes == []


def test_marker_with_invalid_category_is_flagged(scan_root):
    _write(scan_root, "c.py", (
        "def f():\n    try:\n        pass\n"
        "    except Exception:  # silent-ok: because-i-said-so\n        pass\n"
    ))
    violations, notes = cse.find_silent_handlers()
    assert violations, "invalid category must not legalise a silent handler"
    assert any("invalid marker category" in n for n in notes)


def test_logging_handler_is_not_silent(scan_root):
    _write(scan_root, "d.py", (
        "import logging\nlogger = logging.getLogger(__name__)\n\n"
        "def f():\n    try:\n        pass\n"
        "    except ValueError as exc:\n        logger.warning('x: %s', exc)\n        pass\n"
    ))
    violations, _ = cse.find_silent_handlers()
    assert violations == []


def test_ellipsis_body_is_detected(scan_root):
    _write(scan_root, "e.py", "def f():\n    try:\n        pass\n    except OSError:\n        ...\n")
    violations, _ = cse.find_silent_handlers()
    assert [v["kind"] for v in violations] == ["ellipsis"]


def test_suppress_counting(scan_root):
    _write(scan_root, "f.py", (
        "import contextlib\n\n"
        "def f():\n    with contextlib.suppress(ValueError):\n        pass\n"
    ))
    assert cse.count_suppress_calls() == 1


def test_suppress_ratchet_fails_on_growth(scan_root, capsys):
    _write(scan_root, "g.py", (
        "import contextlib\n\n"
        "def f():\n    with contextlib.suppress(ValueError):\n        pass\n"
    ))
    assert cse.main([]) == 1  # baseline 0, measured 1
    assert "suppress count" in capsys.readouterr().out


def test_baseline_bidirectional(scan_root):
    """A stale baseline entry (no longer silent) must fail the gate."""
    _write(scan_root, "h.py", "def f():\n    try:\n        pass\n    except OSError:\n        pass\n")
    violations, _ = cse.find_silent_handlers()
    key = cse._stable_key(violations[0])
    import types  # noqa: F401

    # simulating: baseline declares it, then it gets fixed → stale entry
    cse.SILENT_EXCEPTION_BASELINE = frozenset({key})  # type: ignore[assignment]
    assert cse.main(["--quiet"]) == 0
    path = scan_root / "h.py"
    path.write_text(
        "def f():\n    try:\n        pass\n    except OSError:  # silent-ok: lock-race — x\n        pass\n",
        encoding="utf-8",
    )
    assert cse.main(["--quiet"]) == 1, "stale baseline entry must fail"


def test_stable_key_survives_line_shift(scan_root):
    """Edits above a baselined handler must not change its baseline key."""
    path = _write(scan_root, "shift.py", (
        "def f():\n    try:\n        pass\n    except OSError:\n        pass\n"
    ))
    before = cse.find_silent_handlers()[0][0]
    path.write_text(
        "from __future__ import annotations\n\n# a new comment\nx = 1\n\n"
        + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    after = cse.find_silent_handlers()[0][0]
    assert cse._stable_key(before) == cse._stable_key(after)
    assert before["line"] != after["line"], "line moved, key did not"


def test_stable_key_unique_for_repeated_handlers(scan_root):
    _write(scan_root, "repeat.py", (
        "def f():\n    try:\n        pass\n    except ImportError:\n        pass\n\n"
        "def g():\n    try:\n        pass\n    except ImportError:\n        pass\n"
    ))
    violations, _ = cse.find_silent_handlers()
    keys = [cse._stable_key(v) for v in violations]
    assert len(keys) == 2 and len(set(keys)) == 2


def test_real_tree_passes_silent_gate():
    """Shipped baseline must match the real tree exactly (both directions)."""
    violations, _notes = cse.find_silent_handlers()
    keys = sorted(cse._stable_key(v) for v in violations)
    assert keys == sorted(cse.SILENT_EXCEPTION_BASELINE)
    assert cse.count_suppress_calls() == cse.SUPPRESS_BASELINE


# ── file size gate ──────────────────────────────────────────


def test_file_size_detects_oversize(scan_root, monkeypatch, capsys):
    big = scan_root / "big.py"
    big.write_text("\n".join(f"x{i} = {i}" for i in range(50)), encoding="utf-8")
    monkeypatch.setattr(cfs, "ALLOWED_OVERSIZE", {})
    oversize, notes = cfs.scan(max_lines=10)
    assert oversize == {"maop/big.py": 50}
    assert notes == []
    assert cfs.main(["--max-lines", "10", "--quiet"]) == 1


def test_file_size_exempt_prefix(scan_root, monkeypatch):
    mig = scan_root / "migrations"
    mig.mkdir()
    (mig / "v1.py").write_text("\n".join("x = 1" for _ in range(50)), encoding="utf-8")
    monkeypatch.setattr(cfs, "ALLOWED_OVERSIZE", {})
    oversize, _ = cfs.scan(max_lines=10)
    assert oversize == {}, "migrations/ must be exempt wholesale"


def test_file_size_bidirectional(scan_root, monkeypatch):
    small = scan_root / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(cfs, "ALLOWED_OVERSIZE", {"maop/small.py": "stale"})
    assert cfs.main(["--max-lines", "10", "--quiet"]) == 1


def test_allowlist_not_both_exempt_and_listed():
    for rel in cfs.ALLOWED_OVERSIZE:
        assert not cfs._is_exempt(rel), f"{rel} is exempt yet allowlisted"


def test_real_tree_passes_file_size_gate():
    assert cfs.main(["--quiet"]) == 0


def test_real_tree_oversize_files_are_all_allowlisted():
    oversize, _ = cfs.scan(cfs.MAX_LINES_DEFAULT)
    assert sorted(oversize) == sorted(cfs.ALLOWED_OVERSIZE)


# ── CHANGELOG structure ────────────────────────────────────


def _changelog(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(body, encoding="utf-8")
    return tmp_path


BASE_EXEMPT = (
    "## [未归属记录] — 2026-08~09（待核实归属）\n\nx\n\n"
    "## [2.x] — 2026-06-01 to 2026-07-09\n\nx\n\n"
    "## [1.x] — 2026-05-01 to 2026-05-31\n\nx\n"
)


def test_changelog_good_structure_passes(tmp_path):
    root = _changelog(tmp_path, (
        "## [Unreleased]\n\nx\n\n## [5.2.1] — 2026-10-09\n\nx\n\n"
        "## [5.2.0] — 2026-09-08\n\nx\n\n"
        + BASE_EXEMPT
    ))
    assert dcs.check_changelog(root) == []


def test_changelog_duplicate_unreleased_is_caught(tmp_path):
    root = _changelog(tmp_path, (
        "## [Unreleased]\n\nx\n\n## [5.0.0] — a\n\nx\n\n## [Unreleased]\n\nx\n\n"
        + BASE_EXEMPT
    ))
    issues = dcs.check_changelog(root)
    assert any("个 [Unreleased] 段" in i for i in issues)


def test_changelog_ascending_versions_are_caught(tmp_path):
    root = _changelog(tmp_path, (
        "## [Unreleased]\n\nx\n\n## [5.2.0] — a\n\nx\n\n## [5.2.1] — b\n\nx\n\n"
        + BASE_EXEMPT
    ))
    issues = dcs.check_changelog(root)
    assert any("顺序非降序" in i for i in issues)


def test_changelog_prerelease_equal_core_is_allowed(tmp_path):
    """5.1.0-personal before 5.1.0 keeps the real release timeline."""
    root = _changelog(tmp_path, (
        "## [Unreleased]\n\nx\n\n## [5.1.0-personal] — 2026-08-27\n\nx\n\n"
        "## [5.1.0] — 2026-08-14\n\nx\n\n" + BASE_EXEMPT
    ))
    assert dcs.check_changelog(root) == []


def test_changelog_bad_semver_is_caught(tmp_path):
    root = _changelog(tmp_path, (
        "## [Unreleased]\n\nx\n\n## [5.2] — a\n\nx\n\n" + BASE_EXEMPT
    ))
    issues = dcs.check_changelog(root)
    assert any("不符合 SemVer" in i for i in issues)


def test_changelog_missing_file_is_skipped(tmp_path):
    assert dcs.check_changelog(tmp_path) == []


def test_changelog_stale_exemption_is_caught(tmp_path):
    root = _changelog(tmp_path, "## [Unreleased]\n\nx\n\n## [5.0.0] — a\n\nx\n")
    issues = dcs.check_changelog(root)
    assert any("豁免段" in i for i in issues)


def test_real_changelog_passes():
    assert dcs.check_changelog(REPO) == []


# ── CI wiring + version anchors ────────────────────────────


def test_new_gates_are_wired_into_ci_docs_gate_job():
    text = CI.read_text(encoding="utf-8")
    assert "scripts/check_docs_consistency.py --gate" in text
    assert "scripts/check_silent_exceptions.py" in text
    assert "scripts/check_file_size.py" in text
    # and they must sit in the docs-gate job (which runs on docs-only PRs too)
    job = text.split("Docs consistency gate", 1)[1].split("\n  # ── Test", 1)[0]
    assert "check_silent_exceptions.py" in job
    assert "check_file_size.py" in job


@pytest.mark.parametrize("rel", ["config/agents.yaml", "docs/troubleshooting.md"])
def test_no_stale_maop_version_anchor(rel):
    """A literal "MAOP v4.3"-style锚点 must not come back (third-party CLI
    versions like "Cline v3.0.46" are unaffected by the MAOP-specific pattern)."""
    text = (REPO / rel).read_text(encoding="utf-8")
    offenders = [
        line for line in text.splitlines()
        if re.search(r"MAOP\s+v\d+(\.\d+)*", line)
    ]
    assert not offenders, f"stale MAOP version anchor in {rel}: {offenders}"
