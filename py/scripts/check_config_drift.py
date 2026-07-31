"""Config drift audit — ensures os.getenv count doesn't grow.

Runs in CI to prevent new os.getenv/os.environ calls from being added
without going through the settings module. The baseline is the current
count; any increase fails the check.

Usage: python scripts/check_config_drift.py
"""

from __future__ import annotations

import pathlib
import sys

BASELINE = 148  # Current count as of 2026-08-01; only decrease is allowed.


def count_getenv_calls(root: pathlib.Path) -> int:
    """Count os.getenv/os.environ calls in non-test Python files."""
    count = 0
    for f in root.rglob("*.py"):
        if "test" in f.name or "test" in str(f.parent):
            continue
        if f.name == "settings.py":
            continue  # settings.py is the canonical config entry point
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "os.getenv" in line or "os.environ" in line:
                    count += 1
        except Exception:
            pass
    return count


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent / "maop"
    current = count_getenv_calls(root)
    print(f"os.getenv/os.environ calls (excluding tests + settings): {current}")
    print(f"Baseline: {BASELINE}")
    if current > BASELINE:
        print(f"FAIL: +{current - BASELINE} new os.getenv calls detected.")
        print("Use maop.config.settings.get_settings() instead of os.getenv().")
        return 1
    if current < BASELINE:
        print(f"GOOD: -{BASELINE - current} calls removed since baseline. Update BASELINE.")
    else:
        print("OK: no drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())