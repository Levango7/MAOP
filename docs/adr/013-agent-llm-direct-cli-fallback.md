# ADR-013: Agent 机制 — LLM 直连主路径 + CLI 降级保留（双路径并存）

## Status
**Accepted** — Phase F 决策记录。

## Date
2026-07-22

## Decider
MAOP Core Team

## Context

MAOP v4.0.0 的 Agent 执行机制存在以下问题（2026-07-22 全维度评估发现）：

1. **主执行路径全部走 subprocess CLI** — `config/agents.yaml` 中 16+ 个 agent
   全部配置为 `driver: cli`，`delegate/drivers.py` 的 5 个驱动（cli/wrapper/
   powershell/cmd/python）全部使用 `asyncio.create_subprocess_exec` 调用外部
   CLI 二进制（claude/codex/cursor/kimi/mavis 等）。

2. **ReAct 循环依赖 CLI stdout 解析** — `core/react_loop.py:216-224` 通过
   `dispatcher.dispatch()` 调用 CLI 工具，然后将 `exec_result.stdout` 当作
   LLM 响应来解析 tool_calls。这不是 LangChain 那种在循环内直接调 LLM API
   的真 ReAct。

3. **真实 LLM API 抽象层存在但未上主路径** — `core/llm_provider.py` 有完整的
   httpx 直连 OpenAI/Anthropic/Ollama 实现（支持 SSE 流式、tool use、fallback
   链），但只在 `chat_engine.py` 主路径和 `subagent_lifecycle.py`（自称
   placeholder）中使用，不在 `maop_execute` 主执行路径上。

4. **AgentAdapter 是空 ABC** — `core/agent_bridge.py` 定义了 `AgentAdapter(ABC)`
   抽象基类，但没有任何具体实现子类。

5. **A2A task 执行回到 subprocess** — `core/a2a.py` 是真实的 Google A2A 协议
   实现，但 task 执行路径是 `A2A → WorkerPool → Dispatcher → subprocess CLI`。

## Decision

**采用双路径并存架构**：LLM 直连为主执行路径，CLI 作为降级 fallback 保留。

### 1. 主路径：LLM 直连

- `ReactLoop` 在循环内直接调 `llm_provider.chat_with_fallback()`
  （移除 dispatcher → CLI stdout 解析中间层）
- `subagent_lifecycle._invoke_llm` 接入主路径（从 placeholder 转为正式调用）
- 新增 `AgentAdapter` 具体子类：`OpenAIInProcessAgent`、
  `AnthropicInProcessAgent`、`OllamaInProcessAgent`
- A2A task 执行从 WorkerPool → subprocess 改为 LLM 直连
- `ToolManager.call()` 从 `subprocess.run` 改为 MCP 协议调用

### 2. 降级路径：CLI 包装保留

- `drivers.py` 5 个 subprocess 驱动保留，**不删除**
- `agents.yaml` 的 `driver: cli` 字段标记为 `deprecated`，但保留
- 当 LLM provider 调用失败（API key 未配置 / 网络错误 / 速率限制）时，
  自动降级到 CLI 驱动
- 现有 16+ 个外部 CLI 集成（claude/codex/cursor 等）的测试全部保留

### 3. 配置：agents.yaml 双字段

```yaml
# 新增 model+provider 字段（主路径）
claude:
  model: claude-sonnet-4-5
  provider: anthropic
  # 保留 driver+cli 字段（降级路径）
  cli: claude
  cli_args: -p '{task}'
  driver: cli  # deprecated, used as fallback when llm_provider fails
```

- 新增 `model` + `provider` 字段作为主配置
- 保留 `driver` + `cli` + `cli_args` 字段作为降级 fallback
- 用户可选择配置 LLM（主路径）或保留 CLI（降级），两种模式均测试通过

### 4. 路径选择逻辑

```
maop_execute(agent, task):
  config = load_agent_config(agent)
  if config.model and config.provider:
    try:
      return await llm_provider.chat_with_fallback(config.model, task)
    except (LLMError, APIKeyMissing, RateLimitError):
      logger.warning("LLM provider failed, falling back to CLI")
  if config.driver:
    return await drivers.run_cli(config, task)
  raise NoExecutionPathError(agent)
```

## Consequences

### 变得容易

- **真 Agent 能力**：ReAct 循环在循环内直接调 LLM API，支持真正的
  thought/action/observation 迭代，而非依赖 CLI stdout 解析
- **LLM provider 充分利用**：已有的 httpx 直连 OpenAI/Anthropic/Ollama 代码
  （SSE 流式、tool use、fallback 链）上主路径
- **Subagent 系统激活**：从 placeholder 转为正式调用
- **A2A 真实化**：跨 agent 通信通过 LLM 直连而非 subprocess

### 变得容易

- **向后兼容**：现有用户环境（已配置 claude/codex/cursor CLI）不会失效
- **降级保障**：LLM provider 不可用时自动降级到 CLI

### 风险

- **双路径维护成本**：需要同时维护 LLM 直连和 CLI 两条路径的测试
- **路径选择复杂性**：需要在配置层面区分"用 LLM"vs"用 CLI"vs"自动降级"
- **agents.yaml 复杂度增加**：两套字段并存，需要明确的优先级文档

## Implementation Plan

| Phase | Task | Description |
|-------|------|-------------|
| F2 | agents.yaml 双字段 | 新增 model+provider 字段，标记 driver: cli 为 deprecated |
| F3 | ReactLoop LLM 直连 | 移除 dispatcher → CLI stdout 解析，直接调 llm_provider |
| F4 | subagent_lifecycle 接入 | 从 placeholder 转为正式调用 |
| F5 | AgentAdapter 子类 | 实现 OpenAI/Anthropic/Ollama in-process agent |
| F6 | A2A 执行改造 | task 执行从 subprocess 改为 LLM 直连 |
| F7 | ToolManager 改造 | subprocess.run → MCP 协议 |

## Alternatives Considered

### A. 完全废弃 CLI 驱动

删除 `drivers.py` 和所有 `driver: cli` 配置，主执行只能走 LLM provider。

**否决原因**：用户现有 16+ 个外部 CLI 环境（claude/codex/cursor 等）将完全
失效，且需要重写所有 agent 集成测试。风险过高。

### B. 双路径并存但 LLM 为可选

新增 model+provider 配置作为新路径，driver: cli 保留但不推荐。用户自由选择。

**否决原因**：两套路径没有明确优先级，测试复杂度翻倍。Q2 决策选"全部改为
LLM 直连"作为目标方向，但需要降级保留以降低风险，所以采用本 ADR 的方案。

## References

- Q2 决策（2026-07-22）：用户选择"全部改为 LLM 直连"，CLI 兼容性选择"双路径并存"
- agents.yaml 迁移决策（2026-07-22）：选择"新增字段 + 保留旧字段"
- ADR-006（Superseded）：SSE 删除决策已反转
- `py/maop/core/llm_provider.py`：已有的 LLM API 抽象层
- `py/maop/core/react_loop.py`：需要改造的 ReAct 循环
- `py/maop/delegate/drivers.py`：保留的 CLI 驱动（降级路径）
