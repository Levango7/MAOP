# ADR-005: 保留 PowerShell 核心引擎，Dashboard 可迁 Python

## Status
**Superseded** by [ADR-009](009-python-primary-engine.md). Original: Accepted.

## Date
2026-07-10 (superseded 2026-07-15)

## Decider
MAOP Core Team

## Context
整个项目约 9000 行 PowerShell——有人提出是否全部迁移到 Python。关键分析：

**PS 优势**：进程编排（`Start-Process`/`Start-Job`/`Wait-Process`）是 MAOP 的核心场景——调 20+ 外部 exe/cmd agent CLI。PS 对此天然契合（参数拼接、窗口隐藏、超时 kill 均为原生）。

**Python 劣势**：`subprocess` 可以做但摩擦更大——编码/路径空格/Windows 超时杀进程/后台进程管理都需要额外胶水代码。翻译 9k 行 PS→Python 预估 3-4 周 + 每个外部 CLI 需重新调试调用签名。

**Python 优势**：YAML/JSON 零摩擦、Web server（FastAPI 异步可解决 Dashboard 阻塞问题）、测试生态（pytest）。

## Decision
**不重写。做渐进迁移：**
1. YAML 解析 → Python 桥接（`tools/parse-config.py`）——已做（ADR-001）
2. Dashboard 可选迁 Python FastAPI（留待未来按需决定）
3. 核心编排引擎继续保持 PowerShell

## Consequences
- **变得容易**：保留已有测试、安全审计、生产运行经验
- **变得容易**：YAML 解析问题已通过桥接低成本解决
- **风险**：Windows 锁定不可移植——但 MAOP 的使用场景就是 Windows 单机 agent 编排
