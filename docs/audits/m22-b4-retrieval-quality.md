# M2.2 B4 检索质量实测：NDCG@5、判别力、与覆盖面（2026-10-14）

> 度量入口已产品化：`maop eval multimodal [--k 5] [--provider auto|clip|hash]`。
> 复算夹具：`python scripts/build_clip_eval_fixture.py`（需 torch + 权重缓存）；
> 校验夹具（CI 安全、无 torch）：`python scripts/build_clip_eval_fixture.py --check`。

## 语料与口径

12 张确定性合成图（4 色 × 3 形状，PIL 生成，`py/tests/fixtures/multimodal_eval/`）
+ 8 条查询（4 条 exact、4 条 paraphrase）。相关性分级：**2** = 同色同形、
**1** = 同色异形、0 = 其他。查询文本**不**入被检语料（否则 hash 对相同字符串恒等
self-hit，#1 白拿）。向量来自 `openai/clip-vit-base-patch32`（revision 钉死
`3d74acf9…0268`，Pillow 12.2.0 生成）。

指标 = NDCG@5（gain/log2(i+1)，i 从 1 起；IDCG 同样截断到 k；全零相关 ⇒ 0.0）。

## 实测

| provider | semantic | mean NDCG@5 | min | k |
|---|---|---|---|---|
| `clip-precomputed-fixture`（真 CLIP） | yes | **0.8661** | 0.7623 | 5 |
| `hash-fallback` | **no** | **0.1005** | 0.0000 | 5 |

每条查询的分数（CLIP 侧）：0.762 / 0.882 / 0.762 / 0.900（exact 组），
0.840 / 1.000 / 0.882 / 0.900（paraphrase 组）——**复述查询不差于直述查询**，
说明测的是语义而不是字符串巧合。

判别力边际 **+0.766（8.6×）**。测试里钉的三个门槛（CLIP ≥ 0.75、hash ≤ 0.25、
边际 ≥ 0.5）按实测留了裕量：模型/权重正常变化不 flake，而"harness 偷用 CLIP
夹具跑两行""排序被忽略""回退变语义了"三类造假会直接红（变异实测：把排序改成
按 id 排 ⇒ 三个判别力用例全红）。

## 结论

1. **真 CLIP 在合成语料上 0.87、hash 回退 0.10**：度量有效，且成本极低
   （CI 用冻结夹具即可断言真语义，不需要 torch）。
2. **CLIP 对"颜色"强、对"形状"弱**：sanity 矩阵显示 exact 命中 6/8 排第一，
   两处失手都是 circle↔triangle 混淆（0.30 vs 0.29 量级）。分级标注因此把
   "同色异形"设为 1 分——与模型实际能力对齐，而不是先写标注后碰运气
   （这是设计审核 S1 的要求，已在建夹具时先跑 12×12 矩阵再定 grade）。
3. **中文查询是已知盲区**：CLIP 模型卡限定英文用例（ADR-022 D8），本数据集
   全英文；"中文优先产品的中文检索质量"未被测。触发条件见下。

## 边界（不宣称什么）

- **不宣称 PRD 的"NDCG ≥ 0.8 达标"**：本 harness 只提供可复现度量，PRD 验收
  仍按人工评估口径走（见 `docs/prd-three-phase-roadmap.md`）。
- **语料规模 12+12，不是 50-100 条**（next-tasks-plan 原计划）：够证明判别力与
  流程，**不够做模型选型结论**。计划缩减按仓库规则 #3 在此记账。
- **未做多模型横评**（Chinese-CLIP / AltCLIP）：触发条件 = 出现第二个候选模型
  或中文查询实测低于可接受水位时启动，判据 = 同一 harness 的 mean NDCG@5 对比
  （第一批须补中文查询集）。
- **只评 text→image 单向**：`search_image`（图→图）API 现成但未纳入。
- live-CLIP 互证（夹具向量 vs 真模型）是 `slow` 标记用例，仅在有权重的机器上跑
  （本机 2026-10-14 实测通过，cosine ≥ 1−1e-4；CI 无 torch 自动 skip）。
