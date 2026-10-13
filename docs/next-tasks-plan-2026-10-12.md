# 下一阶段任务规划（2026-10-12）

> 基线：master `151c661`；已发布 v5.3.1；无开放 PR/issue；工作树干净。
> 排序依据：能力收益 / 风险可控 / 兼容性 / 可靠性 / 可持续性（用户既定口径）。

## 波次一（本周，低风险高收益）

### 1. B608 ×141 收口 → 门禁 `-lll`→`-ll`
- **依据**：`docs/audits/review-disposal-plan-2026-10-12.md` 附录 A（自动分类 84 处 + 必人工核 57 处）。
- **做法**：两个 commit——①常量/占位符生成类照表加 `# nosec B608 — <理由>`；
  ②`builder`/`other` 逐站点读构造器：只允许"固定子句 + `?`"（核实后 nosec），发现值拼接 → **改参数化**。
- **先决门禁**：`tests/test_bandit_security_fixes.py::test_nosec_inventory_matches`
  （每加一条 nosec 必须登记，防批量消音）。
- **验收**：`bandit -r maop/ -ll` = 0；ci.yml 切 `-ll`；PR 附"修复前后计数对照"。
- **为何第一**：复评里唯一"承诺挂了两个月"的项；风险低（注释为主），收益=安全面最大未决项归零。

### 2. T2.1 dry-run 验收核验与勾选
- PR #84 已交付哨兵契约 + pipeline 真预演 + dispatch 接线。
- 核验 AC："`MAOP_DRY_RUN_ENFORCE=1` 下 pipeline 任务真预演通过且写操作零副作用（临时目录断言）"
  → 补齐/确认零副作用断言后勾选 ROADMAP，不留空挂。

### 3. 发 v5.4.0（含真安全修复）
- 打包：[Unreleased] 现有 4 段——M2.2 首批（文本+图像/CLIP/RRF）+ 质量门禁三连（ADR-023）
  + bandit 真安全修复（B615/B310/B103）+ wheel-install CI 作业 + T2.1 dry-run。
- **⚠️ roadmap 变更记账**：v5.4.0 定义由"M2.2 全量（B2/B3/B4 收官）"改为
  "M2.2 首批"，B2/B3/B4 顺延 v5.4.x/v5.5.0 —— 按仓库规则 #3 在 PR 描述说明并在 ROADMAP 标注。
- 理由：安全修复不应长期躺在 master 等主线；发布链已全自动（tag 即上 PyPI）。

## 波次二（v5.4.0 窗口，主线）

### 4. M2.2 B2：PG 路径 + 迁移 + 异步队列
- PG 实现（`multimodal_entries` 的 pgvector 表 + 每模态 partial index，ADR-022 D2）
- `alembic 003`（含 downgrade）+ `sqlite_to_pg._COPY_ORDER` 登记新表
- 异步队列/批处理接线 → **NFR 延迟达标闭环**（单张 65-293ms 波动，不做同步承诺）
- 存储量化评估（ADR-022 D7 的 <205KB 图片 1% 不可达记账 → fp16/int8 选项）
- 证据要求：**真 PG 容器实测**（不能只跑 SQLite 腿）

### 5. 巨型文件拆分（7 个 allowlist 递减）
- 优先 `data_service.py`(1148) / `chat_service.py`(1145)：按域切 `dashboard/services/data/`；
  参照 `core/mcp/` 拆分先例（父模块保留转发，防 import 面破裂）。
- 每拆一个就从 `check_file_size.py` 的 ALLOWED_OVERSIZE 移除条目（双向一致门禁强制）。

## 波次三（按余力）

### 6. M2.2 B3 / B4
- B3：音频（Whisper embedding）/视频（关键帧+CLIP）——企业版边界（ADR-016）
- B4：NDCG 标注数据集（50-100 条）+ 评测 harness + 可切换模型评估（Chinese-CLIP/AltCLIP，
  应对 CLIP 模型卡的 out-of-scope 声明，ADR-022 D8）

### 7. T3.3 K8s Operator M0（roadmap 标"按余力"）
- kopf 骨架 + `MaopTask` reconcile 最小闭环 + `ghcr.io/maop/operator` 真实构建 + kind 集成腿激活。

### 8. 治理面收尾
- 47 处 `contextlib.suppress` 逐处语义审查 → 收紧棘轮基线
- keyring 后端（可选 extra + 无桌面环境回退且可见标注）
- `maop agents probe`（本机可跑探活；nightly 版已判定无信息量——CI runner 上外部 CLI 全不存在）
- T2.3：nightly 连续 7 天零假红观察台账（机制现在就能挂）

## 不变量（每批都适用）
1. 分支 → PR → CI 22 检查全绿 → 合并；合并前验 `merged:true` 才动分支。
2. 设计先过独立对抗性审核（本会话三轮实测：每轮都抓到真问题）。
3. 动工前 `git fetch`（仓库有并行活动：#90/#92 为外部 PR）。
4. 三闸门（静默/文件规模/CHANGELOG）+ 全量测试 + ruff/mypy 是每批的出口条件。
