"""Model management endpoints for MAOP Dashboard.

Router 层只保留：路由定义、请求解析（Pydantic）、权限检查
（require_admin）、调用 service、响应格式化、错误处理。业务逻辑
在 ``maop.dashboard.services.model_service`` 中，框架无关。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors
from maop.dashboard.services import model_service
from maop.model.schema import ModelDef, ProviderDef  # 批次3A: 模块级导入以支持 Pydantic 模型继承

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
    return {"status": "ok", **model_service.list_model_agents()}

@router.get("/api/model/quota")
@handle_api_errors("Model quota", error_value={"agents": [], "count": 0, "error": "Model quota failed"})
async def api_model_quota(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.get_model_quota()}

@router.post("/api/model/switch")
@handle_api_errors("Model switch", error_value={"status": "error", "error": "Model switch failed"})
async def api_model_switch(body: ModelSwitchRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    agent_name = body.agent
    new_model = body.model
    if not agent_name or not new_model:
        raise HTTPException(400, "missing agent or model")
    try:
        result = await model_service.switch_model(agent_name, new_model)
    except FileNotFoundError as exc:
        # 批次3A: 统一响应格式为 {status, error}，消除 {success, message, data} 混用。
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", **result}

# ── Model Management v2 (ModelRegistry-backed) ────────────────────

@router.get("/api/model/registry")
@handle_api_errors("Model registry", error_value={"status": "error", "error": "Model registry failed"})
async def api_model_registry(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.get_model_registry_stats()}

@router.get("/api/model/list")
@handle_api_errors("Model list", error_value={"models": [], "count": 0, "error": "Model list failed"})
async def api_model_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.list_models()}

@router.get("/api/model/providers")
@handle_api_errors("Model providers", error_value={"providers": [], "error": "Model providers failed"})
async def api_model_providers(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.list_model_providers()}

@router.get("/api/model/select")
@handle_api_errors("Model select", error_value={"status": "error", "error": "Model select failed"})
async def api_model_select(request: Request, capability: str = "", agent_model: str = "", policy: str = "default") -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.select_model(capability, agent_model, policy)}

@router.get("/api/model/budget")
@handle_api_errors("Model budget", error_value={"status": "error", "error": "Model budget failed"})
async def api_model_budget(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.get_model_budget()}

@router.get("/api/model/quota/status")
@handle_api_errors("Model quota", error_value={"status": "error", "error": "Model quota failed"})
async def api_model_quota_status(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.get_model_quota_status()}

@router.get("/api/model/policies")
@handle_api_errors("Model policies", error_value={"policies": [], "count": 0, "error": "Model policies failed"})
async def api_model_policies(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.list_model_policies()}

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
    return {"status": "ok", **model_service.add_provider(name, pdef)}

@router.post("/api/model/provider/delete")
@handle_api_errors("Provider delete", error_value={"status": "error", "error": "Provider delete failed"})
async def api_provider_delete(body: ProviderNameRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
    if not name:
        raise HTTPException(400, "missing provider name")
    try:
        return {"status": "ok", **model_service.remove_provider(name)}
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
    return {"status": "ok", **model_service.add_model(name, mdef)}

@router.post("/api/model/delete")
@handle_api_errors("Model delete", error_value={"status": "error", "error": "Model delete failed"})
async def api_model_delete(body: ModelNameRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name
    if not name:
        raise HTTPException(400, "missing model name")
    try:
        return {"status": "ok", **model_service.remove_model(name)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

# ── API Key Vault ────────────────────────────────────────────────────

@router.post("/api/model/key/store")
@handle_api_errors("Key store", error_value={"status": "error", "error": "Key store failed"})
async def api_key_store(body: KeyStoreRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    provider = body.provider
    api_key = body.api_key
    if not provider or not api_key:
        raise HTTPException(400, "missing provider or api_key")
    return {"status": "ok", **model_service.store_api_key(provider, api_key)}

@router.post("/api/model/key/delete")
@handle_api_errors("Key delete", error_value={"status": "error", "error": "Key delete failed"})
async def api_key_delete(body: KeyDeleteRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    provider = body.provider
    if not provider:
        raise HTTPException(400, "missing provider")
    try:
        return {"status": "ok", **model_service.delete_api_key(provider)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/api/model/key/list")
@handle_api_errors("Key list", error_value={"providers": [], "error": "Key list failed"})
async def api_key_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **model_service.list_api_key_providers()}

# ── Provider Health Check ────────────────────────────────────────────

@router.post("/api/model/health/check")
@handle_api_errors("Health check", error_value={"status": "error", "error": "Health check failed"})
async def api_health_check(body: HealthCheckRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"status": "ok", **await model_service.check_provider_health(body.provider)}
