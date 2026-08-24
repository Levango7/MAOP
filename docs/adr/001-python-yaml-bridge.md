# ADR-001: Python YAML 桥接替代手写正则解析

## Status
Accepted

## Date
2026-07-10

## Decider
MAOP Core Team

## Context
MAOP 项目最初用 PowerShell 手写正则解析 `agents.yaml`——`maop-plan.ps1`、`maop-loop.ps1`、`delegate-plugin.ps1` 各有一套独立的按行扫描解析器，合计约 **254 行**代码。问题：
1. 三套解析器独立实现，一处改 YAML 结构三处需同步
2. 缩进敏感、不支持嵌套结构
3. 正则匹配脆弱——缩进层级变化就断

同时，用户本地已安装 Python 3.13 + PyYAML 6.0.3。

## Decision
引入 Python 脚本 `tools/parse-config.py` 作为统一的 YAML→JSON 桥接：
- 使用 `yaml.safe_load` 做真正的结构化解析
- 支持 `--section agents|routing|loops|workflows|rules|all` 等按需输出
- 支持 `--agent <name>` 和 `--routing-key <key>` 精确查询
- PS 侧通过 `Invoke-ConfigBridge` 函数统一调用 `python tools/parse-config.py --section X`

提取共享桥接到 `tools/maop-bridge.ps1`，三个核心脚本改为一行 dot-source：
`. (Join-Path $ProjectRoot "tools\maop-bridge.ps1")`

**删除的手写正则：**
- maop-plan.ps1：`Parse-YamlSection` + `Parse-YamlFlatValues` (78行)
- maop-loop.ps1：`Get-RoutingTable` + inline YAML 解析 (48行)
- delegate-plugin.ps1：`Get-AgentConfig` + `Resolve-AgentConfig` + `Resolve-ConfigInSection` (147行)

## Consequences
- **变得容易**：YAML 结构变更只需改 `agents.yaml`，无需同步 PS 脚本
- **变得容易**：新增 agent 字段自动透传，不依赖 PS 正则更新
- **变得容易**：可复用 Python 生态（未来可升级到 schema validation）
- **变得困难**：依赖 Python 环境 + PyYAML（但用户已有，且 `tools/maop-bridge.ps1` 在 Python 不可用时 graceful fallback）
- **风险**：跨进程调用有微小性能开销，但路由解析在 plan 阶段只发生一次，影响可忽略

## Supersedes
替代了 maop-plan.ps1、maop-loop.ps1、delegate-plugin.ps1 中全部手写 YAML 解析逻辑。
