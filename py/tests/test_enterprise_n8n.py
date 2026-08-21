"""Tests for MAOP Enterprise n8n integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("maop.enterprise")

from maop.enterprise.n8n import (
    N8nClient,
    N8nIntegrationError,
    N8nWebhookPayload,
    N8nWorkflowExecution,
    handle_n8n_webhook,
    require_n8n_feature,
)

from maop.config.edition import (
    Edition,
    FeatureFlag,
    reset_edition,
    set_edition,
    set_feature_override,
)


@pytest.fixture(autouse=True)
def enterprise_edition():
    """Enable enterprise edition + n8n feature for all tests."""
    reset_edition()
    set_edition(Edition.ENTERPRISE)
    set_feature_override(FeatureFlag.N8N_INTEGRATION, True)
    yield
    reset_edition()


class TestN8nFeatureGate:
    """Test that n8n integration is properly gated behind Enterprise feature."""

    def test_personal_edition_cannot_use_n8n(self):
        """Personal edition should reject n8n feature."""
        reset_edition()
        set_edition(Edition.PERSONAL)
        from maop.config.edition import FeatureNotAvailable
        with pytest.raises(FeatureNotAvailable):
            require_n8n_feature()

    def test_enterprise_edition_can_use_n8n(self):
        """Enterprise edition with feature flag should allow n8n."""
        # fixture already set enterprise + n8n feature
        require_n8n_feature()  # should not raise

    def test_enterprise_without_feature_flag_cannot_use_n8n(self):
        """Enterprise edition without N8N_INTEGRATION flag should reject."""
        reset_edition()
        set_edition(Edition.ENTERPRISE)
        set_feature_override(FeatureFlag.N8N_INTEGRATION, False)
        from maop.config.edition import FeatureNotAvailable
        with pytest.raises(FeatureNotAvailable):
            require_n8n_feature()
        set_feature_override(FeatureFlag.N8N_INTEGRATION, True)


class TestN8nWebhookPayload:
    """Test webhook payload parsing."""

    def test_valid_payload(self):
        payload = N8nWebhookPayload(
            workflow_id="wf-123",
            execution_id="exec-456",
            event="github.pr.opened",
            data={"repo": "owner/repo", "pr_number": 42},
        )
        assert payload.workflow_id == "wf-123"
        assert payload.event == "github.pr.opened"
        assert payload.data["pr_number"] == 42

    def test_callback_url_optional(self):
        payload = N8nWebhookPayload(
            workflow_id="wf-123",
            execution_id="exec-456",
            event="slack.message",
        )
        assert payload.callback_url is None


class TestHandleWebhook:
    """Test webhook handler."""

    def test_valid_webhook(self):
        payload = {
            "workflow_id": "wf-123",
            "execution_id": "exec-456",
            "event": "github.pr.opened",
            "data": {"repo": "owner/repo", "pr_number": 42},
        }
        result = handle_n8n_webhook(payload)
        assert result["status"] == "accepted"
        assert result["event"] == "github.pr.opened"
        assert result["delegate_hint"]["agent"] == "claude"
        assert result["delegate_hint"]["capability"] == "review"

    def test_invalid_webhook_returns_rejected(self):
        payload = {"invalid": "payload"}
        result = handle_n8n_webhook(payload)
        assert result["status"] == "rejected"
        assert "error" in result

    def test_event_suggestion_mapping(self):
        """Test that different events map to different agent suggestions."""
        test_cases = [
            ("github.pr.opened", "review"),
            ("github.issue.created", "planning"),
            ("slack.message.received", "chat"),
            ("jira.ticket.updated", "planning"),
            ("email.received", "chat"),
            ("git.commit.pushed", "review"),
            ("unknown.event", "chat"),
        ]
        for event, expected_capability in test_cases:
            payload = {
                "workflow_id": "wf-1",
                "execution_id": "exec-1",
                "event": event,
                "data": {},
            }
            result = handle_n8n_webhook(payload)
            assert result["delegate_hint"]["capability"] == expected_capability, \
                f"Event {event} should map to {expected_capability}"


class TestN8nClient:
    """Test N8nClient (with mocked HTTP)."""

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_trigger_workflow_success(self, mock_client_class):
        """Test triggering a workflow."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "executionId": "exec-123",
            "status": "running",
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = N8nClient(base_url="http://localhost:5678", api_key="test-key")
        execution = client.trigger_workflow("wf-123", data={"input": "test"})

        assert execution.execution_id == "exec-123"
        assert execution.workflow_id == "wf-123"
        assert execution.status == "running"

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_trigger_workflow_api_error(self, mock_client_class):
        """Test that API errors are wrapped in N8nIntegrationError."""
        import httpx

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Workflow not found"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response,
        )
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = N8nClient()
        with pytest.raises(N8nIntegrationError):
            client.trigger_workflow("nonexistent")

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_get_execution(self, mock_client_class):
        """Test getting execution status."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "exec-123",
            "workflowId": "wf-123",
            "status": "success",
            "startedAt": "2026-07-25T10:00:00Z",
            "finishedAt": "2026-07-25T10:00:05Z",
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = N8nClient()
        execution = client.get_execution("exec-123")

        assert execution.execution_id == "exec-123"
        assert execution.status == "success"
        assert execution.finished_at is not None

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_list_workflows(self, mock_client_class):
        """Test listing workflows."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "wf-1", "name": "GitHub PR Review"},
                {"id": "wf-2", "name": "Slack Notification"},
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = N8nClient()
        workflows = client.list_workflows()
        assert len(workflows) == 2
        assert workflows[0]["id"] == "wf-1"

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_health_check_success(self, mock_client_class):
        """Test health check returns True when n8n is reachable."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = N8nClient()
        assert client.health_check() is True

    @patch("maop.enterprise.n8n.httpx.Client")
    def test_health_check_failure(self, mock_client_class):
        """Test health check returns False when n8n is unreachable."""
        mock_client_class.side_effect = Exception("Connection refused")

        client = N8nClient()
        assert client.health_check() is False

    def test_context_manager(self):
        """Test that N8nClient works as a context manager."""
        with N8nClient() as client:
            assert client is not None
        # After exit, client should be closed
        assert client._client is None

    def test_api_key_in_headers(self):
        """Test that API key is included in headers when provided."""

        client = N8nClient(api_key="secret-key")
        http_client = client._get_client()
        assert http_client.headers["X-N8N-API-KEY"] == "secret-key"
        client.close()

    def test_no_api_key_no_header(self):
        """Test that no API key header is added when key is empty."""
        client = N8nClient(api_key="")
        http_client = client._get_client()
        assert "X-N8N-API-KEY" not in http_client.headers
        client.close()


class TestN8nWorkflowExecution:
    """Test N8nWorkflowExecution model."""

    def test_model_creation(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        execution = N8nWorkflowExecution(
            execution_id="exec-1",
            workflow_id="wf-1",
            status="success",
            started_at=now,
            finished_at=now,
            data={"result": "ok"},
        )
        assert execution.status == "success"
        assert execution.data == {"result": "ok"}
