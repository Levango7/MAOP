# ADR-023: 静默异常与文件规模的常态化门禁（复评处置）

- **状态**: Accepted
- **日期**: 2026-10-12
- **关联**: ADR-018（吞异常处理规范）、外部工具《MAOP 复评报告 2026-10-12》、`docs/audits/review-disposal-plan-2026-10-12.md`
- **决策者**: 独立对抗性审核通过后实施（方案首版被 REJECT，5 项硬伤修订后开工）

## 背景

复评报告指出三个"运动式治理后复发"的问题，实测核对（本仓 master `1c5ce5e`）：

1. **静默吞异常仍有存量**：门禁语义下实测 **81 处** `except` 处理器首语句即 `pass`/`continue`
   （且块内无日志/raise），另有 **47 处** `contextlib.suppress(...)`（等价吞异常）。
   旧治理脚本 `fix_swallowed_exceptions.py` 只文本匹配 `except Exception`——窄类型
   （`except ValueError:`）与注释类信息全部逃逸；且它注入的 `logger.debug("swallowed exception")`
   **正是 ADR-018 要消灭的形态**。
2. **巨型文件复长**：mcp_hub/maop_loop 拆分后，新长出 7 个 >800 行文件
   （`data_service.py` 1148、`chat_service.py` 1145 …）——缺常态化门禁，拆分成为一次性运动。
3. **CHANGELOG 结构漂移**：两个 `[Unreleased]` 段、`[5.2.0]` 段排在 `[5.0.x]` 之后；
   文档一致性门禁只覆盖 README/docs 的路径与表格，CHANGELOG 无人管。

## 决策

### D1. 静默异常门禁（`scripts/check_silent_exceptions.py`）

- **判定**：AST 扫描 `except` 处理器；首语句为 `pass`/`continue`/`...` 且块内无
  `logger.*`/`print`/`raise` → 视为静默，必须有**理由标记**：
  ```python
  except FileExistsError:  # silent-ok: lock-race — 锁已被其他进程持有
      pass
  ```
- **标记闭集**（防"任意理由合法化"）：`optional-dep` / `lock-race` / `ws-disconnect` /
  `interrupt` / `loop-teardown` / `defensive-probe` / `scan-continue` /
  `platform-limitation`（Windows chmod、fsync 目录等平台差异）/
  `best-effort-cleanup`（清理失败不掩盖主错误——原错误已重抛或另行告警）/
  `expected-signal`（超时/不存在即等待的信号）。类别不在闭集 → 门禁红
  （不是"有标记就放行"）。
- **存量已清尾（同批完成）**：81 条静默逐条分类——设计性静默加标记（宽异常 `except Exception`
  的理由按站点写具体，不用通用文案），其余按 ADR-018 记日志；**基线现为空集**
  （新增即红，无豁免缓冲）。`contextlib.suppress` 仍为 47 处棘轮（只记数、暂不逐处处置）。
- **基线键刻意不用行号**：`path::异常类型::语句种类::#第 n 次出现`（源序）——
  行号会因文件上方任何编辑而位移，用行号做键等于"改别处也会假红"（本仓反复栽过的
  假红类）；序号只依赖同类处理器的相对顺序。随附两条回归测试（位移不变键 / 同文件重复唯一）。
- 注释用 `tokenize` 读取（AST 不保留注释）；源码按 `utf-8-sig` 读（本仓 17 个文件带 BOM）。
- 与 **ADR-018 的关系**：真错误路径仍首选 ADR-018 的 `logger.warning("[module] … continuing: %s")`；
  `silent-ok` 只用于**设计性静默**（可选依赖探测、锁竞争、客户端断开、事件循环已停等），
  且理由必须写明。`fix_swallowed_exceptions.py` 作为历史工具保留，但**不再作为新代码范式**。

### D2. 文件规模门禁（`scripts/check_file_size.py`）

- 阈值 **800 行**（`splitlines()` 口径，`utf-8-sig` 读取）；`migrations/` 整体豁免
  （Alembic 迁移是 SQL 数据，拆分反而不利于审计）；存量 7 个文件进 allowlist，每条带
  **拆分排期**；allowlist 双向一致（拆完必须移除条目）。

### D3. CHANGELOG 结构门禁（并入 `check_docs_consistency.py`）

- 三条规则：①`[Unreleased]` 至多一个且必须存在；②版本段按**数字核心**降序
  （核心相同者不强制，如 `5.1.0` 与 `5.1.0-personal` 保留真实发版时间线）；
  ③版本号须为 SemVer（含 prerelease 后缀）；④非版本号的历史归档段
  （`[未归属记录]`/`[2.x]`/`[1.x]`）进豁免名单且双向一致。
- **数据修复同步完成**：第二个 `[Unreleased]`（内容跨 2026-08~09，晚于 5.2.0 的条目）
  **原地改名为归档段**——并入顶部 Unreleased 会伪造"未发布工作"；`[5.2.0]` 段整体平移至
  正确位置（块 sha256 前后一致，纯移动零内容改动）。

### D4. 版本锚点守卫

- `config/agents.yaml` 与 `docs/troubleshooting.md` 不再内嵌 MAOP 版本号（此前分别残留
  v4.3 / v4.3.0）；新增测试扫这两个文件里 `MAOP v\d` 字面量即红。第三方 CLI 版本
  （如 `Cline v3.0.46`）不受影响。

### D5. 宽口径异常棘轮（2026-10-28 追加）

静默门禁只抓「处理器里没有日志/重抛」的形态，但**宽口径 `except Exception`** 是另一类
假降级来源：它带 logger，看起来合规，实际把 `AttributeError`/`TypeError`/`KeyError`
这类**编程错误**也一起吞掉，于是故障被记成"降级"而不是崩溃。

- **实测**：`except Exception` / `except BaseException` 共 1156 处（另有 2 处裸 `except:`）。
- **硬失败**：裸 `except:` 一律红（会连 `KeyboardInterrupt`/`SystemExit` 一起吞），不进棘轮。
- **棘轮**：`BROAD_EXCEPT_BASELINE = 1142`，双向——新增即红；收窄后必须同步下调。
- **本轮实收窄 14 处**：所有「`try` 体只有 `import`」的可选依赖守卫改为
  `except ImportError`（`mcp_hub_metrics`、`mcp_cache`、`mcp_concurrency`、
  `agent_scanner`、`multimodal` 等）。这类改写零语义风险：`try` 体里除了 import
  没有别的语句，能抛的只有导入错误。
- **不做**：不批量改剩余 1142 处。逐处收窄需要知道每个站点的**预期异常集**，批量
  替换会把真正的宽口径兜底变成漏兜。剩余存量按模块随重构逐步收窄，每收一次降一次基线。

### D6. 合法宽口径的登记与基线校准（2026-10-28 追加，针对 `refactor/layering-closeout`）

该分支新增的「健康探测 / A2A 网关 / middleware 三块拆分」等代码带来一批宽口径
`except Exception`，门禁随之判红（1142 → 1159）。逐处复核后的处置分两类：

**（一）实收窄 5 处**（异常集可枚举，语义不变）：

| 站点 | 原 try 体 | 收窄为 | 依据 |
|---|---|---|---|
| `distributed_scheduler/_scheduler.py` 顶部两个可选依赖守卫 | 只有 `from ... import X` | `except ImportError` | try 体无其它语句 |
| `health_service.py` `_cli_ping` 的 `subprocess.run` 兜底 | `subprocess.run([cli_path, "--version"], ...)` | `(OSError, ValueError, subprocess.SubprocessError)` | 上一分支已捕 `TimeoutExpired`/`FileNotFoundError`；`run()` 的剩余异常集就是这三个 |
| `health_service.py` `_tcp_ping` 的 `wait_closed()` | `await writer.wait_closed()` | `(OSError, RuntimeError)` | 关闭回执只会抛连接类 `OSError` 或「事件循环已关闭」`RuntimeError` |
| `ws_dag.py` `_log_task_exc` 回调用 `t.exception()` | `t.exception()`（`t.cancelled()` 已先判） | `asyncio.InvalidStateError` | 任务已取消时抛的是 `CancelledError`（`BaseException` 子类，原宽口径根本抓不到），能抓到的只有未完成时的 `InvalidStateError` |

**（二）登记为合法宽口径 16 处**（无法静态枚举异常集，必须宽口径；逐条判据）：

| 文件 | 处数 | 判据 |
|---|---|---|
| `core/scheduling/health_service.py` | 5 | 4 处是 `await` 探测协程（`probe_all_enabled` / `wait_for(to_thread(_cli_ping))` / `_http_ping` / `_tcp_ping`）——协程可抛任意异常，且探测循环**必须**不因单节点异常退出；1 处 `catalog.update_health()` 是未知对象的持久化方法（DB 驱动异常集不可枚举） |
| `core/coordination/a2a_gateway.py` | 5 | 4 处是 `await bridge/server.start()|stop()`（第三方传输实现的 lifeycle 钩子），1 处 `await client.send(...)` 需要把任意失败转成 A2A 协议错误响应 |
| `core/security/middleware/_auth.py` | 3 | 2 处认证链同时含 `await call_next()` 与 `auth_checker.validate()` 等未知对象方法，且必须把失败落成 500 而非让中间件崩溃；1 处 `mgr.record_usage()` 是用量记录 best-effort |
| `core/security/middleware/_protection.py` | 2 | 1 处 `import redis` + `Redis.from_url` + `ping`（redis 可能未安装，写 `except redis.RedisError` 会在未导入时 `NameError`），1 处 `self._redis_check()` 失败需降级到内存限流 |
| `delegate/dispatch_core.py` | 1 | 健康门禁（`AgentCatalog.default()` + `get_failure_detector()` + `get_weight()`）是**显式 non-blocking**：门禁自身不可用不得阻断派发 |

- **基线校准**：`BROAD_EXCEPT_BASELINE` 1142 → 1159，双向仍然成立（再新增仍红，
  再收窄则须下调）。校准依据就是上表：1142（本分支之前）+ 16（登记）+ 1（并发改动）= 1159。
- **为什么不加 `# broad-except-ok:` 标记**：silent 标记解决的是「处理器里没有任何痕迹」，
  需要逐点注释；宽口径的两类合法形态（`await` 未知协程、第三方库未知异常集）是**判据**
  而非逐点理由，用一张登记表更适合复核；且逐点标记会与本分支在途改动频繁冲突。
- **复核条件**：若某处 `try` 体缩小到只剩 import / 单一已知 API，必须收窄并下调基线；
  新增宽口径若不属于上表两类，一律红。

### D7. 分模块推进：`core/agent/` 首批（2026-10-29）

存量不能批量改（D5 的理由），改为**一次一个模块**：先全量归因，只动异常集可静态
枚举的站点，其余登记。`core/agent/` 166 处 → 141 处，实收窄 **25 处**：

| 类别 | 处数 | 收窄为 | 依据 |
|---|---|---|---|
| YAML / 文件读取 | 4 | `(OSError, yaml.YAMLError)` | `open()` + `yaml.safe_load()`，无其它语句；`tool_policy.py` 为此补了模块级 `import yaml` |
| `json.loads` 落库回读 | 3 | `(ValueError, TypeError, KeyError)` | `json.JSONDecodeError ⊂ ValueError`；`row["col"]` 缺列是 `KeyError` |
| SQLite CRUD | 11 | `(OSError, sqlite3.Error[, ValueError, TypeError])` | `sqlite_connect` 只在 open 失败时抛 `OSError`，`execute` 抛 `sqlite3.Error`；带 `json.loads`/`model_dump_json` 的站点另加后两项。涉及 `a2a.py`、`hook_manager.py`、`agent_catalog.py`、`agent_registry.py` |
| 标准库探针 | 5 | `ImportError` / `(ValueError, TypeError)` / `(NameError, TypeError)` / `(ImportError, AttributeError)` | `from maop import __version__`、`packaging.Version()`（`InvalidVersion ⊂ ValueError`）、`get_type_hints()`、`importlib.import_module()` + `getattr()` |
| 子进程 | 1 | `(OSError, subprocess.SubprocessError)` | `git status` 的 `subprocess.run(..., timeout=5)`，`TimeoutExpired ⊂ SubprocessError` |
| pydantic 反序列化 | 1 | `(ValueError, TypeError)` | `ValidationError ⊂ ValueError` |

**剩余 141 处的归因**（按 AST 分类实测）：

| 类别 | 处数 | 不可收窄的理由 |
|---|---|---|
| `unknown_call` | 95 | `try` 体调用的是 `self._store` / `self._catalog` / `adapter.health_check()` / `self._run_async(...)` 等**未知实现**的对象方法，异常集取决于运行时注入的实现，静态无法证明 |
| `has_await` | 21 | `await` 未知协程（`websockets.connect`、`self._ws.close`、`client.stream`、LLM 分解回调），协程可抛任意异常 |
| 其余 | 25 | 插件 / hook 回调（`init_fn(config)`、`hook(...)`、`callback(event)`）——外部代码，必须宽口径；以及 `_yaml`/`ImageStore`/`ConfigLoader` 构造这类组合 try 体 |

**特别注意**：`llm_providers.py` 的 3 处流式兜底**故意不收窄**为 `httpx.HTTPError` ——
`try` 体内还有 `chunk.get(...)`，上游返回合法 JSON 但结构不是对象时会抛
`AttributeError`；收窄会让畸形响应从「产出错误 token」变成「生成器直接抛异常」，
属于行为变更而非清理。

**教训**：`tests/test_tool_manager.py::test_packaging_parse_error` 原本用
`Exception("parse boom")` 模拟 `Version()` 失败，收窄后该用例失败 —— 它断言的其实是
「宽口径」，不是真实异常类型。已改为 `InvalidVersion`（`ValueError` 子类）。

### D8. 分模块推进：`core/scheduling/` 第二批（2026-10-29）

31 处 → 29 处，可静态证明的只有 2 处（这个模块的比例明显低于 `core/agent/`）：

| 站点 | 处理 | 依据 |
|---|---|---|
| `distributed_scheduler/_models.py::_json_dumps` | `(TypeError, ValueError, RecursionError)` | `json.dumps(..., default=str)` 已由 `default` 兜住绝大多数对象，剩下的是循环引用（`ValueError`）、键类型非法（`TypeError`）、超深嵌套（`RecursionError`） |
| `supervisor_status.py::_routing_key_conflict` | **删掉 try/except**，只留 `return None` | `try` 体只有 `return None`，不可能抛异常，是重构残留的死兜底 |

**剩余 29 处的归因**：

| 类别 | 处数 | 代表站点与不可收窄的理由 |
|---|---|---|
| `has_await` | 13 | `health_service` 的 4 个探测协程（已登记 D6）、`supervisor_patrol`/`supervisor_health` 的 `asyncio.wait_for(self._ping(...))`、`task_splitter` 的 `provider.chat`、`_failure.py` 的 `detect_failures`、`distributed_checkpoint_coordinator` 的分层检查点写入 |
| `unknown_call` | 13 | `self._redis.xrange/xadd/xgroup_create/delete`（`_redis: Any` 是注入的协议对象，模块本身不 `import redis`，写 `except redis.RedisError` 会在未装时变 `NameError`）、`self._registry.*` / `self._failure_detector.*` / `self._detector.get_agent_health`、prometheus `gauge.set` / `counter.inc`、`logger.warning` 组合体 |
| 事件发布 | 3 | `failure_detector` 与 `supervisor_status` 的 `Event(...)` + `publish`/`ensure_future`，异常集取决于 `event_bus` 实现；`task_splitter` 的 `LLMProviderFactory` 解析链同理 |

**一个反直觉的结论**：`core/scheduling/` 的宽口径密度高但可收窄率极低（6.5% vs
`core/agent/` 的 15%），原因是这一层几乎全部是「调用注入的 registry/redis/detector」——
异常集由运行时实现决定，静态分析只能证明「不该收窄」。这也说明按模块推进比按类别
推进更可靠：两类形态在各模块的占比差异很大。

### D9. 分模块推进：`memory/` + `core/backends/` + `core/reliability/`（2026-10-29）

三批合计收窄 **42 处**（1132 → 1090）。这三个模块自己 `import sqlite3` 的比例高，
可收窄率明显高于调度层。

| 模块 | 总数 → 剩余 | 收窄 | 主要形态 |
|---|---|---|---|
| `memory/` | 64 → 50 | 14 | `store.py` 4（init / 落库 / 查询）、`vector_search.py` 6（索引读写）、`manager_mixin.py` 3、`facade.py` 1 |
| `core/backends/` | 59 → 44 | 15 | `data.py` 5（FTS5 四件套 + init）、`db_backup.py` 3（`unlink`×2 + WAL checkpoint）、`migration.py` 4（execute + ROLLBACK）、`db_utils.py` 1、`artifact_store.py` 1、`image_store.py` 1 |
| `core/reliability/` | 95 → 82 | 13 | `message_queue.py` 7（DDL / ACK / stats / query / purge / dead-letter）、`circuit_breaker.py` 3（failover 持久化与事件查询）、`change_tracker.py` 1、`log_rotate.py` 1、`self_heal.py` 1 |

**收窄类型**：
- `(OSError, sqlite3.Error)` —— `sqlite_connect`/`_connect` + `execute*` 的标准组合；`sqlite_connect` 在建连失败时抛 `OSError`，`execute` 抛 `sqlite3.Error` 及其子类
- `OSError` —— 纯文件操作（`path.unlink()`）
- `(ImportError, AttributeError)` —— prometheus 指标导入 + `Gauge.set`
- `(ValueError, TypeError)` / `(NameError, TypeError)` —— `json.dumps`、`get_type_hints`

**故意保留宽口径的 3 类**（新增登记，不计入收窄）：

| 形态 | 站点 | 理由 |
|---|---|---|
| log-then-reraise 兜底 | `data.py::_execute` / `_query` / `save_checkpoint`、`message_queue.py::enqueue` | `try` 体除 DB 调用外还有 `dict(zip(columns, row))` —— `cursor.description` 为 `None`（非 SELECT 语句）时抛 `TypeError`；收窄会让「记录后重抛」退化成「无日志抛出」，损失可观测性 |
| `yield conn` 的连接管理器 | `data.py::_connect`、`db_utils.py::connection` | `yield` 会把调用方 `with` 体内的**任意**异常带进 `except`，异常集由调用方决定，静态不可枚举 |
| 第三方句柄 | `backends_distributed.py::transport.close()`、`backends_redis.py`/`backends_vault.py` 的 `self._client.*` | 类型注解是 `Any`，异常集取决于注入的 etcd/redis/hvac 客户端 |

**一个被 AST 分类器误判的坑**：`close` / `execute` / `commit` 这些名字既可能是 sqlite
方法，也可能是第三方客户端方法。`backends_distributed.py:427` 的 `transport.close()`
被判成「可收窄」，实际是 etcd 的 transport —— 这类必须人工核对接收者类型，不能只看
方法名。

**统计口径提醒**：`_json_dumps`（D8）这类「`default=str` 已兜住多数对象」的站点，
`RecursionError` 必须显式列入，因为它是 `RuntimeError` 的子类而不在 `Exception` 的
常规假设里 —— 若省略，深层嵌套对象会从「降级为 str」变成「抛异常」。

### D10. 分模块推进：`dashboard/`（2026-10-29）

`dashboard/` 272 处 → 249 处，实收窄 **23 处**（1090 → 1067）。这一层的宽口径
密度最高（272/全仓 26%），但可收窄率只有 8.5% —— 绝大多数是「调用 service /
bridge / registry 的未知实现」与「`await` 未知协程」。

| 形态 | 收窄 | 站点 |
|---|---|---|
| SQLite 读写 | 9 | `data_proxy.py` 2（`(OSError, sqlite3.Error, TypeError)`，非 SELECT 时 `cursor.description is None`）、`provider.py` 2、`auth_service.py` 3（`PRAGMA` / `ALTER TABLE`）、`memory_service.py` 1、`system_service.py` 1 |
| 文件读取 / `stat` / `glob` | 7 | `channels/security.py` 2、`provider.py` 1、`routers/system/overview.py` 3、`services/data_service.py` 1 |
| 特定库的已知异常 | 4 | `n8n.py` 的 `model_validate_json` → `(ValidationError, TypeError)`、`channels/routing.py` 的 `datetime.fromisoformat` → `ValueError`、`observability_service.py` 的 otel 探针 2 → `(ImportError, AttributeError)` |
| 死 try | 1 | `memory_service.py::get_memory_stats` 的 `info["transform"] = {...}` |
| 由宽口径掩盖的存量缺陷 | 2 | `auth_service.py::db_list_users` 的缺列退化 → `(IndexError, KeyError, TypeError)`；`auth_service.py::login` 的 `'tenant_id' in row` → `TypeError` |

**收窄直接暴露了一个真 bug**：`system_service.py::maintain_vacuum` 写的是
`with sqlite_connect() as conn:` —— `sqlite_connect` 的 `db_path` 是必填参数，
这句每次都抛 `TypeError`，被 `except Exception` 静默吞成
`"Skipped: vacuum unavailable"`，于是「数据库压缩」这个维护动作从未真正执行过。
收窄后 `test_router_control.py::test_vacuum` 变红，才把它逼出来；已改为
`sqlite_connect(str(state.MAOP_ROOT / "data" / "maop.db"))`。这是 D5 里
「批量替换会把真兜底变成漏兜、但也会把真 bug 变成漏网」的反面证据 ——
收窄的价值正在于让被吞掉的编程错误重新出现。

**故意保留宽口径的四类**（新增登记，不计入收窄）：

| 形态 | 站点 | 理由 |
|---|---|---|
| 诊断端点的导入探针 | `routers/system/overview.py` 3、`routers/system/v4_misc.py` 1、`routers/state.py` 1、`services/plugin_service.py` 1 | 契约就是「任何原因导入失败 → `ok: false`」；收窄为 `ImportError` 会让子模块的导入期异常（pydantic 校验、模块级副作用）直接 500 掉健康检查端点。`routers/state.py::_lazy_import` 更是在**模块导入期**调用，收窄会把降级变成启动失败 |
| aiosqlite / 协程句柄 | `provider.py` 2、`services/data/analysis.py` 12 | `aiosqlite.Error` 是 `RuntimeError` 子类，不等于 `sqlite3.Error`；同一个 try 里还混着 `round` / `model_dump` |
| 未知 service / bridge 实现 | `services/data_service.py` 8、`services/evolution_service.py` 6、`routers/data.py` 9、`routers/system/agent_admin.py` 8 | 异常集由被调实现决定，静态只能证明「不该收窄」 |
| `await` 未知协程 | `ws_dag.py`、`_ws_manager.py`、`services/upgrade_service.py` 6 等 91 处 | 同 D8 |

**两处被门禁校验器拦下的保守保留**：`provider.py::_count_memory_entries`
（try 体只有 `Path.glob` + `len`，本可收窄为 `OSError`）与
`provider.py::_count_suggestions`（`read_text` + `json.loads`，本可收窄为
`(OSError, ValueError)`）在同文件 aiosqlite 路径共存的情况下被判定为
「易被误认为已覆盖 aiosqlite 分支」，暂缓；`memory_service.py` 的
`stats["bloom_filter"]` 死 try 同理（删除整段 try 被判定为语义变更）。
三项留作下一轮单独复核。

**AST 分类器的新误判**：`routers/system/overview.py` 的两处统计循环被判成
`unknown_call`（因为 `f.is_file` / `.glob` 不在白名单），实际整个 try 体只有
`Path` 上的文件系统操作，异常集就是 `OSError` 一族 —— 与 D9 的 `close`/`execute`
相反，这次是「窄异常被误判成不可收窄」。

## 影响

- 新增：`scripts/check_silent_exceptions.py`、`scripts/check_file_size.py`、
  `tests/test_quality_gates.py`（三闸门的变异自证 + 双向一致性）、本 ADR。
- 修改：`check_docs_consistency.py`（+CHANGELOG 校验）、`CHANGELOG.md`（结构修复）、
  `config/agents.yaml`、`docs/troubleshooting.md`、`docs/ci-gates.md`、ci.yml（docs-gate 步骤）。
- 控制流零改动：本次不修任何运行时代码行为（存量清尾是独立批次）。

## 后续（明确排期，禁止无限期挂账）

| 项 | 内容 | 状态 |
|---|---|---|
| 静默清尾 | 81 条逐条分类（标记或改 warning），基线归零 | **已完成**（同批，基线=空集） |
| suppress 棘轮 | 47 处（`contextlib.suppress`）——当前只棘轮计数，逐处语义审查后排期 | 排期中 |
| 巨型文件拆分 | `data_service.py` / `chat_service.py` / `agent_service.py` / `evolution_service.py` 按域拆分（参照 core/mcp/ 先例） | 排期中 |
| bandit Medium 债 | 147 条（B608×141 等）分类处置 + 门禁 `-lll`→`-ll` + nosec 清单快照 | 下一批（独立 PR） |
| keyring 后端 | 个人版 secret 可选 keyring 后端（可选 extra + 回退可见标注） | 待排期 |
