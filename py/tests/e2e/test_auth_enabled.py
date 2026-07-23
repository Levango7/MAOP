"""End-to-end tests with authentication enabled.

Validates the full request lifecycle under MAOP_AUTH=1:
  - Login -> token -> protected endpoint -> logout -> token invalid
  - SSE endpoint accessible with valid token
  - Message queue ACK after successful dispatch
  - Budget guard blocks dispatch when exceeded

These tests prevent regression of R4 audit P0 fixes.
"""

from __future__ import annotations

import os
import json
import asyncio

# Set auth enabled BEFORE importing app
os.environ["MAOP_AUTH"] = "1"
os.environ["MAOP_ENV"] = "test"
os.environ.setdefault("MAOP_ADMIN_PASSWORD", "TestAdminPass123!")

import pytest
from httpx import AsyncClient, ASGITransport

# Verify auth was enabled at import time (skip if app was already
# imported without MAOP_AUTH=1 by another test module)
from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard import server as _server_mod
if not (_auth_mod._auth_enabled and _server_mod._auth_enabled):
    pytest.skip(
        "MAOP_AUTH=1 must be set before app import; run this test in isolation",
        allow_module_level=True,
    )

from maop.dashboard.server import app

_TEST_PASSWORD = os.environ.get("MAOP_ADMIN_PASSWORD", "TestAdminPass123!")


# -- Helpers --------------------------------------------------------


async def _login(client: AsyncClient, username: str = "admin", password: str = _TEST_PASSWORD) -> str:
    """Login and return the JWT token."""
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    return data["token"]


# -- Fixtures -------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Create test client with auth enabled, using isolated temp DB.

    Patches MAOP_ROOT in the auth router so the auth DB and JWT
    revocation file are created in a temp directory, keeping the
    test self-contained. Also manually sets app.state.auth_manager
    because ASGITransport does not run the ASGI lifespan event.
    """
    # Redirect auth DB and JWT revocation file to temp dir
    monkeypatch.setattr(_auth_mod, "MAOP_ROOT", tmp_path)
    monkeypatch.setenv("MAOP_ROOT_DIR", str(tmp_path))

    # Reset auth manager singleton to use temp DB
    _auth_mod._auth_mgr = None
    mgr = _auth_mod.get_auth_mgr()
    app.state.auth_manager = mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def admin_credentials() -> dict:
    return {"username": "admin", "password": _TEST_PASSWORD}


# -- Test: Auth Flow ------------------------------------------------


class TestAuthFlow:
    """Test complete authentication flow."""

    async def test_login_returns_jwt(self, client, admin_credentials):
        """Login with admin credentials returns a valid JWT."""
        token = await _login(client, **admin_credentials)
        assert token, "Token should not be empty"
        assert token.count(".") == 2, "JWT should have 3 dot-separated parts"

    async def test_protected_endpoint_without_token_returns_401(self, client):
        """Accessing /api/agents without token returns 401."""
        resp = await client.get("/api/agents")
        assert resp.status_code == 401

    async def test_protected_endpoint_with_valid_token(self, client, admin_credentials):
        """Accessing /api/agents with valid token returns 200."""
        token = await _login(client, **admin_credentials)
        resp = await client.get(
            "/api/agents", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

    async def test_logout_invalidates_token(self, client, admin_credentials):
        """After logout, the token is no longer valid."""
        token = await _login(client, **admin_credentials)

        # Verify token works before logout
        resp = await client.get(
            "/api/agents", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

        # Logout
        resp = await client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

        # Verify token is now invalid (revoked)
        resp = await client.get(
            "/api/agents", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401

    async def test_revoked_token_persisted(self, client, admin_credentials, tmp_path):
        """Revoked tokens survive server restart via jwt_revoked.json (P2 fix)."""
        token = await _login(client, **admin_credentials)
        mgr = _auth_mod.get_auth_mgr()
        assert mgr.jwt_handler.revoke_token(token), "revoke_token should return True"

        revoked_file = tmp_path / "data" / "jwt_revoked.json"
        assert revoked_file.exists(), "jwt_revoked.json should be persisted to disk"

        data = json.loads(revoked_file.read_text(encoding="utf-8"))
        sig_b64 = token.split(".")[2]
        assert sig_b64 in data, "Revoked token signature should be in the persisted file"


# -- Test: SSE With Auth --------------------------------------------


class TestSSEWithAuth:
    """Test SSE endpoints under auth=1 (R4 P0 fix)."""

    async def test_sse_global_stream_requires_token(self, client):
        """SSE /api/stream returns 403 without token.

        /api/stream is in AuthMiddleware public_paths, so the middleware
        lets the request through. The handler then calls require_admin()
        which raises HTTPException(403) because no auth roles are set.
        """
        resp = await client.get("/api/stream")
        assert resp.status_code == 403

    async def test_sse_global_stream_with_token(self, client, admin_credentials):
        """SSE /api/stream accessible with valid token in query param.

        SSE endpoints return an infinite stream, so we use
        asyncio.wait_for with a short timeout. A TimeoutError means
        the stream started successfully (token was accepted); a 403
        response means the token was rejected.
        """
        token = await _login(client, **admin_credentials)
        try:
            resp = await asyncio.wait_for(
                client.get("/api/stream", params={"token": token}),
                timeout=3.0,
            )
            # If we get a response, token was accepted (not 403)
            assert resp.status_code != 403, "Token should be accepted by SSE endpoint"
        except asyncio.TimeoutError:
            # Timeout is expected: the SSE stream is infinite, meaning
            # the token was accepted and the endpoint is streaming.
            pass

    async def test_sse_trace_requires_auth(self, client):
        """SSE /api/stream/{trace_id} returns 401 without token.

        Unlike /api/stream (exact match in public_paths), the trace
        endpoint is NOT in public_paths, so AuthMiddleware blocks
        unauthenticated requests with 401.
        """
        resp = await client.get("/api/stream/nonexistent-trace-id")
        assert resp.status_code == 401


# -- Test: Message Queue ACK ----------------------------------------


class TestMessageQueueACK:
    """Test message queue ACK after dispatch (R4 P0 fix)."""

    def test_task_not_duplicated_after_ack(self, tmp_path):
        """Verify that a dispatched task is not re-executed after ACK."""
        from maop.core.message_queue import MessageQueue

        mq = MessageQueue(db_path=tmp_path / "queue.db")
        msg_id = mq.enqueue("test-topic", {"task": "do-work"}, max_retries=3)
        assert msg_id, "enqueue should return a non-empty msg_id"

        # Dequeue the message (status transitions to processing)
        msg = mq.dequeue("test-topic")
        assert msg is not None
        assert msg.id == msg_id
        assert msg.status == "processing"

        # ACK the message (status transitions to acked)
        assert mq.ack(msg_id), "ack should return True"

        # Verify no more pending messages
        msg2 = mq.dequeue("test-topic")
        assert msg2 is None, "No more messages should be available after ACK"

    def test_nack_requeues_message(self, tmp_path):
        """Verify that NACK requeues the message for retry."""
        from maop.core.message_queue import MessageQueue

        mq = MessageQueue(db_path=tmp_path / "queue.db")
        msg_id = mq.enqueue("test-topic", {"task": "retry-me"}, max_retries=3)

        # Dequeue and NACK
        msg = mq.dequeue("test-topic")
        assert msg is not None
        assert mq.nack(msg_id, error="test failure"), "nack should return True"

        # Message should be requeued (pending again)
        msg2 = mq.dequeue("test-topic")
        assert msg2 is not None, "Message should be requeued after NACK"
        assert msg2.id == msg_id, "Same message should be returned after NACK requeue"

    def test_nack_dead_letters_after_max_retries(self, tmp_path):
        """After max_retries+1 NACKs, the message is moved to dead letter."""
        from maop.core.message_queue import MessageQueue

        mq = MessageQueue(db_path=tmp_path / "queue.db")
        msg_id = mq.enqueue("test-topic", {"task": "always-fails"}, max_retries=2)

        # NACK 3 times (initial + 2 retries = 3 total dequeues)
        for i in range(3):
            msg = mq.dequeue("test-topic")
            assert msg is not None, f"Dequeue {i + 1} should return the message"
            assert mq.nack(msg_id, error=f"attempt {i + 1}")

        # After max_retries+1 NACKs, message should be dead, not requeued
        msg = mq.dequeue("test-topic")
        assert msg is None, "Message should be in dead letter, not requeued"


# -- Test: Budget Guard ---------------------------------------------


class TestBudgetGuard:
    """Test budget guard integration (R4 P0 fix).

    The dispatcher checks BudgetGuard.can_spend() before dispatching.
    When it returns False, the dispatcher returns exit_code=-6.
    These tests verify the BudgetGuard logic directly.
    """

    def test_dispatch_blocked_when_budget_exceeded(self, tmp_path):
        """When budget exceeded, can_spend returns False (-> exit_code=-6)."""
        from maop.model.budget import BudgetGuard
        from maop.model.schema import BudgetConfig

        config = BudgetConfig(daily_limit=1.0, monthly_limit=100.0, hard_stop=True)
        guard = BudgetGuard(root_dir=tmp_path, config=config)

        # Initially within budget
        assert guard.can_spend(estimated_cost=0.0)

        # Record spending that exceeds the daily limit
        guard.record(model="test-model", provider="test", cost=1.5)

        # Now budget should be exceeded
        assert not guard.can_spend(estimated_cost=0.0), (
            "can_spend should return False when daily budget is exceeded"
        )

    def test_budget_guard_no_hard_stop_allows_overspend(self, tmp_path):
        """When hard_stop=False, can_spend always returns True."""
        from maop.model.budget import BudgetGuard
        from maop.model.schema import BudgetConfig

        config = BudgetConfig(daily_limit=0.01, monthly_limit=0.01, hard_stop=False)
        guard = BudgetGuard(root_dir=tmp_path, config=config)
        guard.record(model="m", provider="p", cost=100.0)

        assert guard.can_spend(estimated_cost=50.0), (
            "can_spend should return True when hard_stop is disabled"
        )