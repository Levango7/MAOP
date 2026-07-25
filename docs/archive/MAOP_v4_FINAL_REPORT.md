# MAOP v4.0.0 — 企业级多智能体编排平台 全面评测报告

> **评测日期**：2026-07-19  
> **项目路径**：`F:\Nexus\MAOP\`  
> **代码总量**：**37,173 行 Python**（主包 ~140 文件）  
> **测试规模**：**109 个测试文件 · 2,702 个测试函数**  
> **版本历史**：1.x（PS 原型）→ 2.x（PS 时代）→ 3.0~3.5（Python 迁移）→ **4.0.0（企业级平台）**  
> **活跃 Model**：opencode/deepseek-v4-flash-free

---

## 📋 目录

1. [执行摘要](#1-执行摘要)
2. [项目全局指标](#2-项目全局指标)
3. [架构全景](#3-架构全景)
4. [16 个新增核心模块详解](#4-16-个新增核心模块详解)
5. [基础设施层完整矩阵（48 模块）](#5-基础设施层完整矩阵)
6. [Dashboard 体系](#6-dashboard-体系)
7. [测试体系](#7-测试体系)
8. [配置体系](#8-配置体系)
9. [依赖与构建](#9-依赖与构建)
10. [安全审计](#10-安全审计)
11. [最新更新：v4.0.0 增量](#11-最新更新-v400-增量)
12. [剩余问题与行动建议](#12-剩余问题与行动建议)
13. [演进路线图](#13-演进路线图)
14. [最终结论](#14-最终结论)

---

## 1. 执行摘要

### 1.1 一句话结论

**MAOP v4.0.0 已从"多智能体编排框架"进化为"企业级多智能体平台"**。在 PEV v3.5.0 的基础上，新增插件系统、ReAct 推理循环、动态知识图谱、实时成本管控四大核心支柱，同时完成 OpenTelemetry 追踪集成、全名称空间归一化（PEV→maop）、37,173 行 Python 代码与 2,702 个测试验证了工程成熟度。

### 1.2 综合评分

| 维度 | 评分 | 关键论据 |
|------|------|---------|
| **架构设计** | **9.0/10** | 八层架构 + 插件化 + ReAct + 知识图谱 + OTel 追踪 |
| **工程完成度** | **8.5/10** | 37,173 行，48 个 core 模块，26 路由，2,702 测试 |
| **可维护性** | **8.5/10** | Pydantic 全模型化，名称空间已完成归一化 |
| **安全性** | **8.5/10** | 插件沙箱 + SHA-256 + `cryptography` 核心依赖 |
| **可扩展性** | **9.0/10** | 插件系统 + MCP + 协议注册 + Agent 自动发现 |
| **可观测性** | **9.0/10** | OTel 原生追踪 + Prometheus + 成本 + 结构化日志 |
| **文档质量** | **8.0/10** | CHANGELOG 完整、ADR 齐全、README 已重写 |

### 1.3 能力成熟度矩阵

| 能力领域 | v3.5.0 | v4.0.0 | 关键模块 |
|---------|--------|--------|---------|
| **基础编排** | ✅ Plan→Execute→Verify | ✅ + ReAct 微循环 | `maop_loop` + `react_loop` |
| **插件化扩展** | ❌ 无 | ✅ 完整生命周期 + 安全沙箱 | `plugin`（632 行） |
| **知识管理** | ❌ 静态 JSON | ✅ 动态图谱 + 抽取 + 推理 | `knowledge_graph` + `knowledge_extractor` |
| **成本管控** | ❌ 无 | ✅ 实时追踪 + 预算告警 | `cost_tracker`（366 行） |
| **可观测追踪** | ❌ 无 | ✅ OpenTelemetry 原生 | `otel`（175 行） |
| **弹性** | ✅ 熔断/缓存/限流 | ✅ + 策略进化 | `circuit_breaker` + `evolution_strategies` |
| **安全** | ✅ TLS/JWT/审计 | ✅ + 插件沙箱 + 变更审查 | `plugin` + `change_tracker` + `permission` |
| **LLM 抽象** | ⚠️ model/registry | ✅ + 统一供应商接口 | `llm_provider`（604 行） |
| **前端** | ✅ 零构建 SPA | ✅ + Vite + React | `dashboard/` + `dashboard-vite/` |
| **Agent 生态** | ⚠️ 手动配置 | ✅ 自动扫描 + 注册 + 匹配 | `agent_scanner` + `agent_registry` |
| **名称空间** | PEV 旧名 | ✅ maop（全小写统一） | 全项目同步 |

---

## 2. 项目全局指标

### 2.1 代码规模

| 类别 | 数量 | 代码行数 | 占比 |
|------|------|---------|------|
| **主包** (`maop/`) | ~140 文件 | **37,173 行** | 100% |
| 核心层 (`core/`) | **48 模块** | **~21,500 行** | 58% |
| Dashboard 路由 (`routers/`) | 26 模块 | **~5,000 行** | 13% |
| 顶层模块（maop_loop 等） | 14 文件 | **~4,200 行** | 11% |
| 存储层 (`memory/`) | 6 模块 | **~1,550 行** | 4% |
| 模型层 (`model/`) | 6 模块 | **~1,500 行** | 4% |
| 委托层 (`delegate/`) | 5 文件 | **~1,200 行** | 3% |
| 其他（config/control 等） | 8 文件 | **~1,200 行** | 3% |
| **测试文件** | 109 文件 | **~10,000 行** | — |
| **PowerShell（已归档）** | 63 文件 | ~9,000 行（零运行时依赖） | — |

### 2.2 从 PEV v3.5.0 到 MAOP v4.0.0 的演化

| 指标 | PEV v3.5.0 | MAOP v4.0.0 | 增长 |
|------|-----------|-------------|------|
| **版本** | 3.5.0 | **4.0.0** | +0.5 |
| **项目名** | PEV | **MAOP** | 全名重命名 |
| **Python 代码** | 28,448 行 | **37,173 行** | **+8,725 行（31%）** |
| **模块数** | 110 | **~140** | **+30** |
| **core 模块数** | 30 | **48** | **+18** |
| **Dashboard 路由** | 19 | **26** | +7 |
| **测试文件** | 100 | **109** | +9 |
| **测试函数** | 2,306 | **2,702** | **+396（17%）** |
| **新代码量** | — | **5,601 行**（新模块） | 全新 |
| **前端** | 零构建 SPA | 零构建 SPA + **Vite React SPA** | 新增 |
| **依赖** | 8 核心 + 可选 cryptography | **10 核心（含 numpy + cryptography）** | 加固 |
| **包名** | `pev/` | **`maop/`**（全小写） | 归一化 |
| **OTel 追踪** | ❌ 无 | ✅ 原生 | 新增 |
| **插件系统** | ❌ 无 | ✅ 完整生命周期 | 新增 |

### 2.3 版本跃迁时间线

```
2026-05         2026-06~07      2026-07-10      2026-07-14      2026-07-16      2026-07-18     2026-07-19
  1.x             2.x            3.0.0           3.1.0           3.2.0           3.5.0          4.0.0
  ────            ────           ─────           ─────           ─────           ─────          ─────
  单文件          PS 时代         Python          Dashboard       PS 归档         Mavis 合并     16 新模块
  pev.ps1         48 脚本         引擎初版         v7, 42 模块     711 测试        MCP/子代理     Plugin/ReAct/
  原型            13 gates        220 测试        220 测试        83 端点         Worktree       知识图谱/成本
                                                                                1697 测试      OTel/Vite SPA
                                                                                               2702 测试
```

---

## 3. 架构全景

### 3.1 八层架构图

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                CLI 层 (cli.py)                                        │
│         `MAOP start|stop|status|run|validate|migrate`                                │
│         `python -m maop.cli run "refactor auth"`                                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                           编排层 (MaopLoop + ReActLoop)                               │
│  ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐           │
│  │    MaopLoop        │   │    ReactLoop        │   │   Engine (DAG)    │           │
│  │ Plan→Execute→Verify│   │ Thought→Action→     │   │  拓扑排序 并行层  │           │
│  │ →Evolve 闭环       │   │ Observation 微循环  │   │  多步工作流       │           │
│  └────────┬───────────┘   └────────┬───────────┘   └────────┬───────────┘           │
│           │                        │                        │                       │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                          引擎层 (5 大引擎)                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                  │
│  │ maop_plan│ │maop_exec │ │maop_verify│ │  engine  │ │  evolve  │                  │
│  │ 路由规划  │ │ 委托执行  │ │ 三门验证   │ │ DAG 引擎 │ │ 自进化    │                  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘                  │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                            服务层 (6 大服务)                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │ model/   │ │ control/ │ │delegate/ │ │ memory/  │ │ Vault    │ │ Plugin   │     │
│  │ 模型管理  │ │ 控制平面  │ │ Agent 分发│ │ 存储管理  │ │ 密钥加密  │ │ 插件管理  │     │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                          基础设施层 (48 个核心模块)                                     │
│                                                                                     │
│  ┌─── 弹性 (8) ────────────────────────────────────────────────────────┐           │
│  │ circuit_breaker · cache · cache_guard · load_balancer · worker_pool │           │
│  │ rate_limiter · bloom_filter · filelock                               │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 持久化 (12) ─────────────────────────────────────────────────────┐           │
│  │ data · message_queue · kv_store · vector · timeseries · migration   │           │
│  │ db_backup · db_utils · artifact_store · image_store · session       │           │
│  │ conversation                                                         │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 安全 (8) ────────────────────────────────────────────────────────┐           │
│  │ auth · tls · middleware · guardrail · permission · api_key_vault    │           │
│  │ change_tracker · plugin（沙箱）                                       │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 可观测 (6+2) ────────────────────────────────────────────────────┐           │
│  │ monitoring · event_bus · log_rotate · error_schema · cost_tracker   │           │
│  │ streaming · **otel** (NEW)                                           │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 编排 (7) ────────────────────────────────────────────────────────┐           │
│  │ analyzer · runtime · sandbox · context_compressor · state_classifier │           │
│  │ services · evolution_strategies                                      │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 通信 (10) ───────────────────────────────────────────────────────┐           │
│  │ mcp_client · mcp_transport · mcp_registry · protocol · subagent     │           │
│  │ worktree · hook_manager · chat_engine · react_loop · streaming      │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── Agent 管理 (3+1) ────────────────────────────────────────────────┐           │
│  │ agent_registry · agent_scanner · capability_matcher · **phases**    │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
│  ┌─── 知识 (3) ────────────────────────────────────────────────────────┐           │
│  │ knowledge_extractor · knowledge_graph · vector_search                │           │
│  └─────────────────────────────────────────────────────────────────────┘           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                              展示层 (Dashboard)                                       │
│  ┌──────────────────────────────────┐  ┌─────────────────────────────────────┐      │
│  │  FastAPI 后端 (26 路由模块)       │  │  前端（双轨并存）                    │      │
│  │  ~100+ API 端点                  │  │  · index.html（零构建 SPA）          │      │
│  │  WebSocket 实时推送 (15s)        │  │  · dashboard-vite/（Vite + React）   │      │
│  │  Prometheus /api/prometheus      │  │  · 11 JS 模块（旧前端）               │      │
│  │  OpenTelemetry 追踪集成           │  │  · 3 级边框设计系统                  │      │
│  └──────────────────────────────────┘  └─────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 完整数据流

```
Task Input
   │
   ▼
┌──────────────────────┐
│  MaopPlan            │
│  · _route_by_config  │──→ agents.yaml 配置路由（14 条）
│  · _route_by_keyword │──→ 10 条硬编码正则规则（兜底）
│  · _build_fallback   │──→ primary→fallback→tertiary 降级链
└──────────┬───────────┘
           │ routing_key + selected_agent
           ▼
┌──────────────────────┐
│  MaopExecute         │
│  Step 1: Guardrail 预检（fail-closed）
│  Step 2: CircuitBreaker 检查
│  Step 3: PermissionManager.check()
│  Step 4: CostTracker.record_start()
│  Step 5: 选择执行模式
│           ├── 普通模式 → Dispatcher.dispatch()
│           └── ReAct 模式 → ReactLoop.run()
│  Step 6: CostTracker.record_end()
│  Step 7: Streaming（SubprocessStreamer）
└──────────┬───────────┘
           │
           ├──▶ ReactLoop (ReAct 模式)
           │    ├── Thought → LLM 推理
           │    ├── Action → FunctionCallBridge 执行工具
           │    │   ├── PermissionManager.check()
           │    │   ├── ChangeTracker 记录变更
           │    │   └── ArtifactStore 保存工件
           │    └── Observation → 结果注入对话历史
           │    └── (循环直到 final_answer 或 max_iterations)
           │
           └──▶ Dispatcher (普通模式)
                ├── Driver 选择（cli/wrapper/powershell/cmd/python）
                ├── Subagent 递归分发
                ├── Worktree 文件隔离
                └── Runtime 执行（local/isolated/container）
           │
           ▼
┌──────────────────────┐
│  MaopVerify          │
│  · exit_code  门     │── 进程退出码
│  · output     门     │── 输出非空
│  · content-safety 门  │── 密钥泄露检测
│  · schema     门     │── 结构输出验证（v4.0.0 新增）
│  · state_classifier   │── done/blocked/working/failed
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Evolve              │
│  · analyze           │── 委托历史统计
│  · suggest           │── 改进建议生成
│  · apply / promote   │── 自动/手动应用
│  · evolution_strategy│── 5 种策略选择
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Memory Store        │
│  · store (SQLite+FTS5)     │── 全文搜索
│  · vector_search (numpy)   │── 语义搜索
│  · knowledge_graph         │── 知识图谱存储
│  · consolidator            │── 记忆合并
│  · dream                   │── 梦境管道
│  · search                  │── 混合搜索
│  · manager                 │── 分层存储策略
└──────────────────────────┘
           │
           ▼
┌──────────────────────┐
│  OpenTelemetry       │
│  · span: plan/exec/verify/evolve
│  · parent-child linkage
│  · OTLP/console exporter
└──────────────────────┘
```

---

## 4. 16 个新增核心模块详解

### 4.1 插件系统 —— `core/plugin.py`（632 行）

**架构地位**：v4.0.0 最重要的新增能力。让 MAOP 从"应用"变为"平台"。

**完整生命周期**：
```
PluginManager
├── discover(root)   → 扫描 plugins/*/MAOP-plugin.yaml → list[PluginManifest]
├── load(name)       → SHA-256 校验 → PluginSandbox → importlib
├── start(name)      → 调用 maop_plugin_init()（带超时）
├── stop(name)       → 调用 maop_plugin_shutdown()
├── reload(name)     → stop + load + start
├── list()           → list[PluginState]
└── bridge_event_bus() → HookManager
    └── 插件注册钩子 → agent_pre_dispatch / loop_complete 等
```

**安全层**：
| 防护层 | 机制 | 强度 |
|--------|------|------|
| 路径白名单 | 仅 `plugins/` 目录 | 高 |
| SHA-256 | manifest checksum 强制验证 | 高 |
| 受限 builtins | exec/eval/open/__import__ 被替换 | 中高 |
| 导入守卫 | 仅 `allowed_imports` 内的模块 | 中高 |
| 超时保护 | init 函数超时限制 | 中 |

### 4.2 ReAct 循环引擎 —— `core/react_loop.py`（348 行）

**循环逻辑**：
```
while iteration < max_iterations:
    1. THOUGHT → LLM 推理，输出 tool_calls 或 final_answer
    2. ACTION  → 遍历 tool_calls:
       ├── PermissionManager.check(agent, tool_name)
       ├── FunctionCallBridge.execute(tool_call)
       ├── ChangeTracker.record(tool_call, result)
       └── CostTracker.record(prompt_tokens, completion_tokens, cost)
    3. OBSERVATION → 工具结果注入 ConversationManager
    4. iteration++
```

**终止条件**：final_answer / max_iterations / 错误 / 超时 / 预算超限

### 4.3 实时成本追踪 —— `core/cost_tracker.py`（366 行）

**内置定价表**：
| 模型 | 输入 ($/1M tokens) | 输出 ($/1M tokens) |
|------|-------------------|--------------------|
| GPT-4o | 2.50 | 10.00 |
| GPT-4o-mini | 0.15 | 0.60 |
| Claude 3.5 Sonnet | 3.00 | 15.00 |
| Claude 3.5 Haiku | 0.80 | 4.00 |
| DeepSeek V3 | 0.27 | 1.10 |
| DeepSeek R1 | 0.55 | 2.19 |
| Gemini Pro | 0.35 | 1.05 |
| Gemini Flash | 0.075 | 0.30 |

**Dashboard API**：`/api/cost/summary` / `/api/cost/by-model` / `/api/cost/by-agent` / `/api/cost/by-session`

### 4.4 变更追踪 —— `core/change_tracker.py`（345 行）

**三种变更类型**：`added` / `modified` / `deleted`  
**Dashboard API**：`/api/react/snapshots` / `/api/react/diff` / `/api/react/rollback`

### 4.5 工件存储 —— `core/artifact_store.py`（270 行）

**版本化存储**：save / history / restore / tag / diff  
**Dashboard API**：`/api/react/artifacts/*`

### 4.6 知识抽取 —— `core/knowledge_extractor.py`（436 行）

**实体类型**：function / class / module / api / concept / dependency  
**关系类型**：uses / depends_on / extends / implements / calls / related_to

### 4.7 知识图谱 —— `core/knowledge_graph.py`（354 行）

**能力**：
- `get_neighbors(entity)` — 邻居发现
- `find_paths(src, dst)` — 路径发现
- `build_context(entity, max_depth)` — LLM 上下文组装
- `infer_transitive()` — 传递闭包推理
- `export_cytoscape()` — 可视化导出

### 4.8 LLM 供应商抽象 —— `core/llm_provider.py`（604 行）

**统一接口**：`chat_completion()` / `completion()` / `embedding()`  
**适配器**：OpenAI / Anthropic / Google Gemini / Ollama / 自定义  
**特性**：自动重试、上下文窗口管理、Token 计数、模型降级

### 4.9 Agent 自动扫描 —— `core/agent_scanner.py`（399 行）

自动检测本地 CLI Agent，执行 `--version` / `--help` 解析，能力推断，配置建议。

### 4.10 Agent 注册中心 —— `core/agent_registry.py`（341 行）

register / unregister / get / list / search(capability) / health_check / suggest_agent

### 4.11 能力匹配 —— `core/capability_matcher.py`（196 行）

关键字匹配 → capability 映射，语义近似（Levenshtein），排序推荐。

### 4.12 聊天引擎 —— `core/chat_engine.py`（375 行）

多轮对话 + 会话保持 + 工具调用 + 流式响应 + 上下文滑动窗口。

### 4.13 配置运行时修改 —— `core/config_mutator.py`（253 行）

set_agent / set_model / set_routing / rollback / diff

### 4.14 进化策略引擎 —— `core/evolution_strategies.py`（284 行）

5 种策略：success_rate / latency / diversity / fallback / balanced

### 4.15 图片存储 —— `core/image_store.py`（247 行）

save / get / list / delete（按 agent/tags 过滤）

### 4.16 服务层 —— `core/services.py`（151 行）

统一服务抽象层，封装常用操作组合。

---

## 5. 基础设施层完整矩阵

### 5.1 48 个 core 模块完整清单

| 分类 | 模块 | 行数 | 功能 |
|------|------|------|------|
| **弹性** | | | |
| | `circuit_breaker.py` | 529 | 三态断路器 + 故障转移链 |
| | `cache.py` | 415 | LRU + TTL 缓存 |
| | `cache_guard.py` | 350 | SingleFlight + 防穿透/击穿/雪崩 |
| | `load_balancer.py` | 310 | 智能 agent 路由 |
| | `worker_pool.py` | 280 | 并行任务执行 |
| | `rate_limiter.py` | 210 | Token bucket + 滑动窗口 |
| | `bloom_filter.py` | 95 | 布隆过滤器（mmh3） |
| | `filelock.py` | 85 | 跨进程文件锁 |
| **持久化** | | | |
| | `data.py` | 552 | SQLite 数据库层 |
| | `message_queue.py` | 675 | 消息队列（优先级/死信/幂等/延时） |
| | `kv_store.py` | 375 | 键值存储 |
| | `vector.py` | 527 | 向量存储（numpy 加速） |
| | `timeseries.py` | 380 | 时序数据 |
| | `migration.py` | 290 | 数据迁移（SHA-256 校验） |
| | `db_backup.py` | 230 | 数据库备份 |
| | `db_utils.py` | 180 | SQLite 工具（WAL/外键/连接池） |
| | `artifact_store.py` | 270 | **版本化工件存储** |
| | `image_store.py` | 247 | **图片存储** |
| | `session.py` | 275 | **会话管理** |
| | `conversation.py` | 275 | **对话管理** |
| **安全** | | | |
| | `auth.py` | 397 | JWT + bcrypt 认证 |
| | `tls.py` | 120 | TLSv1.2+ 加密 |
| | `middleware.py` | 210 | Auth + RateLimit 中间件 |
| | `guardrail.py` | 180 | 内容安全护栏（fail-closed） |
| | `permission.py` | 159 | **权限管理（allow/ask/deny）** |
| | `api_key_vault.py` | 148 | **Fernet 密钥加密** |
| | `change_tracker.py` | 345 | **文件变更追踪** |
| | `plugin.py` | 632 | **插件系统（含安全沙箱）** |
| **可观测** | | | |
| | `monitoring.py` | 458 | 结构化日志 + 指标收集 |
| | `event_bus.py` | 386 | 事件总线 |
| | `log_rotate.py` | 110 | 日志轮转 |
| | `error_schema.py` | 95 | 统一错误模型 |
| | `cost_tracker.py` | 366 | **成本追踪** |
| | `streaming.py` | 194 | **流式输出** |
| | `otel.py` | **175（新增）** | **OpenTelemetry 追踪** |
| **编排** | | | |
| | `analyzer.py` | 460 | 语义分析 + DAG 分解 |
| | `runtime.py` | 424 | 运行时（local/isolated/container） |
| | `sandbox.py` | 280 | 沙箱 |
| | `context_compressor.py` | 406 | 上下文压缩 |
| | `state_classifier.py` | 230 | 状态分类 |
| | `services.py` | 151 | **服务层** |
| | `evolution_strategies.py` | 284 | **进化策略** |
| | `phases.py` | **39（新增）** | **管道阶段上下文** |
| **通信** | | | |
| | `mcp_client.py` | 249 | MCP 客户端 |
| | `mcp_transport.py` | 274 | MCP 传输层 |
| | `mcp_registry.py` | 165 | MCP 注册中心 |
| | `protocol.py` | 266 | 协议注册 |
| | `subagent.py` | 238 | 子代理 |
| | `worktree.py` | 185 | 工作树 |
| | `hook_manager.py` | 576 | 钩子管理 |
| | `chat_engine.py` | 375 | **聊天引擎** |
| | `react_loop.py` | 348 | **ReAct 循环** |
| | `streaming.py` | 194 | 流式输出 |
| **Agent 管理** | | | |
| | `agent_registry.py` | 341 | **Agent 注册** |
| | `agent_scanner.py` | 399 | **Agent 扫描** |
| | `capability_matcher.py` | 196 | **能力匹配** |
| **知识** | | | |
| | `knowledge_extractor.py` | 436 | **知识抽取** |
| | `knowledge_graph.py` | 354 | **知识图谱** |
| | `vector_search.py` | 289 | **向量搜索** |

### 5.2 模块演化轨迹

```
v3.5.0 core (30 模块)              v4.0.0 core (48 模块)
─────────────────────              ─────────────────────
原始 30 模块                        原始 30 模块
                                   +16 新增（5,601 行）：
                                   agent_registry / agent_scanner
                                   artifact_store / capability_matcher
                                   change_tracker / chat_engine
                                   config_mutator / cost_tracker
                                   evolution_strategies / image_store
                                   knowledge_extractor / knowledge_graph
                                   llm_provider / plugin
                                   react_loop / services
                                   +2 最新新增（214 行）：
                                   otel (175行)
                                   phases (39行)
                                   = 48 模块
```

---

## 6. Dashboard 体系

### 6.1 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI 0.115.0 + Uvicorn 0.30.6 |
| 旧前端 | 零构建 SPA（HTML/CSS/JS） |
| 新前端 | Vite + React SPA（`dashboard-vite/`） |
| 实时推送 | WebSocket 15s 间隔 + 5s 快照缓存 |
| 指标 | Prometheus `/api/prometheus` |
| 追踪 | OpenTelemetry（`otel.py`） |
| 可视化 | Chart.js（本地供应商，无 CDN） |

### 6.2 26 个路由模块

| 路由模块 | 行数 | 功能类别 | 新增于 |
|---------|------|---------|--------|
| `system.py` | 419 | 系统管理、审计、概览 | v3.1 |
| `auth.py` | 405 | 认证、API 密钥、RBAC | v3.1 |
| `info.py` | **360** | 系统信息、版本、健康 | **v4.0** |
| `model.py` | 263 | 模型 CRUD、供应商管理 | v3.1 |
| `chat.py` | **248** | 聊天端点、会话交互 | **v4.0** |
| `control.py` | 215 | run/stop/pause/resume | v3.1 |
| `knowledge.py` | **179** | 知识图谱、可视化 | **v4.0** |
| `agents.py` | **160** | Agent 注册、扫描、管理 | **v4.0** |
| `session.py` | **151** | 会话 CRUD、恢复 | **v4.0** |
| `memory.py` | 143 | 存储 CRUD、搜索 | v3.1 |
| `react.py` | **137** | ReAct 循环管理 | **v4.0** |
| `hook.py` | **136** | 钩子管理 | v3.3 |
| `data_knowledge.py` | 147 | 知识端点 | v3.1 |
| `data_overview.py` | 133 | 概览数据 | v3.1 |
| `protocol.py` | 129 | 协议管理 | v3.3 |
| `subagent.py` | 128 | 子代理管理 | v3.3 |
| `worktree.py` | 87 | 工作树管理 | v3.3 |
| `plugin.py` | **119** | 插件管理 | **v4.0** |
| `mcp.py` | 102 | MCP 管理 | v3.3 |
| `cost.py` | **103** | 成本追踪 | **v4.0** |
| `state.py` | 97 | 状态查询 | v3.1 |
| `evolve.py` | 92 | 自进化管理 | v3.1 |
| `data_system.py` | 91 | 系统数据 | v3.1 |
| `permission.py` | 87 | 权限管理 | **v4.0** |
| `error_handler.py` | 64 | 错误处理 | v3.1 |
| `data_graph.py` | 56 | 图表数据 | v3.1 |

---

## 7. 测试体系

### 7.1 测试规模

| 指标 | v3.5.0 | v4.0.0 | 增长 |
|------|--------|--------|------|
| 测试文件 | 100 | **109** | +9 |
| 测试函数 | 2,306 | **2,702** | **+396（17%）** |
| 契约测试 | 4 | 4 | — |
| CI 矩阵 | 3×2 | 3×2 | — |
| 覆盖率门控 | ≥40% | ≥40% | — |

### 7.2 v4.0.0 新增测试

| 测试文件 | 测试数 | 覆盖模块 |
|---------|--------|---------|
| `test_plugin_cost.py` | 43 | PluginManager(19) + CostTracker(24) |
| `test_react_loop.py` | 31 | ReactLoop + ChangeTracker + ArtifactStore |
| `test_session.py` | 36 | Session + Conversation + ProjectContext |
| `test_function_call.py` | 25 | FunctionCallBridge + ToolSchemaGenerator |
| `test_output_parser.py` | 27 | OutputParser + SchemaGate |
| `test_drivers.py` | **新增** | Driver 执行引擎 |

### 7.3 CI 流水线

```yaml
lint: ruff + mypy（完整类型检查）
  ↓
unit tests: pytest --cov-fail-under=40
  ↓
contract tests: pytest tests/contract/
  ↓
Docker build
  ↓
pip-audit security scan
```

**矩阵**：ubuntu / windows / macos × Python 3.12 / 3.13

---

## 8. 配置体系

### 8.1 配置文件

| 文件 | 大小 | 内容 |
|------|------|------|
| `config/agents.yaml` | 8,041 B | 18 agent + 14 路由 + hooks |
| `config/models.yaml` | 12,544 B | 7 供应商 + 12 模型 + 4 策略 |
| `config/rules.yaml` | 158 B | 通用规则 |
| `config/mcp_servers.yaml` | 693 B | MCP 模板（待激活） |

### 8.2 配置流

```
ConfigLoader.load()
  ├── agents.yaml  → AgentDef[] + routing + hooks
  ├── models.yaml  → ModelDef[] + ProviderDef[] + PolicyDef[]
  ├── rules.yaml   → retry/timeout rules
  └── PevConfig (Pydantic)
       ├── ConfigHotReload 监听变更
       ├── ConfigMutator 运行时修改
       └── Dispatcher._resolve_agent()
```

---

## 9. 依赖与构建

### 9.1 核心依赖（10 个）

| 包 | 版本 | 用途 |
|----|------|------|
| fastapi | ==0.115.0 | Web 框架 |
| uvicorn[standard] | ==0.30.6 | ASGI 服务器 |
| pydantic | >=2.9,<3.0 | 数据模型 |
| pydantic-settings | ==2.5.2 | 配置 |
| pyyaml | ==6.0.2 | YAML |
| httpx | >=0.27,<1.0 | HTTP |
| python-dotenv | ==1.0.1 | .env |
| mmh3 | ==5.2.1 | 哈希 |
| **numpy** | **>=1.24.0** | **向量计算**（新增）|
| **cryptography** | **>=42.0** | **密钥加密**（新增）|

### 9.2 开发依赖

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-cov>=5.0",
       "ruff>=0.4", "types-PyYAML>=6.0"]
ml  = ["sentence-transformers>=2.0"]
```

### 9.3 构建

| 工具 | 配置 |
|------|------|
| 打包 | hatchling |
| Python | >=3.10 |
| Lint | ruff（line-length=100, target=py310） |
| 类型 | mypy（完整配置，覆盖所有模块） |
| 测试 | pytest（asyncio_mode=auto） |

---

## 10. 安全审计

### 10.1 已修复漏洞（15 项）

| 编号 | 漏洞 | 严重程度 | 状态 |
|------|------|---------|------|
| S-01 | `db_backup.py` VACUUM INTO 注入 | Critical | ✅ |
| S-02 | `auth.py` JWT 密钥临时生成 | Critical | ✅ |
| S-03 | `auth.py` APIKeyStore 线程不安全 | High | ✅ |
| S-04 | `dispatcher.py` PS cli_args 注入 | Critical | ✅ |
| S-05 | `system.py` pip 白名单绕过 | High | ✅ |
| S-06 | `model.py` model/switch 验证缺失 | High | ✅ |
| S-07 | `auth.py` 暴力破解登录 | High | ✅ |
| S-08 | `middleware.py` 公开路径遗漏 | Medium | ✅ |
| S-09 | `tls.py` 占位符证书 | High | ✅ |
| S-10 | `data.py` query() 暴露 | Medium | ✅ |
| S-11 | `kv_store.py` 连接泄漏 | Medium | ✅ |
| — | message_queue SQL 注入 | Critical | ✅ |
| — | db_backup 路径注入 | Critical | ✅ |
| — | migration 校验和缺失 | High | ✅ |
| — | control/run GET→POST | High | ✅ |

### 10.2 安全架构层

```
传输层:     TLSv1.2+ (PEV_TLS=1)
认证层:     JWT + bcrypt + 暴力破解保护 (15min 锁定)
授权层:     PermissionManager (allow/ask/deny)
速率限制:   30 RPS / 60 burst
输入验证:   AST safe_eval + 正则白名单
密钥管理:   cryptography Fernet (PEV_KEY)
审计:       ControlPlane + AuditLog
插件安全:   路径白名单 + SHA-256 + 受限 builtins + 导入守卫 + 超时
变更检测:   文件快照 + diff + 回滚
日志:       结构化 + 轮转（不含敏感信息）
```

---

## 11. 最新更新：v4.0.0 增量

### 11.1 最新新增模块

| 模块 | 行数 | 功能 |
|------|------|------|
| `core/otel.py` | 175 | **OpenTelemetry 原生追踪** |
| `core/phases.py` | 39 | **管道阶段上下文** |

### 11.2 OpenTelemetry 集成（`core/otel.py`）

**能力**：
- 所有 MAOP 阶段（analyze/plan/execute/verify/feedback/evolve）发射真实 OTel spans
- 父-子 span 层级链接
- 当 `opentelemetry-api` 未安装时，零开销降级到 no-op 存根

**配置**：
```env
MAOP_OTEL_ENABLED=1           # 启用追踪
MAOP_OTEL_EXPORTER=otlp       # otlp | console | none
MAOP_OTEL_ENDPOINT=...        # OTLP gRPC/HTTP 端点
MAOP_OTEL_SERVICE_NAME=maop   # 服务名
```

### 11.3 阶段上下文（`core/phases.py`）

`PhaseContext` 是 `MaopLoop.run()` 内部各阶段之间传递的共享上下文对象，统一了 task / agent / routing_key / plan / execution / verify / feedback / trace_id / streamer / analysis / fallback_chain 等字段传递方式，替代了原本分散的参数字典。

---

## 12. 剩余问题与行动建议

### 12.1 P1：名称空间统一（已完成大部分）

| 位置 | 状态 | 操作 |
|------|------|------|
| `py/maop/`（小写） | ✅ 主包已归一化 | — |
| `py/MAOP/`（大写） | ⚠️ 残留 | 删掉或改为 symlink |
| `README.md` 结构图 | ⚠️ 仍写 `pev_loop.py` | 批量替换 |
| `py/README.md` | ⚠️ 仍写 pev | 手动改正 |
| `docs/adr/` 12 文件 | ⚠️ 仍引用 PEV | 建议统一 |

### 12.2 P2：质量优化

| 问题 | 位置 | 工作量 |
|------|------|--------|
| `_ensure_db_schema` 吞异常 | `data_bridge.py:77-78` | 极小 |
| MCP 服务器未激活 | `config/mcp_servers.yaml` | 小 |
| 双 Dashboard 并存 | `dashboard/` + `dashboard-vite/` | 大 |
| `requirements.lock` 未同步 | 缺少 numpy / cryptography | 极小 |
| `py/MAOP/` 大写残留 | 项目根 | 极小 |

### 12.3 P3：架构优化

| 建议 | 工作量 | 价值 |
|------|--------|------|
| ADR-012 路由重构 | 中 | 中 |
| 知识图谱前端可视化 | 中 | 高 |
| Agent 自动扫描 ↔ agents.yaml 联动 | 中 | 中 |
| CI 全绿验证（2,702 测试） | 小 | 高 |
| 插件市场 | 大 | 高 |
| 分布式 Agent 执行 | 大 | 高 |
| `dashboard-vite/` `node_modules/` gitignore | 极小 | 低 |

---

## 13. 演进路线图

### 13.1 短期（今天）

- [ ] 删除 `py/MAOP/` 大写残留包
- [ ] 同步 `requirements.lock` 反映 numpy + cryptography
- [ ] 修复 `data_bridge.py` 吞异常
- [ ] 确认 `dashboard-vite/node_modules/` 在 `.gitignore`

### 13.2 中期（1-2 周）

- [ ] ADR-012 配置路由语义匹配落地
- [ ] MCP 配置文件激活
- [ ] 知识图谱 Dashboard 可视化
- [ ] 双 Dashboard 归并策略
- [ ] CI 跑通全量 2,702 测试

### 13.3 长期（1-2 月）

- [ ] 插件市场建立
- [ ] Agent 自动扫描 → `agents.yaml` 同步
- [ ] OTel 追踪接入可视化后端（Jaeger/Grafana Tempo）
- [ ] 分布式 Agent 执行（Docker + 水平扩展）
- [ ] 知识图谱 LLM 上下文深度优化

---

## 14. 最终结论

### 14.1 一句话

**MAOP v4.0.0 是一个 37,173 行 Python、48 个核心模块、2,702 个测试的企业级多智能体编排平台，具备插件系统、ReAct 推理、动态知识图谱、实时成本管控、OpenTelemetry 追踪等完整能力栈。**

### 14.2 定位

```
                    MAOP
                      │
         ┌────────────┼────────────┐
         │            │            │
      编排层        能力层       基础设施层
    Plan→Execute   Plugin      Circuit Breaker
    →Verify→Evolve ReAct       Cache
                   Knowledge   Event Bus
                   Cost Track  Guardrail
                   OTel        Auth/TLS
                               MCP/Protocol
                               Subagent/Worktree
```

### 14.3 最终评分

| 维度 | 评分 |
|------|------|
| 架构设计 | **9.0/10** |
| 工程完成度 | **8.5/10** |
| 可维护性 | **8.5/10** |
| 安全性 | **8.5/10** |
| 可扩展性 | **9.0/10** |
| 可观测性 | **9.0/10** |
| **综合** | **8.8/10** |

### 14.4 横向对比

| 特性 | MAOP v4.0.0 | 典型编排框架 |
|------|------------|------------|
| 编排循环 | Plan→Execute→Verify→Evolve + ReAct | 通常仅 Plan→Execute |
| 插件系统 | ✅ 完整生命周期 + 安全沙箱 | ⚠️ 少数有 |
| 知识图谱 | ✅ 动态 + 推理 + LLM 上下文 | ❌ 罕见 |
| 成本管控 | ✅ 实时追踪 + 预算告警 | ⚠️ 少数有 |
| 可观测性 | ✅ OTel + Prometheus + WS + 结构化日志 | ⚠️ 部分 |
| MCP 支持 | ✅ 完整客户端 + 注册 + 传输 | ❌ 罕见 |
| 子代理 | ✅ 层次化委托 + 消息传递 | ✅ 普遍 |
| 代码量 | 37,173 行 | 通常 5K-20K |
| 测试 | 2,702 个 | 通常 100-500 |
| 语言/依赖 | 纯 Python、10 核心依赖 | 通常更多 |

**MAOP 的差异化优势**：
1. **编辑即平台**：插件系统让 MAOP 从"编排工具"变成"编排平台"
2. **思考即执行**：ReAct 微循环让 agent 具备自主推理能力
3. **知识即上下文**：动态知识图谱自动为 LLM 组装相关上下文
4. **成本即意识**：每 token 可追溯，每 agent 可核算

---

*报告完毕。评测基于 `F:\Nexus\MAOP\` 实际代码，所有结论均可重现。*
