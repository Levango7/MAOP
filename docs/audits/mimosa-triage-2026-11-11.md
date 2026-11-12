# Mimosa 深扫分诊记录（2026-11-11 晚，MAOP）

- 扫描：`scan-2026-11-11T22-33-05.883Z-7535a132ac74`，depth=deep，项目 `F:/Nexus/MAOP-op`
- seal：`sha256:935de67f480e6aed7d35adb22ed49271f375fce3a88a195d7781e33d0a59e328`
- 覆盖度：`partial` / runStatus `inconclusive`（引擎边界：threatModel 0 entryPoints；pathAnalysis 2406 函数/2893 边 completed；validation 0 investigated）
- 报告自述：projection of sealed JSON artifacts，**not runtime verification**
- 结论纪律：机械口径只产候选；以下每条均读源码上下文定性。**本记录不构成「项目安全」的宣称。**

## 总账（59 条 → 0 REAL / 58 FP+已注明 / 1 重复计）

| 类别 | 条数 | REAL | FP |
|---|---|---|---|
| SQL 注入（high） | 21 | 0 | 21 |
| 路径穿越（high） | 16 | 0 | 16 |
| open 是 path-traversal 入口（high） | 1 | 0 | 1 |
| SSRF（high） | 6 | 0 | 6 |
| 命令注入（high 1 + low 1） | 2 | 0 | 2 |
| 硬编码凭据（high） | 4 | 0 | 4 |
| 不安全随机数（low） | 7 | 0 | 7 |
| 疑似跨文件污点（medium） | 2 | 0 | 2 |
| **合计** | **59** | **0** | **59** |

## 一、SQL 注入 21 条：全 FP（拼接变量均为内部常量/已校验标识符/强转 int）

| file:line | 依据（变量从哪来） |
|---|---|
| core/agent/delegation/subagent_db.py:407 | col 来自模块常量 REQUIRED_COLUMNS；L406 `_validate_column_def` 先过 `^[[a-zA-Z_][a-zA-Z0-9_]*$` + 类型白名单 + 禁 `;`/`--` |
| core/backends/backends.py:133 | `PRAGMA busy_timeout={int}`，值经 `_get_busy_timeout_ms()` int 强转 + 负数拒绝 |
| core/backends/data.py:562 | fts_init table/columns 逐个过 `validate_identifier` 后才进 DDL；无 HTTP 路径 |
| core/backends/data.py:648 | fts_rebuild 先 `validate_identifier(table)`，`rebuild` 为字面量 |
| core/backends/db_backup.py:406 | db_name 来自 DEFAULT_DATABASES 常量或无参调用；L366 正则 + `_validate_backup_path` 白名单目录 |
| core/backends/db_utils.py:124 / :260 | 同 busy_timeout，int 强转 |
| core/memory/multimodal_pg.py:211 | self._table 经 `_validate_table`（`^[a-z_][a-z0-9_]*$`）；空间名来自内部注册表；值走 `:name` 绑定 |
| core/memory/vector_store.py:464 / :522 | self._table 默认常量 `vector_entries`，全仓 8 处实例化均未传 table_name |
| core/retention.py:197 | POLICIES 字面量元组 + 导入时 `_validate_policies()` 逐表 validate_identifier；值走 `?` 绑定 |
| core/security/api_key_manager.py:264 | new_cols 函数内字面量列表，ALTER TABLE ADD COLUMN 常量列 |
| core/tenant/rls.py:88 | register_table 先 validate_identifier；`_ensure_column` 插值前再校验；构造参数仅测试传 |
| core/vector/pg_backend.py:504 | **唯一不对称**：DROP INDEX 路径 L482 有正则，CREATE INDEX 路径 L405-418 无入口校验（idx_name 默认常量、全仓无调用方传）→ 硬化项 H1 |
| dashboard/services/auth_service.py:176 | new_cols 字面量，常量列 |
| migrations/memory_migration.py:180 / :186 / :312 | table 来自迁移入口字面量元组；写入侧 `?` 占位 + params 绑定 → 硬化项 H3 |
| migrations/pg/versions/001_initial_schema.py:959 | `_DROP_ORDER` 模块常量表名列表，迁移期 DDL |
| migrations/pg/versions/003_multimodal_entries.py:72 / :78 | MM_TABLE 模块常量，迁移期 DDL |

## 二、路径穿越 16 + open 入口 1：全 FP（无 HTTP 入参进路径）

- dashboard/routers/agents/crud.py:89,112：路径 = `MAOP_ROOT / 'config' / 'agents.yaml'` 模块常量根 + 固定名；HTTP body.name 只作 YAML 键，cli_path 被 `os.path.basename` 裁剪
- core/mcp/mcp_marketplace.py:189,462：`_config_path` 构造默认值 = `Path(__file__).resolve().../config/mcp_marketplace.yaml`；两调用方均固定
- core/evolution/auto_deployer.py:304 / evolution_agent.py:181 / evolution_strategies.py:291 / suggester.py:275 / core/reliability/config_mutator.py:238：均为 `self._root/'data'/'<固定名>.json'`；experiment/winner 只进文件内容
- model/registry.py:428：`self._root/'config'/'models.yaml'`，root 来自 model_service 传 state.MAOP_ROOT
- ab_test_framework.py:343：`self._root/'data'/'ab_experiments.json'` 固定拼接
- py/scripts/first_run_smoke.py:84：CLI `--home` 运维入参 + 固定 `first-run.log`，非 HTTP 面
- py/scripts/fix_swallowed_exceptions.py:79：os.walk(py/maop) 自改写源码的迁移脚本
- dashboard-enterprise/scripts/fix_hardcoded_i18n.py:114,135,159：view 只能取 MAPPING 固定键；路径全静态
- dashboard/services/auth_service.py:210（open 入口 + 链式）：`pwd_file = os.environ.get('MAOP_ADMIN_PASSWORD_FILE')` = 运维配置即信任边界（Docker secrets 标准读法）；所报 sink tests/test_marketplace_key_resolve.py:70 实为测试替身 `_FakeOpener.open`，非文件 open，污点链不成立

## 三、SSRF 6 + 命令注入 2：全 FP（威胁模型注明）

- core/mcp/mcp_hub_transport.py:442：URL 三链路全可控（运维 YAML / 管理接口 require_admin / marketplace `_assert_safe_url` 已做 scheme 白名单 + getaddrinfo 私网拒绝）；残留：无解析后 IP 校验、http 允许、默认跟随重定向 → **行为变更项 S1，排期不改**（loopback MCP 是刻意放行，改了要动 `_assert_http_url` docstring + tests/test_bandit_security_fixes.py:50 nosec 清单）
- deploy.py:251：`http://{settings 默认 127.0.0.1}:{port}/api/health` 运维自检；:247 已拒 `/@?#`
- py/scripts/check_required_checks_drift.py:81 / check_required_checks_satisfiability.py:278 / compose_smoke.py:195 / first_run_smoke.py:35：CI 守卫/冒烟脚本，host 为常量 api.github.com / localhost，port 为 CLI int
- core/agent/ops/sandbox_executor.py:405（命令注入 high）：`subprocess.run(command, shell=True)` 但**生产零引用**（ops/__init__.py 自述 0 处；tests/test_dead_wiring_inventory.py:77 登记 unwired）→ 死代码；**接线即 REAL**：接入前必须 shell=False + shlex 列表 + 命令白名单
- core/mcp/mcp_hub.py:13（low）：命中的是模块 docstring 文本，无代码；真实 stdio 派生在 mcp_hub_transport.py `_StdioTransport`：shlex.split + ALLOWED_COMMANDS 白名单 + `create_subprocess_exec(*args)` 列表传参无 shell

## 四、硬编码凭据 4 + 不安全随机数 7 + 跨文件污点 2：全 FP

- credential_vault.py:61,62 / agent_catalog.py:79：枚举成员字面量（`OAUTH_TOKEN = 'oauth_token'` 等），非密钥；敏感字段经 Fernet 加密落盘（密钥来自 env MAOP_CREDENTIAL_KEY 或 secrets 生成）
- backends_pg.py:44：注释里的占位示例值；真实代码 L42 `os.getenv('MAOP_PG_PASSWORD','')`
- 随机数 7 条：负采样（knowledge_graph_reasoning:475）、可复现训练 RNG seed=42（plan_quality/features:179）、TTL 抖动防雪崩（cache_guard:225,237 / cache:170）、评分并列打散（load_balancer:471）、记忆条目 ID 后缀（memory/models:20，主键非 token；同文件 trace_id 用 uuid4）→ 安全场景全仓已用 secrets
- _auth.py:50 → multimodal_embed.py:331：两端都是 `__init__` 签名，全文件无 eval/exec；唯一 `.eval()` 是 torch `model.eval()`，作用于默认常量模型，无数据流
- auth_service.py:210 → 测试文件：见第二节末，sink 是测试替身方法

## 真实硬化缺口（0 REAL 之外，值得修）

| # | 项 | 修法 | 风险 |
|---|---|---|---|
| H1 | pg_backend.py CREATE INDEX 路径 idx_name 无入口校验（DROP 路径有，全仓唯一不对称） | 入口处 `re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', idx_name)` 否则 ValueError | 无行为变更（默认常量路径不变） |
| H2 | multimodal_spaces.py 注册空间时表名未校验（靠下游 PgMultimodalBackend 兜底） | `__post_init__` 加 `^[a-z_][a-z0-9_]*$` 校验 | 无（企业版注册处同步报错前移） |
| H3 | memory_migration.py 表名来自调用参数（当前调用方全字面量） | `_MIGRATABLE_TABLES = frozenset({...})` 白名单 + 函数入口校验 | 无 |
| H4 | crud.py / auth_service.py 等写点无 realpath+containment（当前无向量，防御纵深） | open_within 包裹 | 低；可排常规债 |
| S1 | mcp_hub_transport.py 传输层 SSRF 加固 | 协议白名单 + 解析 IP 私网拒绝 + 禁重定向 | **行为变更**（loopback 刻意放行），需排期 + 改 nosec 清单 |
| D1 | sandbox_executor.py 死代码 | 保持不接线；接线前必须 shell=False + shlex 列表 + 命令白名单 | — |

## 本轮处置（2026-11-12，分支 sec/mimosa-hardening-identifiers）

- **H1 / H2 / H3 已修**（入口单点校验，行为对默认常量路径零变更），各配一枚钉子测试，并按变异电池纪律验证：逐一拆除修复后对应钉子转红（拆 H1 时旧 DROP 路径钉子仍绿、新钉子红 → 证明旧钉只经 DROP 路径间接钉住、入口校验曾被绕过；拆 H2 时 spec 钉红、后端兜底钉绿；拆 H3 时白名单钉红），恢复后全绿。
- **第二 CREATE INDEX 站点已核**：`migrations/pg/vector_migration.py` `_rebuild_pg_index` 的第二处 CREATE INDEX 站点（L280-281）已有同款 `re.fullmatch` 校验，无需修。
- **调用方破环核查**：表名/索引名全仓调用方均为小写下划线字面量（含企业侧 multimodal_av_contracts；MM_TABLE=`multimodal_entries` 兼容）；index_name 全仓无传（走默认常量）。
- **H4 归档**：当前无向量（路径均常量根 + 固定名），open_within 包裹属防御纵深，排常规债。
- **S1 排期不改**：传输层 SSRF 加固是行为变更（loopback MCP 刻意放行），同步改 `_assert_http_url` docstring 与 tests/test_bandit_security_fixes.py:50 nosec 清单后方可做，单独立项。
- **D1 保持未接线**：sandbox_executor 死代码不接；接线前必须 shell=False + shlex 列表 + 命令白名单，否则按 REAL 处理。
- **record_conflict / record_trace_export**：无生产者，不接（接线即写空数据路径）。
- 六道本机门全绿：ruff（CI 口径，我改六文件全过；存量 3 条 DTZ 为版本差，干净树同报）、mypy 622 文件、config-drift 241==241、env.example 5 passed、silent-exception 1072/46/0 三线零漂移、file-size 0；三目标测试文件 333 passed。

## 方法边界

- 分诊=4 路只读 agent 逐条读 ±20-30 行上下文 + 全仓 grep 追来源； UNSURE 0 条
- 本记录仅针对这 59 条扫描结果，不宣称项目安全；扫描本身 partial/inconclusive（引擎边界）
- 原始产物：`C:/Users/winge/.mimosa/security-scans/project-22a99af835f00fc7cba10283/scan-2026-11-11T22-33-05.883Z-7535a132ac74/`（findings.json / report.md / coverage.json / seal.json）
