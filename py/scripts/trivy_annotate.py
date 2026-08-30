"""Emit trivy CRITICAL findings as GitHub ::error:: annotations.

Run by CI's container-scan job when trivy exits non-zero (CRITICAL findings
present): parses the JSON report and prints one annotation per finding so
the CVE IDs / packages are readable via the check-runs API without
downloading the report artifact. Diagnostic only — never changes exit codes.

Usage:
    python scripts/trivy_annotate.py trivy-report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_MAX = 25


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("::warning::trivy_annotate: no report path given")
        return 0
    path = Path(argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::warning::trivy_annotate: cannot read {path}: {exc}")
        return 0

    findings = 0
    for result in data.get("Results", []):
        target = result.get("target", "?")
        for vuln in result.get("Vulnerabilities", []) or []:
            findings += 1
            if findings > _MAX:
                continue
            vid = vuln.get("VulnerabilityID", "?")
            pkg = vuln.get("PkgName", "?")
            installed = vuln.get("InstalledVersion", "?")
            fixed = vuln.get("FixedVersion", "-")
            sev = vuln.get("Severity", "?")
            safe = f"{vid} {sev} {pkg}@{installed} fixed={fixed} ({target})"
            print(f"::error::trivy: {safe[:230]}")

    print(f"::error::trivy: {findings} CRITICAL finding(s) in {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
