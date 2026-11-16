# MAOP 发布灰度通道与回滚预案

> **适用对象**：MAOP 作为 PyPI 库（`maop-orchestrator`）与 GitHub Release 发行物的分发。
> **权威依据**：`.github/workflows/ci.yml` 的 `publish` / `release-assets` 作业、
> `py/scripts/check_release_tag.py`、`py/scripts/attach_release_assets.py`。
> 本文只描述现有 CI 已具备的能力 + 明确缺失的人工流程；不声称存在任何未实现的自动化。
> **新增**：2026-11-16。此前 `docs/deployment.md` 第 7 章只有部署层的升级/回滚命令，
> 发布层的灰度通道与回滚决策流程缺位，本文补上这一层。

---

## 1. 为什么灰度是 rc tag，而不是流量切分

MAOP 的主分发形态是 PyPI 包与 GitHub Release 附件，不是常驻在线服务。因此：

- **没有流量可以切分**。`docs/design-supervisor-agent.md` 里描述的 agent 加权灰度
  （10% → 50% → 100%）是**被编排的 agent 升级**设计，且原文已注明"首版仅支持全切
  rollout=1.0，加权灰度延后"——它不是 MAOP 自身的发布通道，不要混用这个概念。
- **灰度的等价物是 PEP 440 预发布版本**。用 `x.y.zrcN`（alpha/beta 同理）打 tag，
  PyPI 会把该版本标记为 pre-release，默认 `pip install maop-orchestrator` **不会**装到它；
  只有显式指定版本或加 `--pre` 的消费者才会拉取。这就是自选型灰度：
  愿意尝鲜的消费者先装，稳定通道不受影响。

## 2. 现有 CI 已经具备的能力（逐项可核）

| 环节 | 状态 | 依据 |
|---|---|---|
| rc tag 能触发发布 | 已有 | `ci.yml` 的 `on.push.tags: ['v*']` 匹配 `v5.8.0rc1` |
| tag 版本与包版本一致性 | 已有 | `py/scripts/check_release_tag.py` 的正则 `vX.Y.Z[\w.]*` 接纳 rc，并要求等于 `__version__` |
| PyPI 自动标记预发布 | 已有 | PEP 440 语义，PyPI 侧行为，不需要本仓库配置 |
| GitHub Release 预发布标记 | **2026-11-16 补** | `py/scripts/attach_release_assets.py` 创建 Release 时对 rc/a/b tag 加 `--prerelease` |
| 发布前质量门禁 | 已有 | `publish` / `release-assets` 均 `needs: [test, frontend, e2e]` |
| PyPI trusted publisher | **外部前置，未核** | 需在 PyPI 项目 `maop-orchestrator` 登记本仓库为 trusted publisher；未配置时真 tag 会 401/403 |

**诚实边界**：`--prerelease` 只在**创建** Release 时生效。若 tag 已存在，脚本走
`gh release upload ... --clobber`，不会改动该 Release 的 prerelease 标记。因此灰度 tag
必须一次打对；重跑不会把已发成 stable 的 Release 改回 pre-release。

## 3. 灰度发布操作流程

### 3.1 打 rc tag 前

1. 在 `CHANGELOG.md` 的 `[Unreleased]` 段写清本次变更，并新建 rc 版本段。
2. 版本号同步**四处**（仓库 checklist 明列）：`pyproject.toml`、`py/maop/__init__.py`、
   `Dockerfile`、`package.json`。四处必须同为 `x.y.zrcN`，否则
   `check_release_tag.py` 会以"tag ≠ 包内版本"拦下。
3. 确认 `[Unreleased]` 无未解决的 P0/P1 issue（紧急 hotfix 除外，需事后复盘）。

### 3.2 发布与验证

```bash
git tag -a v5.8.0rc1 -m "Release candidate 1 for 5.8.0"
git push origin v5.8.0rc1
```

推送后观察 `ci.yml` 的 `publish` 与 `release-assets` 两个作业。二者都是 tag 触发，
且都依赖 `test` / `frontend` / `e2e`；任一红灯即视为灰度失败，不做后续晋级。

消费者侧验证（由愿意参与灰度的用户执行，不是 CI 步骤）：

```bash
python -m pip install --pre maop-orchestrator==5.8.0rc1
python -c "import maop; print(maop.__version__)"
```

### 3.3 观察窗口与晋级判据

- **最短观察窗口**：1 个完整工作日（与仓库"发布前 checklist"的 staging 验证口径一致）。
- **晋级为正式版**：窗口内无新 P0/P1、无指向该 rc 的可复现安装失败报告 → 版本号去掉
  `rcN` 后缀，四处同步改回 `x.y.z`，打正式 tag。
- **不晋级**：任一可复现的安装失败或行为回归 → 走第 4 章回滚路径 A。

## 4. 回滚预案

### 4.1 触发条件（量化，不再用"出问题就回滚"）

| 级别 | 触发条件 | 处置 |
|---|---|---|
| P0 | 安装即失败、鉴权绕过、数据损坏 | 立即回滚，不等观察窗口 |
| P1 | 核心链路回归且可复现（≥1 例） | 停止晋级，24h 内定位；不能定位则回滚 |
| P2 | 文档/非核心 UI 问题 | 不回滚，随下一个 patch 修复 |

### 4.2 决策与执行

- **决策人**：仓库 owner（发布动作需 PyPI trusted publisher 权限，天然只能是 owner）。
- **记录要求**：每次回滚必须在 `CHANGELOG.md` 对应版本段写明触发条件、决策时间与
  回滚目标版本；这是"已回滚"是否真实发生的唯一可审计凭据。
- **时限**：P0 从确认到执行回滚 ≤ 2 小时；P1 ≤ 24 小时。

### 4.3 三条回滚路径

**路径 A — 灰度期回滚（rc tag，最轻）**

1. 停止向正式版晋级；在 GitHub Release 说明里追加"此 rc 已弃用 + 原因"。
2. rc 版本**保留在 PyPI 不删**（PyPI 不支持删除已发布版本）；用 `pip index versions
   maop-orchestrator --pre` 确认它仍可见，让已安装者能查到。
3. 修复后打新的 rc tag（`rc(N+1)`），不要复用同一版本号——PyPI 版本不可覆盖。

**路径 B — 正式版回滚（补丁版本，标准做法）**

1. 对坏版本执行 PyPI yank：登录 PyPI → 项目 `maop-orchestrator` → Releases →
   选中版本 → "Yank"。yank 后新安装不会选到它，已安装环境不受影响。
   这是**外部动作**，本仓库的 CI 没有也不应有此权限。
2. 在 GitHub Release 正文追加弃用说明（保留附件，不要删 release——删除会断掉
   已有 `pip install <release-asset-url>` 的链接）。
3. `git revert` 引入回归的提交 → 版本递增 patch（`x.y.z+1`）→ 走完整 CI → 发新版本。
   **不允许**重新发布同一个版本号。

**路径 C — 部署层回滚（已有能力，复用即可）**

已部署实例的回滚命令在 `docs/deployment.md` 第 7 章：Docker Compose 切回
`<previous-version-tag>` 重建、Kubernetes `helm rollback maop-operator 0`、裸机先备份
`data/` 再切版本。发布层回滚（A/B）与部署层回滚（C）是两件事：前者改"用户能装到什么"，
后者改"我这套实例在跑什么"，不可以互相替代。

### 4.4 验证回滚是否真的完成

```bash
python -m pip index versions maop-orchestrator --pre
python -m pip install maop-orchestrator==<目标版本>
python -c "import maop; print(maop.__version__)"
```

三条都要对：版本列表里坏版本已 yank、目标版本可安装、`__version__` 是目标版本。
只做第一条就宣布"回滚完成"不算数。

## 5. 已知缺口（如实登记，不假装覆盖）

- **无自动 yank**：PyPI yank 需账号，CI 不做；路径 B 第 1 步必须人工执行。
- **无自动晋级**：rc → stable 需要人工改四处版本号并打新 tag，没有"一键晋级"命令。
- **无回滚演练**：本文是流程文档，不是演练记录；尚未在真实事故中验证过时限（2h/24h）。
- **Release 标记不可事后修正**：见第 2 章末尾的诚实边界。
