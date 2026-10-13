# M2.2 B2 存储量化评估：PG vs SQLite（实测）

> 2026-10-14。ADR-022 D7 此前只对 SQLite 路径做过预算说明，PG 侧（v5.4.x 的
> B2 交付物）一直没有实测数字。本条用**真 pgvector 库 + 真代码路径**补齐，
> 脚本：`scripts/measure_multimodal_storage.py`（合成条目写入自有
> `quant_<pid>_` 前缀并删除，不碰真数据）。

## 口径

- 条目 = 1 条文本 + 1 张图为一对；`--rows N` ⇒ 共 2N 条，每条都带 512 维
  float32 向量（CLIP 空间，2048 B 净载）。
- 图载荷用仓库夹具 PNG（`py/tests/fixtures/multimodal/red_square.png`）——
  嵌入器会校验图片字节，合成字节会被跳过，那样向量列就空了，量的不是目标。
- PG = `pgvector/pgvector:pg16` 容器，`pg_total_relation_size`（堆+索引+TOAST）；
  SQLite = `MultimodalStore` 的 `multimodal.db` 文件大小（含其内部索引）。
- 本机（无 GPU）实测，数字随机器浮动，**取决于字符的是量级与比例**。

## 结果

| 后端 | 条目数 | 总量 | 堆 | 索引 | 平均每条约当 | 写入耗时 |
|---|---|---|---|---|---|---|
| PG (pgvector) | 2,000 | 12.5 MB | 1.4 MB | 480 KB | ~3.2 KB/条（含 TOAST） | 2.96 s |
| SQLite | 2,000 | 8.0 MB | 8.0 MB | （含在文件内） | 4.18 KB/条 | 24.5 s |
| PG (pgvector) | 10,000 | 32.0 MB | 3.8 MB | 1.5 MB | ~2.7 KB/条（含 TOAST） | 16.4 s |
| SQLite | 10,000 | 39.8 MB | 39.8 MB | （含在文件内） | 4.17 KB/条 | 129.7 s |

10k 行时 PG 索引明细：`multimodal_entries_pkey` 1.1 MB、
`idx_mme_modality` 104 KB、`idx_mme_created` 312 KB（modality 表达式索引
如预期地小——它是 B-tree，不是 ANN；ANN 索引按 ADR-022 D2 留到按数据量评估）。

## 结论

1. **PG 每条更小（~2.7 KB vs ~4.2 KB，约 −35%）**：SQLite 每 4 KB 页基本
   容不下"2048 B 向量 + 元数据"的一条整行，页内碎片把均值推到 4 KB 以上；
   PG 的 vector(512) 超过 2 KB TOAST 阈值被外置压缩，堆里只剩行头。
   ⇒"换 PG 会更省空间"这个假设**在个人版当前画像（文本+图像、无 ANN 索引）下不成立
   也不反面**——PG 略优，但量级相同，选择 PG 的理由是并发/运维而非容量。
2. **写入吞吐差 8 倍（16 s vs 130 s / 万条）**：PG 是`_upsert_rows` 单事务批写；
   SQLite 走逐行 `INSERT OR REPLACE`。这不是缺陷，但说明"SQLite 上跑大规模
   回填"会很慢——`sqlite_to_pg` 迁移大批量数据时应预期到这个量级。
3. **ADR-022 D7 的"<205KB 图片 1% 不可达"数学记账不受影响**：该结论只依赖
   float32 净载（2048 B/条）与条目数，本实测的数字（PG 2.7 KB/条）只**加强**
   它——元数据开销比净载假设估的还小。
4. **ANN 索引仍未纳入审计**：ADR-022 D2 已决策小表走精确扫描，本评估同样
   支持——在万条以下，ANN 索引的写入与存储成本换不回检索收益；到需要时按
   D2 的迁移路径补，并重跑本脚本对比。

**边界**：本机 CPU+HDD，未测并发写入、vacuum 前后、TOAST 压缩策略差异
（同文本重复度高时 PG 压缩率会更好，本合成数据刻意低重复以贴近真实）。
