# MAOP Blackboard M3 运维手册

> 覆盖范围：触发规则引擎、辩论引擎、收敛控制器、多租户隔离、事件联邦总线。
> 适用版本：MAOP v5.5.0+

---

## 1. 架构概述

M3 在 Blackboard 上构建了完整的**知识协作闭环**：

```
[EventBus] ──write──▶ [Blackboard] ──state change──▶ [TriggerRuleEngine]
                                                            │ evaluate
                                                            ▼
                                                  [Knowledge Sources]
                                                            │ write back
                                                            ▼
                                                  [Convergence Check]
                                                            │
                                              ┌──converged?──�
                                              │ YES    NO     │
                                              ▼               ▼
                                       [record result]  [cycle again]
```

跨项目维度：

```
[MAOS EventBus] �──bridge──▶ [FederationBus] �──bridge──▶ [MAOP EventBus]
                                                                    │
                                                        ┌───────────┤
                                                        ▼           ▼
                                                  [Blackboard]  [DebateEngine]
```

---

## 2. 健康检查端点

| 端点 | 用途 |
|------|------|
| `GET /metrics/blackboard` | Prometheus 指标快照 |
| `GET /health/blackboard` | 存活/就绪检查 (k8s probe) |

### 指标清单

| 指标 | 类型 | 说明 |
|------|------|------|
| `bb_solves_initiated_total` | counter | 启动的求解次数 |
| `bb_solves_converged_total` | counter | 收敛的求解次数 |
| `bb_convergence_rate` | gauge | 求解收敛率 |
| `bb_ks_dispatches_total` | counter | KS 调度次数 |
| `bb_ks_errors_total` | counter | KS 执行错误 |
| `bb_debate_rounds_total` | counter | 辩论轮次 |
| `bb_conflicts_active` | gauge | 活跃冲突数 |
| `bb_dead_letters_total` | counter | 死信事件数 |
| `bb_solve_duration_ms` | histogram | 求解耗时分布 |

---

## 3. 告警规则

### P1 — 立即响应

| 条件 | 告警 | 排查路径 |
|------|------|----------|
| `bb_convergence_rate < 0.3`持续 5 分钟 | BlackboardSolveDegraded | 检查 KS 执行日志；dead-letter 队列 |
| `bb_ks_errors_total` 突增 | KnowledgeSourceFailure | 检查各 KS 错误堆栈 |
| `bb_dead_letters_total > 100` 1小时内 | FederationDLQBacklog | 检查事件桥接目标可用性 |

### P2 — 当天处理

| 条件 | 告警 | 排查路径 |
|------|------|----------|
| `bb_solve_duration_ms p99 > 30000` | SlowBlackboardSolve | 检查 KS 并发度、Redis 延迟 |
| `bb_conflicts_active > 5` 持续 30 分钟 | PersistentConflicts | 检查各域 polarity 配置偏差 |

---

## 4. 排查 SOP

### 4.1 求解不收敛

1. 检查 `ConvergenceCriterion` 配置是否合理（阈值 / max_iter）。
2. 用 `controller.get_trace()` 查看最近执行轨迹。
3. 检查是否有 KS 异常导致写入未执行：查看 `bb_ks_errors_total`。
4. 若使用 `GoalStateCriterion`，确认目标域/状态值是否拼写一致。

### 4.2 辩论无共识

1. 降低 `consensus_threshold`（默认 0.6）。
2. 检查参与者数量是否满足 `quorum`。
3. 查看 `result.ballots` 分析各方立场与置信度。

### 4.3 跨项目事件丢失

1. 检查 `FederationBus.dead_letter_size`。
2. 确认目标桥接名称正确（`emit` 时目标 bridge 存在）。
3. 检查网络/防火墙是否阻断 inter-process 通信。

### 4.4 租户数据泄漏

1. 确认 `TenantContext` 在每次请求初始化时设置正确。
2. 检查 `_TENANT_KEY` metadata 是否在写入时被正确打标。
3. 用 `tbb.get_snapshot()` 验证仅返回本租户数据。

---

## 5. 配置参考

+----------------------+--------+------------------+------------------+
| 环境变量             | 默认值 | 说明             | 影响面           |
+======================+========+==================+==================+
| MAOP_BB_MAX_ITER     | 100    | 每 solve 最大    | 收敛上限 / 资源  |
|                      |        | 迭代次数         | 消耗             |
+----------------------+--------+------------------+------------------+
| MAOP_BB_MAX_CONCURRENT| 4     | KS 最大并发      | CPU / 内存       |
+----------------------+--------+------------------+------------------+
| MAOP_BB_CYCLE_DELAY  | 0.0    | 每轮间隔（秒）   | 吞吐 vs 实时性   |
+----------------------+--------+------------------+------------------+
| MAOP_BB_DEBATE_THRESHOLD| 0.6 | 辩论置信度阈值  | 共识灵敏度       |
+----------------------+--------+------------------+------------------+
| MAOP_FEDERATION_DLQ_LIMIT| 1000 | 死信队列上限  | 内存占用         |
+----------------------+--------+------------------+------------------+

---

## 6. 性能基线

| 场景 | 基线 | 备注 |
|------|------|------|
| Blackboard write | <1ms p99 | 纯内存场景 |
| 单规则匹配 | <0.1ms | 简单 predicate |
| 收敛求解 (5 KS) | <50ms | 10 轮迭代 |
| 3 方辩论 2 轮 | <100ms | 无 LLM 调用 |
| 事件联邦转发 | <1ms | 本地进程 |
| 租户隔离读写 | <1.2x 基线 | 多 10 租户开销 <20% |
