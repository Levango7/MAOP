# ADR-012: 配置化路由重构评估（仅设计/不执行）

## Status
Deferred (P2) — config routing partially implemented via _route_by_config in maop_plan.py. Full config-driven routing remains a P2 backlog item.

## Context
`config/agents.yaml` 的 `routing:` 段（:272-324，共 14 条：codegen/refactor/search/planning/review/verify/fileops/chat/quickfix/mcp/memory/docgen/techdoc/pipeline）由 `py/maop/config/loader.py:149-151` 正确解析为 `MaopConfig.routing: dict[str, RouteEntry]`（`RouteEntry` 含 primary/fallback/tertiary，定义于 `loader.py:42`），设计意图是**以配置为路由真源**。`py/maop/maop_loop.py:715` 也确实把 `config=self._config` 传入 `maop_plan`。

但实际生效路径是：
- `py/maop/maop_plan.py:29 _ROUTING_RULES` —— **硬编码** 10 条 `(regex, routing_key, agent)`，由 `_route_by_keyword`（:44）消费，这是真正生效的路由。
- `py/maop/maop_plan.py:53 _route_by_config` —— 本应消费配置路由，但其匹配逻辑为：
  ```python
  for rk, route in config.routing.items():
      if route.primary and route.primary.lower() in task_lower:   # 用「主 agent 名」是否出现在任务文本里
          return rk, route.primary
  ```
  即只有当任务文本里**字面包含某个 agent 名字**（如 "claude"）时才命中——语义上几乎永远不触发，配置路由因此**实质失效**（99% 回落到硬编码 `_ROUTING_RULES`）。
- 两套路由键空间**根本不对齐**：`_ROUTING_RULES` 键为 code/test/debug/deploy/docs/design/security/perf/data/config；`agents.yaml` 键为 codegen/refactor/search/planning/review/.../pipeline。即便 `_route_by_config` 修好，返回结果也无法映射回同一组 routing_key。

`py/maop/maop_loop.py:913 _build_fallback_chain` 已能用 routing_key 从 `config.routing` 取 primary→fallback→tertiary，说明 fallback 链路基础设施就绪，只是上游选不到配置路由键。

## Decision
**推荐：提升 `agents.yaml` routing 为生效真源，修复 `_route_by_config`，弃用硬编码 `_ROUTING_RULES`，不新建 routing.yaml。**

1. **扩展 `RouteEntry` 匹配能力**（`loader.py:42`）：增加 `match: str = ""`（正则）和/或 `keywords: list[str] = []`，使配置能表达「何时命中该路由」。保持单配置文件，避免再拆 routing.yaml 造成碎片。
2. **重写 `_route_by_config`**（`maop_plan.py:53`）：按 `route.match`/`route.keywords` 对任务文本做语义匹配，命中返回 `(rk, route.primary)`；未命中返回 None。
3. **为 `agents.yaml` 14 条 routing 补全 match/keywords**（如 `codegen`: keywords=[code, 生成代码]；`refactor`: [重构]；`search`: [搜索, 检索]；等），使配置路由具备可执行的匹配规则。
4. **弃用 `_ROUTING_RULES`/`_route_by_keyword`**（`maop_plan.py:29,44`）：保留为「配置缺失时的兜底」或整体删除；`maop_plan` 主流程（:106-120）改为优先 `_route_by_config`，无配置才回退关键字。
5. **`maop_loop.py` 影响**：`:715` 已传 config，无需改；`_build_fallback_chain`（:913）已基于 routing_key 查表，天然兼容；仅需确认 `selected_agent` 与 `routing_key` 来自同一配置路由结果（小改动/校验）。

## Consequences
- **正面**：路由规则集中在 `agents.yaml`，运维可改路由而无需动 Python；配置自带 primary/fallback/tertiary 降级链，比硬编码规则更完整；消除「配置解析却从不生效」的设计债。
- **代价**：
  - `loader.py`：`RouteEntry` 加字段（向后兼容，默认空）。
  - `agents.yaml`：14 条需补全 match/keywords（中等手工量）。
  - `maop_plan.py`：重写 `_route_by_config`、调整主流程优先级、收敛 `_ROUTING_RULES`。
  - 测试：`py/.tmp/.../test_routing0` 等需随新匹配语义更新。
- **风险/影响面**：路由是核心路径，改动会改变**生产环境的 agent 选择结果**。当前硬编码规则「能用」，配置路由修复后行为会变化（如某些任务改走不同 agent）。必须配套：① 针对 14 条路由的单元测试；② 在预发环境比对新旧路由输出；③ 建议先以「配置优先 + 旧规则兜底」灰度，再移除兜底。
- **是否值得做**：**值得**，但**不是 P0**。理由——它不造成数据不一致（不像 P0-3 的分裂脑是正确性缺陷），而是可维护性/设计债问题；修复能解锁 14 条精心设计的路由与降级链。建议作为 P1 单独立项，配测试与灰度，由 code-reviewer 在 P0-3 之后执行。
