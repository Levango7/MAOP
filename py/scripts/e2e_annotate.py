"""Extract pytest failure lines from an e2e log and emit GitHub annotations.

Used by the CI "Run e2e tests" step: when pytest exits non-zero we want the
failing test names / assertion lines visible in the job's annotations, so
they can be read via the check-runs API without log-download permissions
(the bash grep/sed pipelines produced 0 annotations on Windows runners due
to encoding/locale differences; a single cross-platform python script
avoids the whole class of problems).

Usage:
    python scripts/e2e_annotate.py /tmp/e2e.log          # bash /tmp path
    python scripts/e2e_annotate.py e2e.log                 # relative fallback

Emits up to 20 ``::error::`` workflow commands, one per matched line.
``::`` and ``%`` are percent-escaped so pytest output cannot inject
workflow-command syntax. ANSI color sequences are stripped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match failure-relevant lines in pytest output.
_MATCH = re.compile(r"FAILED|Error|assert|raise|Traceback", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_MAX_LINES = 20
_MAX_LEN = 220


def main(argv: list[str]) -> int:
    candidates: list[str] = list(argv[1:])
    # Always consider the two CI-canonical locations as fallbacks.
    candidates.append("/tmp/e2e.log")
    candidates.append("e2e.log")

    lines: list[str] = []
    for cand in candidates:
        p = Path(cand)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        break

    if not lines:
        print("::warning::e2e annotate: no log file found to extract failures from")
        return 0

    hits: list[str] = []
    for raw in lines:
        clean = _ANSI.sub("", raw).rstrip()
        if _MATCH.search(clean):
            hits.append(clean)

    for line in hits[-_MAX_LINES:]:
        safe = line.replace("::", "%3A%3A").replace("%", "%25")[:_MAX_LEN]
        print(f"::error::e2e: {safe}")

    if not hits:
        print("::warning::e2e failed but no FAILED/Error/assert lines found in log")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
