# MAOP v5.0.0 迁移指南

> 本指南覆盖从 v4.5.0 → v5.0.0 的所有不兼容变更与迁移步骤。
>
> 版本：v5.0.0（2026-08-11）| 上一版：v4.5.0（2026-08-06）

## 概述

v5.0.0 是一个 **major release**，包含以下类别的变更：

| 类别 | 影响范围 | 是否必须迁移 |
|------|----------|-------------|
| 后端 API 移除 | Python 调用方 | 是（移除的 API 需迁移） |
| 配置环境变量收敛 | `.env` / Docker / K8s | 建议（短名 v6.0.0 移除） |
| Docker 部署变更 | Docker / Compose | 是（版本号更新） |
| 前端 | 无 | 否（前端无 breaking change） |

---

## 1. 后端 API 变更

### 1.1 已移除的 API

以下 API 自 v4.0.0 起废弃，在 v5.0.0 正式移除。调用方必须迁移。

#### `maop.dashboard.provider.create_app()`

**影响**：创建隔离的 FastAPI app，与主 server 路由冲突。

**迁移**：

```python
# ❌ v4.x（已移除）
from maop.dashboard.provider import create_app
app = create_app()

# ✅ v5.0.0
from maop.dashboard.server import app
# 或在 uvicorn 启动时使用字符串引用
# uvicorn maop.dashboard.server:app
```

#### `maop.dashboard.provider._render_html()`

**影响**：v3.x 静态 HTML 渲染器。

**迁移**：前端已统一为 Vue 3 SPA，无需替代。如需自定义渲染，直接使用 `maop.dashboard.provider.DashboardProvider.async_get_state()` 获取数据。

#### `maop.maop_plan._route_by_keyword()` / `_ROUTING_RULES`

**影响**：硬编码关键词路由 fallback。

**迁移**：路由统一走 `agents.yaml` config routing。config routing miss 时回退到 `"chat"/"claude"` 默认值。如需自定义路由，在 `agents.yaml` 的 `routing` 部分添加规则。

```yaml
# agents.yaml routing 示例
routing:
  code:
    pattern: "(?:refactor|rewrite|restructure)"
    primary: codex
    fallback: claude
```

#### `/api/batch` 端点

**影响**：批量数据获取端点，前端不再调用。

**迁移**：使用独立的 `/api/*` 端点替代。

```javascript
// ❌ v4.x（已移除）
const resp = await fetch('/api/batch?keys=report,live,failures');

// ✅ v5.0.0
const [report, live, failures] = await Promise.all([
  fetch('/api/report').then(r => r.json()),
  fetch('/api/live').then(r => r.json()),
  fetch('/api/failures').then(r => r.json()),
]);
```

### 1.2 推迟到 v6.0.0 的 API

以下 API 在 v5.0.0 保留但已加强 deprecation warning，将在 v6.0.0 移除：

| API | 替代方案 |
|-----|---------|
| `maop.core.agent.delegation.subagent_delegation` | `maop.core.agent.delegation.subagent_lifecycle` |
| `maop.core.project_context` | 无（废弃模块，无生产引用） |
| `maop.core.agent.memory_ctx.project_context` | 无（废弃模块，无生产引用） |

---

## 2. 配置环境变量迁移

### 2.1 短名 → 规范长名

以下短名环境变量在 v5.0.0 仍可使用，但会发出 `DeprecationWarning`。**将在 v6.0.0 移除短名**。

| 短名（deprecated） | 规范长名 | 说明 |
|-------------------|---------|------|
| `MAOP_PORT` | `MAOP_DASH_PORT` | Dashboard 监听端口 |
| `MAOP_WORKERS` | `MAOP_DASH_WORKERS` | Uvicorn worker 数 |
| `MAOP_TLS` | `MAOP_TLS_ENABLED` | TLS 开关 |
| `MAOP_AUTH` | `MAOP_AUTH_ENABLED` | 认证开关 |

### 2.2 迁移步骤

1. 编辑 `.env` 文件，将短名改为规范长名：

```bash
# ❌ v4.x
MAOP_PORT=9079
MAOP_WORKERS=4
MAOP_TLS=0
MAOP_AUTH=1

# ✅ v5.0.0
MAOP_DASH_PORT=9079
MAOP_DASH_WORKERS=4
MAOP_TLS_ENABLED=0
MAOP_AUTH_ENABLED=1
```

2. 如果同时设置了短名和长名，长名优先（pydantic `AliasChoices` 按声明顺序匹配）。

3. 启动时检查日志，如有 `[config] MAOP_XXX is deprecated` 告警，按提示迁移。

---

## 3. Docker 部署变更

### 3.1 镜像版本号

```yaml
# ❌ v4.x
image: ghcr.io/maop/operator:4.5.0

# ✅ v5.0.0
image: ghcr.io/maop/operator:5.0.0
```

### 3.2 Helm Chart

```yaml
# Chart.yaml
appVersion: "5.0.0"  # was "4.5.0"

# values.yaml
image:
  tag: "5.0.0"  # was "4.5.0"
```

### 3.3 环境变量

Dockerfile 中的 `ENV` 指令已使用规范长名（`MAOP_DASH_HOST`、`MAOP_DASH_PORT` 等），无需额外迁移。如自定义 `docker-compose.yml` 中使用了短名，按第 2 节迁移。

---

## 4. 前端变更

**无 breaking change。** 前端 `dashboard-enterprise/` 版本号同步更新至 5.0.0，但 API 契约不变。

### 4.1 新增特性

- `Chat.vue` 流式渲染增强：token 计数与流速指示。
- 新增 `useAgentTokenStream.js` composable（Agent 执行 token 流式渲染）。

---

## 5. 测试变更

### 5.1 移除的测试

以下测试类在 v5.0.0 移除（对应已移除的 API）：

- `test_provider.py::TestRenderHtml` — 测试 `_render_html()`
- `test_provider.py::TestCreateApp` — 测试 `create_app()`
- `test_phase7.py::TestRenderHtml` — 测试 `_render_html()`
- `test_phase7.py::TestCreateApp` — 测试 `create_app()`
- `test_phase4.py::TestPlanRouting` 中的 keyword routing 测试 — 测试 `_route_by_keyword()`
- `test_maop_plan.py::TestRouteByKeyword` — 测试 `_route_by_keyword()`
- `test_router_data.py::TestBatch` — 测试 `/api/batch` 端点

### 5.2 新增测试

- `test_agent_token_stream.py` — 测试 `/api/stream/agent/{execution_id}` SSE 端点

---

## 6. 迁移检查清单

- [ ] 全局搜索 `create_app`，替换为 `maop.dashboard.server:app`
- [ ] 全局搜索 `_render_html`，移除调用（前端已用 Vue 3 SPA）
- [ ] 全局搜索 `_route_by_keyword` / `_ROUTING_RULES`，迁移到 `agents.yaml` config routing
- [ ] 全局搜索 `/api/batch`，替换为独立 `/api/*` 端点调用
- [ ] `.env` 文件：`MAOP_PORT` → `MAOP_DASH_PORT`
- [ ] `.env` 文件：`MAOP_WORKERS` → `MAOP_DASH_WORKERS`
- [ ] `.env` 文件：`MAOP_TLS` → `MAOP_TLS_ENABLED`
- [ ] `.env` 文件：`MAOP_AUTH` → `MAOP_AUTH_ENABLED`
- [ ] Docker / Helm：镜像 tag `4.5.0` → `5.0.0`
- [ ] 启动后检查日志无 `[config] ... is deprecated` 告警
- [ ] 运行测试套件确认无 `ImportError` / `AttributeError`