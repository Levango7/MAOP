# AGENTS.md — MAOP 项目指令

> 本文件为 AtomCode / Claude Code 等 AI 编码代理在本仓库工作时的项目级规范。
> 权威信息以 README.md、ROADMAP.md、CHANGELOG.md、docs/ 为准；本文件只提炼高频约定。

## 项目概述

- **MAOP** = Multi-Agent Orchestration Platform：Python-first 多 Agent 编排平台（Plan-Execute-Verify 循环），当前版本 v5.1.0。
- **定位**：编排**外部 CLI agent** 的治理层（内置 31 个第三方 CLI 适配器），**非自研 agent 运行时**。内置 LLM provider 仅用于对话/分析/建议，不承担执行引擎角色（README.md:6）。
- **双版架构**（ADR-016/017）：MAOP = 个人版（MIT 开源）；MAOS = 企业版（Commercial，私有仓库 Levango7/MAOS，代码不在本仓库）。企业功能通过 `maop-enterprise` 包提供。

## 代码结构

| 路径 | 说明 |
|------|------|
| `py/maop/` | Python 包（主包）：`cli.py` 入口、`engine.py`/`maop_loop.py` 编排引擎、`delegate/` 调度、`core/` 基础设施（agent/memory/mcp/security/scheduling/backends 等子包） |
| `py/tests/` | 后端测试（约 266 个文件 / 7300+ 用例；含 contract/performance/reliability/stability/e2e 子目录） |
| `py/scripts/` | CI 辅助脚本（覆盖率 ratchet、配置漂移审计等） |
| `dashboard-enterprise/` | Vue 3 + Vite 前端（`src/views/` 视图、`src/components/` 组件、`src/i18n/` 国际化、`e2e/` Playwright） |
| `config/` | `agents.yaml`（agent 路由表，**dict 格式**）、`models.yaml`、`rules.yaml` 等 |
| `docs/` | 文档中心（`docs/README.md` 有元索引）；架构决策见 `docs/adr/001-017` |
| `deploy/` | k8s operator / patroni / grafana / otel 部署 |

## 常用命令

后端命令在 `py/` 目录下执行：

```bash
# 安装（开发）
cd py && pip install -e .[dev]

# 测试（本地快速，单进程、不开覆盖率）
python -m pytest tests/ -q -n 0 --no-cov -p no:cacheprovider
# 全量收集检查
python -m pytest tests/ --collect-only -q -n 0 --no-cov

# Lint（全量，含 tests/）
python -m ruff check maop/ tests/

# 类型检查（与 CI 同命令）
python -m mypy maop/ --ignore-missing-imports --no-warn-unused-ignores

# 覆盖率门禁（CI 用，本地可选）
python scripts/check_coverage_ratchet.py
```

前端命令在 `dashboard-enterprise/` 下执行：

```bash
cd dashboard-enterprise
npm run lint        # eslint（需 0 error）
npm test            # vitest
npm run build       # vite build
```

## 代码规范（硬性）

- **禁止**裸 `except:` / `except: pass`（ruff E722 全绿是门禁）；禁止 `eval()`（用 `maop_plan.py` 的 AST `safe_eval`）；禁止 `shell=True`（一律 list 形式，防命令注入）。
- **异步任务必须持有引用**：`asyncio.create_task` / `ensure_future` / `loop.create_task` 的结果要存入模块级集合（如 `_bg_tasks: set[asyncio.Task[Any]]`）并 `add_done_callback(set.discard)`，防止任务被 GC（"Task was destroyed but it is pending"）。
- **错误响应统一格式**：dashboard 端点一律用 `maop.dashboard.error_handler.handle_api_errors` 装饰器，输出 ErrorSchema（`status/error/code/detail/request_id`）；不要手写裸 `JSONResponse({"status": "error", ...})`（auth.py 历史遗留正在收敛）。
- **双版差异走 FeatureFlag gate**：禁止直接比较 `get_edition() == ENTERPRISE`，用 `has_feature(FeatureFlag.X)` / `require_feature(...)`（`py/maop/config/edition.py`）。
- **企业代码不在本仓库**：涉及 `maop.enterprise` 的测试必须 `pytest.importorskip("maop.enterprise")` 守卫，否则个人版 CI 会收集失败。
- 前端：ESLint（`eslint.config.js`）0 error；i18n 文案放 `src/i18n/view-*.js`（不要硬编码 UI 文案）；组件契约见 `docs/frontend-style-guide.md` 与 `docs/DESIGN_RULES.md`。
- Python 目标版本 >=3.10（pyproject.toml `requires-python`）；`py/pyproject.toml` 中已忽略 E402/BLE001/S110/F403，新增豁免需注释理由。

## 提交与发布规范

- 提交信息用 Conventional Commits：`fix:` / `feat:` / `docs:` / `chore:` / `refactor:`（仓库现有历史均为此风格）。
- 提交前至少跑：`ruff check` + 相关 `pytest`；涉及前端则跑 `npm run lint`。
- 发布节奏（CHANGELOG.md 头部规范）：patch 每周≤1、minor 每两周≤1、major 每季度≤1；发布前 checklist：CI 全绿、CHANGELOG 更新、版本号在 pyproject/__init__/Dockerfile/package.json 同步、至少 1 个完整工作日 staging 验证、无未解决 P0/P1。
- 版本号同步点：`py/pyproject.toml`、`py/maop/__init__.py`、`py/Dockerfile`、`dashboard-enterprise/package.json`、`deploy/k8s/operator/Chart.yaml` 等。

## 环境注意事项

- 仓库 `.gitattributes` 强制 LF；Windows 贡献者若配置 `core.autocrlf=true`，改动后 `git status` 出现大量假脏属正常，提交前用 `git diff` 确认真实改动。
- `.venv/`、`.venv2/`、`.mypy_cache/`、`.pytest_cache/`、`.ruff_cache/` 均不入库（.gitignore 已覆盖）。
- 生产环境：JWT_SECRET 缺失会拒绝启动（fail-closed）；Redis/PG 密码用 `${VAR:?}` 强制语法（docker-compose）。
