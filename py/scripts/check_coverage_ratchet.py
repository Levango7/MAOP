"""Coverage ratchet — allow gradual improvement, block regression.

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

BASELINE_FILE = pathlib.Path(__file__).resolve().parent / ".cov_baseline.json"
COVERAGE_XML = pathlib.Path(__file__).resolve().parent.parent / "coverage.xml"
FLOOR = 18.0  # absolute minimum, never lower


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
    BASELINE_FILE.write_text(json.dumps({"line_rate": round(rate, 2)}) + "\n")


def main() -> int:
    if not COVERAGE_XML.exists():
        print(f"ERROR: {COVERAGE_XML} not found. Run pytest --cov-report=xml first.")
        return 2

    current = _read_coverage()
    baseline = _read_baseline()
    effective_floor = max(baseline, FLOOR)

    if current < effective_floor:
        print(
            f"FAIL: coverage {current:.2f}% dropped below baseline "
            f"{effective_floor:.2f}%"
        )
        return 1

    if current > baseline:
        _write_baseline(current)
        print(f"GOOD: coverage rose {baseline:.2f}% → {current:.2f}%. Baseline updated.")
    else:
        print(f"OK: coverage {current:.2f}% meets baseline {effective_floor:.2f}%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
