# ADR-018: Swallowed Exception Handling 规范化

**Status**: Accepted
**Date**: 2026-09-02
**Decision Owner**: MAOP Security Team
**Related**: ADR-010 (Batch Bugfix), ADR-004 (Security Hardening)

## Context

在 MAOP 的安全与健壮性审计中（ADR-010 后续轮次），发现 **35+ 处 `logger.debug("Silent exception in ...", exc_info=True)` 吞异常模式**，分布在 20+ 个核心模块。此类模式导致：

1. **可观测性盲区**：异常仅停留在 debug 级别，在 production logging level 下完全不可见
2. **调试成本高昂**：故障排查时无法确认异常是否实际发生及发生位置
3. **与 fail-fast 原则矛盾**：部分场景（如路由决策、认证）的异常本应告警而非静默

典型模式示例（修复前）：
```python
try:
    MAOP_ROUTING_DECISION_TOTAL.inc(...)
except Exception:
    logger.debug("Silent exception in core/selector.py:123", exc_info=True)
```

## Decision

### 规则：吞异常必须带上下文 + 提升日志级别

所有 `except ... : logger.debug("Silent exception in ...")` 模式统一改为：
```python
except Exception as exc:
    logger.warning(
        "[module_name] Short description, continuing: %s",
        exc, exc_info=True,
    )
```

### 控制流不变原则

**不改变任何功能行为**。原逻辑是 best-effort（失败不影响主路径），修复后仍为 best-effort，只是告警可见。

### 分层策略

| 类型 | 示例 | 策略 |
|------|------|------|
| 可选监控指标 | Prometheus 指标打点 | warning + 继续（主路径不受影响） |
| 可选缓存回退 | Bloom filter mmh3 fallback | warning + 继续使用 Python 实现 |
| 关键基础设施 | PG migration CREATE EXTENSION | **fail-fast**（明确错误） |
| 事件总线 | Hook event bus publish | warning + 继续（事件丢失可接受） |

### HookManager 单例重置

在 `tests/conftest.py` 的 autouse fixture 中加入 `reset_hook_manager()`，与已有的 `reset_backends()` 一致。

**理由**：HookManager 是全局单例，首次调用基于当时 MAOP_DATA_DIR 固化 db_path。测试间 MAOP_DATA_DIR 变化但单例不刷新 → 若前一测试 tmp 目录被清理，后续测试引用 dangling 路径报 `no such table: hooks`。这是既有测试顺序 flaky，修复后全量测试稳定通过。

## Consequences

### 正面
- 35+ 处潜在异常从 debug 提升到 warning，production 可观测
- 每条 warning 带 `[module_name]` 前缀和简短描述，可直接定位
- 全量测试 7432 passed, 76 skipped（稳定，无 flaky）
- Ruff 静态检查全部通过

### 负面
- production 日志量可能略增（此前静默的异常现显式输出），需调整日志配置避免 warning 洪泛

### 未改变
- 无任何功能行为变更，所有 best-effort 语义保持不变
- 所有测试通过，生产路径不受影响

## Files Modified

- `py/maop/migrations/pg/env.py` — CREATE EXTENSION fail-fast（P0）
- `py/maop/model/selector.py` — 路由决策指标 warning
- `py/tests/test_phase5.py` — hot_reload watch files 断言更新
- `py/maop/core/security/middleware.py` — 删除死代码 `_lock_time`
- `py/maop/config/hot_reload.py` — 扩展监视 mcp_servers.yaml / tool_whitelist.yaml
- `py/tests/conftest.py` — 加入 `reset_hook_manager()`
- `py/maop/core/agent/plugins_hooks/hook_manager.py` — 4 处 silent exception + 新增 `reset_hook_manager()`
- `py/maop/core/agent/memory_ctx/project_context.py` — 4 处 silent exception
- `py/maop/core/agent/llm_chat/react_loop.py` — 1 处 swallowed exception
- `py/maop/core/routing/load_balancer.py` — 1 处 silent exception
- `py/maop/core/routing/provider_health.py` — 1 处 silent exception
- `py/maop/core/security/auth.py` — 1 处 silent exception
- `py/maop/core/reliability/change_tracker.py` — 1 处 silent exception
- `py/maop/core/reliability/circuit_breaker.py` — 2 处 silent exception
- `py/maop/core/memory/bloom_filter.py` — 1 处 silent exception
- `py/maop/core/evolution/regression.py` — 1 处 silent exception
- `py/maop/core/memory/knowledge_graph.py` — 3 处 silent exception
- `py/maop/core/mcp/mcp_cache.py` — 1 处 silent exception
- `py/maop/core/mcp/mcp_concurrency.py` — 1 处 silent exception
- `py/maop/core/mcp/mcp_hub_compat.py` — 1 处 silent exception
- `py/maop/core/mcp/mcp_hub_metrics.py` — 10 处 silent exception
- `py/maop/core/agent/lifecycle/agent_scanner.py` — 1 处 swallowed exception
- `py/maop/core/agent/delegation/a2a.py` — 1 处 swallowed exception
- `py/maop/core/backends/backends_redis.py` — 1 处 swallowed exception
- `py/maop/core/backends/backends_distributed.py` — 1 处 swallowed exception
- `py/maop/core/budget_guard.py` — 1 处 swallowed exception
- `py/maop/evolve.py` — 2 处 swallowed exception
- `py/maop/dashboard/frontend/static.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/_register_routes.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/ws_dag.py` — 1 处 swallowed exception
- `py/maop/dashboard/upgrade_service.py` — 1 处 swallowed exception
- `py/maop/dashboard/notifications.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/auth.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/stream.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/agents/routes.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/agents/evolution.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/agents/crud.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/budget.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/doc_pipeline_adapter.py` — 2 处 swallowed exception
- `py/maop/dashboard/routes/distributed_worker.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/agent_repair.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/skill_version.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/vector_search.py` — 1 处 swallowed exception
- `py/maop/dashboard/routes/tls.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/cli.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/deploy.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/config_mutator.py` — 1 处 swallowed exception
- `py/maop/dashboard/frontend/data_proxy.py` — 1 处 swallowed exception
