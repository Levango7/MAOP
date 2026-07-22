# ADR-009: Python 主引擎 — 基于实证的架构转向

## Status
Proposed (Supersedes ADR-005)

## Context

ADR-005（2026-07-10）决定保留 PS 核心引擎。当时的主要论据：PS 的进程编排天然适合 CLI agent 调用场景。

经过 12 项 bugfix 的实际修复经验，累积了以下反证：

### 实证发现

| 问题 | PS 的局限 | Python 方案 |
|------|----------|------------|
| **YAML 解析** | 手写正则（dag-engine agent_slot、dynamic-router） | `yaml.safe_load()` 一行 |
| **JSON 类型安全** | 145 处 `ConvertFrom-Json` 全缺 `-AsHashtable`，PS5.1 返回 PSCustomObject 而非 Hashtable，`.Keys` 访问静默丢失 | `json.loads()` 直接返回 dict |
| **并发编排** | Runspace/Job 机制导致 job 泄漏（H-3）、内存累积 | `asyncio.gather()` 无状态泄漏 |
| **子进程管理** | BUG-2: `$p.ExitCode` 在 PS5.1 始终为 0，需 `Start-Process -Wait -PassThru` + try/catch 兜底 | `subprocess.run().returncode` 一次正确 |
| **CLI 调用签名** | 每个外部 agent 需手工拼接参数，空格/引号/编码陷阱多 | shlex 自动处理 + UTF-8 默认 |
| **Dashboard 阻塞** | PS 写 HTTP server 用 `System.Net.HttpListener`，无异步支持 | FastAPI + uvicorn asyncio 原生 |
| **测试生态** | Pester 功能有限，无 fixture/parameterized | pytest + fixture + mock |
| **AI/Agent 框架** | 零生态 | LangChain、OpenAI SDK、RAG 全家桶随时可接入 |

### 迁移现状

- `py/maop/core/` 已有 4 个模块完成（circuit_breaker、error_schema、filelock、guardrail）并全部通过 pytest
- Python bridge（`tools/maop-bridge.ps1`、`evolve-bridge.ps1`、`tools/parse-config.py`）已在实际使用中稳定运行
- PS `src/` 48 个脚本中至少 6 个已发现手写 YAML/JSON 解析的脆弱代码
- M-3 扫描结果：101 处 `ConvertFrom-Json` 全缺 `-AsHashtable`，全量加参数风险巨大且不可逆

## Decision

**Python 为 MAOP 主引擎。PowerShell 降级为 Windows 适配层。**

### 架构分层

```
┌──────────────────────────────────────────┐
│  Python 核心引擎 (maop/maop/)              │
│  ├── asyncio 并发编排（并行 Worker）      │
│  ├── Pydantic 类型安全（YAML/JSON schema）│
│  ├── subprocess CLI 调用                 │
│  └── FastAPI Dashboard（异步非阻塞）      │
├──────────────────────────────────────────┤
│  PowerShell 适配层 (src/ 保留范围)        │
│  └── Windows 专用：服务注册、COM、注册表   │
└──────────────────────────────────────────┘
```

### 迁移策略

- **新增功能**：一律用 Python 实现
- **已有 PS 代码**：不强制重写，按模块自然替换（优先：dag-engine、evolve、maop-loop、memory 系统）
- **PS 保留范围**：仅 Windows 系统管理场景（服务注册、注册表读写、COM 自动化）
- **类型安全规范**：新 Python 代码强制使用 Pydantic 定义数据模型，禁止裸 `dict` 传递

### 基础设施

- `tools/maop-bridge.ps1`：统一 PS→Python 桥接入口
- `py/pyproject.toml`：Python 项目配置，pytest + mypy + ruff 工具链
- 渐进式替换：每个模块先写 Python 版本 → 通过 bridge 调用验证 → 确认稳定后切换

## Consequences

### 优点
- **类型安全**：Pydantic schema 消除 JSON/YAML 解析 95% 的运行时错误
- **测试能力**：pytest 替代 Pester，mock/fixture 覆盖核心路径
- **生态上限**：LangChain/OpenAI 接入无需胶水代码
- **Dashboard**：FastAPI asyncio 解决 CPU 阻塞问题
- **并发**：asyncio 替代 Runspace，消除 job 泄漏

### 代价
- **迁移工期**：核心 6 个模块预估 2-3 周
- **学习成本**：原 PS 为主的开发者需熟悉 Python 工具链
- **双轨期间维护**：bridge 层需维护直到核心模块完全替换

### 不变
- `src/` 存量 PS 脚本继续工作，不做强制重写
- PS 适配层永久保留，Windows 集成能力不丢失
- 所有已有 YAML 配置格式不变

## Alternatives Considered

- **全量重写**：工期 3-4 周，风险集中，不采用
- **保持 PS 主轨道**：ADR-005 的方向，但实证表明 PS 在类型安全、并发、测试方面持续产生 bug
- **混合策略（选中的方案）**：Python 接管核心引擎，PS 做 Windows 胶水，风险可控，收益渐进
