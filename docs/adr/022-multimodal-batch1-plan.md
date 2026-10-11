# M2.2 Batch-1 实施方案（v2，已按独立审核修订）

> 配套 ADR: `docs/adr/022-multimodal-memory-store.md`。v1 经独立对抗性审核（APPROVE WITH
> MINOR ISSUES，8 项应修），本版逐条修订：I1 文本腿链路精确化 / I2 数据目录 / I3 批处理承诺 /
> I4 坏行加固 / I5 验收映射诚实化（预计算真向量夹具）/ I6 图像输入安全 / I7 存储编码与 NFR 记账 /
> I8 许可记录；另采纳 S1-S10 与 6 条遗漏风险中的可落项。

## 1. 范围

PRD 4.3（F2-02）的**个人版文本+图像（R06）SQLite 路径**：嵌入扩展（图像 CLIP）、独立空间存储、
RRF 融合、Facade API、非 mock 集成用例。PG 路径/异步队列（B2）、音视频（B3）、NDCG 数据集（B4）明确不做。

## 2. 文件级方案

### 2.0 基座修改：`py/maop/core/memory/vector_store.py`（默认值即现状的加法）

```python
class VectorStore(VectorSearchMixin):
    def __init__(self, db_path=None, embedding=None, *,
                 hnsw_threshold=DEFAULT_HNSW_THRESHOLD, enable_hnsw=True,
                 table_name: str = "vector_entries",        # 新增
                 create_sql: str | None = None,             # 新增（None=既有 DDL）
                 expected_dim: int | None = None):          # 新增
```
- 内部 10 处 SQL 由硬编码 `vector_entries` 改为 `{self._table}`（唯一改动点，测试网覆盖既有行为）。
- 新增模块级 `_decode_stored_vector(raw: str | bytes) -> list[float]`：以 `[` 开头按 JSON 解，
  否则按 `len(raw)//4` 的 float32 解（**向后兼容既有 JSON 数据**；multimodal 写 BLOB）。
- `_load_cache()`：`expected_dim` 非 None 时逐行校验，**异维行跳过 + WARNING**（审核 I4：
  防一条坏行让 `np.array` 抛 ValueError 穿透 `search_vector`，拖垮全库）。
- 测试：既有 VectorStore 测试零改动；新增 `test_vector_store_space_params.py`（参数默认值等价、
  BLOB 解码、坏行跳过但其余可搜、table_name 隔离两库共存）。

### 2.1 `py/maop/core/memory/multimodal_embed.py`（新增）

```python
ImageInput = str | Path | bytes   # 校验见 §2.6

class MultimodalEmbeddingProvider(EmbeddingProvider):
    model_id: str = "unknown"       # 空间标识 → 写入条目 metadata
    is_semantic: bool = True        # 回退 provider 置 False
    @abstractmethod
    def embed_image(self, image: ImageInput) -> list[float]: ...
    def embed_image_batch(self, images: list[ImageInput]) -> list[list[float]]:
        return [self.embed_image(i) for i in images]   # 基类串行；CLIP 覆盖为单次前向

class ClipEmbedding(MultimodalEmbeddingProvider):   # _dim = 512, model_id = model_name
    def __init__(self, model_name="openai/clip-vit-base-patch32", *, device="cpu"): ...
    # 惰性 import transformers/torch/PIL；ImportError 带安装指引 + HF_ENDPOINT 提示
    # 文本：processor(text=[...], return_tensors="pt", padding=True) 单次前向
    # 图像：processor(images=[...]) 单次前向（审核 I3；禁止逐张循环）
    # 双形态：features = out.pooler_output if hasattr(out, "pooler_output") else out
    # 输出 float32 → np.asarray(...).reshape(-1).astype(float).tolist()

class HashModalityEmbedding(MultimodalEmbeddingProvider):   # _dim = 512, is_semantic=False
    """确定性回退（无 torch 环境/CI）。图像侧哈希输入 = 文件字节（非像素），
    文档与 docstring 明示"仅管道可跑、无语义"。"""

class PrecomputedEmbedding(ClipEmbedding 形状的 provider):
    """审核 I5 采纳：从夹具读真 CLIP 预计算向量（text→vec / image_sha256→vec），
    无 torch 也能在 CI 做**真语义**跨模态排序断言（向量是真实 CLIP 输出，非伪造）。"""
```

### 2.2 `py/maop/core/memory/multimodal_store.py`（新增，子类化）

```python
class MultimodalStore(VectorStore):
    """空间 = 512 维 CLIP。独立库文件 + 独立 DDL + 模态经 metadata 派生。
    继承：_load_cache/_get_entry_info/delete/clear/count/list_all 与 mixin 检索链。
    自建/覆盖：index（覆盖为 BLOB 写入，防记账漂移回 JSON）/index_many（按 modality
    分组批嵌入，返回 id）/index_batch（LSP 兼容 int 计数覆盖）/index_text/index_image/
    search/search_image/stats（基类无 stats）。"""
    def __init__(self, db_path=None, embedding=None, *, enable_hnsw=False):
        super().__init__(db_path or resolve_data_dir() / "multimodal.db",
                         embedding or HashModalityEmbedding(),
                         enable_hnsw=enable_hnsw, table_name="multimodal_entries",
                         create_sql=_MULTIMODAL_DDL, expected_dim=512)
        # 若检测到 MAOP_VECTOR_BACKEND=pg / MAOP_DB_BACKEND=postgresql → WARNING
        # 说明 B1 走 SQLite（PG 路径 B2）
    # 模态**不另设字典**：一律从 _meta_cache[id]["modality"] 派生
    # （继承的 delete/clear 已同步维护该缓存，无脏 id 问题）
    def index(self, entry_id, text, *, vector=None, metadata=None) -> str   # 覆盖→index_text
    def index_text(self, entry_id, content, *, metadata=None) -> str       # 自有 INSERT（BLOB）
    def index_image(self, entry_id, image, *, caption="", metadata=None) -> str
    def index_many(self, items: list[dict]) -> list[str]   # 按 modality 分组批量嵌入（单次前向，返回 id）
    def index_batch(self, entries) -> int                   # LSP 兼容覆盖（int 计数，仍走 BLOB）
    def search(self, query, *, top=10, threshold=0.0, modality=None)
        # modality=None → super().search（跨模态，复用 tier 链）
        # 指定 modality → 在（_cache ∩ 该 modality）子集上 numpy/纯 python 余弦
    def search_image(self, image, *, top=10, threshold=0.0)  # 图找图（modality='image'）
    def stats(self) -> dict      # 自建：count + 按 modality 分组计数 + degraded 标记
```

### 2.3 `py/maop/core/memory/fusion.py`（新增）

```python
class FusedHit(BaseModel):
    id: str; score: float; sources: list[str]; payload: dict[str, Any]

def reciprocal_rank_fusion(
    ranked_lists: Sequence[tuple[str, Sequence[tuple[str, float]]]],
    *, k: int = 60, weights: dict[str, float] | None = None, top: int | None = None,
) -> list[FusedHit]:
    """纯函数：只用 rank；同 id 跨列表合并；ties 按 id 稳定排序。"""
```

### 2.4 `py/maop/memory/multimodal.py`（新增服务层 + 单例）

```python
class MultimodalMemory:
    def __init__(self, store: MultimodalStore | None = None,
                 text_search: Callable[..., list[dict]] | None = None):
        """text_search 契约（审核 N1）：Callable 而非裸方法引用——两实现的
        long_term_search 均为 keyword-only（`*, top=`），Facade 注入时用
        `lambda q, n: impl.long_term_search(q, top=n)` 适配，防位置参数 TypeError
        导致文本腿静默降级。"""
    def store(self, content: str | ImageInput, *, modality=None, caption="",
              metadata=None, entry_id=None) -> str
    def store_batch(self, items: list[dict]) -> list[str]
    def retrieve(self, query: str | ImageInput, *, top=10, modalities=None,
                 include_text_memory=True) -> list[dict]
        # (a) 多模态空间腿 → (b) text_search 腿（可注入）→ (c) RRF 融合 → 统一排序
        # 每条带 sources 标记（multimodal / text_memory）与 degraded 状态
        # 文本腿异常兜底：失败只降级（跳过该腿 + 日志），不让 retrieve 整体抛
    def stats(self) -> dict

def get_multimodal_memory(*, text_search=None, data_dir=None) -> MultimodalMemory  # 模块级单例 + Lock
def reset_multimodal_memory() -> None    # 测试复位（对齐 ADR-019 单例可测性范式）
```

### 2.5 `py/maop/memory/facade.py`（纯新增两方法）

```python
def store_multimodal(self, content, *, modality=None, caption="", metadata=None) -> str
def retrieve_multimodal(self, query, *, top=10, modalities=None,
                        include_text_memory=True) -> list[dict]
```
- 懒初始化经 `get_multimodal_memory()`（模块级单例，**不**在 facade 实例上缓存，避免与
  facade"不缓存底层状态"的类契约冲突——审核 S4）；text_search 腿按 mode 用
  `lambda q, n: self.impl.long_term_search(q, top=n)` 注入（审核 N1：keyword-only 适配），
  并在单例首次构造后**每次调用重绑**（facade 换 mode/impl 时不错绑）。
- 未装 extra 不抛异常：回退 provider + degraded 标记。

### 2.6 图像输入校验（审核 I6）

`multimodal_embed.py` 内 `validate_image_input(src) -> tuple[bytes, str]`（bytes, ext）：
- 扩展名白名单 / `MAX_IMAGE_SIZE = 20MB` 常量与语义对齐 `image_store.py:36-37`（import 复用其常量，不复制字面量）；**白名单取 ImageStore 的子集、去掉 `.svg`**（审核 N6：Pillow verify 解不了 SVG，放行只会把拒绝推迟到解码层）。
- `PIL.Image.open(BytesIO).verify()` 解码校验；path 输入做 `resolve()` 防穿越（对齐 image_store.py:92-103）。
- `ClipEmbedding`/回退 provider/`MultimodalStore.index_image` 统一入口校验，任何路径不绕过。

### 2.7 `py/maop/core/backends/db_utils.py`

`_resolve_data_dir` → 公开 `resolve_data_dir()`（保留旧名转发，零破坏；同步更新
`core/backends/__init__.py` 的 `_SYMBOL_TO_MODULE` 惰性导出映射——审核 N8）；
`multimodal.db` 落此目录。pg/postgresql 环境变量 warning 的落点 =
`MultimodalStore.__init__`（见 §2.2）。

### 2.8 `py/pyproject.toml`

```toml
multimodal = ["transformers>=4.40,<6", "pillow>=10.0", "torch>=2.2"]
```

## 3. 测试计划（`py/tests/`）

| 文件 | 内容 |
|---|---|
| `test_vector_store_space_params.py` | table_name/create_sql/expected_dim 默认等价；BLOB/JSON 双解码；坏行跳过其余可搜；两库共存 |
| `test_multimodal_embed.py` | 回退 provider 确定性/512 维/`is_semantic=False`；图像校验（超限/坏格式/路径穿越拒绝）；CLIP provider 缓存缺失→skip（捕 from_pretrained 异常，conftest 强制 offline 环境）；金标向量自检（cos 阈值，CLIP 可用时） |
| `test_multimodal_store.py` | roundtrip、modality 过滤、图找图、threshold、delete/clear/stats 分组、index_many 分组单次前向**结构性断言**、非 512 拒绝、空库 |
| `test_fusion.py` | RRF 排序/权重/ties/空列表/跨列表重复 id/单列表保序 |
| `test_multimodal_integration.py` | **非 mock 集成**：真实 SQLite + 真实 MultimodalMemory 全链；①stats 模态计数 ②RRF 跨空间去重融合 ③modalities 过滤 ④**预计算真 CLIP 向量夹具的语义排序断言**（CI 可跑，非伪造）⑤ `long_term_index` 修复回归（接口面存在性） |
| `tests/fixtures/clip_gold.json`（新） | 本机真 CLIP 输出：5 文本 + 3 图像（按 sha256 键）向量 + 金标文本自检对 |

## 4. 验收映射（诚实版，审核 I5 修订）

| PRD 条目 | B1 覆盖 | 说明 |
|---|---|---|
| R01 图像 CLIP（P0，图/音/视频） | ⚠️ 部分 | 仅图像、CLIP 单模型；本机实证；音视频 B3 |
| R02 存储扩展（P0） | ⚠️ 部分 | SQLite 空间表落地；"每模态独立索引"SQLite 侧=内存过滤，PG partial index B2 |
| R03 融合检索（P0） | ⚠️ 部分 | RRF 起步（PRD 原文"加权融合/重排"未实现，接口预留） |
| R04 Facade API（P0） | ✅ | `store_multimodal`/`retrieve_multimodal` |
| R05 模态元数据（P1） | ⚠️ 部分 | modality/时间戳/来源记录；bytes 原始文件落盘委托 ImageStore（B2） |
| R06 个人版=文本+图像（P1） | ✅ | |
| NFR 嵌入延迟 | ⚠️ 部分 | 批处理 API 达标路径（本机实测覆盖 batch 路径写入 PR）；异步队列 B2；单张不达标如实标注 |
| NFR 检索延迟 P95<150ms（百万级） | ❌ B2 | 本机无百万级数据；B2 真数据压测 |
| NFR 存储增量<1% | ⚠️ 记账 | fp32 BLOB 2048B/条；<205KB 图片该指标数学不可达（详见 ADR D7）；量化 B2 |
| NFR 现有 API 零变更 | ✅ | 门禁：既有测试零改动 |
| AC 非 mock 集成 | ✅ 管道 + ✅ 语义排序 | 语义排序用**真实 CLIP 预计算向量**在 CI 断言（非回退 provider 自欺，非 mock） |

## 5. 风险与对策（审核后补全）

| 风险 | 对策 |
|---|---|
| 回退 provider 被误当语义 | `is_semantic=False` + stats degraded + 集成测试不做语义断言 + 文档/日志明示 |
| 多进程可见性 | 如实记录（与 VectorStore 同源限制）；写路径即时可见、跨进程最终一致（重启加载） |
| 并发写 multimodal.db | sqlite_connect WAL + busy timeout（既有）；索引写入单进程场景优先 |
| mixin 宿主属性契约 | 子类化 VectorStore 后由基类提供全部属性（`_hnsw_index`/`_enable_hnsw` 等），无手工契约（审核 S2 消解） |
| 表格化限制 | 空间固定 512；维度校验 + expected_dim 双重 |
| torch 体积/HF 缓存 | optional extra；HF_HOME 治理与 air-gap 预置进部署文档；CI 不装（既有先例） |
| 许可 | D8：权重不再分发、model_id 入库、备选模型评估（B4 前） |
| 回滚 | 删 `multimodal.db` + 回退代码（B1 无迁移） |

## 6. 明确不做

PG 实现/迁移/异步队列（B2）；Whisper/视频（B3）；NDCG harness/标注数据集（B4）；
dashboard 展示多模态条目（B2 决策）；原始图像落盘与清理（委托 ImageStore，B2 接线）。