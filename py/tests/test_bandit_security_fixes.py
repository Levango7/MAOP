"""Guards for the bandit-driven security fixes (复评处置·第三批).

Covers:
* MCP server URL scheme whitelist (B310 real fix, mcp_hub_transport)
* ``agent_repair`` exec-bit fix no longer widening read permissions (B103)
* CLIP revision pin (B615) — asserted in ``test_multimodal_embed.py``
  (``test_clip_revision_is_pinned``)
* nosec inventory snapshot: every ``# nosec`` in ``py/maop`` is registered
  here, so the silencing surface can only grow deliberately (design review
  of the disposal plan flagged "batch-nosec = silent silencing" as a risk).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MAOP = REPO / "py" / "maop"

#: ``rel_path:rule`` pairs of every ``# nosec`` in ``py/maop``.
#: Adding one is a decision — review the surrounding validation first.
NOSEC_INVENTORY: frozenset[str] = frozenset({
    "maop/core/mcp/mcp_hub_transport.py:B310",
    "maop/core/mcp/mcp_marketplace.py:B310",
    "maop/deploy.py:B310",
    "maop/core/agent/ops/sandbox_executor.py:B602",  # pre-existing (2026-08, x2)
})


# ── MCP URL scheme whitelist ───────────────────────────────


def test_assert_http_url_rejects_non_http_schemes():
    from maop.core.mcp.mcp_hub_transport import _assert_http_url

    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x/1"):
        with pytest.raises(ValueError, match="http/https"):
            _assert_http_url(url)


def test_assert_http_url_requires_hostname():
    from maop.core.mcp.mcp_hub_transport import _assert_http_url

    with pytest.raises(ValueError, match="no hostname"):
        _assert_http_url("http:///nohost")


def test_assert_http_url_allows_loopback_and_private():
    """Local MCP servers on 127.0.0.1 are the common case — must stay allowed."""
    from maop.core.mcp.mcp_hub_transport import _assert_http_url

    _assert_http_url("http://127.0.0.1:8080/mcp")
    _assert_http_url("https://192.168.1.10:9443/mcp")
    _assert_http_url("http://localhost:3000")


def test_streamable_transport_rejects_file_url_at_construction():
    from maop.core.mcp.mcp_hub_transport import _StreamableHttpTransport
    from maop.core.mcp.mcp_hub_types import MCPServerConfig

    with pytest.raises(ValueError, match="http/https"):
        _StreamableHttpTransport(MCPServerConfig(url="file:///etc/passwd"))


# ── agent_repair exec-bit fix ───────────────────────────────


def test_agent_repair_does_not_widen_read_permissions():
    """B103 regression pin: no literal 0o755 chmod; exec bit added via stat."""
    src = (MAOP / "core" / "agent" / "lifecycle" / "agent_repair.py").read_text(
        encoding="utf-8-sig"
    )
    assert not re.search(r"os\.chmod\([^,]+,\s*0o7\d\d", src), (
        "a fixed-permission chmod re-introduces the B103 widening"
    )
    assert "stat.S_IXUSR" in src and "os.stat(" in src


# ── nosec inventory ────────────────────────────────────────


def _scan_nosec() -> set[str]:
    found: set[str] = set()
    for path in MAOP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "# nosec" not in line:
                continue
            rule = re.search(r"#\s*nosec\s*([A-Z]\d{3})?", line)
            rule_id = (rule.group(1) if rule and rule.group(1) else "ALL")
            found.add(f"{path.relative_to(REPO / 'py').as_posix()}:{rule_id}")
    return found


def test_nosec_inventory_matches():
    """New ``# nosec`` must be registered deliberately (anti-mass-silencing)."""
    actual = _scan_nosec()
    new = sorted(actual - set(NOSEC_INVENTORY))
    stale = sorted(set(NOSEC_INVENTORY) - actual)
    assert not new, (
        "new # nosec directives appeared — review the validation around them, "
        f"then add to NOSEC_INVENTORY: {new}"
    )
    assert not stale, f"NOSEC_INVENTORY entries no longer present: {stale}"


def test_nosec_carries_a_reason():
    """A nosec without a nearby justification comment is not auditable."""
    offenders: list[str] = []
    for path in MAOP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "# nosec" not in line:
                continue
            window = "\n".join(lines[max(0, lineno - 3):lineno])
            has_reason = bool(re.search(r"[A-Za-z\u4e00-\u9fff]{6,}", window))
            if not has_reason:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"# nosec without a justification nearby: {offenders}"
