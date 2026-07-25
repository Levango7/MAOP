# Contributing to MAOP

MAOP（Model Agentic Orchestration Platform）是基于 FastAPI 的智能体编排框架，遵循 Plan-Execute-Verify 范式。当前版本 4.3.0，采用 Python 3.12+、双线发布（Personal/Enterprise）、双包结构（pyproject.toml + pyproject-enterprise.toml）、CI 通过 GitHub Actions。本指南面向所有贡献者，描述如何搭建环境、提交代码、通过 CI 并参与发布流程。

---

## 1. Development Environment Setup

### 1.1 Prerequisites

| 依赖 | 最低版本 | 用途 |
|------|----------|------|
| Python | 3.12 | 核心运行时 |
| Node.js + npm | 18+ | 构建 dashboard-enterprise |
| Git | 2.30+ | 版本控制 |
| Docker + Docker Compose | 最新稳定版 | 集成测试 |
| OpenSSL | 1.1.1+ | license 签发测试 |

### 1.2 Clone & Install

```bash
git clone https://github.com/Levango7/MAOP.git
cd MAOP

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows

# 安装依赖（Personal 版）
pip install -r py/requirements.txt -e py/

# 安装开发依赖
pip install -r py/requirements.txt -e py/[dev]
# 或 pip install pytest pytest-asyncio pytest-cov ruff mypy pip-audit
```

### 1.3 Build Frontend

```bash
cd dashboard-enterprise
npm install
npm run build  # 输出到 dist/
cd ..
```

### 1.4 Start Development Server

```bash
# 启动 Dashboard
maop start --port 9079

# 或开发模式（自动重载）
MAOP_ENV=development maop start --port 9079

# 访问
# http://127.0.0.1:9079
# http://127.0.0.1:9079/api/docs  # Swagger UI
```

---

## 2. Project Structure

```
MAOP/
├── py/                      # Python 包（核心代码）
│   ├── maop/
│   │   ├── core/            # 核心模块（50+ 文件）
│   │   ├── dashboard/       # FastAPI 路由
│   │   │   └── routers/     # 32 个 API router
│   │   ├── delegate/        # 委派/调度
│   │   ├── enterprise/      # 企业版功能
│   │   ├── memory/          # 三层记忆
│   │   ├── migrations/      # Alembic 迁移
│   │   ├── model/           # 模型管理
│   │   ├── worker/          # 任务执行器
│   │   ├── cli.py           # CLI 入口
│   │   ├── maop_loop.py     # 主编排循环
│   │   └── ...
│   ├── tests/               # 测试套件（4150+ 用例）
│   │   ├── contract/        # 契约测试
│   │   └── e2e/             # E2E 测试
│   ├── pyproject.toml       # Personal 版包定义
│   └── pyproject-enterprise.toml  # Enterprise 版包定义
├── dashboard-enterprise/    # Vue3 SPA 前端
├── config/                  # 运行时配置
├── docs/                    # 文档
│   └── adr/                 # 架构决策记录
├── monitoring/              # 监控配置（Prometheus/Grafana）
├── archive/                 # 归档代码（PS1 legacy）
├── docker-compose.yml       # 生产部署
└── CHANGELOG.md             # 变更日志
```

---

## 3. Coding Standards

### 3.1 Python 规范

- **Linter**: ruff（配置在 pyproject.toml）
- **Formatter**: ruff format
- **Type checker**: mypy（严格模式）
- **Line length**: 100 字符
- **Import 顺序**: 标准库 → 第三方 → 本地（TYPE_CHECKING 块用于前向引用）
- **命名**: 类 PascalCase、函数/变量 snake_case、常量 UPPER_SNAKE、私有前缀 `_`
- **类型注解**: 所有公开函数必须标注返回类型；优先用 `str | None` 而非 `Optional[str]`
- **Docstring**: 模块级 + 类级 + 公开函数，Google 风格

```bash
# 检查
cd py && ruff check maop/ tests/
cd py && mypy maop/

# 自动修复
cd py && ruff check --fix maop/
cd py && ruff format maop/
```

### 3.2 Vue/JS 规范

- Vue 3 Composition API（`<script setup>`）
- ES Modules
- ESLint + Prettier
- 测试：vitest

### 3.3 异步编程规范

- **必须**使用 async/await，禁止在 async 函数中调用同步阻塞 API（subprocess.run、requests.get）
- **必须**使用 `asyncio.create_subprocess_exec` 替代 `subprocess.run`
- **必须**使用 `httpx.AsyncClient` 替代 `requests`
- **禁止**在 async 函数中使用 `time.sleep()`，改用 `await asyncio.sleep()`
- **数据库**操作使用同步 sqlite3 但放在 executor 中，或使用 aiosqlite

### 3.4 安全规范

- **禁止**硬编码密钥、token、密码
- **必须**通过环境变量或 `core/api_key_vault.py` 读取
- **必须**对路径进行 `os.path.realpath` 校验，防止路径遍历
- **必须**对所有写操作端点调用 `require_admin(request)`
- **必须**对插件进行沙箱隔离（`core/sandbox.py`）
- **必须**对日志输出进行敏感数据脱敏（`monitoring.py` 的 `_redact_sensitive`）

---

## 4. Testing Guidelines

### 4.1 测试类型

| 类型 | 目录 | 用例数 | 用途 |
|------|------|--------|------|
| Unit | `py/tests/test_*.py` | 3900+ | 单元测试 |
| Contract | `py/tests/contract/` | ~150 | API 契约测试 |
| E2E | `py/tests/e2e/` | ~20 | 端到端 |
| Stress | `py/tests/test_stress.py` | 少量 | 压力测试 |

### 4.2 运行测试

```bash
# 全量测试
cd py && pytest

# 带覆盖率
cd py && pytest --cov=maop --cov-report=html --cov-fail-under=60

# 单个测试文件
cd py && pytest tests/test_maop_loop.py -v

# 按 keyword
cd py && pytest -k "test_circuit_breaker" -v

# 跳过慢测试
cd py && pytest -m "not slow"

# 前端测试
cd dashboard-enterprise && npm test
```

### 4.3 编写测试

- 使用 `pytest-asyncio`：`@pytest.mark.asyncio`
- 使用 fixture 共享 setup
- 测试函数命名：`test_<被测函数>_<场景>`
- 必须覆盖：正常路径 + 异常路径 + 边界条件
- 异步测试用 `pytest.mark.asyncio`
- 涉及 LLM 的测试用 mock，禁止真实调用
- 数据库测试用 `:memory:` 或临时文件

```python
import pytest
from maop.core.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    @pytest.fixture
    def breaker(self):
        return CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self, breaker):
        """Circuit should open after failure_threshold consecutive failures."""
        for _ in range(3):
            await breaker.record_failure()
        assert breaker.state == "open"

    @pytest.mark.asyncio
    async def test_resets_on_success(self, breaker):
        """Circuit must reset failure count on successful execution."""
        await breaker.record_failure()
        await breaker.record_success()  # reset
        assert breaker.failure_count == 0
```

### 4.4 测试覆盖率

| 指标 | 阈值 | 说明 |
|------|------|------|
| 全局最低 | 60% | CI 强制 |
| 目标 | 80%+ | 推荐水平 |
| core/、enterprise/ | 85%+ | 关键模块 |

查看报告：`py/htmlcov/index.html`

---

## 5. Git Workflow

### 5.1 分支模型

| 分支 | 用途 |
|------|------|
| `main` | 稳定分支，受保护 |
| `develop` | 集成分支（如启用） |
| `feature/<name>` | 新功能 |
| `bugfix/<name>` | Bug 修复 |
| `hotfix/<name>` | 紧急修复（基于 main） |

### 5.2 Commit 规范

遵循 Conventional Commits：

```
<type>(<scope>): <subject>

<body>

<footer>
```

| type | 用途 |
|------|------|
| feat | 新功能 |
| fix | Bug 修复 |
| docs | 文档 |
| style | 代码风格（不影响逻辑） |
| refactor | 重构 |
| perf | 性能优化 |
| test | 测试 |
| chore | 构建/工具 |
| ci | CI 配置 |
| security | 安全修复 |

示例：

```bash
git commit -m "feat(dispatcher): add SLA-aware priority preemption"
git commit -m "fix(maop_loop): reset circuit breaker on success (H10)"
git commit -m "docs(api): add API reference (M34)"
git commit -m "security(auth): upgrade PBKDF2 to 600k iterations (OWASP 2023)"
```

### 5.3 Pull Request 流程

1. Fork 仓库（如外部贡献者）
2. 创建分支：`git checkout -b feature/my-feature`
3. 提交：遵循 commit 规范
4. 推送：`git push origin feature/my-feature`
5. 创建 PR：标题 + 描述 + 关联 issue
6. CI 检查必须全部通过
7. 至少 1 个 reviewer 批准
8. Squash merge 到 main

### 5.4 PR 模板

```markdown
## Description
<what & why>

## Type of Change
- [ ] feat (new feature)
- [ ] fix (bug fix)
- [ ] docs (documentation)
- [ ] refactor
- [ ] perf
- [ ] security
- [ ] test
- [ ] chore / ci

## Testing
- [ ] Unit tests pass
- [ ] New tests added for new code
- [ ] Manual testing performed

## Checklist
- [ ] Code follows style guide (ruff + mypy)
- [ ] Self-reviewed
- [ ] Documentation updated (CHANGELOG.md, docs/)
- [ ] No new warnings
- [ ] No secrets committed
```

---

## 6. CI/CD Pipeline

### 6.1 GitHub Actions

配置文件：`.github/workflows/ci.yml`

**触发**: push 到 main、PR 到 main

**Job 矩阵**:

| OS | Python |
|----|--------|
| ubuntu-latest | 3.12 |
| ubuntu-latest | 3.13 |
| windows-latest | 3.12 |
| macos-latest | 3.12 |

**步骤**:

1. Checkout
2. Setup Python
3. Install dependencies (`pip install -r py/requirements.lock`)
4. Lint (ruff)
5. Type check (mypy)
6. Test (pytest with coverage)
7. Coverage check (>= 60%)
8. Security scan (pip-audit)
9. Docker build
10. Upload coverage to Codecov

### 6.2 本地预检

提交前必须本地运行：

```bash
cd py
ruff check maop/ tests/
ruff format --check maop/ tests/
mypy maop/
pytest --cov=maop --cov-fail-under=60
pip-audit
```

CI 状态对照：

| 检查项 | 本地命令 | CI 强制 |
|--------|----------|---------|
| Lint | `ruff check` | ✅ |
| Format | `ruff format --check` | ✅ |
| Types | `mypy maop/` | ✅ |
| Tests | `pytest` | ✅ |
| Coverage | `>= 60%` | ✅ |
| Security | `pip-audit` | ✅ |

---

## 7. Architecture Decision Records (ADR)

每个重要架构决策必须写 ADR：

- 目录：`docs/adr/`
- 命名：`NNN-<slug>.md`（如 `016-dual-edition-architecture.md`）
- 模板：

```markdown
# ADR-NNN: Title

## Status
Accepted (2026-07-XX)

## Context
<why this decision is needed>

## Decision
<what was decided>

## Consequences
- Positive: ...
- Negative: ...
- Mitigations: ...
```

查看现有 ADR：`docs/adr/README.md`

---

## 8. Release Process

### 8.1 版本号

遵循 Semantic Versioning 2.0.0：`MAJOR.MINOR.PATCH`

### 8.2 CHANGELOG.md

遵循 Keep a Changelog 1.1.0：

```markdown
## [X.Y.Z] — YYYY-MM-DD

### Added
- 新功能

### Changed
- 变更

### Fixed
- Bug 修复

### Security
- 安全修复
```

### 8.3 发布步骤

```bash
# 1. 更新版本号：py/maop/__init__.py 的 __version__
# 2. 更新 CHANGELOG.md
# 3. 提交
git commit -m "chore(release): vX.Y.Z"

# 4. 打 tag
git tag -a vX.Y.Z -m "Release X.Y.Z"

# 5. 推送
git push origin main --tags

# 6. GitHub Release 自动构建 Docker 镜像
# 7. 双包发布到 PyPI（Personal + Enterprise）
```

### 8.4 双线发布

| 版本 | 包定义 | License | 模块 |
|------|--------|---------|------|
| Personal | `pyproject.toml` | MIT | 开源 |
| Enterprise | `pyproject-enterprise.toml` | 商业 license | 含 enterprise/ 模块 |

License 校验：Ed25519 签名（`scripts/generate_license.py`）

---

## 9. Hard Constraints

贡献者必须遵守以下项目级硬性约束，违反将导致 PR 被拒绝：

| 约束 | 说明 |
|------|------|
| 熔断器重置 | 成功执行后必须重置失败计数 |
| 消息状态更新 | 消息处理成功后必须更新投递状态（确保准确反压检测） |
| DAG 循环检测 | DAG 执行必须检查循环依赖并抛出 ValueError 含循环链 |
| Agent 行为 | PipelineOrchestrator 不得硬编码 agent 名称，行为由 AgentMeta 属性决定 |
| 统一执行路径 | 执行引擎必须使用 run() 和 run_plan() 的单一统一路径 |
| Checkpoint 完整性 | Checkpoint 必须保存并恢复完整 DAG 节点状态 |
| 写操作鉴权 | 所有写操作端点必须包含 `require_admin` 校验 |
| 插件校验和 | 插件 manifest 必须包含 SHA-256 校验和 |
| 插件沙箱 | 插件执行必须通过 PluginSandbox 隔离 |
| CI 配置 | CI 配置必须引用 `maop/` 目录 |
| 前端统一 | 前端必须使用统一 Vue3 SPA |
| Docker Compose | 必须使用根目录 docker-compose.yml |
| OpenTelemetry | dashboard 端点必须为 http://otel-collector:4317 |
| 企业版 License | 需要 MAOP_LICENSE_KEY + Ed25519 签名校验 |
| n8n 集成 | 受 FeatureFlag.N8N_INTEGRATION 限制 |

---

## 10. Getting Help

| 渠道 | 链接/路径 |
|------|----------|
| GitHub Issues | https://github.com/Levango7/MAOP/issues |
| GitHub Discussions | https://github.com/Levango7/MAOP/discussions |
| 架构演进 | `docs/adr/` |
| 项目现状 | `docs/comprehensive-audit-report.md` |
| 设计原则 | `DESIGN_RULES.md` |

欢迎贡献代码、提交 issue、参与讨论。请先阅读本指南与 `DESIGN_RULES.md`，确保贡献符合项目方向。
