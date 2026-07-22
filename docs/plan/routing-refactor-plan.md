# 配置化路由重构 —— 详细实现方案

依据 ADR-012 决策方向，将 `agents.yaml` routing 段提升为生效真源，修复 `_route_by_config`，
弃用硬编码 `_ROUTING_RULES`。本文件不修改源码，仅记录可执行的变更清单。

---

## 目录

1. [变更 1：扩展 RouteEntry —— 增加 match / keywords 字段](#变更-1扩展-routeentry--增加-match--keywords-字段)
2. [变更 2：为 agents.yaml 的 14 条路由补全 keywords 表](#变更-2为-agentsyaml-的-14-条路由补全-keywords-表)
3. [变更 3：重写 _route_by_config —— 语义匹配](#变更-3重写-_route_by_config--语义匹配)
4. [变更 4：弃用 _ROUTING_RULES / _route_by_keyword](#变更-4弃用-_routing_rules--_route_by_keyword)
5. [变更 5：更新 maop_plan() 主流程优先级](#变更-5更新-maop_plan-主流程优先级)
6. [灰度 / 回退策略](#灰度--回退策略)
7. [附录：两套路由键空间对照](#附录两套路由键空间对照)

---

## 变更 1：扩展 RouteEntry —— 增加 match / keywords 字段

### 文件

`py/maop/config/loader.py`

### 位置

第 42-46 行，`class RouteEntry(BaseModel)`

### 当前内容

```python
class RouteEntry(BaseModel):
    """Routing table entry: primary -> fallback -> tertiary."""
    primary: str = ""
    fallback: str = ""
    tertiary: str = ""
```

### 改为

```python
class RouteEntry(BaseModel):
    """Routing table entry: primary -> fallback -> tertiary.

    ``match``: optional regex pattern (re.search) to match against task text.
    ``keywords``: optional list of keyword strings; any match triggers this route.
    Both are backward-compatible — empty defaults mean 'no matching rule'.
    """
    primary: str = ""
    fallback: str = ""
    tertiary: str = ""
    match: str = ""
    keywords: list[str] = Field(default_factory=list)
```

### 影响

- **向后兼容**：`match=""`, `keywords=[]` 是默认值，现有 YAML 配置无需修改即可解析。
- `RouteEntry(**entry)` 在 `loader.py:151` 会直接解包 YAML 中新增的 `match` 和 `keywords` 字段，**无需修改 loader 逻辑**。

---

## 变更 2：为 agents.yaml 的 14 条路由补全 keywords 表

### 文件

`config/agents.yaml`

### 位置

`routing:` 段（当前第 243-295 行）

### 建议的 keywords / match 规则

每条路由补一个 `keywords:` 字段。`match` 正则仅在 keywords 不够精确时使用。

| 路由键 | primary | keywords（建议） | match（正则，可选） | 设计理由 |
|--------|---------|------------------|---------------------|----------|
| `codegen` | claude | `[编写, 写代码, 生成, 开发, implement, coding, 功能, feature, 实现]` | — | 核心编码任务 |
| `refactor` | claude | `[重构, 重写, rewrite, restructure, 清理, clean up, 优化结构]` | `(?:refactor|rewrite|restructure|clean\s+up)` | 代码结构调整 |
| `search` | kimi | `[搜索, 检索, 查, 查找, find, 调研, research, 查询, look up, 资料]` | — | 搜索/知识获取 |
| `planning` | openclaw | `[规划, 计划, 方案, 架构, plan, design, 设计, blueprint, 策略, strategy]` | `(?:plan|architect|design|blueprint|strategy)` | 分析与设计 |
| `review` | kimi | `[审查, 审核, review, code review, 评审, 检查代码]` | — | 代码审查 |
| `verify` | mavis-verifier | `[验证, verify, 测试, test, 单元测试, 集成测试, assert, 断言]` | `(?:verify|test|assert|spec|unit\s+test|integration)` | 验证/测试 |
| `fileops` | qoder | `[文件操作, 创建文件, 删除文件, 移动文件, 读写文件, file operations, read file, write file, 复制]` | — | 文件系统操作 |
| `chat` | openclaw | `[聊天, 对话, 问答, 闲聊, chat, 一般问题, 随意]` | — | 通用对话 |
| `quickfix` | cursor | `[修复, fix, bug, 错误, 紧急, quick fix, hotfix, 补丁, patch, 问题]` | `(?:fix|bug|error|hotfix|patch|quick\s*fix)` | 快速修复 |
| `mcp` | openclaw | `[mcp, 工具调用, 外部工具, MCP, tool call, function call]` | — | MCP 工具调用 |
| `memory` | mavis | `[记忆, 记住, 存储, memory, remember, 历史, 上下文]` | — | 记忆/持久化 |
| `docgen` | doc-pipeline | `[生成文档, 文档, document, 注释, 说明, 文档生成, readme, api文档]` | `(?:document|doc|readme|guide|comment)` | 文档生成 |
| `techdoc` | doc-pipeline | `[技术文档, 规范, specification, 接口文档, API文档, tech doc, 架构文档, 白皮书]` | — | 技术文档 |
| `pipeline` | doc-pipeline | `[流水线, pipeline, 工作流, workflow, 自动化流程, ci/cd, 持续集成]` | — | 流水线/工作流 |

### YAML 修改示例（以 codegen、refactor、search 为例）

```yaml
routing:
  codegen:
    primary: claude
    fallback: cursor
    tertiary: kilo
    keywords: [编写, 写代码, 生成, 开发, implement, coding, 功能, feature, 实现]
  refactor:
    primary: claude
    fallback: qoder
    tertiary: openclaw
    match: (?:refactor|rewrite|restructure|clean\s+up)
    keywords: [重构, 重写, rewrite, restructure, 清理, clean up, 优化结构]
  search:
    primary: kimi
    fallback: qoder
    tertiary: hermes
    keywords: [搜索, 检索, 查, 查找, find, 调研, research, 查询, look up, 资料]
  # ... 其余 11 条同理
```

> **注意**：`match` 正则仅在 `keywords` 列表非空但仍然无法区分时使用。对于多数路由，`keywords` 已足够。

---

## 变更 3：重写 `_route_by_config` —— 语义匹配

### 文件

`py/maop/maop_plan.py`

### 位置

第 53-64 行，`def _route_by_config(task, config)`

### 当前内容

```python
def _route_by_config(task: str, config: MaopConfig | None) -> tuple[str, str] | None:
    """Route using config routing table (keyword matching)."""
    if config is None:
        return None

    task_lower = task.lower()
    for rk, route in config.routing.items():
        # RouteEntry doesn't have keywords field; use primary agent name as hint
        if route.primary and route.primary.lower() in task_lower:
            return rk, route.primary

    return None
```

### 改为

```python
def _route_by_config(task: str, config: MaopConfig | None) -> tuple[str, str] | None:
    """Route using config routing table (match regex + keywords).

    Priority:
      1. route.match (regex) — exact pattern match wins first
      2. route.keywords — any keyword literal match in task text
      3. If both empty, skip this route

    Returns (routing_key, primary_agent) or None.
    """
    if config is None:
        return None

    task_lower = task.lower()
    for rk, route in config.routing.items():
        # Step 1: try regex match (most specific)
        if route.match:
            try:
                if re.search(route.match, task_lower):
                    return rk, route.primary
            except re.error:
                logger.warning("Invalid regex in routing.%s.match: %r", rk, route.match)
                continue

        # Step 2: try keyword list match
        if route.keywords:
            for kw in route.keywords:
                if kw.lower() in task_lower:
                    return rk, route.primary

    return None
```

### 说明

- 输入 `task_lower` 用小写匹配，`keywords` 和 `match` 默认都应是大小写不敏感的。
- 先遍历 `match` 正则（更精确），再遍历 `keywords` 列表。
- 如果 `keywords` 列表为空且 `match` 也为空，则不参与匹配。
- 异常保护：`re.error` 时跳过当前路由并记录警告。

---

## 变更 4：弃用 `_ROUTING_RULES` / `_route_by_keyword`

### 文件

`py/maop/maop_plan.py`

### 位置

第 29-50 行

### 当前内容

```python
_ROUTING_RULES: list[tuple[str, str, str]] = [
    (r"(?:refactor|rewrite|restructure|clean\s+up)", "code", "codex"),
    (r"(?:test|spec|verify|assert|unit\s+test|integration)", "test", "codex"),
    (r"(?:debug|fix|bug|error|exception|traceback|repair)", "debug", "codex"),
    (r"(?:deploy|release|publish|ship|rollout)", "deploy", "codex"),
    (r"(?:document|docs?|readme|guide|explain|comment)", "docs", "claude"),
    (r"(?:design|architect|plan|strategy|blueprint)", "design", "claude"),
    (r"(?:security|audit|vuln|cve|hardening)", "security", "codex"),
    (r"(?:performance|optim|speed|benchmark|latency)", "perf", "codex"),
    (r"(?:data|database|sql|query|migration|schema)", "data", "codex"),
    (r"(?:config|setting|env|variable|preference)", "config", "codex"),
]


def _route_by_keyword(task: str) -> tuple[str, str]:
    """Match task keywords to routing key + agent."""
    task_lower = task.lower()
    for pattern, routing_key, agent in _ROUTING_RULES:
        if re.search(pattern, task_lower):
            return routing_key, agent
    return "chat", "claude"
```

### 改为

```python
# ── Legacy keyword routing rules (DEPRECATED) ────────────────
# Kept as fallback when config is None or missing routing section.
# Will be removed in a future cleanup after config routing is fully validated.
_ROUTING_RULES: list[tuple[str, str, str]] = [
    (r"(?:refactor|rewrite|restructure|clean\s+up)", "code", "codex"),
    (r"(?:test|spec|verify|assert|unit\s+test|integration)", "test", "codex"),
    (r"(?:debug|fix|bug|error|exception|traceback|repair)", "debug", "codex"),
    (r"(?:deploy|release|publish|ship|rollout)", "deploy", "codex"),
    (r"(?:document|docs?|readme|guide|explain|comment)", "docs", "claude"),
    (r"(?:design|architect|plan|strategy|blueprint)", "design", "claude"),
    (r"(?:security|audit|vuln|cve|hardening)", "security", "codex"),
    (r"(?:performance|optim|speed|benchmark|latency)", "perf", "codex"),
    (r"(?:data|database|sql|query|migration|schema)", "data", "codex"),
    (r"(?:config|setting|env|variable|preference)", "config", "codex"),
]


def _route_by_keyword(task: str) -> tuple[str, str]:
    """DEPRECATED: Fallback keyword matching when config routing is unavailable.

    Will be removed once config routing is fully validated in production.
    Note: returns legacy routing key space (code/test/debug/...), NOT config space.
    """
    task_lower = task.lower()
    for pattern, routing_key, agent in _ROUTING_RULES:
        if re.search(pattern, task_lower):
            return routing_key, agent
    return "chat", "claude"
```

### 说明

- 仅添加 `DEPRECATED` 标记和 docstring 变更，**不删除代码**。
- 保留作为 `config is None` 或 `config.routing` 为空时的兜底。
- 注意：`_route_by_keyword` 返回的 routing key 空间（code/test/deploy/docs/design/security/perf/data/config）**不同于**配置路由（codegen/refactor/search/planning/...）。当它作为兜底返回时，`maop_plan()` 主流程会使用这些旧键去查 `config.routing`，由于键空间不匹配，**不会查到对应路由项**。此时 `selected_agent` 直接用返回值，fallback 链可能退化。这是可接受的——兜底行为表示「配置路由未覆盖」，应退化为简单选择。

---

## 变更 5：更新 `maop_plan()` 主流程优先级

### 文件

`py/maop/maop_plan.py`

### 位置

第 106-120 行

### 当前内容

```python
    # Try config-based routing first
    if routing_key:
        rk = routing_key
        agent = "claude"
        if config:
            for route_rk, route in config.routing.items():
                if route_rk == rk:
                    agent = route.primary
                    break
    else:
        config_result = _route_by_config(task, config)
        if config_result:
            rk, agent = config_result
        else:
            rk, agent = _route_by_keyword(task)
```

### 改为

```python
    # Priority 1: explicit routing_key override
    if routing_key:
        rk = routing_key
        agent = "claude"
        if config:
            for route_rk, route in config.routing.items():
                if route_rk == rk:
                    agent = route.primary
                    break
    else:
        # Priority 2: config-based routing (match regex + keywords)
        config_result = _route_by_config(task, config)
        if config_result:
            rk, agent = config_result
        else:
            # Priority 3: legacy keyword routing (DEPRECATED — fallback only)
            rk, agent = _route_by_keyword(task)
```

### 说明

仅添加注释标记优先级，**逻辑不变**。当前流程已经正确：
1. 显式 `routing_key` 覆盖
2. `_route_by_config`（配置路由）
3. `_route_by_keyword`（兜底）

---

## 灰度 / 回退策略

### 分步推进计划

| 阶段 | 动作 | 风险 | 回退方式 |
|------|------|------|----------|
| **Phase 0**（当前） | 硬编码 `_ROUTING_RULES` 生效；`_route_by_config` 几乎从不命中 | 无 | — |
| **Phase 1** | 合并 PR1：扩展 RouteEntry + 补全 agents.yaml keywords + 重写 `_route_by_config`。**保留 `_route_by_keyword` 兜底** | 低。配置路由先走，不命中则回退旧规则，行为不变 | git revert PR1 |
| **Phase 2** | 在测试环境收集 Phase 1 的路由命中率日志。确认 `_route_by_config` 覆盖率达 >90% | 中。需确保 14 条路由的 keywords 覆盖面足够 | 调整 keywords，重新部署 |
| **Phase 3** | 合入 PR2：移除 `_ROUTING_RULES` 和 `_route_by_keyword`，清理 deprecation 标记 | 高。一旦配置路由有遗漏，任务会 fallback 到 `chat:claude` 而非旧规则 | 紧急回退 PR2 或临时恢复 PR1 |

### Phase 1 灰度细节

1. **配置优先 + 旧规则兜底**：
   - `_route_by_config` 返回有效结果 → 使用配置路由。
   - `_route_by_config` 返回 None → 回退 `_route_by_keyword`。
   - 用户无感知。

2. **日志监控**：
   在 `_route_by_config` 和 `_route_by_keyword` 处分别加日志（可选，生产可关）：
   ```python
   logger.debug("Route config: matched rk=%s agent=%s", rk, agent)   # 配置命中
   logger.debug("Route fallback: matched rk=%s agent=%s", rk, agent)  # 兜底命中
   ```

3. **预估覆盖**：
   - 当 `config` 非 None 且 `routing` 段完整时，`_route_by_config` 应覆盖 95%+ 的生产请求。
   - 剩余 5% 由 `_route_by_keyword` 兜底（通常是旧键空间规则如 security/perf/data/config）。

### 回退方案

- **PR1 回退**：`git revert <commit>` 恢复 `RouteEntry`、`maop_plan.py`、`agents.yaml` 三个文件的变更。
- **PR2 回退**：`git revert <commit>` 恢复 `_ROUTING_RULES` 代码，重新启用旧规则。
- **热修复**：若仅 keywords 覆盖不全，可在 agents.yaml 补充关键词后重新加载配置（`ConfigLoader.reload()` 支持热重载）。

---

## 附录：两套路由键空间对照

| 硬编码键（_ROUTING_RULES） | 配置键（agents.yaml routing） | 对齐状态 |
|----------------------------|-------------------------------|---------|
| code → codex | codegen → claude | 不对齐 |
| test → codex | verify → mavis-verifier | 不对齐 |
| debug → codex | quickfix → cursor | 不对齐 |
| deploy → codex | —（无对应） | 旧规则独有 |
| docs → claude | docgen → doc-pipeline / techdoc → doc-pipeline | 不对齐 |
| design → claude | planning → openclaw / refactor → claude | 不对齐 |
| security → codex | —（无对应） | 旧规则独有 |
| perf → codex | —（无对应） | 旧规则独有 |
| data → codex | —（无对应） | 旧规则独有 |
| config → codex | —（无对应） | 旧规则独有 |
| —（无对应） | chat → openclaw | 配置独有 |
| —（无对应） | fileops → qoder | 配置独有 |
| —（无对应） | mcp → openclaw | 配置独有 |
| —（无对应） | memory → mavis | 配置独有 |
| —（无对应） | pipeline → doc-pipeline | 配置独有 |
| —（无对应） | review → kimi | 配置独有 |
| —（无对应） | search → kimi | 配置独有 |

旧键空间的 `security`/`perf`/`data`/`config`/`deploy` 在配置路由中没有直接对应项。若需要保留这些路由，可在 `agents.yaml` 中新增对应 routing 条目（如 `analyze` 或 `devops`），或由 `codegen`/`quickfix` 的 keywords 覆盖。

---

## 总结：PR 拆分建议

### PR1 —— 配置路由生效（无行为变化）

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `py/maop/config/loader.py` | 修改 | RouteEntry 增加 `match: str = ""`, `keywords: list[str] = [...]` |
| `config/agents.yaml` | 修改 | 14 条 routing 补全 `keywords` / `match` 字段 |
| `py/maop/maop_plan.py` | 修改 | 重写 `_route_by_config`；标记 `_ROUTING_RULES`/`_route_by_keyword` 为 DEPRECATED |

### PR2 —— 清理遗留代码（后续）

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `py/maop/maop_plan.py` | 修改 | 删除 `_ROUTING_RULES`、`_route_by_keyword`；移除 deprecation 标记；简化 `maop_plan()` fallback |

### 不修改的文件

| 文件 | 说明 |
|------|------|
| `py/maop/maop_loop.py` | `:715` 已传 config；`_build_fallback_chain` 基于 routing_key 查表，天然兼容 |
| `py/maop/config/loader.py`（除 RouteEntry 外） | 解包逻辑不变，YAML 新增字段自动落入 RouteEntry |
