"""PRD 1.3 success-metric gate - cold-start time and disk footprint.

Enforces the personal-edition targets declared in
``deliverables/PRD-Dual-Edition-Architecture-202607200729.md`` §1.3:

    - cold start (import maop): < 3 seconds
    - installed disk footprint (maop package): < 50 MB

Run in CI (performance job) AFTER ``pip install -e .``:

    python scripts/check_perf_metrics.py

Exit codes: 0 = pass, 1 = gate violated (CI failure), 2 = measurement error.

Note: keep this file ASCII-only. Windows CI runners use cp1252 stdout by
default; non-ASCII output (e.g. U+2192 arrow) crashed other ratchet scripts
with UnicodeEncodeError. All output must be encodable in cp1252, or stdout
must be reconfigured to UTF-8.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# PRD 1.3 personal-edition targets.
COLD_START_LIMIT_S = 3.0
DISK_LIMIT_MB = 50.0


def _measure_cold_start() -> float:
    """Time a fresh interpreter doing ``import maop`` (seconds)."""
    code = "import maop"
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=120,
        check=False,
    )
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError(
            f"import maop failed (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace')[-500:]}"
        )
    return dt


def _measure_disk_mb() -> float:
    """Total size of the installed ``maop`` package directory (MB)."""
    import importlib.util

    spec = importlib.util.find_spec("maop")
    if spec is None or spec.origin is None:
        raise RuntimeError("cannot locate installed maop package")
    root = pathlib.Path(spec.origin).resolve().parent
    total = sum(
        f.stat().st_size for f in root.rglob("*") if f.is_file()
    )
    return total / (1024.0 * 1024.0)


def main() -> int:
    failures: list[str] = []
    try:
        cold = _measure_cold_start()
    except Exception as exc:
        print(f"[perf-gate] cold-start measurement error: {exc}")
        return 2
    status = "PASS" if cold < COLD_START_LIMIT_S else "FAIL"
    print(
        f"[perf-gate] cold start: {cold * 1000:.0f} ms "
        f"(limit {COLD_START_LIMIT_S:.0f}s) -> {status}"
    )
    if status == "FAIL":
        failures.append("cold start")

    try:
        disk = _measure_disk_mb()
    except Exception as exc:
        print(f"[perf-gate] disk measurement error: {exc}")
        return 2
    status = "PASS" if disk < DISK_LIMIT_MB else "FAIL"
    print(
        f"[perf-gate] disk footprint: {disk:.1f} MB "
        f"(limit {DISK_LIMIT_MB:.0f} MB) -> {status}"
    )
    if status == "FAIL":
        failures.append("disk footprint")

    if failures:
        print(
            "[perf-gate] FAILED: " + ", ".join(failures)
            + " (see PRD 1.3 success metrics)"
        )
        return 1
    print("[perf-gate] all PRD 1.3 success metrics within limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
