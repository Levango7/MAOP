# MAOP 多智能体编排平台 — 全面审查报告

> 审查日期：2026-07-18 ｜ 审查方式：4 个并行子代理分维度深审 + 主代理逐项复核取证 + 沙箱实测测试套件
> 项目版本：pyproject 3.2.2 ｜ 规模实测：178 个 Python 文件 / 42,554 行，88 个 PS1 / 12,907 行（含 archive），JS 2,064 行

---

## 一、总体结论

MAOP 是一个**有真实架构野心、部分基础设施扎实，但"宣称"系统性跑在"实现"前面**的项目。两周内由 AI agent 高速迭代成型，六层架构划分清晰、核心 Plan→Execute→Verify 环路可用、约 1,900+ 个有真实断言的测试——这些值得肯定。但当前状态**不可用于生产**：

- 归档中混入**活体 JWT 签名密钥和真实凭证数据库**，持有本 zip 即可伪造管理员令牌、远程执行任意任务（P0）
- 多个宣称的核心能力（Evolve 自进化、fallback 链、控制平面、skip_verify）存在**逻辑断裂或空转**
- 文档/README/CHANGELOG 的关键数字（测试数、agent 数、CI 配置、"all passing"）**与实际不符**，测试套件在两个平台上均为红

| 维度 | 评级 | 说明 |
|---|---|---|
| 架构设计 | B+ | 分层清晰、ADR 文化好，但层间有绕行和循环依赖 |
| 基础设施实现 | B− | core/ 约一半扎实（熔断器/消息队列/缓存），一半注水或重复建设 |
| 核心环路可靠性 | C− | 主环路可用，fallback/evolve/skip_verify/control-plane 四条路径断裂 |
| 安全现状 | D | 上一轮审计确有真实修复，但本次归档泄露密钥直接抵消 |
| 测试与 CI | C | 测试量大且断言真实，但套件是红的、CI 配置与宣称不符 |
| 文档可信度 | C− | 关键数字三处互相矛盾，ADR 状态滞后 |

---

## 二、P0 — 致命问题（立即处理）

### P0-1 归档内含活体 JWT 签名密钥，可伪造管理员令牌【已复核确认】

- `data/jwt_secret`（64 字节十六进制，实测内容以 `28f921c7f508b914...` 开头）随 zip 分发
- `py/maop/core/auth.py:298-341` 的 `load_jwt_secret()` 优先读取此文件；`dashboard/routers/auth.py:79` 用它初始化 JWTHandler
- **攻击场景**：任何拿到本归档的人可离线签发 admin JWT → 通过 `AuthMiddleware`（middleware.py:251 `require_admin`）→ 调用 `/api/control/run` **远程执行任意任务**
- **修复**：从发布物剔除整个 `data/`；启动时强制重新生成密钥；立即轮换

### P0-2 归档含真实凭证库与 18 份数据库备份【已复核确认】

- `data/auth.db` 内含真实 admin 密码哈希（`pbkdf2_sha256$260000$7vXGNj+A40C/...`，users 表 1 行实测）
- `data/backups/` 18 个 `.bak`（maop/queue/memory.db 各 6 份）、`logs/`、`cache/`、764 个 `.pyc`（可反编译）
- pbkdf2 260k 迭代本身合格，但弱口令仍可被离线爆破；且 `routers/auth.py:66-67` 保留**无盐 SHA-256 旧哈希兼容**，若历史口令是旧格式可秒破
- **修复**：发布流程加入"运行时数据禁区"清单（data/ logs/ cache/ *.pyc __pycache__/）

### P0-3 skip_verify 逻辑自相矛盾：跳过验证必然整体失败【已复核确认】

- `maop_loop.py:991`：`if skip: return VerifyResult(passed=False, ...)`
- `maop_loop.py:667`：`success = exec_result is not None and exec_result.is_success() and (verify_result.passed if verify_result else True)`
- 跳过时 verify_result 非 None 且 passed=False → **LoopResult.success 恒为 False**。`--skip-verify` 标志等于自我否决
- **修复**：skip 时返回 `passed=True, summary="skipped"`，或在 667 行显式区分"未验证"与"验证失败"

---

## 三、P1 — 严重问题

### 3.1 安全类

**P1-1 WebSocket 认证完全失效【已复核确认】**
`dashboard/server.py:265-268` 判断 `if payload is None: close`，但 `core/auth.py:254-293` 的 `validate_token` **所有返回路径都是 AuthResult 对象，永不返回 None**（含 `AuthResult(authenticated=False)`）。伪造/过期 token 均可连接 `/ws` 获取实时数据。修复：改为 `if not payload.authenticated: close`。

**P1-2 AuthMiddleware 初始化失败时全站裸奔（fail-open）**
`core/middleware.py:78,103,130`：auth 组件为 None 时直接 `call_next` 放行。应默认拒绝 + 显式白名单。

**P1-3 dispatcher 转义与 shlex 解析不匹配（上一轮"已修复"项未修好）【已复核确认】**
`delegate/dispatcher.py:117-121`：对 prompt 做 `\'` 转义后交给 POSIX 模式 `shlex.split()`——单引号内反斜杠是字面量，奇数引号触发 ValueError（DoS），偶数引号可注入额外 argv（如向 `claude -p` 注入危险 flag）。SECURITY.md 声称的 null byte stripping 也未在 `_run_cli`/`_run_cmd` 落实。修复：放弃字符串模板，直接构造 argv 列表。

**P1-4 Windows 内建命令分支仍可命令注入**
`core/sandbox.py:163`、`core/runtime.py:55`：`cmd.exe /c subprocess.list2cmdline(...)` 不转义 `& | %`，`echo x & whoami` 可 RCE。修复：Windows 分支弃用 `cmd /c`。

**P1-5 登录无锁定 + 旧哈希兼容**
`/api/auth/login` 公开（server.py:190），仅 30rps/IP 全局限流（≈10 万次/小时），无账户锁定；配合 P0-2 泄露的哈希可离线+在线双路爆破。

**P1-6 Web 防御缺失**：无 CSP/XFO/nosniff 安全头；CORS `allow_credentials=True` 且 methods/headers 为 `*`（server.py:174）；TLS 默认关闭且绑定 0.0.0.0（server.py:299,304）；TLSv1/1.1 仅警告仍可选（tls.py:46-55）。XSS 残留：`app-overview.js:129-133`、`app-control.js:72` 未转义插入 innerHTML。`/api/logs` 的 type 参数未校验即拼 glob（data_system.py:44）。

### 3.2 核心逻辑类

**P1-7 maop_execute.py 是死代码，guardrail 被绕过**
`maop_loop.py:952` 直接调 `Dispatcher.dispatch()`，绕过 maop_execute 的内容安全前置/输出后置检查；maop_execute 仅被测试引用。Engines 层实际只有 plan/verify 在环路上。

**P1-8 Evolve 自进化阶段空转【已复核确认】**
`evolve.py:89` 读 `logs/delegations.json`，但全仓库生产代码只有 reader 没有 writer（仅测试写该文件）→ `EvolveEngine.analyze()` 在生产永远返回空统计，Phase 4 形同虚设。

**P1-9 路由配置被硬编码架空【已复核确认】**
`maop_plan.py:29-44` 的 `_ROUTING_RULES` 硬编码 10 条规则（几乎全路由到 codex/claude），`config/agents.yaml` 的 routing 表被绕过。ADR-002 已记录此问题但仍 Proposed。

**P1-10 fallback 链默认失效**
`maop_loop.py:940`：`agents = fallback_chain if retry else fallback_chain[:1]`，而 CLI 默认 `retry=False`（cli.py:77）→ 主路径永不 fallback，只对同一 agent 重试 3 次。

**P1-11 控制平面全是 stub 却记 audit SUCCESS**
`control/plane.py:117-141` 8 个内置 handler 全部返回 `{"switched": True}` 式假结果（注释自认 stubs），`execute()` 仍写审计日志 SUCCESS——**审计记录造假**。

**P1-12 Engine 假成功**：无 step_executor 时 AGENT/DAG 步骤直接返回 SUCCESS 占位输出（engine.py:462-468），整个 workflow 可"成功"但什么都没执行。

**P1-13 Verify 与 README 不符**：README 称 "lint/test/semantic 三 gate"，实际 GATE_REGISTRY 为 exit_code/output/content-safety/syntax-check/lint/dry-run（maop_verify.py:137-144），无 test/semantic gate；`_gate_dry_run` 恒 True（:132）。

### 3.3 真实性类（文档/宣称 vs 实际）

**P1-14 测试套件当前是红的【已沙箱实测】**
- 项目自己的 `py/test_results.txt`：**6 failed, 1962 passed**（Windows, Py3.14），失败为 TestPrune 的 NameError
- 本审查沙箱实测：**16 failed, 1945 passed**（Linux, Py3.12，已排除 stress/slow）——16 个失败全部因引用不存在的外部模块 `pipeline_core`（test_doc_pipeline_adapter 9 个 + test_event_hook_async 7 个）
- 有趣发现：TestPrune 在 Linux 单独跑全过（86 passed），是**测试隔离/平台相关的顺序依赖**缺陷
- `CHANGELOG.md:18` 的 "all passing" 被直接证伪

**P1-15 README 关键数字失实**："711 tests"（实测 1,956 个测试函数）；"lint (ruff + mypy)"（ci.yml:33 只有 ruff，无 mypy）；"18 agents"（实际 19，含 doc-pipeline）；"12 models"（实际 10）；"3 平台 × 2 Python"（实际 × 3：3.12/3.13/3.14）。三个文档间测试数互相矛盾（711 / 763→1697 / 1962）。

**P1-16 ADR-003 "mock fallback 已移除"不属实【已复核确认】**
- 前端硬编码兜底：`app-overview.js:23` API 端点写死 `'101'`；`:37-46` `source_files||75`、`code_lines||21482`、`tests_total||1917`、`platform||'Windows AMD64'`；`:50-61` 系统状态 10 项全写死（"TLS 已启用"与实际默认关闭矛盾）
- 后端硬编码：`/api/optimizer` 写死 recommendations（data_overview.py:102-104）；`/api/workflows` 无数据时返回 5 个假 workflow（system.py:329-333）；`/api/prompts` 返回 5 个假 prompt（data_knowledge.py:85-89）
- ADR-003 承诺的 `toggleBackendBanner` 前端完全不存在

**P1-17 前后端字段级契约错配【已复核确认】**
- `/api/overview`：`system.py:286` 读 `rpt.get("total")`，但 `data_bridge.py:195` report() 返回 `total_delegations` → **委托数恒 0**；`avg_latency_ms` 字段不存在；live/timeseries 类型判断写反（dict 判 list、list 判 dict）→ 最近活动恒空、图表恒不画
- success_rate 单位错：后端返回 0-1 小数，前端 `app-overview.js:24` 直接拼 `'%'` → 90% 显示 "0.9%"
- `logs_get(name)` 忽略 name 恒查 error_log（data_bridge.py:635-643）→ 所有日志类型返回同一错误日志
- 6 处 POST 裸 fetch 无 Authorization 头（app-evolve.js:185/191/208 等），默认 MAOP_AUTH=1 下全部 403
- "保存模型配置"按钮实际调 `/api/control/refresh`（清缓存）却提示"已保存"（app-actions.js:60-72）——假成功

---

## 四、P2 — 工程缺陷（节选）

- **并发**：TaskPool 优先级队列是摆设——submit 既入队又立即 `ensure_future`，`_execute` 从不从队列取任务（concurrency.py:181-184）；TaskPool/WorkerPool 的 `_tasks/_results` 永不清理（内存泄漏）；WorkerPool 每任务新建 MaopLoop 重开 5 个 SQLite（worker_pool.py:193）
- **SQLite 线程安全不一致**：circuit_breaker/message_queue 每操作新连接+WAL（好）；kv_store.py:97-101 持久单连接无锁无 WAL，跨线程直接 ProgrammingError
- **预算对账造假**：`maop_loop.py:542-543` 用 `len(task)//4` 冒充 actual_tokens，estimated_cost 传 0.0
- **非幂等缓存**：`maop_loop.py:490-531` 以 `agent:routing_key:task[:200]` 缓存执行结果（TTL 60s），有副作用的任务在窗口内被静默去重
- **MaopLoop 过度初始化**：拉起 16 个子系统，其中 7 个初始化后从未使用（maop_loop.py:217-329）
- **LoadBalancer "adaptive" 失效**：`record_start/record_finish` 全仓库零调用，指标恒 0 退化为静态权重
- **契约测试浅**：仅覆盖 12/90 端点路径，只断言"端点存在"无 schema 校验；`/api/batch` 前端零调用是死端点；provider.py 整文件未挂载死代码，还残留 ADR-006 已移除的 SSE
- **CI**：pip-audit `continue-on-error: true` 形同虚设；存在双 CI 文件（根与 py/ 下各一份，后者含非严格 mypy，生效路径不明）；`py/docker-compose.yml` build context 必失败（Dockerfile 要求 repo 根 context） **[部分修复 2026-07-21]：pip-audit continue-on-error 已在 t06 移除；`py/docker-compose.yml` 已在 t15 删除，根目录版本成为单一权威来源，otel endpoint 服务名误配（localhost→otel-collector）已同步修复；"双 CI 文件"问题尚未处理**
- **数据管道**：`/api/overview` 每请求遍历全部 .py 逐行数行无缓存；日志端点整文件 read_text 无上限；SQL 无分页

## 五、P3 — 卫生问题

归档混入 `py/.pytest_cache`、`py/.ruff_cache`、`test_output.txt`、`batch_run_tests.py` 等运行产物；`hello/ JSON/ output/ snapshots/ Verify/` 等空目录；`test-html.py` 硬编码 localhost:8080（实际 9079）；ADR 双编号并存（001-010 与 ADR-001/002 共 12 篇，索引只列 8 篇）；ADR-005/009 状态未随迁移完成翻转；Dockerfile LABEL version 滞后；README 结构图仍是 `F:\Nexus\MAOP\` Windows 路径。

---

## 六、验证为真的优点（应继续保持）

1. **代码无活密钥**：全部 provider 走 `api_key_env` 环境变量引用；命中的 `sk-abc...` 均为测试夹具
2. **上一轮审计的部分修复是真实的**：tool_manager shell=True 已改 shell=False；SQL 全部参数化；safe_eval AST 白名单（engine.py:22-47）；py/ 下 shell=True 零残留
3. **core/ 半数模块是真实现**：circuit_breaker（完整状态机+SQLite 持久化）、message_queue（ACK 回收/死信/幂等/延迟投递）、cache（LRU+TTL jitter+SingleFlight）质量扎实
4. **测试不注水**：抽查 10 个文件无 `assert True`，均为真实行为断言；契约测试有正确的 marker 体系
5. **前后端 API 路径 100% 对得上**（约 45 个 fetch 全量核对），93 个端点真实注册
6. **工程文化底子好**：ADR/CHANGELOG/SECURITY.md 齐备（问题在滞后而非缺失）；密码哈希 pbkdf2 260k 迭代合格；前端 esc() 转义覆盖面大

---

## 七、修复路线图（按优先级）

**第 1 批（今天）**：从发布物剔除 `data/`、`logs/`、缓存与 `.pyc` → 轮换 JWT 密钥与 admin 口令 → 修 WebSocket `payload is None` 判断 → 修 skip_verify 返回值
**第 2 批（本周）**：dispatcher 改纯 argv 构造 → middleware 默认拒绝 → 删除无盐 SHA-256 兼容 + 登录锁定 → 修 `/api/overview` 四处字段错配与 success_rate 单位 → 6 处 POST 补认证头
**第 3 批（下周）**：修复/隔离 16+6 个失败测试使套件转绿 → 统一 README/CHANGELOG 数字口径 → 删除前后端假数据兜底并补 toggleBackendBanner → control plane stub 要么实现要么标注 → evolve 接上真实数据源或下线
**第 4 批（持续）**：Windows cmd 分支重构、并发与资源泄漏治理、契约测试扩到全端点 schema 校验、ADR 状态梳理

---

## 八、审查过程说明

本次审查由 1 个主代理 + 4 个并行子代理完成：主代理先行侦察（结构/密钥/配置），随后派出架构质量、安全专项、Dashboard 全栈、测试 CI 文档 4 个子代理并行深审；子代理报告返回后，主代理对全部 P0/P1 结论**逐项打开文件复核取证**（本报告标注"已复核确认"的条目均经主代理亲眼验证 文件:行号），并在干净沙箱中实际安装运行了完整测试套件（16 failed / 1945 passed，Linux Py3.12）。所有结论均有代码证据，未采纳任何子代理的未取证推断。
