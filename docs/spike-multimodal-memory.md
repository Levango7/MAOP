# T3.2 Spike 报告：多模态记忆嵌入与检索（2026-10-11）

> 范围：ROADMAP v5.4.0 前置 spike（2 周时间盒）——嵌入扩展接口、pgvector 多向量列方案、
  融合检索（RRF）PoC、KGE 选型。**本文只记录实测与结论，不改生产代码。**
> 本机环境：Windows 11 + Python 3.14.3（CPU only）。**无梯子**——`huggingface.co` 实测
> `000`（不可达），`hf-mirror.com` 可达（本文全部模型经它下载）。

## 1. 结论摘要（TL;DR）

| 问题 | 实测结论 |
|---|---|
| CLIP 能否本地跑 | **能**。`openai/clip-vit-base-patch32` 经 hf-mirror 下载 7-39s，文本/图像嵌入均出 512 维向量，语义相似度工作正常（"a red car" ↔ 红色图像 cosine 0.262） |
| 嵌入延迟达标吗 | **文本达标、单张图像不达标**：热机文本 60ms（目标无此项）、图像单张 400-811ms（PRD 目标 <200ms ❌）；**批处理 8 张摊销后每图 53ms（✅）** |
| pgvector 多向量列现状 | **不存在**——现为单 `vector(N)` 列 + `metadata jsonb`，初始化时按 `dimension` **ALTER 列**。CLIP(512) 与 MiniLM(384) 维度不同，**同列无法共存** ⇒ 多模态必须改 schema |
| 融合检索 | RRF 零依赖可实现（本文给了公式与实现草案）；但**没有可评测的数据集**，NDCG≥0.8 验收当前无法验证 |
| 音频/视频 | Whisper 模型镜像可达（307）但**未实测嵌入**；视频=关键帧+CLIP 属工程组合，无新技术风险 |
| 结论 | **文本+图像（个人版轻量）可行**；音频/视频建议按 PRD 留企业版。**落地前必须先做 schema 迁移设计**（见 §5 风险） |

## 2. 嵌入能力实测

### 2.1 环境与可达性

```
sentence_transformers: YES    torch: YES    transformers: YES    PIL: YES
huggingface.co:     000 (不可达)
hf-mirror.com:      200
openai/clip-vit-base-patch32/resolve/main/config.json   → 307 (镜像有)
sentence-transformers/all-MiniLM-L6-v2/...              → 307
openai/whisper-tiny / whisper-base/...                  → 307
```

**结论**：无梯子环境下，`HF_ENDPOINT=https://hf-mirror.com` 可完成下载。落地代码需
读取该环境变量（仓库已有此惯例，见 memory「local 后端需 HF_ENDPOINT 镜像」）。

### 2.2 CLIP 实测（`F:\tmp\clip_probe.py`、`latency_probe.py`、`batch_probe.py`）

| 项 | 实测值 |
|---|---|
| 首次 import（含 torch） | 94s（进程冷启动，一次性） |
| 模型下载 + 加载（缓存后） | 7.4s |
| 文本嵌入（热机，单条） | median 60ms |
| 图像嵌入（热机，单张） | median 811ms / min 400ms（**波动大，未达 <200ms**） |
| 图像批处理 batch=4 | 218ms 总计 → **每图 54ms** |
| 图像批处理 batch=8 | 425ms 总计 → **每图 53ms** ✅ |
| 语义有效性 | "a red car" ↔ 纯红图像 **cosine 0.262**（同维度、非随机，方向正确） |

**延迟结论（关键）**：PRD 的「图像 < 200ms」在**单张请求**下 CPU-only 不达标；
但**批处理摊销后达标**。⇒ 架构建议：**异步入队 + 批处理嵌入**（不阻塞请求路径），
而非"上传即同步嵌入"。

### 2.3 transformers 新 API 兼容性（踩坑记录）

新版 `CLIPModel.get_text_features()` 返回 `BaseModelOutputWithPooling` 而**不是** Tensor
（老教程的 `.shape` 会 AttributeError）。兼容写法：

```python
out = model.get_text_features(**inputs)
vec = out if isinstance(out, torch.Tensor) else out.pooler_output
```

落地代码必须做此双形态兼容（否则不同 transformers 版本上行为不同）。

## 3. 存储层现状与差距

### 3.1 现状（实测读码）

- `maop/core/vector/pg_backend.py`：**单** `vector(N)` 列（`vector_entries` 表），
  `dimension` 变化时 `ALTER TABLE ... TYPE vector({dim})`——**每次只能一个维度**。
- `maop/memory/vector_search.py`：SQLite 路径，`EMBEDDING_DIM = 384` 硬编码，
  暴力余弦（`np.dot`），`LIMIT 10_000`。
- `maop/memory/models.py: MemoryEntry`：纯文本字段（content/task/tags/topic…），
  **无 modality / media_ref**。

### 3.2 差距矩阵（vs PRD F2-02）

| PRD 需求 | 现状 | 差距 | 工作量估计 |
|---|---|---|---|
| R01 嵌入扩展（CLIP/Whisper/关键帧） | 无多模态嵌入器 | 新增 `Embedder` 协议 + CLIP 实现 | 中（spike 已验证可行） |
| R02 pgvector 多向量列 | 单列 + ALTER | **schema 迁移**：`vector(512)` 列或独立 `multimodal_vectors` 表 | **大**（见 §5 风险） |
| R03 融合检索（加权/RRF） | 无融合层 | 新增 RRF 模块（§4 草案） | 小-中 |
| R04 `store_multimodal` / `retrieve_multimodal` API | 无 | Facade 扩展（契约新增方法） | 中 |
| R05 模态元数据 | 无 | `MemoryEntry` 加 `modality` / `media_ref` | 小 |
| R06 个人版文本+图像、音视频企业版 | — | 按 edition 分流（`is_enterprise()` 已有惯例） | 小 |

## 4. RRF 融合检索 PoC 草案

**为什么 RRF（Reciprocal Rank Fusion）而非加权分数**：跨模态余弦分数分布**不可比**
（文本-文本 vs 文本-图像的量纲/分布不同，加权需要归一化 + 调参），RRF 只用**排名**
（rank），零调参、零归一化，是工业界跨模态融合的常见起步方案（PRD 也说"RRF 起步"）。

```python
def rrf_fuse(rankings: list[list[str]], *, k: int = 60) -> list[tuple[str, float]]:
    """跨模态融合：每个排名列表贡献 1/(k + rank)。

    rankings: 各模态的 id 排名列表（已按模态内相似度降序）。
    k: RRF 平滑常数（原论文默认 60）。
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
```

**未验证部分（诚实标注）**：NDCG≥0.8 需要标注数据集（查询→相关模态文档），当前
**没有**；spike 只验证了 CLIP 相似度**方向正确**（红车↔红图 0.262 > 随机水平），
不足以宣称融合质量达标。**这一步必须在落地版把关**（构造 50-100 条标注查询，真评测）。

## 5. 风险与阻塞（落地前必须解决）

1. **pgvector schema 迁移是最大风险**：现单列架构无法容纳 384（文本）与 512（图像）
   两个维度。三个候选方案：
   - (a) **独立表** `multimodal_vectors(id, modality, embedding vector(512), meta jsonb)`
     ——不动现表，风险最低，**推荐**；
   - (b) 现表加列 `embedding_image vector(512)`——改既有表，迁移需回填；
   - (c) 统一到 512 维（文本嵌入零填充/投影）——污染文本语义，**不推荐**。
   方案 (a) 的迁移脚本 + 回滚预案必须在落地 PR 里单独评审。
2. **无标注数据集** ⇒ NDCG 验收无法在 spike 阶段完成（见 §4）。
3. **CPU-only 延迟**：单张图像 400-811ms，需要批处理架构（节流 + 队列）才能达到
   平均吞吐目标；若客户要求单张 <200ms，需 GPU 或更小模型（ViT-B/32 已是小模型）。
4. **依赖体积**：torch + transformers 体量大（本机已装，但 pip install maop-orchestrator
   不应默认拉 torch）⇒ 多模态嵌入必须做成 **optional extra**（`[multimodal]`），
   与 PRD R06「个人版轻量」一致。
5. **视频**：关键帧抽取需 ffmpeg（本机有，但用户环境不确定）⇒ 企业版依赖，需文档化前置。

## 6. 落地建议（供 v5.4.0 排期）

**建议分期**：

- **M2.2 第一刀（个人版）**：`modality` 元数据（R05）+ `[multimodal]` optional extra
  （CLIP 文本/图像）+ 独立表存储（方案 a）+ RRF 融合（R03）+ Facade API（R04）。
  验收：文本记忆零回归（R06 硬要求）+ 图像嵌入延迟**批处理摊销 <200ms/图** + RRF
  单测（标注数据集 50 条起，真评测）。
- **M2.2-b（企业版）**：音频（Whisper）+ 视频（关键帧）+ GPU 加速选项。

**接口设计原则（写入落地 PR）**：
- `Embedder` 协议：`embed_text(list[str]) -> np.ndarray`、`embed_media(list[MediaRef]) -> np.ndarray`、
  `dimension: int`、`modality: str`。CLIP 一个实例同时给文本与图像（共享 512 维空间，
  这是 CLIP 的设计），**不是**两个嵌入器。
- 存储分离：文本向量留现表（384），媒体向量进新表（512）——**跨表融合靠 RRF，不靠维度对齐**。

## 7. 交付物清单（本 spike）

- 实测脚本（临时，不入库）：`F:\tmp\clip_probe.py`、`latency_probe.py`、`batch_probe.py`
- 本报告 + 结论入库（本文）
- **未改任何生产代码**（spike 性质）

## 8. 后续动作（按依赖序）

1. 本文入 PR 评审（若结论被接受 → 进 M2.2 施工图）
2. 落地第 1 刀开工前：schema 迁移方案 (a) 单独设计评审（含回滚）
3. 标注数据集构造（50-100 条，覆盖文本/图像跨模态查询）
4. CLIP 嵌入器 + `[multimodal]` extra 实现（可选依赖懒加载）