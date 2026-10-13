# M2.2 Batch-2 实施方案（v2，按独立审核修订）

> 独立对抗性审核第一版判定 **REJECT**（4 阻断 + 8 应修），本版逐条修订。
> 审核中已被本机 pgvector 实测印证的结论已标注「审核实测」。
> 配套：`docs/adr/022-multimodal-memory-store.md`（需同步偏差记账，见 §6）。

## 修订要点（对第一版的修正）

| # | 第一版 | 修订（v2） | 依据 |
|---|---|---|---|
| B1 | 迁移挂**根树** `migrations/alembic/versions/004_*` | 迁移挂 **pg 树** `migrations/pg/versions/003_multimodal_entries.py`，`down_revision="002_users_tenant_id"`；pg 扩展由 `pg/env.py:_enable_extensions` 统一建 | 根树 `001_init.py` 跑 `data/migrations/001_init.sql`（AUTOINCREMENT/INSERT OR IGNORE 等 SQLite-only）→ 整条根链**永远不可能在 PG 上 upgrade head**；PG schema 真源是 pg 树（`maop migrate pg-init`，cli.py:442-448）。**ADR-022:183 原本就写的是 pg 树 003**，第一版擅自改弦易帜未与 ADR 对账 |
| B2 | 004 在非 PG 方言建「SQLite 等价表」 | **根树不加迁移**。SQLite 侧表由 `MultimodalStore._MULTIMODAL_DDL` 运行时自建（库是独立文件 `multimodal.db`，本就不属 alembic 管辖）；在 maop.db 建幻影表会让 `sqlite_to_pg`「看见表、复制 0 行、报告成功」而真实数据在另一个文件 | 同上 + 双源 DDL 必漂移 |
| B3 | 只往 `_COPY_ORDER` 登记表名 | `migrate()` 增加**多模态源引擎**（自动发现 `<data_dir>/multimodal.db`）；`_JSON_COLUMNS` 注册 `multimodal_entries: {"metadata"}`；BLOB→`'[..]'` 转换走**按 (table, column) 的转换器注册表**（不进共享 `_transform_row` 的 if 分支） | `migrate()` 只用一个 sqlite 引擎自省 maop.db（sqlite_to_pg.py:518），不发现 multimodal.db 则登记=空操作 |
| B4 | 协议列 9 方法、声明 SQLite 已满足 | 协议补 `index`、`index_batch(entries)->int`（LSP 兼容）、`list_all(*,limit,offset)`（**标注为可选面**，isinstance 测试只钉核心面）；`PgMultimodalBackend.__init__` 注入同一 `MultimodalEmbeddingProvider`；`get_multimodal_memory()` 增加后端分支，**PG 实例键改为 `(backend, dsn)` 组合**（ADR-019 纪律，避免与 SQLite data_dir 实例互撞）；`space` 属性与 `_store` 类型放宽为协议 | 三方法在 SQLite 版均存在；服务层 `degraded` 读 `stats()["degraded"]`，两端 stats 键集必须一致 |

**应修项吸收**：M1 新 PG 腿必须挂进 CI（否则 G4 恒假绿）且**用预计算真 CLIP 夹具**（migrations job 不装
torch/Pillow）；M4 PG 腿补 `downgrade base → upgrade head`；M5 stats 键集两端一致（PG 出 `backend:"pg"`）+
同分 tie 顺序不参与参数化断言；M6 回写 ADR-022 偏差（物理列→表达式索引）+ `docs/database-schema.md` 补
PG 形态；M7 不拆新 job；M8 新文件 <800 行、转换器独立模块、禁静默 except。

## 1. 范围（v2）

- **G1 PG 后端**：`PgMultimodalBackend`（新 `py/maop/core/memory/multimodal_pg.py`），表
  `multimodal_entries(id TEXT PK, text TEXT, vector vector(512), metadata JSONB, created_at)`——
  **列名就叫 `vector`**（审核实测：`vector vector(512)` 合法，与 pgvector 类型同名无冲突 → 零列映射）。
  每模态独立索引：`CREATE INDEX ... USING hnsw (vector vector_cosine_ops) WHERE (metadata->>'modality') = 'image'`
  （审核实测：表达式 partial HNSW 合法，需 pgvector ≥0.5.0 → 镜像 0.8.7，注释固定版本要求）。
  > 偏差记账：ADR-022:53-55 原写「物理列 + partial index」→ 改为**表达式索引**（保持与 SQLite 侧
  > 同一份写入形状、杜绝双源不一致）——必须回写 ADR-022。
- **G2 命名空间单一真源**：新 `core/memory/multimodal_schema.py` —— 一份列规格（名字/可空/语义/
  modality 落点），渲染出 SQLite DDL（`_MULTIMODAL_DDL` 改为从规格生成）与 PG 迁移 DDL；
  `tests/test_multimodal_schema_parity.py` 断言两端列名/顺序/约束一致（防未来单边加列静默漂移）。
- **G3 迁移登记**：pg 树 `003_multimodal_entries.py`（`down_revision="002_users_tenant_id"`，
  `_require_destructive_ack` 守卫的 downgrade）+ `sqlite_to_pg` 多源引擎与转换器注册表。
- **G4 异步批处理**：`MultimodalIngestQueue`（进程内线程 + 有界 `queue.Queue`，close 降级同步+warning；
  PG 侧 batch 走**单事务**；`drain()/close()` 显式 API）。**不复用** MessageQueue（SQLite 持久队列对
  2s 批处理窗口是过度设计且引入磁盘故障面）与 PreemptableWorkerPool（asyncio 与同步 Facade 打架）。
- **G5 真 PG 证据**：migrations job 的 service 换 `pgvector/pgvector:pg16`（审核实测 env 语义与官方一致），
  新增并**挂进 CI** 的 `tests/test_multimodal_pg_integration.py`（预计算夹具路径 + PG alembic
  upgrade/downgrade），保留 `test_backends_pg_integration.py` 原腿（连通冒烟）。
- **不做**：改 `PgVectorBackend`；B3/B4；跨进程持久队列；dashboard 展示。

## 2. 实施拆分（4 个 PR，降低回归面）

**PR-1**：G2 命名空间 + G1 后端 + 协议 + 服务层/工厂重接 + 退休
`_warn_if_non_sqlite_backend_selected`（改写文案或删除，含回归测试）+ 单测（全程 mock engine，无真 PG）。

**PR-2**：G3 迁移 + `sqlite_to_pg` 多源引擎 + BLOB→vector 转换器（独立模块）+ parity/downgrade 测试。

**PR-3**：G4 异步队列 + 测试（含「2 文本+2 图 → `index_many` 指数 2 次」的正确计数断言 + 关闭降级）。

**PR-4**：G5 CI 腿 + 换镜像 + 真 PG 端到端证据（本机 docker + CI）。

## 3. 验收（v2）

| 项 | 判据 |
|---|---|
| PG 表 | 真 PG 上 `\d multimodal_entries` 有 `vector(512)` 列 + 每模态 partial HNSW 索引；pgvector 版本进 PR 证据 |
| 迁移链 | pg 树 `upgrade head`（003 建表）→ `downgrade base`（守卫路径）→ `upgrade head` **进 CI**；CI 无 PG 时相关腿 skipif |
| 迁移工具 | 真跑 `sqlite_to_pg`：从 `multimodal.db` 复制 1 text + 1 image 行 → PG 侧可检索、modality 正确；BLOB→vector 精度有单测（含「直插 BLOB 会 DatatypeMismatch」的失败模式钉住） |
| 两端同构 | `isinstance(store, MultimodalBackend)` 两端过 + 参数化集成用例跑同一断言集（**绕开 SQLite 跨进程不可见的语义差**、不用 tie 顺序） |
| 统计一致 | `stats()` 键集两端一致（`backend` 区分）；`degraded`/`modalities` 语义相同 |
| NFR 延迟闭环 | submit 即时返回（<10ms）；批处理摊销与 B1 实测同量级（真数字进 PR）；批量按模态分组计数正确 |
| 现有 API 零变更 | 全量测试零改动通过；`store()` 仍同步 |
| ADR-022 偏差 | 表达式索引替代物理列、迁移挂 pg 树，两处回写 ADR-022 与 `docs/database-schema.md` |

## 4. 风险（v2 增补）

| 风险 | 对策 |
|---|---|
| pgvector 版本 <0.5.0 无 partial HNSW | 迁移/代码注释固定 ≥0.5.0；CI 镜像 `pg16`（ships 0.8.7，审核实测） |
| PG 腿未挂 CI = 假绿 | CI job 显式调用新测试文件 + alembic pg 树步骤（M1） |
| dirty-data：maop.db 幻影表（若旧代码建过） | 迁移不自建；`sqlite_to_pg` 只认 multimodal.db 源 |
| 多进程写 PG 半批事务 | `index_many` PG 侧单事务（对齐 pg_backend insert_batch 模式） |
| 两端可见性语义不对称 | 参数化断言显式绕开（文档写明） |
| `_warn_if_non_sqlite_backend_selected` 退休后误报/漏报 | 退休或改写 + 回归测试钉住三种 env 组合 |
| `sqlite_to_pg.py` 逼近 800 行门禁 | 转换器独立模块（M8） |

## 5. 审核要点（第二轮请重点）

1. v2 的三处阻断修法（pg 树 003 / 不建幻影表 / 多源引擎）是否真的消除了假绿链？
2. 命名空间单一真源（列规格模块 + parity 测试）是否足够防漂移，还是过度设计？
3. 协议「核心面 vs 可选面」的划分是否合理（isinstance 只钉核心）？
4. PR 拆分粒度（4 个 PR）是否合理，有无该合并/再拆的？
5. 异步队列的最小形态（进程内线程+有界队列）在关停、测试隔离、asyncio 事件 loop 共存上还有没有坑？