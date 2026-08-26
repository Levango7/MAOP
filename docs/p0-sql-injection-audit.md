# P0 SQL 注入核实与修复审计报告（Task 485）

## 1 审计范围与方法

### 1.1 扫描范围

- 目录：`py/maop/`（含 `core/`、`memory/`、`migrations/`、`dashboard/` 等全部子包）
- 排除：`py/tests/`（仅新增测试文件，不修改现有测试）

### 1.2 检测模式

| 编号 | 模式 | 说明 |
| --- | --- | --- |
| M1 | `f"SELECT/INSERT/UPDATE/DELETE/ALTER/PRAGMA/CREATE/DROP ... {expr}` | f-string 单引号/双引号拼接 |
| M2 | `f"""SELECT ... {expr}` | f-string 三引号多行拼接 |
| M3 | `execute(f"...")` / `_query(f"...")` / `text(f"...")` | 直接执行拼接 SQL |
| M4 | `.format(` 与 SQL 关键字组合 | str.format 拼接 |
| M5 | `%s ... % (args)` 与 SQL 组合 | % 格式化拼接 |
| M6 | `"..." + var`（where/clause/table/column 类变量） | 字符串加号拼接 |
| M7 | `ORDER BY {`、`GROUP BY {`、`LIMIT {` | 子句位置插值 |

### 1.3 分析维度

对每处插值逐一确认：插值来源（API 参数／内部调用／硬编码常量／schema 反射）、上游校验（`validate_identifier` 白名单正则 `^[a-zA-Z_][a-zA-Z0-9_]*$`、frozenset 白名单、dict 映射、`int()` 强转、动态 `?` 占位符）以及暴露面（是否可被 Dashboard API 触达）。

## 2 总体统计

共发现 **80 处** f-string SQL 插值构造点（与安全审核报告"约 82 处"吻合，差异来自对中间变量 `where_sql = f"WHERE {...}"` 的归并口径），分布于 33 个文件。

表：风险分级统计对照表

| 风险等级 | 数量 | 处置 |
| --- | --- | --- |
| 高危（插值直接来自 API 请求参数且无验证） | 0 | 无需修复 |
| 中危（公共 API 参数理论可注入） | 2 | 已修复（见第 3 章） |
| 低危／豁免（内部常量、白名单、参数化、强转防护） | 78 | 记录豁免理由（见第 4 章） |

Dashboard 层（`py/maop/dashboard/`）60 处 SQL 命中均为静态字符串（无任何拼接），未计入上述 80 处。

## 3 修复项（中危）

### 3.1 TenantRLS.scoped_select — columns/order_by 未校验

- 位置：`py/maop/core/tenant/rls.py`（原 L113-L131）
- 问题：该方法经 `TenantManager.scoped_select`（`manager.py:274-278`）作为公共 API 透传，`table` 已有 `validate_identifier` 校验，但 `columns`（拼入 SELECT 投影位）与 `order_by`（拼入 ORDER BY 子句）为调用方原始字符串，未经任何校验；若未来从 API 层透传用户输入即可注入。
- 修复方式：

```python
# 代码示例：columns 列表白名单校验（Python）
@staticmethod
def _validate_column_list(columns: str) -> str:
    for part in columns.split(","):
        token = part.strip()
        if not token or token == "*":
            continue
        validate_identifier(token, "column")
    return columns
```

```python
# 代码示例：order_by 语法白名单校验（Python）
@staticmethod
def _validate_order_by(order_by: str) -> str:
    for term in order_by.split(","):
        tokens = term.strip().split()
        if not tokens:
            raise ValueError("Invalid ORDER BY: empty term")
        if len(tokens) > 2:
            raise ValueError(f"Invalid ORDER BY term: {term!r}")
        validate_identifier(tokens[0], "order by column")
        if len(tokens) == 2 and tokens[1].upper() not in ("ASC", "DESC"):
            raise ValueError(f"Invalid ORDER BY direction in {term!r}")
    return order_by
```

同时 `limit` 增加 `int()` 强转（`safe_limit = int(limit)`），并在 docstring 明确 `where` 为"调用方构造的固定模板 SQL 片段 + params 配对"的查询构造器契约。

### 3.2 MaopDatabase.fts_search — highlight_tag 可逃逸字符串字面量

- 位置：`py/maop/core/backends/data.py` L505-L507（修复后）
- 问题：`highlight_tag` 被直接拼入 `highlight({fts_name}, 0, '<{tag}>', '</{tag}>')` 的 SQL 字符串字面量内；若传入含单引号的值可逃逸字面量注入 SQL。当前默认值为 `"mark"` 且仓库内无外部调用方传入，属防御纵深加固。
- 修复方式：进入函数即校验字符集白名单：

```python
# 代码示例：highlight_tag 字符集白名单（Python）
if not re.fullmatch(r"[a-zA-Z0-9_-]+", highlight_tag):
    raise ValueError(f"invalid highlight tag: {highlight_tag!r}")
```

两处修复均不改变合法输入下的查询语义（现有测试 `test_tenant_isolation.py`、`test_enhancements.py::TestFts5Search` 全部通过）。

### 3.3 新增防御测试

新增 `py/tests/test_sql_injection_hardening.py`，共 26 个用例：

- 合法输入回归：columns 星号/逗号列表、ORDER BY 混合方向、limit 数字字符串强转、默认 highlight_tag。
- 恶意输入拒绝：columns 注入（`"* FROM sqlite_master--"`、`"(SELECT secret)"` 等 5 种）、order_by 注入（`"name; DROP TABLE items"`、`"ABS(name)"` 等 5 种）、非法方向（`DESCENDING`）、恶意标签（`"mark'</sql"`、`"'; DROP TABLE ...; --"` 等 4 种）。

## 4 豁免清单及理由

### 4.1 豁免判定标准

满足以下任一条件判为豁免：插值为纯硬编码常量；插值经 `validate_identifier` 强正则校验；插值为 frozenset/dict 白名单映射（越界回退或报错）；值为动态生成的 `?` 占位符序列；数值经 `int()` 强转；索引名经 `re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*")` 校验；表名集合经源库 schema 反射交集约束（仅限运维迁移 CLI）。

### 4.2 逐点豁免记录

表：豁免点明细对照表

| 序号 | 文件:行 | 插值内容 | 豁免理由 |
| --- | --- | --- | --- |
| 1 | agent_registry/lifecycle/agent_registry.py:145 | `{col_name} {col_def}`（ALTER TABLE ADD COLUMN） | 来自硬编码列表 `new_cols`（L137-141），纯内部常量 |
| 2 | agent_registry/lifecycle/agent_registry.py:362-365 | `{where}`（SELECT） | where 由固定子句模板（`enabled=1`/`capabilities LIKE ?`/`provider=?`）构造，值全走 params |
| 3 | mcp/mcp_hub_ops.py:330-337 | `{', '.join(sets)}`（UPDATE SET） | sets 为固定模板（`status = ?`/`updated_at = ?`/`error = ?`），值走 params |
| 4 | memory/episodic_store.py:353-370 | `{', '.join(sets)}`（UPDATE SET） | 同上，固定 SET 模板 |
| 5 | reliability/circuit_breaker.py:683,688 | `{where_sql}`（SELECT events） | 条件模板固定（`agent = ?`/`timestamp >= ?`/`timestamp <= ?`），值走 params |
| 6 | reliability/circuit_breaker.py:708,713 | `{where_sql}`（COUNT） | 同上 |
| 7 | reliability/message_queue.py:625-628 | `{table}`、`{where}`（COUNT） | `table` 经 `_VALID_TABLES` frozenset 白名单（L623）；`where` 仅内部固定串调用（L574-578） |
| 8 | migrations/pg/env.py:57 | `"{ext}"`（CREATE EXTENSION） | ext 来自硬编码元组 `("vector", "pg_trgm")` |
| 9 | prompt_manager.py:296-298 | `{placeholders}`（IN 子句） | 动态生成 `?` 占位符 `",".join("?" for _ in ids)`，值走 params |
| 10 | migrations/sqlite_to_pg.py:303 | `"{table}"`（SELECT COUNT） | 运维迁移 CLI；table 必须与源库 sqlite_master 反射结果交集（L484-487），列名来自 inspect 反射 |
| 11 | migrations/sqlite_to_pg.py:315 | `{col_list}`、`"{table}"`（分页 SELECT） | 同上；LIMIT/OFFSET 走绑定参数 `:lim/:off` |
| 12 | migrations/sqlite_to_pg.py:336 | `"{table}" ({col_list}) VALUES ({param_list})` | 同上；`:col` 具名参数由 SQLAlchemy 绑定 |
| 13 | migrations/pg/versions/001_initial_schema.py:938-939 | `"{table}"`（DROP TABLE CASCADE） | table 来自硬编码列表 `_DROP_ORDER` |
| 14 | migrations/pg/vector_migration.py:168-174 | `{cols}`（SELECT） | cols 为硬编码串 `"id, text, vector, metadata, created_at"`，表名硬编码 `vector_entries` |
| 15 | migrations/pg/vector_migration.py:276,284 | `{idx_name}`（DROP INDEX CONCURRENTLY） | idx_name 经 `re.fullmatch([a-zA-Z_][a-zA-Z0-9_]*)` 校验（L276） |
| 16 | migrations/pg/vector_migration.py:290,295 | `{idx_name}`、`{int(...)}`（CREATE INDEX） | idx_name 同上；lists/m/ef_construction 均 `int()` 强转；itype 白名单二选一（L272-274） |
| 17 | migrations/memory_migration.py:174 | `{table}`（SELECT LIMIT 0 取列） | table 由硬编码元组传入（L361：`("memory_entries","memory_traces","memory_trajectory")`） |
| 18 | migrations/memory_migration.py:180 | `{table}`（SELECT COUNT） | 同上 |
| 19 | migrations/memory_migration.py:264 | `{table}`（SELECT *） | 同上 |
| 20 | migrations/memory_migration.py:301 | `{table} ({cols_csv}) VALUES ({placeholders})` | table 同上；cols_csv 为源/目标库 PRAGMA 反射交集；placeholders 动态 `?` |
| 21 | migrations/memory_migration.py:491 | 同 L301 结构 | 同 L301（第二处写入分支） |
| 22 | memory/shared_db.py:153-154,170 | `{cols_csv}`、`{placeholders}` | cols 反射自源库 cursor.description；placeholders 动态 `?` |
| 23 | memory/search.py:122-126 | `{where_sql}`（SELECT recent） | 固定条件模板 + params |
| 24 | memory/search.py:167-183,184-195 | `{where_sql}`（FTS5 JOIN） | 同上；MATCH 表达式走 `?` 参数 |
| 25 | memory/search.py:244-248 | `{where_sql}`（regex fallback） | 同上 |
| 26 | memory/search.py:292-308 | `m.{field}`、`GROUP BY m.{field}`（facets） | field 经白名单 `{"topic","agent","tags"}` 校验，越界回退 `"topic"` |
| 27 | memory/search.py:310-316 | `{field}`（facets plain 分支） | 同上 |
| 28 | vector/pg_backend.py:473-475 | `{idx_name}`（DROP INDEX CONCURRENTLY） | idx_name 经 `re.fullmatch` 校验；来源为内部 `_default_index_name`（itype 内部常量） |
| 29 | tenant/rls.py:81 | `{table}`（PRAGMA table_info） | 经 `validate_identifier(table)` 校验（L79） |
| 30 | tenant/rls.py:88 | `{table}`（ALTER TABLE ADD COLUMN） | 同上 |
| 31 | tenant/rls.py:146-155 | `{table}`、`{col_list}`、`{placeholders}`（scoped_insert） | table 与每个 column 均经 validate_identifier；placeholders 动态 `?` |
| 32 | tenant/hierarchy.py:265-274 | `{where}`（SELECT orgs） | 固定条件模板（`tenant_id = ?`/`parent_id = ?`）+ params |
| 33 | tenant/hierarchy.py:430-441 | `{placeholders}`（3 处 DELETE IN） | 动态 `?` 占位符，值走 params 元组展开 |
| 34 | tenant/gdpr_manager.py:456-471 | `{where}`（SELECT dsr） | 固定条件模板 + params |
| 35 | tenant/gdpr_manager.py:554 | `{where}`（SELECT dpa） | 同上 |
| 36 | tenant/gdpr_manager.py:683-688 | `{table}`（导出 SELECT） | table 来自硬编码元组 `("memory_entries","long_term_memory")`（L683） |
| 37 | tenant/gdpr_manager.py:702 | `{table}`（回退 SELECT） | 同上 |
| 38 | tenant/compliance_manager.py:250-252 | `{table}`（DELETE memory） | table 来自硬编码三元组（L250） |
| 39 | tenant/compliance_manager.py:313-315 | `{table}`（导出 memory） | 同上（L313）；`_tenant_filter` 返回固定串 `" AND tenant_id = ?"` |
| 40 | tenant/audit.py:202-207 | `{where}`（审计查询） | 固定条件模板（tenant_id/action/resource/actor/timestamp 全部 `= ?`）+ params |
| 41 | tenant/audit.py:218-222 | `{' AND '.join(clauses)}`（COUNT） | 同上 |
| 42 | security/session.py:157-167 | `{where}`（sessions list） | 固定条件模板 + params |
| 43 | security/session.py:237-242 | `{where}`（COUNT） | 同上 |
| 44 | security/session.py:217,247-249 | `ORDER BY {sort_col} {order.upper()}` | sort_col 经 `_SORTABLE_COLUMNS` dict 白名单映射（L177-184），越界回退 `created_at`；order 仅接受 asc/desc 二值（L216）；LIMIT/OFFSET 走 `?` |
| 45 | security/session.py:296-301 | `{set_clause}`（UPDATE sessions） | 每个 key 先经 `validate_identifier(key, "session column")`（L296-297） |
| 46 | security/api_key_manager.py:240-253 | `{col} {decl}`（ALTER TABLE ADD COLUMN） | col/decl 来自硬编码 `new_cols` 列表 |
| 47 | security/api_key_manager.py:414-420 | `{set_clause}`（UPDATE api_keys） | set_clause 的列名为代码内硬编码 dict key（源码已注释说明） |
| 48 | memory/knowledge_graph.py:277-280 | `{placeholders}`（relations IN） | 动态 `?` 占位符 ×2 + LIMIT `?`；表名另经 `_validate_table` 校验 |
| 49 | memory/knowledge_graph.py:480-490 | `{where}`（edges 查询） | 固定子句（`source = ?`/`target = ?`/`relation_type = ?`）+ params |
| 50 | memory/knowledge_extractor.py:261-276 | `{where}`（UPDATE facts access_count） | 固定条件模板 + params；空条件回退 `1=1` |
| 51 | memory/knowledge_extractor.py:273-281 | `{where}`（SELECT facts） | 同上 |
| 52 | memory/knowledge_extractor.py:311-315 | `{where}`（SELECT relations） | 同上 |
| 53 | cost_tracker.py:353-376 | `{where}`（SELECT cost_entries） | 固定条件模板（session_id/agent/model/created_at 全部 `=?`）+ params；LIMIT 走 `?` |
| 54 | backends/kv_store.py:224-226 | `{placeholders}`（IN 子句批量读） | 动态 `?` 占位符，keys 展开 params |
| 55 | backends/db_utils.py:100 | `PRAGMA busy_timeout={...}` | 值经 `_get_busy_timeout_ms()` int() 强转 + 异常兜底回退 10000（L37-54） |
| 56 | backends/db_utils.py:159 | 同上（连接池路径） | 同上 |
| 57 | backends/backends.py:124 | `PRAGMA busy_timeout={...}` | 同上（复用同一函数） |
| 58 | backends/data.py:365-372 | `{json_column}`、`{table}`（json_query） | 双双经 validate_identifier；json_path/value/limit 走 `?` |
| 59 | backends/data.py:374-378 | 同上（无 value 分支） | 同上 |
| 60 | backends/data.py:420-425 | `{table}`、`{json_column}`（json_each） | 同上 |
| 61 | backends/data.py:451-458 | `{fts_name}`、`{cols}`、`{table}`（fts_init DDL） | table 与每列均经 validate_identifier |
| 62 | backends/data.py:497-510 | `{fts_name}`、`{highlight_tag}`（fts_search highlight） | table 经校验；highlight_tag 本次已加白名单（见 3.2）；query/limit 走 `?` |
| 63 | backends/data.py:518-524 | `{fts_name}`（fts_search plain） | table 经校验 |
| 64 | backends/data.py:538-542 | `{fts_name}({fts_name})`（fts_rebuild） | table 经校验，fts_name 由其派生 |
| 65 | agent/tools/tool_audit.py:159-175 | `sql += " AND ..."`（query） | 追加的均为固定片段，值走 `?` params |
| 66 | agent/tools/tool_audit.py:205-206 | `{where}`（COUNT total） | where 为固定串 `"WHERE created_at >= ?"` 或空（L202） |
| 67 | agent/tools/tool_audit.py:209-211 | `{where}` + `{'AND'/'WHERE'}`（success COUNT） | 同上，连接词为三目常量 |
| 68 | agent/tools/tool_audit.py:214-215 | `{where}`（AVG duration） | 同上 |
| 69 | agent/tools/tool_audit.py:218-221 | `{where}`（GROUP BY tool_name） | 同上 |
| 70 | agent/tools/tool_audit.py:223-226 | `{where}`（GROUP BY agent） | 同上 |
| 71 | agent/memory_ctx/worktree.py:218-230 | `{', '.join(sets)}`（UPDATE worktree_nodes） | 固定 SET 模板（`result = ?`/`metadata = ?`/`updated_at = ?`） |
| 72 | agent/delegation/subagent_lifecycle.py:425-435 | `{', '.join(sets)}`（UPDATE subagents） | 固定 SET 模板 |
| 73 | agent/delegation/subagent_db.py:341-344 | `({col_list})`（重建表数据拷贝） | col_list 为新旧表 PRAGMA 反射列交集（sorted(common_cols)），非用户输入 |
| 74 | agent/delegation/subagent_db.py:399-406 | `{col} {col_def}`（ALTER TABLE ADD COLUMN） | REQUIRED_COLUMNS 硬编码 dict + `_validate_column_def` 白名单校验（P2 安全修复已存在，L405） |
| 75 | monitoring/timeseries.py:211-219 | `FROM {table}`、`{agg_col}` | table 为二选一硬编码（ts_5min/ts_1hour）；agg_col 经 `agg_map` dict 白名单映射，越界回退 `avg_value` |
| 76 | dashboard/routers/*（60 处命中） | 静态 SQL | 无任何拼接，不计入插值点 |
| 77 | channels/models.py 等 dashboard 数据面 | 静态 SQL | 同上 |

注：序号 76/77 为补充说明行，不占用插值点计数；实际豁免插值点为 78 处（80 − 2 修复项）。

### 4.3 代表性豁免理由归纳

- **固定子句模板 + 参数化值**（占比最高，约 40 处）：所有用户可控值（agent、status、timestamp、LIKE 关键词等）一律通过 `?` 占位符传入 params，SQL 文本中只出现代码内字面量片段。此类是标准"条件装配器"写法，不存在注入路径。
- **标识符白名单校验**（约 15 处）：`validate_identifier` 强制 `^[a-zA-Z_][a-zA-Z0-9_]*$`，表名/列名即使来自上层也无法携带引号、空格或注释符。
- **动态占位符 IN 子句**（6 处）：`",".join("?" * len(ids))` 是 IN 子句参数化的唯一正确形态，占位符本身不含任何输入内容。
- **白名单映射**（3 处）：排序列、聚合列、facet 字段均通过 dict/frozenset 映射，非法值回退默认而非拼入。
- **int() 强转**（6 处）：LIMIT/OFFSET/busy_timeout/索引参数等数值位全部整数化后才入 SQL。
- **运维迁移工具的反射交集**（8 处）：`sqlite_to_pg.py`、`memory_migration.py` 等的表名必须真实存在于源库 schema（反射交集）才会被拼入 SQL，且这些 CLI 不暴露给 Dashboard API 面。

## 5 验证结果

表：验证命令与结果对照表

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| Lint | `python -m ruff check py/maop/` | exit 0（0 error） |
| 类型检查 | `python -m mypy py/maop/ --ignore-missing-imports` | Success: no issues found in 356 source files |
| 定向回归 | `pytest test_sql_injection_hardening.py + test_tenant_isolation.py + test_enhancements.py` | 115 passed, 0 failed |
| 全量回归 | `python -m pytest py/tests/ -q --timeout=60 --no-cov` | 7921 passed, 58 skipped, 0 failed（4m11s） |

语义保持性确认：两处修复仅在"非法输入"分支抛出 `ValueError`，所有既有合法调用路径（含现有测试覆盖的 `scoped_select("acme", "items")`、`fts_search(..., highlight=True)` 默认标签）输出与修复前完全一致。

## 6 结论

1. 安全审核报告所指"约 82 处 f-string SQL 拼接"经逐一溯源核实，**无一属于高危**（不存在插值直接来自 API 请求参数且无验证的点）；Dashboard API 层 60 处 SQL 全部为静态字符串。
2. 项目整体防护基线良好：统一使用 `validate_identifier` 白名单、固定子句模板 + `?` 参数化、dict 白名单映射与 `int()` 强转，符合 SQLite 参数化最佳实践。
3. 本轮修复 2 处中危纵深缺口（`TenantRLS.scoped_select` 的 columns/order_by 校验、`MaopDatabase.fts_search` 的 highlight_tag 校验），并新增 26 个防御用例固化行为。
4. 其余 78 处均记录豁免理由（见第 4 章），后续如新增 SQL 构造点，建议沿用同一套模式并优先复用 `db_utils.validate_identifier`。