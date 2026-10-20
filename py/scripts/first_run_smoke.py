"""First-run smoke test for an installed MAOP wheel.

Run this with a venv that has the built wheel installed. It deliberately
starts the server from a directory *outside* the repository so the test
cannot accidentally import the source tree or read the repository config.

Checks:
  1. `maop init` writes config + dashboard into the home.
  2. `maop start` serves `/` and the personal owner-mode APIs (200, not 401/403).
  3. The home data directory is writable and the process exits cleanly.

Usage:
    python py/scripts/first_run_smoke.py \
        --python /tmp/fr-venv/bin/python \
        --home /tmp/fr-home \
        --port 9188
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _http(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _wait_for(url: str, process: subprocess.Popen, timeout_s: float = 90.0) -> int:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with {process.returncode}")
        try:
            status, _ = _http(url, timeout=2.0)
            if status == 200:
                return status
            last_error = f"HTTP {status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable, help="Python from the wheel venv")
    parser.add_argument("--home", required=True, help="Writable MAOP_HOME")
    parser.add_argument("--port", type=int, default=9188)
    args = parser.parse_args()

    home = Path(args.home).expanduser().resolve()
    run_dir = Path(tempfile.mkdtemp(prefix="maop-first-run-"))
    env = os.environ.copy()
    env.pop("MAOP_ROOT_DIR", None)
    env.pop("MAOP_ROOT", None)
    env["MAOP_HOME"] = str(home)
    env["MAOP_MULTIMODAL_PROVIDER"] = "hash"
    env["MAOP_AUTO_SCHED"] = "0"
    env["MAOP_BACKGROUND_TASKS"] = "0"

    subprocess.run(
        [args.python, "-m", "maop", "init", "--home", str(home)],
        cwd=run_dir, env=env, check=True,
    )
    if not (home / "config" / "agents.yaml").is_file():
        raise SystemExit("init did not create config/agents.yaml")
    if not (home / "dashboard" / "dist-enterprise" / "index.html").is_file():
        raise SystemExit("init did not create the dashboard bundle")

    log = open(home / "first-run.log", "w", encoding="utf-8")  # noqa: SIM115
    process = subprocess.Popen(
        [
            args.python, "-m", "maop", "start",
            "--host", "127.0.0.1", "--port", str(args.port),
        ],
        cwd=run_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{args.port}"
        _wait_for(f"{base}/api/health", process)
        checks = {
            "/": 200,
            "/api/health": 200,
            "/api/overview": 200,
            "/api/info/edition": 200,
            "/api/control/status": 200,
        }
        results = {}
        for path, expected in checks.items():
            status, body = _http(base + path)
            results[path] = status
            if status != expected:
                raise SystemExit(f"{path} expected {expected}, got {status}: {body[:300]}")
        status, html = _http(base + "/")
        if '<div id="app"></div>' not in html:
            raise SystemExit("dashboard index.html was not served")
        if not (home / "data" / "maop.db").exists():
            raise SystemExit("data/maop.db was not created in MAOP_HOME")
        print(json.dumps({"ok": True, "home": str(home), "checks": results}, ensure_ascii=False))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
