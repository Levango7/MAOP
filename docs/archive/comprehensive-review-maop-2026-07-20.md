# MAOP 整体工程评测报告

**日期**：2026-07-20
**工作流**：综合代码审查（Workflow 1）+ 架构 / 测试 / 运维 / 文档交叉评估
**参与成员**：Cody（代码审查师）、Archi（系统架构师）、Tessa（测试专家）、Rex（SRE 工程师）、Docu（技术文档师）
**项目路径**：`F:\Nexus\MAOP\`

---

## 📌 TL;DR（执行摘要）

- **整体结论**：MAOP 是一个**功能真实、代码量扎实**的多智能体编排平台（约 91 个 core 模块，插件系统 / ReAct 微循环 / 知识图谱 / 成本管控 / OpenTelemetry / 多租户 / MCP Hub / 三层记忆均真实落地）。但它**不是生产就绪**，且仓库内 4 份既有评估报告（最高称「8.6/10 企业级平台」）经核实属于 **AI 生成的注水稿**——模块数、测试数、通过率、架构评分均与代码现实严重不符。
- **严重度分布**：🔴 严重 7 项 / 🟠 高 4 项 / 🟡 中 8 项 / 🟢 低 1 项
- **阻塞 / 非阻塞**：2 个 🔴 为**生产部署阻断项**（SQLite 被三容器并发写、CI 被 16 个 collection error 卡死）；另有 4 个 🔴 为安全默认失效，必须上线前修。
- **一句话**：平台「骨架真、皮实足、但安全与质量门禁是装饰」——先把 CI、部署隔离、安全默认关掉的问题修掉，再谈「企业级」。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟡 **6.5 / 10 有条件通过**（非生产就绪；功能广度优于 PEV，但质量门禁与默认安全未生效） |
| 阻塞项数量 | 2（生产部署阻断：SQLite 并发写、CI 卡死） |
| 关键行动项 | 7 条 P0（见行动清单） |
| 建议下一步 | 先恢复 CI 全绿 + 关掉默认匿名/明文部署，再依次修沙箱/权限/BYOK/成本伪造，最后补真实可观测后端 |
| 既有 4 份报告 | ❌ 不可信（AI 注水稿，数字与代码矛盾，对 16 个 collection error 只字不提） |

---

## 一、对既有 4 份评估报告的核验（最关键结论）

仓库根目录 `MAOP_audit_report.md` / `MAOP_COMPREHENSIVE_ANALYSIS.md` / `MAOP_v4_EVALUATION.md` / `MAOP_v4_FINAL_REPORT.md` 给出了极高的评价（架构 9.0/10、总评 ~8.6/10「企业级平台」）。主理人与各成员逐项核对代码后判定：**这些报告非基于代码的真实评估，系 AI 生成注水稿**。对照如下：

| 报告声称 | 代码实测（本评审核实） | 结论 |
|---------|----------------------|------|
| 「48 个 core 模块」 | `py/maop/core/` 实际 **91 个 .py 模块** | ❌ 严重低估 |
| 「109 测试文件 · 2,702 测试函数」 | 实际 **133~138 测试文件 · 3,167 个 `def test_`** | ❌ 数字全错 |
| 「2,702 测试验证工程成熟度 / 测试全过」 | `pytest --co` 实测 **2,800 collected + 16 collection errors**（starlette.testclient `httpx2` 依赖错配 + numpy/httpx/aiosqlite 缺失），整片 router/集成测试无法运行 | ❌ 不成立 |
| 「架构设计 9.0/10、可观测性 9.0/10、安全 8.5/10」 | 观测性 exporter 仅 `logging` 且默认关闭；安全默认全关（见下） | ❌ 严重高估 |
| 「八层架构」 | README 写「五层」；真实为 CLI/编排/引擎/服务/基础设施/存储/展示 多子包（~91 模块），八层是把并列组件重复计数 | ⚠️ 文档与代码皆不实 |
| 模块存在性（plugin/react_loop/knowledge_graph/cost_tracker/llm_provider/agent_scanner 等） | 全部**真实存在**（已逐一确认） | ✅ 这部分没编 |

> 提示：与 PEV 那版「8.5/10 包装稿」不同，MAOP 报告里**列举的模块确实存在**，不是纯虚构；问题在于**规模数字注水、测试通过率造假、安全/可观测评分虚高**。

---

## 二、🔍 审查发现（按严重度，Cody）

| # | 严重度 | 类别 | 文件:行 | 问题描述 | 建议修复 |
|---|--------|------|---------|---------|---------|
| 1 | 🔴严重 | 安全 | `core/plugin.py:171-208` | **Plugin 沙箱可逃逸**：仅替换 `module.__builtins__`，但 `builtins` 在白名单内，插件可 `import builtins; builtins.__import__('os').system(...)` 拿回真实能力；Windows 下 init 跑线程无隔离 | 独立子进程 + 禁 `builtins` 导入；危险插件强制子进程/seccomp |
| 2 | 🔴严重 | 安全 | `maop_execute.py:171-180` | **权限默认自动批准**：`PermissionManager.check` 默认 `ask`，但此处立即 `hp.approve()`（注释 "auto-approving for now"），默认配置下所有 action 放行 | `ask` 必须挂起等真实人工确认；默认决策应为 `deny` |
| 3 | 🔴严重 | 安全 | `core/byok.py:114-142` | **BYOK 明文 + 无租户隔离**：`direct` 源明文存 key；`tenant` 源不校验调用方租户身份，租户 A 可传 `tenant_id="B"` 取走 B 的密钥；vault 源全租户共享一把钥匙 | 密钥来自加密 vault 且按认证租户强绑定；移除 `direct` 明文源 |
| 4 | 🔴严重 | 正确性 | `maop_loop.py:435-440` / `core/react_loop.py:142` | **Token/成本数据伪造**：agent 未返回 usage 时回退 `len(text)//4` 估算写入 `BudgetGuard`，即 PEV 遗留假 token 模式，成本看板不可信 | 要求 provider 返回真实 usage；无 usage 记为「未知」而非伪造 |
| 5 | 🟠高 | 安全 | `core/sandbox.py:119-190` | **SandboxManager 无隔离即称沙箱**：仅给 cwd+超时跑任意命令，无容器/chroot/seccomp；若 API 可传入 command 即 RCE | 限制白名单二进制，或明确文档为「工作目录管理器」禁止 API 收自由命令 |
| 6 | 🟠高 | 安全 | `delegate/drivers.py:296-303` | **cmd 驱动转义漏 `%` 与 `"`**：`%MAOP_KEY%` 会被 cmd 展开泄露环境变量，未转义 `"` 可破坏引号 | 补转义 `%`/`"`，加 cmd 全量元字符测试 |
| 7 | 🟡中 | 安全 | `core/auth.py:256-295` | **JWT 不校验 alg 头**：始终 HS256 重算，缺 alg-confusion 纵深防御 | 断言 `header.alg` |
| 8 | 🟡中 | 性能 | `cost_tracker.py:172` / `tenant.py:156` / `sandbox.py:175` | **同步 sqlite3 阻塞事件循环**：async 路径直接阻塞 IO | `asyncio.to_thread` 或 aiosqlite |
| 9 | 🟡中 | 安全 | `core/tenant.py:148-177` | **`check_quota` 先写后查**：先累加再判超配额，超额已计入；全平台共享库未做 tenant 分片，隔离未落地 | 先算后写；按 tenant 分片 |
| 10 | 🟢低 | 可维护 | `control/plane.py:127-149` | control plane handler 为 no-op stub，状态已诚实记 `SKIPPED`，但 detail 仍写误导性字段 | 补全真实逻辑或明确标注未实现 |

---

## 三、🏗️ 架构评估（Archi）

- **层数之争**：README「五层」与报告「八层」皆不实。真实为 CLI / 编排（MaopLoop+ReActLoop）/ 引擎（DAG）/ 调度（dispatcher+maop_plan）/ 基础设施（core ~91 模块）/ 存储 / 展示多子包。报告把 MaopLoop 与 ReActLoop 并列计数夸大层数。
- **ReActLoop vs MaopLoop**：设计正确——`maop_execute.py:129` 在 Execute 阶段内嵌 `ReactLoop` 作为内层微循环，非冲突。文档误导为并列层。
- **状态统一**：ADR-011 部分落地但状态仍 `Proposed`；**新分裂脑仍在**——`kv_store.py:66` 默认 `data/kv.db`，`services.py:129` 却建 `data/kv_store.db` 两库并存；`data/` 下 memory/prompts/tools/vectors 同时有 `.db` 与 `.json` 双写残留。
- **插件系统**：生命周期状态机 + SQLite 持久化**真实完善**；但「安全沙箱」被高估（见 Cody #1），校验和默认关闭（`MAOP_PLUGIN_STRICT_CHECKSUM=0` 仅 warn）。
- **三层记忆**：`three_layer_memory.py` 真落地（Working=LRU+TTL / Episodic=SQLite+衰减+consolidate / Semantic=VectorStore），**优于 PEV 单库+TTL**，是真实亮点。
- **MCP Hub**：拆分合理，但 `mcp_hub.py` 与 `mcp_client.py`/`mcp_transport.py` **重复实现传输与数据模型**，易漂移。
- **过度设计**：ADR-012 揭示 `maop_plan.py:_route_by_config` 用「agent 名是否在任务文本」匹配，99% 回落硬编码 `_ROUTING_RULES`，agents.yaml 的 14 条配置路由从不开火——**配置路由是死代码**。

---

## 四、🧪 测试体系（Tessa，实测）

- **实测命令**：`cd F:/Nexus/MAOP/py && python -m pytest tests/ --co -q` → **2,800 collected, 16 errors**。
- **抽样**：`pytest tests/contract tests/test_event_bus.py tests/test_error_schema.py tests/test_dispatcher.py tests/test_auth.py tests/test_budget.py -q` → **135 passed, 2 errors**（可收集部分通过率高，但绝非「全过」）。
- **16 个 collection error 根因**：
  1. 6 个 `test_router_*`：`RuntimeError: starlette.testclient requires httpx2`——`starlette 1.3.1` 误依赖 `httpx2`（PyPI 真包名 `httpx`），版本/依赖声明错配。修：`pip install "starlette==0.40.*" httpx`。
  2. 10 个 `ModuleNotFound`：缺 `numpy`/`httpx`/`aiosqlite`。修：`pip install numpy httpx aiosqlite`。
- **覆盖结构薄弱**：仅 `tests/contract/` 一个子目录，**无 unit/integration 分层**，全部平铺；**无强制覆盖率门禁**（`pyproject` 仅声明 `pytest-cov`，`addopts` 未加 `--cov`，`.coverage` 是空跑）。
- **结论**：报告「2,702 测试验证成熟度 / 全部通过」不成立。先修依赖恢复 16 文件，再补 `--cov` 门禁。

---

## 五、🚀 运维就绪度（Rex）

- **明确判断：不可直接生产部署。** 2 个 🔴 阻断 + 默认安全关闭。
- 🔴 **SQLite 三容器并发写**：`docker-compose.yml` 中 dashboard/agent-exec/queue-worker 共享 `maop-data` 卷且 `BACKEND_STORAGE=sqlite`，单写库多进程并发会锁竞争/损坏（注脚自承需 NFS/sidecar）。
- 🔴 **CI 被 16 collection error 卡死**：`ci.yml` 的 `test` 无 `continue-on-error`，收集失败即非零退出，`docker`/`audit` 因 `needs:test` 被整段跳过，无法出镜像。
- 🟠 **默认无认证/无 TLS**：`MAOP_AUTH_ENABLED=0`，TLS 仅 `--profile tls` 可选，默认 `0.0.0.0:9079` 明文开放。
- 🟠 **可观测性注水**：`otel-collector-config.yaml` exporter 仅 `logging`(WARN)，无 Jaeger/Prometheus/Tempo；`MAOP_OTEL_ENABLED=0` 默认关；端点默认 `localhost:4317`（容器内应是 `otel-collector:4317`）。 **[部分修复 2026-07-21 t15]：docker-compose.yml 中 dashboard 服务现在通过 `MAOP_OTEL_ENDPOINT=http://otel-collector:4317` env var 注入正确端点；代码层默认值仍未改（需保留对本地非容器部署的兼容）；otel-collector-config.yaml exporter 仍为 `logging`。**
- 🟡 **备份/日志调度真实但脆弱**：`DbBackup(3600s)`+`LogRotateScheduler(600s)` 在 lifespan 自启，但 daemon 线程无监工，同卷无异地。
- 🟡 **熔断 half-open 非真实探测**：`circuit_breaker.py:435` 仅按 cooldown 恢复，未真探活。
- 🟡 **配置漂移**：根 `docker-compose.yml` 与 `py/docker-compose.yml` 并存，otel 端点服务名误配。 **[已修复 2026-07-21 t15]：删除 `py/docker-compose.yml`（其 build context + volume 路径在 py/ 内启动时会指向不存在的 py/config 目录），根目录版本成为单一权威来源；dashboard 服务新增 `MAOP_OTEL_ENDPOINT=http://otel-collector:4317` 与 `MAOP_OTEL_EXPORTER=otlp` 环境变量，otel-collector 服务的误配置 env vars 已移除（collector 不读取这些，应由 app 注入）。**
- 🟡 **deploy.py 仅本地拉起**：不构建容器，`start()` 返回 STARTING 不校验就绪，硬编码 `maop.dashboard.server:app`。
- **亮点**：`/api/health`、`/api/metrics`、熔断阈值/失败链、时序降采样（24h/7d/90d）设计合理——机制是真写的，只是默认未接通生产后端。

---

## 六、📚 文档准确性（Docu）

- **README 出入**：核心模块数「30+」→ 实际 91；架构图标 `dispatcher.py` 实际在 `delegate/dispatcher.py`（README 已脱节）；端口 9079 与代码 `cli.py:18` 一致 ✅；13 个核心模块表全部存在 ✅。
- **4 份报告可信度**：**AI 注水稿**（见第一节，数字全错、对 16 error 只字不提）。
- **文档间矛盾**：ADR-002（Accepted）描述的 `server.ps1/server-v2.ps1` 已被删，停留在 PS 时代；ADR-009 称 `core/` 仅 4 模块（实际 91）且状态 Proposed，但 Python 已是事实主引擎；ADR-011 引用的 `message_queue.db` 分裂脑已消除但状态仍 Proposed。
- **少数对齐文档**：`SECURITY.md` 与代码一致（dispatcher 存在、`safe_eval`、端口、TLS 均属实），是罕见可靠文档。
- **REMEDIATION_PLAN**：全篇 PS 化（已删文件），与 Python 现实矛盾，但诚实标 P3 测试「待后续」，与 16 error 吻合。

---

## ✅ 行动清单（按优先级排序）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | 修 CI 16 collection error：锁 `starlette==0.40.*`+`httpx`，装 `numpy httpx aiosqlite` | Tessa / Rex | P0 | 0.5 天 |
| 2 | docker-compose SQLite 三容器并发写 → 单写服务或换 Postgres + 独立消息队列 | Rex | P0 | 1-2 天 |
| 3 | Plugin 沙箱逃逸 → 子进程隔离 + 禁 `builtins` 导入 + 默认强制校验和 | Cody | P0 | 2 天 |
| 4 | Permission 默认 auto-approve → 默认 `deny`/挂起等真实人工确认 | Cody | P0 | 0.5 天 |
| 5 | BYOK 明文+无租户隔离 → 加密 vault + 按认证租户强绑定，移除 `direct` 明文源 | Cody | P0 | 1 天 |
| 6 | 成本/token 伪造 `len//4` → 真实 usage 或标记「未知」 | Cody | P0 | 0.5 天 |
| 7 | docker-compose 默认 `MAOP_AUTH_ENABLED=0`/无 TLS → 强制 auth+tls 或前置网关 | Rex | P0 | 0.5 天 |
| 8 | 观测性接真实后端（Tempo/Prometheus），metrics 端点放开 scrape | Rex | P1 | 1 天 |
| 9 | 状态分裂脑：统一 KV 真源（kv.db vs kv_store.db），清理 JSON+db 双写残留 | Archi | P1 | 1 天 |
| 10 | cmd 驱动补转义 `%`/`"`；JWT 校验 alg 头 | Cody | P1 | 0.5 天 |
| 11 | 同步 sqlite → `asyncio.to_thread`/aiosqlite | Cody | P1 | 1 天 |
| 12 | MCP hub 去重传输与数据模型，统一到 `mcp_transport.py` | Archi | P2 | 1 天 |
| 13 | 清理 config 路由死代码（ADR-012）或执行之；README 同步 | Archi / Docu | P2 | 0.5 天 |
| 14 | 文档纠正：48→91、109→133、2702→3167，标注 16 collection error；更新 ADR-002/009/011 状态 | Docu | P1 | 0.5 天 |

---

## ⚠️ 待完善 / 已知局限

- 本评测未逐文件通读 91 个 core 模块，重点核查了安全/编排/存储/部署相关路径；个别中低危项可能仍有遗漏。
- 测试「可收集部分抽样通过率高」是基于抽跑，未全量跑通（因 16 error 未先修）。
- 性能基线（吞吐/延迟）未做压测，仅基于代码静态判断。
- 三层记忆、插件生命周期、ReAct 嵌套为**真实亮点**，本评测未否定其设计价值。

---

## 📚 数据来源 & 成员产出索引

- Cody（代码审查师）原始产出：安全/正确性/性能审查，10 条发现（见第二节）
- Archi（系统架构师）原始产出：分层/状态统一/插件/MCP/路由架构评估，7 条发现（见第三节）
- Tessa（测试专家）原始产出：pytest 实测 2,800 collected+16 errors 根因与修复（见第四节）
- Rex（SRE 工程师）原始产出：docker-compose/otel/CI/部署核查，8 条发现 + 「不可直接生产部署」判断（见第五节）
- Docu（技术文档师）原始产出：README/ADR/4 份报告准确性核验（见第六节）
- 主理人（甄宇航）实测基准：core 模块数 91、测试文件 133、def test_ 3,167、pytest 16 collection errors、4 份报告数字矛盾。

---

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
