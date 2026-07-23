"""MAOP Dashboard — Agent Platform Management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from maop.dashboard.error_handler import handle_api_errors
from maop.core.middleware import require_admin

router = APIRouter(prefix="/api/agents", tags=["agents"])


class RegisterAgentRequest(BaseModel):
    name: str = Field(max_length=100)
    cli_path: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    provider: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=100)
    driver: str = Field(default="cli", max_length=50)
    cli_args: str = Field(default="", max_length=500)
    timeout_s: int = Field(default=120, ge=1, le=3600)


_instance_cache: dict[str, Any] = {}


def _get_scanner():
    if "scanner" not in _instance_cache:
        from maop.core.agent_scanner import AgentScanner
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent.parent
        _instance_cache["scanner"] = AgentScanner(root_dir=str(root))
    return _instance_cache["scanner"]


def _get_registry():
    if "registry" not in _instance_cache:
        from maop.core.agent_registry import AgentRegistry
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent.parent
        _instance_cache["registry"] = AgentRegistry(root_dir=str(root))
    return _instance_cache["registry"]


def _get_matcher():
    if "matcher" not in _instance_cache:
        from maop.core.capability_matcher import CapabilityMatcher
        from maop.core.agent_registry import AgentRegistry
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent.parent
        registry = AgentRegistry(root_dir=str(root))
        _instance_cache["matcher"] = CapabilityMatcher(registry=registry)
    return _instance_cache["matcher"]


@router.get("")
@handle_api_errors
async def list_agents(
    enabled_only: bool = Query(False, description="Only enabled agents"),
    healthy_only: bool = Query(False, description="Only healthy agents"),
    capability: str = Query("", description="Filter by capability"),
    provider: str = Query("", description="Filter by provider"),
):
    registry = _get_registry()
    agents = registry.list_agents(
        enabled_only=enabled_only,
        healthy_only=healthy_only,
        capability=capability or "",
        provider=provider or "",
    )
    return {"agents": [a.model_dump() for a in agents]}


@router.get("/routes")
@handle_api_errors
async def get_agent_routes():
    registry = _get_registry()
    agents = registry.list_agents()
    routes = []
    for a in agents:
        routes.append({
            "name": a.name,
            "provider": getattr(a, "provider", ""),
            "model": getattr(a, "model", ""),
            "capabilities": getattr(a, "capabilities", []),
            "enabled": getattr(a, "enabled", True),
            "driver": getattr(a, "driver", "cli"),
        })
    return {"routes": routes}

@router.get("/match")
@handle_api_errors
async def match_agents(
    task: str = Query(..., description="Task description"),
    requirements: str = Query("", description="Comma-separated capabilities"),
    top_k: int = Query(5, ge=1, le=20),
):
    matcher = _get_matcher()
    reqs = [r.strip() for r in requirements.split(",") if r.strip()] if requirements else None
    scores = matcher.match(task=task, requirements=reqs, top_k=top_k)
    return {"matches": [s.model_dump() for s in scores]}

@router.get("/{name}")
@handle_api_errors
async def get_agent(name: str):
    registry = _get_registry()
    agent = registry.get_agent(name)
    if agent is None:
        return JSONResponse(status_code=404, content={"status": "error", "error": "Agent not found"})
    return {"status": "ok", "agent": agent.model_dump()}


@router.post("/scan")
@handle_api_errors
async def scan_agents(request: Request):
    require_admin(request)
    scanner = _get_scanner()
    registry = _get_registry()
    found = scanner.scan()
    synced = registry.sync_from_scanner(scanner)
    return {"scanned": len(found), "synced": synced, "agents": [a.model_dump() for a in found]}


@router.post("/{name}/health-check")
@handle_api_errors
async def check_agent_health(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    result = registry.health_check(name)
    return {"result": result.model_dump()}


@router.post("/health-check-all")
@handle_api_errors
async def check_all_health(request: Request):
    require_admin(request)
    registry = _get_registry()
    results = registry.health_check_all()
    return {"results": [r.model_dump() for r in results]}


@router.post("/{name}/enable")
@handle_api_errors
async def enable_agent(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    ok = registry.enable(name)
    return {"enabled": ok}


@router.post("/{name}/disable")
@handle_api_errors
async def disable_agent(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    ok = registry.disable(name)
    return {"disabled": ok}


@router.post("/register")
@handle_api_errors
async def register_agent(body: RegisterAgentRequest, request: Request):
    require_admin(request)
    from maop.core.agent_registry import RegisteredAgent
    registry = _get_registry()
    agent = RegisteredAgent(
        name=body.name,
        cli_path=body.cli_path,
        provider=body.provider,
        capabilities=body.capabilities,
        description=body.description,
        model=body.model,
        driver=body.driver,
        cli_args=body.cli_args,
        timeout_s=body.timeout_s,
        source="manual",
    )
    registry.register(agent)
    return {"agent": agent.model_dump()}


@router.delete("/{name}")
@handle_api_errors
async def unregister_agent(name: str, request: Request):
    require_admin(request)
    registry = _get_registry()
    ok = registry.unregister(name)
    return {"deleted": ok}




@router.get("/{name}/health-log")
@handle_api_errors
async def get_health_log(name: str, limit: int = Query(50, ge=1, le=200)):
    registry = _get_registry()
    log = registry.get_health_log(agent_name=name, limit=limit)
    return {"log": log}
