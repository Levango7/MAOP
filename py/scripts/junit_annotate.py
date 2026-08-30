"""Emit GitHub annotations for failures recorded in a pytest junit xml file.

Used by CI steps that already produce --junitxml: on failure the step calls
this script so the failing test IDs are readable via the check-runs API
(without log-download permissions). The script NEVER changes the step's
exit code — callers keep pytest's own exit code.

Usage:
    python scripts/junit_annotate.py test-results.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("::warning::junit_annotate: no junit xml path given")
        return 0
    path = argv[1]
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        print(f"::warning::junit_annotate: cannot parse {path}: {exc}")
        return 0

    fails: list[str] = []
    for case in tree.iter("testcase"):
        for child in case:
            if child.tag in ("failure", "error"):
                name = case.attrib.get("name", "?")
                cls = case.attrib.get("classname", "")
                fails.append(f"{cls}::{name}" if cls else name)

    if not fails:
        print("::notice::junit_annotate: junit xml has no recorded failures")
        return 0

    for name in fails[:20]:
        safe = name.replace("::", "%3A%3A")[:220]
        print(f"::error::test-fail: {safe}")
    print(f"::error::{len(fails)} test failure(s) recorded in junit xml")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
