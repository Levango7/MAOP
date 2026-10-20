"""Pin the design-token contrast gate to CI and to its pair count.

A step that only exists in a YAML file someone may delete is not a gate. This
test runs the real script (self-test + full 164 pairs) and asserts the CI wiring
still invokes it, matching the coverage-gate guard precedent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND = _ROOT / "dashboard-enterprise"
_CI = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_ci_runs_the_design_contrast_gate():
    assert "Design tokens (WCAG contrast gate)" in _CI
    assert "npm run design:contrast" in _CI


def test_npm_script_checks_both_themes_and_the_pair_floor():
    scripts = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    cmd = scripts["design:contrast"]
    assert "scripts/wcag.py" in cmd
    assert "--tokens src/styles/tokens.css" in cmd
    assert "--tokens src/styles/themes.css" in cmd
    assert "--min-pairs 164" in cmd


def test_self_test_and_full_gate_pass():
    self_test = subprocess.run(
        [sys.executable, "scripts/wcag.py", "--self-test"],
        cwd=_FRONTEND, capture_output=True, text=True, timeout=60,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "OK" in self_test.stdout

    gate = subprocess.run(
        [
            sys.executable, "scripts/wcag.py",
            "--tokens", "src/styles/tokens.css",
            "--tokens", "src/styles/themes.css",
            "--pairs", "design/contrast-pairs.txt",
            "--pairs-only", "--min-pairs", "164",
        ],
        cwd=_FRONTEND, capture_output=True, text=True, timeout=60,
    )
    assert gate.returncode == 0, gate.stdout[-2000:] + gate.stderr[-2000:]
    assert "164 pairs checked, ALL PASS" in gate.stdout
