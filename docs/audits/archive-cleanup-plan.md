# P2-9 archive/ 目录清理分析报告（只读）

> 分析对象：`F:\Nexus\MAOP\archive/`（旧 JS dashboard 与 PS 脚本归档）
> 报告性质：**只读清理分析**。本报告仅统计、搜索、分类与给出命令清单，**未执行任何删除/移动操作**。
> 生成日期：2026-08-16
> 分析结论摘要：`archive/` 全部 107 个文件均被 git 跟踪、零运行时依赖（无任何 `import` / `open` / 路径加载），可整体 `git rm -r archive/` 安全清理；历史内容由 git history 留存。

---

## 1. archive/ 目录现状

### 1.1 总览（`python os.walk` 实测）

| 指标 | 数值 |
|---|---|
| 总文件数 | **107** |
| 总大小 | **957,268 B ≈ 934.8 KB（0.91 MB）** |
| git 跟踪状态 | 107/107 全部被跟踪（`git ls-files archive/`），`.gitignore` 无 archive 相关条目 |
| 最近一次提交 | `62a8767 chore(data): track seed configs + harden backup cleanup` |

### 1.2 顶层子目录结构（前 2 层）

```
archive/  (0 文件，容器目录)
├── js-dashboard/        (17 文件, 428.0 KB)  ← 旧原生 JS dashboard
│   └── js/              (14 文件, 144.2 KB)
│       └── vendor/      (chart.umd.min.js, 205 KB)
├── legacy/              (4 文件, 3.0 KB)     ← 旧启动 wrapper
└── ps-legacy/           (86 文件, 503.8 KB)  ← EOL 的 PowerShell 引擎
    ├── gates/           (13 文件, 7.0 KB)
    └── tests/           (12 文件, 93.6 KB)
```

### 1.3 文件类型分布（数量 + 大小）

| 扩展名 | 文件数 | 大小 | 说明 |
|---|---|---|---|
| `.ps1` | 87 | 504.9 KB | ps-legacy/ 全部 + legacy/start-server.ps1 |
| `.js` | 15 | 344.8 KB | js-dashboard/js/ 14 个（含 vendor 库 205 KB）+ 无 |
| `.css` | 1 | 51.2 KB | js-dashboard/style.css |
| `.html` | 1 | 32.0 KB | js-dashboard/index.html |
| `.py` | 2 | 1.4 KB | legacy/run_dashboard.py、start_dashboard.py |
| `.bat` | 1 | 0.5 KB | legacy/start.bat |
| **合计** | **107** | **934.8 KB** | |

### 1.4 子目录内容摘要

**js-dashboard/（17 文件）**：`index.html` + `style.css` + `js/` 下 `app-*.js` 共 13 个业务脚本 + `js/vendor/chart.umd.min.js`（Chart.js，205 KB，占比最大）。这是被 Vue 3 取代的原生 JS 仪表盘。

**legacy/（4 文件）**：`run_dashboard.py` / `start_dashboard.py`（旧启动 wrapper）、`start-server.ps1`、`start.bat`。两个 .py 的 docstring 自述："Canonical entry: python -m MAOP.dashboard.server，This wrapper exists for backward compatibility"。

**ps-legacy/（86 文件）**：EOL 的 PowerShell 引擎（`engine.ps1`、`delegate.ps1`、`dag-engine.ps1`、`memory.ps1`、`pev-*.ps1` 等 61 个顶层脚本 + `gates/` 质量门 13 个 + `tests/` Pester 测试 12 个）。

---

## 2. 引用检查（Grep）

搜索范围：`py/`、`docs/`、`dashboard/`、`README.md`，补充：`dashboard-enterprise/src/`、`config/`、`maop.ps1`、`scripts/`、`tools/`、`CHANGELOG.md`、`deliverables/`。

### 2.1 结论先行

**没有任何代码对 `archive/` 下的文件做运行时引用**（无 `import` / `from` / `open()` / `Path()` / 字符串路径加载）。全部命中均为**描述性文本**（注释、错误提示、文档说明归档事实）。**例外**：`dashboard-enterprise/playwright.config.js:23` 引用的 `../py/start_dashboard.py` 位于 `py/`，与 `archive/legacy/` 无关（已确认 `py/start_dashboard.py` 存在，是独立文件）。

### 2.2 命中明细（含 archive 路径的引用）

| 文件:行 | 内容 | 性质 |
|---|---|---|
| `py/maop/dashboard/server.py:80` | `# Legacy native JS dashboard archived to archive/js-dashboard/` | 注释 |
| `py/maop/core/routing/dynamic_router.py:3` | `Python port of archive/ps-legacy/dynamic-router.ps1.` | 注释（Python 移植版） |
| `config/agents.yaml:731` | `PS wrapper archived to archive/ps-legacy/.` | 描述文本 |
| `maop.ps1:77` | `PS scripts have been archived to archive/ps-legacy/.` | 错误提示文本 |
| `README.md:330` | `原生JS版本已归档至 archive/js-dashboard/` | 描述文本 |
| `docs/contributing.md:91` | `├── archive/  # 归档代码（PS1 legacy）` | 目录结构文档 |
| `docs/DESIGN_RULES.md:5,135,165` | 多处：`已归档至 archive/js-dashboard/，不再适用` | 描述废弃 |
| `CHANGELOG.md:565,612,614,681` | `PS 脚本 EOL v4.0`、`start_dashboard.py/start.bat/start-server.ps1/run_dashboard.py moved to archive/`、`Canonical entry: python -m maop.dashboard.server` | 归档记录 |
| `dashboard/dist-enterprise/assets/*.js` | 构建产物中渲染的文档内容（README/DESIGN_RULES 等编译产物） | 构建产物文本 |
| `docs/archive/**` | `docs/archive/audits/...`、`docs/archive/plans/...` | **注意：是 `docs/archive/`（文档归档），与根 `archive/` 是两个不同目录** |
| `dashboard-enterprise/src/*` | `AppIcon.vue` 图标名 `archive`、`Tasks.vue` 状态 `archived` 等 | UI 图标/状态枚举，与目录无关 |
| `dashboard-enterprise/playwright.config.js:23` | `python ../py/start_dashboard.py` | 引用 `py/` 下文件，**非 archive/** |

### 2.3 仍需被引用的 archive 内容

**无。** 以上命中无一条需要 `archive/` 中文件继续存在（均为说明性文本，删除后只是文档里的路径描述失效，不影响运行）。

---

## 3. 分类判断与处理建议

### 3.1 可安全删除（git history 已留存）

| 目录 | 理由 |
|---|---|
| `archive/js-dashboard/` | 已被 `dashboard-enterprise/`（Vue 3 + Vite）取代；`docs/DESIGN_RULES.md` 明确"随归档废弃，不再适用"；`README.md:330` 确认归档事实 |
| `archive/ps-legacy/` | `CHANGELOG.md` 记录 "EOL v4.0，Python 为唯一运行时，61 个 .ps1 移入 archive"；`docs/archive/audits/MAOP_COMPREHENSIVE_ANALYSIS.md:69` 记录"零运行时依赖"；`py/maop/core/routing/dynamic_router.py` 是其中 `dynamic-router.ps1` 的 Python 移植版（替代完成） |
| `archive/legacy/` | 4 个文件均为旧启动 wrapper；`CHANGELOG.md:614` 记录 canonical entry 已切换为 `python -m maop.dashboard.server`；`py/start_dashboard.py` 为现行替代；无任何脚本再调用这些 wrapper（grep 确认） |

### 3.2 可迁移（如有价值的文档/配置）

**无必须迁移项。** 可选建议（不执行，供决策）：

- 若希望删除后仍能从工作区快速找回"原生 JS dashboard"的入口，可在 `docs/` 的某处（如 `docs/README.md` 或本报告）写一行指引：通过 `git log -- archive/js-dashboard/` 回溯，无需保留物理文件。
- `legacy/` 的两个 .py wrapper 逻辑（约 20 行）已被 `py/start_dashboard.py` 完整覆盖，无需迁移。

### 3.3 建议保留

**无。** `archive/` 下所有内容均无引用依赖、无运行价值。**特别提醒**：`docs/archive/`（11+ 份历史审计/计划文档）与根 `archive/` 无关，**不在本次清理范围**，勿误删。

---

## 4. 建议 git 命令清单（**仅清单，未执行**）

### 4.1 方案 A：整体清理（推荐，符合 P2-9 目标）

```bash
# 一次性移除整个 archive/（git history 已留存，可随时回溯）
git rm -r archive/

# 提交（不含在本次只读范围，以下仅示例）
git commit -m "chore(archive): remove archived JS dashboard & EOL PS scripts (P2-9)"
```

### 4.2 方案 B：分步清理（若要分次提交）

```bash
# 1) 先删被完全取代的旧 JS dashboard（最大头，428 KB）
git rm -r archive/js-dashboard/

# 2) 再删 EOL 的 PowerShell 引擎
git rm -r archive/ps-legacy/

# 3) 最后删旧启动 wrapper
git rm -r archive/legacy/
```

### 4.3 前置保险（可选，强烈建议先做）

```bash
# 在删除前打 tag 锚点，保证归档内容可一键找回（不占工作区、不占检出）
git tag archive-v4.0-before-removal
```

### 4.4 删除后建议同步更新（另开 PR/提交，非本任务范围）

```bash
# docs 中描述性文本里的 archive/ 路径会变成失效链接，可在后续任务中清理：
#   - docs/contributing.md:91     （目录结构示例）
#   - docs/DESIGN_RULES.md        （"归档至 archive/js-dashboard/" 表述）
#   - README.md:330
# 注：dashboard/dist-enterprise/ 构建产物中的相关文本会随下次重新构建自动消失，无需手改。
```

---

## 5. 风险标注

| 风险 | 等级 | 说明 |
|---|---|---|
| 破坏运行时 | **无** | py/ 中零 import/open 依赖 `archive/`；唯一含路径的 `playwright.config.js:23` 指向 `py/start_dashboard.py`（已确认独立存在） |
| 破坏测试 | **无** | ps-legacy 测试为 Pester 脚本，自引用 `F:\Nexus\MAOP\tests\*.tests.ps1`（该路径已不存在），属历史遗留，非现行测试链 |
| 文档失效链接 | 低 | README/contributing/DESIGN_RULES/CHANGELOG 及 `dashboard/dist-enterprise` 构建产物中的描述文本会指向已删除路径，属文档问题，不影响功能 |
| 误删 `docs/archive/` | 低（需注意） | `docs/archive/` 是独立文档归档目录（11+ 份审计/计划），grep 时极易混淆，执行删除时务必只写 `archive/`（根路径） |
| 历史可恢复性 | 可控 | 所有内容已提交进 git history；建议删除前先打 `git tag` 锚点（见 4.3），或直接用 `git log`/`git show <commit>:archive/...` 取回 |
| 权限/路径风险（Windows） | 低 | 如用 `rm -rf` 而非 `git rm`，注意路径书写；本报告一律建议 `git rm -r` |

---

## 6. 结论

1. `archive/` 共 **107 文件 / 934.8 KB**，全部已入 git history，属"git history 已留存"的典型可清理内容。
2. 三个子目录（js-dashboard / legacy / ps-legacy）**全部零运行时引用**，均可安全删除；无需迁移项、无需保留项。
3. 建议执行 `git rm -r archive/`（或分步三连），删除前先 `git tag archive-v4.0-before-removal` 做锚点；文档中的描述性引用留待后续文档清理任务处理。
