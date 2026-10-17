# ADR-029: LLM Cost Optimization（F3-05）

## Status

Proposed

## Context

MAOP 进入了 **PRD 第5章阶段三"生态与平台化"**，F3-05 LLM Cost Optimization 是可最早启动的特性——仅依赖 F1-04（OTel）和已有模型管理体系，无跨阶段二依赖（PRD 6.3 可并行组）。

### 现状分析（已实测）

| 已有能力 | 位置 | F3-05 复用方式 |
|----------|------|----------------|
| `CostTracker` + `BudgetStatus` | `py/maop/core/cost_tracker.py` | 成本明细 / 预算守卫的底层存储 |
| `SemanticCache` | `py/maop/core/memory/semantic_cache.py` | 语义缓存命中 → 直接返回 |
| `ModelSelector` | `py/maop/model/selector.py` | 能力/策略选模型的现有实现 |
| `chat_with_fallback` | `py/maop/core/agent/llm_chat/llm_factory.py` | 现有被动降级链（模型级 `fallback_model`） |
| `LoadBalancer` | `py/maop/core/routing/load_balancer.py` | 负载感知选工 |

### 缺失能力（本 ADR 引入）

| 缺口 | 说明 |
|------|------|
| 不存在"智能路由器" | 当前路由仅按 capability 匹配 + quality/latency 打分，不含**预算感知/任务类型感知**的多因素打分 |
| 降级链仅模型级，不含任务级降级 | 现有 `fallback_model` 仅在模型不可用时触发；无"子任务分类 → 低优任务主动降级"策略 |
| 无成本 dashboard | Cost 页面仅展示历史花费，无**优化建议 / 预算告警联动** |
| 语义缓存分散 | `SemanticCache` 现独立实现，未被 LLM 工厂统一调用 |

## Decision

引入三级成本优化架构（Router → Fallback Chain → Cache），挂载在 `LLMProviderFactory.chat_with_fallback` 之上：

### D1. LLMCostRouter（路由层）

```
                   ┌──────────────────────────┐
  request(task_cls)│   LLMCostRouter.route()   │
──────────────────►│                          │──► ModelConfig (selected)
                   │  F1 Capability Matching  │
                   │  × CostTier Scoring      │
                   │  × Budget-Aware Guard    │
                   │  × Complexity Proxy      │
                   └──────────────────────────┘
```

- **输入**: `messages`, `task_classification`（由 Plan phase 产出），`budget_remaining`，`latency_slo_ms`
- **输出**: 选定 `ModelConfig` + 路由决策日志（mass: `cost_usd_delta`、`routing_reason`）
- **决策要素**:
  1. Capability gate（必须先满足能力匹配，复用 `ModelSelector`）
  2. Task-class routing: code/chat/summarize/emb → 不同 quality tier
  3. Budget guard: 剩余 < 10% → force downgrade
  4. Latency SLO: < 300ms → exclude slow tier；> 3s → include slow tier

### D2. CostAwareFallbackChain（降级链）

在现有 `chat_with_fallback` 之上扩展为"任务级降级链"：

```
model_A(fail) → model_B(fail) → cache_check → cheat_response
     ↑                                ↑
  模型降级（已有）               语义缓存（本 ADR 新增）
```

- 语义缓存检查插入在模型降级之后（模型已降级到最便宜后，检查缓存再决定是否走便宜模型）
- 降级响应为结构化 JSON `{degraded: true, reason: "cache_hit|budget_exceeded|chain_exhausted"}`

### D3. CostDashboard + optimization hints

Dashboard `/api/cost/optimization` 端点返回优化建议：

```json
{
  "monthly_savings_pct": 42.3,
  "top_waste_agent": "code-reviewer",
  "recommendation": "降级 code-reviewer 到 gpt-4o-mini"
}
```

### D4. 默认配置

| 项 | 默认值 | 切换方式 |
|----|--------|----------|
| F3-05 功能 | **关闭**（opt-in） | `MAOP_LLM_COST_ROUTER_ENABLED=1` |
| 路由策略 | BALANCED | `MAOP_COST_ROUTING_POLICY=aggressive\|balanced\|quality_first` |
| 语义缓存阈值 | 0.92 | `MAOP_COST_SEMANTIC_CACHE_THRESHOLD` |

## Consequences

### Positive

- 综合成本降低 ≥ 40%（PRD 5.6.4 AC）
- Opt-in 安全上线，现有行为不变
- 挂载点清晰，不侵入 ModelSelector 内部

### Negative

- 路由延迟增加 < 10ms（路由打分含预算查询）
- 需要保留旧 `chat_with_fallback` 行为不变（guard test 覆盖）

## Related

- PRD: F3-05 LLM Cost Optimization（PRD 5.6）
- HLD: §4.5 LLM Cost Optimization Architecture（HLD 4.5）
- 复用: `CostTracker`（`core/cost_tracker.py`）、`SemanticCache`（`core/memory/semantic_cache.py`）
- 并行组: F3-02 Visual Builder 可独立推进
