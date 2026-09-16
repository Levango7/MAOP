# MAOP 项目综合评估报告

> **评估对象**：`F:\Nexus\MAOP`  
> **评估日期**：2026-09-17  
> **评估方式**：6 维度并行只读评估，证据导向（每项发现标注真实文件路径+行号）  
> **代码版本**：v5.2.0（`py/maop/__init__.py:3`）  
> **项目规模**：Python 419 文件/131,140 行 + 前端 212 文件/60,712 行  

---

## 一、执行摘要

本次评估对 MAOP 项目进行了 6 维度全面只读审查，共发现 **92 项问题**（高风险 19 项、中风险 38 项、低风险 35 项），同时识别出多项安全与工程实践亮点。

### 风险总览

| 维度 | 高风险 | 中风险 | 低风险 | 小计 |
|------|--------|--------|--------|------|
| 架构与模块层 | 2 | 5 | 4 | 11 |
| 代码质量与债务层 | 4 | 8 | 6 | 18 |
| 测试与 CI 层 | 3 | 5 | 4 | 12 |
| 安全层 | 0 | 5 | 6 | 11 |
| 文档与契约层 | 4 | 6 | 5 | 15 |
| 运维与部署层 | 6 | 9 | 10 | 25 |
| **合计** | **19** | **38** | **35** | **92** |

### 高风险发现清单（19 项）

| 编号 | 维度 | 标题 | 证据 |
|------|------|------|------|
| ARCH-H1 | 架构 | core/agent/__init__.py 惰性加载 150+ 符号，"上帝包"门面 | `py/maop/core/agent/__init__.py:26-182` |
| ARCH-H2 | 架构 | 路由层直接穿透到 core 层，服务层几乎不存在 | `py/maop/dashboard/services/` 仅 1 文件 |
| QUAL-H1 | 代码质量 | supervisor.py 1572 行，远超 800 行阈值 | `py/maop/core/scheduling/supervisor.py` |
| QUAL-H2 | 代码质量 | 12 个 Python 文件超过 800 行，75 个超过 500 行 | `wc -l` 统计 |
| QUAL-H3 | 代码质量 | 前端 TS strict 模式形同虚设——实际为 JS 项目 | `dashboard-enterprise/tsconfig.json:6` + 0 个 Vue 用 lang="ts" |
| QUAL-H4 | 代码质量 | sqlite-vec 默认依赖缺失于 requirements.lock | `py/pyproject.toml:46` vs `py/requirements.lock`（缺失） |
| TEST-H1 | 测试CI | 前端 vitest 覆盖率门禁阈值过低（40/40/30/40） | `dashboard-enterprise/vitest.config.js:19-24` |
| TEST-H2 | 测试CI | CI 单一 workflow 缺少 release/security/PR 专用 workflow 与 dependabot | `.github/` 仅 `workflows/ci.yml` |
| TEST-H3 | 测试CI | E2E 测试仅单浏览器（chromium），缺跨浏览器与移动端 | `dashboard-enterprise/playwright.config.js:13-18` |
| DOC-H1 | 文档契约 | CHANGELOG.md 中 v5.0.0 被错误标注为 v5.1.0，v5.1.0 重复出现 | `CHANGELOG.md:162` 和 `:261` |
| DOC-H2 | 文档契约 | ROADMAP.md 中 v5.2.0 状态自相矛盾（"已发布"与"进行中"并存） | `ROADMAP.md:10` vs `:128-130` |
| DOC-H3 | 文档契约 | 7 个核心文档版本号过时（标 v5.1.0，代码已 v5.2.0） | `docs/api-reference.md:3` 等 7 处 |
| DOC-H4 | 文档契约 | README 适配器计数与 CHANGELOG 修复记录不一致 | `README.md:8` vs `CHANGELOG.md:65` |
| OPS-H1 | 运维部署 | Dockerfile 基础镜像未钉定到 digest | `py/Dockerfile:16` |
| OPS-H2 | 运维部署 | K8s 缺少 NetworkPolicy | `deploy/k8s/operator/templates/` 无 networkpolicy.yaml |
| OPS-H3 | 运维部署 | K8s 缺少 PodDisruptionBudget | grep `PodDisruptionBudget` 无结果 |
| OPS-H4 | 运维部署 | PM2 配置完全缺失 | glob `**/ecosystem*` 无结果 |
| OPS-H5 | 运维部署 | 三套部署形态端口不一致 | `docker-compose.yml:75`（9079）vs `values.yaml:70`（8443） |
| OPS-H6 | 运维部署 | etcd 生产环境允许匿名认证 | `docker-compose.prod.yml:759` |

---

## 二、维度详述

### 2.1 架构与模块层（2H / 5M / 4L）

#### 🔴 ARCH-H1：core/agent/__init__.py 惰性加载 150+ 符号，构成"上帝包"门面
- **风险等级**：H
- **描述**：`core/agent/__init__.py` 通过 `__getattr__` 惰性加载 re-export 超过 150 个符号（`__all__` 含 155 个），`_SYMBOL_TO_MODULE` 映射表 200+ 条目。虽然惰性加载避免了循环导入，但庞大的符号映射表本身成为维护负担，符号来源不透明。
- **证据**：`py/maop/core/agent/__init__.py:26-182`（`__all__`）、`:185-392`（`_SYMBOL_TO_MODULE`）、`:395-413`（`__getattr__`）
- **改进建议**：逐步废弃顶层 re-export，引导调用方直接从子模块导入；在 v6.0.0 移除映射表。

#### 🔴 ARCH-H2：路由层直接穿透到 core 层，服务层几乎不存在
- **风险等级**：H
- **描述**：`dashboard/services/` 仅含 `upgrade_service.py`，路由处理函数直接调用 `core/agent/` 下业务逻辑（60 处 import），跳过服务层抽象。导致路由函数包含大量业务编排代码，难以独立测试。
- **证据**：`py/maop/dashboard/services/` 仅 2 文件；`py/maop/dashboard/routers/agent_collaboration.py:86-90` 直接 import 3 个 core 类
- **改进建议**：为每个业务域提取 Service 类，路由层仅负责 HTTP 转换 + 调用 Service。

#### 🟡 ARCH-M1：路由前缀定义风格不统一（prefix= vs 装饰器全路径）
- **风险等级**：M
- **描述**：72 个路由文件中约 40 个使用 `prefix="/api/xxx"`，约 30 个使用 `APIRouter()` + 装饰器全路径。混合风格不一致。
- **证据**：`routers/feedback.py:40`（风格 A）vs `routers/control.py:23,41`（风格 B）；grep `APIRouter(prefix=` 41 处 vs `APIRouter()` 31 处
- **改进建议**：统一采用 `prefix=` 参数风格。

#### 🟡 ARCH-M2：多个路由文件共享相同前缀，路由分散
- **风险等级**：M
- **描述**：`/api/agents` 被 4 个路由文件共享，`/api/evolution` 端点分散在 2 个文件中。
- **证据**：`routers/agents/crud.py:26`、`routers/agents/evolution.py:29`、`routers/agents/routes.py:25`、`routers/agents/memory.py:15` 均为 `prefix="/api/agents"`
- **改进建议**：在 `agents/__init__.py` 中聚合子路由。

#### 🟡 ARCH-M3：前后端无 API 契约共享，前端手动拼接 fetch 调用
- **风险等级**：M
- **描述**：前端未利用 FastAPI OpenAPI schema 生成类型，API 路径散落在 51 个 Vue 视图中。
- **证据**：`dashboard-enterprise/src/stores/api.js:10-13` 仅 2 个端点常量；`package.json` 无 `openapi-typescript`
- **改进建议**：引入 `openapi-typescript` 自动生成前端类型，建立集中 API 端点常量文件。

#### 🟡 ARCH-M4：API 版本管理为别名复制而非真正版本化路由
- **风险等级**：M
- **描述**：`_register_v1_aliases()` 为所有 `/api/*` 创建 `/api/v1/*` 别名指向同一函数，无法实现 v1→v2 破坏性变更隔离。
- **证据**：`py/maop/dashboard/_register_routes.py:764-790`
- **改进建议**：如需真正版本化，为每个版本创建独立 `APIRouter(prefix="/api/v1")`。

#### 🟡 ARCH-M5：system/__init__.py 手动展平子路由，绕过 include_router
- **风险等级**：M
- **描述**：通过 `router.routes.append(_route)` 手动展平而非 `include_router`，导致 OpenAPI tags/prefix 信息丢失。
- **证据**：`py/maop/dashboard/routers/system/__init__.py:53-59`
- **改进建议**：改用 `router.include_router(sub.router)` 标准机制。

#### 🟢 ARCH-L1~L4：正面发现
- **L1**：core/agent/ 18 个子模块无循环依赖，依赖方向合理（`core/` 不反向依赖 `dashboard/`）
- **L2**：adapters/__init__.py 有优秀 docstring，ARCHITECTURE.md 记录 116 个模块
- **L3**：DataProxy 使用 Mixin 组合模式，职责分离良好
- **L4**：命名空间包支持企业版扩展，特性标志门控 + ImportError 优雅降级

---

### 2.2 代码质量与债务层（4H / 8M / 6L）

#### 🔴 QUAL-H1：supervisor.py 1572 行，远超 800 行阈值
- **风险等级**：H
- **证据**：`py/maop/core/scheduling/supervisor.py`（1572 行）；`docs/p1-code-evaluation.md:31`（P1 暂缓项）
- **改进建议**：按调度器职责拆分为 `supervisor_core.py`、`supervisor_health.py`、`supervisor_rebalance.py`。

#### 🔴 QUAL-H2：12 个 Python 文件超过 800 行，75 个超过 500 行
- **风险等级**：H
- **描述**：Top5：`supervisor.py`（1572）、`001_initial_schema.py`（938）、`preset_agents.py`（919）、`vector_store.py`（908）、`engine.py`（898）
- **证据**：`wc -l` 统计；`docs/p23-backend-audit.md:17`
- **改进建议**：优先拆分 `engine.py`、`dispatch_core.py`、`cache.py` 等核心模块。

#### 🔴 QUAL-H3：前端 TypeScript strict 模式形同虚设
- **风险等级**：H
- **描述**：`tsconfig.json` 设 `strict: true`，但 212 个源文件中仅 1 个 `.ts`（`env.d.ts`），132 个 `.js` + 79 个 `.vue` 均未用 `lang="ts"`。
- **证据**：`dashboard-enterprise/tsconfig.json:6`；0 个 Vue 文件使用 `lang="ts"`
- **改进建议**：逐步迁移到 TS 或承认 JS 项目并移除 `typescript` devDependency。

#### 🔴 QUAL-H4：sqlite-vec 默认依赖缺失于 requirements.lock
- **风险等级**：H
- **描述**：`sqlite-vec>=0.1.6` 在 pyproject.toml 是默认依赖，但 requirements.lock 中完全缺失。CI SBOM/pip-audit 不会包含 sqlite-vec，可能漏审 CVE。
- **证据**：`py/pyproject.toml:46` vs `py/requirements.lock`（grep `sqlite-vec` 无结果）
- **改进建议**：立即在 requirements.lock 中添加 sqlite-vec>=0.1.6；建立 CI 检查确保 lock 与 pyproject.toml 同步。

#### 🟡 QUAL-M1~M8：中风险发现
- **M1**：mypy 默认宽松，strict 仅覆盖 6 个新模块（`pyproject.toml:169-170`）
- **M2**：ESLint 未集成 @typescript-eslint（`eslint.config.js:13-16`）
- **M3**：lint 脚本仅检查 `.js`/`.vue`，遗漏 `.ts`（`package.json:16-17`）
- **M4**：无集中 tech-debt.md，债务分散在多份审核报告
- **M5**：6 个函数圈复杂度 >20（`docs/p23-backend-audit.md:60-99`）
- **M6**：前端 8 个 Vue 文件超过 800 行（Agents.vue 1579 行最大）
- **M7**：Python 150 个函数无返回类型注解（96% 覆盖率）
- **M8**：requirements.lock 缺失多个 dev 依赖（pytest-cov/xdist/timeout 等）

#### 🟢 QUAL-L1~L6：低风险发现
- **L1**：mypy `warn_unused_ignores = false` 削弱类型守卫
- **L2**：ruff 豁免 BLE001/S110 削弱异常检查
- **L3**：`vue/no-v-html` 关闭虽有说明，应改为 sanitize 强制
- **L4**：源码 TODO/FIXME 极少（仅 1 处），债务在文档中登记
- **L5**：历史死代码已清理，当前无废弃标记
- **L6**：`hvac` 在 requirements.txt 中但不在 pyproject.toml 和 requirements.lock 中

---

### 2.3 测试与 CI 层（3H / 5M / 4L）

#### 🔴 TEST-H1：前端 vitest 覆盖率门禁阈值过低（40/40/30/40）
- **风险等级**：H
- **描述**：后端 Python ratchet FLOOR=80% vs 前端 branches=30%，前后端质量门禁严重不对齐。
- **证据**：`dashboard-enterprise/vitest.config.js:19-24`
- **改进建议**：引入 ratchet 机制渐进抬升到 70%+。

#### 🔴 TEST-H2：CI 单一 workflow 缺少 release/security/PR 专用 workflow 与 dependabot
- **风险等级**：H
- **描述**：816 行单文件塞 14 个 job，无 dependabot/CODEOWNERS/PR 模板/nightly workflow。
- **证据**：`.github/` 仅 `workflows/ci.yml` 1 个文件
- **改进建议**：拆分 `release.yml`/`security.yml`/`nightly.yml`；添加 dependabot.yml + CODEOWNERS。

#### 🔴 TEST-H3：E2E 测试仅单浏览器（chromium）
- **风险等级**：H
- **描述**：企业级多租户 dashboard 缺 Firefox/Safari/移动端验证。
- **证据**：`dashboard-enterprise/playwright.config.js:13-18`（projects 仅 1 项）
- **改进建议**：增加 firefox 和 webkit 项目 + 移动端视口测试。

#### 🟡 TEST-M1~M5：中风险发现
- **M1**：覆盖率 ratchet 企业包门禁在本仓永远跳过（`ci.yml:263-272`）
- **M2**：flaky 测试仅靠 reruns=3 兜底，无显式标记与隔离（`ci.yml:193`）
- **M3**：测试矩阵排除 macOS 旧 Python 缺补偿验证（`ci.yml:114-120`）
- **M4**：soak 测试目录无有效测试文件，CI 无 soak job
- **M5**：前端 mock fetch 易漂移，缺 MSW/拦截层

#### 🟢 TEST-L1~L4：低风险发现
- **L1**：conftest.py autouse fixture 链复杂，存在隐性执行顺序依赖
- **L2**：pre-commit 的 pytest-fast hook 与 CI 测试集不一致
- **L3**：Playwright retries=1 可能掩盖真实 flaky
- **L4**：CI 无 Slack/邮件通知机制

---

### 2.4 安全层（0H / 5M / 6L）

> **安全层整体评价：优秀**。项目经过多轮安全审计（代码中大量 P0/P1 fix 注释），自实现 JWT + API Key 认证体系质量高。未发现高风险安全问题。

#### 🟡 SEC-M1~M5：中风险发现
- **M1**：速率限制使用内存 token bucket，多 worker 部署不共享状态（`middleware.py:364-373`）
- **M2**：JWT 撤销黑名单文件持久化，多实例部署可能不一致（`auth.py:226`）
- **M3**：credential_vault 降级模式在非生产环境允许 base64 混淆（`credential_vault.py:116-134`）
- **M4**：sandbox.py 不提供 OS 级隔离（`sandbox.py:3-38`）
- **M5**：缺少 Dependabot/Renovate 自动依赖更新配置

#### 🟢 SEC-L1~L6：低风险发现
- **L1**：.env.example 包含弱占位符值（`dummy`、`change-me-in-production`）
- **L2**：少数 SQL 使用 f-string（DDL 语句，表名为常量，风险可控）
- **L3**：LDAP bind 密码存储在配置中
- **L4**：CORS 在非生产环境允许通配符
- **L5**：无专门 CSRF 中间件（JWT Bearer token 降低 CSRF 风险）
- **L6**：API Key 校验使用 SHA-256（非恒定时间比较，实际风险极低）

#### ✅ 安全亮点（22 项）
- API Key 256 位熵 + SHA-256 哈希存储 + scopes/IP 白名单/速率限制
- PBKDF2-HMAC-SHA256 600k 迭代（符合 OWASP 2023）
- JWT 密钥强度校验 + 弱密钥黑名单 + 生产环境强制环境变量
- credential_vault Fernet 加密 + 生产拒绝降级 + 审计日志
- 完整安全 headers（CSP/HSTS/X-Frame-Options DENY/COOP/CORP）
- SSRF 防护 + 文件上传白名单 + 路径遍历防护
- CI 安全扫描全覆盖：pip-audit + bandit + gitleaks + trivy + cyclonedx SBOM
- TLS 拒绝 SSLv2/v3 + mTLS fail-fast

---

### 2.5 文档与契约层（4H / 6M / 5L）

#### 🔴 DOC-H1：CHANGELOG.md 中 v5.0.0 被错误标注为 v5.1.0
- **风险等级**：H
- **描述**：`## [5.1.0] — 2026-08-14` 出现两次（行 162 和 261），行 261 内容实为 v5.0.0（breaking changes），CHANGELOG 完全缺失 `## [5.0.0]` 标题。
- **证据**：`CHANGELOG.md:162` 和 `:261`；`ROADMAP.md:12`（v5.0.0 = 2026-08-11 major）
- **改进建议**：将行 261 修正为 `## [5.0.0] — 2026-08-11`。

#### 🔴 DOC-H2：ROADMAP.md 中 v5.2.0 状态自相矛盾
- **风险等级**：H
- **描述**：行 10 声明"已发布：v5.2.0"，行 128 标注"进行中"，行 130 标注"开发中，功能尚未可用"。
- **证据**：`ROADMAP.md:10` vs `:128-130`；验收标准 `:145-148` 均未勾选
- **改进建议**：统一 v5.2.0 状态。

#### 🔴 DOC-H3：7 个核心文档版本号过时（标 v5.1.0，代码已 v5.2.0）
- **风险等级**：H
- **证据**：`docs/api-reference.md:3`、`docs/configuration.md:7`、`docs/deployment.md:5`、`docs/user-guide.md:31`、`docs/contributing.md:3` 等 7 处
- **改进建议**：批量更新至 v5.2.0；CI 加入文档版本号 drift 检查。

#### 🔴 DOC-H4：README 适配器计数与 CHANGELOG 修复记录不一致
- **风险等级**：H
- **证据**：`README.md:8`（25 个开箱可用）vs `CHANGELOG.md:65`（修正为 26 个开箱可用）
- **改进建议**：核实实际 agent 数量，统一 README 与 CHANGELOG。

#### 🟡 DOC-M1~M6：中风险发现
- **M1**：API_CHANGELOG.md 缺少 v5.2.0 条目
- **M2**：api-reference.md 声称 448 个端点但标注"计数待复核"（实际 554 处路由定义）
- **M3**：deployment.md 中 Helm Chart 路径 `./helm/` 与实际 `deploy/k8s/operator/` 不一致
- **M4**：deployment.md 中 K8s 镜像标签版本号过时（5.1.0 vs 5.2.0）
- **M5**：前端无 TypeScript 类型定义，缺失 API 类型契约保障
- **M6**：database-schema.md 自认仅覆盖 53/101 张表

#### 🟢 DOC-L1~L5：低风险发现
- **L1**：docs/ 实际 111 个 .md 文件（非任务描述的 137 个）
- **L2**：archive/README.md ADR 范围标注过时（001-017 vs 实际 001-021）
- **L3**：p23-docs-audit.md 版本号一致性审计结论过时
- **L4**：缺少独立的数据库迁移历史文档
- **L5**：api-reference.md 未文档化 MAOP_EXPOSE_DOCS 生产环境覆盖

---

### 2.6 运维与部署层（6H / 9M / 10L）

#### 🔴 OPS-H1：Dockerfile 基础镜像未钉定到 digest
- **风险等级**：H
- **证据**：`py/Dockerfile:16`（`ARG PYTHON_IMAGE=python:3.13-slim`，注释提到 digest pinning 但未实现）
- **改进建议**：改为 `python:3.13-slim@sha256:<digest>`。

#### 🔴 OPS-H2：K8s 缺少 NetworkPolicy
- **风险等级**：H
- **描述**：多租户场景下缺乏网络隔离，违反零信任网络原则。
- **证据**：`deploy/k8s/operator/templates/` 无 networkpolicy.yaml
- **改进建议**：新增 NetworkPolicy 限制 ingress 仅允许 webhook/metrics 端口。

#### 🔴 OPS-H3：K8s 缺少 PodDisruptionBudget
- **风险等级**：H
- **证据**：grep `PodDisruptionBudget` 在 `deploy/` 无结果
- **改进建议**：新增 PDB 设置 `minAvailable: 1`。

#### 🔴 OPS-H4：PM2 配置完全缺失
- **风险等级**：H
- **描述**：裸机/VM 部署缺乏进程管理标准化方案。
- **证据**：glob `**/ecosystem*` 和 `**/*pm2*` 均无结果
- **改进建议**：创建 `ecosystem.config.js` 定义 dashboard/agent-exec/queue-worker 三进程。

#### 🔴 OPS-H5：三套部署形态端口不一致
- **风险等级**：H
- **证据**：`docker-compose.yml:75`（9079）vs `deploy/k8s/operator/values.yaml:70`（8443）
- **改进建议**：统一定义端口常量，三种部署形态一致使用。

#### 🔴 OPS-H6：etcd 生产环境允许匿名认证
- **风险等级**：H
- **描述**：Patroni DCS 允许匿名访问，可篡改集群 leader key 导致脑裂。
- **证据**：`docker-compose.prod.yml:759`（`ALLOW_NONE_AUTHENTICATION: "yes"`）
- **改进建议**：将 etcd 认证启用纳入部署自动化脚本。

#### 🟡 OPS-M1~M9：中风险发现
- **M1**：pip 安装硬编码清华源（`Dockerfile:50-51`）
- **M2**：ClusterRole 对 secrets 有 delete 权限（`role.yaml:11`）
- **M3**：K8s 缺少 MAOP 应用本身部署（仅 Operator）
- **M4**：环境变量在 Docker 与 K8s 间未对齐（20+ vs 3）
- **M5**：依赖服务声明不一致（Docker 有 depends_on，K8s 无 initContainer）
- **M6**：.env.example 包含弱密码默认值
- **M7**：备份频率默认 1 小时可能不足
- **M8**：Patroni 配置文件为参考文档不生效
- **M9**：缺少备份验证机制（restore drill）

#### 🟢 OPS-L1~L10：低风险发现
- **L1**：HEALTHCHECK 仅覆盖 dashboard 服务
- **L2**：ValidatingWebhook 缺少 timeoutSeconds 和 namespaceSelector
- **L3**：Grafana SLO 仪表盘缺失
- **L4**：Prometheus 告警规则文件冗余风险
- **L5**：.env.example 未覆盖 K8s 部署所需变量
- **L6**：环境变量存在 deprecated 别名未清理
- **L7**：.env.sandbox.example 覆盖变量较少
- **L8**：迁移脚本 002 未实现独立 downgrade SQL
- **L9**：003 迁移在非 PG 方言被 stamp 为已应用
- **L10**：OTel Collector Jaeger exporter 使用 insecure TLS

---

## 三、改进优先级矩阵

### P0 — 立即修复（影响安全/数据完整性/供应链）

| 编号 | 标题 | 维度 | 预估工作量 |
|------|------|------|-----------|
| OPS-H6 | etcd 匿名认证 | 运维 | 2h |
| QUAL-H4 | sqlite-vec 缺失于 requirements.lock | 代码质量 | 0.5h |
| DOC-H1 | CHANGELOG v5.0.0 版本标注错误 | 文档 | 1h |
| DOC-H2 | ROADMAP v5.2.0 状态矛盾 | 文档 | 1h |
| DOC-H3 | 7 个文档版本号过时 | 文档 | 2h |
| DOC-H4 | README 适配器计数不一致 | 文档 | 1h |

### P1 — 高优先级（影响可维护性/可靠性/安全性）

| 编号 | 标题 | 维度 | 预估工作量 |
|------|------|------|-----------|
| OPS-H1 | 基础镜像 digest 钉定 | 运维 | 2h |
| OPS-H2 | K8s NetworkPolicy | 运维 | 3h |
| OPS-H3 | K8s PodDisruptionBudget | 运维 | 1h |
| OPS-H4 | PM2 配置创建 | 运维 | 4h |
| OPS-H5 | 三套部署端口统一 | 运维 | 4h |
| TEST-H2 | CI workflow 拆分 + dependabot | 测试CI | 8h |
| TEST-H1 | 前端覆盖率门禁抬升 | 测试CI | 4h |
| TEST-H3 | E2E 跨浏览器覆盖 | 测试CI | 8h |
| QUAL-H1 | supervisor.py 拆分 | 代码质量 | 8h |
| QUAL-H3 | 前端 TS/JS 策略决策 | 代码质量 | 4h |
| ARCH-H2 | 服务层提取 | 架构 | 16h |
| ARCH-H1 | core/agent re-export 废弃 | 架构 | 8h |

### P2 — 中优先级（提升工程质量）

| 编号 | 标题 | 维度 | 预估工作量 |
|------|------|------|-----------|
| QUAL-H2 | 12 个超大文件拆分 | 代码质量 | 16h |
| ARCH-M1~M5 | 路由风格统一/聚合/契约/版本化 | 架构 | 12h |
| SEC-M1 | Redis-backed 限流 | 安全 | 4h |
| SEC-M2 | JWT 撤销黑名单共享存储 | 安全 | 4h |
| SEC-M5 | Dependabot 配置 | 安全 | 1h |
| OPS-M2~M5 | K8s 权限收窄/应用部署/环境变量对齐 | 运维 | 12h |
| DOC-M1~M6 | API 变更日志/端点数/部署路径/Schema 补全 | 文档 | 8h |
| TEST-M1~M5 | flaky 管理/矩阵补偿/soak/MSW | 测试CI | 12h |

### P3 — 低优先级（渐进优化）

- QUAL-M1~M8：mypy strict 迁移、ESLint TS 集成、tech-debt.md 创建、高 CC 函数拆分、Vue 大文件拆分
- SEC-L1~L6：占位符强化、SQL f-string 标注、LDAP vault 引导、CORS 日志、CSRF 评估
- DOC-L1~L5：文件数更新、ADR 范围、审计归档、迁移历史文档、EXPOSE_DOCS 文档
- OPS-L1~L10：健康检查说明、webhook 超时、SLO 仪表盘、告警合并、env 覆盖、deprecated 清理等

---

## 四、正面亮点汇总

### 安全层（22 项亮点）
- API Key 256 位熵 + SHA-256 哈希 + scopes/IP 白名单/速率限制/软撤销/轮换
- PBKDF2-HMAC-SHA256 600k 迭代（OWASP 2023 推荐）
- JWT 密钥强度校验 + 弱密钥黑名单 + 生产强制环境变量 + alg 全验证
- credential_vault Fernet 加密 + 生产拒绝降级 + 审计日志 + 掩码预览
- 完整安全 headers（CSP/HSTS/X-Frame-Options DENY/COOP/CORP/Permissions-Policy）
- SSRF 防护（拒绝 loopback/private/元数据端点 + DNS rebinding 防护）
- 文件上传 20MB 限制 + 扩展名白名单 + 路径遍历防护
- CI 安全扫描全覆盖（pip-audit + bandit + gitleaks + trivy + cyclonedx SBOM）
- TLS 拒绝 SSLv2/v3 + mTLS fail-fast + CORS 生产 fail-closed

### 架构层（4 项亮点）
- core/ 18 个子模块无循环依赖，依赖方向正确
- DataProxy Mixin 组合模式，职责分离良好
- 命名空间包支持企业版扩展，特性标志门控
- adapters 有优秀 docstring + ARCHITECTURE.md 模块清单

### 测试 CI 层（5 项亮点）
- CI 安全扫描链完整（5 维度覆盖，分级门禁合理）
- 覆盖率 ratchet 机制精良（渐进抬升 + 浮点免疫 + 防自膨胀）
- 测试隔离严谨（autouse fixture 隔离数据目录 + 重置单例）
- CI 并发控制 + 最小权限
- pytest-rerunfailures + pytest-timeout flaky 兜底

### 运维部署层（4 项亮点）
- Docker Compose 部署链路成熟（多阶段构建 + 非 root + cap_drop + 日志轮转）
- 监控告警达 SRE 最佳实践（SLO 多燃烧率 + 三级告警 + 多通道通知）
- 数据库迁移设计严谨（破坏性降级保护 + 幂等性 + 自动迁移）
- 容灾方案完整（Patroni 3 节点同步复制 RPO=0 + WAL-G PITR + S3 备份）

### 代码质量层（6 项亮点）
- 源码 TODO/FIXME 极少（仅 1 处）
- 历史死代码已清理
- Python 类型标注覆盖率 96%
- pre-commit 集成完整
- CVE 审计记录详尽
- ESLint 9.x flat config 迁移完成

### 文档契约层（5 项亮点）
- FastAPI OpenAPI 自动文档 + 生产 fail-closed
- ADR 体系完整（21 个 ADR，状态管理规范）
- 代码版本号五处统一（__init__.py/pyproject.toml/package.json/Chart.yaml/values.yaml）
- 迁移文件结构清晰（版本链完整 + 方言守卫）
- 文档归档规范（26 个归档文件有分类索引）

---

## 五、维度间交叉发现

### 5.1 前后端类型契约断裂（ARCH-M3 + QUAL-H3 + DOC-M5）
三个维度独立发现同一根因：前端使用纯 JavaScript，未利用后端 FastAPI OpenAPI schema 自动生成类型。这导致：
- 架构层：API 路径散落在 51 个 Vue 视图中（ARCH-M3）
- 代码质量：TS strict 模式形同虚设（QUAL-H3）
- 文档契约：无 API 类型契约保障（DOC-M5）

**统一改进方案**：引入 `openapi-typescript` 从 FastAPI `/api/openapi.json` 生成前端 TS 类型 + 建立集中 API 端点常量 + CI schema diff 检查。

### 5.2 CI workflow 单一化（TEST-H2 + SEC-M5）
测试 CI 层和安全层独立发现 CI 缺少 dependabot 和 workflow 拆分。统一改进：添加 `.github/dependabot.yml` + 拆分 `release.yml`/`security.yml`/`nightly.yml`。

### 5.3 多实例部署状态共享（SEC-M1 + SEC-M2）
安全层发现速率限制和 JWT 撤销黑名单均使用进程本地存储，多 worker/多实例部署下失效。统一改进：使用 Redis-backed 共享存储。

### 5.4 文档版本号系统性过时（DOC-H3 + DOC-M1~M4 + OPS 相关）
文档与契约层发现 7 个文档版本号过时，同时影响部署文档中的镜像标签和 Helm Chart 路径。统一改进：批量更新 + CI drift 检查。

---

## 六、改进路线图建议

### 阶段一：紧急修复（1-2 天）
1. etcd 匿名认证（OPS-H6）
2. sqlite-vec 加入 requirements.lock（QUAL-H4）
3. CHANGELOG/ROADMAP/文档版本号修正（DOC-H1~H4）

### 阶段二：安全与可靠性（3-5 天）
1. K8s NetworkPolicy + PDB（OPS-H2, H3）
2. 基础镜像 digest 钉定（OPS-H1）
3. PM2 配置创建 + 端口统一（OPS-H4, H5）
4. CI workflow 拆分 + dependabot（TEST-H2, SEC-M5）
5. 前端覆盖率门禁抬升（TEST-H1）

### 阶段三：架构优化（1-2 周）
1. 服务层提取（ARCH-H2）
2. supervisor.py 及超大文件拆分（QUAL-H1, H2）
3. core/agent re-export 废弃（ARCH-H1）
4. 前后端类型契约建立（ARCH-M3 + QUAL-H3 统一方案）
5. E2E 跨浏览器覆盖（TEST-H3）

### 阶段四：渐进提升（持续）
1. mypy strict 迁移
2. 路由风格统一
3. flaky 测试管理
4. Vue 大文件拆分
5. 文档补全

---

## 七、附录

### 评估方法
- 6 个评估 subagent 并行执行（并行上限 5，分 2 批次）
- 每个 subagent 独立只读评估，不修改任何文件
- 所有发现标注真实文件路径和行号
- 风险分级标准：H=高风险（影响安全/数据/可用性）、M=中风险（影响可维护性/可靠性）、L=低风险（渐进优化）

### 评估覆盖范围
- Python 后端：419 文件 / 131,140 行
- 前端：212 文件 / 60,712 行
- 配置文件：40+ 个（Dockerfile/docker-compose/K8s/Helm/CI/env/monitoring）
- 文档：111 个 .md 文件
- 测试：335 Python + 56 vitest + 4 Playwright + 8 E2E

### 评估维度与发现统计

| 维度 | H | M | L | 小计 | 评估时长 |
|------|---|---|---|------|---------|
| 架构与模块层 | 2 | 5 | 4 | 11 | 6m44s |
| 代码质量与债务层 | 4 | 8 | 6 | 18 | ~8m |
| 测试与 CI 层 | 3 | 5 | 4 | 12 | 5m47s |
| 安全层 | 0 | 5 | 6 | 11 | 7m04s |
| 文档与契约层 | 4 | 6 | 5 | 15 | 11m23s |
| 运维与部署层 | 6 | 9 | 10 | 25 | 6m29s |
| **合计** | **19** | **38** | **35** | **92** | ~45m |

---

*报告生成时间：2026-09-17*  
*评估团队：6 个并行评估 subagent + 1 个汇总 agent*  
*评估状态：✅ 全部完成*