"""Local compose smoke: build, up, poll /api/health, diagnose on failure.

Runs INSIDE the compose-smoke CI job (single `python` invocation — no bash
loops/command substitution, which never executed on the runner). Prints
::error:: annotations line-by-line when the stack does not become healthy.

Exit codes: 0 healthy; 1 build/up failure or health timeout.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request

COMPOSE_FILE = "docker-compose.yml"
HEALTH_URL = "http://localhost:9079/api/health"
POLL_TOTAL_S = 120
POLL_INTERVAL_S = 2


def sh(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", check=False,
    )


def err(msg: str) -> None:
    safe = str(msg).replace("::", "%3A%3A")[:230]
    print(f"::error::compose: {safe}")


def main() -> int:
    print("compose up -d --build ...")
    up = sh(["up", "-d", "--build"])
    if up.returncode != 0:
        err(f"compose up failed rc={up.returncode}")
        # ef14bee: progress spam (Volume/Network/Created lines) buried the
        # actual error — show the LAST 40 lines of stderr/stdout and put
        # any error-looking lines FIRST.
        blob = f"{up.stderr or ''}\n{up.stdout or ''}"
        lines = [ln for ln in blob.splitlines() if ln.strip()]
        error_lines = [ln for ln in lines if any(
            k in ln.lower() for k in ("error", "failed", "denied", "exit", "not found", "invalid")
        )]
        for line in error_lines[:15]:
            err(f"E {line}")
        for line in lines[-25:]:
            err(f"T {line}")
        return 1

    deadline = time.time() + POLL_TOTAL_S
    healthy = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                body = resp.read().decode(errors="replace")[:200]
            print(f"healthy: {body}")
            healthy = True
            break
        except Exception:
            time.sleep(POLL_INTERVAL_S)

    if healthy:
        return 0

    err(f"/api/health not healthy after {POLL_TOTAL_S}s")
    ps = sh(["ps"], timeout=60)
    for line in ps.stdout.splitlines()[:12]:
        err(f"PS {line}")
    logs = sh(["logs", "--tail", "30", "dashboard"], timeout=120)
    text = logs.stdout or logs.stderr or ""
    for line in text.splitlines()[-25:]:
        err(f"LOG {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
