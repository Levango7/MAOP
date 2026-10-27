# MAOP 多Agent调度分层整理计划

## 目标架构（分层）

```
Layer 1: Ingress          CLI / HTTP API / WebSocket
Layer 2: Orchestration    MaopLoop / Engine
Layer 3: Selection        AgentCatalog → AgentRouter → Dispatcher
Layer 4: Execution        Driver / DistributedScheduler
Layer 5: Supervision      HealthService → FailurePatternDetector → Supervisor
Layer 6: Coordination     A2AGateway ↔ Blackboard ↔ MCP Hub
Layer 7: Observability    Dashboard WS / metrics / tracing
```

## 当前九大问题与整改项

| # | 问题 | 优先级 | 整改 |
|---|------|--------|------|
| 1 | 三套agent注册表（AgentCatalog / AgentLifecycle / RedisAgentRegistry / WorkerRegistry）ID体系不打通 | P0 | 建 AgentIdentity 联邦映射表，统一 `agent_name ↔ card_id ↔ worker_id` |
| 2 | 五套健康检查互不通信 | P0 | 抽取 HealthService，统一探测+写入 AgentCatalog.healthy |
| 3 | 健康状态不下发到调度 | P0 | Dispatcher / DistributedScheduler 发送前检查 AgentCatalog.healthy |
| 4 | Dispatcher 与 DistributedScheduler 职责重叠 | P1 | 界定：单agent同步走 Dispatcher，DAG节点分布式走 DistributedScheduler |
| 5 | Supervisor 继承 FailurePatternDetector，单例混用 | P1 | 组合替代继承，get_supervisor 与 get_failure_detector 分离 |
| 6 | SubAgentManager 双身份（async lifecycle + sync delegation） | P1 | 拆分为两类，各自只管自己列 |
| 7 | 两套A2A + 一个传输服务器 | P1 | A2AGateway 统一门户，旧实现废弃 |
| 8 | 两套MCP Hub | P2 | CoordMCPHubBridge 唯一适配层，业务依赖生产版 core/mcp/mcp_hub.py |
| 9 | BlackboardWSBridge 未接线 | P2 | dashboard lifespan 注册，黑板写入实时推WS |

## 阶段1：身份与健康（P0，立即实施）

### 任务1.1：建立 AgentIdentity 联邦映射表
- 新建 `py/maop/core/agent/identity.py`
- 类 `AgentIdentity`：SQLite 表 `agent_identity`，字段 `agent_name`（主键，对应 AgentCatalog.name）、`card_id`（Redis A2A）、`worker_id`（分布式调度）、`source`（首次注册来源）
- 提供 `register_mapping(agent_name, card_id=None, worker_id=None)` / `get_by_card_id(card_id)` / `get_by_worker_id(worker_id)` / `get_by_agent_name(agent_name)` / `remove_mapping(agent_name)`
- 供 RedisAgentRegistry.register_agent() / WorkerRegistry.register() / AgentCatalog.register() 调用，建立三方联动

### 任务1.2：抽取 HealthService 统一探测
- 新建 `py/maop/core/scheduling/health_service.py`
- 类 `HealthService`：
  - 单一探测循环（替代分散在 AgentRegistry.health_check_all / RedisAgentRegistry._probe_loop / Supervisor.patrol 的重复探测）
  - 探测方式：CLI `--version`（lifecycle agent）、HTTP health_url（A2A agent）、TCP ping（兜底）
  - 结果统一写入 `AgentCatalog.update_health(name, healthy)` + `agent_identity` 关联表
  - 提供 `start()` / `stop()` / `probe_agent(agent_name)` / `get_health(name)` / `list_unhealthy()`
- 现有探测入口保留兼容封装，但内部委托 HealthService，避免破坏现有调用方

### 任务1.3：调度前健康门禁
- `py/maop/delegate/dispatch_core.py` `Dispatcher.dispatch()`：在 driver 执行前，读取 `AgentCatalog.get(agent_name).healthy`，为 `False` 时返回 403/拒绝，走 fallback_agents
- `py/maop/core/scheduling/distributed_scheduler.py`：在 DAG 节点分发到 Redis Worker 前，检查目标 worker 对应的 agent 健康状态， unhealthy 时跳过并重试其他 worker
- 确保 `FailurePatternDetector.get_weight() == 0.0`（drained）时也拒绝分发

### 阶段1验收标准
- 新agent只需在 AgentCatalog 注册一次，HealthService 自动探测，所有调度路径自动感知健康状态
- unhealthy/drained agent 不再收到新任务
- 不破坏现有 CLI / API 入口（maop run / /api/dag/execute 等零改动）