# MAOP Platform — Model Management + Control Plane + Contract Testing

## Overview

MAOP 平台化演进，三大支柱：
1. **Model Management** — 统一模型注册中心、选择器、配额、预算
2. **Control Plane** — 统一控制面 + 审计事件系统
3. **Contract Testing** — 前后端 API 契约验证

## Architecture

```
config/models.yaml (权威注册中心)
       │
       ▼
py/maop/model/
  ├── schema.py      # Pydantic 模型定义
  ├── registry.py    # ModelRegistry + ProviderRegistry
  ├── selector.py    # ModelSelector (策略路由)
  ├── fallback.py    # FallbackManager (模型级降级)
  ├── quota.py       # QuotaEnforcer (per-provider 限流)
  └── budget.py      # BudgetGuard (成本追踪 + 预算)
       │
       ▼
py/maop/delegate/dispatcher.py
  └── ModelSelector 注入 → dispatch 前自动选模型
       │
       ▼
py/maop/control/
  ├── audit.py       # AuditEvent + AuditLog (JSONL)
  └── plane.py       # ControlPlane (统一控制 + 审计)
       │
       ▼
py/maop/dashboard/server.py
  └── /api/model/* + /api/audit/* 端点
```

## Model Management

### config/models.yaml

权威注册中心，定义 providers、models、policies、budget、quota。

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/model/registry` | GET | 完整注册中心统计 |
| `/api/model/list` | GET | 所有模型列表 |
| `/api/model/providers` | GET | Provider 列表 + 健康状态 |
| `/api/model/select` | GET | 按能力/策略选最佳模型 |
| `/api/model/budget` | GET | 预算状态 |
| `/api/model/quota/status` | GET | 各 Provider 配额使用 |
| `/api/model/policies` | GET | 选择策略列表 |
| `/api/model/switch` | POST | 切换 Agent 模型 |
| `/api/model/agents` | GET | Agent 模型信息 |
| `/api/model/quota` | GET | Agent 配额/可用性 |

### Dispatcher 集成

Dispatcher 接受 `model_selector` 参数，在 `dispatch()` 中：
1. 解析 Agent 配置
2. **通过 ModelSelector 选模型** (新增)
3. 熔断器检查
4. 执行驱动
5. 记录结果

## Control Plane

### Audit System

- **AuditEvent**: 结构化审计事件 (actor/action/target/level/detail/trace_id)
- **AuditLog**: JSONL 追加日志，支持 read_recent + filter

### ControlPlane

统一控制入口，所有操作自动审计：
- `model.switch` / `control.run` / `control.pause` / `control.resume`
- `control.stop` / `config.reload` / `cache.clear` / `memory.prune`

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audit/events` | GET | 最近审计事件 |
| `/api/audit/filter` | GET | 按条件过滤审计事件 |

## Contract Testing

### 目录结构

```
py/tests/contract/
  ├── conftest.py                      # 注册 contract marker
  ├── test_model_api_contract.py       # 模型 API 契约
  ├── test_control_api_contract.py     # 控制面 API 契约
  └── test_dispatcher_contract.py      # Dispatcher 集成契约
```

### CI 集成

GitHub Actions CI 分两步：
1. **Unit tests**: `pytest tests/ -q --ignore=tests/contract`
2. **Contract tests**: `pytest tests/contract/ -q -m contract`

## Test Results

| Test Suite | Count | Status |
|------------|-------|--------|
| Model Management | 47 | ✅ all passed |
| Control Plane | 20 | ✅ all passed |
| Contract Tests | 25 | ✅ all passed |
| **Total New** | **92** | **✅ all passed** |
| Existing Tests | 547 | ✅ passed (10 pre-existing failures unrelated) |

## Files Created/Modified

### New Files
- `config/models.yaml` — 模型权威注册中心
- `py/maop/model/__init__.py` — 模块入口
- `py/maop/model/schema.py` — Pydantic 模型定义
- `py/maop/model/registry.py` — ModelRegistry + ProviderRegistry
- `py/maop/model/selector.py` — ModelSelector
- `py/maop/model/fallback.py` — FallbackManager
- `py/maop/model/quota.py` — QuotaEnforcer
- `py/maop/model/budget.py` — BudgetGuard
- `py/maop/control/__init__.py` — 控制面模块入口
- `py/maop/control/audit.py` — 审计事件系统
- `py/maop/control/plane.py` — ControlPlane
- `py/tests/test_model_management.py` — 47 个测试
- `py/tests/test_control_plane.py` — 20 个测试
- `py/tests/contract/__init__.py` — 契约测试包
- `py/tests/contract/conftest.py` — contract marker
- `py/tests/contract/test_model_api_contract.py` — 模型 API 契约
- `py/tests/contract/test_control_api_contract.py` — 控制面 API 契约
- `py/tests/contract/test_dispatcher_contract.py` — Dispatcher 集成契约

### Modified Files
- `py/maop/delegate/dispatcher.py` — 注入 ModelSelector
- `py/maop/dashboard/server.py` — 新增 9 个 API 端点
- `.github/workflows/ci.yml` — CI 接入 contract tests
