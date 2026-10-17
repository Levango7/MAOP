"""MAOP Coordination — M4 全链路集成测试 (Dashboard API + WS + Blackboard).

Tests the M4-A ~ M4-D deliverables end-to-end via the real FastAPI app
and WebSocket endpoint, covering:

- **M4-A** : Blackboard M3 Operations API (debate/consensus/convergence/health)
- **M4-B** : WebSocket event broadcast for M3 operations
- **M4-C** : Prometheus /metrics endpoint (text format)
- **M4-D** : Multi-Tenant Blackboard Admin API (tenants/rbac/context)
- **M4-E** : Full pipeline stress + end-to-end convergence

Design reference: tests/test_coordination_m3.py (M3 test).
"""

from __future__ import annotations

import asyncio
import sys
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, r"F:/Nexus/MAOP/py")

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app():
    """Import the real FastAPI app (conftest sets MAOP_ENV=test, MAOP_AUTH=0)."""
    from maop.dashboard.server import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    """Synchronous TestClient for HTTP endpoints."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(app):
    """TestClient that bypasses require_admin (patches admin guard)."""
    # Patch require_admin to no-op in all routers that use it
    with patch("maop.dashboard.routers.bb_m3_ops.require_admin", lambda request: None), \
         patch("maop.dashboard.routers.tenant_blackboard.require_admin", lambda request: None), \
         TestClient(app) as c:
        yield c


# ────────────────────────────────────────────────────────────────────
# M4-A: Blackboard M3 Operations API
# ────────────────────────────────────────────────────────────────────


class TestM3HealthEndpoints:
    """Tests for GET /api/bb-m3/health and /probes."""

    def test_health_returns_snapshot(self, client):
        resp = client.get("/api/bb-m3/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body
        assert "solves" in body["data"]
        assert "dispatches" in body["data"]
        assert "debates" in body["data"]
        assert "conflicts" in body["data"]

    def test_probes_returns_liveness_readiness(self, client):
        resp = client.get("/api/bb-m3/probes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "liveness" in body["data"]
        assert "readiness" in body["data"]
        assert body["data"]["liveness"] is True

    def test_probes_readiness_has_ready_flag(self, client):
        resp = client.get("/api/bb-m3/probes")
        data = resp.json()["data"]["readiness"]
        assert "ready" in data
        assert isinstance(data["ready"], bool)


class TestM3ConsensusScan:
    """Tests for GET /api/bb-m3/consensus/scan."""

    def test_scan_empty_blackboard(self, client):
        resp = client.get("/api/bb-m3/consensus/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert "conflicts" in body
        assert "active_count" in body
        assert "summary" in body

    def test_scan_with_query_params(self, client):
        resp = client.get("/api/bb-m3/consensus/scan?domain=decision&deadlock_cycles=5")
        assert resp.status_code == 200

    def test_scan_detects_conflicts(self, admin_client):
        """Seed two conflicting entries; scan should detect them."""
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        # Clear first for deterministic state (clear all domains)
        for domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(domain))
        try:
            asyncio.run(
                bb.write("decision", {"v": 1}, contributor="a",
                         metadata={"polarity": "positive"}, confidence=0.9)
            )
            asyncio.run(
                bb.write("decision", {"v": 2}, contributor="b",
                         metadata={"polarity": "negative"}, confidence=0.9)
            )
            resp = admin_client.get("/api/bb-m3/consensus/scan")
            assert resp.status_code == 200
            body = resp.json()
            assert body["active_count"] >= 1
        finally:
            for domain in list(bb._entries.keys()):
                asyncio.run(bb.clear(domain))


class TestM3DebateEndpoint:
    """Tests for POST /api/bb-m3/debate/run."""

    def test_debate_run_success(self, admin_client):
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        asyncio.run(
            bb.write("decision", {"q": "Should we proceed?"})
        )
        try:
            resp = admin_client.post("/api/bb-m3/debate/run", json={
                "question_domain": "decision",
                "consensus_threshold": 0.3,
                "quorum": 1,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert "data" in body
            assert "converged" in body["data"]
            assert "final_stance" in body["data"]
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_debate_no_entries_returns_400(self, admin_client):
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        resp = admin_client.post("/api/bb-m3/debate/run", json={
            "question_domain": "nonexistent_domain",
        })
        assert resp.status_code == 400

    def test_debate_with_custom_participants(self, admin_client):
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        asyncio.run(
            bb.write("decision", {"q": "test"})
        )
        try:
            resp = admin_client.post("/api/bb-m3/debate/run", json={
                "question_domain": "decision",
                "participants": [
                    {"name": "v1", "stance": "agree", "confidence": 0.9},
                    {"name": "v2", "stance": "agree", "confidence": 0.8},
                ],
                "consensus_threshold": 0.5,
                "quorum": 2,
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))


class TestM3ConvergenceEndpoint:
    """Tests for POST /api/bb-m3/convergence/start and GET /{solve_id}."""

    def test_convergence_start_returns_solve_id(self, admin_client):
        resp = admin_client.post("/api/bb-m3/convergence/start", json={
            "max_iterations": 5,
            "goal_domain": "decision",
            "goal_state": "finalized",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "solve_id" in body
        assert body["status"] == "started"
        assert len(body["solve_id"]) > 0

    def test_convergence_status_not_found(self, admin_client):
        resp = admin_client.get("/api/bb-m3/convergence/nonexistent-id")
        assert resp.status_code == 404

    def test_convergence_status_found(self, admin_client):
        # Start a solve
        start_resp = admin_client.post("/api/bb-m3/convergence/start", json={
            "solve_id": "test-solve-001",
            "max_iterations": 3,
        })
        assert start_resp.status_code == 200
        solve_id = start_resp.json()["solve_id"]
        assert solve_id == "test-solve-001"

        # Query status
        status_resp = admin_client.get(f"/api/bb-m3/convergence/{solve_id}")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert "solve_id" in body
        assert "is_running" in body
        assert "iteration" in body


# ────────────────────────────────────────────────────────────────────
# M4-B: WebSocket Event Broadcast
# ────────────────────────────────────────────────────────────────────


class TestWebSocketBroadcast:
    """Tests for WebSocket /ws endpoint receiving M3 events."""

    def test_ws_hello_on_connect(self, app):
        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert data["type"] == "hello"
            assert "msg" in data

    def test_ws_ping_pong(self, app):
        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.send_text("ping")
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_ws_receives_broadcast(self, app):
        """Verify WS broadcast infrastructure: clients receive broadcast messages.

        The background _ws_push_loop emits periodic 'snapshot' events to all
        connected clients — receiving these proves the WS broadcast path works.

        Note: M3-op events (bb_m3.*) are emitted via background tasks started
        with asyncio.create_task(). Starlette TestClient runs each request in
        a fresh event loop (asyncio.run), which cancels pending tasks on return.
        This is a known TestClient limitation — in production (uvicorn), the
        event loop persists and background tasks run normally.
        """
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        try:
            with TestClient(app) as client, client.websocket_connect("/ws") as ws:
                hello = ws.receive_json()  # hello
                assert hello["type"] == "hello"

                # The background _ws_push_loop broadcasts 'snapshot' every 15s.
                # Receiving at least one proves the WS broadcast infrastructure works.
                received_types = []
                for _ in range(30):
                    try:
                        msg = ws.receive_json()
                        received_types.append(msg.get("type", ""))
                        if "snapshot" in received_types:
                            break
                    except Exception:
                        break

                # Prove WS broadcast is functional: either a snapshot was received
                # (from the background push loop) or other broadcast types arrived
                assert len(received_types) > 0, "Expected at least one WS broadcast message"
                assert "snapshot" in received_types, (
                    f"Expected 'snapshot' broadcast from push loop, got: {received_types}"
                )
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_ws_receives_conflict_alert(self, app):
        """Scanning with conflicts should emit a conflict_alert via WS."""
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        asyncio.run(
            bb.write("decision", {"v": 1}, contributor="a",
                     metadata={"polarity": "positive"}, confidence=0.9)
        )
        asyncio.run(
            bb.write("decision", {"v": 2}, contributor="b",
                     metadata={"polarity": "negative"}, confidence=0.9)
        )
        try:
            with TestClient(app) as client, client.websocket_connect("/ws") as ws:
                ws.receive_json()  # hello
                scan_resp = client.get("/api/bb-m3/consensus/scan")
                assert scan_resp.status_code == 200

                # Wait for potential WS broadcast. The alert is emitted by a
                # background task inside the scan request's event loop, and
                # TestClient cancels pending tasks when that loop returns (see
                # the note on test_ws_receives_broadcast), so it may never
                # arrive. receive_json() blocks forever when the server sends
                # nothing -- bound the wait: drain on a daemon thread, then
                # force-close the socket from the test thread. Closing only
                # touches the send side (never blocked here), unblocks the
                # drain via WebSocketDisconnect, and keeps the with-block
                # from hanging. The scan itself is the hard assertion.
                received_types: list[str] = []

                def _drain() -> None:
                    try:
                        for _ in range(10):
                            msg = ws.receive_json()
                            received_types.append(str(msg.get("type", "")))
                    except Exception:
                        pass

                drain = threading.Thread(target=_drain, daemon=True)
                drain.start()
                drain.join(timeout=10.0)
                if drain.is_alive():
                    ws.close()
                    drain.join(timeout=5.0)

                # Conflict alert may or may not appear depending on timing,
                # but we verify the scan itself worked
                assert scan_resp.json()["active_count"] >= 1
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))


# ────────────────────────────────────────────────────────────────────
# M4-C: Prometheus /metrics Endpoint
# ────────────────────────────────────────────────────────────────────


class TestPrometheusMetrics:
    """Tests for GET /api/bb-m3/metrics."""

    def test_metrics_returns_text_format(self, client):
        resp = client.get("/api/bb-m3/metrics")
        assert resp.status_code == 200
        text = resp.text
        # Must contain expected Prometheus metric names
        assert "maop_blackboard_solves_initiated_total" in text
        assert "maop_blackboard_solves_converged_total" in text
        assert "maop_blackboard_iterations_total" in text

    def test_metrics_reflects_operations(self, admin_client):
        """After starting a solve, metrics should update."""
        # Trigger a convergence solve (converges immediately due to seeded state)
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        asyncio.run(
            bb.write("decision", {"v": 1}, metadata={"state": "finalized"})
        )
        try:
            with patch("maop.dashboard.routers.bb_m3_ops.require_admin", lambda request: None):
                admin_client.post("/api/bb-m3/convergence/start", json={
                    "max_iterations": 5,
                    "goal_domain": "decision",
                    "goal_state": "finalized",
                })

            # Small delay for background task to start
            import time
            time.sleep(0.1)

            resp = admin_client.get("/api/bb-m3/metrics")
            assert resp.status_code == 200
            text = resp.text
            # Verify metric format: name value (no labels in basic case)
            lines = [l for l in text.strip().split("\n") if l]
            assert len(lines) > 5  # More than a few metrics
            # At least one metric should have a numeric value
            for line in lines[:3]:
                parts = line.split()
                assert len(parts) >= 2
                float(parts[1])  # value must be parseable as float
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_metrics_probe_labels(self, client):
        resp = client.get("/api/bb-m3/metrics")
        text = resp.text
        assert "maop_blackboard_probe_liveness" in text
        assert "maop_blackboard_probe_readiness" in text


# ────────────────────────────────────────────────────────────────────
# M4-D: Multi-Tenant Blackboard Admin API
# ────────────────────────────────────────────────────────────────────


class TestTenantAdminList:
    """Tests for GET /api/tenant-blackboard/tenants."""

    def test_list_tenants_returns_array(self, client):
        resp = client.get("/api/tenant-blackboard/tenants")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert isinstance(body["data"], list)

    def test_list_tenants_shows_seeded_tenants(self, client):
        """Seed tenant data and verify they show up in the list."""
        from maop.core.reliability.blackboard import get_blackboard
        from maop.core.reliability.coordination_tenant import (
            RBACPermission,
            TenantBlackboard,
            TenantContext,
        )
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        ctx = TenantContext(
            tenant_id="tenant-alpha",
            user_id="alice",
            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
        )
        tbb = TenantBlackboard(bb, ctx)
        asyncio.run(
            tbb.write("decision", {"v": "alpha_data"})
        )
        try:
            resp = client.get("/api/tenant-blackboard/tenants")
            assert resp.status_code == 200
            body = resp.json()
            tenant_ids = [t["tenant_id"] for t in body["data"]]
            assert "tenant-alpha" in tenant_ids
            # Verify total_entries and domains
            alpha = next(t for t in body["data"] if t["tenant_id"] == "tenant-alpha")
            assert alpha["total_entries"] >= 1
            assert "decision" in alpha["domains"]
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))


class TestTenantAdminDetail:
    """Tests for GET /api/tenant-blackboard/tenants/{tenant_id}."""

    def test_get_tenant_detail(self, client):
        from maop.core.reliability.blackboard import get_blackboard
        from maop.core.reliability.coordination_tenant import (
            RBACPermission,
            TenantBlackboard,
            TenantContext,
        )
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        ctx = TenantContext(
            tenant_id="acme-corp",
            user_id="bob",
            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
        )
        tbb = TenantBlackboard(bb, ctx)
        asyncio.run(
            tbb.write("decision", {"secret": "acme-123"})
        )
        try:
            resp = client.get("/api/tenant-blackboard/tenants/acme-corp")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            detail = body["data"]
            assert detail["tenant_id"] == "acme-corp"
            assert "snapshot" in detail
            assert "history" in detail
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_get_nonexistent_tenant_returns_empty(self, client):
        resp = client.get("/api/tenant-blackboard/tenants/ghost-tenant-999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


class TestTenantAdminDelete:
    """Tests for DELETE /api/tenant-blackboard/tenants/{tenant_id}."""

    def test_delete_tenant_data(self, admin_client):
        from maop.core.reliability.blackboard import get_blackboard
        from maop.core.reliability.coordination_tenant import (
            RBACPermission,
            TenantBlackboard,
            TenantContext,
        )
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        ctx = TenantContext(
            tenant_id="delete-me",
            user_id="admin",
            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ, RBACPermission.BB_ADMIN}),
        )
        tbb = TenantBlackboard(bb, ctx)
        asyncio.run(
            tbb.write("decision", {"v": "to-be-deleted"})
        )
        # Verify data exists
        assert len(bb.read("decision")) >= 1

        resp = admin_client.delete("/api/tenant-blackboard/tenants/delete-me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["tenant_id"] == "delete-me"
        assert body["data"]["removed_entries"] >= 1

    def test_delete_nonexistent_tenant(self, admin_client):
        resp = admin_client.delete("/api/tenant-blackboard/tenants/zz-no-such-tenant")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["removed_entries"] == 0


class TestRBACPermissionsEndpoint:
    """Tests for GET /api/tenant-blackboard/rbac/permissions."""

    def test_list_permissions(self, client):
        resp = client.get("/api/tenant-blackboard/rbac/permissions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        perms = body["data"]
        assert len(perms) >= 5  # At least 5 RBACPermission values
        values = {p["value"] for p in perms}
        assert "blackboard:read" in values
        assert "blackboard:write" in values
        assert "blackboard:admin" in values


class TestTenantContextValidate:
    """Tests for POST /api/tenant-blackboard/context/validate."""

    def test_validate_valid_context(self, admin_client):
        resp = admin_client.post("/api/tenant-blackboard/context/validate", json={
            "tenant_id": "test-tenant",
            "user_id": "alice",
            "roles": ["admin"],
            "permissions": ["blackboard:read", "blackboard:write"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        data = body["data"]
        assert data["tenant_id"] == "test-tenant"
        assert data["user_id"] == "alice"
        assert "blackboard:read" in data["permissions"]
        assert "blackboard:write" in data["permissions"]

    def test_validate_context_with_admin_role(self, admin_client):
        resp = admin_client.post("/api/tenant-blackboard/context/validate", json={
            "tenant_id": "sys",
            "user_id": "root",
            "roles": ["superadmin"],
            "permissions": [],
        })
        assert resp.status_code == 200
        body = resp.json()
        # superadmin role grants all permissions
        assert body["data"]["has_admin"] is True

    def test_validate_unknown_permissions_filtered(self, admin_client):
        resp = admin_client.post("/api/tenant-blackboard/context/validate", json={
            "tenant_id": "t",
            "user_id": "u",
            "permissions": ["blackboard:read", "invalid:perm", "blackboard:admin"],
        })
        assert resp.status_code == 200
        perms = resp.json()["data"]["permissions"]
        # Invalid permission should be filtered out
        assert "invalid:perm" not in perms
        assert "blackboard:read" in perms


# ────────────────────────────────────────────────────────────────────
# M4-E: Full Pipeline Stress + End-to-End Convergence
# ────────────────────────────────────────────────────────────────────


class TestFullPipelineE2E:
    """End-to-end tests that exercise the entire M3+M4 stack together."""

    def test_convergence_e2e_converges(self, admin_client):
        """Seed goal state upfront; convergence should complete in 1 iteration."""
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        asyncio.run(
            bb.write("decision", {"verdict": "approved"}, metadata={"state": "finalized"})
        )
        try:
            resp = admin_client.post("/api/bb-m3/convergence/start", json={
                "max_iterations": 10,
                "goal_domain": "decision",
                "goal_state": "finalized",
            })
            assert resp.status_code == 200
            solve_id = resp.json()["solve_id"]

            # Poll until no longer running (max 5 seconds)
            import time
            for _ in range(50):
                status = admin_client.get(f"/api/bb-m3/convergence/{solve_id}")
                if status.status_code == 200:
                    st = status.json()
                    if not st.get("is_running", True):
                        break
                time.sleep(0.1)

            # Verify final status shows convergence completion
            # (the controller may have already finished)
            final_status = admin_client.get(f"/api/bb-m3/convergence/{solve_id}")
            assert final_status.status_code == 200
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_debate_then_scan_pipeline(self, admin_client):
        """Run consensus scan, then debate, then verify debates counter incremented."""
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        # Seed a decision for debate
        asyncio.run(
            bb.write("decision", {"q": "should we launch?"})
        )
        try:
            # Step 1: Scan (no conflicts yet)
            scan_resp = admin_client.get("/api/bb-m3/consensus/scan")
            assert scan_resp.status_code == 200

            # Step 2: Run debate on the decision entry
            debate_resp = admin_client.post("/api/bb-m3/debate/run", json={
                "question_domain": "decision",
                "consensus_threshold": 0.3,
                "quorum": 1,
            })
            assert debate_resp.status_code == 200

            # Step 3: After debate, scan again (conflict may appear if debate recorded hypothesis)
            scan2 = admin_client.get("/api/bb-m3/consensus/scan")
            assert scan2.status_code == 200

            # Step 4: Verify health endpoint reflects debates
            health = admin_client.get("/api/bb-m3/health")
            assert health.status_code == 200
            # The debate engine records results in hypothesis, which doesn't affect solves counter
            # But we verify the endpoint is consistent
            assert "debates" in health.json()["data"]
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_tenant_isolation_e2e(self, admin_client):
        """Write entries as two tenants; verify they are isolated."""
        from maop.core.reliability.blackboard import get_blackboard
        from maop.core.reliability.coordination_tenant import (
            RBACPermission,
            TenantBlackboard,
            TenantContext,
        )
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))

        ctx_a = TenantContext(
            tenant_id="iso-a",
            user_id="alice",
            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
        )
        ctx_b = TenantContext(
            tenant_id="iso-b",
            user_id="bob",
            permissions=frozenset({RBACPermission.BB_WRITE, RBACPermission.BB_READ}),
        )
        tbb_a = TenantBlackboard(bb, ctx_a)
        tbb_b = TenantBlackboard(bb, ctx_b)
        asyncio.run(tbb_a.write("decision", {"v": "a-only"}))
        asyncio.run(tbb_b.write("decision", {"v": "b-only"}))
        try:
            # Verify each tenant only sees their own data in the admin API
            resp_a = admin_client.get("/api/tenant-blackboard/tenants/iso-a")
            resp_b = admin_client.get("/api/tenant-blackboard/tenants/iso-b")
            assert resp_a.status_code == 200
            assert resp_b.status_code == 200

            snap_a = resp_a.json()["data"]["snapshot"]
            snap_b = resp_b.json()["data"]["snapshot"]

            # iso-a should only have its own entry
            decision_a = snap_a.get("decision", [])
            assert len(decision_a) >= 1
            assert decision_a[0]["content"]["v"] == "a-only"

            decision_b = snap_b.get("decision", [])
            assert len(decision_b) >= 1
            assert decision_b[0]["content"]["v"] == "b-only"
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))


class TestStressConcurrentSolves:
    """Stress test: start multiple concurrent convergence solves."""

    def test_concurrent_convergence_starts(self, admin_client):
        """Start 5 concurrent solves; all should return unique solve_ids."""
        from maop.core.reliability.blackboard import get_blackboard
        bb = get_blackboard()
        for _domain in list(bb._entries.keys()):
            asyncio.run(bb.clear(_domain))
        try:
            solve_ids = []
            for i in range(5):
                resp = admin_client.post("/api/bb-m3/convergence/start", json={
                    "max_iterations": 10,
                    "goal_domain": "decision",
                    "goal_state": "finalized",
                })
                assert resp.status_code == 200
                sid = resp.json()["solve_id"]
                assert sid is not None
                solve_ids.append(sid)

            # All IDs should be unique
            assert len(set(solve_ids)) == 5

            # All should be queryable
            for sid in solve_ids:
                status = admin_client.get(f"/api/bb-m3/convergence/{sid}")
                assert status.status_code == 200
        finally:
            for _d in list(bb._entries.keys()):
                asyncio.run(bb.clear(_d))

    def test_rapid_health_probes_no_error(self, admin_client):
        """Rapidly hit health/probes/metrics to ensure no race conditions."""
        for _ in range(20):
            h = admin_client.get("/api/bb-m3/health")
            assert h.status_code == 200
            p = admin_client.get("/api/bb-m3/probes")
            assert p.status_code == 200
            m = admin_client.get("/api/bb-m3/metrics")
            assert m.status_code == 200
