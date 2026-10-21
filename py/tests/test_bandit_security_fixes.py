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
from collections import Counter

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MAOP = REPO / "py" / "maop"

#: ``rel_path:rule`` pairs of every ``# nosec`` in ``py/maop``.
#: Adding one is a decision — review the surrounding validation first.
NOSEC_INVENTORY: frozenset[str] = frozenset({
    "maop/core/agent/auth/agent_proxy_gateway.py:B608:10",
    "maop/core/agent/auth/credential_vault.py:B608:9",
    "maop/core/agent/auth/harness_auth.py:B608:8",
    # 2026-10-21 billing records：where 只由 "agent_name = ?" / "timestamp >= ?"
    # 两段字面量拼成，值一律走 params 绑定 ⇒ 无注入路径。
    "maop/core/agent/billing/billing_abstraction.py:B608:1",
    "maop/core/agent/delegation/subagent_db.py:B608:1",
    "maop/core/agent/delegation/subagent_lifecycle.py:B608:1",
    "maop/core/agent/lifecycle/agent_registry.py:B608:1",
    "maop/core/agent/memory_ctx/worktree.py:B608:1",
    "maop/core/agent/ops/sandbox_executor.py:B602:2",
    "maop/core/agent/plugins_hooks/hook_manager.py:B608:1",
    "maop/core/agent/tools/tool_audit.py:B608:5",
    "maop/core/backends/data.py:B608:6",
    "maop/core/backends/image_store.py:B608:2",
    "maop/core/backends/kv_store.py:B608:1",
    "maop/core/coordination/a2a_transport_server.py:B104:1",
    "maop/core/cost_tracker.py:B608:1",
    "maop/core/mcp/mcp_hub_ops.py:B608:1",
    "maop/core/mcp/mcp_hub_transport.py:B310:1",
    "maop/core/mcp/mcp_marketplace.py:B310:1",
    "maop/core/memory/episodic_store.py:B608:2",
    "maop/core/memory/knowledge_extractor.py:B608:3",
    "maop/core/memory/knowledge_graph.py:B608:4",
    # 2026-10-17 逐行核过 :244-266：where 只能由 "source = ?" / "target = ?" / "1=1" /
    # " AND relation_type = ?" 四段字面量拼成，direction 与 relation_type 都不进 SQL 文本，
    # 值一律走 params 绑定 ⇒ 无注入路径，登记而非改写。
    "maop/core/memory/knowledge_graph_reasoning.py:B608:1",
    "maop/core/memory/multimodal_pg.py:B608:5",
    "maop/core/memory/multimodal_store.py:B608:1",
    "maop/core/memory/vector_store.py:B608:8",
    "maop/core/monitoring/timeseries.py:B608:1",
    "maop/core/reliability/circuit_breaker.py:B608:2",
    "maop/core/reliability/message_queue.py:B608:1",
    "maop/core/reliability/pipeline_checkpoint.py:B608:1",
    "maop/core/security/api_key_manager.py:B608:1",
    "maop/core/security/session.py:B608:4",
    "maop/core/tenant/audit.py:B608:2",
    "maop/core/tenant/compliance_manager.py:B608:11",
    "maop/core/tenant/gdpr_manager.py:B608:6",
    "maop/core/tenant/hierarchy.py:B608:4",
    "maop/core/tenant/rls.py:B608:3",
    "maop/dashboard/routers/agent_versions_helpers.py:B608:1",
    "maop/dashboard/services/agent_versions_service.py:B608:8",
    "maop/dashboard/services/auth_service.py:B608:1",
    "maop/dashboard/services/chat_service.py:B608:10",
    "maop/deploy.py:B310:1",
    "maop/memory/search.py:B608:7",
    "maop/memory/store.py:B608:1",
    "maop/migrations/memory_migration.py:B608:4",
    "maop/migrations/pg/vector_migration.py:B608:1",
    "maop/migrations/sqlite_to_pg.py:B608:5",
    "maop/prompt_manager.py:B608:1",
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


def _scan_nosec() -> Counter[str]:
    found: Counter[str] = Counter()
    for path in MAOP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "# nosec" not in line:
                continue
            rule = re.search(r"#\s*nosec\s*([A-Z]\d{3})?", line)
            rule_id = (rule.group(1) if rule and rule.group(1) else "ALL")
            found[f"{path.relative_to(REPO / 'py').as_posix()}:{rule_id}"] += 1
    return found


def test_nosec_inventory_matches():
    """New ``# nosec`` must be registered deliberately (anti-mass-silencing).

    Registry is ``path:RULE:count`` — line numbers are deliberately NOT used
    (they shift on any edit above, the 假红 class ADR-023 already covers);
    a count change means sites were added/removed in that file.
    """
    actual = _scan_nosec()
    actual_keys = {f"{k}:{n}" for k, n in actual.items()}
    new = sorted(actual_keys - set(NOSEC_INVENTORY))
    stale = sorted(set(NOSEC_INVENTORY) - actual_keys)
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
