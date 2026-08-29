"""Coverage ratchet - allow gradual improvement, block regression.

Note: keep this file ASCII-only. The GOOD branch printed an arrow (U+2192)
which crashed on Windows CI (cp1252 stdout): UnicodeEncodeError. All output
must be encodable in cp1252, or stdout must be reconfigured to UTF-8.

Reads ``coverage.xml`` and enforces that total coverage does not drop
below the stored baseline. If coverage improves, the baseline file is
updated so the bar only moves in one direction (upward).

Run AFTER pytest --cov --cov-report=xml:

    python scripts/check_coverage_ratchet.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import xml.etree.ElementTree as ET

# Windows CI runner 默认 cp1252 stdout，print 非 ASCII（如箭头 →）会抛
# UnicodeEncodeError（曾导致 ratchet 误报失败）。强制 UTF-8 + replace 兜底。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASELINE_FILE = pathlib.Path(__file__).resolve().parent / ".cov_baseline.json"
COVERAGE_XML = pathlib.Path(__file__).resolve().parent.parent / "coverage.xml"
FLOOR = 80.0  # absolute minimum, never lower (2026-08-21: 实测 82%, 从 18 修正)

# Floating-point tail immunity: a measured 81.58999999... must compare equal
# to a stored 81.59, not "below". Any difference within this epsilon counts
# as unchanged; the ratchet only rejects real regressions.
_EPS = 0.005


def _read_coverage() -> float:
    """Parse line-rate from coverage.xml (Cobertura schema)."""
    tree = ET.parse(COVERAGE_XML)
    root = tree.getroot()
    return float(root.attrib["line-rate"]) * 100.0


def _read_baseline() -> float:
    if BASELINE_FILE.exists():
        return float(json.loads(BASELINE_FILE.read_text())["line_rate"])
    return FLOOR


def _write_baseline(rate: float) -> None:
    # Round DOWN (math.floor to 2dp): rounding can write a baseline HIGHER
    # than the measured rate (81.586 -> "81.59"), which makes the next run
    # of the SAME code fail the ratchet (81.586 < 81.59). Floor guarantees
    # the stored bar never exceeds what was actually measured.
    import math
    floored = math.floor(rate * 100) / 100
    BASELINE_FILE.write_text(json.dumps({"line_rate": floored}) + "\n")


def main() -> int:
    if not COVERAGE_XML.exists():
        print(f"ERROR: {COVERAGE_XML} not found. Run pytest --cov-report=xml first.")
        return 2

    current = _read_coverage()
    baseline = _read_baseline()
    effective_floor = max(baseline, FLOOR)

    # Epsilon comparison: within _EPS of the bar counts as "meets", not
    # "dropped". This kills the round()-induced self-inflation failure where
    # a measured 81.589999 was stored as 81.59 and then failed against
    # itself on the next identical run.
    if current < effective_floor - _EPS:
        print(
            f"FAIL: coverage {current:.2f}% dropped below baseline "
            f"{effective_floor:.2f}%"
        )
        return 1

    if current > baseline + _EPS:
        _write_baseline(current)
        # ASCII-only: U+2192 arrow crashed Windows CI (cp1252 cannot encode it).
        print(
            f"GOOD: coverage rose {baseline:.2f}% -> {current:.2f}%. "
            "Baseline updated."
        )
    else:
        # current within _EPS of baseline (equal or marginal): NOT a
        # regression — the ratchet's contract is "never drop below".
        print(f"OK: coverage {current:.2f}% meets baseline {effective_floor:.2f}%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
