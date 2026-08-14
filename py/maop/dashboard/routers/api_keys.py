"""API Key management endpoints.

Provides CRUD + usage statistics for MAOP API keys. All write operations
require admin role. Read operations (list/get/usage) also require admin
since keys are sensitive metadata.

Routes (prefix ``/api/api-keys``):
  POST   /                  create key (returns plaintext once)
  GET    /                  list keys
  GET    /{key_id}          get key detail
  DELETE /{key_id}          hard-delete key + usage
  POST   /{key_id}/revoke   soft-revoke key
  GET    /{key_id}/usage    paginated usage stats
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request


from maop.core.security.api_key_manager import (
    ApiKeyCreate,
    ApiKeyCreateResult,
    ApiKeyResponse,
    ApiKeyUpdate,
    ApiKeyUsageResponse,
    get_api_key_manager,
)
from maop.core.security.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _get_manager(request: Request) -> Any:
    """Return the ApiKeyManager from app.state, falling back to the global singleton."""
    mgr = getattr(request.app.state, "api_key_manager", None)
    if mgr is not None:
        return mgr
    return get_api_key_manager()


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction (respects MAOP_TRUST_PROXY)."""
    import os

    if os.environ.get("MAOP_TRUST_PROXY", "0") == "1":
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _actor(request: Request) -> str:
    """Identity of the current caller for audit fields."""
    return getattr(request.state, "auth_identity", "") or ""


# ── Create ────────────────────────────────────────────────────────


@router.post("", response_model=ApiKeyCreateResult, status_code=201)
@router.post("/", response_model=ApiKeyCreateResult, status_code=201, include_in_schema=False)
async def create_api_key(body: ApiKeyCreate, request: Request) -> ApiKeyCreateResult:
    """Create a new API key. The plaintext key is returned **only** here."""
    require_admin(request)
    mgr = _get_manager(request)
    # Stamp created_by with the caller identity if not explicitly provided.
    if not body.created_by:
        body.created_by = _actor(request)
    result = mgr.create_key(body)
    logger.info(
        "[api-keys] Created key id=%s name=%s by=%s",
        result.key_id, body.name, body.created_by,
    )
    return result


# ── List ──────────────────────────────────────────────────────────


@router.get("", response_model=list[ApiKeyResponse])
@router.get("/", response_model=list[ApiKeyResponse], include_in_schema=False)
async def list_api_keys(
    request: Request,
    tenant_id: str = Query("", description="Filter by tenant_id"),
) -> list[ApiKeyResponse]:
    """List all API keys (optionally filtered by tenant)."""
    require_admin(request)
    return _get_manager(request).list_keys(tenant_id=tenant_id)


# ── Get detail ────────────────────────────────────────────────────


@router.get("/{key_id}", response_model=ApiKeyResponse)
async def get_api_key(key_id: str, request: Request) -> ApiKeyResponse:
    """Get a single API key by its key_id."""
    require_admin(request)
    key = _get_manager(request).get_key(key_id)
    if key is None:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    return key


# ── Revoke ────────────────────────────────────────────────────────


@router.post("/{key_id}/revoke")
async def revoke_api_key(key_id: str, request: Request) -> dict[str, Any]:
    """Soft-revoke an API key (enabled=0, revoked_at set)."""
    require_admin(request)
    actor = _actor(request)
    ok = _get_manager(request).revoke_key(key_id, revoked_by=actor)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"API key not found or already revoked: {key_id}",
        )
    return {"status": "ok", "message": f"Key {key_id} revoked", "revoked_by": actor}


# ── Update ────────────────────────────────────────────────────────


@router.put("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(key_id: str, body: ApiKeyUpdate, request: Request) -> ApiKeyResponse:
    """Update editable metadata of an API key (name/scopes/rate_limit/ip_whitelist)."""
    require_admin(request)
    key = _get_manager(request).update_key(key_id, body)
    if key is None:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    logger.info("[api-keys] Updated key id=%s", key_id)
    return key


# ── Delete ────────────────────────────────────────────────────────


@router.delete("/{key_id}")
async def delete_api_key(key_id: str, request: Request) -> dict[str, Any]:
    """Hard-delete an API key and all its usage records."""
    require_admin(request)
    ok = _get_manager(request).delete_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    return {"status": "ok", "message": f"Key {key_id} deleted"}


# ── Usage stats ───────────────────────────────────────────────────


@router.get("/{key_id}/usage", response_model=ApiKeyUsageResponse)
async def get_api_key_usage(
    key_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ApiKeyUsageResponse:
    """Return paginated usage records plus in-window request count."""
    require_admin(request)
    mgr = _get_manager(request)
    # Verify the key exists (404 if not) before reporting usage.
    if mgr.get_key(key_id) is None:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    return mgr.get_usage(key_id, limit=limit, offset=offset)


# ── Validate (debug helper) ───────────────────────────────────────


@router.post("/validate")
async def validate_api_key(request: Request) -> dict[str, Any]:
    """Validate a plaintext key without recording usage.

    Body: ``{"key": "...", "scope": "read", "ip": "1.2.3.4"}``
    All fields except ``key`` are optional. Admin-only.
    """
    require_admin(request)
    body = await request.json()
    plaintext = body.get("key", "")
    scope = body.get("scope", "")
    ip = body.get("ip", "")
    result = _get_manager(request).validate_key(plaintext, client_ip=ip, required_scope=scope)
    return result.model_dump()