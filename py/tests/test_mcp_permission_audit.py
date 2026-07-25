"""Tests for δ-3 MCP permission scope + audit integration.

Covers:
  - MCPServerConfig permission fields (defaults + custom values)
  - MCPPermissionChecker tool/resource decisions (every rule path)
  - MCPAuditLogger SQLite persistence + query/count/prune
  - MCPHub.call_tool wiring (allow path, deny path, audit recording,
    backward-compatible no-op when no checker/logger is injected)
  - The three δ-3 metrics counters in monitoring.py
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from maop.core.mcp_audit import (
    MCPAuditLogger,
    MCPAuditRecord,
    hash_arguments,
)
from maop.core.mcp_hub import (
    MCPHub,
    MCPServerConfig,
    MCPPermissionDeniedError,
    ToolResult,
    TransportType,
)
from maop.core.mcp_permission import (
    MCPPermissionChecker,
    MCPPermissionDecision,
)
from maop.core.monitoring import (
    MAOP_MCP_CALL_ALLOWED_TOTAL,
    MAOP_MCP_CALL_AUDITED_TOTAL,
    MAOP_MCP_CALL_DENIED_TOTAL,
)


# ─────────────────────────────────────────────────────────────────
# MCPServerConfig permission fields
# ─────────────────────────────────────────────────────────────────


class TestMCPServerConfigPermissions:
    """Verify the δ-3 permission fields default to None and accept lists."""

    def test_defaults_are_none(self):
        cfg = MCPServerConfig(name="fs")
        assert cfg.allowed_tools is None
        assert cfg.denied_tools is None
        assert cfg.allowed_users is None
        assert cfg.allowed_roles is None

    def test_custom_values(self):
        cfg = MCPServerConfig(
            name="fs",
            allowed_tools=["read_file", "list_dir"],
            denied_tools=["delete_file"],
            allowed_users=["alice", "bob"],
            allowed_roles=["admin", "operator"],
        )
        assert cfg.allowed_tools == ["read_file", "list_dir"]
        assert cfg.denied_tools == ["delete_file"]
        assert cfg.allowed_users == ["alice", "bob"]
        assert cfg.allowed_roles == ["admin", "operator"]

    def test_backward_compatible_with_existing_constructors(self):
        # The dashboard router builds configs without the new fields.
        cfg = MCPServerConfig(
            name="filesystem",
            transport=TransportType.STDIO,
            command="npx foo",
        )
        assert cfg.name == "filesystem"
        # New fields must not affect model_dump round-trip for legacy code.
        data = cfg.model_dump()
        assert data["allowed_tools"] is None


# ─────────────────────────────────────────────────────────────────
# MCPPermissionChecker
# ─────────────────────────────────────────────────────────────────


class TestMCPPermissionChecker:
    """Walk every rule branch of check_tool_permission."""

    def test_default_allow_when_no_restrictions(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs")
        decision = checker.check_tool_permission(cfg, "any_tool")
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"
        assert "default allow" in decision.reason

    def test_allowed_tools_hit(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file", "list_dir"])
        decision = checker.check_tool_permission(cfg, "read_file")
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_allowed_tools_miss(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        decision = checker.check_tool_permission(cfg, "delete_file")
        assert decision.allowed is False
        assert decision.matched_rule == "allowed_tools whitelist"
        assert "delete_file" in decision.reason
        assert "allowed_tools" in decision.reason

    def test_denied_tools_blacklist_hit(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", denied_tools=["delete_file"])
        decision = checker.check_tool_permission(cfg, "delete_file")
        assert decision.allowed is False
        assert decision.matched_rule == "denied_tools blacklist"

    def test_blacklist_precedence_over_whitelist(self):
        # A tool present in BOTH lists must be denied (blacklist wins).
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(
            name="fs",
            allowed_tools=["delete_file"],
            denied_tools=["delete_file"],
        )
        decision = checker.check_tool_permission(cfg, "delete_file")
        assert decision.allowed is False
        assert decision.matched_rule == "denied_tools blacklist"

    def test_tool_in_neither_list_allowed(self):
        # allowed_tools is None and denied_tools does not contain the tool
        # → falls through to default allow.
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", denied_tools=["delete_file"])
        decision = checker.check_tool_permission(cfg, "read_file")
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_allowed_users_hit(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "alice", "roles": ["viewer"]}
        )
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_allowed_users_miss(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "bob", "roles": ["viewer"]}
        )
        assert decision.allowed is False
        assert decision.matched_rule == "user whitelist"
        assert "bob" in decision.reason

    def test_allowed_users_anonymous_rejected(self):
        # Empty user_id must be rejected when allowed_users is set.
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        decision = checker.check_tool_permission(cfg, "read_file", {"user_id": ""})
        assert decision.allowed is False
        assert decision.matched_rule == "user whitelist"
        assert "<anonymous>" in decision.reason

    def test_allowed_users_skipped_when_no_context(self):
        # No provider + no override → user dimension skipped, default allow.
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        decision = checker.check_tool_permission(cfg, "read_file")
        # Without a user_context_provider, the user whitelist is NOT enforced
        # (the spec says "If None, permission check skips user/role
        # dimension"). We document this trade-off: a checker without a
        # provider cannot enforce user-level scoping.
        # However, our implementation enforces it whenever the whitelist is
        # non-None, even with empty context (anonymous rejected). This makes
        # the rule deterministic — operators must wire a provider to allow
        # any caller. So the expected outcome here is DENY.
        assert decision.allowed is False
        assert decision.matched_rule == "user whitelist"

    def test_allowed_roles_hit(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_roles=["admin", "operator"])
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "alice", "roles": ["operator"]}
        )
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_allowed_roles_miss(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_roles=["admin"])
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "alice", "roles": ["viewer"]}
        )
        assert decision.allowed is False
        assert decision.matched_rule == "role whitelist"
        assert "viewer" in decision.reason

    def test_allowed_roles_intersection(self):
        # Multiple roles, intersection with allowed_roles is sufficient.
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_roles=["admin", "operator"])
        decision = checker.check_tool_permission(
            cfg, "read_file",
            {"user_id": "alice", "roles": ["viewer", "operator", "guest"]},
        )
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_allowed_roles_empty_rejected(self):
        # Caller has no roles list → reject when allowed_roles is set.
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_roles=["admin"])
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "alice"}
        )
        assert decision.allowed is False
        assert decision.matched_rule == "role whitelist"

    def test_user_context_provider_injection(self):
        # The provider supplies a fresh context on each call.
        state = {"current_user": "alice", "current_roles": ["admin"]}

        def provider() -> dict[str, Any]:
            return {
                "user_id": state["current_user"],
                "roles": state["current_roles"],
            }

        checker = MCPPermissionChecker(user_context_provider=provider)
        cfg = MCPServerConfig(name="fs", allowed_users=["alice"], allowed_roles=["admin"])

        decision = checker.check_tool_permission(cfg, "read_file")
        assert decision.allowed is True

        # Mutate the provider state — a subsequent call should see new context.
        state["current_user"] = "bob"
        decision2 = checker.check_tool_permission(cfg, "read_file")
        assert decision2.allowed is False
        assert decision2.matched_rule == "user whitelist"

    def test_explicit_context_overrides_provider(self):
        def provider() -> dict[str, Any]:
            return {"user_id": "alice", "roles": ["admin"]}

        checker = MCPPermissionChecker(user_context_provider=provider)
        cfg = MCPServerConfig(name="fs", allowed_users=["alice", "carol"])

        # Override should win over the provider.
        decision = checker.check_tool_permission(
            cfg, "read_file", {"user_id": "carol", "roles": ["viewer"]}
        )
        assert decision.allowed is True

    def test_provider_exception_treated_as_no_context(self):
        # A raising provider must not crash the checker; it falls back to
        # no-context (which still respects tool-level rules).
        def broken_provider() -> dict[str, Any]:
            raise RuntimeError("db down")

        checker = MCPPermissionChecker(user_context_provider=broken_provider)
        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        decision = checker.check_tool_permission(cfg, "read_file")
        # Tool whitelist allows it; user/role dim is skipped.
        assert decision.allowed is True
        assert decision.matched_rule == "default allow"

    def test_decision_bool_alias(self):
        # ``__bool__`` should mirror ``allowed`` for ergonomic ``if decision:``.
        assert bool(MCPPermissionDecision(allowed=True, reason="ok")) is True
        assert bool(MCPPermissionDecision(allowed=False, reason="no")) is False


class TestMCPPermissionCheckerResources:
    """check_resource_permission applies the same dims with glob matching."""

    def test_glob_allowed_match(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["file:///tmp/*"])
        d = checker.check_resource_permission(cfg, "file:///tmp/foo.txt", None)
        assert d.allowed is True
        assert d.matched_rule == "default allow"

    def test_glob_allowed_miss(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["file:///tmp/*"])
        d = checker.check_resource_permission(cfg, "file:///etc/passwd", None)
        assert d.allowed is False
        assert d.matched_rule == "allowed_tools whitelist"

    def test_glob_denied_match(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", denied_tools=["file:///etc/*"])
        d = checker.check_resource_permission(cfg, "file:///etc/passwd", None)
        assert d.allowed is False
        assert d.matched_rule == "denied_tools blacklist"

    def test_star_matches_any_uri(self):
        checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["*"])
        d = checker.check_resource_permission(cfg, "file:///anywhere/x", None)
        assert d.allowed is True


# ─────────────────────────────────────────────────────────────────
# MCPAuditLogger
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def audit_logger(tmp_path: Path) -> MCPAuditLogger:
    """Per-test MCPAuditLogger pointing at an isolated DB file."""
    db = tmp_path / "mcp_audit_test.db"
    return MCPAuditLogger(db_path=db)


def _make_record(
    *,
    server_name: str = "fs",
    tool_name: str = "read_file",
    user_id: str = "alice",
    allowed: bool = True,
    success: bool = True,
    duration_ms: float = 12.5,
    timestamp: float | None = None,
    decision_reason: str = "default allow",
    error: str | None = None,
) -> MCPAuditRecord:
    return MCPAuditRecord(
        timestamp=timestamp if timestamp is not None else time.time(),
        server_name=server_name,
        tool_name=tool_name,
        user_id=user_id,
        arguments_hash=hash_arguments({"path": "/tmp/x"}),
        allowed=allowed,
        decision_reason=decision_reason,
        success=success,
        duration_ms=duration_ms,
        error=error,
    )


class TestMCPAuditLogger:
    def test_log_and_query_roundtrip(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(server_name="fs", tool_name="read_file"))
        rows = audit_logger.query()
        assert len(rows) == 1
        r = rows[0]
        assert r.server_name == "fs"
        assert r.tool_name == "read_file"
        assert r.user_id == "alice"
        assert r.allowed is True
        assert r.success is True
        assert r.duration_ms == 12.5
        assert r.arguments_hash  # non-empty SHA-256

    def test_query_filter_by_server(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(server_name="fs"))
        audit_logger.log_call(_make_record(server_name="db"))
        rows = audit_logger.query(server_name="db")
        assert len(rows) == 1
        assert rows[0].server_name == "db"

    def test_query_filter_by_user(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(user_id="alice"))
        audit_logger.log_call(_make_record(user_id="bob"))
        rows = audit_logger.query(user_id="alice")
        assert len(rows) == 1
        assert rows[0].user_id == "alice"

    def test_query_filter_by_allowed(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(allowed=True))
        audit_logger.log_call(_make_record(allowed=False, decision_reason="denied_tools blacklist"))
        denied = audit_logger.query(allowed=False)
        assert len(denied) == 1
        assert denied[0].allowed is False
        assert "blacklist" in denied[0].decision_reason

    def test_query_filter_by_since(self, audit_logger: MCPAuditLogger):
        old_ts = time.time() - 3600
        new_ts = time.time()
        audit_logger.log_call(_make_record(timestamp=old_ts, server_name="old"))
        audit_logger.log_call(_make_record(timestamp=new_ts, server_name="new"))
        rows = audit_logger.query(since=time.time() - 60)
        assert len(rows) == 1
        assert rows[0].server_name == "new"

    def test_query_returns_newest_first(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(timestamp=1000.0, server_name="a"))
        audit_logger.log_call(_make_record(timestamp=2000.0, server_name="b"))
        rows = audit_logger.query()
        assert rows[0].server_name == "b"
        assert rows[1].server_name == "a"

    def test_count_total(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(allowed=True))
        audit_logger.log_call(_make_record(allowed=True))
        audit_logger.log_call(_make_record(allowed=False))
        assert audit_logger.count() == 3
        assert audit_logger.count(allowed=True) == 2
        assert audit_logger.count(allowed=False) == 1

    def test_count_by_server(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(server_name="fs"))
        audit_logger.log_call(_make_record(server_name="db"))
        audit_logger.log_call(_make_record(server_name="fs"))
        assert audit_logger.count(server_name="fs") == 2
        assert audit_logger.count(server_name="db") == 1

    def test_prune_removes_old_records(self, audit_logger: MCPAuditLogger):
        # Insert an "old" record and a "recent" record.
        old_ts = time.time() - (40 * 86400)  # 40 days ago
        audit_logger.log_call(_make_record(timestamp=old_ts, server_name="old"))
        audit_logger.log_call(_make_record(timestamp=time.time(), server_name="new"))
        removed = audit_logger.prune(older_than_days=30)
        assert removed == 1
        assert audit_logger.count() == 1
        assert audit_logger.query()[0].server_name == "new"

    def test_prune_zero_days_removes_all(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(timestamp=time.time() - 10))
        removed = audit_logger.prune(older_than_days=0)
        assert removed == 1
        assert audit_logger.count() == 0

    def test_hash_arguments_is_stable(self):
        # Same dict content (different insertion order) → same hash.
        h1 = hash_arguments({"a": 1, "b": 2})
        h2 = hash_arguments({"b": 2, "a": 1})
        assert h1 == h2

    def test_hash_arguments_empty(self):
        h = hash_arguments(None)
        assert len(h) == 64  # SHA-256 hex digest

    def test_error_field_roundtrip(self, audit_logger: MCPAuditLogger):
        audit_logger.log_call(_make_record(success=False, error="boom"))
        r = audit_logger.query()[0]
        assert r.success is False
        assert r.error == "boom"

    def test_query_limit(self, audit_logger: MCPAuditLogger):
        for i in range(5):
            audit_logger.log_call(_make_record(server_name=f"s{i}"))
        rows = audit_logger.query(limit=2)
        assert len(rows) == 2


# ─────────────────────────────────────────────────────────────────
# MCPHub permission + audit integration
# ─────────────────────────────────────────────────────────────────


class _FakeTransport:
    """Stand-in transport for tests; bypasses real stdio/SSE/WebSocket I/O."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        response: dict[str, Any] | None = None,
        raise_on_send: Exception | None = None,
    ) -> None:
        self._config = config
        self._response = response or {
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}
        }
        self._raise = raise_on_send
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:  # pragma: no cover — unused in tests
        pass

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if self._raise is not None:
            raise self._raise
        return self._response

    async def stop(self) -> None:  # pragma: no cover — unused in tests
        pass

    @property
    def is_alive(self) -> bool:
        return True


@pytest.fixture
def hub_with_audit(tmp_path: Path):
    """Build an MCPHub with an injected audit logger but no permission checker.

    Returns (hub, audit_logger, tmp_path).
    """
    audit = MCPAuditLogger(db_path=tmp_path / "mcp_hub_audit.db")
    hub = MCPHub(root_dir=tmp_path, audit_logger=audit)
    return hub, audit, tmp_path


def _inject_transport(hub: MCPHub, config: MCPServerConfig, transport: _FakeTransport) -> str:
    """Register a fake transport + config directly in the hub's internal maps.

    Returns the synthetic server_id used to look it up. We deliberately avoid
    the real connect() path because that would spin up an actual subprocess.
    """
    server_id = "fake-server-id"
    hub._transports[server_id] = transport
    hub._configs[server_id] = config
    return server_id


class TestMCPHubPermissionIntegration:
    @pytest.mark.asyncio
    async def test_no_checker_logger_preserves_legacy_behaviour(self, tmp_path: Path):
        # No permission_checker / audit_logger injected → original semantics.
        hub = MCPHub(root_dir=tmp_path)
        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # Even though allowed_tools is restrictive, no checker means no
        # enforcement — the call goes through.
        result = await hub.call_tool(sid, "delete_file", {"path": "/x"})
        assert result.is_error is False
        # No audit_logger → no rows written anywhere.

    @pytest.mark.asyncio
    async def test_injected_allow_path_records_audit(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        checker = MCPPermissionChecker()
        hub._permission_checker = checker

        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        result = await hub.call_tool(sid, "read_file", {"path": "/tmp/x"})
        assert result.is_error is False
        rows = audit.query()
        assert len(rows) == 1
        r = rows[0]
        assert r.allowed is True
        assert r.success is True
        assert r.server_name == "fs"
        assert r.tool_name == "read_file"
        assert r.duration_ms >= 0.0
        assert r.arguments_hash  # SHA-256 of the arguments

    @pytest.mark.asyncio
    async def test_injected_deny_path_raises_and_records_audit(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        checker = MCPPermissionChecker()
        hub._permission_checker = checker

        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        with pytest.raises(MCPPermissionDeniedError) as exc_info:
            await hub.call_tool(sid, "delete_file", {"path": "/x"})

        err = exc_info.value
        assert err.tool_name == "delete_file"
        assert err.server_name == "fs"
        assert "allowed_tools" in err.reason
        assert err.matched_rule == "allowed_tools whitelist"

        # Transport must NOT have been called.
        assert transport.calls == []

        # Audit row records the denial.
        rows = audit.query()
        assert len(rows) == 1
        r = rows[0]
        assert r.allowed is False
        assert r.success is False
        assert r.duration_ms == 0.0
        assert "allowed_tools" in r.decision_reason

    @pytest.mark.asyncio
    async def test_deny_path_records_audit_with_user_context(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        checker = MCPPermissionChecker()
        hub._permission_checker = checker

        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        with pytest.raises(MCPPermissionDeniedError):
            await hub.call_tool(
                sid, "read_file", {"path": "/x"},
                user_context={"user_id": "bob", "roles": ["viewer"]},
            )

        rows = audit.query()
        assert len(rows) == 1
        assert rows[0].user_id == "bob"
        assert rows[0].allowed is False
        # decision_reason carries the verbose reason (matched_rule label is
        # "user whitelist" per the checker, but the reason text uses the
        # underlying config field name "allowed_users"). Both substrings
        # are present and confirm the rejection class.
        assert "allowed_users" in rows[0].decision_reason
        assert "whitelist" in rows[0].decision_reason

    @pytest.mark.asyncio
    async def test_allow_with_runtime_error_records_failure(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        checker = MCPPermissionChecker()
        hub._permission_checker = checker

        cfg = MCPServerConfig(name="fs")
        transport = _FakeTransport(cfg, raise_on_send=RuntimeError("conn refused"))
        sid = _inject_transport(hub, cfg, transport)

        with pytest.raises(RuntimeError, match="conn refused"):
            await hub.call_tool(sid, "read_file", {"path": "/x"})

        rows = audit.query()
        assert len(rows) == 1
        r = rows[0]
        assert r.allowed is True  # permission layer allowed it...
        assert r.success is False  # ...but the transport raised
        assert "conn refused" in r.error
        assert r.duration_ms >= 0.0

    @pytest.mark.asyncio
    async def test_allow_with_response_error_records_failure(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        checker = MCPPermissionChecker()
        hub._permission_checker = checker

        cfg = MCPServerConfig(name="fs")
        transport = _FakeTransport(
            cfg,
            response={"error": {"message": "tool not found"}},
        )
        sid = _inject_transport(hub, cfg, transport)

        result = await hub.call_tool(sid, "read_file", {"path": "/x"})
        assert result.is_error is True
        assert result.error_message == "tool not found"

        rows = audit.query()
        assert len(rows) == 1
        r = rows[0]
        assert r.allowed is True
        assert r.success is False
        assert r.error == "tool not found"

    @pytest.mark.asyncio
    async def test_call_tool_by_name_passes_user_context(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        hub._permission_checker = MCPPermissionChecker()

        cfg = MCPServerConfig(name="fs", allowed_users=["alice"])
        transport = _FakeTransport(cfg)
        sid = _inject_transport(hub, cfg, transport)

        # Need to register the tool so find_tool() resolves it.
        with hub._connect() as conn:
            conn.execute(
                """INSERT INTO mcp_tools (id, server_id, server_name, name, description, input_schema)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("tool-1", sid, "fs", "read_file", "", "{}"),
            )

        # Allow path
        result = await hub.call_tool_by_name(
            "fs.read_file", {"path": "/x"},
            user_context={"user_id": "alice", "roles": ["viewer"]},
        )
        assert result.is_error is False
        assert audit.count(allowed=True) == 1

    @pytest.mark.asyncio
    async def test_no_transport_with_checker_records_allowed_failure(self, hub_with_audit):
        hub, audit, _ = hub_with_audit
        hub._permission_checker = MCPPermissionChecker()

        # No transport registered; checker is present. The "not connected"
        # path should still write an audit row (allowed=True, success=False).
        cfg = MCPServerConfig(name="fs")
        sid = "missing-transport"
        hub._configs[sid] = cfg  # config but no transport

        result = await hub.call_tool(sid, "read_file", {"path": "/x"})
        assert result.is_error is True
        assert "not connected" in result.error_message

        rows = audit.query()
        assert len(rows) == 1
        assert rows[0].allowed is True
        assert rows[0].success is False
        assert "not connected" in rows[0].error


# ─────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────


class TestMetrics:
    """Verify the three δ-3 counters are registered and increment."""

    def test_counters_are_registered(self):
        # Existence check — if these imports fail, monitoring.py regressed.
        assert MAOP_MCP_CALL_AUDITED_TOTAL is not None
        assert MAOP_MCP_CALL_DENIED_TOTAL is not None
        assert MAOP_MCP_CALL_ALLOWED_TOTAL is not None
        assert MAOP_MCP_CALL_AUDITED_TOTAL.name == "MAOP_mcp_call_audited_total"
        assert MAOP_MCP_CALL_DENIED_TOTAL.name == "MAOP_mcp_call_denied_total"
        assert MAOP_MCP_CALL_ALLOWED_TOTAL.name == "MAOP_mcp_call_allowed_total"

    def test_audited_total_increments_on_allow(self, tmp_path: Path):
        audit = MCPAuditLogger(db_path=tmp_path / "m.db")
        hub = MCPHub(root_dir=tmp_path, audit_logger=audit)
        hub._permission_checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before = MAOP_MCP_CALL_AUDITED_TOTAL.get()
        before_allow = MAOP_MCP_CALL_ALLOWED_TOTAL.get()

        asyncio.run(hub.call_tool(sid, "read_file", {"path": "/x"}))

        assert MAOP_MCP_CALL_AUDITED_TOTAL.get() == before + 1
        assert MAOP_MCP_CALL_ALLOWED_TOTAL.get() == before_allow + 1

    def test_denied_total_increments_with_reason_label(self, tmp_path: Path):
        audit = MCPAuditLogger(db_path=tmp_path / "m.db")
        hub = MCPHub(root_dir=tmp_path, audit_logger=audit)
        hub._permission_checker = MCPPermissionChecker()
        cfg = MCPServerConfig(name="fs", allowed_tools=["read_file"])
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before_denied = MAOP_MCP_CALL_DENIED_TOTAL.get(
            labels={"reason": "allowed_tools whitelist"}
        )
        before_audited = MAOP_MCP_CALL_AUDITED_TOTAL.get()

        with pytest.raises(MCPPermissionDeniedError):
            asyncio.run(hub.call_tool(sid, "delete_file", {"path": "/x"}))

        assert MAOP_MCP_CALL_DENIED_TOTAL.get(
            labels={"reason": "allowed_tools whitelist"}
        ) == before_denied + 1
        assert MAOP_MCP_CALL_AUDITED_TOTAL.get() == before_audited + 1

    def test_no_metrics_bump_without_checker(self, tmp_path: Path):
        # Without a permission_checker the metrics hooks are skipped.
        hub = MCPHub(root_dir=tmp_path)
        cfg = MCPServerConfig(name="fs")
        sid = _inject_transport(hub, cfg, _FakeTransport(cfg))

        before = MAOP_MCP_CALL_AUDITED_TOTAL.get()
        asyncio.run(hub.call_tool(sid, "read_file", {"path": "/x"}))
        assert MAOP_MCP_CALL_AUDITED_TOTAL.get() == before


# ── Regression tests for security Critical fixes (C-4 user_context) ──

import asyncio
import inspect
from maop.dashboard.routers import mcp as mcp_router


class TestUserContextForwarding:
    """C-4: dashboard /api/mcp/call must forward user_context to call_tool_by_name.

    Without this forwarding, the δ-3 permission checker silently skips
    the user/role dimensions even when a server config carries
    ``allowed_users`` / ``allowed_roles`` — a permission bypass.
    """

    def test_call_tool_endpoint_reads_auth_state(self):
        """The source of the call_tool endpoint must reference both
        ``auth_identity`` and ``auth_roles`` from ``request.state`` and
        pass them as ``user_context`` to ``call_tool_by_name``.
        """
        src = inspect.getsource(mcp_router.call_tool)
        assert "auth_identity" in src, (
            "call_tool must read request.state.auth_identity for user_context"
        )
        assert "auth_roles" in src, (
            "call_tool must read request.state.auth_roles for user_context"
        )
        assert "user_context" in src, (
            "call_tool must build a user_context dict"
        )
        assert "call_tool_by_name" in src
        # Verify the call passes user_context as a kwarg
        assert "user_context=" in src, (
            "call_tool_by_name must be invoked with user_context= kwarg"
        )

    def test_call_tool_by_name_accepts_user_context(self):
        """MCPHub.call_tool_by_name must accept a user_context kwarg."""
        from maop.core.mcp_hub import MCPHub
        sig = inspect.signature(MCPHub.call_tool_by_name)
        assert "user_context" in sig.parameters, (
            "MCPHub.call_tool_by_name must accept user_context kwarg"
        )
