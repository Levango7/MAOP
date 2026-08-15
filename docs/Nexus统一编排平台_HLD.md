# Nexus统一编排平台_HLD

> 文档版本：v1.0
> 编写日期：2026-08-11
> 文档性质：高层设计（High-Level Design，HLD）
> 上游依据：`_盘点_DataEngineBDP_NexusChain.md`、`_盘点_MAOP_OpsMesh_Interaction.md` 两份接口盘点报告
> 适用范围：Nexus 体系下 Interaction / MAOP / DataEngineBDP / NexusChain / OpsMesh 五个项目的统一编排平台

---

## 第1章 文档概述

### 1.1 目的

本文档为 Nexus 统一编排平台的高层设计（HLD），目的在于：

1. 将 Interaction、MAOP、DataEngineBDP、NexusChain、OpsMesh 五个独立项目在架构层面连成一套分层协作体系，明确各层职责边界与协作契约。
2. 以 MAOP 为核心编排引擎，DataEngineBDP / NexusChain / OpsMesh 作为能力节点通过 Adapter 注册到 MAOP Registry，Interaction 作为人机入口，形成"交互层 → 网关层 → 编排层 → 能力节点层 → 事件/可观测层"五层架构。
3. 重点给出四个跨切面设计：能力节点 Adapter 接口契约、统一鉴权与租户透传、Kafka 事件总线 topic 规范、可观测性方案，作为后续 LLD 与实现的依据。
4. 统一部署拓扑与非功能需求基线，为 Helm Umbrella Chart 与环境分层提供设计输入。

### 1.2 范围

本文档覆盖以下五个项目的统一编排设计：

| 项目 | 定位 | 主语言/框架 | 关键端口 |
|------|------|-------------|----------|
| Interaction | 人机交互层（Agent 工作台，4 场景 subagent） | 原生 HTML/JS + Electron + PWA | 8123（静态） |
| MAOP | 编排能力层（多代理编排平台，Plan-Execute-Verify） | Python 3 + FastAPI | 9079 |
| DataEngineBDP | 数据能力层（湖仓集一体大数据平台，33+ 组件） | Java 17 / Go / Python | 8080-8086、18086、18090、8094 |
| NexusChain | 价值流转层（区块链支付编排平台） | Java 17 / Gradle / Rust | 8080-8085、19585、9235、9999、50051 |
| OpsMesh | 运维执行层（网段运维中枢） | Go 1.26 + gRPC | 8080、9090、9091 |

本文档不涉及单个项目内部细节实现，仅在设计契约层面约定跨项目协作。

### 1.3 读者对象

- 架构师：作为统一编排平台架构决策依据。
- 各项目 Owner：作为本项目对外契约与适配改造的输入。
- 平台/SRE 团队：作为统一部署、可观测性、鉴权运维的依据。
- LLD 编写者：作为各章细化设计的上游需求。

### 1.4 术语表

表：术语对照表

| 术语 | 全称 | 含义 |
|------|------|------|
| HLD | High-Level Design | 高层设计文档 |
| LLD | Low-Level Design | 低层设计文档 |
| IdP | Identity Provider | 身份提供者 |
| RBAC | Role-Based Access Control | 基于角色的访问控制 |
| RLS | Row-Level Security | 行级安全 |
| DAG | Directed Acyclic Graph | 有向无环图 |
| Adapter | — | 能力节点适配器，向 MAOP Registry 注册能力 |
| Registry | — | 能力节点注册中心，MAOP 内部组件 |
| A2A | Agent-to-Agent | Google A2A 标准，JSON-RPC 2.0 跨系统 agent 通信 |
| MCP | Model Context Protocol | 工具调用协议标准 |
| Plan-Execute-Verify | — | MAOP 三阶段编排循环 |
| OTel | OpenTelemetry | 开源可观测性框架 |
| OTLP | OpenTelemetry Protocol | OTel 数据导出协议 |
| SSE | Server-Sent Events | 服务端推送事件 |
| DLQ | Dead Letter Queue | 死信队列 |
| TCC | Try-Confirm-Cancel | 分布式事务模式 |
| PoA | Proof of Authority | 联盟共识算法 |
| MPC | Multi-Party Computation | 多方安全计算（门限签名） |
| SKE | Self-Managed K8s Engine | 自管 K8s 引擎 |
| APISIX | — | 云原生 API 网关 |
| Keycloak | — | 开源 IAM，OIDC/SAML IdP |
| Strimzi | — | Kafka on K8s Operator |
| Helm Umbrella Chart | — | 父 Chart 聚合多个子 Chart 的统一部署单元 |

---

## 第2章 整体架构

### 2.1 整体架构图

图：Nexus统一编排平台五层架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          L1 交互层 (Interaction)                              │
│   Agent 工作台 (Electron + PWA, 端口 8123)                                    │
│   4 场景 subagent: office / code / study / life                              │
│   17 个 function-calling 工具 + 8 个 SaaS 集成                                │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ HTTPS (浏览器/Electron 主进程)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    L2 统一 API 网关层 (APISIX + Keycloak)                      │
│   APISIX 网关 :9080/:9443    Keycloak IdP :8080 (realm=nexus)                 │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│   │ JWT 校验│  │ 路由分发│  │ 限流熔断│  │ 审计留痕│  │租户透传│            │
│   └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘            │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ HTTP/gRPC + X-Tenant-ID 头
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    L3 编排引擎层 (MAOP)                                       │
│   FastAPI :9079    Plan-Execute-Verify loop    DAG 调度                       │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│   │  Registry    │  │ Dispatcher   │  │  A2A / MCP   │                       │
│   │ (能力注册中心)│  │ (能力路由)   │  │ (跨系统协议) │                       │
│   └──────┬───────┘  └──────┬───────┘  └──────────────┘                       │
│          │ Adapter 注册    │ capability match + regex scoring                 │
└──────────┼──────────────────┼───────────────────────────────────────────────┘
           │                  │
   ┌───────┴──────────────────┴───────────────────────────────────────┐
   │                                                                   │
   ▼                        ▼                        ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ L4-1 DataEngine  │  │ L4-2 NexusChain │  │ L4-3 OpsMesh    │
│ BDP Adapter      │  │ Adapter         │  │ Adapter         │
│ (数据能力节点)   │  │ (价值流转节点)  │  │ (运维执行节点)  │
│ REST :8080-8086  │  │ REST :8080-8085 │  │ REST :8080      │
│ OTLP :4317       │  │ gRPC :50051     │  │ gRPC :9090      │
│ Keycloak JWT     │  │ API Key+HMAC    │  │ 网关头注入      │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              L5 事件总线 + 可观测性基础设施                                    │
│  ┌─────────────────────┐  ┌──────────────────────────────────────────────┐  │
│  │ Kafka 集群 (Strimzi) │  │ OpenTelemetry Collector (OTLP gRPC :4317)    │  │
│  │ nexus.* topic 规范   │  │ → Jaeger / Tempo (trace)                     │  │
│  │ DLQ + 重试           │  │ → Prometheus (metrics) + Alertmanager        │  │
│  │                      │  │ → Loki / ELK (logs)                          │  │
│  └─────────────────────┘  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 五层分层职责说明

表：五层分层职责对照表

| 层级 | 名称 | 承载项目 | 职责 | 不做 |
|------|------|----------|------|------|
| L1 | 交互层 | Interaction | 人机入口、场景化 Agent 工作台、本地数据沉淀、SaaS 集成 | 不承载后端业务逻辑、不持有租户全局状态 |
| L2 | 统一 API 网关层 | APISIX + Keycloak | 统一鉴权、路由分发、限流熔断、审计、租户透传、协议转换 | 不做业务编排、不持有业务数据 |
| L3 | 编排引擎层 | MAOP | Plan-Execute-Verify 驱动 DAG、能力节点 Registry、能力路由、A2A/MCP 协议、Subagent 调度 | 不直接执行数据/支付/运维原子操作 |
| L4 | 能力节点层 | DataEngineBDP / NexusChain / OpsMesh | 提供数据、价值流转、运维三大类原子能力，通过 Adapter 注册到 MAOP | 不做跨域编排决策、不感知其他能力节点 |
| L5 | 事件总线与可观测层 | Kafka + OTel + Prometheus + Loki | 跨节点事件投递、全链路追踪、指标采集、日志聚合、告警 | 不承载业务逻辑 |

### 2.3 数据流、控制流、事件流

#### 2.3.1 数据流（同步请求/响应）

用户在 Interaction 工作台发起任务 → APISIX 网关校验 JWT 并注入 `X-Tenant-ID` 等身份头 → MAOP 接收编排请求，Dispatcher 按 capability match 路由到对应 Adapter → Adapter 通过 REST/gRPC 调用能力节点（DataEngineBDP/NexusChain/OpsMesh）→ 结果沿原路返回。同步链路用于查询、即时执行、配置类操作。

#### 2.3.2 控制流（编排调度）

MAOP Plan-Execute-Verify loop 驱动 DAG 推进：Plan 阶段将任务拆解为 DAG 节点；Execute 阶段按 DAG 拓扑顺序调用能力节点 Adapter，每个节点对应一个能力调用；Verify 阶段校验执行结果，失败触发重试或熔断。OpsMesh 作为执行底座，可承接 MAOP 下发的运维类任务（部署、配置、巡检），通过 gRPC 通道下发到网段 agent。

#### 2.3.3 事件流（异步通知）

能力节点在完成原子操作后向 Kafka 发布事件（如 `nexus.data.governance.completed.v1`、`nexus.payment.settled.v1`、`nexus.ops.deploy.completed.v1`）；MAOP 订阅相关 topic 驱动 DAG 后继节点推进；Interaction 通过 MAOP 的 WebSocket `/ws` 或 SSE 端点接收实时进度推送。事件采用 CloudEvents 1.0 信封格式，统一携带 `tenant_id` 字段实现租户隔离。

---

## 第3章 统一 API 网关层

### 3.1 APISIX + Keycloak 统一鉴权设计

统一 API 网关采用 APISIX 作为入口网关，Keycloak 作为统一 IdP。DataEngineBDP 已集成 Keycloak（realm=shuqing），统一编排平台复用该 Keycloak 实例并新增 realm=nexus 作为统一身份域。

图：统一鉴权流程图

```
Client ──(1) 登录请求──▶ APISIX ──▶ Keycloak /realms/nexus/protocol/openid-connect/token
                                              │
                                              ▼
                                   (2) 签发 JWT (RS256)
Client ◀──(3) JWT──────────────── APISIX ◀── Keycloak
Client ──(4) 业务请求+JWT──▶ APISIX
                              │
                              ├─(5) jwt-auth plugin 校验签名/过期/aud
                              ├─(6) consumer binding → tenant_id
                              ├─(7) proxy-rewrite 注入 X-Tenant-ID/X-User-ID/X-User-Roles
                              ▼
                          (8) 路由到 MAOP / 能力节点
```

Keycloak 配置要点：

- realm：`nexus`，统一租户身份域。
- 签名算法：RS256（与 OpsMesh 网关二次校验能力对齐）。
- token 类型：Access Token（短期 15min）+ Refresh Token（滑动 7d）。
- client：`nexus-platform`（机密客户端，Authorization Code + PKCE）。
- 自定义 claim：`tenant_id`、`user_id`、`roles`、`scope`、`edition`（personal/enterprise）。
- 用户 federation：MAOP 现有 PBKDF2 用户迁移至 Keycloak；OpsMesh LDAP 通过 Keycloak LDAP federation 接入；NexusChain 商户 API Key 在 Keycloak 中建模为 Service Account。

### 3.2 路由规则

APISIX 路由按能力域前缀分发到下游服务，所有对外 API 统一前缀 `/api/nexus/v1/`。

表：APISIX 路由规则对照表

| 路由前缀 | 上游服务 | 上游地址 | 协议 | 说明 |
|----------|----------|----------|------|------|
| `/api/nexus/v1/orchestrate/*` | MAOP | maop:9079 | HTTP | 编排入口，Plan-Execute-Verify |
| `/api/nexus/v1/data/*` | DataEngineBDP | dataengine-bdp-gateway:8081 | HTTP | 数据查询/治理/目录 |
| `/api/nexus/v1/payment/*` | NexusChain | nexus-gateway:8080 | HTTP | 支付编排/订单/桥 |
| `/api/nexus/v1/ops/*` | OpsMesh | opsmesh:8080 | HTTP | 运维任务/部署/CMDB |
| `/api/nexus/v1/ops/grpc/*` | OpsMesh | opsmesh:9090 | gRPC | agent 通道（仅内部） |
| `/api/nexus/v1/interaction/*` | MAOP | maop:9079 | HTTP | Interaction 回调入口（经 MAOP 中转） |
| `/ws` | MAOP | maop:9079 | WebSocket | 实时推送 |
| `/api/nexus/v1/health` | APISIX 自身 | — | HTTP | 网关健康聚合 |

### 3.3 限流、熔断、审计

- 限流：APISIX `limit-req` 插件按 `consumer_id + tenant_id` 维度令牌桶限流，默认 30 RPS / burst 60（与 MAOP RateLimitMiddleware 对齐）；NexusChain 支付域按租户配额单独配置（沿用 TenantRateLimiter 语义）。
- 熔断：APISIX `api-breaker` 插件对下游 5xx 触发熔断；MAOP 内部 `core/circuit_breaker.py`（failure_threshold=5，recovery_timeout=30s）作为编排层二级熔断；NexusChain Sentinel 作为能力节点内部三级熔断。
- 审计：APISIX `clickhouse-logger` 或 `kafka-logger` 插件将所有请求审计日志投递到 Kafka `nexus.audit.access.v1` topic；OpsMesh `audit_log`（100% 留痕）与 MAOP `AuditLogger`（append-only）作为各层本地审计补充。

### 3.4 对外暴露的统一 API 命名规范

统一 API 命名遵循 RESTful 风格，命名规范如下：

```
/api/nexus/v1/{domain}/{resource}[/{id}][/{action}]
```

- `domain`：能力域，取值 `orchestrate | data | payment | ops | interaction`。
- `resource`：资源名，复数形式（如 `jobs`、`payments`、`tasks`）。
- `action`：动作名，小写下划线（如 `cancel`、`settle`、`deploy`）。
- 版本：URL 路径版本 `v1`，与 Keycloak JWT `aud` 字段对齐；重大变更升 `v2` 并行。
- 示例：`POST /api/nexus/v1/orchestrate/dags`、`GET /api/nexus/v1/data/lineage/graph`、`POST /api/nexus/v1/payment/payments/{id}/settle`、`POST /api/nexus/v1/ops/deploys`。

---

## 第4章 MAOP 编排引擎层

### 4.1 Plan-Execute-Verify loop 驱动 DAG

MAOP 采用三阶段循环驱动 DAG 执行，复用现有 `maop_loop.py` + `maop_plan.py` + `maop_execute.py` + `maop_verify.py` 实现，并向能力节点 Adapter 扩展。

图：Plan-Execute-Verify 循环示意图

```
        ┌──────────────────────────────────────────┐
        │                                          │
        ▼                                          │
   ┌─────────┐    ┌─────────┐    ┌─────────┐       │
   │  Plan   │───▶│ Execute │───▶│ Verify  │──成功─┤
   └─────────┘    └─────────┘    └─────────┘       │
        │              │              │             │
        │              │              │ 失败        │
        │              │              ▼             │
        │              │         ┌─────────┐       │
        │              │         │ Retry / │       │
        │              │         │ Breaker │       │
        │              │         └────┬────┘       │
        │              │              │             │
        │              │              ├─可重试─▶ Execute
        │              │              └─不可重试─▶ 终止
        │              │
        │              ▼
        │         调用 Adapter.invoke(capability, input)
        │
        ▼
   将任务拆解为 DAG 节点
   (每个节点绑定一个 capability)
```

- Plan：将自然语言或结构化任务拆解为 DAG 节点序列，每个节点绑定一个 capability（如 `data.query`、`payment.settle`、`ops.deploy`），节点间声明数据依赖。
- Execute：按 DAG 拓扑顺序执行节点，通过 Dispatcher 路由到对应 Adapter，调用 `Adapter.invoke(capability, input)`；支持并行执行无依赖节点。
- Verify：校验节点输出是否满足声明式后置条件（schema 校验 + 业务断言）；失败按重试策略回退或触发 Circuit Breaker。

### 4.2 能力节点注册中心（Registry）设计

Registry 是 MAOP 内部组件，基于现有 `core/agent_registry.py` 扩展为能力节点注册中心。

表：Registry 数据模型参数说明表

| 字段 | 类型 | 说明 |
|------|------|------|
| `node_id` | string | 能力节点唯一标识（如 `dataengine-bdp`） |
| `node_type` | enum | `data \| payment \| ops` |
| `adapter_endpoint` | string | Adapter 调用地址（REST 或 gRPC） |
| `adapter_protocol` | enum | `rest \| grpc` |
| `capabilities` | list | 能力声明列表，每项含 `name`、`input_schema`、`output_schema`、`timeout_ms`、`retryable` |
| `events_published` | list | 该节点发布的 Kafka topic 列表 |
| `events_subscribed` | list | 该节点订阅的 Kafka topic 列表 |
| `health_endpoint` | string | 健康检查端点 |
| `tenant_scope` | enum | `global \| tenant-isolated` |
| `status` | enum | `registered \| active \| draining \| offline` |

注册流程：能力节点启动时通过 `POST /api/nexus/v1/orchestrate/registry/register` 向 MAOP 提交声明；MAOP 校验 schema 后写入 Registry，并通过心跳（10s 间隔）维持注册态；失联 30s 标记 `draining`，60s 标记 `offline` 并触发 Adapter failover。

### 4.3 编排策略

表：编排策略参数说明表

| 策略 | 实现 | 默认值 | 说明 |
|------|------|--------|------|
| 能力路由 | Dispatcher capability match + regex scoring | — | 按 capability 名匹配 Adapter，多 Adapter 候选时按 score 排序 |
| 熔断 | `core/circuit_breaker.py` | failure_threshold=5, recovery_timeout=30s | 单 Adapter 5 次失败熔断 30s |
| 重试 | DAG 节点 `retryable` + 指数退避 | max=3, backoff=1s/2s/4s | 可重试错误重试，不可重试立即终止 |
| 超时 | DAG 节点 `timeout_ms` | data=30s, payment=60s, ops=300s | 按 capability 类型分级 |
| 优先级 | EventBus priority | NORMAL | CRITICAL 优先调度（如支付结算） |
| 预算守卫 | `core/budget_guard.py` | 日/月预算上限 | LLM token 与 API 调用成本控制 |

### 4.4 与 OpsMesh 执行底座的关系

OpsMesh 在统一编排平台中承担双重角色：

1. 作为 L4 能力节点：通过 OpsMesh Adapter 向 MAOP Registry 注册 `ops.deploy`、`ops.task`、`ops.cmdb` 等能力，MAOP 通过 REST/gRPC 调用 OpsMesh 控制面。
2. 作为执行底座：MAOP 可将运维类 DAG 节点（部署、巡检、配置）整体下发给 OpsMesh，由 OpsMesh 控制面通过 gRPC 通道分发到网段 agent 执行，结果通过 `nexus.ops.task.completed.v1` 事件回投 Kafka，MAOP 订阅后推进 DAG。

这种"能力节点 + 执行底座"双角色设计，使得 MAOP 不必直接管理网段 agent，复用 OpsMesh 的 agent 纳管、网段分桶、HA failover、HMAC 签名等成熟能力。

---

## 第5章 能力节点 Adapter 接口契约

### 5.1 Adapter 通用契约

每个能力节点 Adapter 是一个薄适配层，向 MAOP Registry 暴露统一调用接口，对内转换为本项目原生协议。Adapter 部署在能力节点侧（推荐）或 MAOP 侧（兜底），本文档约定部署在能力节点侧。

表：Adapter 通用接口参数说明表

| 接口 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 能力调用 | POST | `/adapter/v1/invoke` | body: `{capability, input, tenant_id, trace_id}`，resp: `{output, status, error}` |
| 能力发现 | GET | `/adapter/v1/capabilities` | 返回该 Adapter 支持的 capability 列表与 schema |
| 健康检查 | GET | `/adapter/v1/health` | 返回 `{status, version, deps}` |
| 就绪探针 | GET | `/adapter/v1/ready` | 依赖就绪检查 |
| 注册握手 | POST | `/adapter/v1/register` | 启动时向 MAOP Registry 注册 |

调用协议选择原则：

- 同步调用：查询、即时执行、配置类操作，使用 REST 或 gRPC。
- 异步事件：长耗时操作（数据治理管道、支付结算、部署）、跨节点联动，使用 Kafka 事件。
- 流式：进度推送使用 SSE/WebSocket（经 MAOP 中转）。

### 5.2 DataEngineBDP Adapter

DataEngineBDP 作为数据能力节点，Adapter 包装其 33+ 组件的核心能力，向 MAOP 暴露数据查询、治理、目录、向量、LLM 网关等能力。

#### 5.2.1 能力声明

表：DataEngineBDP Adapter 能力声明对照表

| 能力名 | 调用方式 | 上游端点 | 请求 schema 摘要 | 响应 schema 摘要 | 事件 topic |
|--------|----------|----------|------------------|------------------|------------|
| `data.query` | 同步 REST | sql-gateway:8081 `/api/v1/query` | `{sql, dialect, virtual}` | `{rows, schema, duration_ms}` | — |
| `data.federate` | 同步 REST | karmada-federated-query:8094 `/api/v1/federated-query` | `{sql, clusters}` | `{rows, degraded}` | — |
| `data.govern` | 异步事件 | governance-real-time-pipeline:18090 `/api/v1/pipeline/start` | `{pipeline_id, rules}` | `{pipeline_id, status}` | `nexus.data.governance.completed.v1` |
| `data.lineage` | 同步 REST | governance-lineage-analyzer:8086 `/api/v1/lineage/graph` | `{dataset, depth}` | `{nodes, edges}` | — |
| `data.catalog` | 同步 REST | catalog:8082 `/api/v1/datasets` | `{filter, page}` | `{datasets, total}` | — |
| `data.vector.search` | 同步 REST | vector-engine:8086 `/api/v1/search` | `{collection, query, top_k}` | `{hits}` | — |
| `data.llm.chat` | 同步 REST | llm-gateway:8084 `/v1/chat/completions` | OpenAI 兼容 | OpenAI 兼容 | — |
| `data.nl2sql` | 同步 REST | nl2sql `/nl2sql` | `{question, schema}` | `{sql, confidence}` | — |
| `data.finops` | 同步 REST | finops-dashboard:8085 `/api/v1/cost` | `{range, group_by}` | `{costs, forecast}` | — |
| `data.infra.provision` | 异步事件 | infra-orchestrator:8085 `/api/v1/provision` | `{provider, spec}` | `{cluster_id, status}` | `nexus.data.infra.provisioned.v1` |
| `data.encaps` | 同步 REST | encaps-layer:8080 `/api/v1/encrypt` | `{algo, plaintext}` | `{ciphertext}` | — |

#### 5.2.2 健康契约

- 健康检查：`GET http://dataengine-bdp-adapter:8090/adapter/v1/health`，聚合下游 33+ 组件 Actuator `/actuator/health` 与 Go 组件 `/healthz`。
- 就绪探针：`GET /adapter/v1/ready`，检查 Kafka 连通、Keycloak 连通、核心组件（sql-gateway、catalog、llm-gateway）就绪。
- 超时：查询类 30s，治理管道触发 5s（异步），基础设施编排 300s。

#### 5.2.3 事件契约

表：DataEngineBDP 事件契约对照表

| topic | 方向 | 事件 schema 摘要 | 触发时机 |
|-------|------|------------------|----------|
| `nexus.data.governance.completed.v1` | 发布 | `{pipeline_id, dataset, rules_passed, rules_failed, tenant_id}` | 治理管道完成 |
| `nexus.data.governance.alert.v1` | 发布 | `{dataset, rule, severity, tenant_id}` | 治理规则触发告警 |
| `nexus.data.infra.provisioned.v1` | 发布 | `{cluster_id, provider, status, tenant_id}` | 基础设施编排完成 |
| `nexus.data.ready.v1` | 发布 | `{dataset, partition, tenant_id}` | 数据就绪（CDC 完成） |

### 5.3 NexusChain Adapter

NexusChain 作为价值流转能力节点，Adapter 包装支付编排、跨链桥、签名、钱包等能力。

#### 5.3.1 能力声明

表：NexusChain Adapter 能力声明对照表

| 能力名 | 调用方式 | 上游端点 | 请求 schema 摘要 | 响应 schema 摘要 | 事件 topic |
|--------|----------|----------|------------------|------------------|------------|
| `payment.create` | 同步 REST | nexus-gateway:8080 `/api/v1/payments` | `{order_id, amount, currency, channel}` | `{payment_id, status}` | — |
| `payment.settle` | 异步事件 | nexus-gateway:8080 `/api/v1/execution` | `{payment_id, settlement_spec}` | `{settlement_id, status}` | `nexus.payment.settled.v1` |
| `payment.query` | 同步 REST | nexus-gateway:8080 `/api/v1/payments/{id}` | — | `{payment, status, history}` | — |
| `payment.bridge` | 异步事件 | nexus-bridge:8084 `/bridge/transfer` | `{from_chain, to_chain, amount, recipient}` | `{bridge_id, status}` | `nexus.payment.bridge.completed.v1` |
| `payment.webhook` | 同步 REST | nexus-gateway:8080 `/api/v1/webhooks` | `{event, signature}` | `{ack}` | — |
| `payment.stablecoin` | 同步 REST | nexus-core:19585 `/stablecoin` | `{action, amount}` | `{tx_hash, status}` | — |
| `payment.consortium` | 同步 gRPC | nexus-consortium:9999 `Entry` | proto: ConsortiumProposal | proto: ConsortiumResult | — |
| `payment.signing` | 同步 gRPC | nexus-signing-service:50051 `MpcCryptoService.Sign` | proto: SignRequest | proto: SignResponse | — |
| `payment.wallet` | 同步 REST | nexus-wallet-service:8083 `/wallet/{id}` | — | `{balance, addresses}` | — |
| `payment.order` | 同步 REST | nexus-gateway:8080 `/api/v1/orders` | `{merchant, items, amount}` | `{order_id, status}` | — |

#### 5.3.2 健康契约

- 健康检查：`GET http://nexuschain-adapter:8090/adapter/v1/health`，聚合 nexus-gateway/core/bridge/signing/wallet 各组件 Actuator `/actuator/health`。
- 就绪探针：检查 Kafka `payment-events` topic 连通、Nacos 连通、Seata TC 连通、MPC 节点 quorum。
- 超时：支付创建 10s，结算 60s（异步触发 5s），跨链桥 120s，MPC 签名 30s。

#### 5.3.3 事件契约

表：NexusChain 事件契约对照表

| topic | 方向 | 事件 schema 摘要 | 触发时机 |
|-------|------|------------------|----------|
| `nexus.payment.created.v1` | 发布 | `{payment_id, order_id, amount, currency, tenant_id}` | 支付创建 |
| `nexus.payment.settled.v1` | 发布 | `{payment_id, settlement_id, amount, fee, tenant_id}` | 结算完成 |
| `nexus.payment.failed.v1` | 发布 | `{payment_id, reason, tenant_id}` | 支付失败 |
| `nexus.payment.bridge.completed.v1` | 发布 | `{bridge_id, from_chain, to_chain, tx_hash, tenant_id}` | 跨链桥完成 |
| `nexus.payment.bridge.failed.v1` | 发布 | `{bridge_id, reason, tenant_id}` | 跨链桥失败 |
| `nexus.payment.consortium.proposal.v1` | 发布 | `{proposal_id, proposer, action, tenant_id}` | 联盟提案 |
| `nexus.payment.webhook.dlq.v1` | 发布 | `{webhook_id, payload, attempts, last_error}` | Webhook 投递失败进死信 |

### 5.4 OpsMesh Adapter

OpsMesh 作为运维执行能力节点，Adapter 包装部署、任务下发、CMDB、告警等能力。

#### 5.4.1 能力声明

表：OpsMesh Adapter 能力声明对照表

| 能力名 | 调用方式 | 上游端点 | 请求 schema 摘要 | 响应 schema 摘要 | 事件 topic |
|--------|----------|----------|------------------|------------------|------------|
| `ops.deploy` | 异步事件 | opsmesh:8080 `/api/v1/deploys` | `{plan, targets, rollback_on_fail}` | `{deploy_id, status}` | `nexus.ops.deploy.completed.v1` |
| `ops.task` | 异步事件 | opsmesh:8080 `/api/v1/tasks` | `{type, target, command, timeout}` | `{task_id, status}` | `nexus.ops.task.completed.v1` |
| `ops.task.batch` | 异步事件 | opsmesh:8080 `/api/v1/tasks/batch` | `{tasks[]}` | `{task_ids[], status}` | `nexus.ops.task.completed.v1` |
| `ops.task.cancel` | 同步 REST | opsmesh:8080 `/api/v1/tasks/{id}/cancel` | — | `{status}` | — |
| `ops.task.result` | 同步 REST | opsmesh:8080 `/api/v1/tasks/{id}/result` | — | `{result, logs}` | — |
| `ops.cmdb.query` | 同步 REST | opsmesh:8080 `/api/v1/cmdb/instances` | `{model, filter}` | `{instances}` | — |
| `ops.alert.ack` | 同步 REST | opsmesh:8080 `/api/v1/alerts/{id}/ack` | — | `{status}` | — |
| `ops.k8s.deploy` | 异步事件 | opsmesh:8080 `/api/v1/k8s/clusters/{id}/test` + `/api/v1/deploys` | `{cluster_id, chart, values}` | `{deploy_id, status}` | `nexus.ops.deploy.completed.v1` |
| `ops.middleware.deploy` | 异步事件 | opsmesh:8080 `/api/v1/middleware-templates/{id}/deploy` | `{template_id, target}` | `{instance_id, status}` | `nexus.ops.deploy.completed.v1` |
| `ops.federation.forward` | 同步 REST | opsmesh:8080 `/api/v1/federation/forward/task` | `{peer, task}` | `{status}` | — |
| `ops.agent.register` | 同步 gRPC | opsmesh:9090 `Registration.Register` | proto: AgentInfo | proto: RegisterResp | — |
| `ops.workflow` | 异步事件 | opsmesh:8080 `/api/v1/workflows` | `{dag, triggers}` | `{workflow_id, status}` | `nexus.ops.workflow.completed.v1` |

#### 5.4.2 健康契约

- 健康检查：`GET http://opsmesh-adapter:8090/adapter/v1/health` → 透传 OpsMesh `/healthz`（P1-C2 深度：store ping）。
- 就绪探针：`GET /adapter/v1/ready` → 透传 OpsMesh `/readyz`（store + redis + IsLeader）。
- 超时：任务下发 5s（异步触发），部署 300s，CMDB 查询 10s，gRPC agent 通道 10s。

#### 5.4.3 事件契约

表：OpsMesh 事件契约对照表

| topic | 方向 | 事件 schema 摘要 | 触发时机 |
|-------|------|------------------|----------|
| `nexus.ops.task.completed.v1` | 发布 | `{task_id, type, target, result, duration_ms, tenant_id}` | 任务执行完成 |
| `nexus.ops.task.failed.v1` | 发布 | `{task_id, reason, attempts, tenant_id}` | 任务失败进死信 |
| `nexus.ops.deploy.completed.v1` | 发布 | `{deploy_id, targets, status, revision, tenant_id}` | 部署完成 |
| `nexus.ops.deploy.failed.v1` | 发布 | `{deploy_id, target, reason, tenant_id}` | 部署失败 |
| `nexus.ops.alert.v1` | 发布 | `{alert_id, rule, severity, target, tenant_id}` | 告警触发 |
| `nexus.ops.device.online.v1` | 发布 | `{device_id, segment, agent_id, tenant_id}` | 设备上线 |
| `nexus.ops.device.offline.v1` | 发布 | `{device_id, segment, reason, tenant_id}` | 设备离线 |
| `nexus.ops.workflow.completed.v1` | 发布 | `{workflow_id, status, nodes_completed, tenant_id}` | 工作流完成 |

### 5.5 能力契约汇总表

表：三个 Adapter 能力契约汇总对照表

| Adapter | 能力数 | 同步能力 | 异步能力 | 发布 topic 数 | 订阅 topic 数 | 健康端点 | 协议 |
|---------|--------|----------|----------|---------------|---------------|----------|------|
| DataEngineBDP Adapter | 11 | 9 | 2 | 4 | 0 | `/adapter/v1/health` | REST |
| NexusChain Adapter | 10 | 7 | 3 | 7 | 0 | `/adapter/v1/health` | REST + gRPC |
| OpsMesh Adapter | 12 | 6 | 6 | 8 | 0 | `/adapter/v1/health` | REST + gRPC |
| 合计 | 33 | 22 | 11 | 19 | 0 | — | — |

说明：三个 Adapter 均不直接订阅其他 Adapter 的事件，跨能力节点联动统一由 MAOP 订阅后通过 DAG 编排驱动，避免能力节点间直接耦合。

---

## 第6章 统一鉴权与租户透传方案

### 6.1 Keycloak 作为统一 IdP

统一编排平台采用 Keycloak 作为唯一对外 IdP，复用 DataEngineBDP 现有 Keycloak 实例，新增 realm=nexus 作为统一身份域。各项目对接方式如下：

- DataEngineBDP：已使用 Keycloak（realm=shuqing），新增 realm=nexus 后通过多 realm 配置同时支持；Adapter 层透传 Keycloak JWT，无需改造。
- MAOP：现有 JWT HS256 自签发，迁移为 Keycloak RS256 验签；保留 MAOP 内部 RBAC/SSO/License 作为授权补充，鉴权（认证）统一由 Keycloak 完成。
- OpsMesh：现有网关身份头注入模式（X-Tenant-ID/X-User-ID/X-User-Roles），由 APISIX 在 Keycloak 校验后注入这些头，OpsMesh 内核二次校验 JWT RS256（`--jwt-public-key`）作为纵深防御，无需改造。
- NexusChain：现有 API Key + HMAC + 时间戳防重放，在 Keycloak 中将商户建模为 Service Account，签发 client credentials token；Adapter 层将 Keycloak JWT 转换为 NexusChain 原生 `X-Tenant-Api-Key` 头，HMAC 签名由 Adapter 完成。
- Interaction：无账号体系，统一编排平台下用户在 Interaction 中通过浏览器跳转 Keycloak 登录页完成认证，Token 存储于 Electron safeStorage（DPAPI）或浏览器内存；保留 Interaction 本地 AI Key 加密不变。

### 6.2 统一 JWT claim 规范

表：统一 JWT claim 参数说明表

| claim | 类型 | 必填 | 说明 |
|-------|------|------|------|
| `iss` | string | 是 | 签发方，固定 `https://keycloak/realms/nexus` |
| `aud` | string[] | 是 | 受众，`["nexus-platform"]` |
| `sub` | string | 是 | 用户 ID（Keycloak UUID） |
| `exp` | long | 是 | 过期时间戳（秒） |
| `iat` | long | 是 | 签发时间戳（秒） |
| `tenant_id` | string | 是 | 租户 ID |
| `user_id` | string | 是 | 用户业务 ID（同 sub） |
| `roles` | string[] | 是 | 角色列表，如 `["admin", "operator"]` |
| `scope` | string | 是 | OAuth scope，空格分隔，如 `data.query payment.settle ops.deploy` |
| `edition` | string | 否 | `personal \| enterprise`，MAOP 双版 gate |
| `tenant_quota` | object | 否 | 租户配额快照（MAOP Enterprise） |
| `sid` | string | 否 | 会话 ID，用于登出吊销 |

签名算法统一为 RS256，Keycloak realm 公钥通过 `/.well-known/openid-configuration` JWK 端点分发，各项目通过 JWKS 缓存验签。

### 6.3 租户上下文透传

租户上下文从网关透传到编排层再到各能力节点，按协议分三种载体：

图：租户上下文透传示意图

```
Keycloak ──JWT(tenant_id)──▶ APISIX
                               │
                               ├─ HTTP 头注入: X-Tenant-ID / X-User-ID / X-User-Roles
                               ▼
                            MAOP
                               │
                               ├─ HTTP 头透传: X-Tenant-ID (调用 DataEngineBDP / NexusChain Adapter)
                               ├─ gRPC metadata 透传: x-tenant-id (调用 OpsMesh gRPC)
                               ├─ Kafka 事件字段: data.tenant_id (CloudEvents data)
                               ▼
                          能力节点
                               │
                               ├─ DataEngineBDP: TenantContext ThreadLocal (从 X-Tenant-ID 解析)
                               ├─ NexusChain: TenantContext ThreadLocal + tenant_id 行级隔离
                               ├─ OpsMesh: TenantID 行级隔离 (从 X-Tenant-ID 解析, --require-auth)
                               └─ 事件发布: 强制加盖 tenant_id 字段
```

- HTTP：APISIX `proxy-rewrite` 插件将 JWT claim 转为 `X-Tenant-ID`、`X-User-ID`、`X-User-Roles`、`X-Trace-ID` 头；MAOP 透传到能力节点 Adapter；DataEngineBDP 与 NexusChain 从头解析填充 `TenantContext` ThreadLocal；OpsMesh 从头解析做行级隔离。
- gRPC：MAOP 调用 OpsMesh gRPC 时将租户上下文放入 metadata `x-tenant-id`、`x-user-id`；OpsMesh `internal/authctx/` 从 metadata 提取。
- 事件：Kafka 事件 CloudEvents `data` 字段强制包含 `tenant_id`；消费者按 `tenant_id` 过滤，跨租户事件拒绝处理。

### 6.4 各项目鉴权差异的适配方案

各项目鉴权机制差异在 Adapter 层做转换，能力节点内部保持原生鉴权不变：

- DataEngineBDP Adapter：Keycloak JWT 透传，Adapter 不做转换；DataEngineBDP 内部 Keycloak resource server 直接验签。
- NexusChain Adapter：Adapter 持有商户 API Key 与 HMAC 密钥，将上游 Keycloak JWT 映射为 NexusChain `X-Tenant-Api-Key` 头并计算 HMAC-SHA256 签名（timestamp + body），调用 nexus-api-gateway。
- OpsMesh Adapter：APISIX 已注入 `X-Tenant-ID` 等头，Adapter 透传；OpsMesh 内核 `--jwt-public-key` 二次校验 JWT RS256 作为纵深防御。
- MAOP：Keycloak JWT RS256 验签替代原 HS256 自签发；MAOP 内部 RBAC/SSO/License 保留作为授权层。
- Interaction：浏览器/Electron 通过 Keycloak OIDC Authorization Code + PKCE 登录，Token 存储于 safeStorage；Interaction 本地 AI Key 加密不变。

### 6.5 五项目鉴权方式对照表

表：五项目鉴权方式统一前后对照表

| 项目 | 当前鉴权方式 | 统一后方案 | 适配层改造点 |
|------|--------------|------------|--------------|
| Interaction | 无账号 + AI Key 加密（浏览器 AES-GCM / Electron safeStorage DPAPI） | Keycloak OIDC 登录 + Token 存 safeStorage，AI Key 加密保留 | 新增 Keycloak 登录页跳转与 Token 管理 |
| MAOP | JWT HS256 自签发 + RBAC + SSO + License + LDAP | Keycloak RS256 验签，RBAC/SSO/License 保留为授权层 | `JWTHandler` 改为 Keycloak JWKS 验签 |
| DataEngineBDP | JWT HMAC-SHA256 + Keycloak realm=shuqing + Karmada mTLS | Keycloak realm=nexus RS256，多 realm 共存 | 多 realm 配置，Adapter 透传 |
| NexusChain | API Key + HMAC-SHA256 + 时间戳防重放 + MPC mTLS | Keycloak Service Account + Adapter 转 API Key/HMAC | Adapter 持有 API Key 与 HMAC 密钥做转换 |
| OpsMesh | 网关头注入 + JWT HS256/RS256 + gRPC mTLS + HMAC + Install Token | APISIX 注入头 + Keycloak RS256 二次验签，HMAC/Install Token 保留 | APISIX `proxy-rewrite` 注入头，`--jwt-public-key` 指向 Keycloak |

---

## 第7章 Kafka 事件总线 topic 规范

### 7.1 统一 topic 命名规范

统一 Kafka topic 命名规范如下：

```
nexus.{domain}.{event_type}.{version}
```

- `domain`：能力域，取值 `data \| payment \| ops \| orchestrate \| audit`。
- `event_type`：事件类型，小写下划线，动词过去式表示已完成（如 `settled`、`completed`、`failed`）。
- `version`：schema 版本，`v1`、`v2`...，重大不兼容变更升版本，旧 topic 并行保留。

示例：`nexus.payment.settled.v1`、`nexus.data.governance.completed.v1`、`nexus.ops.deploy.completed.v1`、`nexus.orchestrate.dag.completed.v1`、`nexus.audit.access.v1`。

命名约束：

- 全小写，点分，禁止大写与特殊字符。
- 长度不超过 64 字符。
- `domain` 与 `event_type` 段禁止包含 `nexus`、`v1` 等保留词。

### 7.2 各能力节点发布的事件清单

#### 7.2.1 DataEngineBDP 发布事件

表：DataEngineBDP 发布事件 schema 对照表

| topic | 事件类型 | data schema 摘要 | 触发组件 |
|-------|----------|------------------|----------|
| `nexus.data.ready.v1` | 数据就绪 | `{dataset, partition, ready_at, rows, tenant_id}` | flink-cdc |
| `nexus.data.governance.completed.v1` | 治理完成 | `{pipeline_id, dataset, rules_passed, rules_failed, duration_ms, tenant_id}` | governance-real-time-pipeline |
| `nexus.data.governance.alert.v1` | 治理告警 | `{dataset, rule, severity, value, threshold, tenant_id}` | rule-engine / governance |
| `nexus.data.infra.provisioned.v1` | 基础设施就绪 | `{cluster_id, provider, region, status, tenant_id}` | infra-orchestrator |

#### 7.2.2 NexusChain 发布事件

表：NexusChain 发布事件 schema 对照表

| topic | 事件类型 | data schema 摘要 | 触发组件 |
|-------|----------|------------------|----------|
| `nexus.payment.created.v1` | 支付创建 | `{payment_id, order_id, amount, currency, channel, tenant_id}` | nexus-gateway |
| `nexus.payment.settled.v1` | 结算完成 | `{payment_id, settlement_id, amount, fee, settled_at, tenant_id}` | nexus-settlement |
| `nexus.payment.failed.v1` | 支付失败 | `{payment_id, stage, reason, code, tenant_id}` | nexus-gateway / nexus-core |
| `nexus.payment.bridge.completed.v1` | 跨链完成 | `{bridge_id, from_chain, to_chain, amount, tx_hash, tenant_id}` | nexus-bridge |
| `nexus.payment.bridge.failed.v1` | 跨链失败 | `{bridge_id, reason, tx_hash, tenant_id}` | nexus-bridge |
| `nexus.payment.consortium.proposal.v1` | 联盟提案 | `{proposal_id, proposer, action, voters, tenant_id}` | nexus-consortium |
| `nexus.payment.webhook.dlq.v1` | Webhook 死信 | `{webhook_id, payload, attempts, last_error, next_retry_at}` | KafkaEventStore |

#### 7.2.3 OpsMesh 发布事件

表：OpsMesh 发布事件 schema 对照表

| topic | 事件类型 | data schema 摘要 | 触发组件 |
|-------|----------|------------------|----------|
| `nexus.ops.task.completed.v1` | 任务完成 | `{task_id, type, target, result, duration_ms, agent_id, tenant_id}` | controlplane |
| `nexus.ops.task.failed.v1` | 任务失败 | `{task_id, type, reason, attempts, agent_id, tenant_id}` | controlplane |
| `nexus.ops.deploy.completed.v1` | 部署完成 | `{deploy_id, targets, status, revision, duration_ms, tenant_id}` | controlplane |
| `nexus.ops.deploy.failed.v1` | 部署失败 | `{deploy_id, target, reason, revision, tenant_id}` | controlplane |
| `nexus.ops.alert.v1` | 告警触发 | `{alert_id, rule, severity, target, value, tenant_id}` | controlplane |
| `nexus.ops.device.online.v1` | 设备上线 | `{device_id, segment, agent_id, ip, tenant_id}` | agent → controlplane |
| `nexus.ops.device.offline.v1` | 设备离线 | `{device_id, segment, reason, last_seen, tenant_id}` | controlplane |
| `nexus.ops.workflow.completed.v1` | 工作流完成 | `{workflow_id, status, nodes_completed, nodes_failed, tenant_id}` | controlplane |

#### 7.2.4 MAOP 发布与订阅事件

表：MAOP 编排事件 schema 对照表

| topic | 方向 | data schema 摘要 | 说明 |
|-------|------|------------------|------|
| `nexus.orchestrate.dag.started.v1` | 发布 | `{dag_id, plan, tenant_id, user_id}` | DAG 启动 |
| `nexus.orchestrate.dag.completed.v1` | 发布 | `{dag_id, status, duration_ms, tenant_id}` | DAG 完成 |
| `nexus.orchestrate.dag.failed.v1` | 发布 | `{dag_id, failed_node, reason, tenant_id}` | DAG 失败 |
| `nexus.orchestrate.node.started.v1` | 发布 | `{dag_id, node_id, capability, tenant_id}` | 节点启动 |
| `nexus.orchestrate.node.completed.v1` | 发布 | `{dag_id, node_id, output, duration_ms, tenant_id}` | 节点完成 |
| `nexus.audit.access.v1` | 发布 | `{method, path, user_id, tenant_id, status, latency_ms, trace_id}` | 访问审计（APISIX 投递） |

### 7.3 MAOP 订阅事件驱动 DAG 推进

MAOP 订阅能力节点事件，将事件映射到 DAG 节点完成，推进后继节点：

表：MAOP 事件订阅与 DAG 推进对照表

| 订阅 topic | 触发动作 | 期望后继 capability |
|------------|----------|---------------------|
| `nexus.data.ready.v1` | 标记 `data.query` / `data.govern` 节点完成 | `data.govern` / `data.lineage` |
| `nexus.data.governance.completed.v1` | 标记 `data.govern` 节点完成 | `payment.settle`（数据驱动支付） |
| `nexus.data.infra.provisioned.v1` | 标记 `data.infra.provision` 节点完成 | `ops.deploy`（基础设施就绪后部署） |
| `nexus.payment.created.v1` | 标记 `payment.create` 节点完成 | `payment.settle` |
| `nexus.payment.settled.v1` | 标记 `payment.settle` 节点完成 | `payment.bridge` / DAG 完成 |
| `nexus.payment.failed.v1` | 标记支付节点失败，触发重试或补偿 | — |
| `nexus.payment.bridge.completed.v1` | 标记 `payment.bridge` 节点完成 | DAG 完成 |
| `nexus.ops.task.completed.v1` | 标记 `ops.task` 节点完成 | 后继运维节点 |
| `nexus.ops.deploy.completed.v1` | 标记 `ops.deploy` 节点完成 | `ops.task`（部署后巡检） |
| `nexus.ops.deploy.failed.v1` | 标记部署失败，触发回滚 | `ops.deploy`（rollback） |

### 7.4 事件 schema 规范（CloudEvents）

所有 Kafka 事件采用 CloudEvents 1.0 信封格式，JSON 编码：

```json
{
  "specversion": "1.0",
  "id": "uuid-v4",
  "source": "/nexus/payment/nexus-gateway",
  "type": "nexus.payment.settled.v1",
  "time": "2026-08-11T08:30:00.123Z",
  "datacontenttype": "application/json",
  "subject": "payment:pay_2026081108300001",
  "tenantid": "tenant_acme",
  "userid": "user_001",
  "traceid": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "data": {
    "payment_id": "pay_2026081108300001",
    "settlement_id": "stl_2026081108300002",
    "amount": "100.00",
    "fee": "0.50",
    "settled_at": "2026-08-11T08:29:59.000Z",
    "tenant_id": "tenant_acme"
  }
}
```

字段约束：

- `specversion`：固定 `1.0`。
- `id`：UUID v4，事件唯一标识，幂等键。
- `source`：发布者路径，`/nexus/{domain}/{component}`。
- `type`：与 topic 同名，`nexus.{domain}.{event_type}.{version}`。
- `time`：RFC 3339 时间戳。
- `tenantid`、`userid`：顶层字段，便于不解析 data 即可做租户路由。
- `traceid`：W3C Trace Context binary 格式，与 OTel span 对齐。
- `data`：业务 payload，强制包含 `tenant_id`（与顶层 `tenantid` 一致，双重保障）。

### 7.5 死信队列与重试策略

每个业务 topic 配套一个 DLQ topic，命名 `nexus.{domain}.{event_type}.{version}.dlq`。

表：死信队列与重试策略参数说明表

| 策略 | 默认值 | 说明 |
|------|--------|------|
| 重试次数 | 3 | 指数退避：1s / 2s / 4s（可配） |
| 重试异常 | 可重试错误 | 网络超时、5xx、Kafka rebalance；不可重试：schema 校验失败、租户不存在 |
| DLQ 投递 | 重试耗尽后 | DLQ 消息携带 `original_topic`、`failure_reason`、`attempts`、`original_offset` |
| DLQ 消费 | 人工 + 半自动 | OpsMesh 告警面板展示 DLQ 计数；MAOP `/api/nexus/v1/orchestrate/dlq/replay` 支持手动重放 |
| 保留期 | 7d | DLQ topic `retention.ms=604800000`，业务 topic `retention.ms=259200000`（3d） |

OpsMesh 现有 `webhook-dlq` 与 agent 任务死信机制沿用，统一到 Kafka DLQ topic；MAOP 现有 EventBus `DeadLetterEntry` 作为进程内 DLQ 补充。

### 7.6 全部 topic 汇总表

表：Kafka topic 全量汇总对照表

| topic 名 | 发布者 | 订阅者 | 事件 schema | 分区策略 | 保留期 |
|----------|--------|--------|-------------|----------|--------|
| `nexus.data.ready.v1` | DataEngineBDP flink-cdc | MAOP | CloudEvents data: dataset/partition/rows | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.data.governance.completed.v1` | DataEngineBDP governance | MAOP | pipeline_id/dataset/rules | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.data.governance.alert.v1` | DataEngineBDP rule-engine | MAOP / OpsMesh 告警 | dataset/rule/severity | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.data.infra.provisioned.v1` | DataEngineBDP infra-orchestrator | MAOP | cluster_id/provider/status | 按 `tenant_id` hash，6 分区 | 3d |
| `nexus.payment.created.v1` | NexusChain nexus-gateway | MAOP | payment_id/order_id/amount | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.payment.settled.v1` | NexusChain nexus-settlement | MAOP | payment_id/settlement_id/fee | 按 `tenant_id` hash，12 分区 | 7d |
| `nexus.payment.failed.v1` | NexusChain | MAOP | payment_id/stage/reason | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.payment.bridge.completed.v1` | NexusChain nexus-bridge | MAOP | bridge_id/from/to/tx_hash | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.payment.bridge.failed.v1` | NexusChain nexus-bridge | MAOP | bridge_id/reason | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.payment.consortium.proposal.v1` | NexusChain nexus-consortium | MAOP | proposal_id/proposer/action | 按 `tenant_id` hash，6 分区 | 3d |
| `nexus.payment.webhook.dlq.v1` | NexusChain KafkaEventStore | 人工重放 | webhook_id/payload/attempts | 单分区，0 分区 | 7d |
| `nexus.ops.task.completed.v1` | OpsMesh controlplane | MAOP | task_id/type/target/result | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.ops.task.failed.v1` | OpsMesh controlplane | MAOP / OpsMesh 告警 | task_id/reason/attempts | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.ops.deploy.completed.v1` | OpsMesh controlplane | MAOP | deploy_id/targets/revision | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.ops.deploy.failed.v1` | OpsMesh controlplane | MAOP / OpsMesh 告警 | deploy_id/reason | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.ops.alert.v1` | OpsMesh controlplane | MAOP / OpsMesh 告警面板 | alert_id/rule/severity | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.ops.device.online.v1` | OpsMesh agent | MAOP / OpsMesh CMDB | device_id/segment/agent_id | 按 `segment` hash，12 分区 | 3d |
| `nexus.ops.device.offline.v1` | OpsMesh controlplane | MAOP / OpsMesh CMDB | device_id/segment/reason | 按 `segment` hash，12 分区 | 3d |
| `nexus.ops.workflow.completed.v1` | OpsMesh controlplane | MAOP | workflow_id/status/nodes | 按 `tenant_id` hash，6 分区 | 3d |
| `nexus.orchestrate.dag.started.v1` | MAOP | Interaction（经 WS 推送） | dag_id/plan | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.orchestrate.dag.completed.v1` | MAOP | Interaction（经 WS 推送） | dag_id/status/duration | 按 `tenant_id` hash，12 分区 | 7d |
| `nexus.orchestrate.dag.failed.v1` | MAOP | Interaction / OpsMesh 告警 | dag_id/failed_node/reason | 按 `tenant_id` hash，6 分区 | 7d |
| `nexus.orchestrate.node.started.v1` | MAOP | Interaction（经 WS 推送） | dag_id/node_id/capability | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.orchestrate.node.completed.v1` | MAOP | Interaction（经 WS 推送） | dag_id/node_id/output | 按 `tenant_id` hash，12 分区 | 3d |
| `nexus.audit.access.v1` | APISIX | 审计消费（ELK/ClickHouse） | method/path/user_id/status | 按 `tenant_id` hash，24 分区 | 30d |
| `*.dlq` | 各发布者 | 人工重放 / MAOP replay API | original_topic/failure_reason | 单分区 | 7d |

分区策略统一按 `tenant_id` hash（设备类按 `segment` hash），分区数按吞吐量分级（6/12/24）；副本数 `replication.factor=3`，`min.insync.replicas=2`。

---

## 第8章 可观测性方案

### 8.1 OpenTelemetry 全链路追踪

统一采用 OpenTelemetry 作为追踪标准，OTLP gRPC 导出到 OTel Collector，后端存储 Jaeger 或 Tempo。

图：全链路追踪拓扑示意图

```
Interaction ──┐
APISIX ───────┤
MAOP ─────────┼──▶ OTel Collector (:4317 OTLP gRPC) ──▶ Jaeger / Tempo
DataEngineBDP ┤                                          │
NexusChain ───┤                                          ▼
OpsMesh ──────┘                                     Span 查询 / 依赖图
```

- Trace Context 传播：W3C `traceparent` 头（HTTP）/ metadata（gRPC）/ CloudEvents `traceid` 字段（Kafka）。MAOP 已集成 OTel，OpsMesh 已实现 W3C Trace Context HTTP+gRPC 提取/注入，DataEngineBDP OTLP 4317，NexusChain Micrometer+Brave+Zipkin W3C traceparent（需将 Zipkin exporter 切换为 OTLP）。
- Span 命名：`{layer}.{project}.{operation}`，如 `gateway.apisix.route`、`orchestrate.maop.dag.execute`、`capability.dataengine.sql.query`、`capability.nexuschain.payment.settle`、`capability.opsmesh.task.execute`。
- 采样率：默认 10%，支付与异常链路 100% 采样（按 topic 与 error 标记强制采样）。

### 8.2 Metrics

统一采用 Prometheus 指标格式，各项目暴露 `/metrics` 端点，Prometheus scrape 后聚合到 Grafana。

表：Metrics 端点与指标对照表

| 项目 | Metrics 端点 | 指标前缀 | 关键指标 |
|------|--------------|----------|----------|
| APISIX | prometheus plugin（独立端口 9091） | `apisix_` | request_total / request_latency / upstream_5xx |
| MAOP | `/api/prometheus`（端口 9079） | `maop_` | dag_total / dag_duration / node_failed / agent_active / cost_usd |
| DataEngineBDP | Actuator `/actuator/prometheus`（各组件） | `dataengine_` | query_duration / query_rows / governance_rules_passed / llm_tokens |
| NexusChain | Actuator `/actuator/prometheus`（各组件） | `nexuschain_` | payment_total / settle_duration / bridge_latency / mpc_sign_duration |
| OpsMesh | `/metrics`（端口 9091） | `opsmesh_` | agent_active / task_pending / task_duration / deploy_total / http_latency |

### 8.3 Logs

统一采用 JSON 结构化日志，导出到 Loki 或 ELK，按 `tenant_id` + `trace_id` 索引关联。

- 日志格式：JSON Lines，字段含 `timestamp`、`level`、`logger`、`msg`、`tenant_id`、`trace_id`、`span_id`、`thread`、`extra`。
- 各项目现状：MAOP `MAOP_JSON_LOG=1` 已支持；OpsMesh 结构化日志 + Log Collect（loki/es）已支持；DataEngineBDP 结构化 JSON + traceId MDC 已支持；NexusChain Micrometer 日志需补充 JSON 格式化；Interaction Electron 滚动日志（JSON Lines，1MB 截断）保留本地。
- 采集：K8s 环境通过 Filebeat / Promtail 采集容器 stdout；OpsMesh agent 日志通过 `logCollectLoop` 直推 loki/es。

### 8.4 告警

统一采用 Alertmanager 告警，OpsMesh 告警面板作为统一告警展示。

- 告警源：Prometheus Rule（各项目 Helm Chart 自带，OpsMesh `prometheusrule.yaml` 模板）+ OpsMesh 业务告警（`nexus.ops.alert.v1` 事件）+ DataEngineBDP 治理告警（`nexus.data.governance.alert.v1` 事件）。
- 告警通道：OpsMesh `internal/notify/` 已支持 feishu / dingtalk / slack / 企业微信 / SMTP 邮件，统一编排平台复用。
- 告警分级：P0（立即响应，如支付失败、部署失败、DAG 失败）、P1（5min 响应，如任务死信、治理告警）、P2（30min 响应，如设备离线、配额预警）。
- 告警抑制与静默：OpsMesh `/api/v1/alerts/{id}/silence` 已支持，Alertmanager inhibit_rules 配置跨级别抑制。

### 8.5 可观测性端点对照表

表：五项目可观测性端点对照表

| 项目 | 健康/就绪 | Metrics | Tracing | Logs |
|------|-----------|---------|---------|------|
| Interaction | 无（本地静态） | 无 | 无 | Electron 滚动日志（JSON Lines，1MB 截断） |
| APISIX | `/healthz` | prometheus plugin :9091 | OTel plugin | stdout JSON |
| MAOP | `/api/health` | `/api/prometheus` :9079 | OTel OTLP :4317（`MAOP_OTEL_ENABLED=1`） | JSON 结构化（`MAOP_JSON_LOG=1`） |
| DataEngineBDP | Actuator `/actuator/health` | Actuator `/actuator/prometheus`（各组件） | OTLP gRPC :4317 | JSON + traceId MDC |
| NexusChain | Actuator `/actuator/health` | Actuator `/actuator/prometheus`（各组件） | Micrometer+Brave，切换为 OTLP :4317 | JSON（待补格式化） |
| OpsMesh | `/healthz` + `/readyz` | `/metrics` :9091 | OTel OTLP gRPC（`--otel-endpoint`）+ W3C HTTP+gRPC | 结构化 + Log Collect → loki/es |
| OTel Collector | `/health` | 自身 metrics :8888 | — | 自身日志 |

---

## 第9章 部署拓扑

### 9.1 整体部署架构图

统一部署于 K8s 集群（DataEngineBDP SKE 或目标集群），通过 Helm Umbrella Chart 聚合各子 Chart。

图：K8s 集群部署拓扑架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                    K8s 集群 (SKE / 自管)                          │
│                                                                  │
│  namespace: nexus-gateway                                        │
│  ├── APISIX (Deployment, 3 副本)                                 │
│  └── Keycloak (StatefulSet, 3 副本, PG 后端)                     │
│                                                                  │
│  namespace: nexus-orchestrate                                    │
│  ├── MAOP dashboard (Deployment, 2 副本)                         │
│  ├── MAOP agent-exec (Deployment, 2 副本)                        │
│  ├── MAOP queue-worker (Deployment, 2 副本)                      │
│  └── Redis (StatefulSet)                                         │
│                                                                  │
│  namespace: nexus-data                                           │
│  ├── DataEngineBDP 33+ 组件 (各 Deployment/StatefulSet)          │
│  ├── DataEngineBDP Adapter (Deployment)                          │
│  └── Kafka (Strimzi, 3 broker + 3 zookeeper)                     │
│                                                                  │
│  namespace: nexus-payment                                        │
│  ├── NexusChain 7 组件 (各 Deployment/StatefulSet)               │
│  ├── NexusChain Adapter (Deployment)                             │
│  └── Nacos / Seata / Sentinel (StatefulSet)                      │
│                                                                  │
│  namespace: nexus-ops                                            │
│  ├── OpsMesh controlplane (Deployment, 3 副本, leader 选举)      │
│  ├── OpsMesh agent (DaemonSet, 每网段)                           │
│  ├── OpsMesh Adapter (Deployment)                                │
│  └── MySQL / Redis (StatefulSet)                                 │
│                                                                  │
│  namespace: nexus-observability                                  │
│  ├── OTel Collector (Deployment, 3 副本)                         │
│  ├── Prometheus (StatefulSet) + Alertmanager                     │
│  ├── Loki / ELK                                                  │
│  └── Grafana (Deployment)                                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Interaction: 部署在用户本地（Electron + PWA），不进 K8s
```

### 9.2 各项目部署方式对比

表：各项目部署方式对照表

| 项目 | 当前部署方式 | 容器化 | K8s 原生 | 统一编排平台部署建议 |
|------|--------------|--------|----------|----------------------|
| Interaction | Electron + PWA + GitHub Pages + Vercel | 无 | 无 | 保持本地形态，不进 K8s |
| MAOP | pip + docker-compose + 7 profile | docker-compose | 无原生 Helm | 新增 Helm Chart（参考 OpsMesh 模板） |
| DataEngineBDP | 41 Dockerfile + 60 Helm Chart + docker-compose | Dockerfile | 60 Helm Chart | 复用现有 Helm Chart，纳入 Umbrella |
| NexusChain | docker-compose + Helm 子 chart + K8s manifest + Istio + Strimzi | Dockerfile | Helm 子 chart + Istio | 复用现有 Helm 子 chart，纳入 Umbrella |
| OpsMesh | 单 Go 二进制 + Dockerfile(distroless) + Helm Chart(17 模板) + systemd + Operator | Dockerfile + Dockerfile.agent | Helm Chart + Operator | 复用现有 Helm Chart，纳入 Umbrella |

### 9.3 统一部署建议（Helm Umbrella Chart）

建议构建 `nexus-platform` Helm Umbrella Chart，聚合各子 Chart：

```yaml
# Chart.yaml
apiVersion: v2
name: nexus-platform
version: 1.0.0
type: application
dependencies:
  - name: apisix
    version: 2.x.x
    repository: https://charts.apiseven.com
  - name: keycloak
    version: 20.x.x
    repository: https://codecentric.github.io/helm-charts
  - name: maop
    version: 1.x.x
    repository: file://charts/maop
  - name: dataengine-bdp
    version: 1.x.x
    repository: file://charts/dataengine-bdp
  - name: nexuschain
    version: 1.x.x
    repository: file://charts/nexuschain
  - name: opsmesh
    version: 1.x.x
    repository: file://charts/opsmesh
  - name: strimzi-kafka-operator
    version: 0.40.x
    repository: https://strimzi.io/charts
  - name: opentelemetry-collector
    version: 0.96.x
    repository: https://open-telemetry.github.io/opentelemetry-helm-charts
  - name: kube-prometheus-stack
    version: 60.x.x
    repository: https://prometheus-community.github.io/helm-charts
  - name: loki-stack
    version: 2.x.x
    repository: https://grafana.github.io/helm-charts
```

部署命令示例：通过 Helm Umbrella Chart 一键部署 Nexus 平台

```bash
helm dependency update ./nexus-platform
helm install nexus ./nexus-platform \
  -f values-dev.yaml \
  -n nexus-platform --create-namespace
```

### 9.4 环境分层

表：环境分层参数说明表

| 环境 | 目的 | 副本数 | 资源配额 | 数据 | 鉴权 |
|------|------|--------|----------|------|------|
| dev | 开发联调 | 单副本 | 低（1C1G） | mock / 内存 | auth 关闭 |
| staging | 预发布验证 | 2 副本 | 中（2C4G） | 脱敏快照 | auth 开启，自签证书 |
| prod | 生产 | 3 副本 | 高（4C8G+） | 真实 | auth 开启，Keycloak RS256，TLS 1.2+ |

环境隔离通过 K8s namespace + NetworkPolicy + ResourceQuota 实现（沿用 DataEngineBDP 三重隔离模式）；配置通过 Helm values-{env}.yaml 分层覆盖。

### 9.5 部署产物与端口映射表

表：各项目部署产物与端口映射对照表

| 项目 | 部署产物 | K8s namespace | 对内端口 | 对外端口（经 APISIX） | 备注 |
|------|----------|---------------|----------|----------------------|------|
| Interaction | Electron exe + PWA HTML | —（本地） | 8123（本地静态） | — | 用户本地 |
| APISIX | apisix image | nexus-gateway | 9080 / 9443 | 80 / 443（Ingress） | 统一入口 |
| Keycloak | keycloak image | nexus-gateway | 8080 | —（内部） | IdP |
| MAOP | maop image（dashboard + agent-exec + queue-worker） | nexus-orchestrate | 9079 | `/api/nexus/v1/orchestrate/*` | 编排引擎 |
| DataEngineBDP | 33+ 镜像 + Adapter | nexus-data | 8080-8086 / 18086 / 18090 / 8094 | `/api/nexus/v1/data/*` | 数据能力 |
| NexusChain | 7 镜像 + Adapter | nexus-payment | 8080-8085 / 19585 / 9235 / 9999 / 50051 | `/api/nexus/v1/payment/*` | 价值流转 |
| OpsMesh | opsmesh image（controlplane + agent DaemonSet） + Adapter | nexus-ops | 8080 / 9090 / 9091 | `/api/nexus/v1/ops/*` | 运维执行 |
| Kafka | Strimzi | nexus-data | 9092（broker） | — | 事件总线 |
| OTel Collector | otel-collector-contrib | nexus-observability | 4317（OTLP gRPC） / 4318（HTTP） | — | 追踪 |
| Prometheus | prom image | nexus-observability | 9090 | — | 指标 |
| Alertmanager | alertmanager image | nexus-observability | 9093 | — | 告警 |
| Loki | loki image | nexus-observability | 3100 | — | 日志 |
| Grafana | grafana image | nexus-observability | 3000 | 3000（Ingress） | 可视化 |

---

## 第10章 非功能需求

### 10.1 性能

表：性能非功能需求参数说明表

| 指标 | 目标值 | 适用链路 | 依据 |
|------|--------|----------|------|
| 网关 P99 延迟 | < 20ms（不含下游） | APISIX 路由 | APISIX 性能基线 |
| 编排查询 P99 | < 200ms | MAOP DAG 查询 | MAOP 现有指标 |
| 数据查询 P99 | < 2s（小结果集） | data.query | DataEngineBDP sql-gateway |
| 支付创建 P99 | < 500ms | payment.create | NexusChain nexus-gateway |
| 支付结算 P99 | < 5s（异步触发 < 1s） | payment.settle | NexusChain nexus-settlement |
| 跨链桥 P99 | < 30s | payment.bridge | NexusChain nexus-bridge |
| 运维任务下发 P99 | < 200ms（异步触发） | ops.task | OpsMesh controlplane |
| 部署完成 P99 | < 5min（标准 Helm） | ops.deploy | OpsMesh controlplane |
| 事件投递 P99 | < 100ms | Kafka 发布 | Kafka 集群基线 |
| WebSocket 推送延迟 | < 1s | MAOP /ws → Interaction | MAOP 15s snapshot 改为事件驱动 |
| 系统吞吐 | > 10000 RPS（网关层） | APISIX | APISIX 水平扩展 |

### 10.2 可用性

表：可用性非功能需求参数说明表

| 指标 | 目标值 | 实现方式 |
|------|--------|----------|
| 平台整体 SLA | 99.9%（prod） | 多副本 + 跨 AZ 部署 |
| 网关 SLA | 99.95% | APISIX 3 副本 + 健康检查 + HPA |
| 编排引擎 SLA | 99.9% | MAOP 2 副本 + Redis HA |
| 数据能力 SLA | 99.9% | DataEngineBDP 各组件多副本 + Karmada 联邦降级 |
| 支付能力 SLA | 99.95% | NexusChain 多副本 + Seata TCC 最终一致 + Sentinel 熔断 |
| 运维能力 SLA | 99.9% | OpsMesh 3 副本 leader 选举 + agent failover |
| 事件总线 SLA | 99.95% | Kafka 3 broker + min.insync.replicas=2 + DLQ |
| RTO | < 5min | K8s 自调度 + 健康探针 + 自动重启 |
| RPO | < 1s（事件）/ 0（同步） | Kafka 同步刷盘 + 同步链路无 RPO |

### 10.3 安全

表：安全非功能需求参数说明表

| 维度 | 要求 | 实现方式 |
|------|------|----------|
| 传输加密 | 全链路 TLS 1.2+ | APISIX HTTPS + 内部 mTLS（Karmada/MPC/OpsMesh gRPC） |
| 身份认证 | Keycloak RS256 JWT，OIDC Authorization Code + PKCE | 统一鉴权方案（第6章） |
| 授权 | RBAC + scope + 租户隔离 | MAOP RBAC + Keycloak scope + tenant_id 行级隔离 |
| 租户隔离 | 行级 + Namespace + NetworkPolicy + ResourceQuota | DataEngineBDP 三重隔离模式推广 |
| 密钥管理 | Vault 托管 / K8s Secret + Sealed Secrets | MAOP Vault backend + OpsMesh kubeconfig AES-256-GCM |
| 审计 | 100% 留痕，保留 90d | APISIX audit topic + OpsMesh audit_log + MAOP AuditLogger |
| 合规 | GDPR | MAOP compliance + Interaction GDPR 日志 |
| 防重放 | timestamp + nonce | NexusChain HMAC + OpsMesh Install Token |
| WAF | APISIX waf plugin | SQL 注入 / XSS / 路径遍历防护 |
| 依赖安全 | 定期 CVE 扫描 | 各项目 CI/CD + Trivy / Grype |

### 10.4 扩展性

表：扩展性非功能需求参数说明表

| 维度 | 要求 | 实现方式 |
|------|------|----------|
| 水平扩展 | 所有无状态组件支持 HPA | APISIX / MAOP / Adapter / OpsMesh controlplane HPA |
| 能力扩展 | 新能力节点通过 Adapter 即插即用 | MAOP Registry + Adapter 通用契约（第5章） |
| 事件扩展 | 新 topic 按 naming spec 即可 | Kafka topic 自动创建（Strimzi） |
| 协议扩展 | A2A / MCP 标准协议 | MAOP A2A + MCP Hub 已实现 |
| 插件扩展 | MAOP Plugin System + OpsMesh Operator | MAOP `core/plugin.py` + OpsMesh CRD |
| 多云扩展 | DataEngineBDP 4 provider + Karmada 联邦 | infra-orchestrator + karmada-federated-query |
| 多链扩展 | NexusChain 多链桥 + 联盟 PoA | nexus-bridge Web3j + nexus-consortium |
| 网段扩展 | OpsMesh 网段分桶 + 联邦 | OpsMesh `--segment` + federation |
| 配额扩展 | 租户级多资源配额 | MAOP ResourceQuotaManager + NexusChain TenantRateLimiter |

---

## 附录 A：关键设计决策汇总

表：关键设计决策对照表

| 决策点 | 决策 | 理由 |
|--------|------|------|
| 编排引擎选型 | MAOP | 已具备 Plan-Execute-Verify + DAG + Registry + A2A + MCP，最成熟 |
| 统一网关 | APISIX + Keycloak | DataEngineBDP 已用 APISIX + Keycloak，复用成本最低 |
| 统一 IdP | Keycloak realm=nexus | DataEngineBDP 已有 Keycloak，新增 realm 不影响现有 |
| JWT 签名算法 | RS256 | OpsMesh 已支持 RS256 验签，安全性高于 HS256 |
| 统一事件总线 | Kafka + Strimzi | DataEngineBDP / NexusChain 已用 Kafka，OpsMesh 可插拔 Bus 已支持 kafka |
| 事件格式 | CloudEvents 1.0 | 业界标准，与 OTel trace 关联友好 |
| topic 命名 | `nexus.{domain}.{event_type}.{version}` | 域隔离 + 版本化 + 可读性 |
| 租户透传 | HTTP 头 + gRPC metadata + 事件字段 | OpsMesh 网关头注入模式最成熟，推广到全链路 |
| 租户隔离 | tenant_id 行级 + Namespace + NetworkPolicy | DataEngineBDP 三重隔离最严格 |
| 跨系统 agent 协议 | A2A（JSON-RPC 2.0） | MAOP 已实现 Google A2A 标准 |
| 工具调用协议 | MCP | MAOP 已实现 MCP Hub |
| 统一部署 | Helm Umbrella Chart | OpsMesh Helm Chart 模板最完整，作为参考 |
| 可观测性 | OTel + Prometheus + Loki | MAOP + OpsMesh 均已实现 OTel，业界主流 |
| OpsMesh 角色 | 能力节点 + 执行底座 | 复用其 agent 纳管与网段分桶，避免 MAOP 重复造轮子 |
| Adapter 部署位置 | 能力节点侧 | 就近转换，减少 MAOP 耦合，故障隔离 |

---

## 附录 B：与盘点报告的对应关系

表：HLD 章节与盘点报告对应关系对照表

| HLD 章节 | 上游盘点依据 |
|----------|--------------|
| 第2章 整体架构 | 两份报告"概述"与"统一编排平台设计建议" |
| 第3章 统一 API 网关层 | DataEngineBDP APISIX/Keycloak + OpsMesh 网关头注入 |
| 第4章 MAOP 编排引擎层 | MAOP 报告 1.8 Agent 机制 + 1.4 事件总线 |
| 第5章 Adapter 接口契约 | DataEngineBDP A 节点 + NexusChain A 节点 + OpsMesh A 节点 |
| 第6章 鉴权与租户透传 | DataEngineBDP C/E + NexusChain C/E + MAOP 1.3/1.5 + OpsMesh 2.3/2.5 + Interaction 3.3/3.5 |
| 第7章 Kafka 事件总线 | DataEngineBDP D + NexusChain D + OpsMesh 2.4 + MAOP 1.4 |
| 第8章 可观测性 | DataEngineBDP F + NexusChain F + MAOP 1.6 + OpsMesh 2.6 + Interaction 3.6 |
| 第9章 部署拓扑 | DataEngineBDP G + NexusChain G + MAOP 1.7 + OpsMesh 2.7 + Interaction 3.7 |
| 第10章 非功能需求 | 两份报告"汇总对比" + "统一编排平台设计建议" |

---

> 文档结束。本 HLD 基于两份接口盘点报告的静态盘点结果编写，所有端点、端口、鉴权、事件、部署信息均来自实际代码与配置文件，未修改任何源码。后续 LLD 应以本 HLD 为上游依据，细化各 Adapter 接口 schema、APISIX 路由配置、Keycloak realm 配置、Kafka topic 创建脚本、Helm Umbrella Chart 模板等。