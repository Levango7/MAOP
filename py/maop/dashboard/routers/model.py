"""Model management endpoints for MAOP Dashboard."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/model/agents")
@handle_api_errors("Model agents", error_value={"agents": [], "count": 0, "error": "Model agents failed"})
async def api_model_agents() -> dict[str, Any]:
    from maop.config.loader import ConfigLoader
    cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
    agents = []
    for name, ad in cfg.agents.items():
        cli_path = shutil.which(ad.cli) if ad.cli else None
        agents.append({"name": name, "cli": ad.cli, "driver": ad.driver,
            "model": getattr(ad, "model", ""), "timeout_s": ad.timeout_s,
            "capabilities": ad.capabilities, "description": ad.description,
            "cli_available": cli_path is not None, "cli_path": cli_path or ""})
    return {"agents": agents, "count": len(agents)}

@router.get("/api/model/quota")
async def api_model_quota() -> dict[str, Any]:
    agents_cfg = []
    try:
        from maop.config.loader import ConfigLoader
        cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
        for name, ad in cfg.agents.items():
            cli_path = shutil.which(ad.cli) if ad.cli else None
            agents_cfg.append({"agent": name, "cli": ad.cli, "model": getattr(ad, "model", ""),
                "available": cli_path is not None, "cli_path": cli_path or "", "driver": ad.driver})
    except Exception:
        logger.debug("Failed to load agent config", exc_info=True)
    return {"agents": agents_cfg, "count": len(agents_cfg)}

@router.post("/api/model/switch")
@handle_api_errors("Model switch", error_value={"status": "error", "error": "Model switch failed"})
async def api_model_switch(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    new_model = body.get("model", "")
    if not agent_name or not new_model:
        raise HTTPException(400, "missing agent or model")
    ypath = MAOP_ROOT / "agents.yaml"
    if not ypath.exists():
        ypath = MAOP_ROOT / "config" / "agents.yaml"
    if not ypath.exists():
        return {"status": "error", "error": "agents.yaml not found"}
    import yaml
    _text = await asyncio.to_thread(Path(ypath).read_text, encoding="utf-8")
    data = yaml.safe_load(_text)
    agents = data.get("agents", {})
    if agent_name not in agents:
        return {"status": "error", "error": f"Unknown agent: {agent_name}"}
    mpath = MAOP_ROOT / "models.yaml"
    if not mpath.exists():
        mpath = MAOP_ROOT / "config" / "models.yaml"
    if mpath.exists():
        import yaml as _yaml
        _mtext = await asyncio.to_thread(Path(mpath).read_text, encoding="utf-8")
        mdata = _yaml.safe_load(_mtext)
        valid_models = set(mdata.get("models", {}).keys()) if isinstance(mdata, dict) else set()
        if valid_models and new_model not in valid_models:
            return {"status": "error", "error": f"Unknown model: {new_model}. Valid: {sorted(valid_models)}"}
    agents[agent_name]["model"] = new_model
    _dumped = yaml.dump(data, allow_unicode=True, default_flow_style=False)
    await asyncio.to_thread(Path(ypath).write_text, _dumped, encoding="utf-8")
    return {"status": "ok", "agent": agent_name, "model": new_model}

# ── Model Management v2 (ModelRegistry-backed) ────────────────────
_model_registry = None

def _get_model_registry() -> Any:
    global _model_registry
    if _model_registry is None:
        from maop.model.registry import ModelRegistry
        _model_registry = ModelRegistry(project_root=str(MAOP_ROOT))
    return _model_registry

@router.get("/api/model/registry")
@handle_api_errors("Model registry", error_value={"status": "error", "error": "Model registry failed"})
async def api_model_registry() -> dict[str, Any]:
    return {"status": "ok", "stats": _get_model_registry().stats()}

@router.get("/api/model/list")
@handle_api_errors("Model list", error_value={"models": [], "count": 0, "error": "Model list failed"})
async def api_model_list() -> dict[str, Any]:
    reg = _get_model_registry()
    models = []
    for m in reg.list_models(enabled_only=False):
        models.append({"name": m.name, "provider": m.provider, "family": m.family,
            "context_window": m.context_window, "max_output": m.max_output,
            "cost_per_1k_input": m.cost_per_1k_input, "cost_per_1k_output": m.cost_per_1k_output,
            "capabilities": m.capabilities, "latency_tier": m.latency_tier.value,
            "quality_tier": m.quality_tier.value, "enabled": m.enabled,
            "provider_healthy": reg.providers.is_healthy(m.provider)})
    return {"models": models, "count": len(models)}

@router.get("/api/model/providers")
@handle_api_errors("Model providers", error_value={"providers": [], "error": "Model providers failed"})
async def api_model_providers() -> dict[str, Any]:
    return {"providers": _get_model_registry().providers.list_providers()}

@router.get("/api/model/select")
@handle_api_errors("Model select", error_value={"status": "error", "error": "Model select failed"})
async def api_model_select(capability: str = "", agent_model: str = "", policy: str = "default") -> dict[str, Any]:
    from maop.model.selector import ModelSelector
    em = ModelSelector(_get_model_registry()).select(capability=capability, agent_model=agent_model, policy_name=policy)
    return {"status": "ok", "effective_model": em.model_dump()}

@router.get("/api/model/budget")
@handle_api_errors("Model budget", error_value={"status": "error", "error": "Model budget failed"})
async def api_model_budget() -> dict[str, Any]:
    from maop.model.budget import BudgetGuard
    reg = _get_model_registry()
    return {"status": "ok", "budget": BudgetGuard(root_dir=str(MAOP_ROOT), config=reg.config.budget).stats()}

@router.get("/api/model/quota/status")
@handle_api_errors("Model quota", error_value={"status": "error", "error": "Model quota failed"})
async def api_model_quota_status() -> dict[str, Any]:
    from maop.model.quota import QuotaEnforcer
    return {"status": "ok", "quotas": QuotaEnforcer(_get_model_registry()).usage_all()}

@router.get("/api/model/policies")
@handle_api_errors("Model policies", error_value={"policies": [], "count": 0, "error": "Model policies failed"})
async def api_model_policies() -> dict[str, Any]:
    reg = _get_model_registry()
    policies = []
    for name, p in reg.config.policies.items():
        policies.append({"name": name, "strategy": p.strategy.value, "max_cost_per_task": p.max_cost_per_task,
            "prefer_low_latency": p.prefer_low_latency, "fallback_on_error": p.fallback_on_error,
            "fallback_on_timeout": p.fallback_on_timeout})
    return {"policies": policies, "count": len(policies)}

# ── Provider CRUD ────────────────────────────────────────────────────

@router.post("/api/model/provider/add")
@handle_api_errors("Provider add", error_value={"status": "error", "error": "Provider add failed"})
async def api_provider_add(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "missing provider name")
    from maop.model.schema import ProviderDef
    pdef = ProviderDef(**{k: v for k, v in body.items() if k != "name"})
    reg = _get_model_registry()
    reg.add_provider(name, pdef)
    reg.save()
    return {"status": "ok", "provider": name}

@router.post("/api/model/provider/delete")
@handle_api_errors("Provider delete", error_value={"status": "error", "error": "Provider delete failed"})
async def api_provider_delete(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "missing provider name")
    reg = _get_model_registry()
    try:
        reg.remove_provider(name)
        reg.save()
        return {"status": "ok", "removed": name}
    except ValueError as exc:
        raise HTTPException(409, str(exc))

# ── Model CRUD ───────────────────────────────────────────────────────

@router.post("/api/model/add")
@handle_api_errors("Model add", error_value={"status": "error", "error": "Model add failed"})
async def api_model_add(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "missing model name")
    from maop.model.schema import ModelDef
    mdef = ModelDef(**{k: v for k, v in body.items() if k != "name"})
    reg = _get_model_registry()
    reg.add_model(name, mdef)
    reg.save()
    return {"status": "ok", "model": name}

@router.post("/api/model/delete")
@handle_api_errors("Model delete", error_value={"status": "error", "error": "Model delete failed"})
async def api_model_delete(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    name = body.get("name", "")
    if not name:
        raise HTTPException(400, "missing model name")
    reg = _get_model_registry()
    removed = reg.remove_model(name)
    if not removed:
        raise HTTPException(404, f"Model {name} not found")
    reg.save()
    return {"status": "ok", "removed": name}

# ── API Key Vault ────────────────────────────────────────────────────

_api_key_vault = None

def _get_api_key_vault() -> Any:
    global _api_key_vault
    if _api_key_vault is None:
        from maop.core.security.api_key_vault import ApiKeyVault
        _api_key_vault = ApiKeyVault(root_dir=str(MAOP_ROOT))
    return _api_key_vault

@router.post("/api/model/key/store")
@handle_api_errors("Key store", error_value={"status": "error", "error": "Key store failed"})
async def api_key_store(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    provider = body.get("provider", "")
    api_key = body.get("api_key", "")
    if not provider or not api_key:
        raise HTTPException(400, "missing provider or api_key")
    vault = _get_api_key_vault()
    vault.store(provider, api_key)
    return {"status": "ok", "provider": provider}

@router.post("/api/model/key/delete")
@handle_api_errors("Key delete", error_value={"status": "error", "error": "Key delete failed"})
async def api_key_delete(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    provider = body.get("provider", "")
    if not provider:
        raise HTTPException(400, "missing provider")
    vault = _get_api_key_vault()
    deleted = vault.delete(provider)
    return {"status": "ok" if deleted else "not_found", "provider": provider}

@router.get("/api/model/key/list")
@handle_api_errors("Key list", error_value={"providers": [], "error": "Key list failed"})
async def api_key_list() -> dict[str, Any]:
    vault = _get_api_key_vault()
    return {"providers": vault.list_providers()}

# ── Provider Health Check ────────────────────────────────────────────

@router.post("/api/model/health/check")
@handle_api_errors("Health check", error_value={"status": "error", "error": "Health check failed"})
async def api_health_check(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    provider = body.get("provider", "")
    reg = _get_model_registry()
    vault = _get_api_key_vault()
    from maop.core.routing.provider_health import ProviderHealthChecker
    checker = ProviderHealthChecker(registry=reg, vault=vault)
    if provider:
        result = await checker.check(provider)
        return {"status": "ok", "result": result.model_dump()}
    results = await checker.check_all()
    return {"status": "ok", "results": [r.model_dump() for r in results]}
