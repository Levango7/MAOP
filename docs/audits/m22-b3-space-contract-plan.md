# M2.2 B3：多模态空间扩展契约（设计 v1，待独立审核）

> 2026-10-14。范围 = ROADMAP 最后一项 B3（音频 Whisper / 视频关键帧）。
> **边界前提（ADR-016/022-D5）**：音频/视频是**企业版特性**，实现（provider/
> 模型/依赖）属于私有仓 MAOS，不在 MIT 的 MAOP 里。因此 B3 在本仓的交付不是
> "把 Whisper 接进来"，而是 **ADR-022 D5 承诺的扩展点变成真的**：
> "模态是字符串注册值、新空间 = 新表 + 新迁移；企业版新增 provider/模态不改
> 本协议"。

## 1. 现状（代码实证，判定"承诺是否已经成立"）

| 承诺 | 现状 | 证据 |
|------|------|------|
| 新空间 = 新表 | **半真**：`VectorStore` 有 `table_name`/`create_sql`/`expected_dim` 参数（B1 加的），但 `MultimodalStore.__init__` 把它们**硬编码**成 `multimodal_entries` + 512 维 | `multimodal_store.py:131-133` |
| 模态是字符串注册值 | **假**：`MODALITY_TEXT`/`MODALITY_IMAGE` 是模块常量；`index_image` 走**图像校验**（字节/扩展名/解码），音频字节会被 `validate_image_input` 拒 | `multimodal_store.py:54-55,188-196` |
| 企业版新增 provider 不改协议 | **半真**：provider 可注入（B2 工厂+注入 seam），但**读取侧**（`stats()["space"]="multimodal-512"`、`search(modality=...)` 只认内置两态）写死了单一空间 | `multimodal_store.py:489-492,434` |
| 新迁移 | **假**：`py/maop/migrations/pg/versions/003_multimodal_entries.py` 是手写单文件，没有"按空间声明生成迁移"的机制 | `migrations/pg/versions/003_*` |

结论：**B1/B2 把"一个空间"做对了，但"第二个空间"至今插不进来**——企业侧要加
音频空间，必须改 core 的表名/维度/模态校验，即**违反 D5 的隔离承诺**。这就是
B3 在本仓的真实缺口。

## 2. 设计：空间注册表 + 通用写入路径

### 2.1 `MultimodalSpaceSpec`（声明式空间）
`core/memory/multimodal_spaces.py`（新模块）：
```python
@dataclass(frozen=True)
class MultimodalSpaceSpec:
    name: str                 # "clip-512"（默认）/ "audio-512"（企业侧注册）
    table_name: str           # "multimodal_entries" / "audio_entries"
    dimension: int            # 512 / 512 / …
    modalities: tuple[str, ...]   # ("text","image") / ("audio",)
    backend: str = "sqlite"   # "sqlite" | "pg"（跟随全局后端时为 auto）
    description: str = ""
```
- `register_multimodal_space(spec)`：进程内注册（重复注册同名 ⇒ 后者覆盖+
  warning，便于测试与企业侧热替换）；`get_multimodal_space(name)`；
  `list_multimodal_spaces()`；`reset_multimodal_spaces()`（测试用，恢复内置）。
- **内置空间在 import 时自注册**（`DEFAULT_SPACE = "clip-512"`），现有用户
  行为零变化（表名/维度/模态与今天逐字节一致）。

### 2.2 `open_multimodal_space(name, *, data_dir=None, dsn=None, embedding=None)`
- 按 spec 选后端：pg ⇒ `PgMultimodalBackend`（**DDL 按 spec.dimension 渲染**
  `vector(N)`，今天硬编码 512）；sqlite ⇒ `MultimodalStore`（把 spec 的
  `table_name`/`expected_dim` 传进 `VectorStore` 参数——B1 就已支持，只是没人用）。
- **PG DDL 参数化**：`multimodal_schema.pg_ddl(dim=spec.dimension)`，
  今天 `vector(512)` 是字面量；音频空间（如 CLAP 512/768）无需改 core。

### 2.3 通用写入路径（不假设模态语义）
新 `index_entry(modality, entry_id, *, text="", payload=None, vector=None, metadata=None, ext=None)`：
- `vector` **必填或可由注入 provider 产出**；当调用方自带向量（企业侧
  CLAP/Whisper-embedding 的场景）时**完全不碰图像校验**。
- 沿用 `index_many` 已有的"item 带 vector 则跳过 embedding"语义，把这条路径
  提升为一等方法，并让 `index_many` 认 `modality` 字段路由（音频 item 不再
  被猜成 image）。
- `search(query, modality=...)` / `stats()` 改为**按 spec.modalities 校验**
  （非法模态报错列出该空间支持的模态），`stats()["space"]` 用 spec.name。

### 2.4 服务层与单例
`get_multimodal_memory()` 的 key 从 `sqlite:<dir>` 扩为 `sqlite:<dir>:<space>`；
`get_multimodal_memory(space="clip-512")` 默认值不变 ⇒ 现有调用零变化，
音频空间与图像空间可在同一进程共存（ADR-019 单例纪律保持）。

## 3. 明确不做（防范围膨胀/越版）

- **不实现 Whisper/CLAP/ffmpeg**：企业侧模型与依赖归 MAOS（ADR-016）。
  本仓只提供机制 + 契约文档（§4）。
- **不做音频/视频的 pg 迁移文件**：迁移按空间声明由企业侧仓生成；本仓把
  DDL 渲染参数化（§2.2）并给出生成指引。
- **不做跨空间融合检索**（audio×image 同查）：RRF 融合留在单空间内，
  跨空间检索是独立设计（B3+）。
- **不改默认空间的任何行为**：表名/维度/模态/stats 输出对默认空间逐字节一致
  （用"默认空间 stats 形状"测试钉住）。

## 4. 契约文档（给 MAOS 的实现指引）

ADR-022 增补 **D11 扩展契约**：企业侧加一个模态需要且仅需要——
1. 在私有仓 `register_multimodal_space(MultimodalSpaceSpec(name="audio-512", table_name="audio_entries", dimension=…, modalities=("audio",)))`；
2. 用自己的 provider 产出向量，`index_entry("audio", id, vector=…)`；
3. PG 侧用 `multimodal_schema.pg_ddl(dim=…)` 生成迁移（附指引）；
4. 版本文档兼容口径（MAOS ≥ 某版才认该表）。

## 5. 测试

1. **注册表**：重复注册/覆盖/列举/重置；未知空间名报错并列出可用空间。
2. **默认空间零变化**：`stats()` 形状与表名与今天一致（回归钉子）。
3. **第二空间真跑**：注册 `audio-512`（dim 8 的假向量，避免依赖模型）⇒
   SQLite 与 PG 两条路径都能建表、写、按 modality 检索，**且不碰
   `multimodal_entries`**（跨空间隔离断言）。
4. **通用写入**：自带 vector 的 item 不触发图像校验（音频字节直接过）；
   modality 非法时报错列出该空间支持的模态。
5. **PG DDL 参数化**：`pg_ddl(dim=8)` 渲染 `vector(8)`；默认仍 `vector(512)`。
6. **单例 key**：两个空间在同进程各持一个实例、互不串数据。

## 6. 记账

CHANGELOG + ROADMAP（B3 的本仓部分勾掉，企业侧实现标注在 MAOS 待办）+ ADR-022 D11。


---

## v2 修订（独立对抗性审核回执，2026-10-14）

审核结论 **REJECT（12 必修）**。逐条处置：

| # | 必修项 | 处置 |
|---|--------|------|
| M1 | 只参数化 dim 不参数化 table ⇒ 新空间表从未被创建、写入全被 warning 吞掉 | `sqlite_ddl(table)`/`pg_ddl(table, dim)` 双参数 + 索引名按表名派生；`MultimodalStore` 按 spec 取 table/create_sql |
| M2 | PG `_UPSERT_SQL`/`_SEARCH_SQL`/modality_of/delete/clear/count/stats 全 Literal 表名 | 改实例级模板 + 标识符白名单校验（bandit B608 口径保留） |
| M3 | 旧迁移与新空间迁移的共存没答案 | `render_pg_migration(spec, revision, down_revision)` + 测试断言复现 003；索引名含表名 |
| M4 | `_write` 维度校验硬编码常量 | 改 `self._expected_dim`；provider 维度不符在构造期处理 |
| M5 | `get_multimodal_memory_if_exists()` 任意取一个 ⇒ 停机丢第二空间队列 | 改为返回**全部**实例，`drain_multimodal_ingest` 遍历关闭 |
| M6 | 默认空间取名 `clip-512` 与 `stats()["space"]` 现值冲突 | 默认名改 `multimodal-512`（与事实一致），并加"默认 spec == 旧常量"等价性测试 |
| M7 | 注册表跨测试泄漏 + "覆盖+warning"与单例并存是坑 | conftest teardown 加 `reset_multimodal_spaces()`；同名不同定义**报错**而非覆盖 |
| M8 | 默认路径不走注册表 ⇒ 注册表零生产调用方（B2 同型裸奔） | `_build_store` 收殓为"经 `get_multimodal_space` 取 spec 再构库" |
| M9 | `index_entry` 与 `VectorSearch.index_entry` 同名签名不同 | 改名 `index_modality`；加入 `MultimodalBackend` Protocol；`payload` 语义取消，改 `source_*` 引用字段 |
| M10 | "item 带 vector 就跳过校验"不实：图像 item 仍过校验、modality 被覆写 | `index_many` 按显式 modality 路由；`index_modality` 不覆写 caller modality |
| M11 | 服务层只认 image/text ⇒ 企业侧绕开协议 | `store/store_batch` 透传显式 modality（走通用路径），读取侧同样按 spec 校验 |
| M12 | 多进程注册时机没钉死 | ADR-022 D11 写明：MAOS 包 import 侧车注册，未注册即用报错并列可用空间 |

建议项采纳：DDL 参数化的门禁预检（规避 config-drift/silent-exception/file-size）、
`list_multimodal_spaces()` 作为企业侧能力探测、spec 带 `model_id`、PG stub 引擎断言、
report 文档化、harness 用 tmp_path、fail-loud、`evaluate_space` 签名对齐多 provider。

"明确不做"清单的异议处置：持久化队列同意不做；pg 迁移**改为交付渲染函数**；
跨空间融合保留 B3+ 但在 ADR 明记现状约束；"不改默认空间行为"升级为可执行等价性测试。
