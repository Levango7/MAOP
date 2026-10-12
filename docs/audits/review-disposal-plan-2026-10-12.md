# MAOP 复评遗留问题处置方案（2026-10-12，供独立审核）

> 来源：外部工具《MAOP 复评报告（2026-10-12）》。本文件先把报告的每条结论**在最新 master
> （`1c5ce5e`，含 M2.2 B1）上重新实测核对**，再给方案。核对结果与报告有出入处已标注。

## 0. 核对结果（报告 vs 实测）

| 报告结论 | 实测（当前 master） | 判定 |
|---|---|---|
| bandit Medium 债 96 条（B608×84+B310×10） | **147 条**：B608×141、B310×3、B103×1、**B615×2（本轮 B1 新引入）**；门禁 `bandit -r maop/ -lll`（ci.yml:824） | 数量有出入，**问题属实且更大** |
| 巨型文件 7 个 >800 行 | **8 个**：data_service 1147、chat_service 1144、001_initial_schema 938、preset_agents 919、_register_routes 823、distributed_scheduler 821、agent_service 810、evolution_service 806 | 属实（多 1 个） |
| 静默 pass/continue 59 处逃过正则 | **51 处**（473 源文件 / 1627 个 except / 1214 带处理）；逃过捕获的**真实原因不是多行正则**（脚本 `\s*` 已跨行）而是它只匹配 `except Exception`——存量几乎全是**窄类型**（ImportError/ValueError/FileNotFoundError/WebSocketDisconnect…） | 数量近似，**根因诊断需更正** |
| CHANGELOG 结构混乱 | 属实：两个 `## [Unreleased]`（:40 与 :1789）、`[5.2.0]` 排在 `[5.0.0]` 之后（:1980 vs :2012）、无门禁 | 属实 |
| `5.1.0-personal` 非 SemVer | **报告误判**：`5.1.0-personal` 是合法 SemVer **prerelease**（`5.1.0-personal` < `5.1.0`，现排序也正确） | 不修（门禁须接受 prerelease） |
| agents.yaml `MAOP v4.3` 漂移 | 属实（`config/agents.yaml:19`，当前 5.3.1） | 属实 |
| 个人版 secret 默认 local、keyring 未接入 | 属实（全仓无 keyring 引用） | 属实（低优先，见 §4） |
| 建议：nightly 给 enabled agent 跑 `--version` 探活 | **建议本身有问题**：nightly 跑在 GitHub runner，仓库里 30+ 外部 CLI（cline/kimi/gemini…）在该 runner 上**全部不存在**，探活报告恒为"全 missing"，无信息量 | 改造后做（见 §4） |

## 1. PR-A：防复发门禁 + 结构性修复（本批做）

### A1 巨型文件门禁
- 新 `py/scripts/check_file_size.py`：扫 `py/maop/**/*.py`，阈值 **800 行**；
  存量超标文件进 **ALLOWLIST（带理由 + 拆分排期编号）**；allowlist 之外新增超标 → 退出码 1。
- **migrations 目录整体豁免**（`001_initial_schema.py` 938 行=SQL 迁移本体，拆分反而不可审计）。
- 进 CI：并入既有 "Docs consistency gate" job（
  `python scripts/check_docs_consistency.py --gate && python scripts/check_file_size.py`）——
  不新增 job 名（避免动 required checks 清单）。
- 测试：`tests/test_file_size_gate.py`：①allowlist 与实际超标集合**双向一致**（清单漂移即红）
  ②变异自证（超过阈值且不在清单的文件 → 检出）③阈值可注入（小阈值跑自证，不依赖真实大文件）。

### A2 CHANGELOG 结构修复 + 门禁
- **先修数据**：①`## [Unreleased]`（:1789）是历史残留段——按仓库先例（"先例保留不碰"注释）
  核实其内容后**并入顶部 [Unreleased] 或改名归档段**；②把 `[5.2.0]` 段移动到 `[5.2.1]` 之前
  （纯段落平移，内容零改动；移动本身不改语义，附 PR 内 diff 证据）。
- **再上门禁**（`check_docs_consistency.py` 内新增检查函数，复用既有 CLI）：
  ①`## [Unreleased]` 至多 1 个；②`## [X.Y.Z(-prerelease)?]` 标题按 SemVer **降序**（prerelease 按
  SemVer 规则参与比较：`5.1.0-personal < 5.1.0`）；③标题版本号必须匹配
  `\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?`（`[2.x]`/`[1.x]` 这类**历史折叠段**在清单尾部，用显式豁免名单）。
- 测试：与现有 docs-gate 测试同款（合成 CHANGELOG 变异自证 ×3：重复 Unreleased / 降序破坏 / 非法版本号）。

### A3 静默吞异常清尾 + 门禁
- **分类处置**（ADR-018 精神：吞异常必须有理由，但**不是一律加日志**）：
  - 真错误路径（如 `discovery_mixin` 的 OSError、`compliance_manager` 的 OperationalError、
    `ide_extension_adapter:317`）→ 按 ADR-018 格式 `logger.warning("[module] … continuing: %s", exc)`。
  - 设计性静默（`except ImportError: pass` 可选依赖探测、`FileExistsError` 锁竞争、
    `WebSocketDisconnect`、`KeyboardInterrupt`、`RuntimeError: no running loop`）
    → 加**显式理由标记** `# silent-ok: <一句理由>`（新约定，写进 docs/ci-gates.md）。
- **门禁**新 `py/scripts/check_silent_exceptions.py`：AST 扫描 `except` 体首个语句为
  `pass`/`continue` 且无 `# silent-ok:` 标记、块内无 `logger/log/raise/print` → 红。
  AST 而非正则（避开报告指出的"多行写法逃过正则"类问题）。
- 测试：变异自证（合成模块：裸 `except X: pass` → 检出；带标记/带日志 → 放行）+
  全仓基线归零断言（存量 51 处全部处置后计数为 0）。

### A4 agents.yaml 版本锚点
- `config/agents.yaml:19` 改为**不嵌版本**的表述（"MAOP self-referential agent…"），
  避免又要人肉同步；新增测试 `tests/test_agents_yaml_version_anchor.py`：扫
  `config/agents.yaml` 内含 `MAOP v\d` 字样的行 → 红（第三方 CLI 版本如 `Cline v3.0.46` 不受影响）。

## 2. PR-B：bandit Medium 债清零 + 门禁恢复 `-ll`（本批做）

处置政策（先分类再动手，141 处 B608 已按插值表达式归类为 53 类）：

| 类 | 例 | 处置 |
|---|---|---|
| 常量插值 | `_LICENSE_TABLE`、`FEEDBACK_COLUMNS`、`self._table`（受控）、`_AUDIT_TABLE` | `# nosec B608 — 模块常量表名/列清单` |
| 占位符生成 | `placeholders`、`col_list`、`', '.join(sets)`（由 `?` 组成） | 同上（值是 `?`，非输入） |
| 子句构造器产物 | `where`/`where_sql`/`where_clause`/`set_clause` | **逐个核构造器**：只允许"固定子句 + `?`"，核实后 nosec + 理由；发现值拼接 → **改参数化** |
| 变量表名 | `table`（23 处）、`fts_name`、`json_column`、`c` | 核来源：闭集（枚举/常量推导）→ nosec；来自外部输入 → 加 `validate_identifier()` 白名单 + nosec |
| 其他 | B310×3（`urlopen`）、B103×1（chmod）、**B615×2（B1 新引入的 `from_pretrained` 无 revision）** | B310：确认 URL 来源 + scheme 校验；B103：核权限位；B615：**加 `revision=` 钉版**（真实修复，不打 nosec） |

- 门禁：ci.yml `bandit -r maop/ -lll` → **`-ll`**（与注释里的两个月承诺对齐），
  PR 内附"修复前后计数对照"（147→0）。
- 测试：`tests/test_bandit_medium_clean.py`——**在本地跑 bandit**（skipif 未安装）
  断言 Medium 计数为 0，防"注释漂移回来"。

## 3. 明确不做（本批）

- **拆 data_service.py / chat_service.py**：1147/1144 行拆分是独立重构（参照 mcp_hub 拆法），
  本批只做门禁 + allowlist 登记（附拆分排期），不在同一 PR 里混做（降低回归面）。
- 其余 >800 文件的拆分同上。

## 4. 后续（下一批，已设计未实施）

- **keyring 后端**（可选 extra `[keyring]` + `KeyringSecretBackend` + settings 选择 + 优雅回退
  + 文档 + 测试）。风险点=后端在无桌面环境（CI/服务器）不可用 → 必须回退 local 且**可见标注**。
- **agent 探活（改造版）**：nightly 跑在 GitHub runner（外部 CLI 全不在）→ 改为
  **`maop agents probe` 子命令**（本机可跑：对 enabled agent 跑 `--version` 探活，输出表格式报告，
  不卡死；CI 只断言"命令可跑 + 报告结构"）；`agents.yaml` 里"auth may be broken"类描述改为
  机器可验证字段（`last_verified_at` + 探活结果回填路径留白）。

## 5. 审核要点（请重点挑战）

1. A1 的 allowlist 双向一致性检查是否足够防"清单腐化"？migrations 豁免的边界？
2. A2 把 `[5.2.0]` 段整体平移是否可接受（历史段序修正 vs "历史不可改"的取舍）？
   第二个 `[Unreleased]`（:1789）应并入还是归档改名？请核该段内容后给判断。
3. A3 的 `# silent-ok:` 新约定是否会被滥用成"静默后门"？门禁能否防住（AST 扫描是否能被绕过）？
   51 处里哪些**不该**加日志（我列的设计性静默清单是否有误判）？
4. A4 的表述改法（去掉版本）是否会丢信息？有无比"删版本"更好的做法（如运行时插值）？
5. PR-B 的 nosec 政策：`# nosec B608 — <理由>` 的粒度（行级 vs 函数级）、
   理由模板是否足够让未来审计者复核？"变量表名走 validate_identifier + nosec"是否真安全
   （请核 `py/maop/core/backends/db_utils.py` 的 `validate_identifier` 实现强度）？
6. 排序与分批是否合理？有没有该提前的高风险项（例如 B615 打 nosec 而不是钉版就是错的——我按钉版处理）？

---

## 附录 A：bandit B608（141 处）处置表（2026-10-12 生成，供后续批次逐站点执行）

分类口径：按 f-string 内插值表达式自动归类；`builder`/`other` 两类**必须人工读构造器**（审核要求「行号→类→构造器证据」），不得批量 nosec。

| 类 | 处数 | 处置 |
|---|---|---|
| `builder` | 40 | **人工核构造器**：只允许固定子句 + `?`；发现值拼接则改参数化 |
| `const` | 32 | 模块常量表名/列清单 → `# nosec B608 — 模块常量` |
| `const-or-literal` | 26 | 语句内无插值（常量拼接）→ 同上 |
| `generated` | 19 | 占位符/子句由代码生成（值是 `?`）→ nosec + 理由 |
| `other` | 17 | **人工核**：确认是标识符（闭集/自省）还是值（须参数化） |
| `attr` | 7 | 类内受控属性 → nosec + 理由 |

### 按文件分布

| 文件 | 处数 |
|---|---|
| `maop/core/tenant/compliance_manager.py` | 11 |
| `maop/core/agent/auth/agent_proxy_gateway.py` | 10 |
| `maop/dashboard/services/chat_service.py` | 10 |
| `maop/core/agent/auth/credential_vault.py` | 9 |
| `maop/core/agent/auth/harness_auth.py` | 8 |
| `maop/core/memory/vector_store.py` | 8 |
| `maop/dashboard/services/agent_versions_service.py` | 8 |
| `maop/memory/search.py` | 7 |
| `maop/core/backends/data.py` | 6 |
| `maop/core/tenant/gdpr_manager.py` | 6 |
| `maop/core/agent/tools/tool_audit.py` | 5 |
| `maop/migrations/sqlite_to_pg.py` | 5 |
| `maop/core/memory/knowledge_graph.py` | 4 |
| `maop/core/security/session.py` | 4 |
| `maop/core/tenant/hierarchy.py` | 4 |
| `maop/migrations/memory_migration.py` | 4 |
| `maop/core/memory/knowledge_extractor.py` | 3 |
| `maop/core/tenant/rls.py` | 3 |
| `maop/core/backends/image_store.py` | 2 |
| `maop/core/memory/episodic_store.py` | 2 |
| `maop/core/reliability/circuit_breaker.py` | 2 |
| `maop/core/tenant/audit.py` | 2 |
| `maop/core/agent/delegation/subagent_db.py` | 1 |
| `maop/core/agent/delegation/subagent_lifecycle.py` | 1 |
| `maop/core/agent/lifecycle/agent_registry.py` | 1 |
| `maop/core/agent/memory_ctx/worktree.py` | 1 |
| `maop/core/agent/plugins_hooks/hook_manager.py` | 1 |
| `maop/core/backends/kv_store.py` | 1 |
| `maop/core/cost_tracker.py` | 1 |
| `maop/core/mcp/mcp_hub_ops.py` | 1 |
| `maop/core/memory/multimodal_store.py` | 1 |
| `maop/core/monitoring/timeseries.py` | 1 |
| `maop/core/reliability/message_queue.py` | 1 |
| `maop/core/reliability/pipeline_checkpoint.py` | 1 |
| `maop/core/security/api_key_manager.py` | 1 |
| `maop/dashboard/routers/agent_versions_helpers.py` | 1 |
| `maop/dashboard/services/auth_service.py` | 1 |
| `maop/memory/store.py` | 1 |
| `maop/migrations/pg/vector_migration.py` | 1 |
| `maop/prompt_manager.py` | 1 |

### 需人工核对的站点（57 处）

```
maop/core/agent/auth/agent_proxy_gateway.py:344  [builder]  _AUDIT_EVENT_TABLE, where
maop/core/agent/auth/credential_vault.py:387  [other]  _VAULT_TABLE, credential_id, _VAULT_TABLE
maop/core/agent/delegation/subagent_db.py:344  [builder]  col_list, col_list
maop/core/agent/lifecycle/agent_registry.py:365  [builder]  ' AND '.join(clauses), where
maop/core/agent/plugins_hooks/hook_manager.py:603  [builder]  where
maop/core/agent/tools/tool_audit.py:206  [builder]  where
maop/core/agent/tools/tool_audit.py:210  [other]  where, 'AND' if where else 'WHERE'
maop/core/agent/tools/tool_audit.py:215  [builder]  where
maop/core/agent/tools/tool_audit.py:219  [builder]  where
maop/core/agent/tools/tool_audit.py:224  [builder]  where
maop/core/backends/data.py:458  [other]  json_column
maop/core/backends/data.py:464  [other]  json_column, table, json_column, json_column
maop/core/backends/data.py:609  [other]  fts_name
maop/core/backends/data.py:624  [other]  fts_name
maop/core/backends/data.py:646  [other]  table, fts_name, fts_name
maop/core/cost_tracker.py:387  [builder]  where
maop/core/memory/knowledge_extractor.py:276  [builder]  where
maop/core/memory/knowledge_extractor.py:280  [builder]  where, where
maop/core/memory/knowledge_extractor.py:314  [builder]  where
maop/core/memory/knowledge_graph.py:496  [builder]  where
maop/core/reliability/circuit_breaker.py:712  [builder]  where_sql
maop/core/reliability/circuit_breaker.py:737  [builder]  where_sql
maop/core/reliability/message_queue.py:677  [other]  table!r, table, where
maop/core/security/api_key_manager.py:439  [other]  col, set_clause
maop/core/security/session.py:174  [builder]  where
maop/core/security/session.py:249  [builder]  where
maop/core/security/session.py:254  [builder]  where
maop/core/security/session.py:308  [builder]  set_clause
maop/core/tenant/compliance_manager.py:261  [other]  "memory_entries", "long_term_memory", "short_term_memory", table!r, table
maop/core/tenant/compliance_manager.py:324  [builder]  table
maop/core/tenant/gdpr_manager.py:485  [builder]  where
maop/core/tenant/gdpr_manager.py:568  [builder]  where
maop/core/tenant/gdpr_manager.py:702  [builder]  table
maop/core/tenant/gdpr_manager.py:716  [builder]  table, table
maop/core/tenant/hierarchy.py:274  [builder]  where
maop/core/tenant/rls.py:170  [builder]  columns, table
maop/core/tenant/rls.py:184  [builder]  where, columns, table
maop/core/tenant/rls.py:212  [builder]  table, col_list, placeholders
maop/dashboard/services/agent_versions_service.py:161  [builder]  where_sql
maop/dashboard/services/chat_service.py:798  [builder]  where_sql
maop/dashboard/services/chat_service.py:882  [builder]  where_sql
maop/dashboard/services/chat_service.py:937  [builder]  FEEDBACK_COLUMNS, where_sql
maop/memory/search.py:90  [other]  r.id for r in fts_results, placeholders
maop/memory/search.py:138  [builder]  where_sql
maop/memory/search.py:198  [builder]  where_sql
maop/memory/search.py:264  [builder]  where_sql
maop/memory/search.py:315  [other]  kw
maop/memory/search.py:326  [other]  kw, field, field
maop/migrations/memory_migration.py:180  [builder]  table
maop/migrations/memory_migration.py:186  [builder]  table
maop/migrations/memory_migration.py:308  [builder]  table
maop/migrations/memory_migration.py:646  [other]  tr.table
maop/migrations/sqlite_to_pg.py:303  [builder]  table
maop/migrations/sqlite_to_pg.py:324  [other]  c, col_list, table
maop/migrations/sqlite_to_pg.py:329  [other]  c, col_list, table, "lim": batch_size
maop/migrations/sqlite_to_pg.py:345  [builder]  col_list, table
maop/migrations/sqlite_to_pg.py:366  [other]  c, c, table, col_list
```

> 已完成（真修复 PR #91）：B615×2 钉 40-hex 模型 revision（含夹具 revision 与守卫测试）、B310×3（MCP transport 方案白名单 + marketplace 已有 SSRF 守卫 + deploy host 校验）、B103（exec 位只加不重写权限）。
>
> **2026-10-12 附录 A 已执行完毕（B608 清零）**：141 处逐站点处置（84 分类 + 57 人工核，结论全部安全、无真注入面），`bandit -r maop/ -ll` 由 148 → **0**，ci.yml 门禁切到 `-ll`；nosec 清单快照升级为 `path:RULE:count`（44 条目/146 行）。**本附录的处置表已完成使命**，后续新增 B608 由门禁 + 清单快照兜底。
