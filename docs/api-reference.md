# MAOP API Reference

MAOP（Multi-Agent Orchestration Platform）是基于 FastAPI 实现的智能体编排框架，采用 Plan-Execute-Verify 范式。本文档覆盖 `dashboard` 模块暴露的全部 HTTP 与 WebSocket 端点，版本 5.1.0，供使用方、集成方与运维人员查阅。所有示例可直接复制运行，默认服务地址为 `http://127.0.0.1:9079`。

---

## 1. Overview

### Base URL

```
http://127.0.0.1:9079
```

生产环境建议反向代理到 TLS，并通过 Nginx/Caddy 终结 HTTPS。

### Versioning

所有 `/api/*` 路由会自动获得 `/api/v1/*` 别名，便于客户端固定主版本。以下路由豁免版本别名，始终只暴露原路径：

| Path | Reason |
| --- | --- |
| `/api/health` | K8s 探针，需稳定 |
| `/api/stream` | SSE 长连接 |
| `/api/auth/{login,logout,refresh}` | 登录流前端约定 |

### Authentication

采用 JWT Bearer Token，登录后所有受保护接口需在请求头携带：

```
Authorization: Bearer <token>
```

登录端点：`POST /api/auth/login`，请求体 `{username, password}`，返回 `{token, expires_at}`。

### Authorization

- 写操作普遍调用 `require_admin` 依赖，要求 JWT 携带 `admin` 角色
- 管理员专属端点在表格 `Admin` 列标记为 `[admin]`
- 非管理员调用将返回 `403 Forbidden`

### Enterprise Boundary

企业版能力受 FeatureFlag 控制，Personal 版自动返回 `404 Not Found`：

| Domain | Required Feature |
| --- | --- |
| `tenant` / `audit` / `rbac` / `sso` | `MULTI_USER` |
| `n8n` | `N8N_INTEGRATION` |

### Error Response

统一错误响应格式：

```json
{"detail": "...", "code": "..."}
```

常见 HTTP 状态码：

| Code | Meaning |
| --- | --- |
| 200 | OK |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 500 | Internal Server Error |

### OpenAPI Docs

- `GET /api/docs` — Swagger UI
- `GET /api/redoc` — ReDoc
- `GET /api/openapi.json` — OpenAPI schema

生产环境 `MAOP_ENV=production` 时，文档端点自动禁用。

### Login Example

```bash
curl -X POST http://127.0.0.1:9079/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "changeme"}'
```

响应：

```json
{"token": "eyJhbGciOi...", "expires_at": "2026-08-02T10:00:00Z"}
```

---

## 2. Authentication & Authorization

`/api/auth` 命名空间下的端点：

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/auth/login` | 登录获取 JWT | | 豁免版本别名 |
| POST | `/api/auth/logout` | 登出，吊销当前 token | | |
| GET | `/api/auth/status` | 当前认证状态 | | |
| POST | `/api/auth/register` | 注册新用户 | `[admin]` | |
| GET | `/api/auth/users` | 用户列表 | `[admin]` | |
| DELETE | `/api/auth/users/{username}` | 删除用户 | `[admin]` | |
| PUT | `/api/auth/users/{username}` | 更新用户信息 | `[admin]` | |

### JWT Usage Example

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:9079/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl http://127.0.0.1:9079/api/auth/users \
  -H "Authorization: Bearer $TOKEN"
```

---

## 3. Core Endpoints

### 3.1 Data（只读查询）— `data.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/report` | 总览报告 | | |
| GET | `/api/agents/stats` | Agent 统计 | | |
| GET | `/api/timeseries` | 时间序列 | | |
| GET | `/api/metrics` | 指标 | | |
| GET | `/api/live` | 实时快照 | | |
| GET | `/api/snapshot` | 系统快照 | | |
| GET | `/api/failures` | 失败列表 | | |
| GET | `/api/chain` | 调用链 | | |
| GET | `/api/optimizer` | 优化器状态 | | |
| GET | `/api/graph/stats` | 图统计 | | |
| GET | `/api/graph/nodes` | 图节点 | | |
| GET | `/api/graph/edges` | 图边 | | |
| GET | `/api/vector/stats` | 向量索引统计 | | |
| GET | `/api/vector/search` | 向量检索 | | |
| GET | `/api/wiki/stats` | Wiki 统计 | | |
| GET | `/api/prompts` | Prompt 列表 | | |
| GET | `/api/coordination` | 协调状态 | | |
| GET | `/api/teams` | 团队列表 | | |
| GET | `/api/skills` | 技能列表 | | |
| GET | `/api/tools/stats` | 工具统计 | | |
| GET | `/api/guardrails` | 护栏状态 | | |
| GET | `/api/sandbox/list` | 沙箱列表 | | |
| GET | `/api/human/pending` | 待人审项 | | |
| GET | `/api/mcp/servers` | MCP 服务列表 | | |
| GET | `/api/mcp/tools` | MCP 工具列表 | | |
| GET | `/api/versions` | 版本信息 | | |
| GET | `/api/providers` | 提供方列表 | | |
| GET | `/api/logs` | 日志 | | |
| GET | `/api/logs/delegations` | 委托日志 | | |
| GET | `/api/logs/checker` | Checker 日志 | | |
| GET | `/api/logs/analysis` | 分析日志 | | |

代表性示例：

```bash
curl http://127.0.0.1:9079/api/report -H "Authorization: Bearer $TOKEN"
```

### 3.2 Control（动作）— `control.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/control/status` | 当前任务状态 | | |
| POST | `/api/control/run` | 启动任务 | | body: `{goal}` |
| POST | `/api/control/pause` | 暂停 | | |
| POST | `/api/control/resume` | 恢复 | | |
| POST | `/api/control/stop` | 停止 | | |
| POST | `/api/control/validate` | 校验配置 | | |
| POST | `/api/control/doctor` | 自检 | | |
| POST | `/api/control/cancel` | 取消任务 | | |
| POST | `/api/control/refresh` | 刷新缓存 | | |
| POST | `/api/control/clear-cache` | 清缓存 | `[admin]` | |
| POST | `/api/control/provider-health` | 提供方健康 | | |
| POST | `/api/control/maintain` | 维护模式 | `[admin]` | |

```bash
curl -X POST http://127.0.0.1:9079/api/control/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"goal": "总结本周会议纪要"}'
```

### 3.3 Model（模型管理）— `model.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/model/agents` | Agent 模型映射 | | |
| GET | `/api/model/quota` | 配额 | | |
| GET | `/api/model/quota/status` | 配额状态 | | |
| POST | `/api/model/switch` | 切换模型 | `[admin]` | |
| GET | `/api/model/registry` | 模型注册表 | | |
| GET | `/api/model/list` | 模型列表 | | |
| GET | `/api/model/providers` | 提供方 | | |
| GET | `/api/model/select` | 选择默认模型 | | |
| GET | `/api/model/budget` | 预算 | | |
| GET | `/api/model/policies` | 策略 | | |
| POST | `/api/model/provider/add` | 添加提供方 | `[admin]` | |
| POST | `/api/model/provider/delete` | 删除提供方 | `[admin]` | |
| POST | `/api/model/add` | 添加模型 | `[admin]` | |
| POST | `/api/model/delete` | 删除模型 | `[admin]` | |
| POST | `/api/model/key/store` | 存储密钥 | `[admin]` | |
| POST | `/api/model/key/delete` | 删除密钥 | `[admin]` | |
| GET | `/api/model/key/list` | 密钥列表 | `[admin]` | |
| POST | `/api/model/health/check` | 健康检查 | | |

```bash
curl -X POST http://127.0.0.1:9079/api/model/switch \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent":"planner","model":"doubao-pro"}'
```

### 3.4 Evolve（自演化分析建议）— `evolve_insights.py`

> **概念区分（P2-2）**：MAOP 有两组极易混淆的「演化」API 族。
> - **`/api/evolve/*`（本族）** = *自演化分析建议*：读执行历史 → 生成 Prompt / 策略改进建议（只读分析 + 可选 apply），**不改线上行为**。对应前端 `Evolve.vue`。
> - **`/api/evolution/*`（见下节）** = *AB 实验 + 部署晋升*：在真实流量上跑 A/B（SPRT 早停），经人工 gate 批准后**自动提升 / 回滚**线上 Agent。对应前端 `EvolutionHistory.vue`。
> 两者职责正交：**evolve 出「点子」，evolution 决定「是否上线」**。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/evolve/status` | 演化状态 | | |
| GET | `/api/evolve/metrics` | 演化指标 | | |
| POST | `/api/evolve/analyze` | 触发分析 | | |
| GET | `/api/evolve/suggestions` | 建议列表 | | |
| GET | `/api/evolve/suggestions-list` | 建议清单（分页） | | |
| GET | `/api/evolve/report` | 演化报告 | | |
| GET | `/api/evolve/strategies` | 策略列表 | | |
| GET | `/api/evolve/history` | 演化历史 | | |
| POST | `/api/evolve/apply-suggestion` | 应用某条建议 | Admin | 写操作 |

```bash
curl -X POST http://127.0.0.1:9079/api/evolve/analyze -H "Authorization: Bearer $TOKEN"
```

### Evolution（AB 实验 + 部署晋升）— `evolution_experiment.py`

> 见上方 §3.4 概念区分。`/api/evolution/*` 在真实流量上做 A/B 实验（SPRT 早停），经人工 gate 批准后自动提升或回滚线上 Agent；本族是**写操作 + 发布动作**，与 `/api/evolve/*` 的只读分析严格区分。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/evolution/evaluate` | 评估一组 trace 性能指标 | | |
| POST | `/api/evolution/suggest` | 生成候选改进 | | |
| POST | `/api/evolution/ab/create` | 创建 AB 实验 | Admin | |
| POST | `/api/evolution/ab/record` | 记录实验样本 | | |
| GET | `/api/evolution/ab/evaluate/{experiment}` | 实验统计（SPRT） | | |
| GET | `/api/evolution/ab/list` | 实验列表 | | |
| POST | `/api/evolution/deploy/promote` | 自动提升 | Admin | |
| POST | `/api/evolution/deploy/rollback` | 回滚 | Admin | |
| GET | `/api/evolution/deploy/history` | 晋升历史 | | |
| POST | `/api/evolution/run` | 触发一轮演化循环 | Admin | |
| GET | `/api/evolution/cycles` | 演化循环历史 | | |
| GET | `/api/evolution/pending` | 待批准列表 | Admin | |
| POST | `/api/evolution/approve` | 人工 gate 批准提升 | Admin | |
| GET | `/api/evolution/skills` | 技能列表 | | |
| POST | `/api/evolution/skills/composite` | 组合技能 | | |

### 3.5 Memory（记忆 + 神经机制）— `memory.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/memory/deep` | 深度记忆 | | |
| GET | `/api/memory/search` | 记忆检索 | | |
| GET | `/api/memory/trace` | 记忆轨迹 | | |
| GET | `/api/memory/stats` | 记忆统计 | | |
| GET | `/api/neural/status` | 神经状态 | | |
| GET | `/api/neural/attention` | 注意力 | | |
| POST | `/api/neural/attention` | 调整注意力 | | |

```bash
curl http://127.0.0.1:9079/api/memory/search?query=last-deploy-failure \
  -H "Authorization: Bearer $TOKEN"
```

### 3.6 System（框架状态/审计/配置/概览）— `system.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/subsystems` | 子系统状态 | | |
| GET | `/api/framework/status` | 框架状态 | | |
| GET | `/api/framework/logs` | 框架日志 | | |
| GET | `/api/framework/config` | 框架配置 | | |
| GET | `/api/agent/config` | Agent 配置 | | |
| POST | `/api/agent/config/update` | 更新 Agent 配置 | `[admin]` | |
| GET | `/api/agent/upgrade` | 升级状态 | | |
| POST | `/api/agent/upgrade` | 触发升级 | `[admin]` | |
| GET | `/api/workflow/list` | 工作流列表 | | |
| POST | `/api/workflow/run` | 运行工作流 | | |
| GET | `/api/overview` | 系统概览 | | |
| GET | `/api/coordination_report` | 协调报告 | | |
| GET | `/api/workflows` | 工作流别名 | | |
| GET | `/api/routing` | 路由概览 | | |
| GET | `/api/security/config` | 安全配置 | | |
| GET | `/api/audit/events` | 审计事件 | | |
| GET | `/api/audit/summary` | 审计摘要 | | |
| GET | `/api/audit/filter` | 审计过滤 | | |
| GET | `/api/system/resources` | 资源 | | |
| GET | `/api/system/diagnostics` | 诊断 | | |

```bash
curl http://127.0.0.1:9079/api/overview -H "Authorization: Bearer $TOKEN"
```

---

## 4. Agent & Delegation

### `agents/`（prefix=`/api/agents`）

由 `agents/crud.py` + `agents/evolution.py` + `agents/memory.py` + `agents/routes.py` 共同提供，共 23 个端点。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/agents` | Agent 列表 | | |
| GET | `/api/agents/presets` | Agent 预设 | | |
| GET | `/api/agents/routes` | 路由表 | | |
| GET | `/api/agents/match` | 匹配 Agent | | |
| GET | `/api/agents/{name}` | Agent 详情 | | |
| GET | `/api/agents/{name}/health-log` | 健康日志 | | |
| GET | `/api/agents/{name}/diagnose` | 诊断 | `[admin]` | |
| POST | `/api/agents/{name}/repair` | 修复 | `[admin]` | |
| POST | `/api/agents/scan` | 扫描 Agent | `[admin]` | |
| POST | `/api/agents/{name}/health-check` | 健康检查 | `[admin]` | |
| POST | `/api/agents/health-check-all` | 全员检查 | `[admin]` | |
| POST | `/api/agents/{name}/enable` | 启用 | `[admin]` | |
| POST | `/api/agents/{name}/disable` | 禁用 | `[admin]` | |
| POST | `/api/agents/register` | 注册 Agent | `[admin]` | |
| DELETE | `/api/agents/{name}` | 删除 Agent | `[admin]` | |
| GET | `/api/agents/upgrade/status` | 升级状态 | | |
| GET | `/api/agents/{name}/upgrade/check` | 升级检查 | | |
| POST | `/api/agents/{name}/upgrade` | 触发升级 | `[admin]` | |
| POST | `/api/agents/{name}/evolve` | 触发演化 | `[admin]` | |
| GET | `/api/agents/{name}/evolution-status` | 演化状态 | | |
| GET | `/api/agents/{name}/memory` | Agent 记忆 | | |
| POST | `/api/agents/{name}/memory` | 写入记忆 | `[admin]` | |
| DELETE | `/api/agents/{name}/memory` | 清除记忆 | `[admin]` | |
| GET | `/api/agents/{name}/memory/summary` | 记忆摘要 | | |

### `subagent.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/subagent/spawn` | 派生子代理 | `[admin]` | |
| POST | `/api/subagent/wait` | 等待子代理 | `[admin]` | |
| POST | `/api/subagent/cancel` | 取消子代理 | `[admin]` | |
| GET | `/api/subagent/list` | 子代理列表 | | |
| GET | `/api/subagent/transcript` | 子代理回执 | | |

### `agent_bridge.py`（prefix=`/api/bridge`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/bridge/adapters` | 适配器列表 | | |
| POST | `/api/bridge/call` | 调用适配器 | `[admin]` | |
| GET | `/api/bridge/health` | 健康检查 | | |
| POST | `/api/bridge/sync-config` | 同步配置 | `[admin]` | |

```bash
curl -X POST http://127.0.0.1:9079/api/agents/scan -H "Authorization: Bearer $TOKEN"
```

---

## 5. Chat & Streaming

### `chat.py`（prefix=`/api/chat`）

11 个端点，覆盖流式对话、会话查询/删除、记忆检索、图片上传等。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/chat/stream` | 流式对话（SSE） | | body: `{session_id?, message}` |
| GET | `/api/chat/models` | 可用模型 | | |
| GET | `/api/chat/sessions` | 会话列表 | | |
| GET | `/api/chat/{session_id}` | 会话详情 | | |
| DELETE | `/api/chat/{session_id}` | 删除会话 | | |
| POST | `/api/chat/memory/search` | 会话内记忆检索 | | |
| POST | `/api/chat/memory/consolidate` | 记忆合并 | `[admin]` | |
| GET | `/api/chat/memory/stats` | 记忆统计 | | |
| POST | `/api/chat/upload` | 上传图片/附件 | | multipart |
| GET | `/api/chat/images/{session_id}` | 会话图片列表 | | |
| DELETE | `/api/chat/images/{image_id}` | 删除图片 | | |

### `stream.py`（prefix=`/api/stream`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/stream/{trace_id}` | SSE 流 | `[admin]` | 豁免版本别名 |
| GET | `/api/stream/active` | 活跃流 | `[admin]` | |
| GET | `/api/stream/dag/{execution_id}` | DAG 执行流 | `[admin]` | |
| GET | `/api/stream/agent/{execution_id}` | Agent 执行流 | `[admin]` | |

### WebSocket `/ws`

实时推送系统快照，每 15 秒一次。

```bash
curl -N http://127.0.0.1:9079/api/stream/trace-123 -H "Authorization: Bearer $TOKEN"
```

```python
import websockets, asyncio, json

async def listen():
    async with websockets.connect("ws://127.0.0.1:9079/ws") as ws:
        async for msg in ws:
            print(json.loads(msg))

asyncio.run(listen())
```

---

## 6. Plugins & MCP

### `plugin.py`（prefix=`/api/plugins`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/plugins/discover` | 发现插件 | `[admin]` | |
| POST | `/api/plugins/load` | 加载 | `[admin]` | |
| POST | `/api/plugins/start` | 启动 | `[admin]` | |
| POST | `/api/plugins/stop` | 停止 | `[admin]` | |
| POST | `/api/plugins/reload` | 重载 | `[admin]` | |
| GET | `/api/plugins/config` | 配置 | | |
| POST | `/api/plugins/load-all` | 批量加载 | `[admin]` | |
| POST | `/api/plugins/start-all` | 批量启动 | `[admin]` | |
| POST | `/api/plugins/stop-all` | 批量停止 | `[admin]` | |
| GET | `/api/plugins/list` | 列表 | | |
| GET | `/api/plugins/{name}` | 详情 | | |

### `mcp.py`（prefix=`/api/mcp`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/mcp/connect/{server_name}` | 连接 MCP | | |
| POST | `/api/mcp/disconnect/{server_name}` | 断开 | | |
| GET | `/api/mcp/servers` | 服务列表 | | |
| GET | `/api/mcp/tools` | 工具列表 | | |
| POST | `/api/mcp/call` | 调用工具 | | |
| GET | `/api/mcp/health` | 健康 | | |

```bash
curl -X POST http://127.0.0.1:9079/api/plugins/load \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"my-plugin","path":"./plugins/my_plugin"}'
```

---

## 7. Routing & Scheduling（Phase γ-4）

### `routing.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/routing/decisions/recent` | 最近决策 | | |
| GET | `/api/routing/decisions/stats` | 决策统计 | | |
| GET | `/api/routing/decisions/{trace_id}` | 决策详情 | | |

### `routing_preview.py`（prefix=`/api/routing`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/routing/match` | 模拟匹配 | `[admin]` | |
| GET | `/api/routing/cooldowns` | 冷却配置 | `[admin]` | |
| GET | `/api/routing/scores` | 路由评分 | `[admin]` | |

```bash
curl http://127.0.0.1:9079/api/routing/decisions/recent -H "Authorization: Bearer $TOKEN"
```

---

## 8. Hooks & Protocols

### `hook.py`（prefix=`/api/hook`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/hook/register` | 注册 hook | | |
| POST | `/api/hook/unregister` | 注销 | | |
| POST | `/api/hook/enable` | 启用 | | |
| POST | `/api/hook/disable` | 禁用 | | |
| GET | `/api/hook/list` | 列表 | | |
| GET | `/api/hook/get` | 查询 hook | | |
| POST | `/api/hook/trigger` | 触发 | | |
| GET | `/api/hook/logs` | 日志 | | |
| GET | `/api/hook/events` | 事件 | | |

### `protocol.py`（prefix=`/api/protocol`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/protocol/register` | 注册协议 | `[admin]` | |
| POST | `/api/protocol/unregister` | 注销 | `[admin]` | |
| GET | `/api/protocol/get` | 查询 | | |
| GET | `/api/protocol/list` | 列表 | | |
| GET | `/api/protocol/versions` | 版本 | | |
| POST | `/api/protocol/validate` | 校验 | `[admin]` | |
| POST | `/api/protocol/send` | 发送 | `[admin]` | |
| GET | `/api/protocol/messages` | 消息 | | |

```bash
curl -X POST http://127.0.0.1:9079/api/hook/trigger \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"event":"on_task_done","payload":{"trace_id":"t-1"}}'
```

---

## 9. React & Worktree（代码演化）

### `react.py`（prefix=`/api/react`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/react/snapshots` | 快照列表 | | |
| POST | `/api/react/snapshots` | 创建快照 | `[admin]` | |
| GET | `/api/react/diff` | diff | | |
| GET | `/api/react/changes` | 变更 | | |
| DELETE | `/api/react/snapshots/{id}` | 删除 | `[admin]` | |
| GET | `/api/react/artifacts` | 产物列表 | | |
| POST | `/api/react/artifacts` | 创建产物 | `[admin]` | |
| GET | `/api/react/artifacts/{name}` | 产物详情 | | |
| GET | `/api/react/artifacts/{name}/history` | 历史 | | |
| POST | `/api/react/artifacts/{name}/restore` | 恢复 | `[admin]` | |
| DELETE | `/api/react/artifacts/{name}` | 删除 | `[admin]` | |

### `worktree.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/worktree/create-root` | 创建根 | `[admin]` | |
| POST | `/api/worktree/branch` | 分支 | `[admin]` | |
| POST | `/api/worktree/abandon` | 弃用 | `[admin]` | |
| GET | `/api/worktree/get` | 查询 | | |
| GET | `/api/worktree/list` | 列表 | | |
| POST | `/api/worktree/merge` | 合并 | `[admin]` | |
| POST | `/api/worktree/checkpoint` | 检查点 | `[admin]` | |
| POST | `/api/worktree/rollback` | 回滚 | `[admin]` | |

```bash
curl -X POST http://127.0.0.1:9079/api/react/snapshots \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"path":"./src","tag":"v1"}'
```

---

## 10. Session & Knowledge

### `session.py`（prefix=`/api/session`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/session/list` | 会话列表 | | |
| GET | `/api/session/{id}` | 详情 | | |
| POST | `/api/session/create` | 创建 | | |
| POST | `/api/session/{id}/append` | 追加 | | |
| POST | `/api/session/{id}/close` | 关闭 | | |
| GET | `/api/session/{id}/messages` | 消息 | | |
| DELETE | `/api/session/{id}` | 删除 | | |
| POST | `/api/session/{id}/fork` | 派生 | | |
| GET | `/api/session/active` | 活跃 | | |
| POST | `/api/session/{id}/export` | 导出 | | |

### `knowledge.py`（prefix=`/api/knowledge`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/knowledge/stats` | 统计 | | |
| GET | `/api/knowledge/facts` | 事实 | | |
| GET | `/api/knowledge/entities/{name}` | 实体 | | |
| GET | `/api/knowledge/relations` | 关系 | | |
| GET | `/api/knowledge/graph` | 图谱 | | |
| GET | `/api/knowledge/context` | 上下文 | | |
| POST | `/api/knowledge/extract` | 抽取 | | |
| GET | `/api/knowledge/vector/stats` | 向量统计 | | |
| POST | `/api/knowledge/vector/search` | 向量检索 | | |
| POST | `/api/knowledge/vector/index` | 向量索引 | | |

```bash
curl http://127.0.0.1:9079/api/session/active -H "Authorization: Bearer $TOKEN"
```

---

## 11. Cost & Budget & Tool Audit

### `cost.py`（prefix=`/api/cost`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/cost/entries` | 费用明细 | | |
| GET | `/api/cost/summary` | 汇总 | | |
| GET | `/api/cost/budget` | 预算 | | |
| GET | `/api/cost/pricing` | 定价表 | | |
| PUT | `/api/cost/pricing/{model}` | 单模型定价 | `[admin]` | |
| POST | `/api/cost/record` | 记录费用 | | |

### `budget.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/budget/status` | 状态 | | |
| POST | `/api/budget/reset` | 重置 | | |
| POST | `/api/budget/record` | 记录 | | |

### `tool_audit.py`

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/tool-audit/entries` | 审计条目 | | |
| GET | `/api/tool-audit/stats` | 统计 | | |
| POST | `/api/tool-audit/cleanup` | 清理 | `[admin]` | |

```bash
curl http://127.0.0.1:9079/api/cost/summary -H "Authorization: Bearer $TOKEN"
```

---

## 12. Permission

`permission.py`（prefix=`/api`）：

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/permission/rules` | 添加规则 | | |
| DELETE | `/api/permission/rules/{rule_id}` | 删除规则 | | |
| GET | `/api/permission/rules` | 规则列表 | | |
| GET | `/api/permission/check` | 权限校验 | | |
| GET | `/api/approval/pending` | 待审批 | | |
| POST | `/api/approval/{id}/approve` | 批准 | | |
| POST | `/api/approval/{id}/reject` | 拒绝 | | |

```bash
curl "http://127.0.0.1:9079/api/permission/check?user=alice&action=run&resource=control" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 13. Enterprise Endpoints

### [Enterprise MULTI_USER]

> **Enterprise Only** — 需要 FeatureFlag.MULTI_USER，Personal 版返回 404

#### `tenant.py`（prefix=`/api/tenant`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/tenant/list` | 租户列表 | `[admin]` | |
| POST | `/api/tenant/create` | 创建租户 | `[admin]` | |
| GET | `/api/tenant/{id}` | 详情 | `[admin]` | |
| POST | `/api/tenant/{id}/suspend` | 暂停 | `[admin]` | |
| POST | `/api/tenant/{id}/activate` | 激活 | `[admin]` | |
| DELETE | `/api/tenant/{id}` | 删除 | `[admin]` | |
| GET | `/api/tenant/{id}/usage` | 用量 | `[admin]` | |

#### `audit.py`（prefix=`/api/audit`）

16 个端点，覆盖审计事件查询、过滤、导出、告警规则 CRUD 与告警评估。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/audit/events` | 审计事件 | `[admin]` | |
| GET | `/api/audit/summary` | 审计摘要 | `[admin]` | |
| GET | `/api/audit/filter` | 审计过滤 | `[admin]` | |
| POST | `/api/audit/events/advanced` | 高级事件查询 | `[admin]` | |
| GET | `/api/audit/export` | 导出 | `[admin]` | |
| GET | `/api/audit/stats` | 统计 | `[admin]` | |
| GET | `/api/audit/timeline` | 时间线 | `[admin]` | |
| GET | `/api/audit/heatmap` | 热力图 | `[admin]` | |
| POST | `/api/audit/alert/rules` | 创建告警规则 | `[admin]` | |
| GET | `/api/audit/alert/rules` | 告警规则列表 | `[admin]` | |
| GET | `/api/audit/alert/rules/{rule_id}` | 规则详情 | `[admin]` | |
| PUT | `/api/audit/alert/rules/{rule_id}` | 更新规则 | `[admin]` | |
| DELETE | `/api/audit/alert/rules/{rule_id}` | 删除规则 | `[admin]` | |
| GET | `/api/audit/alert/history` | 告警历史 | `[admin]` | |
| POST | `/api/audit/alert/{alert_id}/acknowledge` | 确认告警 | `[admin]` | |
| POST | `/api/audit/alert/evaluate` | 评估告警 | `[admin]` | |

#### `rbac.py`（prefix=`/api/rbac`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/rbac/grants` | 授权列表 | | |
| POST | `/api/rbac/grant` | 授权 | `[admin]` | |
| POST | `/api/rbac/revoke` | 撤销 | `[admin]` | |
| GET | `/api/rbac/roles` | 角色列表 | | |
| GET | `/api/rbac/permissions` | 权限列表 | | |

#### `sso.py`（prefix=`/api/sso`）

17 个端点，覆盖 OIDC/SAML 授权流、Provider CRUD、元数据与启用状态。

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/api/sso/authorize` | 跳转授权 | | |
| GET | `/api/sso/callback` | 回调 | | |
| POST | `/api/sso/logout` | 登出 | | |
| GET | `/api/sso/validate` | 校验 | | |
| GET | `/api/sso/config` | 配置 | | |
| GET | `/api/sso/enabled` | SSO 是否启用 | | |
| GET | `/api/sso/providers` | Provider 列表 | | |
| POST | `/api/sso/providers` | 创建 Provider | `[admin]` | |
| GET | `/api/sso/providers/{provider_id}` | Provider 详情 | | |
| PUT | `/api/sso/providers/{provider_id}` | 更新 Provider | `[admin]` | |
| DELETE | `/api/sso/providers/{provider_id}` | 删除 Provider | `[admin]` | |
| POST | `/api/sso/providers/{provider_id}/test` | 测试连接 | `[admin]` | |
| GET | `/api/sso/providers/{provider_id}/metadata` | 元数据 | | |
| GET | `/api/sso/oidc/{provider_id}/login` | OIDC 登录 | | |
| GET | `/api/sso/oidc/{provider_id}/callback` | OIDC 回调 | | |
| GET | `/api/sso/saml/{provider_id}/login` | SAML 登录 | | |
| POST | `/api/sso/saml/{provider_id}/acs` | SAML ACS | | |

### [Enterprise N8N_INTEGRATION]

> **Enterprise Only** — 需要 FeatureFlag.N8N_INTEGRATION，Personal 版返回 404

#### `n8n.py`（prefix=`/api/n8n`）

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| POST | `/api/n8n/webhook` | 接收 webhook | | |
| GET | `/api/n8n/workflows` | 工作流 | | |
| POST | `/api/n8n/workflows/{id}/trigger` | 触发 | | |
| GET | `/api/n8n/executions/{id}` | 执行详情 | | |
| GET | `/api/n8n/health` | 健康 | | |

```bash
curl http://127.0.0.1:9079/api/tenant/list -H "Authorization: Bearer $TOKEN"
```

---

## 14. Top-level Endpoints

| Method | Path | Description | Admin | Notes |
| --- | --- | --- | --- | --- |
| GET | `/` | SPA index.html | | |
| GET | `/style.css` | 样式 | | |
| GET | `/favicon.svg` | 图标 | | |
| GET | `/api/health` | K8s probe | | 豁免版本别名 |
| POST | `/api/csp-report` | CSP 违规上报 | | |
| GET | `/api/csp-violations` | CSP 违规列表 | | |
| GET | `/api/prometheus` | Prometheus 指标 | | |
| WS | `/ws` | WebSocket 实时推送 | | 15s 快照 |
| GET | `/api/v1/version` | API 版本 | | |
| GET | `/{full_path:path}` | SPA fallback | | |
| POST | `/a2a` | JSON-RPC A2A | | 条件挂载 |

```bash
curl http://127.0.0.1:9079/api/health
curl http://127.0.0.1:9079/api/prometheus
curl http://127.0.0.1:9079/api/v1/version
```

---

## 15. Error Handling

### 标准错误响应

```json
{"detail": "Resource not found", "code": "NOT_FOUND"}
```

全局 500 处理器统一捕获未预期异常，仅返回通用错误信息，不泄露内部细节：

```json
{"detail": "Internal server error", "code": "INTERNAL_ERROR"}
```

### 中间件状态码

| Middleware | 可能返回 | Notes |
| --- | --- | --- |
| CSP | 400 / 403 | Content-Security-Policy 校验 |
| Auth | 401 / 403 | JWT 校验失败或权限不足 |
| RateLimit | 429 | 超出限流阈值 |
| CORS | 403 | 跨域拒绝 |

---

## 16. SDK & Client Examples

### Python 完整流程

```python
import requests, json, time, websockets, asyncio

BASE = "http://127.0.0.1:9079"

# 1. Login
token = requests.post(f"{BASE}/api/auth/login",
    json={"username":"admin","password":"changeme"}).json()["token"]
H = {"Authorization": f"Bearer {token}"}

# 2. Trigger task
run = requests.post(f"{BASE}/api/control/run",
    headers=H, json={"goal":"总结本周会议纪要"}).json()
trace_id = run["trace_id"]

# 3. Listen via SSE
import urllib.request
req = urllib.request.Request(f"{BASE}/api/stream/{trace_id}", headers=H)
with urllib.request.urlopen(req) as r:
    for line in r:
        print(line.decode().strip())

# Or via WebSocket
async def listen():
    async with websockets.connect(f"ws://127.0.0.1:9079/ws?token={token}") as ws:
        for _ in range(5):
            print(await ws.recv())
asyncio.run(listen())

# 4. Fetch final report
report = requests.get(f"{BASE}/api/report", headers=H).json()
print(json.dumps(report, ensure_ascii=False, indent=2))
```

### curl 完整流程

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://127.0.0.1:9079/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 2. Run task
TRACE_ID=$(curl -s -X POST http://127.0.0.1:9079/api/control/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"goal":"总结本周会议纪要"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['trace_id'])")

# 3. Stream progress
curl -N http://127.0.0.1:9079/api/stream/$TRACE_ID \
  -H "Authorization: Bearer $TOKEN"

# 4. Get report
curl http://127.0.0.1:9079/api/report -H "Authorization: Bearer $TOKEN"
```

---

**MAOP v5.1.0** · API Reference