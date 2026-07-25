"""MAOP Enterprise n8n Integration.

Provides bidirectional integration with n8n workflow automation:
  - Outbound: MAOP agent triggers n8n workflow during execution
  - Inbound: n8n webhook calls MAOP /api/delegate for LLM processing

n8n is positioned as the "external trigger + SaaS integration layer":
  - Listens to 400+ SaaS events (GitHub/Slack/Jira/Email/etc.)
  - Calls MAOP for intelligent LLM processing at decision points
  - Distributes MAOP's results to external systems

This module is gated behind FeatureFlag.N8N_INTEGRATION (Enterprise only).
Personal edition cannot use n8n integration.

Architecture:
  ┌──────────────┐    webhook    ┌──────────────┐
  │  n8n         │ ────────────> │  MAOP        │
  │  (workflows) │ <───────────  │  (LLM brain) │
  └──────────────┘  HTTP trigger  └──────────────┘

Usage:
    from maop.enterprise.n8n import N8nClient, require_n8n_feature

    require_n8n_feature()  # raises FeatureNotAvailable in personal edition

    client = N8nClient(base_url="http://localhost:5678", api_key="...")
    execution = client.trigger_workflow("workflow-123", {"input": "data"})
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from maop.config.edition import FeatureFlag, has_feature, require_feature

logger = logging.getLogger(__name__)

__all__ = [
    "N8nClient",
    "N8nWorkflowExecution",
    "N8nWebhookPayload",
    "N8nIntegrationError",
    "require_n8n_feature",
]


class N8nIntegrationError(Exception):
    """Base exception for n8n integration errors."""


class N8nWorkflowExecution(BaseModel):
    """Represents an n8n workflow execution."""

    execution_id: str = Field(description="n8n execution ID")
    workflow_id: str = Field(description="n8n workflow ID")
    status: str = Field(description="Execution status: running|success|error|crashed")
    started_at: datetime = Field(description="When execution started")
    finished_at: datetime | None = Field(default=None, description="When execution finished")
    data: dict[str, Any] | None = Field(default=None, description="Execution output data")
    error: str | None = Field(default=None, description="Error message if failed")


class N8nWebhookPayload(BaseModel):
    """Payload received from n8n webhook."""

    workflow_id: str = Field(description="n8n workflow ID that triggered this webhook")
    execution_id: str = Field(description="n8n execution ID")
    event: str = Field(description="Event type (e.g., 'github.pr.opened', 'slack.message')")
    data: dict[str, Any] = Field(default_factory=dict, description="Event payload data")
    callback_url: str | None = Field(default=None, description="URL to POST results back to")


def require_n8n_feature() -> None:
    """Assert that n8n integration is available.

    Raises FeatureNotAvailable if:
      - Running in Personal edition
      - N8N_INTEGRATION feature flag is not enabled
    """
    require_feature(FeatureFlag.N8N_INTEGRATION)


def _parse_iso(dt_str: str) -> datetime:
    """Parse ISO 8601 datetime, tolerating a trailing Z suffix.
    Python < 3.11 datetime.fromisoformat rejects Z; n8n returns Z timestamps.
    """
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


class N8nClient:
    """HTTP client for n8n REST API.

    n8n API docs: https://docs.n8n.io/api/

    Used for outbound integration: MAOP triggers n8n workflows
    during agent execution (e.g., "after code review, create Jira ticket").
    """

    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        api_key: str = "",
        api_version: str = "v1",
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_version = api_version
        self._timeout_s = timeout_s
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["X-N8N-API-KEY"] = self._api_key
            self._client = httpx.Client(
                base_url=f"{self._base_url}/api/{self._api_version}",
                headers=headers,
                timeout=self._timeout_s,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "N8nClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def trigger_workflow(
        self,
        workflow_id: str,
        data: dict[str, Any] | None = None,
        wait_for_completion: bool = False,
    ) -> N8nWorkflowExecution:
        """Trigger an n8n workflow by ID.

        Parameters
        ----------
        workflow_id : str
            The n8n workflow ID to trigger.
        data : dict, optional
            Input data to pass to the workflow's trigger node.
        wait_for_completion : bool
            If True, block until workflow execution finishes.
            If False, return immediately with status="running".

        Returns
        -------
        N8nWorkflowExecution
            Execution info.
        """
        require_n8n_feature()

        client = self._get_client()
        payload = {"data": data or {}}

        try:
            if wait_for_completion:
                # Use webhook path for synchronous execution
                resp = client.post(
                    f"/workflows/{workflow_id}/execute",
                    json=payload,
                    params={"wait": "true"},
                )
            else:
                # Async: just activate the workflow trigger
                resp = client.post(f"/workflows/{workflow_id}/execute", json=payload)

            resp.raise_for_status()
            result = resp.json()

            return N8nWorkflowExecution(
                execution_id=str(result.get("executionId", "")),
                workflow_id=workflow_id,
                status=result.get("status", "running"),
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc) if result.get("status") == "success" else None,
                data=result.get("data"),
                error=result.get("error"),
            )

        except httpx.HTTPStatusError as exc:
            raise N8nIntegrationError(
                f"n8n API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise N8nIntegrationError(f"Failed to connect to n8n at {self._base_url}: {exc}") from exc

    def get_execution(self, execution_id: str) -> N8nWorkflowExecution:
        """Get the status of a workflow execution."""
        require_n8n_feature()

        client = self._get_client()
        try:
            resp = client.get(f"/executions/{execution_id}")
            resp.raise_for_status()
            result = resp.json()

            return N8nWorkflowExecution(
                execution_id=str(result.get("id", execution_id)),
                workflow_id=str(result.get("workflowId", "")),
                status=result.get("status", "unknown"),
                started_at=_parse_iso(result["startedAt"]) if result.get("startedAt") else datetime.now(timezone.utc),
                finished_at=_parse_iso(result["finishedAt"]) if result.get("finishedAt") else None,
                data=result.get("data"),
                error=result.get("error"),
            )
        except httpx.HTTPStatusError as exc:
            raise N8nIntegrationError(
                f"n8n API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise N8nIntegrationError(f"Failed to connect to n8n: {exc}") from exc

    def list_workflows(self, limit: int = 100) -> list[dict[str, Any]]:
        """List all workflows in n8n."""
        require_n8n_feature()

        client = self._get_client()
        try:
            resp = client.get("/workflows", params={"limit": limit})
            resp.raise_for_status()
            return resp.json().get("data", [])
        except httpx.HTTPStatusError as exc:
            raise N8nIntegrationError(
                f"n8n API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise N8nIntegrationError(f"Failed to connect to n8n: {exc}") from exc

    def health_check(self) -> bool:
        """Check if n8n is reachable."""
        try:
            client = self._get_client()
            resp = client.get("/workflows", params={"limit": 1})
            return resp.status_code == 200
        except Exception:
            return False


def handle_n8n_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process an inbound webhook from n8n.

    This function is called when n8n sends a webhook to MAOP
    (e.g., "GitHub PR opened → n8n → MAOP for code review").

    The function:
      1. Validates the payload
      2. Parses the event type and data
      3. Returns a response that n8n can use in subsequent nodes

    The actual LLM processing is done by the caller (typically a
    FastAPI route handler that calls /api/delegate internally).

    Parameters
    ----------
    payload : dict
        The JSON body received from n8n.

    Returns
    -------
    dict
        Response to send back to n8n. Contains:
          - status: "accepted" | "rejected"
          - event: the parsed event type
          - data: the parsed event data
          - delegate_hint: suggested agent + task for MAOP processing
    """
    require_n8n_feature()

    try:
        webhook = N8nWebhookPayload(**payload)
    except Exception as exc:
        logger.warning("[n8n] Invalid webhook payload: %s", exc)
        return {"status": "rejected", "error": f"Invalid payload: {exc}"}

    # Parse event type to suggest an MAOP agent
    delegate_hint = _suggest_agent_for_event(webhook.event)

    logger.info(
        "[n8n] Webhook received: event=%s workflow=%s execution=%s",
        webhook.event, webhook.workflow_id, webhook.execution_id,
    )

    return {
        "status": "accepted",
        "event": webhook.event,
        "execution_id": webhook.execution_id,
        "workflow_id": webhook.workflow_id,
        "data": webhook.data,
        "delegate_hint": delegate_hint,
    }


def _suggest_agent_for_event(event: str) -> dict[str, str]:
    """Suggest an MAOP agent + task based on the n8n event type.

    This is a heuristic mapping. Users can override the suggested agent
    in their n8n workflow configuration.
    """
    event_lower = event.lower()

    if "github" in event_lower and ("pr" in event_lower or "pull_request" in event_lower):
        return {"agent": "claude", "task": "Review this pull request", "capability": "review"}
    if "github" in event_lower and "issue" in event_lower:
        return {"agent": "claude", "task": "Analyze this GitHub issue", "capability": "planning"}
    if "slack" in event_lower and "message" in event_lower:
        return {"agent": "claude", "task": "Analyze this Slack message", "capability": "chat"}
    if "jira" in event_lower or "ticket" in event_lower:
        return {"agent": "claude", "task": "Analyze this Jira ticket", "capability": "planning"}
    if "email" in event_lower:
        return {"agent": "claude", "task": "Analyze this email", "capability": "chat"}
    if "commit" in event_lower:
        return {"agent": "claude", "task": "Review this commit", "capability": "review"}

    return {"agent": "claude", "task": "Process this event", "capability": "chat"}