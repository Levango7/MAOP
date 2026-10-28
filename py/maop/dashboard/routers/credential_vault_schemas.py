"""Response models for the credential-vault endpoints the dashboard calls.

``CredentialSummary`` (``maop/core/agent/auth/credential_vault.py``) is the
vault's own masked view — never the plaintext ``Credential`` — and
``credential_service.list_credentials`` serialises it with
``model_dump(mode="json")``, so it is reused verbatim here.

``DELETE /credentials/{id}`` echoes the deleted **id** (a string), not a bool;
using the notifications ``DeleteEnvelope`` (``deleted: bool``) would fail
validation at runtime.
"""

from __future__ import annotations

from pydantic import BaseModel

from maop.core.agent.auth.credential_vault import CredentialSummary


class CredentialListEnvelope(BaseModel):
    """``GET /api/credential-vault/credentials`` — masked summaries only."""

    status: str = "error"
    credentials: list[CredentialSummary] = []
    count: int = 0
    error: str = ""


class CredentialCreateEnvelope(BaseModel):
    """``POST /api/credential-vault/credentials`` — new credential id."""

    status: str = "error"
    credential_id: str = ""
    error: str = ""


class CredentialDeleteEnvelope(BaseModel):
    """``DELETE /api/credential-vault/credentials/{id}`` — echoes the id."""

    status: str = "error"
    deleted: str = ""
    error: str = ""


class CredentialRotateEnvelope(BaseModel):
    """``POST /api/credential-vault/credentials/{id}/rotate`` — rotated id."""

    status: str = "error"
    credential_id: str = ""
    error: str = ""
