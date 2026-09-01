# ADR-019: 模块级单例的可测性问题（MAOP_DATA_DIR 切换下路径固化）

**Status**: Accepted
**Date**: 2026-09-02
**Decision Owner**: MAOP Architecture Team
**Related**: ADR-018 (Swallowed Exception Handling), `tests/conftest.py`

## Context

在 ABC 收尾（B：跑全量测试）时，全量 pytest 出现 6 个失败，错误统一为 `no such table: hooks`。本以为是 Silent exception 改动引入的回归，**验证后发现是既有 flaky**：

- 用 `git stash` 把全部 16 个文件改动暂存后单独跑 3 个指定测试 → 通过
- 用改动后代码单独跑 test_maop_execute.py + test_phase4.py → 64 passed 全过
- 用改动后代码跑全量 → 6 failed（"no such table: hooks"）
- 修复 `tests/conftest.py` 加入 `reset_hook_manager()` 后全量 7432 passed 稳定

### 根因

`py/maop/core/agent/plugins_hooks/hook_manager.py` 维护一个模块级全局单例：

```python
_hook_manager: HookManager | None = None

def get_hook_manager(root_dir=None) -> HookManager:
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager(root_dir=root_dir or "data")
    return _hook_manager
```

`HookManager.__init__` 在构造时调用 `get_db_path("hook_manager")`：

```python
self._db_path = get_db_path("hook_manager")
```

`get_db_path` 内部读 `os.getenv("MAOP_DATA_DIR")`：

```python
def _resolve_data_dir() -> Path:
    data_dir = os.getenv("MAOP_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir)
    root = find_project_root()
    return root / "data"
```

`conftest.py` autouse fixture 给每个测试 `monkeypatch.setenv("MAOP_DATA_DIR", tmp_path / "data")`：

- 测试 A 第一个调用 `get_hook_manager()` → 单例用 A 的 tmp 目录创建并固化 `db_path`
- 测试 A 结束 → tmp 目录被 `_tmp_dirs` 清理（`shutil.rmtree`）
- 测试 B 用同一个单例 → 单例持有的 `db_path` 仍指向 A 的已删除目录 → `sqlite_connect()` 开新连接但 hooks 表不存在 → "no such table: hooks"

### 问题面更广

`py/maop/` 全局 grep 出 **19 个模块级单例**：

| 模块 | 单例 | 是否在 conftest reset |
|------|------|---------------------|
| `core/agent/plugins_hooks/hook_manager.py` | `_hook_manager` | ✅ 已加 |
| `core/backends/backends.py` | `ConnectionPool` | ✅ `reset_backends` |
| `core/config/config_history.py` | `_global_history` | ❌ |
| `core/reliability/event_bus.py` | `_global_bus` | ❌ |
| `core/reliability/worker_pool.py` | `_global_pool` | ❌ |
| `core/routing/load_balancer.py` | `_global_lb` | ❌ |
| `core/routing/route_scorer.py` | `_singleton_lock` (LoadBalancer) | ❌ |
| `dashboard/routers/hook.py` | `_hook_mgr` | ❌ |
| `dashboard/routers/mcp.py` | `_mcp_hub` | ❌ |
| `dashboard/routers/model.py` | `_model_registry` / `_api_key_vault` | ❌ |
| `dashboard/routers/agent_proxy.py` | `_agent_proxy` | ❌ |
| `dashboard/routers/budget.py` | `_budget_guard` | ❌ |
| `dashboard/routers/protocol.py` | `_protocol_reg` | ❌ |
| `dashboard/routers/subagent.py` | `_subagent_mgr` | ❌ |
| `dashboard/routers/tool_audit.py` | `_tool_audit` | ❌ |
| `dashboard/routers/worktree.py` | `_worktree_mgr` | ❌ |
| `delegate/sla_monitor.py` | `_metrics` | ❌ |
| `maop_loop_phases.py` | `_otel_tracer` | ❌ |

其中持有 `db_path` / `root_dir` / 任何受 `MAOP_DATA_DIR` 影响的资源路径的单例，**理论上都有同样 flaky 风险**。目前全量测试 7432 passed 0 failed 是因为：(a) 多数测试用 mock；(b) 真正调用 get_xxx() 单例的测试恰好以非冲突顺序调度。本质是 **隐性的顺序耦合**。

## Decision

### 短期（本 ADR 接受 + 已落地）

`tests/conftest.py` autouse fixture 已加入 `reset_hook_manager()`。这是修复暴露问题的最小动作，**不重写任何业务代码**。

### 中期（推荐但不强制）

建立统一的单例重置协议：

1. **每个模块级单例配套 `reset_<name>()` 函数**（与已有的 `reset_backends()`、`reset_hook_manager()` 一致）
2. **conftest autouse fixture 一次性 reset 所有已知单例**
3. 在 conftest 维护一个 `_KNOWN_SINGLETONS` 列表，新单例必须注册 + 配套 reset

### 长期（设计债，建议 ADR-020 跟进）

将单例模式从"模块级全局 + 懒加载"重构为"DI 容器显式管理"：

- 进程启动时构造一次服务容器（`ServiceContainer`）
- 测试通过 `container.override(HookManager, MockHookManager())` 显式替换
- 消除"测试间共享单例"这一根本问题

短期投入小、长期投入大但根治。**本 ADR 接受中期方案作为推荐路径**，长期方案列为技术债 backlog。

## Consequences

### 正面
- 暴露 6 个 flaky 测试已修复（7432 passed, 0 failed）
- 文档化 19 个同类风险点，避免后续开发者重新踩坑
- 中期方案（统一 reset 协议）成本可控（~20 行 + 单测）

### 负面
- 短期只 fix hook_manager，其他 17 个单例仍存在潜在 flaky 风险（目前未触发）
- 长期 DI 重构涉及面广，需要专门迭代

### 不变的
- 业务代码不重写
- 模块 API 不变（`get_hook_manager()` 签名一致）

## Follow-up Backlog

- [ ] 为 `config_history` / `event_bus` / `worker_pool` / `load_balancer` / 各 router 单例补充 `reset_*()` 函数
- [ ] conftest 集中调用，移除分散 import
- [ ] （可选）ADR-020: 引入显式 DI 容器替换模块级单例
