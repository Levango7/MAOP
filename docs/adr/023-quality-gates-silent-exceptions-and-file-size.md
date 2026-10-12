# ADR-023: 静默异常与文件规模的常态化门禁（复评处置）

- **状态**: Accepted
- **日期**: 2026-10-12
- **关联**: ADR-018（吞异常处理规范）、外部工具《MAOP 复评报告 2026-10-12》、`docs/audits/review-disposal-plan-2026-10-12.md`
- **决策者**: 独立对抗性审核通过后实施（方案首版被 REJECT，5 项硬伤修订后开工）

## 背景

复评报告指出三个"运动式治理后复发"的问题，实测核对（本仓 master `1c5ce5e`）：

1. **静默吞异常仍有存量**：门禁语义下实测 **81 处** `except` 处理器首语句即 `pass`/`continue`
   （且块内无日志/raise），另有 **47 处** `contextlib.suppress(...)`（等价吞异常）。
   旧治理脚本 `fix_swallowed_exceptions.py` 只文本匹配 `except Exception`——窄类型
   （`except ValueError:`）与注释类信息全部逃逸；且它注入的 `logger.debug("swallowed exception")`
   **正是 ADR-018 要消灭的形态**。
2. **巨型文件复长**：mcp_hub/maop_loop 拆分后，新长出 7 个 >800 行文件
   （`data_service.py` 1148、`chat_service.py` 1145 …）——缺常态化门禁，拆分成为一次性运动。
3. **CHANGELOG 结构漂移**：两个 `[Unreleased]` 段、`[5.2.0]` 段排在 `[5.0.x]` 之后；
   文档一致性门禁只覆盖 README/docs 的路径与表格，CHANGELOG 无人管。

## 决策

### D1. 静默异常门禁（`scripts/check_silent_exceptions.py`）

- **判定**：AST 扫描 `except` 处理器；首语句为 `pass`/`continue`/`...` 且块内无
  `logger.*`/`print`/`raise` → 视为静默，必须有**理由标记**：
  ```python
  except FileExistsError:  # silent-ok: lock-race — 锁已被其他进程持有
      pass
  ```
- **标记闭集**（防"任意理由合法化"）：`optional-dep` / `lock-race` / `ws-disconnect` /
  `interrupt` / `loop-teardown` / `defensive-probe` / `scan-continue` /
  `platform-limitation`（Windows chmod、fsync 目录等平台差异）/
  `best-effort-cleanup`（清理失败不掩盖主错误——原错误已重抛或另行告警）/
  `expected-signal`（超时/不存在即等待的信号）。类别不在闭集 → 门禁红
  （不是"有标记就放行"）。
- **存量已清尾（同批完成）**：81 条静默逐条分类——设计性静默加标记（宽异常 `except Exception`
  的理由按站点写具体，不用通用文案），其余按 ADR-018 记日志；**基线现为空集**
  （新增即红，无豁免缓冲）。`contextlib.suppress` 仍为 47 处棘轮（只记数、暂不逐处处置）。
- **基线键刻意不用行号**：`path::异常类型::语句种类::#第 n 次出现`（源序）——
  行号会因文件上方任何编辑而位移，用行号做键等于"改别处也会假红"（本仓反复栽过的
  假红类）；序号只依赖同类处理器的相对顺序。随附两条回归测试（位移不变键 / 同文件重复唯一）。
- 注释用 `tokenize` 读取（AST 不保留注释）；源码按 `utf-8-sig` 读（本仓 17 个文件带 BOM）。
- 与 **ADR-018 的关系**：真错误路径仍首选 ADR-018 的 `logger.warning("[module] … continuing: %s")`；
  `silent-ok` 只用于**设计性静默**（可选依赖探测、锁竞争、客户端断开、事件循环已停等），
  且理由必须写明。`fix_swallowed_exceptions.py` 作为历史工具保留，但**不再作为新代码范式**。

### D2. 文件规模门禁（`scripts/check_file_size.py`）

- 阈值 **800 行**（`splitlines()` 口径，`utf-8-sig` 读取）；`migrations/` 整体豁免
  （Alembic 迁移是 SQL 数据，拆分反而不利于审计）；存量 7 个文件进 allowlist，每条带
  **拆分排期**；allowlist 双向一致（拆完必须移除条目）。

### D3. CHANGELOG 结构门禁（并入 `check_docs_consistency.py`）

- 三条规则：①`[Unreleased]` 至多一个且必须存在；②版本段按**数字核心**降序
  （核心相同者不强制，如 `5.1.0` 与 `5.1.0-personal` 保留真实发版时间线）；
  ③版本号须为 SemVer（含 prerelease 后缀）；④非版本号的历史归档段
  （`[未归属记录]`/`[2.x]`/`[1.x]`）进豁免名单且双向一致。
- **数据修复同步完成**：第二个 `[Unreleased]`（内容跨 2026-08~09，晚于 5.2.0 的条目）
  **原地改名为归档段**——并入顶部 Unreleased 会伪造"未发布工作"；`[5.2.0]` 段整体平移至
  正确位置（块 sha256 前后一致，纯移动零内容改动）。

### D4. 版本锚点守卫

- `config/agents.yaml` 与 `docs/troubleshooting.md` 不再内嵌 MAOP 版本号（此前分别残留
  v4.3 / v4.3.0）；新增测试扫这两个文件里 `MAOP v\d` 字面量即红。第三方 CLI 版本
  （如 `Cline v3.0.46`）不受影响。

## 影响

- 新增：`scripts/check_silent_exceptions.py`、`scripts/check_file_size.py`、
  `tests/test_quality_gates.py`（三闸门的变异自证 + 双向一致性）、本 ADR。
- 修改：`check_docs_consistency.py`（+CHANGELOG 校验）、`CHANGELOG.md`（结构修复）、
  `config/agents.yaml`、`docs/troubleshooting.md`、`docs/ci-gates.md`、ci.yml（docs-gate 步骤）。
- 控制流零改动：本次不修任何运行时代码行为（存量清尾是独立批次）。

## 后续（明确排期，禁止无限期挂账）

| 项 | 内容 | 状态 |
|---|---|---|
| 静默清尾 | 81 条逐条分类（标记或改 warning），基线归零 | **已完成**（同批，基线=空集） |
| suppress 棘轮 | 47 处（`contextlib.suppress`）——当前只棘轮计数，逐处语义审查后排期 | 排期中 |
| 巨型文件拆分 | `data_service.py` / `chat_service.py` / `agent_service.py` / `evolution_service.py` 按域拆分（参照 core/mcp/ 先例） | 排期中 |
| bandit Medium 债 | 147 条（B608×141 等）分类处置 + 门禁 `-lll`→`-ll` + nosec 清单快照 | 下一批（独立 PR） |
| keyring 后端 | 个人版 secret 可选 keyring 后端（可选 extra + 回退可见标注） | 待排期 |
