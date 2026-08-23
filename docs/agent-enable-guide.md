# Agent 启用指南

本指南说明 MAOP 中 6 个当前被禁用 agent 的启用方法。这些 agent 在 `config/agents.yaml` 中以 `enabled: false` 标记，禁用原因多为 CLI 未安装、API key 未配置或网络不可达。启用前请先确认对应的前置条件已满足，避免启用后调用失败影响编排链路。

## 第1章 通用启用流程

### 1.1 启用步骤

每个被禁用 agent 的启用流程遵循以下 4 步：

1. **定位配置**：在 `config/agents.yaml` 中找到对应 agent 的配置块（搜索 agent 名称作为顶层 key）。
2. **修改启用标志**：将 `enabled: false` 改为 `enabled: true`。
3. **配置凭据与依赖**：根据 agent 类型配置对应的 API key、endpoint 或安装 CLI 到 PATH。
4. **重启服务**：重启 MAOP 后端服务使配置生效，并通过健康检查验证 agent 可用。

### 1.2 验证启用结果

启用后建议执行以下验证：

- 运行 `maop health` 或调用 `/api/health/agents` 端点，确认目标 agent 状态为 `ready`。
- 通过 `/api/agents/{name}/test` 触发一次最小任务调用，确认 CLI 可执行且凭据有效。
- 查看服务日志，确认无 `CLI not found`、`auth failed`、`timeout` 等错误。

### 1.3 通用配置示例

以 `deepcode` 为例，启用前后的配置差异如下：

代码示例：启用 agent 的 yaml 片段

```yaml
# config/agents.yaml
agents:
  deepcode:
    capabilities:
    - codegen
    - search
    - review
    - explain
    cli: deepcode
    cli_args: -p {task}
    description: 'DeepCode agent (enabled)'
    driver: cli
    enabled: true          # ← 由 false 改为 true
    model: yi-large
    timeout_s: 120
```

## 第2章 各 Agent 启用细则

### 2.1 deepcode

- **当前状态**：`enabled: false`，禁用原因 `not registered / no auth (2026-07-22)`。
- **CLI**：`deepcode`（`cli_args: -p {task}`）。
- **默认模型**：`yi-large`。
- **能力**：codegen / search / review / explain。

#### 2.1.1 启用前置条件

1. 注册 DeepCode 账号并获取 API key。
2. 安装 `deepcode` CLI 到系统 PATH，执行 `deepcode --version` 可正常返回。

#### 2.1.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.deepcode` 块中将 `enabled` 改为 `true`。
2. 在 `config/models.yaml` 中确认 `yi-large` 模型定义存在且 provider 凭据已配置；若使用 DeepCode 自有 endpoint，补充对应 API key 与 endpoint。
3. 重启服务并按 1.2 节验证。

### 2.2 gemini

- **当前状态**：`enabled: false`，禁用原因 `no VPN/proxy for Google APIs (2026-07-22)`。
- **CLI**：`gemini`（`cli_args: -p '{task}'`）。
- **默认模型**：`auto`。
- **能力**：codegen / chat / search / review / explain / vision / multimodal。

#### 2.2.1 启用前置条件

1. 配置 VPN 或 HTTP/HTTPS 代理，确保运行环境可访问 Google APIs（`generativelanguage.googleapis.com`）。
2. 安装 `gemini` CLI 到系统 PATH，并完成 `gemini auth login` 登录或设置 `GEMINI_API_KEY` 环境变量。

#### 2.2.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.gemini` 块中将 `enabled` 改为 `true`。
2. 在环境变量或 `.env` 中配置代理（如 `HTTPS_PROXY=http://127.0.0.1:7890`）与 Google API key。
3. 重启服务并按 1.2 节验证，重点关注网络可达性与认证状态。

### 2.3 langcli

- **当前状态**：`enabled: false`，禁用原因 `no Anthropic API key, no VPN (2026-07-22)`。
- **CLI**：`langcli`（`cli_args: -p "{task}"`）。
- **默认模型**：`auto`。
- **能力**：codegen / refactor / explain / search / review。

#### 2.3.1 启用前置条件

1. 配置 VPN 或代理，确保可访问 Anthropic API（`api.anthropic.com`）。
2. 取得 Anthropic API key 并安装 `langcli` CLI 到系统 PATH。

#### 2.3.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.langcli` 块中将 `enabled` 改为 `true`。
2. 在环境变量中配置 `ANTHROPIC_API_KEY` 与代理变量。
3. 重启服务并按 1.2 节验证。

### 2.4 mimo

- **当前状态**：`enabled: false`，禁用原因 `CLI not found in PATH (2026-07-22 health check)`。
- **CLI**：`mimo`（`cli_args: --print {task}`）。
- **默认模型**：`mimo-v2.5`（built-in）。
- **能力**：codegen / chat / quickfix。

#### 2.4.1 启用前置条件

1. 安装 `mimo` CLI 到系统 PATH，执行 `mimo --version` 可正常返回。
2. 若 `mimo` 需要凭据，按其官方文档完成登录或 API key 配置。

#### 2.4.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.mimo` 块中将 `enabled` 改为 `true`。
2. 在 `config/models.yaml` 中确认 `mimo-v2.5` 模型定义存在（provider: mimo）。
3. 重启服务并按 1.2 节验证。

### 2.5 qoder

- **当前状态**：`enabled: false`，禁用原因 `CLI not found in PATH (2026-07-22 health check)`。
- **CLI**：`qoderclicn`（`cli_args: -p {task}`）。
- **默认模型**：`auto`（QoderCN built-in）。
- **能力**：codegen / search / fileops / review / planning / explain / chat / refactor。

#### 2.5.1 启用前置条件

1. 安装 `qoderclicn` CLI 到系统 PATH，执行 `qoderclicn --version` 可正常返回。
2. 若 `qoderclicn` 需要凭据，按其官方文档完成登录或 API key 配置。

#### 2.5.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.qoder` 块中将 `enabled` 改为 `true`。
2. 重启服务并按 1.2 节验证。

### 2.6 qwen

- **当前状态**：`enabled: false`，禁用原因 `CLI not found in PATH (2026-07-22 health check)`。
- **CLI**：`qwen`（`cli_args: -p '{{safePrompt}}' -o json --safe-mode`）。
- **默认模型**：`qwen3.5-plus`（built-in）。
- **能力**：codegen / search / fileops / planning / review / explain / chat。

#### 2.6.1 启用前置条件

1. 安装 `qwen` CLI 到系统 PATH，执行 `qwen --version` 可正常返回。
2. 若 `qwen` 需要凭据，按其官方文档完成登录或 API key 配置。

#### 2.6.2 配置步骤

1. 在 `config/agents.yaml` 的 `agents.qwen` 块中将 `enabled` 改为 `true`。
2. 在 `config/models.yaml` 中确认 `qwen3.5-plus` 模型定义存在（provider: qwen）。
3. 重启服务并按 1.2 节验证。

## 第3章 启用状态速查表

表：被禁用 agent 启用条件对照表

| Agent   | CLI          | 默认模型     | 禁用原因                         | 启用关键动作                         |
|---------|--------------|--------------|----------------------------------|--------------------------------------|
| deepcode| deepcode     | yi-large     | not registered / no auth         | 注册账号 + 配置 API key              |
| gemini  | gemini       | auto         | no VPN/proxy for Google APIs     | 配置 VPN/代理 + Google API key       |
| langcli | langcli      | auto         | no Anthropic API key, no VPN     | 配置 Anthropic API key + VPN         |
| mimo    | mimo         | mimo-v2.5    | CLI not found in PATH            | 安装 mimo CLI 到 PATH                |
| qoder   | qoderclicn   | auto         | CLI not found in PATH            | 安装 qoderclicn CLI 到 PATH          |
| qwen    | qwen         | qwen3.5-plus | CLI not found in PATH            | 安装 qwen CLI 到 PATH                |

## 第4章 注意事项

- **配置文件备份**：修改 `config/agents.yaml` 前建议备份原文件，便于回滚。
- **环境变量优先级**：API key 同时存在于 `.env` 与系统环境变量时，以服务进程实际加载的变量为准；建议统一在 `.env` 中管理。
- **CLI 版本兼容**：部分 CLI 升级后参数可能变化，若启用后调用报参数错误，核对 `cli_args` 与 CLI 版本是否匹配。
- **健康检查**：MAOP 启动时会执行 agent 健康检查，CLI 不在 PATH 的 agent 会被自动标记为 `disabled`，即使 `enabled: true` 也无法调用；请先确保 CLI 可执行。
- **M7 修复评估**：上述 6 个 agent 的启用计划统一标记为 `M7 修复评估`，启用前可参考最新 M7 评估结论确认是否仍有遗留依赖。