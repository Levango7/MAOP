# ADR-022: 多模态记忆的存储空间与检索架构（F2-02 落地选型）

- **状态**: Accepted（独立对抗性审核两轮通过：第一轮 APPROVE WITH MINOR ISSUES，8 项应修已全部修订并被复核确认）
- **日期**: 2026-10-12
- **关联**: ADR-016（双版架构）、PRD 4.3（F2-02）、`docs/spike-multimodal-memory.md`（2026-10-11 第一次 spike）、F1-02（pgvector）、F1-03（UnifiedMemoryProtocol）
- **前置实测**: [docs/spike-multimodal-memory.md](../spike-multimodal-memory.md) —— 本机（Windows 11 / Python 3.14 / CPU only / 无梯子）真跑数据，非推演

## 背景

PRD 4.3（F2-02）要求多模态记忆：图像/音频/视频嵌入（R01）、"pgvector 多向量列"存储（R02）、
跨模态融合检索（R03）、`MemoryFacade.store_multimodal()/retrieve_multimodal()`（R04）、
模态元数据（R05）、个人版 = 文本 + 图像（R06）。

第一次 spike（2026-10-11）实测得出三条与 PRD 原文有出入的硬结论：

1. **CLIP 可用但单张延迟贴着阈值**：`openai/clip-vit-base-patch32`（512 维，文本/图像共享空间）
   经 hf-mirror 可用；图像单张嵌入 spike 探针热机 median **811ms**（PRD 要求 <200ms），
   B1 落码后复测为 65-293ms 波动（见 D4）——**单张不可依赖**，
   batch 摊销后 42-53ms/图（达标）⇒ 架构取"异步入队 + 批处理"。
2. **"pgvector 多向量列"在现有架构下不可行**：`py/maop/core/vector/pg_backend.py` 是
   **单** `vector(N)` 列 + 初始化 ALTER 维度（`_ALTER_DIM_SQL`，pg_backend.py:108-111）的架构；
   表中已有 384 维行时执行 `USING embedding::vector(512)` 会直接 cast 报错（被 :252-253 的
   except 静默吞掉）⇒ CLIP(512) 与 MiniLM(384)/OpenAI(1536) **无法共列共存**。
3. **NDCG 验收需要标注数据集**：PRD 要求人工评估 NDCG ≥ 0.8——现无数据集，需先构造（B4）。

## 决策

### D1. 嵌入扩展接口：`EmbeddingProvider` 多态 + `[multimodal]` optional extra

- CLIP 嵌入器扩展既有 `EmbeddingProvider` ABC（`py/maop/core/memory/vector_embed.py:80-96`），
  不新造协议；子类经 `_dim` 声明 512（既有 `HashEmbedding(dim=128)` 同行惯例）。
  **文本与图像共用同一 CLIP 空间**（CLIP 设计如此）⇒ 跨模态直查不需要翻译层。
- 依赖（transformers + pillow + torch）放入新 optional extra `[multimodal]`（版本约束
  `transformers>=4.40,<6`，下界未实测故带上界防 API 漂移），默认安装零增重。
  缺失时注册确定性回退 provider（哈希，**非语义**，`is_semantic=False`，结果标记
  `degraded: true`），文档/日志明示其非语义性质，集成测试不对回退 provider 做语义断言。
- 环境适配：`HF_ENDPOINT=https://hf-mirror.com` 镜像支持写入文档与错误消息
  （本机实测 `huggingface.co` 000、`hf-mirror.com` 200）；模型缓存位置由 `HF_HOME`/
  `XDG_CACHE_HOME` 决定（本机实测落在 `F:\Agent\kilo\cache\huggingface\hub`，600MB+ 权重），
  离线/air-gap 预置方法与 `MAOP_DATA_DIR` 无关——写入部署文档。
- `transformers` 新版 `get_text_features()/get_image_features()` 返回
  `BaseModelOutputWithPooling`（5.14.1 实测，`pooler_output` 即投影后向量）⇒ 实现按
  `pooler_output` 双形态兼容。**注意**：ViT-B/32 文本侧 hidden 也是 512 维，维度校验抓不到
  "未投影"的静默语义错位 ⇒ 用金标向量夹具（真 CLIP 输出固化）做自检断言。

### D2. 存储：独立嵌入空间表 `multimodal_entries`（对 PRD R02 的偏差，带证据）

PRD R02 原文为 "pgvector 多向量列，每模态独立索引，共享元数据"。实测证明"多列"在
现有单列 + ALTER 架构下不可行（背景 2）。落地为**多空间（多表）**：

- 新表 `multimodal_entries(id, text, vector(512)/BLOB, metadata, created_at)`：
  - **模态落在 metadata JSON**（`metadata.modality`，值 `text`/`image`；B3 扩 `audio`/`video`）。
    SQLite 侧检索过滤在内存模态映射上做（个人版规模精确且快）；PG 侧 B2 用
    **`modality` 表达式 B-tree 索引**（`(metadata->>'modality')`）——"每模态独立索引"
    的意图在 PG 落地，SQLite 侧如实标注为"内存过滤"。
    > **偏差（2026-10-13 B2 PR-4 实测修正）**：原计划"每模态 partial **HNSW**"。
    > pgvector 0.8.7 上 partial HNSW 在空表/数行规模会把明显更优的向量排到更差的
    > 之后（实测查询 "a red square" 时 img-blue 一度压过 img-red），而 pgvector
    > 官方建议小数据集走精确扫描。故 B2 改为 modality 表达式 B-tree + **精确
    > 余弦排序**，ANN 索引留给后续按数据量评估的迁移。
  - `metadata` 统一 JSON（来源引用、时间戳、`model_id`、`degraded` 等）——满足"共享元数据"（R05）。
  - CLIP 文本/图像共享 512 维空间 ⇒ 表内**跨模态直查**；未来新模型（新维度）→
    **新表 + 新迁移**（演化路径写死在迁移里，可审计，见 D9）。
- **库文件隔离**：独立文件 `<data_dir>/multimodal.db`（理由：隔离与"删文件即回滚"；
  附带规避 `vec_vectors` 虚表潜在撞名——现状全仓无代码创建该表，mixin tier-1 在无该表的库上
  每查抛错一次后降级 numpy，如实记录）。
- **对既有代码的最小化触碰**：给 `VectorStore` 加三个**默认值即现状**的参数
  （`table_name="vector_entries"`、`create_sql=现有 DDL`、`expected_dim=None`）+ 存储向量
  解码容忍 JSON/float32-BLOB 双格式（`_decode_stored_vector`）+ `_load_cache` 的
  metadata 解析兜底（一条坏 metadata 不再中止整次加载、留下半满缓存）+ `expected_dim` 非空时
  异维坏行跳过+告警。`MultimodalStore` 以**子类**方式**继承**：
  `_load_cache`/`_get_entry_info`/`delete`/`clear`/`count`/`list_all` 与 mixin 检索链；
  **自建/覆盖**：`index`（覆盖为 BLOB 文本写入，防静默漂移回 JSON 记账）、`index_many`
  （按 modality 分组批嵌入，返回 id；`index_batch` 覆盖为 LSP 兼容 int 计数、仍走 BLOB）、`index_text`/`index_image`/`search`/`search_image`/`stats`
  （基类无 `stats`，自建：`count()` + 从 `_meta_cache` 派生 modality 计数——不另设模态字典，
  避免继承的 `delete`/`clear` 造成脏 id）。
  `expected_dim=512` 同时加固：一条异维坏行只被跳过+告警，不再让 `np.array` 抛 `ValueError`
  穿透、拖垮全库检索。
- 既有 `vector_entries` 及其数据**零改动**（满足 NFR"现有文本记忆 API 零变更"）。
- **后端选择现状如实记录**：`maop.core.vector` 工厂/`VectorBackend` ABC 在 B1 前**无任何生产
  调用方**（仅自身引用，`test_dead_wiring_inventory.py` 的 KNOWN_ORPHANS 曾据此登记）。
  B1 引入了**唯一一处生产导入**：`MultimodalStore` 的 PG 选择告警探针（只读
  `resolve_backend_name()` 做提示，不做存储）；故该模块已从孤儿清单移除。**存储语义上
  仍是"预留形态"**——PG 实际存储路径在 B2 落地，本 ADR 不宣称"经既有
  `MAOP_VECTOR_BACKEND` 打通"；B1 若检测到 `MAOP_VECTOR_BACKEND=pg` /
  `MAOP_DB_BACKEND=postgresql` 打 warning 说明当前走 SQLite。
- 偏差记账：本 ADR 即偏差记录；同步在 PRD 4.3 / ROADMAP v5.4.0 加一行脚注指向本 ADR。

### D3. 融合检索：RRF 起步（可升级重排）

- 纯函数 `reciprocal_rank_fusion(ranked_lists, *, k=60, weights=None, top=None)`：
  输入 `[(source, [(id, score), ...]), ...]`，只用 rank 不用 score；等权默认；同 id 跨列表
  合并计分；输出 `FusedHit(id, score, sources, payload)`。无状态、可单测。
- **两路检索腿定义（审核后精确化，防"含糊接链路"）**：
  - 多模态腿：查询文本 → CLIP 文本侧编码 → `multimodal_entries` 空间检索（天然跨图像+文本模态）；
    查询图像 → 图找图（modality='image' 过滤）。
  - 文本记忆腿：**显式注入适配器**（信号 `Callable[..., list[dict]]`）——
    注入方负责适配 keyword-only 签名，标准接法
    `lambda q, n: impl.long_term_search(q, top=n)`（两实现 `long_term_search` 均为 `*, top=` keyword-only，
    位置参数直传会 TypeError ⇒ 由 Facade 用 lambda 适配，**不**裸传方法引用）。
    两模式真实链路（已核源码）：
    - chat 模式：`MemoryManager.long_term_search`（manager_mixin.py:384）→ `semantic_search`
      （:204）→ legacy `VectorSearch.search`（默认 384 维 MiniLM；模型不可用时 384 维哈希回退，
      `is_semantic` 属性可判）。
    - agent 模式：`SemanticMixin.semantic_search` → `VectorStore`（**默认 HashEmbedding 128 维非语义**，
      除非配置了真实 provider）。⇒ ADR 不假设"文本空间是 MiniLM"，融合伙伴语义性由
      调用方配置决定，结果里带 `degraded` 标记。
- 默认等权；权重可配（为后续"加权融合/重排"留接口而不预先实现）。
- 顺带修复（B1 内，独立提交）：chat 模式 `MemoryManager.long_term_index` 调用
  **不存在的** `self.vector_search.index(...)`（真实类只有 `index_entry`/`index_all`，
  manager_mixin.py:378），异常被吞导致单条长期记忆写入静默失效——这是审核发现的既存缺陷，
  修复 + 补"接口面存在性"回归测试（防同类再发）。

### D4. 延迟架构：同步 API 先行，异步入队 + 批处理达标

- spike 探针实测单张热机 400-811ms（未达 PRD <200ms）；**B1 交付前对已落地代码路径
  复测**（同一台机、同一模型、`ClipEmbedding.embed_image` 走完整校验+前向）：
  冷启首调 206ms；热机单张 n=10 为 min 85 / median 119 / max 293ms，
  另一轮 n=3 为 65-75ms —— 单张延迟**波动大且贴着 200ms 阈值**（CPU 负载敏感），
  **batch 路径稳定**：经批处理路径（`index_many`）走单次前向 42ms/图（含 SQLite 写入）。
  ⇒ 架构结论不变（**异步入队 + 批处理**是达标路径），但"单张必然超阈值"的说法
  修正为"单张在阈值附近波动、不可依赖"；两轮实测数字都记录在案（spike 报告 + 本 ADR）。
- **批处理的实现承诺**：`ClipEmbedding.embed_image_batch` 必须用
  `processor(images=[...])` **单次前向**（不是逐张循环）；`store_batch/index_many` 按
  modality 分组调用图像/文本批方法。CI 无法计时 ⇒ 加**结构性断言**（单次前向调用），
  本机实测数字（覆盖 batch 路径）写进 PR 证据。
- 异步队列接线（与任务队列/worker 集成）放 B2，作为 NFR 达标闭环。
  本 ADR 记录该时序承诺：**同步 API 交付不宣称延迟达标**。
- **2026-10-14 闭环（M2.2 B2 收尾）**：接线落点是**聊天上传**
  （`chat_service.save_image → maybe_index_uploaded_image → store_async`）——
  这是运行系统里图片唯一真实的到达事件（task/worker 当前没有产出图片的生产
  路径，接了就是死码，故未接）。上传请求 <10ms 返回（结构断言：submit 前
  零次 embed），后台按批嵌入；停机 close+drain 覆盖 worker 手里的部分批。
  provider 由工厂解析（`resolve_multimodal_provider`），此前全仓无一处构造
  `ClipEmbedding` ⇒ 生产一直跑 hash 回退，本条一并修正。

### D5. 版本范围：个人版文本+图像先行，音频/视频为企业版

- R06 落地：B1-B2 = 文本 + 图像（CLIP）；音频（Whisper embedding）/视频（关键帧+CLIP）
  为 B3（企业版特性，遵循 ADR-016 双版边界）。**扩展点承诺**：模态是字符串注册值、
  新空间 = 新表 + 新迁移；企业版新增 provider/模态不改本协议（B3 时细化）。

### D6. 图像输入安全与原始引用（审核新增）

- 图像输入（path/bytes）统一校验：扩展名白名单与 20MB 上限对齐既有
  `ImageStore` 常量（`image_store.py:36-37`：`ALLOWED_EXTENSIONS`、`MAX_IMAGE_SIZE`）、
  Pillow `verify()` 解码校验（防解码炸弹）、路径穿越校验（对齐 image_store.py:91-103）。
- 原始文件持久化**不在 B1**：path 输入记 `source_ref`，bytes 输入记
  `sha256 + size + mime`，原始落盘委托既有 `ImageStore`/上传层（B2 接线）；
  ADR 明示该边界，避免"第二套上传面"。

### D7. 存储编码与 NFR 记账（审核新增）

- SQLite 侧向量用 **float32 BLOB**（2048B/条；先例 `memory/vector_search.py:143-145`），
  不用 JSON（10563B/条，5.2x 浪费——审核实测）。解码容忍双格式（见 D2）。
- NFR"向量存储增量 < 原始文件大小 1%"**数学记账**：2048B ÷ 1% = 205KB ⇒
  **小于 ~205KB 的图片该指标不可达**（向量定长、与文件大小无关）。对策（B2 评估）：
  fp16/int8 量化（1024B/512B）或按文件阈值选择性入向量库；本 ADR 记录该推导，
  防止验收时才发现指标先天不可达。
- **2026-10-14 PG 侧实测**（`docs/audits/m22-b2-storage-quantization.md`，
  脚本 `scripts/measure_multimodal_storage.py`）：万条规模 PG ~2.7KB/条（TOAST
  压缩后的堆均值）、SQLite ~4.2KB/条（4KB 页装不下 2048B 向量+元数据，页内
  碎片推高）。⇒ 数学记账不变（元数据开销比净载假设还小）；换 PG 的理由是
  并发/运维而非容量；ANN 索引仍未纳入审计（支撑 D2 的"小表走精确扫描"）。

### D8. 许可与使用范围（审核新增）

- 上游 OpenAI CLIP 仓库为 MIT；但 `openai/clip-vit-base-patch32` 模型卡**无显式 license 字段**，
  且声明 "not developed for general model deployment"、部署用途 "out of scope"，用例限定英文。
- 本项目为中文优先的个人版 ⇒ 记录为**已知约束**：① 权重与代码分开发布（extra 只拉代码依赖，
  权重由用户环境下载，不在本项目再分发）；② per-entry 记 `model_id` 支持换模型；
  ③ 备选可切换权重（Chinese-CLIP / AltCLIP / open_clip 社区复现）列入 B4 之前的评估项，
  与 PRD 风险缓解措施"可切换嵌入模型"一致。

### D10. 检索质量可度量（B4，2026-10-14）

- 度量入口 **`maop eval multimodal`**（`core/memory/retrieval_eval.py` +
  `cli_eval.py`）：对**当前进程解析出的 provider** 跑 NDCG@k，回答"我这个部署
  的多模态记忆是真的在语义检索吗"。这也是该模块的生产引用方（否则它是
  dead-wiring 门禁下的孤儿——类级/工具级死代码正是本 ADR 系列反复踩的坑）。
- 指标 = **NDCG@5**（gain/log2(i+1)、IDCG 同 k 截断、全零相关 ⇒ 0.0），
  分级相关性（2=同色同形 / 1=同色异形 / 0）。只走 `MultimodalStore.search`
  的余弦序——**不经 `MultimodalMemory.retrieve`**（那是 RRF rank 分，会压缩
  待测信号）。
- 数据集 = 12 张确定性合成图 + 8 条查询，向量用真 CLIP 冻结
  （`scripts/build_clip_eval_fixture.py` 复算，revision 钉子门禁防腐化）。
  查询文本不入被检语料（防 hash self-hit 白拿 #1）。
- 实测（`docs/audits/m22-b4-retrieval-quality.md`）：CLIP **0.8661** vs hash
  回退 **0.1005**（边际 +0.766）。测试钉 CLIP ≥ 0.75 / hash ≤ 0.25 / 边际 ≥ 0.5，
  并按实测先跑 12×12 sanity 矩阵再定 grade（发现 CLIP 颜色强、形状弱 ⇒
  "同色异形"记 1 分，与模型能力对齐）。
- **本 harness 不承担 PRD 的"人工评估 NDCG≥0.8"验收**；语料 12+12 而非原计划
  50-100 条（规模缩减按规则 #3 记账在 ROADMAP），多模型横评的触发条件 =
  出现第二候选模型或中文查询低于可接受水位（中文盲区见 D8）。

### D9. 可切换嵌入模型（HLD 期望对齐）

- HLD 期望 ADR 覆盖"多模态嵌入模型选型与可切换"（hld-three-phase-roadmap.md:1709）。
  设计：`MultimodalEmbeddingProvider.model_id` 暴露空间标识，写入每条的 metadata；
  空间（表）与模型绑定，换模型 = 新空间 + 迁移/回填（B2+）。
- 命名对齐：HLD 示例 `retrieve_multimodal(query, top_k=5, ...)` vs 本实现用 `top`（与仓内
  `retrieve(layer, query, top=10)` 一致）——记偏差，PRD R04 未定参数名。

## 备选方案对比（审核后补全）

| 方案 | 结论 | 理由 |
|---|---|---|
| A. 单表多 `vector(N)` 列（PRD 原文字面） | **否决** | 需废弃 pg_backend 的单列+ALTER 逻辑并重写既有 384/1536 数据迁移；每列 ALTER 影响全表；背离 NFR"零变更" |
| B. 独立空间表（**采纳**） | 采纳 | 既有路径零改动；空间独立维度/索引；`删文件即回滚`；演化路径显式；代价（两套存储实例、dashboard 既有列出/统计看不到新表、备份双轨）已在"影响"诚实列出 |
| C. 单表 + 无维度约束 `vector` 列 + `dim` 判别列 + 每维 partial index | 部分可取，**B1 不选** | 静态 ANN 索引在 PG 可行（非稻草人）；但混合维度过滤+索引膨胀使查询路径复杂化、SQLite 侧无法等价；PG 侧若 B2 实测证明单表多空间更优可再评估（本 ADR 记录该选项） |
| D. 给 `VectorStore` 加 `table_name` 参数，不建子类/空间概念 | **部分采纳**（参数要加） | 参数化避免复制 `_load_cache/index/delete/stats`（采纳为基座）；但"空间"语义（独立库文件、独立编码、模态映射、expected_dim）仍需子类承载 |
| E. 全量 padding 到 1536 单空间 | 否决 | padding 破坏余弦相似度语义；存储浪费 ~3x |

## 影响

- 新增（最终路径，与 Batch-1 方案一致）：
  - `py/maop/core/memory/multimodal_embed.py`（provider 抽象 + CLIP + 回退 + 预计算夹具 provider）
  - `py/maop/core/memory/multimodal_store.py`（`MultimodalStore(VectorStore)`）
  - `py/maop/core/memory/fusion.py`（RRF 纯函数）
  - `py/maop/memory/multimodal.py`（服务层 `MultimodalMemory` + 模块级单例工厂）
  - `py/maop/migrations/pg/versions/003_multimodal_entries.py`（**B2**）
  - `py/pyproject.toml` `[multimodal]` extra
- 修改（全部为默认值即现状的加法）：
  - `VectorStore`：`table_name` / `create_sql` / `expected_dim` 参数 + BLOB/JSON 双格式解码
    + `_load_cache` metadata 解析兜底（坏 metadata 不再中止整次加载留下半满缓存）
  - `db_utils.py`：`_resolve_data_dir` 提为公开 `resolve_data_dir()`（更新 `_SYMBOL_TO_MODULE` 映射）
  - `MemoryFacade`：`store_multimodal` / `retrieve_multimodal`（纯新增）
  - `MemoryManager.long_term_index`：修 `vector_search.index` 不存在缺陷（D3 顺带修复）
  - `migrations/sqlite_to_pg.py`：`_COPY_ORDER`（:65 起）登记 `multimodal_entries`（**B2**）
- 不变量：既有全部测试零改动通过（回归门禁）。

## 风险与限制（如实记录）

| 风险/限制 | 记录 |
|---|---|
| 多进程可见性 | 与既有 VectorStore 同源：缓存仅在空时加载，**跨进程新写入对已初始化实例不可见**；同进程写入即时可见。文档明示 |
| dashboard 覆盖 | 既有 `/api/vector/list` 等看不到 `multimodal_entries`（B2 决定是否暴露） |
| HF 缓存治理 | 600MB+ 权重落 `HF_HOME`/`XDG_CACHE_HOME`（与 `MAOP_DATA_DIR` 无关）；air-gap 预置方法进部署文档 |
| 回滚口径 | B1 无迁移：删 `<data_dir>/multimodal.db` + 回退代码即完全回滚；B2 alembic 003 需写 downgrade |
| 双版边界 | 音视频/新模态为企业版扩展点；`[multimodal]` extra 与个人版代码同仓发布不构成企业版能力 |
| 用例范围 | CLIP 模型卡限定英文用例，产品中文优先 ⇒ D8 的已知约束 |

## 落地批次（v5.4.0 窗口内）

| 批次 | 内容 | 验收 |
|---|---|---|
| B1 | 本 ADR + VectorStore 参数化/加固 + 空间存储（SQLite）+ provider（CLIP/回退/预计算）+ RRF + Facade 方法 + 非 mock 集成用例 + `long_term_index` 修复 | ①集成用例（真实 SQLite+真实入库检索融合）绿；②预计算真 CLIP 向量的跨模态排序断言绿（非 mock 语义）；③文本用例零改动 |
| B2 | PG 实现 + alembic 003 + 真 PG 证据 + sqlite_to_pg 登记 + 异步队列/批处理接线 + 存储量化评估 | NFR 延迟达标（批处理实测）；检索 P95 测量；NFR 存储记账复核 |
| B3 | 音频（Whisper）/视频（关键帧）——企业版 | PRD R01 其余模态 |
| B4 | 标注数据集（50-100 条）+ NDCG harness + 可切换模型评估（Chinese-CLIP 等） | PRD AC NDCG ≥ 0.8（人工评估） |