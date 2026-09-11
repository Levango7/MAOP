"""Model management endpoints for MAOP Dashboard."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.model.schema import ModelDef, ProviderDef  # 批次3A: 模块级导入以支持 Pydantic 模型继承

from .state import MAOP_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic 请求模型 (P2-#4 fix: 输入校验) ────────────────────────

class ModelSwitchRequest(BaseModel):
    agent: str = ""
    model: str = ""


class ProviderNameRequest(BaseModel):
    name: str = ""


class ModelNameRequest(BaseModel):
    name: str = ""


class KeyStoreRequest(BaseModel):
    provider: str = ""
    api_key: str = ""


class KeyDeleteRequest(BaseModel):
    provider: str = ""


class HealthCheckRequest(BaseModel):
    provider: str = ""


# 批次3A: Provider/Model 添加端点的请求模型。
# 继承自 ProviderDef/ModelDef 并添加 name 字段，由 FastAPI 自动校验所有字段类型。
class ProviderAddRequest(ProviderDef):
    """POST /api/model/provider/add 请求体。

    继承 ProviderDef 全部字段并添加 name，由 FastAPI 自动校验。
    """
    name: str = ""


class ModelAddRequest(ModelDef):
    """POST /api/model/add 请求体。

    继承 ModelDef 全部字段并添加 name，由 FastAPI 自动校验。
    """
    name: str = ""

@router.get("/api/model/agents")
@handle_api_errors("Model agents", error_value={"agents": [], "count": 0, "error": "Model agents failed"})
async def api_model_agents(request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.config.loader import ConfigLoader
    cfg = ConfigLoader(project_root=str(MAOP_ROOT)).load()
    agents = []
    for name, ad in cfg.agents.items():
        cli_path = shutil.which(ad.cli) if ad.cli else None
        agents.append({"name": name, "cli": ad.cli, "driver": ad.driver,
            "model": getattr(ad, "model", ""), "timeout_s": ad.timeout_s,
            "capabilities": ad.capabilities, "description": ad.description,
            "cli_available": cli_path is not None, "cli_path": cli_path or ""})
    return {"status": "ok", "agents": agents, "count": len(agents)}

@router.get("/api/model/quota")
@handle_api_errors("Model quota", error_value={"agents": [], "count": 0, "error": "Model quota failed"})
async def api_model_quota(request: Request) -> dict[str, Any]:
    require_admin(request)
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
    return {"status": "ok", "agents": agents_cfg, "count": len(agents_cfg)}

@router.post("/api/model/switch")
@handle_api_errors("Model switch", error_value={"status": "error", "error": "Model switch failed"})
async def api_model_switch(body: ModelSwitchRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    agent_name = body.agent
    new_model = body.model
    if not agent_name or not new_model:
        raise HTTPException(400, "missing agent or model")
    ypath = MAOP_ROOT / "agents.yaml"
    if not ypath.exists():
        ypath = MAOP_ROOT / "config" / "agents.yaml"
    if not ypath.exists():
        # 批次3A: 统一响应格式为 {status, error}，消除 {success, message, data} 混用。
        raise HTTPException(status_code=404, detail="agents.yaml not found")
    import yaml
    _text = await asyncio.to_thread(Path(ypath).read_text, encoding="utf-8")
    data = yaml.safe_load(_text)
    agents = data.get("agents", {})
    if agent_name not in agents:
        # 批次3A: 统一响应格式为 {status, error}，消除 {success, message, data} 混用。
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")
    mpath = MAOP_ROOT / "models.yaml"
    if not mpath.exists():
        mpath = MAOP_ROOT / "config" / "models.yaml"
    if mpath.exists():
        import yaml as _yaml
        _mtext = await asyncio.to_thread(Path(mpath).read_text, encoding="utf-8")
        mdata = _yaml.safe_load(_mtext)
        valid_models = set(mdata.get("models", {}).keys()) if isinstance(mdata, dict) else set()
        if valid_models and new_model not in valid_models:
            # 批次3A: 统一响应格式为 {status, error}，消除 {success, message, data} 混用。
            raise HTTPException(status_code=404, detail=f"Unknown model: {new_model}. Valid: {sorted(valid_models)}")

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
async def api_model_registry(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", "stats": _get_model_registry().stats()}

@router.get("/api/model/list")
@handle_api_errors("Model list", error_value={"models": [], "count": 0, "error": "Model list failed"})
async def api_model_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    reg = _get_model_registry()
    models = []
    for m in reg.list_models(enabled_only=False):
        models.append({"name": m.name, "provider": m.provider, "family": m.family,
            "context_window": m.context_window, "max_output": m.max_output,
            "cost_per_1k_input": m.cost_per_1k_input, "cost_per_1k_output": m.cost_per_1k_output,
            "capabilities": m.capabilities, "latency_tier": m.latency_tier.value,
            "quality_tier": m.quality_tier.value, "enabled": m.enabled,
            "provider_healthy": reg.providers.is_healthy(m.provider)})
    return {"status": "ok", "models": models, "count": len(models)}

@router.get("/api/model/providers")
@handle_api_errors("Model providers", error_value={"providers": [], "error": "Model providers failed"})
async def api_model_providers(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", "providers": _get_model_registry().providers.list_providers()}

@router.get("/api/model/select")
@handle_api_errors("Model select", error_value={"status": "error", "error": "Model select failed"})
async def api_model_select(request: Request, capability: str = "", agent_model: str = "", policy: str = "default") -> dict[str, Any]:
    require_admin(request)
    from maop.model.selector import ModelSelector
    em = ModelSelector(_get_model_registry()).select(capability=capability, agent_model=agent_model, policy_name=policy)
    return {"status": "ok", "effective_model": em.model_dump()}

@router.get("/api/model/budget")
@handle_api_errors("Model budget", error_value={"status": "error", "error": "Model budget failed"})
async def api_model_budget(request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.model.budget import BudgetGuard
    reg = _get_model_registry()
    return {"status": "ok", "budget": BudgetGuard(root_dir=str(MAOP_ROOT), config=reg.config.budget).stats()}

@router.get("/api/model/quota/status")
@handle_api_errors("Model quota", error_value={"status": "error", "error": "Model quota failed"})
async def api_model_quota_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.model.quota import QuotaEnforcer
    return {"status": "ok", "quotas": QuotaEnforcer(_get_model_registry()).usage_all()}

@router.get("/api/model/policies")
@handle_api_errors("Model policies", error_value={"policies": [], "count": 0, "error": "Model policies failed"})
async def api_model_policies(request: Request) -> dict[str, Any]:
    require_admin(request)
    reg = _get_model_registry()
    policies = []
    for name, p in reg.config.policies.items():
        policies.append({"name": name, "strategy": p.strategy.value, "max_cost_per_task": p.max_cost_per_task,
            "prefer_low_latency": p.prefer_low_latency, "fallback_on_error": p.fallback_on_error,
            "fallback_on_timeout": p.fallback_on_timeout})
    return {"status": "ok", "policies": policies, "count": len(policies)}

# ── Provider CRUD ────────────────────────────────────────────────────

@router.post("/api/model/provider/add")
@handle_api_errors("Provider add", error_value={"status": "error", "error": "Provider add failed"})
async def api_provider_add(request: Request, body: ProviderAddRequest) -> dict[str, Any]:
    # 批次3A: 用 Pydantic ProviderAddRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（name + ProviderDef 全部字段类型）。
    require_admin(request)
    name = body.name
    if not name:
        raise HTTPException(400, "missing provider name")
    # 构造 ProviderDef（排除 name 字段）
    pdef = ProviderDef(**{k: v for k, v in body.model_dump().items() if k != "name"})
    reg = _get_model_registry()
    reg.add_provider(name, pdef)
    reg.save()
    return {"status": "ok", "provider": name}

@router.post("/api/model/provider/delete")
@handle_api_errors("Provider delete", error_value={"status": "error", "error": "Provider delete failed"})
async def api_provider_delete(body: ProviderNameRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
    if not name:
        raise HTTPException(400, "missing provider name")
    reg = _get_model_registry()
    try:
        reg.remove_provider(name)
        reg.save()
        return {"status": "ok", "removed": name}
    except ValueError as exc:
        # 批次3A: 脱敏——ValueError 细节不暴露给客户端，仅日志记录。
        logger.warning("[model] Remove provider failed: %s", exc)
        raise HTTPException(409, "Provider removal failed") from exc

# ── Model CRUD ───────────────────────────────────────────────────────

@router.post("/api/model/add")
@handle_api_errors("Model add", error_value={"status": "error", "error": "Model add failed"})
async def api_model_add(request: Request, body: ModelAddRequest) -> dict[str, Any]:
    # 批次3A: 用 Pydantic ModelAddRequest 替代 await request.json()，
    # 由 FastAPI 自动校验请求体（name + ModelDef 全部字段类型）。
    require_admin(request)
    name = body.name
    if not name:
        raise HTTPException(400, "missing model name")
    # 构造 ModelDef（排除 name 字段）
    mdef = ModelDef(**{k: v for k, v in body.model_dump().items() if k != "name"})
    reg = _get_model_registry()
    reg.add_model(name, mdef)
    reg.save()
    return {"status": "ok", "model": name}

@router.post("/api/model/delete")
@handle_api_errors("Model delete", error_value={"status": "error", "error": "Model delete failed"})
async def api_model_delete(body: ModelNameRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
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
async def api_key_store(body: KeyStoreRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    provider = body.provider
    api_key = body.api_key
    if not provider or not api_key:
        raise HTTPException(400, "missing provider or api_key")
    vault = _get_api_key_vault()
    vault.store(provider, api_key)
    return {"status": "ok", "provider": provider}

@router.post("/api/model/key/delete")
@handle_api_errors("Key delete", error_value={"status": "error", "error": "Key delete failed"})
async def api_key_delete(body: KeyDeleteRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    provider = body.provider
    if not provider:
        raise HTTPException(400, "missing provider")
    vault = _get_api_key_vault()
    deleted = vault.delete(provider)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider {provider} not found")
    return {"status": "ok", "provider": provider}

@router.get("/api/model/key/list")
@handle_api_errors("Key list", error_value={"providers": [], "error": "Key list failed"})
async def api_key_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    vault = _get_api_key_vault()
    return {"providers": vault.list_providers()}

# ── Provider Health Check ────────────────────────────────────────────

@router.post("/api/model/health/check")
@handle_api_errors("Health check", error_value={"status": "error", "error": "Health check failed"})
async def api_health_check(body: HealthCheckRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    provider = body.provider
    reg = _get_model_registry()
    vault = _get_api_key_vault()
    from maop.core.routing.provider_health import ProviderHealthChecker
    checker = ProviderHealthChecker(registry=reg, vault=vault)
    if provider:
        result = await checker.check(provider)
        return {"status": "ok", "result": result.model_dump()}
    results = await checker.check_all()
    return {"status": "ok", "results": [r.model_dump() for r in results]}
