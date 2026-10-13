# M2.2 B2 收尾：多模态运行时接线（设计 v1，待独立审核）

> 2026-10-14。范围 = ROADMAP「B2 仍未交付」第一条：**异步队列与任务/worker 的进程间接线**。
> 结论先行：这不是"接一根线"，实测发现**两处真断链**——异步队列零生产调用方，
> 且 `ClipEmbedding`（真 CLIP provider）**全仓无任何构造点**，即生产路径上多模态
> 记忆一直跑在 hash 回退上（`provider_id="hash-fallback"`，非语义）。
> 既有 dead-wiring 门禁只覆盖**模块级**孤儿，类级死代码（ provider/队列 API 无调用方）
> 不在其口径内——这正是它能长期绿着的原因。

## 1. 现状（代码实证）

| 组件 | 状态 | 证据 |
|------|------|------|
| `MultimodalIngestQueue` | 写好、14 例测试、**零生产调用方** | `store_async`/`drain_ingest` 仅 `memory/multimodal.py` 自身定义，全仓无引用 |
| `ClipEmbedding`（真 CLIP） | 写好、**全仓零构造点**（含测试） | `grep -rn CLIPEmbeddingProvider` 空；类名是 `ClipEmbedding`，`grep` 亦空 |
| provider 默认值 | `MultimodalStore.__init__` 硬编码 `HashModalityEmbedding()` | `multimodal_store.py:123` |
| 图片到达事件 | `ImageStore.save()`（chat 上传 → 磁盘 + SQLite meta） | `core/backends/image_store.py`，`dashboard/services/chat_service.py:save_image` |
| 进程模型 | dashboard(uvicorn) / worker 均长驻；各自持队列、共享 DB | `server.py` lifespan 有 shutdown 段 |

**判定**：B1/B2 已交付部分是"引擎"；现在缺的是"传动轴"。不接线的话，
ADR-022 D4 的延迟预算实测（53ms/图）在生产里永远不会发生，CLIP 语义检索也不会发生。

## 2. 设计（三段）

### 2.1 provider 工厂（让真 CLIP 有机会上场）

`multimodal_embed.py` 新增 `get_multimodal_provider()`：
- 解析顺序：**显式注入 > 设置覆盖 > 自动探测**。设置字段
  `multimodal_provider: str = ""`（`MAOP_MULTIMODAL_PROVIDER`；""=auto，
  `clip`=强制 CLIP（失败即报错，不静默回退），`hash`=强制回退（测试/CI 用））。
- auto = `ClipEmbedding` 可构造则用之，否则 `HashModalityEmbedding()` + **一次性 warning**
  （写明"非语义回退，装 [multimodal] extra 才有语义检索"）。
- **进程内缓存**（CLIP 权重 ~600MB，重复构造是灾难）；`reset_multimodal_provider()` 供测试。
- 构造失败（装了一半/权重不可达）→ 记 error 并回退 hash，**不让进程起不来**。

接线点：`memory/multimodal.py:_build_store()` 给 SQLite/PG 后端注入工厂 provider。
**`MultimodalStore.__init__` 默认值保持 hash 不变**——否则本机（有 torch）跑直接构造
store 的单测会尝试下载 CLIP 权重，测试套件被拖垮/flake。产品入口（facade→service→
`_build_store`）走工厂。这是"默认值保守、入口智能"的取舍。

### 2.2 上传自动索引（异步队列的真调用方）

- 设置字段 `multimodal_auto_index: str = ""`（`MAOP_MULTIMODAL_AUTO_INDEX`；
  ""=auto，`1`=开，`0`=关）。**auto = 解析出的 provider 是语义型**
  （`ClipEmbedding` ⇒ 开；hash 回退 ⇒ 关）。理由：hash 向量无语义还占 2KB/张，
  没装 extra 的用户行为必须逐字节不变；装了 extra 的用户上传即可检索。
- `chat_service.save_image()` 落盘后调用 `maybe_index_uploaded_image(...)`
  （新函数，放 `memory/multimodal.py`）：读设置 → 开则 `store_async(image=Path, metadata=
  {source:"chat-upload", session_id, image_id, filename})` → submit 幂等（同 image_id 二次
  上传=更新同 id）。
- **fail-soft**：索引任何失败只 warning，不影响 upload 返回（上传是主路径）。
- **`store_async` 补同步兜底**：队列已 close 时 submit 返回 "" —— 现状是**静默丢弃**；
  改为同步写一次 + warning（与队列自身"关停降级同步"的承诺一致，消灭丢数据路径）。

### 2.3 停机 drain（进程边界闭环）

- dashboard lifespan shutdown 段追加 `drain_multimodal_ingest(timeout=3)`：
  取单例（不存在=没开过，零开销）→ `drain_ingest()` → suppress 全部异常。
- 每个 uvicorn worker 各自 lifespan ⇒ 各自 drain 自己的队列（队列是进程内的，
  DB 共享，无跨进程争抢）。文档写明**崩溃窗口**（≤2s 未 flush 的条目会丢）——
  与 PR-3 的"非持久队列"决策一致，不扩范围做持久化。

## 3. 测试（先红后绿 / 变异自证）

1. **provider 工厂**：无 torch ⇒ hash + 降级标记（CI 现状即此路径，必须绿）；
   强制 `hash` ⇒ 不 import torch；强制 `clip` 且构造失败 ⇒ 明确报错不静默；
   缓存 ⇒ 两次调用同一对象。
2. **接线存在性守卫（类级死代码门禁）**：`test_multimodal_wiring_guards.py`
   —— ①`_build_store` 必须从工厂取 provider（解析源码断言 + 注入假 provider 走通）；
   ②`save_image` 必须调用自动索引（monkeypatch `store_async` 记调用，断言 metadata 带
   session_id/image_id）；③死代码回归：`ClipEmbedding` 与 `store_async` 在全仓**非测试**
   代码里出现引用点（新孤儿即红）。
3. **自动索引行为**：默认关（CI 无 torch）⇒ 走 `save_image` 不创建 multimodal.db、
   不启动队列；强制开（hash 回退）⇒ drain 后session 图可检索、metadata 齐全；
   图片坏（不存在的 path）⇒ 不炸、记 warning。
4. **延迟钉子**：注入 sleep(0.5s) 的 provider ⇒ `save_image` 端到端 <0.3s
   （证明 embedding 不在请求路径上）；随后 drain ⇒ 真写入。
5. **同步兜底**：close 后 submit ⇒ 走同步写、条目不丢。

## 4. 文档与记账

- `docs/configuration.md` 两个新 env 行；README 多模态节补"上传即可检索（需 extra）"。
- CHANGELOG [Unreleased] 新段；ROADMAP「仍未交付」去掉进程间接线，保留存储量化/B3/B4。

## 5. 明确不做（防范围膨胀）

- 持久化/跨进程队列（2s 窗口的磁盘队列收益不成比例，PR-3 已论证）。
- task/worker 侧的批量回填（当前无"任务产出图片"的生产路径，接了是死码；
  WRITE 守卫会先红——等真有产出路径再接）。
- CLIP 模型切换 UI / 多模型并存（B4 的可切换模型评估再做）。

---

## v2 修订（独立对抗性审核回执，2026-10-14）

审核结论 **REJECT（11 必修 + 8 建议）**。逐条处置：

| # | 必修项 | 处置 |
|---|--------|------|
| M1 | 重复上传=新 uuid id ⇒ 多模态重复入库（"同 id 幂等"是空话） | 条目 id 由**图片 checksum** 派生（`img-<sha256[:16]>`），真重复上传=同 id upsert |
| M2 | `index_many` 图像组**一坏全丢**（8 张里 1 张被删⇒整批 skip，接线后首次被生产触发） | 改 **per-item 降级**：先整批，失败逐张重试、只跳过失败者；SQLite 与 PG 两后端同修 |
| M3 | auto-index 默认判据**机器相关**（本机有 extra+HF 缓存=开），测试"默认关"在本机红 | 测试显式置 `MAOP_MULTIMODAL_AUTO_INDEX=0/1`；另立一条只断言"默认值可预测"的用例；文档写明默认值取决于 extra+权重缓存 |
| M4 | 首个上传者独扛 ~600MB/150s 模型加载（正是要消除的请求路径延迟） | lifespan **启动段预热** provider 并 info 记录解析结果（provider + auto-index 决策） |
| M5 | 停机只 `drain()` 不 `close()` ⇒ **每次优雅停机必丢** worker 手里那批 | `drain_multimodal_ingest()` 内部改 `close(drain=True, timeout)`；未 flush 数记 error；文档口径改为"≤ flush_interval_s 且未 close" |
| M6 | 停机"取单例"实际会**创建**单例（没上传过的部署白加载 600MB） | 新增非创建入口 `get_multimodal_memory_if_exists()` |
| M7 | 上传路径调 `get_multimodal_memory()` 会**静默抹掉 RRF 文本腿**（每次重绑 text_search） | 重绑改 `if text_search is not None` |
| M8 | 文本 grep 门禁可被变异绕过（命中类定义/docstring） | 改 **AST call-site 检测** + 扫描器自证用例（合成"无调用"源码 ⇒ 必须报 0） |
| M9 | `.env.example` 漏同步 ⇒ `test_env_example_drift` 必红 | 两个新字段同步写 `.env.example`（含默认值说明） |
| M10 | 无 provider 注入 seam，"显式注入"是空话；墙钟阈值在 Windows 必 flake | `get_multimodal_memory(..., embedding=)` / `_build_store(..., embedding=)` 加 seam；延迟钉子改为"**submit 返回前零次 embed 调用**" |
| M11 | 同步兜底返回 `""` ⇒ 调用方无法区分成功与丢弃 | 同步兜底返回**真实 eid** |

建议项采纳：①摄入队列挂 `_bg_enabled` 同款门控（多 worker 场景）+ 文档写明跨 worker 检索可见性延迟；②`drain()` 按 `batch_size` 分块（防 50 张一次前向拖爆 join 超时）；④三态值闭集解析 + 非法值 warning；⑤`reset_multimodal_memory()` 内 close 所有队列 + 清 provider 缓存，并进 conftest autouse teardown（ADR-019 单例毒害案发现场）；⑥`maybe_index_uploaded_image` 完整放 `memory/multimodal.py`，`chat_service.save_image` 只加 3 行（该文件已在 oversize 清单）；⑦ADR-022 D4 勾销；⑧ImportError（没装）/RuntimeError（装了一半）分 warning/error。另按审核更正：`ClipEmbedding` 在 `test_multimodal_embed.py` 有 2 处测试构造且**本机 torch+权重缓存齐备**（故本机默认 provider=CLIP，测试必须显式隔离）；延迟数字改引 ADR-022 D4 原文（批 42ms/图、单张 65-293ms）；"worker 各自持队列"说法删除（worker 无摄入点）；**仓里没有 WRITE 守卫**，"明确不做"的理由改写。
