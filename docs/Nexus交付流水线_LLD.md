# Nexus交付流水线_LLD

> 文档版本：v1.0
> 编写日期：2026-08-11
> 文档性质：低层设计文档（Low-Level Design，LLD）
> 上游文档：Nexus 统一编排平台 HLD、接口盘点报告（DataEngineBDP × NexusChain、MAOP × OpsMesh × Interaction）
> 输出路径：F:\Nexus\Workflow\Nexus交付流水线_LLD.md

---

## 第1章 文档概述

### 1.1 目的

本文档为 Nexus 统一编排平台"交付流水线"模块的低层设计文档（LLD），目的在于：

- 将 HLD 中"需求 → 编码 → 审查 → 构建 → 测试 → 部署 → 压测 → 回归"8 阶段交付流水线落地为可实现的工程方案
- 明确每个阶段由哪个项目承载、调用哪些具体 API、下发什么任务、编排哪个 agent
- 给出 MAOP DAG 编排定义、agents.yaml 配置、k6 压测脚本规范、失败回滚/重试/死信策略
- 与文档生成 Workflow（doc-pipeline）建立联动关系
- 拆分实现任务，作为后续开发、联调、验收的依据

### 1.2 范围

表：本文档覆盖范围说明表

| 维度 | 覆盖 | 不覆盖 |
|------|------|--------|
| 项目 | Interaction、MAOP、OpsMesh、DataEngineBDP、NexusChain | 平台外的第三方 SaaS |
| 阶段 | 需求/编码/审查/构建/测试/部署/压测/回归 | 需求前期的市场调研、上线后的运营 |
| 接口 | 各项目对外 API、gRPC、事件总线、Agent 机制 | 各项目内部实现细节 |
| 产物 | 各阶段产物名、存储位置、流转关系 | 产物的具体格式定义 |
| 编排 | MAOP DAG workflow、agents.yaml、条件分支、并行编排 | MAOP 内部调度算法实现 |
| 压测 | k6 脚本规范、模板、目标 API、报告格式 | k6 源码改造 |
| 容错 | 失败检测、重试、回滚、死信、超时 | 业务逻辑正确性验证 |
| 联动 | 文档生成 Workflow 触发时机与调用方式 | 文档生成内部实现 |

### 1.3 与 HLD 的关系

表：LLD 与 HLD 对应关系表

| HLD 层次 | LLD 对应 | 说明 |
|---------|---------|------|
| 5 项目架构定位 | 第2章 总体设计 | 复用 HLD 的项目定位，明确各阶段承载项目 |
| 编排架构 Interaction → MAOP → 能力节点 | 第3章 各阶段详细设计 + 第4章 DAG 编排 | 将编排架构细化为具体 API 调用与 DAG 节点 |
| Kafka 事件驱动 | 第3章 各阶段完成事件 + 第7章 触发与监控 | 明确事件 topic、payload、消费方 |
| 各项目能力 | 第3章 承载项目 + 第5章 压测目标 + 第6章 回滚策略 | 引用接口盘点报告的具体端点 |
| 文档生成 Workflow | 第8章 联动 | 作为交付流水线的产物生成器 |

### 1.4 术语表

表：术语对照表

| 术语 | 全称 | 含义 |
|------|------|------|
| MAOP | Multi-Agent Orchestration Platform | 多 Agent 编排平台，本流水线的编排引擎 |
| OpsMesh | Operations Mesh | 运维网格，任务执行底座 |
| Interaction | Agent Workbench | Agent 工作台，用户交互入口 |
| DataEngineBDP | Data Engine Big Data Platform | 数据引擎大数据平台 |
| NexusChain | Nexus Chain | 区块链支付编排平台 |
| DAG | Directed Acyclic Graph | 有向无环图，MAOP 工作流编排模型 |
| A2A | Agent-to-Agent | Agent 间通信协议（JSON-RPC 2.0） |
| MCP | Model Context Protocol | 模型上下文协议，工具调用标准 |
| SKE | Secure Kubernetes Environment | 安全 K8s 环境，DataEngineBDP 部署底座 |
| APISIX | Apache APISIX | API 网关 |
| SSE | Server-Sent Events | 服务端推送事件 |
| DLQ | Dead Letter Queue | 死信队列 |
| TCC | Try-Confirm-Cancel | 分布式事务模式 |
| Plan-Execute-Verify | 三阶段循环 | MAOP 编排核心循环 |
| k6 | Grafana k6 | 开源压测工具 |
| HikariCP | Hikari Connection Pool | Java 连接池 |
| Tomcat | Apache Tomcat | Java Servlet 容器 |
| Helm | CNCF Helm | K8s 包管理工具 |
| Strimzi | Strimzi Kafka Operator | K8s 上的 Kafka Operator |

### 1.5 输入依据

本文档基于以下两份接口盘点报告编写，所有 API、端口、鉴权、事件总线、部署方式、Agent 机制均来自实际代码与配置文件的只读扫描：

- F:\Nexus\Workflow\_盘点_DataEngineBDP_NexusChain.md
- F:\Nexus\Workflow\_盘点_MAOP_OpsMesh_Interaction.md

表：5 项目关键能力摘要表

| 项目 | 主端口 | 协议 | 鉴权 | 事件总线 | 部署 | Agent 机制 |
|------|--------|------|------|---------|------|------------|
| Interaction | 8123（静态） | HTTP + Electron IPC | 无账号体系，AI Key 加密 | localStorage + Webhook Bus | PWA + Electron + GitHub Pages | 4 场景 subagent + function-calling |
| MAOP | 9079（FastAPI） | HTTP REST + WebSocket + A2A | JWT(HS256) + RBAC + TLS + License | Async EventBus + RabbitMQ + Redis Streams | docker-compose + 7 profile | agents.yaml + Plan-Execute-Verify + Subagent + MCP Hub |
| OpsMesh | 8080/9090/9091 | HTTP REST + gRPC + SSE | 网关身份头 + JWT + gRPC mTLS + HMAC | 可插拔 Bus（noop/log/kafka）+ Audit + Alert Webhook | Helm Chart（17 模板）+ distroless | 单二进制 --mode=agent + gRPC 通道 |
| DataEngineBDP | 8080-8086 等 | HTTP REST + gRPC | JWT(HMAC-SHA256) + Keycloak | Kafka（CDC/VirtualAdapter/Knative）+ Webhook | 41 Dockerfile + 60 Helm Chart | 33+ 组件，无独立 Agent 框架 |
| NexusChain | 8080/19585/50051 等 | HTTP REST + gRPC | API Key + HMAC-SHA256 + 时间戳防重放 | Kafka（payment-events/webhook-dlq）+ Nacos + Seata TCC | docker-compose + Helm + Istio + Strimzi | 11+ 组件，无独立 Agent 框架 |

---

## 第2章 交付流水线总体设计

### 2.1 8 阶段交付流水线

图：Nexus交付流水线总体流程图

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  需求    │ -> │  编码    │ -> │  审查    │ -> │  构建    │ -> │  测试    │
│Require   │    │Code      │    │Review    │    │Build     │    │Test      │
│Interaction│    │MAOP      │    │MAOP      │    │OpsMesh   │    │OpsMesh   │
│          │    │coder     │    │reviewer  │    │多语言    │    │+DataEngine│
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                  │
                                                                  v
┌──────────┐    ┌──────────┐    ┌──────────┐
│  回归    │ <- │  压测    │ <- │  部署    │
│Regress   │    │LoadTest  │    │Deploy    │
│MAOP      │    │k6        │    │OpsMesh   │
│regression│    │JS脚本    │    │+DataEngine│
└──────────┘    └──────────┘    └──────────┘
```

各阶段承载项目与核心能力：

表：8 阶段承载项目对照表

| 阶段 | 英文 | 承载项目 | 核心能力 |
|------|------|---------|---------|
| 需求 | Require | Interaction + MAOP | 工作台提交需求，MAOP plan 工具拆解为 DAG |
| 编码 | Code | MAOP | 编排 coder agent（agents.yaml 定义），多代理协作 |
| 审查 | Review | MAOP | 编排 reviewer agent + code-review subagent，产出审查报告 |
| 构建 | Build | OpsMesh | 下发构建任务到构建 agent，支持 Java/Go/Python/Node 多语言 |
| 测试 | Test | OpsMesh + DataEngineBDP | OpsMesh 执行测试任务，DataEngineBDP 提供测试数据/环境（SKE） |
| 部署 | Deploy | OpsMesh + DataEngineBDP | OpsMesh 编排 Helm 部署，DataEngineBDP SKE 提供部署底座 |
| 压测 | LoadTest | k6 + OpsMesh | k6 脚本（JS）对部署后服务压测 |
| 回归 | Regress | MAOP | regression 模块（persona simulation），产出回归报告 |

### 2.2 阶段间数据流转与事件驱动

#### 2.2.1 数据流转

表：阶段间产物流转对照表

| 上游阶段 | 下游阶段 | 流转产物 | 存储位置 | 流转方式 |
|---------|---------|---------|---------|---------|
| 需求 | 编码 | 需求规格（DAG + 任务列表） | MAOP SQLite/PostgreSQL | MAOP 内部状态 |
| 编码 | 审查 | 代码变更（patch / branch） | Git worktree（MAOP worktree 模块） | Git 引用 |
| 审查 | 构建 | 审查通过标记 + 代码快照 | MAOP 审计日志 + Git | 事件 + Git tag |
| 构建 | 测试 | 制品（镜像 / jar / binary） | OpsMesh 任务产物 + 镜像仓库 | OpsMesh 任务结果 + Registry |
| 测试 | 部署 | 测试报告 + 通过标记 | OpsMesh 任务产物 | OpsMesh 任务结果 |
| 部署 | 压测 | 部署清单 + 服务端点 | OpsMesh 部署记录 + K8s | Helm release + Service |
| 压测 | 回归 | 压测报告 + SLA 标记 | k6 报告产物 | 文件 + 事件 |
| 回归 | （完成） | 回归报告 + 交付总结 | MAOP 报告 | MAOP 报告 API |

#### 2.2.2 事件驱动

各阶段完成后通过 Kafka 事件触发下一阶段。MAOP EventBus 与 OpsMesh KafkaBus 均支持 pub/sub，统一使用以下 topic 命名规范：

表：交付流水线 Kafka 事件 topic 对照表

| 事件 topic | 发布方 | 订阅方 | 触发下一阶段 | payload 关键字段 |
|-----------|--------|--------|------------|----------------|
| nexus.delivery.require.completed | MAOP | MAOP 编码编排器 | 编码 | requirement_id, dag_id, tasks[] |
| nexus.delivery.code.completed | MAOP | MAOP 审查编排器 | 审查 | commit_id, branch, files[] |
| nexus.delivery.review.completed | MAOP | MAOP 构建编排器 | 构建 | review_passed, review_report_url |
| nexus.delivery.build.completed | OpsMesh | OpsMesh 测试调度器 | 测试 | artifact_url, image_tag, build_log_url |
| nexus.delivery.test.completed | OpsMesh | OpsMesh 部署调度器 | 部署 | test_passed, test_report_url, coverage |
| nexus.delivery.deploy.completed | OpsMesh | k6 压测触发器 | 压测 | release_name, service_endpoints[] |
| nexus.delivery.loadtest.completed | k6 | MAOP 回归编排器 | 回归 | sla_passed, loadtest_report_url |
| nexus.delivery.regress.completed | MAOP | Interaction 工作台 | （完成） | regress_passed, delivery_summary_url |
| nexus.delivery.*.failed | 各阶段 | OpsMesh 告警 + Interaction | 告警/重试/回滚 | stage, error, trace_id, retryable |

事件信封遵循 OpsMesh 事件契约（SchemaVersion = "1.0.0"）：

```yaml
# 配置示例：事件信封契约
event:
  schemaVersion: "1.0.0"
  tenantID: "tenant-xxx"
  userID: "user-xxx"
  action: "stage_completed"
  target: "nexus.delivery.build"
  detail:
    pipeline_id: "pipe-20260811-001"
    stage: "build"
    artifact_url: "registry.example.com/nexus/app:v1.2.3"
  level: "LevelInfo"
  version: "1.0.0"
```

### 2.3 与文档生成 Workflow 的关系

文档生成 Workflow（doc-pipeline）作为交付流水线的产物生成器，在以下时机被调用：

- 部署成功后：自动生成 API 文档、变更日志、部署清单
- 回归完成后：生成交付总结报告、验收报告
- 任意阶段失败后：生成失败分析报告（供人工介入）

详细联动设计见第8章。

---

## 第3章 各阶段详细设计

### 3.1 需求阶段（Require）

#### 3.1.1 承载项目

Interaction（交互层）+ MAOP（编排引擎）

#### 3.1.2 触发方式

用户在 Interaction 工作台手动提交需求，或通过 Webhook 触发（如外部 Issue 系统创建 Issue 后触发）。

#### 3.1.3 具体实现

1. 用户在 Interaction 工作台（http://127.0.0.1:8123/agent-workbench.html）通过 `plan` 工具（TOOLS[12]）提交需求描述
2. Interaction 通过 fetch 调用 MAOP 控制面 API：

```bash
# 命令示例：提交需求到MAOP编排控制面
curl -X POST http://maop:9079/api/control/run \
  -H "Authorization: Bearer ${MAOP_JWT}" \
  -H "Content-Type: application/json" \
  -d '{
    "task": "拆解需求并生成交付DAG",
    "requirement": "用户提交的需求描述",
    "pipeline": "nexus-delivery"
  }'
```

3. MAOP 通过 Plan-Execute-Verify 的 plan 阶段（`maop_plan.py`）将需求拆解为 DAG，DAG 节点对应后续 7 个阶段
4. MAOP 通过 Dispatcher（`dispatcher.py` + `core/dynamic_router.py`）将各节点路由到对应 agent

#### 3.1.4 输入/输出产物

表：需求阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 需求描述 | Interaction localStorage | 用户在工作台输入的文本 |
| 输出 | DAG 定义 | MAOP SQLite/PostgreSQL | 8 阶段 DAG + 任务列表 |
| 输出 | pipeline_id | MAOP EventBus | 流水线实例 ID，贯穿后续所有阶段 |

#### 3.1.5 完成事件

发布 `nexus.delivery.require.completed` 到 MAOP EventBus，payload 包含 `requirement_id, dag_id, tasks[]`，触发编码阶段。

### 3.2 编码阶段（Code）

#### 3.2.1 承载项目

MAOP（编排 coder agent）

#### 3.2.2 触发方式

订阅 `nexus.delivery.require.completed` 事件自动触发。

#### 3.2.3 具体实现

1. MAOP 从 EventBus 消费 `nexus.delivery.require.completed`
2. MAOP 通过 agents.yaml 中定义的 coder agent 执行编码任务：

```bash
# 命令示例：MAOP编排coder agent执行编码
maop run --task "实现需求 XXX 的代码" --agent coder --dag-id ${DAG_ID}
```

3. coder agent 通过 MCP Hub（`core/mcp_hub.py`）调用代码生成工具，通过 Subagent（`core/subagent.py`）实现多代理协作
4. 编码过程中使用 Git worktree（`core/worktree.py`）隔离分支，避免污染主分支
5. 编码完成后通过 A2A 协议（`/a2a` 端点）将代码变更通知审查 agent

#### 3.2.4 输入/输出产物

表：编码阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | DAG + 任务列表 | MAOP 内部状态 | 来自需求阶段 |
| 输出 | 代码变更 | Git worktree 分支 | patch 或 branch |
| 输出 | commit_id | Git | 提交 ID |

#### 3.2.5 完成事件

发布 `nexus.delivery.code.completed`，payload 包含 `commit_id, branch, files[]`，触发审查阶段。

### 3.3 审查阶段（Review）

#### 3.3.1 承载项目

MAOP（编排 reviewer agent + code-review subagent）

#### 3.3.2 触发方式

订阅 `nexus.delivery.code.completed` 事件自动触发。

#### 3.3.3 具体实现

1. MAOP 从 EventBus 消费 `nexus.delivery.code.completed`
2. MAOP 通过 agents.yaml 中定义的 reviewer agent 执行代码审查
3. 通过 Subagent 管理器（`core/subagent_manager.py`）spawn code-review subagent，对代码变更进行细粒度审查
4. 审查结果通过 `/api/subagent/transcript` 端点查询
5. 审查失败则发布 `nexus.delivery.review.failed`，触发回退到编码阶段（带审查意见）

#### 3.3.4 输入/输出产物

表：审查阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 代码变更 | Git worktree 分支 | 来自编码阶段 |
| 输出 | 审查报告 | MAOP 审计日志 | 通过/失败 + 问题列表 |
| 输出 | review_passed | 事件 payload | 布尔值，决定是否进入构建 |

#### 3.3.5 完成事件

发布 `nexus.delivery.review.completed`（含 `review_passed` 字段），若 `review_passed=true` 触发构建阶段，否则触发编码阶段重做。

### 3.4 构建阶段（Build）

#### 3.4.1 承载项目

OpsMesh（下发构建任务到构建 agent）

#### 3.4.2 触发方式

订阅 `nexus.delivery.review.completed`（且 `review_passed=true`）事件自动触发。

#### 3.4.3 具体实现

1. MAOP 通过 OpsMesh HTTP API 下发构建任务：

```bash
# 命令示例：通过OpsMesh下发构建任务
curl -X POST http://opsmesh:8080/api/v1/tasks \
  -H "X-Tenant-ID: ${TENANT_ID}" \
  -H "X-User-Id: ${USER_ID}" \
  -H "Authorization: Bearer ${OPSMESH_JWT}" \
  -d '{
    "type": "shell",
    "command": "build.sh",
    "segment": "build-segment",
    "timeout": 1800,
    "max_retries": 2,
    "env": {
      "GIT_REPO": "https://github.com/nexus/app",
      "GIT_COMMIT": "${COMMIT_ID}",
      "BUILD_LANG": "java"
    }
  }'
```

2. OpsMesh 控制面将任务派发到对应网段的构建 agent（通过 gRPC PullTasks）
3. 构建 agent 执行 shell 任务，支持多语言构建：
   - Java：`gradle build` 或 `mvn package`（JDK 17 / JDK 8）
   - Go：`go build -o binary ./cmd/app`
   - Python：`pip wheel .` 或 `python -m build`
   - Node：`npm run build` 或 `tsc`
4. 构建产物（镜像/jar/binary）推送到镜像仓库或制品仓库
5. 多语言构建可并行（详见第4章 DAG 并行编排）

#### 3.4.4 输入/输出产物

表：构建阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | commit_id | Git | 来自审查阶段 |
| 输出 | 镜像/制品 | 镜像仓库 / OpsMesh 任务产物 | image_tag 或 artifact_url |
| 输出 | build_log | OpsMesh 任务结果 | 构建日志 URL |

#### 3.4.5 完成事件

发布 `nexus.delivery.build.completed`，payload 包含 `artifact_url, image_tag, build_log_url`，触发测试阶段。

### 3.5 测试阶段（Test）

#### 3.5.1 承载项目

OpsMesh（执行测试任务）+ DataEngineBDP（提供测试数据/环境）

#### 3.5.2 触发方式

订阅 `nexus.delivery.build.completed` 事件自动触发。

#### 3.5.3 具体实现

1. DataEngineBDP 通过 SKE（Secure Kubernetes Environment）准备测试环境，通过 infra-orchestrator（端口 8085）API 创建测试命名空间：

```bash
# 命令示例：DataEngineBDP准备测试环境
curl -X POST http://dataengine:8085/api/v1/infra/provision \
  -H "Authorization: Bearer ${DATAENGINE_JWT}" \
  -d '{
    "env": "test",
    "namespace": "nexus-test-${PIPELINE_ID}",
    "skeleton": "ske-test-template"
  }'
```

2. DataEngineBDP 通过 sql-gateway（端口 8081）提供测试数据，使用 SQLite 模拟数据库环境（用户偏好）：

```bash
# 命令示例：DataEngineBDP SQL网关准备测试数据
curl -X POST http://dataengine:8081/api/v1/query \
  -H "Authorization: Bearer ${DATAENGINE_JWT}" \
  -d '{
    "sql": "SELECT * FROM test_data WHERE scenario = ${SCENARIO}",
    "adapter": "sqlite"
  }'
```

3. OpsMesh 下发测试任务到测试 agent，执行单元测试、集成测试、E2E 测试
4. 单元测试与集成测试可并行（详见第4章 DAG 并行编排）
5. 测试报告通过 OpsMesh `/api/v1/tasks/{id}/result` 查询

#### 3.5.4 输入/输出产物

表：测试阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 镜像/制品 | 镜像仓库 | 来自构建阶段 |
| 输入 | 测试数据 | DataEngineBDP sql-gateway | SQLite 模拟数据 |
| 输入 | 测试环境 | DataEngineBDP SKE | K8s 测试命名空间 |
| 输出 | 测试报告 | OpsMesh 任务产物 | 通过率、覆盖率、失败用例 |
| 输出 | test_passed | 事件 payload | 布尔值，决定是否进入部署 |

#### 3.5.5 完成事件

发布 `nexus.delivery.test.completed`，payload 包含 `test_passed, test_report_url, coverage`，若 `test_passed=true` 触发部署阶段，否则触发审查阶段修复。

### 3.6 部署阶段（Deploy）

#### 3.6.1 承载项目

OpsMesh（编排 Helm 部署）+ DataEngineBDP（SKE 部署底座）

#### 3.6.2 触发方式

订阅 `nexus.delivery.test.completed`（且 `test_passed=true`）事件自动触发。

#### 3.6.3 具体实现

1. OpsMesh 通过部署中心 API（`/api/v1/deploys`）编排 Helm 部署：

```bash
# 命令示例：OpsMesh编排Helm部署
curl -X POST http://opsmesh:8080/api/v1/deploys \
  -H "X-Tenant-ID: ${TENANT_ID}" \
  -H "Authorization: Bearer ${OPSMESH_JWT}" \
  -d '{
    "chart": "nexus-app",
    "release": "nexus-app-${PIPELINE_ID}",
    "namespace": "nexus-prod",
    "values": {
      "image.tag": "${IMAGE_TAG}",
      "replicas": 3
    },
    "strategy": "blue-green",
    "rollback_on_failure": true
  }'
```

2. DataEngineBDP 通过 SKE 提供部署底座（K8s 集群 + APISIX 网关 + 监控）
3. OpsMesh 通过 `/api/v1/k8s/clusters` 管理目标集群
4. 部署策略支持蓝绿、金丝雀、滚动更新（OpsMesh 部署中心支持 fan-out + Reconcile + Rollback）
5. 部署后通过健康检查端点（`/healthz`、`/readyz`）验证服务就绪

#### 3.6.4 输入/输出产物

表：部署阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 镜像/制品 | 镜像仓库 | 来自构建阶段 |
| 输入 | Helm Chart | OpsMesh 部署配置 | nexus-app chart |
| 输出 | Helm release | K8s 集群 | release_name |
| 输出 | 服务端点 | K8s Service + Ingress | service_endpoints[] |
| 输出 | 部署清单 | OpsMesh 部署记录 | 部署详情 |

#### 3.6.5 完成事件

发布 `nexus.delivery.deploy.completed`，payload 包含 `release_name, service_endpoints[]`，触发压测阶段。

### 3.7 压测阶段（LoadTest）

#### 3.7.1 承载项目

k6（压测工具）+ OpsMesh（任务下发）

#### 3.7.2 触发方式

订阅 `nexus.delivery.deploy.completed` 事件自动触发。

#### 3.7.3 具体实现

1. MAOP 通过 OpsMesh 下发 k6 压测任务：

```bash
# 命令示例：OpsMesh下发k6压测任务
curl -X POST http://opsmesh:8080/api/v1/tasks \
  -H "X-Tenant-ID: ${TENANT_ID}" \
  -H "Authorization: Bearer ${OPSMESH_JWT}" \
  -d '{
    "type": "shell",
    "command": "k6 run /scripts/loadtest.js",
    "env": {
      "TARGET_BASE_URL": "http://nexus-app-prod:8080",
      "SCENARIO": "smoke,load,stress"
    },
    "timeout": 3600
  }'
```

2. k6 脚本（JavaScript）对部署后服务压测，目标包括：
   - NexusChain 支付 API（`/api/v1/payments`、`/api/v1/orders`）
   - DataEngineBDP SQL 网关（`/api/v1/query`）
   - OpsMesh 任务 API（`/api/v1/tasks`）
3. k6 脚本规范详见第5章
4. 压测报告包含 RPS、延迟分位、错误率、资源占用

#### 3.7.4 输入/输出产物

表：压测阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 服务端点 | K8s Service | 来自部署阶段 |
| 输入 | k6 脚本 | 脚本仓库 | JavaScript 脚本 |
| 输出 | 压测报告 | k6 产物 | RPS、延迟分位、错误率 |
| 输出 | sla_passed | 事件 payload | 布尔值，是否满足 SLA |

#### 3.7.5 完成事件

发布 `nexus.delivery.loadtest.completed`，payload 包含 `sla_passed, loadtest_report_url`，触发回归阶段。压测失败默认不阻断交付（产出报告供决策），可配置为阻断。

### 3.8 回归阶段（Regress）

#### 3.8.1 承载项目

MAOP（regression 模块）

#### 3.8.2 触发方式

订阅 `nexus.delivery.loadtest.completed` 事件自动触发。

#### 3.8.3 具体实现

1. MAOP 通过 regression 模块（`core/regression.py`）执行回归测试
2. regression 模块支持 persona simulation（角色模拟），模拟用户操作验证业务流程
3. 回归测试通过 MAOP Subagent 并行执行多个 persona 场景
4. 回归报告通过 MAOP `/api/report` 端点查询

```bash
# 命令示例：MAOP触发回归测试
maop run --task "执行回归测试" --agent regression \
  --persona "user,admin,merchant" \
  --pipeline-id ${PIPELINE_ID}
```

#### 3.8.4 输入/输出产物

表：回归阶段产物说明表

| 类型 | 名称 | 存储位置 | 说明 |
|------|------|---------|------|
| 输入 | 部署后服务 | K8s 集群 | 来自部署阶段 |
| 输入 | persona 定义 | MAOP 配置 | 用户角色定义 |
| 输出 | 回归报告 | MAOP 报告 | 各 persona 通过情况 |
| 输出 | 交付总结 | MAOP 报告 | 全流水线总结 |

#### 3.8.5 完成事件

发布 `nexus.delivery.regress.completed`，payload 包含 `regress_passed, delivery_summary_url`，通知 Interaction 工作台展示交付结果。

### 3.9 各阶段汇总

表：8 阶段实现方式对照表

| 阶段 | 承载项目 | 触发 | 实现方式 | 输入产物 | 输出产物 | 完成事件 |
|------|---------|------|---------|---------|---------|---------|
| 需求 | Interaction + MAOP | 手动/Webhook | Interaction 工作台提交 → MAOP plan 拆解 DAG | 需求描述 | DAG + pipeline_id | nexus.delivery.require.completed |
| 编码 | MAOP | 事件 | MAOP 编排 coder agent + worktree 隔离 | DAG + 任务列表 | 代码变更 + commit_id | nexus.delivery.code.completed |
| 审查 | MAOP | 事件 | MAOP 编排 reviewer + code-review subagent | 代码变更 | 审查报告 + review_passed | nexus.delivery.review.completed |
| 构建 | OpsMesh | 事件 | OpsMesh 下发 shell 任务到构建 agent | commit_id | 镜像/制品 + build_log | nexus.delivery.build.completed |
| 测试 | OpsMesh + DataEngineBDP | 事件 | OpsMesh 执行测试 + DataEngineBDP 提供数据/环境 | 镜像 + 测试数据 | 测试报告 + test_passed | nexus.delivery.test.completed |
| 部署 | OpsMesh + DataEngineBDP | 事件 | OpsMesh Helm 部署 + DataEngineBDP SKE 底座 | 镜像 + Helm Chart | Helm release + 服务端点 | nexus.delivery.deploy.completed |
| 压测 | k6 + OpsMesh | 事件 | OpsMesh 下发 k6 任务执行 JS 脚本 | 服务端点 + k6 脚本 | 压测报告 + sla_passed | nexus.delivery.loadtest.completed |
| 回归 | MAOP | 事件 | MAOP regression 模块 + persona simulation | 部署后服务 + persona | 回归报告 + 交付总结 | nexus.delivery.regress.completed |

---

## 第4章 DAG 编排定义

### 4.1 agents.yaml 配置

MAOP 通过 `config/agents.yaml` 声明式定义各阶段 agent。以下为交付流水线所需 agent 配置示例：

```yaml
# 配置示例：agents.yaml
agents:
  # 需求拆解 agent（MAOP 内置 plan 工具）
  requirement-planner:
    capabilities: [planning, orchestrate, verify, memory]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent planner
    timeout_s: 600
    subagents:
      self:
        capabilities: [planning, memory]
        cli_args: -m maop.cli run --task "{task}" --depth {depth}

  # 编码 agent
  coder:
    capabilities: [codegen, chat, search, memory, mcp, vision]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent coder
    timeout_s: 3600
    subagents:
      self:
        capabilities: [codegen, memory]
        cli_args: -m maop.cli run --task "{task}" --depth {depth}
      architect:
        capabilities: [codegen, planning, review]
        cli_args: -m maop.cli run --task "{task}" --agent architect

  # 代码审查 agent
  reviewer:
    capabilities: [review, explain, search, memory]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent reviewer
    timeout_s: 1800
    subagents:
      code-review:
        capabilities: [review, search]
        cli_args: -m maop.cli run --task "{task}" --agent code-review
      security-review:
        capabilities: [review, search]
        cli_args: -m maop.cli run --task "{task}" --agent security-review

  # 构建 agent（OpsMesh 任务执行）
  builder:
    capabilities: [pipeline, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent builder
    timeout_s: 1800
    mcp:
      - opsmesh-task-sender  # 通过 MCP 调用 OpsMesh 下发任务

  # 测试 agent
  tester:
    capabilities: [pipeline, verify, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent tester
    timeout_s: 3600
    mcp:
      - opsmesh-task-sender
      - dataengine-sql-gateway

  # 部署 agent
  deployer:
    capabilities: [pipeline, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent deployer
    timeout_s: 1800
    mcp:
      - opsmesh-deploy-center
      - dataengine-infra-orchestrator

  # 压测 agent
  loadtester:
    capabilities: [pipeline, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent loadtester
    timeout_s: 3600
    mcp:
      - opsmesh-task-sender
      - k6-runner

  # 回归 agent
  regression:
    capabilities: [verify, memory, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent regression
    timeout_s: 3600
    subagents:
      persona-user:
        capabilities: [verify, memory]
        cli_args: -m maop.cli run --task "{task}" --persona user
      persona-admin:
        capabilities: [verify, memory]
        cli_args: -m maop.cli run --task "{task}" --persona admin
      persona-merchant:
        capabilities: [verify, memory]
        cli_args: -m maop.cli run --task "{task}" --persona merchant
```

### 4.2 DAG 工作流定义

MAOP 通过 DAG workflow 定义整条交付流水线。以下为 DAG 定义示例，包含节点、依赖、事件触发、条件分支、并行编排：

```yaml
# 配置示例：DAG编排定义
workflow:
  name: nexus-delivery-pipeline
  version: "1.0.0"
  description: "Nexus 交付流水线：需求→编码→审查→构建→测试→部署→压测→回归"

  # 全局变量
  variables:
    pipeline_id: "${PIPELINE_ID}"
    tenant_id: "${TENANT_ID}"
    git_repo: "https://github.com/nexus/app"

  # 事件触发
  triggers:
    - type: manual
      description: "Interaction 工作台手动触发"
    - type: webhook
      endpoint: "/api/control/run"
      description: "外部 Webhook 触发"
    - type: event
      topic: "nexus.delivery.external.trigger"
      description: "Kafka 事件触发"

  # DAG 节点
  nodes:
    # 阶段 1：需求拆解
    - id: require
      agent: requirement-planner
      stage: require
      timeout_s: 600
      inputs:
        requirement: "${REQUIREMENT_TEXT}"
      outputs:
        - dag_id
        - tasks
      on_success:
        publish: nexus.delivery.require.completed
        next: code

    # 阶段 2：编码
    - id: code
      agent: coder
      stage: code
      depends_on: [require]
      timeout_s: 3600
      inputs:
        dag_id: "${require.dag_id}"
        tasks: "${require.tasks}"
      outputs:
        - commit_id
        - branch
        - files
      on_success:
        publish: nexus.delivery.code.completed
        next: review

    # 阶段 3：代码审查
    - id: review
      agent: reviewer
      stage: review
      depends_on: [code]
      timeout_s: 1800
      inputs:
        commit_id: "${code.commit_id}"
        branch: "${code.branch}"
      outputs:
        - review_passed
        - review_report_url
      on_success:
        publish: nexus.delivery.review.completed
        # 条件分支：审查通过进入构建，否则回退编码
        next:
          - condition: "${review_passed == true}"
            node: build
          - condition: "${review_passed == false}"
            node: code
            message: "审查未通过，带审查意见回退编码"

    # 阶段 4：构建（多语言并行）
    - id: build
      agent: builder
      stage: build
      depends_on: [review]
      timeout_s: 1800
      parallel:
        - id: build-java
          command: "gradle build"
          env: { BUILD_LANG: java, JDK_VERSION: "17" }
        - id: build-go
          command: "go build -o binary ./cmd/app"
          env: { BUILD_LANG: go }
        - id: build-python
          command: "pip wheel ."
          env: { BUILD_LANG: python }
        - id: build-node
          command: "npm run build"
          env: { BUILD_LANG: node }
      inputs:
        commit_id: "${code.commit_id}"
      outputs:
        - artifact_url
        - image_tag
        - build_log_url
      on_success:
        publish: nexus.delivery.build.completed
        next: test
      on_failure:
        retry: 2
        backoff: exponential
        dead_letter: true

    # 阶段 5：测试（单元测试与集成测试并行）
    - id: test
      agent: tester
      stage: test
      depends_on: [build]
      timeout_s: 3600
      parallel:
        - id: test-unit
          command: "pytest tests/unit"
          opsmesh_task: true
        - id: test-integration
          command: "pytest tests/integration"
          opsmesh_task: true
          depends_on: [test-unit]  # 集成测试依赖单元测试通过
        - id: test-e2e
          command: "playwright test"
          opsmesh_task: true
          depends_on: [test-integration]
      inputs:
        image_tag: "${build.image_tag}"
        test_data_source: "dataengine-sql-gateway"
        test_env: "dataengine-ske"
      outputs:
        - test_passed
        - test_report_url
        - coverage
      on_success:
        publish: nexus.delivery.test.completed
        next:
          - condition: "${test_passed == true}"
            node: deploy
          - condition: "${test_passed == false}"
            node: review
            message: "测试未通过，通知审查 agent 修复"
      on_failure:
        retry: 1
        dead_letter: true

    # 阶段 6：部署
    - id: deploy
      agent: deployer
      stage: deploy
      depends_on: [test]
      timeout_s: 1800
      strategy: blue-green
      inputs:
        image_tag: "${build.image_tag}"
        chart: "nexus-app"
        namespace: "nexus-prod"
      outputs:
        - release_name
        - service_endpoints
      on_success:
        publish: nexus.delivery.deploy.completed
        next: loadtest
      on_failure:
        rollback: helm-rollback
        retry: 1

    # 阶段 7：压测
    - id: loadtest
      agent: loadtester
      stage: loadtest
      depends_on: [deploy]
      timeout_s: 3600
      inputs:
        service_endpoints: "${deploy.service_endpoints}"
        k6_script: "/scripts/loadtest.js"
      outputs:
        - sla_passed
        - loadtest_report_url
      on_success:
        publish: nexus.delivery.loadtest.completed
        next: regress
      on_failure:
        # 压测失败默认不阻断，仅产出报告
        next: regress
        alert: true

    # 阶段 8：回归
    - id: regress
      agent: regression
      stage: regress
      depends_on: [loadtest]
      timeout_s: 3600
      parallel:
        - id: regress-user
          persona: user
        - id: regress-admin
          persona: admin
        - id: regress-merchant
          persona: merchant
      inputs:
        service_endpoints: "${deploy.service_endpoints}"
      outputs:
        - regress_passed
        - delivery_summary_url
      on_success:
        publish: nexus.delivery.regress.completed
        next: complete
      on_failure:
        alert: true
        next: complete

    # 终态
    - id: complete
      type: sink
      depends_on: [regress]
      action: notify
      notify:
        - interaction-workbench
        - feishu-webhook
```

### 4.3 条件分支

表：DAG 条件分支对照表

| 分支节点 | 条件 | 跳转目标 | 说明 |
|---------|------|---------|------|
| review.on_success | review_passed == true | build | 审查通过进入构建 |
| review.on_success | review_passed == false | code | 审查未通过回退编码，带审查意见 |
| test.on_success | test_passed == true | deploy | 测试通过进入部署 |
| test.on_success | test_passed == false | review | 测试未通过通知审查 agent 修复 |
| loadtest.on_failure | 默认 | regress | 压测失败不阻断，继续回归 |
| loadtest.on_failure | 配置阻断=true | （终止） | 可配为阻断交付 |

### 4.4 并行编排点

表：DAG 并行编排点对照表

| 并行点 | 并行节点 | 说明 |
|--------|---------|------|
| build.parallel | build-java / build-go / build-python / build-node | 多语言构建并行，互不依赖 |
| test.parallel | test-unit / test-integration / test-e2e | 单元测试先行，集成测试依赖单元测试通过，E2E 依赖集成测试 |
| regress.parallel | regress-user / regress-admin / regress-merchant | 多 persona 回归并行，互不依赖 |

### 4.5 DAG 执行控制

表：DAG 执行控制参数说明表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| timeout_s | 各阶段独立配置 | 节点超时阈值，超时触发失败处理 |
| retry | 0-2 | 失败重试次数 |
| backoff | exponential | 退避算法（exponential/linear/fixed） |
| dead_letter | true/false | 失败后是否进入死信队列 |
| rollback | helm-rollback/ske-rollback | 失败后回滚策略 |
| parallel | 节点列表 | 并行执行的子节点 |
| condition | 表达式 | 条件分支判断 |

---

## 第5章 k6 压测脚本规范

### 5.1 脚本规范

#### 5.1.1 语言与命名

- 脚本语言：JavaScript（k6 原生支持）
- 代码命名：camelCase（如 paymentCreateBody, bridgeLockBody, webhookConfirmBody）
- 自定义指标命名：snake_case（如 biz_success_rate, payment_latency_p99）
- 后端技术栈：Java（Spring Boot, Tomcat, HikariCP, JVM, Redis）
- 数据库：SQLite 模拟数据库环境进行测试（用户偏好）

#### 5.1.2 压测目标

表：k6 压测目标 API 对照表

| 目标服务 | 项目 | 端点 | 端口 | 压测场景 |
|---------|------|------|------|---------|
| NexusChain 支付 API | NexusChain | /api/v1/payments、/api/v1/orders | 8080 | 支付创建、订单查询、Webhook 确认 |
| NexusChain 跨链桥 | NexusChain | /bridge/* | 8084 | 桥锁定、桥解锁 |
| DataEngineBDP SQL 网关 | DataEngineBDP | /api/v1/query | 8081 | SQL 查询、联邦查询 |
| DataEngineBDP 规则引擎 | DataEngineBDP | /api/v1/execute | 8083 | 规则评估 |
| OpsMesh 任务 API | OpsMesh | /api/v1/tasks | 8080 | 任务下发、结果查询 |
| OpsMesh 部署 API | OpsMesh | /api/v1/deploys | 8080 | 部署查询 |

### 5.2 k6 脚本模板

以下为对 NexusChain 支付 API 压测的 k6 脚本模板示例：

```javascript
// 代码示例：k6压测脚本（JavaScript）
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Counter, Gauge, Histogram, Rate, Trend } from 'k6/metrics';

// ============ 自定义指标（snake_case） ============
const bizSuccessRate = new Rate('biz_success_rate');
const paymentLatencyP99 = new Trend('payment_latency_p99', true);
const orderQueryLatency = new Trend('order_query_latency', true);
const webhookConfirmCount = new Counter('webhook_confirm_count');
const bridgeLockErrors = new Counter('bridge_lock_errors');
const activeUsersGauge = new Gauge('active_users_gauge');
const sqlQueryDuration = new Histogram('sql_query_duration', {
  buckets: [10, 50, 100, 200, 500, 1000, 2000, 5000],
});

// ============ 配置 ============
const baseUrl = __ENV.TARGET_BASE_URL || 'http://nexus-app-prod:8080';
const tenantId = __ENV.TENANT_ID || 'tenant-test';
const apiKey = __ENV.API_KEY || 'test-api-key';

// 请求头（HMAC-SHA256 + 时间戳防重放，与 NexusChain 鉴权对齐）
function buildHeaders(extra) {
  const timestamp = Date.now();
  const nonce = Math.random().toString(36).substring(2);
  return Object.assign({
    'Content-Type': 'application/json',
    'X-Tenant-Api-Key': apiKey,
    'X-Timestamp': timestamp.toString(),
    'X-Nonce': nonce,
    'X-Signature': hmacSha256(`${timestamp}${nonce}`, apiKey),
  }, extra || {});
}

// ============ 场景定义 ============
export const options = {
  scenarios: {
    // 冒烟测试：1 个 VU 持续 30s
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '30s',
      tags: { scenario: 'smoke' },
    },
    // 负载测试：阶梯加压
    load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 20 },   // 30s 内加到 20 VU
        { duration: '1m', target: 50 },    // 1m 内加到 50 VU
        { duration: '2m', target: 100 },   // 2m 内加到 100 VU
        { duration: '1m', target: 50 },    // 1m 内降到 50 VU
        { duration: '30s', target: 0 },    // 30s 内降到 0
      ],
      tags: { scenario: 'load' },
    },
    // 压力测试：突破容量
    stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 200 },
        { duration: '5m', target: 500 },
        { duration: '2m', target: 1000 },
        { duration: '1m', target: 0 },
      ],
      tags: { scenario: 'stress' },
    },
  },
  // 阈值
  thresholds: {
    http_req_failed: ['rate<0.01'],           // 错误率 < 1%
    http_req_duration: ['p(95)<500', 'p(99)<1000'],  // P95 < 500ms, P99 < 1s
    biz_success_rate: ['rate>0.99'],          // 业务成功率 > 99%
    payment_latency_p99: ['p(99)<800'],       // 支付 P99 < 800ms
    order_query_latency: ['p(95)<200'],       // 订单查询 P95 < 200ms
  },
  // 超时
  httpDebug: __ENV.HTTP_DEBUG || '',
};

// ============ HMAC-SHA256 签名 ============
function hmacSha256(message, key) {
  // k6 内置 crypto 模块
  const crypto = require('k6/crypto');
  return crypto.hmac('sha256', key, message, 'hex');
}

// ============ 请求体构造（camelCase） ============
function paymentCreateBody(merchantId, amount, currency) {
  return JSON.stringify({
    merchantId: merchantId,
    amount: amount,
    currency: currency,
    channel: 'nexus-chain',
    timestamp: Date.now(),
    nonce: Math.random().toString(36).substring(2),
  });
}

function bridgeLockBody(asset, amount, sourceChain, targetChain) {
  return JSON.stringify({
    asset: asset,
    amount: amount,
    sourceChain: sourceChain,
    targetChain: targetChain,
    lockId: `lock-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`,
  });
}

function webhookConfirmBody(orderId, status, signature) {
  return JSON.stringify({
    orderId: orderId,
    status: status,
    signature: signature,
    confirmTime: Date.now(),
  });
}

function sqlQueryBody(sql, adapter) {
  return JSON.stringify({
    sql: sql,
    adapter: adapter || 'sqlite',
    timeout: 5000,
  });
}

// ============ 主测试函数 ============
export default function () {
  activeUsersGauge.add(1);
  const merchantId = `m_${__VU}_${__ITER}`;

  group('payment-flow', () => {
    // 1. 创建支付订单
    const createResp = http.post(
      `${baseUrl}/api/v1/payments`,
      paymentCreateBody(merchantId, 100.00, 'CNY'),
      { headers: buildHeaders(), tags: { api: 'payment_create' } }
    );

    const createOk = check(createResp, {
      'payment create status 200': (r) => r.status === 200,
      'payment create has orderId': (r) => r.json('orderId') !== null,
      'payment create latency < 500ms': (r) => r.timings.duration < 500,
    });
    bizSuccessRate.add(createOk);
    paymentLatencyP99.add(createResp.timings.duration);

    if (!createOk) {
      bridgeLockErrors.add(1);
      return;
    }

    const orderId = createResp.json('orderId');

    // 2. 查询订单
    sleep(0.1);  // 思考时间
    const queryResp = http.get(
      `${baseUrl}/api/v1/orders/${orderId}`,
      { headers: buildHeaders(), tags: { api: 'order_query' } }
    );

    check(queryResp, {
      'order query status 200': (r) => r.status === 200,
      'order query id matches': (r) => r.json('orderId') === orderId,
    });
    orderQueryLatency.add(queryResp.timings.duration);

    // 3. Webhook 确认
    sleep(0.2);
    const webhookResp = http.post(
      `${baseUrl}/api/v1/webhooks/confirm`,
      webhookConfirmBody(orderId, 'SUCCESS', hmacSha256(orderId, apiKey)),
      { headers: buildHeaders(), tags: { api: 'webhook_confirm' } }
    );

    if (check(webhookResp, { 'webhook confirm 200': (r) => r.status === 200 })) {
      webhookConfirmCount.add(1);
    }
  });

  // 跨链桥压测
  group('bridge-flow', () => {
    const lockResp = http.post(
      `${baseUrl}/bridge/lock`,
      bridgeLockBody('USDT', 50.00, 'ethereum', 'polygon'),
      { headers: buildHeaders(), tags: { api: 'bridge_lock' } }
    );

    check(lockResp, {
      'bridge lock status 200': (r) => r.status === 200,
      'bridge lock has lockId': (r) => r.json('lockId') !== null,
    });
  });

  // SQL 网关压测（DataEngineBDP）
  group('sql-gateway', () => {
    const sqlResp = http.post(
      `${baseUrl}/api/v1/query`,
      sqlQueryBody('SELECT COUNT(*) FROM orders WHERE status = "PAID"', 'sqlite'),
      { headers: buildHeaders(), tags: { api: 'sql_query' } }
    );

    check(sqlResp, {
      'sql query status 200': (r) => r.status === 200,
      'sql query has result': (r) => r.json('data') !== null,
    });
    sqlQueryDuration.add(sqlResp.timings.duration);
  });

  activeUsersGauge.add(-1);
  sleep(0.5);  // 思考时间
}

// ============ 阶段钩子 ============
export function setup() {
  console.log(`k6 压测启动，目标：${baseUrl}`);
  return { startTime: Date.now() };
}

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`k6 压测结束，总时长：${duration}s`);
}
```

### 5.3 压测场景与阈值

表：k6 压测场景与阈值对照表

| 场景 | 执行器 | VU 范围 | 持续时间 | 阈值 | 用途 |
|------|--------|---------|---------|------|------|
| smoke | constant-vus | 1 | 30s | http_req_failed rate<0.01 | 冒烟验证 |
| load | ramping-vus | 0→100→0 | 5m | p(95)<500ms, p(99)<1000ms | 正常负载 |
| stress | ramping-vus | 0→1000 | 10m | biz_success_rate>0.99 | 容量突破 |

### 5.4 压测报告格式

表：k6 压测报告字段说明表

| 指标 | 单位 | 说明 |
|------|------|------|
| RPS | req/s | 每秒请求数 |
| http_req_duration | ms | HTTP 请求延迟（含 p50/p90/p95/p99） |
| http_req_failed | rate | HTTP 错误率 |
| biz_success_rate | rate | 业务成功率（自定义） |
| payment_latency_p99 | ms | 支付延迟 P99（自定义） |
| order_query_latency | ms | 订单查询延迟（自定义） |
| webhook_confirm_count | count | Webhook 确认次数（自定义） |
| bridge_lock_errors | count | 桥锁定错误次数（自定义） |
| active_users_gauge | count | 活跃用户数（自定义） |
| vus | count | 虚拟用户数 |
| vus_max | count | 最大虚拟用户数 |
| iterations | count | 迭代次数 |
| data_received | KB | 接收数据量 |
| data_sent | KB | 发送数据量 |

### 5.5 资源占用监控

压测期间通过 Prometheus 采集后端资源占用：

表：后端资源监控指标对照表

| 指标 | 来源 | 说明 |
|------|------|------|
| jvm_memory_heap_used | JVM Micrometer | JVM 堆内存使用 |
| jvm_gc_pause_seconds | JVM Micrometer | GC 暂停时间 |
| tomcat_threads_busy | Tomcat Micrometer | Tomcat 忙线程数 |
| hikaricp_connections_active | HikariCP Micrometer | HikariCP 活跃连接数 |
| redis_commands_latency | Redis exporter | Redis 命令延迟 |
| process_cpu_usage | JVM Micrometer | 进程 CPU 使用率 |

---

## 第6章 失败回滚/重试/死信策略

### 6.1 失败检测

表：各阶段失败检测方式对照表

| 阶段 | 失败检测方式 | 检测实现 |
|------|------------|---------|
| 需求 | plan 工具返回错误 | MAOP maop_plan.py 异常 |
| 编码 | coder agent 退出码非 0 | MAOP agent executor exitCode |
| 审查 | reviewer agent 标记 review_passed=false | MAOP subagent transcript |
| 构建 | OpsMesh 任务 status=failed | OpsMesh 任务结果 exitCode 非 0 |
| 测试 | 测试用例失败率超阈值 | OpsMesh 任务结果 + threshold |
| 部署 | Helm 部署失败 / 健康检查不通过 | OpsMesh 部署中心 + /healthz、/readyz |
| 压测 | k6 threshold 不满足 | k6 thresholds 评估 |
| 回归 | persona 验证失败 | MAOP regression 模块结果 |

### 6.2 重试策略

表：各阶段重试策略对照表

| 阶段 | 最大重试 | 退避算法 | 可重试错误 | 不可重试错误 |
|------|---------|---------|-----------|-------------|
| 需求 | 0 | — | — | 需求不明确、语法错误 |
| 编码 | 1 | exponential（初始 30s） | LLM 超时、网络抖动 | 代码语法错误、依赖缺失 |
| 审查 | 0 | — | — | 审查失败需人工介入 |
| 构建 | 2 | exponential（初始 60s） | 网络超时、镜像仓库不可达 | 编译错误、依赖冲突 |
| 测试 | 1 | fixed（30s） | 测试环境未就绪、数据未同步 | 断言失败、用例错误 |
| 部署 | 1 | fixed（60s） | K8s API 超时、镜像拉取失败 | Helm chart 错误、配置错误 |
| 压测 | 0 | — | — | 压测失败不重试，仅产出报告 |
| 回归 | 1 | exponential（初始 30s） | 服务未就绪 | 业务逻辑错误 |

退避算法说明：

- exponential：`wait = base * 2^attempt`（如 base=60s，第 1 次重试等 60s，第 2 次等 120s）
- linear：`wait = base * attempt`
- fixed：`wait = base`

### 6.3 回滚策略

表：各阶段回滚策略对照表

| 阶段 | 回滚策略 | 回滚实现 | 说明 |
|------|---------|---------|------|
| 需求 | 无需回滚 | — | 仅生成 DAG，无副作用 |
| 编码 | Git worktree abandon | MAOP worktree 模块 `worktree abandon` | 丢弃编码分支 |
| 审查 | 无需回滚 | — | 仅产出审查报告 |
| 构建 | 无需回滚 | — | 不产生镜像，直接告警 |
| 测试 | 阻止部署 | 事件 payload test_passed=false | 通知审查 agent 修复 |
| 部署 | Helm rollback / SKE 回滚 / 蓝绿回退 | OpsMesh `/api/v1/deploys` Rollback + DataEngineBDP SKE | 回滚到上一版本 |
| 压测 | 不阻断交付 | — | 产出报告供决策（可配为阻断） |
| 回归 | 不阻断交付 | — | 产出报告供决策 |

部署阶段回滚详细策略：

```bash
# 命令示例：OpsMesh Helm rollback
curl -X POST http://opsmesh:8080/api/v1/deploys/${DEPLOY_ID}/rollback \
  -H "X-Tenant-ID: ${TENANT_ID}" \
  -H "Authorization: Bearer ${OPSMESH_JWT}" \
  -d '{
    "strategy": "helm-rollback",
    "target_revision": "${PREVIOUS_REVISION}"
  }'
```

部署回滚策略选择：

- Helm rollback：OpsMesh 部署中心原生支持，回滚到上一 Helm release revision
- SKE 回滚：DataEngineBDP SKE 回滚到上一版本镜像
- 蓝绿回退：切换 Service Selector 指向旧版本 Deployment

### 6.4 死信处理

OpsMesh 任务失败重试耗尽后进入死信队列（dead_letter）：

表：死信处理流程对照表

| 步骤 | 实现 | 说明 |
|------|------|------|
| 1. 进入死信 | OpsMesh `store.go` max_retries 耗尽 | 任务 status=dead_letter |
| 2. 告警 | OpsMesh Alert Webhook | 通知飞书/钉钉/Slack/企业微信/SMTP |
| 3. 人工介入入口 | OpsMesh `/api/v1/tasks/{id}/result` + Interaction 工作台 | 人工查看死信任务详情 |
| 4. 人工处理 | 重试 / 跳过 / 终止 | 人工决策后通过 API 操作 |
| 5. 审计留痕 | OpsMesh Audit Log | 100% 留痕，可查 |

OpsMesh 告警 Webhook 配置：

```yaml
# 配置示例：OpsMesh告警Webhook
alert:
  webhook:
    type: feishu  # feishu/dingtalk/slack/wecom/smtp
    url: "https://open.feishu.cn/open-apis/im/v1/messages"
    secret: "${FEISHU_WEBHOOK_SECRET}"
  rules:
    - name: dead-letter-alert
      condition: "task.status == 'dead_letter'"
      level: critical
      message: "任务 ${task.id} 进入死信队列，请人工介入"
    - name: build-failure-alert
      condition: "stage == 'build' && task.status == 'failed'"
      level: warn
      message: "构建失败：${task.error}"
```

MAOP EventBus 死信处理：

- MAOP EventBus 内置 dead-letter（1000 条上限）
- DeadLetterEntry 包含：event_id, topic, handler_name, error, attempts, timestamp
- 通过 `/api/failures` 端点查询死信

### 6.5 超时策略

表：各阶段超时阈值对照表

| 阶段 | 超时阈值 | 超时处理 | 说明 |
|------|---------|---------|------|
| 需求 | 600s（10min） | 标记失败，告警 | plan 工具拆解超时 |
| 编码 | 3600s（1h） | 标记失败，worktree abandon | coder agent 执行超时 |
| 审查 | 1800s（30min） | 标记失败，告警 | reviewer agent 执行超时 |
| 构建 | 1800s（30min） | 标记失败，不产生镜像 | 构建任务超时 |
| 测试 | 3600s（1h） | 标记失败，阻止部署 | 测试任务超时 |
| 部署 | 1800s（30min） | 标记失败，触发回滚 | 部署任务超时 |
| 压测 | 3600s（1h） | 标记失败，不阻断 | k6 执行超时 |
| 回归 | 3600s（1h） | 标记失败，不阻断 | regression 执行超时 |

### 6.6 失败处理汇总

表：各阶段失败处理策略对照表

| 阶段 | 失败检测 | 重试策略 | 回滚策略 | 死信处理 | 超时 |
|------|---------|---------|---------|---------|------|
| 需求 | plan 异常 | 不重试 | 无需回滚 | 不进死信，直接告警 | 600s |
| 编码 | exitCode 非 0 | 1 次，exponential 30s | worktree abandon | 进 MAOP dead-letter | 3600s |
| 审查 | review_passed=false | 不重试 | 无需回滚 | 进 MAOP dead-letter | 1800s |
| 构建 | OpsMesh task failed | 2 次，exponential 60s | 不产生镜像，直接告警 | 进 OpsMesh dead_letter | 1800s |
| 测试 | 用例失败率超阈值 | 1 次，fixed 30s | 阻止部署，通知审查 | 进 OpsMesh dead_letter | 3600s |
| 部署 | Helm 失败/健康检查不通过 | 1 次，fixed 60s | Helm rollback / SKE 回滚 / 蓝绿回退 | 进 OpsMesh dead_letter | 1800s |
| 压测 | k6 threshold 不满足 | 不重试 | 不阻断，产出报告 | 不进死信，仅告警 | 3600s |
| 回归 | persona 验证失败 | 1 次，exponential 30s | 不阻断，产出报告 | 进 MAOP dead-letter | 3600s |

---

## 第7章 触发与监控入口

### 7.1 触发方式

表：交付流水线触发方式对照表

| 触发方式 | 入口 | 实现 | 说明 |
|---------|------|------|------|
| 手动触发 | Interaction 工作台 | 用户在工作台点击"启动交付" → fetch MAOP /api/control/run | 最常用，用户主动发起 |
| Webhook 触发 | MAOP /api/control/run | 外部系统（如 GitHub push、Jira issue 创建）调用 Webhook | 自动触发，如代码合并触发 |
| 定时触发 | OpsMesh cron | OpsMesh 5 字段 cron 调度（`internal/cron/`）派生 pending 任务 | 定时回归，如每日凌晨全量回归 |
| 事件触发 | Kafka | 订阅 `nexus.delivery.external.trigger` 事件 | 跨流水线触发，如上游流水线完成触发下游 |

#### 7.1.1 手动触发

用户在 Interaction 工作台通过 `plan` 工具提交需求，Interaction 通过 fetch 调用 MAOP：

```bash
# 命令示例：Interaction工作台手动触发交付流水线
curl -X POST http://maop:9079/api/control/run \
  -H "Authorization: Bearer ${MAOP_JWT}" \
  -d '{
    "task": "启动 Nexus 交付流水线",
    "requirement": "需求描述",
    "pipeline": "nexus-delivery"
  }'
```

#### 7.1.2 Webhook 触发

外部系统通过 Webhook 触发：

```bash
# 命令示例：外部Webhook触发交付流水线
curl -X POST http://maop:9079/api/control/run \
  -H "X-Webhook-Signature: ${HMAC_SIGNATURE}" \
  -d '{
    "source": "github",
    "event": "push",
    "ref": "refs/heads/main",
    "commit": "abc123"
  }'
```

#### 7.1.3 定时触发

OpsMesh cron 调度：

```bash
# 命令示例：OpsMesh定时触发全量回归
curl -X POST http://opsmesh:8080/api/v1/tasks \
  -H "X-Tenant-ID: ${TENANT_ID}" \
  -d '{
    "type": "shell",
    "command": "trigger-pipeline.sh --type regression",
    "schedule": "0 2 * * *",
    "segment": "cron-segment"
  }'
```

### 7.2 监控视图

表：交付流水线监控视图对照表

| 视图 | 提供方 | 入口 | 展示内容 |
|------|--------|------|---------|
| DAG 执行进度 | Interaction 工作台 | http://127.0.0.1:8123 | DAG 节点状态、当前阶段、进度条 |
| 实时流 | MAOP dashboard | http://maop:9079 | WebSocket /ws 推送 snapshot（15s 间隔） |
| 任务面板 | OpsMesh B/S 仪表盘 | http://opsmesh:8080 | 设备/任务双表 + 详情抽屉 + 5s 轮询 |
| SSE 事件流 | OpsMesh | /api/v1/events/stream | task_status / alert_new / device_online / device_offline |
| 指标 | Prometheus | http://opsmesh:9091/metrics | 任务计数、延迟直方图、runtime |
| 日志 | OpsMesh /api/v1/logs | Loki / ES | 结构化日志检索 |

#### 7.2.1 Interaction 工作台监控

Interaction 工作台通过 fetch 调用 MAOP `/api/live`、`/api/snapshot`、`/api/timeseries` 端点获取实时数据，展示：

- DAG 节点状态（pending / running / done / failed）
- 当前阶段进度条
- 各阶段产物链接
- 失败告警

#### 7.2.2 MAOP dashboard 实时流

MAOP dashboard 通过 WebSocket `/ws` 端点推送 snapshot（15s 间隔，5s TTL 缓存），展示：

- 编排循环状态（Plan-Execute-Verify）
- Agent 执行情况
- 事件总线消息
- 死信队列

#### 7.2.3 OpsMesh 任务面板

OpsMesh B/S 仪表盘（端口 8080）展示：

- 设备/任务双表
- 详情抽屉
- 5s 轮询刷新
- SSE 实时推送 task_status / alert_new

### 7.3 通知

表：交付流水线通知方式对照表

| 通知场景 | 提供方 | 通道 | 说明 |
|---------|--------|------|------|
| 阶段成功 | MAOP EventBus → OpsMesh Alert | 飞书/钉钉/Slack/企业微信/SMTP | 各阶段完成通知 |
| 阶段失败 | OpsMesh Alert Webhook | 飞书/钉钉/Slack/企业微信/SMTP | 失败告警 |
| 死信告警 | OpsMesh Alert Webhook | 飞书/钉钉/Slack/企业微信/SMTP | critical 级别 |
| 交付完成 | MAOP → Interaction 工作台 | 工作台通知 + 飞书/钉钉 | 交付总结 |
| 人工介入 | OpsMesh Alert + Interaction | 飞书/钉钉 + 工作台 | 死信任务人工处理 |

OpsMesh Alert Webhook 支持的通道（`internal/notify/`）：

- generic（通用 Webhook）
- feishu（飞书）
- dingtalk（钉钉）
- slack（Slack）
- wecom（企业微信）
- SMTP（邮件）

---

## 第8章 与文档生成 Workflow 的联动

### 8.1 联动模式

文档生成 Workflow（doc-pipeline）作为交付流水线的产物生成器，在特定阶段被调用。两种调用方式：

表：文档流水线调用方式对照表

| 调用方式 | 编排方 | 实现 | 适用场景 |
|---------|--------|------|---------|
| MAOP 编排 doc-pipeline agent | MAOP | agents.yaml 定义 doc-generator agent，DAG 节点调用 | 文档生成需要多步骤编排 |
| OpsMesh 下发文档生成任务 | OpsMesh | /api/v1/tasks 下发 shell 任务 | 文档生成为单步脚本 |

### 8.2 触发时机

表：文档生成触发时机对照表

| 触发时机 | 触发条件 | 生成文档 | 调用方式 |
|---------|---------|---------|---------|
| 部署成功后 | nexus.delivery.deploy.completed | API 文档、变更日志、部署清单 | MAOP 编排 doc-generator agent |
| 回归完成后 | nexus.delivery.regress.completed | 交付总结报告、验收报告 | MAOP 编排 doc-generator agent |
| 阶段失败后 | nexus.delivery.*.failed | 失败分析报告 | OpsMesh 下发文档生成任务 |
| 手动触发 | 用户在工作台请求 | 指定文档类型 | MAOP 编排 doc-generator agent |

### 8.3 DAG 节点扩展

在主交付 DAG 中扩展文档生成节点：

```yaml
# 配置示例：DAG编排定义（文档生成节点扩展）
workflow:
  name: nexus-delivery-pipeline-with-docs
  nodes:
    # ... 原有 8 阶段节点 ...

    # 部署成功后生成 API 文档
    - id: gen-api-doc
      agent: doc-generator
      stage: post-deploy
      depends_on: [deploy]
      condition: "${deploy.status == 'success'}"
      timeout_s: 600
      inputs:
        service_endpoints: "${deploy.service_endpoints}"
        release_name: "${deploy.release_name}"
      outputs:
        - api_doc_url
        - changelog_url
        - deploy_manifest_url
      on_success:
        publish: nexus.delivery.doc.api.completed

    # 回归完成后生成交付总结
    - id: gen-delivery-summary
      agent: doc-generator
      stage: post-regress
      depends_on: [regress]
      condition: "${regress.status == 'success'}"
      timeout_s: 600
      inputs:
        pipeline_id: "${pipeline_id}"
        regress_report: "${regress.delivery_summary_url}"
      outputs:
        - delivery_summary_url
        - acceptance_report_url
      on_success:
        publish: nexus.delivery.doc.summary.completed
        next: complete

    # 阶段失败后生成失败分析报告
    - id: gen-failure-analysis
      agent: doc-generator
      stage: post-failure
      trigger: nexus.delivery.*.failed
      timeout_s: 300
      inputs:
        failed_stage: "${event.stage}"
        error: "${event.error}"
        trace_id: "${event.trace_id}"
      outputs:
        - failure_analysis_url
      on_success:
        publish: nexus.delivery.doc.failure.completed
```

### 8.4 doc-generator agent 配置

```yaml
# 配置示例：agents.yaml（doc-generator agent）
agents:
  doc-generator:
    capabilities: [codegen, chat, search, memory, mcp]
    driver: cli
    cli_args: -m maop.cli run --task "{task}" --agent doc-generator
    timeout_s: 600
    mcp:
      - doc-pipeline-tool  # 文档生成 MCP 工具
    subagents:
      api-doc-gen:
        capabilities: [codegen, search]
        cli_args: -m maop.cli run --task "{task}" --doc-type api
      changelog-gen:
        capabilities: [codegen, search]
        cli_args: -m maop.cli run --task "{task}" --doc-type changelog
      manifest-gen:
        capabilities: [codegen, search]
        cli_args: -m maop.cli run --task "{task}" --doc-type manifest
```

### 8.5 文档产物存储

表：文档产物存储说明表

| 文档类型 | 存储位置 | 访问方式 | 保留期 |
|---------|---------|---------|--------|
| API 文档 | 对象存储 + MAOP 报告 | URL | 长期 |
| 变更日志 | Git 仓库 + 对象存储 | URL + Git | 长期 |
| 部署清单 | OpsMesh 部署记录 + 对象存储 | URL + API | 90 天 |
| 交付总结 | MAOP 报告 + 对象存储 | URL | 长期 |
| 验收报告 | MAOP 报告 + 对象存储 | URL | 长期 |
| 失败分析 | MAOP 报告 + 对象存储 | URL | 30 天 |

---

## 第9章 实现任务拆分建议

### 9.1 任务拆分原则

- 按阶段拆分：每个交付阶段独立任务
- 按组件拆分：跨项目的集成任务独立
- 依赖明确：任务间依赖关系清晰
- 颗粒适中：单个任务预估工时 1-5 天

### 9.2 任务清单

表：交付流水线实现任务清单

| 任务ID | 任务描述 | 涉及项目 | 依赖 | 预估工时 |
|--------|---------|---------|------|---------|
| T-001 | Interaction 工作台增加"启动交付"入口，调用 MAOP /api/control/run | Interaction, MAOP | 无 | 1 天 |
| T-002 | MAOP agents.yaml 配置 8 阶段 agent（planner/coder/reviewer/builder/tester/deployer/loadtester/regression） | MAOP | 无 | 1 天 |
| T-003 | MAOP DAG workflow 定义（8 阶段 + 条件分支 + 并行编排） | MAOP | T-002 | 2 天 |
| T-004 | MAOP EventBus 配置交付流水线 9 个 topic（nexus.delivery.*） | MAOP | 无 | 1 天 |
| T-005 | MAOP coder agent 接入 Git worktree 隔离 | MAOP | T-002 | 1 天 |
| T-006 | MAOP reviewer agent 接入 code-review subagent | MAOP | T-002 | 1 天 |
| T-007 | MAOP builder agent 通过 MCP 调用 OpsMesh /api/v1/tasks 下发构建任务 | MAOP, OpsMesh | T-002 | 2 天 |
| T-008 | OpsMesh 构建任务模板（Java/Go/Python/Node 多语言） | OpsMesh | 无 | 2 天 |
| T-009 | DataEngineBDP SKE 测试环境准备 API 集成 | DataEngineBDP, MAOP | T-002 | 2 天 |
| T-010 | DataEngineBDP sql-gateway 测试数据提供（SQLite 模拟） | DataEngineBDP | T-009 | 1 天 |
| T-011 | MAOP tester agent 通过 MCP 调用 OpsMesh + DataEngineBDP | MAOP, OpsMesh, DataEngineBDP | T-007, T-010 | 2 天 |
| T-012 | OpsMesh 部署中心集成 Helm 部署（蓝绿/金丝雀/滚动） | OpsMesh | T-008 | 3 天 |
| T-013 | MAOP deployer agent 通过 MCP 调用 OpsMesh 部署中心 + DataEngineBDP SKE | MAOP, OpsMesh, DataEngineBDP | T-012 | 2 天 |
| T-014 | k6 压测脚本编写（NexusChain 支付 API） | k6, NexusChain | 无 | 2 天 |
| T-015 | k6 压测脚本编写（DataEngineBDP SQL 网关） | k6, DataEngineBDP | 无 | 1 天 |
| T-016 | k6 压测脚本编写（OpsMesh 任务 API） | k6, OpsMesh | 无 | 1 天 |
| T-017 | MAOP loadtester agent 通过 MCP 调用 k6 | MAOP, k6 | T-014, T-015, T-016 | 1 天 |
| T-018 | MAOP regression agent 接入 persona simulation | MAOP | T-002 | 2 天 |
| T-019 | OpsMesh 失败重试 + 死信队列配置 | OpsMesh | T-008 | 1 天 |
| T-020 | OpsMesh 部署回滚策略实现（Helm rollback / SKE 回滚 / 蓝绿回退） | OpsMesh, DataEngineBDP | T-012 | 2 天 |
| T-021 | OpsMesh Alert Webhook 配置（飞书/钉钉/Slack/企业微信/SMTP） | OpsMesh | 无 | 1 天 |
| T-022 | MAOP dead-letter 查询端点集成到 Interaction 工作台 | MAOP, Interaction | T-004 | 1 天 |
| T-023 | Interaction 工作台 DAG 执行进度视图 | Interaction, MAOP | T-001 | 2 天 |
| T-024 | MAOP dashboard 实时流集成交付流水线 snapshot | MAOP | T-004 | 1 天 |
| T-025 | OpsMesh cron 定时触发集成 | OpsMesh | T-003 | 1 天 |
| T-026 | 文档生成 Workflow 联动（部署后生成 API 文档/变更日志/部署清单） | MAOP, doc-pipeline | T-013 | 2 天 |
| T-027 | 文档生成 Workflow 联动（回归后生成交付总结/验收报告） | MAOP, doc-pipeline | T-018 | 1 天 |
| T-028 | 文档生成 Workflow 联动（失败后生成失败分析报告） | MAOP, OpsMesh, doc-pipeline | T-019 | 1 天 |
| T-029 | 端到端联调（8 阶段全流程） | 全部 | T-001 ~ T-028 | 3 天 |
| T-030 | 压测验证（对交付流水线本身压测） | k6 | T-029 | 1 天 |

### 9.3 任务依赖关系

图：任务依赖关系示意图

```
T-001 (Interaction入口)
T-002 (agents.yaml) -> T-003 (DAG) -> T-029 (联调)
T-004 (EventBus) -> T-022 (死信查询) -> T-024 (实时流)
T-005 (coder worktree)
T-006 (reviewer subagent)
T-007 (builder MCP) -> T-008 (构建模板) -> T-011 (tester)
T-009 (SKE) -> T-010 (sql-gateway) -> T-011 (tester)
T-012 (Helm部署) -> T-013 (deployer) -> T-026 (API文档)
T-014, T-015, T-016 (k6脚本) -> T-017 (loadtester)
T-018 (regression) -> T-027 (交付总结)
T-019 (重试死信) -> T-028 (失败分析)
T-020 (回滚策略)
T-021 (Alert Webhook)
T-023 (DAG进度视图)
T-025 (cron触发)
T-029 (联调) -> T-030 (压测验证)
```

### 9.4 工时汇总

表：工时汇总表

| 类别 | 任务数 | 预估工时 |
|------|--------|---------|
| 编排配置（MAOP） | 8 | 11 天 |
| 任务执行（OpsMesh） | 5 | 9 天 |
| 数据/环境（DataEngineBDP） | 2 | 3 天 |
| 压测脚本（k6） | 4 | 5 天 |
| 监控/通知 | 5 | 6 天 |
| 文档联动 | 3 | 4 天 |
| 联调/验证 | 2 | 4 天 |
| 入口/视图（Interaction） | 2 | 3 天 |
| **合计** | **30** | **45 天** |

### 9.5 里程碑建议

表：里程碑规划表

| 里程碑 | 包含任务 | 预计完成 | 交付物 |
|--------|---------|---------|--------|
| M1：编排骨架 | T-002, T-003, T-004 | 第 4 天 | DAG 可运行，事件总线就绪 |
| M2：编码/审查 | T-005, T-006, T-001 | 第 7 天 | 需求→编码→审查闭环 |
| M3：构建/测试 | T-007, T-008, T-009, T-010, T-011 | 第 16 天 | 构建/测试阶段打通 |
| M4：部署/压测 | T-012, T-013, T-014, T-015, T-016, T-017 | 第 26 天 | 部署/压测阶段打通 |
| M5：回归/容错 | T-018, T-019, T-020, T-021 | 第 32 天 | 回归/容错策略就绪 |
| M6：监控/文档 | T-022, T-023, T-024, T-025, T-026, T-027, T-028 | 第 41 天 | 监控/文档联动完成 |
| M7：联调/验收 | T-029, T-030 | 第 45 天 | 全流程验收通过 |

---

## 附录

### 附录 A：参考文档

- Nexus 统一编排平台 HLD
- F:\Nexus\Workflow\_盘点_DataEngineBDP_NexusChain.md（接口盘点报告 1）
- F:\Nexus\Workflow\_盘点_MAOP_OpsMesh_Interaction.md（接口盘点报告 2）
- MAOP agents.yaml 配置规范（F:\Nexus\MAOP\config\agents.yaml）
- OpsMesh 任务生命周期文档（F:\Nexus\OpsMesh\internal\agent\agent.go）
- k6 官方文档（https://k6.io/docs/）

### 附录 B：关键端口速查

表：5 项目关键端口速查表

| 项目 | 端口 | 协议 | 用途 |
|------|------|------|------|
| Interaction | 8123 | HTTP（静态） | 工作台本地服务 |
| MAOP | 9079 | HTTP REST + WebSocket | 编排控制面 + dashboard |
| OpsMesh | 8080 | HTTP REST + SSE | 控制面 B/S + 任务 API |
| OpsMesh | 9090 | gRPC | agent 通道 |
| OpsMesh | 9091 | HTTP | Prometheus metrics |
| DataEngineBDP | 8080-8086 | HTTP REST | 各组件 API |
| DataEngineBDP | 18086, 18090, 8094 | HTTP REST | 调度/治理/联邦 |
| NexusChain | 8080 | HTTP REST | nexus-gateway |
| NexusChain | 19585 | HTTP + gRPC | nexus-core |
| NexusChain | 8084 | HTTP | nexus-bridge |
| NexusChain | 50051 | gRPC | MPC 签名 |

### 附录 C：事件 topic 命名规范

表：事件 topic 命名规范表

| 规范项 | 规则 | 示例 |
|--------|------|------|
| 格式 | `nexus.delivery.{stage}.{action}` | nexus.delivery.build.completed |
| stage | require / code / review / build / test / deploy / loadtest / regress | build |
| action | completed / failed / started / cancelled | completed |
| 通配 | `nexus.delivery.*.failed` | 匹配所有阶段失败事件 |
| 信封 | OpsMesh Event SchemaVersion 1.0.0 | 见 2.2.2 节 |

---

> 文档结束。