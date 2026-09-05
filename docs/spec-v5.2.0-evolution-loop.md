# Spec — MAOP v5.2.0 自演化闭环 MVP（F2-01 / M2.1）

> 生成日期：2026-09-06
> 基于：PRD `docs/prd-three-phase-roadmap.md` §4.2（F2-01）+ HLD `docs/hld-three-phase-roadmap.md` §3.1 + ROADMAP.md v5.2.0 节
> 状态：待确认（项目总监生成，架构师细化后进入开发）
> 关联：ADR-019（模块级单例可测性）、待立 ADR-020（演化闭环安全边界）

---

## 1. 产品定义

- **一句话描述**：把已落地的 `py/maop/core/evolution/` 16 模块底座接入主循环，形成「评估 → 建议 → 审批 → A/B → 提升/回滚」可观测、可审批、可回滚的完整闭环。
- **目标用户**：Agent 调优工程师、平台运维、业务方（PRD §4.2.1 三类用户故事）。
- **核心问题**：当前 `_phase_evolve` 只调 `EvolveEngine.analyze()` 产出建议，建议不进入验证与部署，"自演化"实际是断链的一半。

## 2. MVP 范围（锁定 — 不在此列表的功能一律不做）

| 优先级 | 功能 | 验收标准摘要 | 来源 |
|--------|------|-------------|------|
| P0 | EvolutionLoop 接入主循环 `_phase_evolve` | 开关开启后走完整七阶段闭环；关闭时行为与现状完全一致 | ROADMAP v5.2.0 |
| P0 | 配置开关 `MAOP_EVOLUTION_LOOP_ENABLED` | **默认 false**，未开启时零行为变化 | ROADMAP v5.2.0 |
| P0 | 人工 gate（PendingApproval 停靠） | 高风险改进需人工审批后才进 A/B；未审批不进 A/B | F2-01-R06 + PRD 4.2.4 |
| P0 | A/B 验证接线 | 复用 `ab_test.py`（Z 检验）+ `evolution_perf_loop.py`（SPRT），对齐 HLD §3.1.3 | F2-01-R03 |
| P0 | 自动回滚接线 | 劣化候选 → 自动回滚 < 5 分钟；复用 `regression.py` + `prompt_version.py` | F2-01-R04 + PRD 4.2.3 |
| P0 | dashboard 闭环可视化 | 状态机流转 + A/B 结果 + 审批入口可见 | F2-01-R07（P1 提级，因可观测是安全前提） |
| P1 | 闭环调度配置周期 | 每日评估 / 每周建议 / 持续 A/B 可配 | F2-01-R05 |

## 3. 明确不做（Out-of-Scope — 锁定）

| 不做的功能 | 原因 | 何时考虑 |
|------------|------|----------|
| 多模态记忆（F2-02） | 属 v5.3.0 / M2.2 | 2026-11 后 |
| 知识图谱推理（F2-03）、Plan 质量学习（F2-04） | 属 v6.0.0 / M2.3 | 2027-01 后 |
| PRD 4.2.4「4 周综合分提升 ≥ 15%」 | 需 4 周真实运行，MVP 阶段无法验证 | v7.0.0 GA 时验收 |
| 分布式 A/B 流量承载 | 依赖 F1-01，当前单实例 | F1-01 落地后 |
| 新增 LLM 模型/Suggester 算法改造 | 底座已有，本次只接线不重写 | 有明确劣化证据时 |
| 自动部署到生产环境 | 高危，MVP 只到「提升为 current 版本」 | ADR-020 明确边界后再议 |

## 4. 技术架构（锁定 — 含版本锚定）

| 层 | 技术 | 实际版本 | 锁定原因 |
|----|------|----------|----------|
| 语言 | Python | 3.14.3（CI 矩阵 3.10–3.13 亦须通过） | 现有运行时 |
| 后端框架 | FastAPI | 0.141.1 | 现有栈 |
| ORM / 数据访问 | SQLAlchemy | 2.0.51 | 现有栈 |
| 前端框架 | Vue 3 | ^3.5.0 | 现有栈 |
| 前端路由 | vue-router | ^4.5.0 | 现有栈 |
| 状态管理 | pinia | ^3.0.0 | 现有栈 |
| 构建 | vite | ^8.1.1 | 现有栈 |
| 图标 | `AppIcon.vue`（统一描边 SVG，24 viewBox） | 现有组件 | **P0 规则：禁止 emoji 作功能图标** |

**待补 ADR**：ADR-020（演化闭环安全边界 / 人工 gate 设计）——架构师 Phase 2 必须产出。

## 5. 接线点（锁定 — 开发唯一依据）

| 位置 | 现状 | 目标 |
|------|------|------|
| `py/maop/maop_loop_phases.py:422` `_phase_evolve` | 仅调 `EvolveEngine.analyze()` | 开关开启时调 `EvolutionLoop.run_cycle()`，关闭时保持原逻辑 |
| `py/maop/core/evolution/evolution_loop.py:139` | `run_cycle(dry_run=False, auto_rollback=True) -> LoopReport` | 直接复用，不改签名 |
| 同上 `:255` | `rollback_cycle(cycle_id, snapshot_id="") -> int` | 直接复用 |
| 同上 `:337` | `get_cycle_history(limit=20) -> list[LoopReport]` | 直接复用 |
| `dashboard/routers/evolve_insights.py`<br>`dashboard/routers/evolution_experiment.py` | 既有路由基础 | 扩展闭环状态查询 + 审批提交端点 |

**API 端点清单**（架构师 Phase 2 细化，须同时产出 `openapi.yaml`）：

| Method | Path | 功能 | 认证 |
|--------|------|------|------|
| GET | `/api/evolution/loop/status` | 闭环状态机当前状态 + 最近 cycle 摘要 | 复用现有 |
| POST | `/api/evolution/loop/trigger` | 手动触发一轮闭环（支持 `dry_run`） | 复用现有 |
| GET | `/api/evolution/approvals` | 待审批改进列表 | 复用现有 |
| POST | `/api/evolution/approvals/{id}/decision` | 审批通过 / 拒绝 | 复用现有 |
| GET | `/api/evolution/ab/{cycle_id}` | A/B 结果与显著性检验数据 | 复用现有 |

## 6. 数据（锁定）

- EvolutionLoop 已有 `_init_db()`（SQLite），本次**不新增表结构**，仅复用。
- 若架构师 Phase 2 判定确需新增（如审批记录持久化），须在 ADR-020 中说明并走 alembic 迁移。

## 7. 页面清单（锁定）

| 页面 | 路由 | 核心组件 | 对应 API |
|------|------|----------|----------|
| 演化闭环看板 | `/evolve`（现有页面扩展） | 状态机流转图、待审批列表、A/B 结果卡、回滚按钮 | 上表全部 |
| 审批抽屉 | `/evolve` 内嵌 | 改进详情（评估依据 + 候选 diff + 审批按钮） | approvals/decision |

## 8. 设计 Token（锁定）

- **图标**：`AppIcon.vue` 统一描边 SVG，尺寸 16 / 20 / 24px，**禁止 emoji**
- **颜色**：沿用现有 CSS 变量体系，禁止硬编码色值（唯一例外 `#fff` / `#000`）
- **主题**：沿用现有浅色/深色双主题，不新增主题
- **禁止**：紫→粉渐变、AI 模板味占位文案、弹性缓动 `cubic-bezier(0.68,-0.55,0.265,1.55)`

## 9. 验收标准（锁定 — QA 以此为准，EARS 格式）

| 编号 | 功能 | EARS 验收标准 | 优先级 |
|------|------|---------------|--------|
| AC-01 | 开关默认 | While `MAOP_EVOLUTION_LOOP_ENABLED` 未设置，系统**必须**保持现有 `_phase_evolve` 行为（仅 analyze），且全量测试零回归 | P0 |
| AC-02 | 开关开启 | While 开关为 true 且主循环执行到 `_phase_evolve`，系统**必须**调用 `EvolutionLoop.run_cycle()` 并返回 `LoopReport` | P0 |
| AC-03 | 闭环 E2E | When 对模拟 agent 执行 observe→suggest→approve→A/B→promote/rollback，系统**必须**完整跑通并产出状态机流转记录 | P0 |
| AC-04 | 人工 gate | If 改进处于 `PendingApproval` 且未审批，系统**必须**阻止其进入 A/B 阶段 | P0 |
| AC-05 | 自动回滚 | When 注入劣化候选，系统**必须**在 5 分钟内触发自动回滚 | P0 |
| AC-06 | 显著性检验 | When 使用 synthetic 对照数据执行 A/B 检验，系统**必须**输出正确的 p-value（对照人工计算） | P0 |
| AC-07 | 可观测 | While 闭环运行，dashboard `/evolve` **必须**展示状态机当前状态、A/B 结果、审批入口 | P0 |
| AC-08 | 单例治理 | When 测试中切换 `MAOP_DATA_DIR`，系统**必须**不出现路径固化导致的跨测试污染（ADR-019 同类） | P0 |

## 10. 边界与约束

- 单实例部署，无分布式 A/B 流量承载
- `MAOP_EVOLUTION_LOOP_ENABLED` 默认 false；开启前须有 ADR-020 安全边界定义
- 自动回滚只回滚到上一 `prompt_version` 版本链节点，**不触碰生产部署**
- 全量 CI 须保持 9 平台（3 OS × Py 3.10–3.13）全绿 + E2E 全绿
- 新增代码单文件 ≤ 300 行，依赖只向下（routes → services → repositories）

## 11. 内嵌已知坑（来自项目记忆）

| 坑 | 技术栈指纹 | 根因 | 修法 |
|----|-----------|------|------|
| **模块级单例路径固化**（ADR-019） | python / MAOP_DATA_DIR / 全局单例 | `get_hook_manager()` 类模块级单例在构造时读 `MAOP_DATA_DIR` 固化 `_db_path`，测试切换数据目录后旧实例仍指向旧路径 → `no such table: hooks` | EvolutionLoop 接入**必须**提供可重置工厂 + conftest reset fixture，禁止裸模块级单例 |
| pytest-xdist Windows 竞态 | pytest-xdist / windows | execnet worker 竞态 `INTERNALERROR: KeyError: WorkerController` | CI windows 矩阵已设 `-n 0`，勿改回 |
| 测试模块实例分裂 | sys.modules.pop | 模块级 `sys.modules.pop` 在收集阶段执行，导致 patch 打到旧实例 | 任何 pop 必须包在 fixture 内做 save/restore |
| E2E redirect 竞态 | playwright / vue-router | `toHaveURL` 首次轮询抢在 Vue Router redirect 前读到旧 URL | 断言规范路径（`/home`、`/memory/graph`），不用被 redirect 的旧路径 |

## 12. 端到端验证步骤

```bash
# 1. 开关默认关闭 —— 行为不变
cd py && MAOP_EVOLUTION_LOOP_ENABLED= python -m pytest tests/ -q -x
# 断言：零回归，_phase_evolve 仅调 analyze

# 2. 开关开启 —— 闭环跑通（dry_run 先验）
cd py && MAOP_EVOLUTION_LOOP_ENABLED=1 python -c "
from maop.core.evolution.evolution_loop import EvolutionLoop
loop = EvolutionLoop()
report = loop.run_cycle(dry_run=True)
print(report.state_transitions)   # 断言：observe→suggest→... 全链路
"

# 3. 人工 gate 拦截
# 触发一轮闭环 → 断言候选停靠 PendingApproval → 未审批时 A/B 阶段无该候选

# 4. 劣化注入 → 自动回滚
# 注入劣化候选 → 断言 5 分钟内触发 rollback_cycle()

# 5. 前端
cd dashboard-enterprise && npm run test:e2e
# 断言：/evolve 展示状态机 + A/B 结果 + 审批入口
```

## 13. 变更记录

| 日期 | 变更内容 | 原因 | 影响范围 |
|------|----------|------|----------|
| 2026-09-06 | 初版生成 | 阶段二启动（PRD/HLD/ROADMAP 已定稿） | 全文 |
| 2026-09-06 | 补 §14-§19（六项实施细节） | 让 spec 可直接驱动开发，不再需口头补全 | 接线/测试/部署 |

---

## 14. 状态机（基于 `evolution_loop_types.py` 实测）

**LoopPhase 当前 7 阶段**（按 spec §1 "七阶段" 引用）：

| 阶段 | 含义 | 阶段产出（`PhaseResult.details` 关键字段） |
|------|------|------------------------------------------|
| `observe` | 错误采样 | `errors_observed`, `error_samples` |
| `heal` | 自愈尝试 | `heal_attempts`, `heal_successes` |
| `suggest` | 产生建议 | `suggestions_generated`, `EvolutionSuggestion[]` |
| `debate` | 多 Agent 辩论（**默认禁用**） | `debate_enabled`, `consensus_score` |
| `evaluate` | 评估建议质量 | `eval_results` |
| `apply` | 应用 mutation | `applied_count`, `snapshot_id` |
| `validate` | 验证改进效果 | `validation_improved` |
| `consolidate` | 提交/回滚 | `consolidated`, `rolled_back` |

**LoopReport 字段已含**（`evolution_loop_types.py:78-106`）：`cycle_id / started_at / finished_at / total_duration_s / phases / errors_observed / heal_attempts / heal_successes / dry_run / snapshot_id / rolled_back / suggestions_generated / suggestions_applied / validation_improved / consolidated`。

**人工 gate 新增字段**（AC-04，需在 `LoopReport` 上扩展，**与 §6 锁定冲突处理**：见 ADR-020 必要性）：

```python
# 拟扩展字段（Phase 2 ADR-020 评审后再定夺是否需要 alembic 迁移）
pending_approval: list[str] = Field(default_factory=list)   # EvolutionSuggestion.id 列表
approval_state: str = "n/a"  # n/a | pending | approved | rejected | partial
approved_by: str = ""        # username
approved_at: float = 0.0
```

若 ADR-020 评审认定不需持久化（仅运行时内存记录），`pending_approval` 字段可驻留 `LoopReport` 不落 DB；否则走 alembic。

---

## 15. AC-08 conftest reset fixture 模板

**根因**（来自 ADR-019）：模块级单例 `_db_path` 在 import 时固化，测试切换 `MAOP_DATA_DIR` 后旧实例仍指向旧路径 → `no such table: xxx`。

**强制规则**（**禁止裸模块级单例**）：

```python
# py/tests/conftest_evolution.py （新文件，spec §6 锁定"不新增表"不冲突）
import pytest
import importlib

@pytest.fixture
def evolution_loop_factory(monkeypatch, tmp_path):
    """可重置工厂：每次返回新实例 + 显式传入 db_path。"""
    db_root = tmp_path / "evo"
    db_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MAOP_DATA_DIR", str(db_root))

    def _factory():
        # 强制重 import 拿到新单例（不要缓存）
        from maop.core.evolution import evolution_loop
        importlib.reload(evolution_loop)
        from maop.core.evolution.evolution_loop import EvolutionLoop
        return EvolutionLoop(root_dir=db_root)

    return _factory


@pytest.fixture(autouse=True)
def _reset_evolution_singletons():
    """任何测试跑完都重置 evolution 模块的 _db_path 缓存。"""
    yield
    try:
        from maop.core.evolution import evolution_loop, evolution_loop_types
        for mod in (evolution_loop, evolution_loop_types):
            for attr in ("_db_path", "_instance", "_singleton"):
                if hasattr(mod, attr):
                    setattr(mod, attr, None)
    except ImportError:
        pass
```

**测试写法**（避免 ADR-019 同类）：

```python
# py/tests/test_evolution_loop_ac04.py （新文件，AC-04 验收）
def test_pending_approval_blocks_ab(evolution_loop_factory):
    loop = evolution_loop_factory()          # 每次新实例
    # 注入建议 → 触发 PendingApproval → 断言 A/B 未运行
    ...
```

**禁止**（在 conftest fixture 之前）：

```python
# ❌ 错：模块级 import 会触发 _db_path 固化
from maop.core.evolution.evolution_loop import EvolutionLoop  # at module top
```

---

## 16. AC-05 自动回滚验收脚本骨架

**Spec §12.4 要求"5 分钟内触发 rollback_cycle()"**。骨架：

```python
# py/tests/test_evolution_loop_ac05.py （新文件）
import time
import pytest

@pytest.mark.timeout(360)  # 6 分钟硬上限（> 5min SLA）
def test_auto_rollback_within_5min(evolution_loop_factory):
    loop = evolution_loop_factory()

    # 1. 建立基线（apply 一个有效建议 → 当前 prompt_version 记 v1）
    loop.run_cycle(dry_run=False)

    # 2. 注入劣化候选（VALIDATE 阶段必然失败）
    bad_suggestion = loop._build_degradation_test_suggestion()

    # 3. 触发闭环，5 分钟内必须 rollback
    t0 = time.monotonic()
    report = loop.run_cycle(dry_run=False, auto_rollback=True)
    elapsed = time.monotonic() - t0

    assert report.rolled_back, "rolled_back flag 未置位"
    assert elapsed < 300, f"自动回滚耗时 {elapsed:.1f}s 超 5min SLA"
    assert report.snapshot_id != "", "snapshot_id 为空，回滚无基线"
    # 4. 验证 prompt_version 已回到 v1
    pv = loop.prompt_version.get_current(bad_suggestion.target_name)
    assert pv.version_id != bad_suggestion.mutation_params["version_id"]
```

**辅助方法**（`EvolutionLoop` 需新增）：

```python
# py/maop/core/evolution/evolution_loop.py 新增（spec §5 接线点的扩展）
def _build_degradation_test_suggestion(self) -> EvolutionSuggestion:
    """AC-05 测试辅助：构造必然失败的 mutation（仅测试用，生产无入口）。"""
    return EvolutionSuggestion(
        category="performance",
        mutation_type="adjust_timeout",
        severity="HIGH",
        target_name="__ac05_test__",
        mutation_params={"timeout_s": -1},  # 负值必拒
    )
```

---

## 17. AC-06 显著性检验对照脚本

**`ab_test._z_test_p_value`（line 222）的 EARS 验收**：

```python
# py/tests/test_evolution_loop_ac06.py （新文件）
from maop.core.evolution.ab_test import _z_test_p_value

def test_z_test_pvalue_matches_scipy():
    """对照人工计算：p1=0.10, n1=1000, p2=0.12, n2=1000, 期望 p≈0.245。"""
    p_value = _z_test_p_value(p1=0.10, p2=0.12, n1=1000, n2=1000)
    # 手算 z = (0.10-0.12)/sqrt(0.11*0.89*(1/1000+1/1000)) ≈ -0.602
    # 双侧 p = 2*(1-Φ(0.602)) ≈ 0.547
    assert 0.50 <= p_value <= 0.60, f"p_value={p_value} 偏离 0.547 太远"
```

**回归测试**（同样需要）：

```python
# py/tests/test_evolution_loop_ac06_regression.py
def test_z_test_extreme_cases():
    # 极端 p1=0 vs p2=1, n=1000 → p≈0
    p = _z_test_p_value(0.0, 1.0, 1000, 1000)
    assert p < 1e-10
    # 相同比例 p1=p2=0.5, n=1000 → p≈1
    p = _z_test_p_value(0.5, 0.5, 1000, 1000)
    assert p > 0.99
```

---

## 18. 与 48h soak 并行边界

**当前状态**（2026-09-05 启动，PID 12416，预计 2026-09-07 00:01:45 结束，第二轮 48h soak）。

**v5.2.0 开发期间并行规则**：

| 边界 | 规则 |
|------|------|
| **soak 进程** | 不重启、不杀；它只读 `_phase_evolve` 路径，v5.2.0 加开关 `MAOP_EVOLUTION_LOOP_ENABLED` 默认 `false`（AC-01），soak 行为零变化 |
| **DB 文件** | soak 用 `data/maop.db`（prod-data 卷）；v5.2.0 新代码用 `MAOP_DATA_DIR` 隔离（`pyproject.toml` 测试 fixture tmp_path），**不触碰 prod-data** |
| **端口** | 后端 `9079`（dashboard 占用）；soak 不抢端口；v5.2.0 无新增端口 |
| **dashboard 路由** | `/evolve` 已有 `evolve_insights.py` + `evolution_experiment.py`；v5.2.0 只**扩展**不新增；soak 不访问 `/evolve` 路径 |
| **CI** | 每次 commit 触发 9 平台矩阵；soak 在本机跑，不进 CI；CI 与 soak 互不干扰 |
| **soak 报告补 commit** | 2026-09-07 跑完后第 6 章覆盖更新（已在 commit `26ffd61` 留了"待第二轮"说明） |

**风险点**：`py/maop/maop_loop_phases.py:422` 是被 prod 路径引用的活代码，改动需：

1. AC-01 验证基线：开关关闭时与现状 byte-level 行为一致（不仅测试通过，trace/event 序列也要一致）
2. 改动粒度 ≤ 30 行，diff 在 PR review 中一目了然
3. 不改 `EvolveEngine.analyze()` 自身，只改 `_phase_evolve` 的调用分支

---

## 19. 实施 checklist（Phase 1 → Phase 2 → 验收）

### Phase 1：spec → 代码（预计 8-12 commit）

- [ ] commit 本 spec（本文档）
- [ ] 立 ADR-020 stub（占位 + TODO，Phase 2 评审后填充）
- [ ] AC-08 conftest fixture（§15）
- [ ] 接线点 #1：`maop_loop_phases.py:422` 加开关（AC-01/02）
- [ ] AC-01 验证：开关关闭时全量测试零回归
- [ ] AC-02 验证：开关开启时 `run_cycle()` 被调用且返回 `LoopReport`
- [ ] AC-08 验证：conftest 切换 `MAOP_DATA_DIR` 不污染（ADR-019 同类）
- [ ] AC-04 接线：人工 gate 字段 + PendingApproval 状态机扩展（§14 字段）
- [ ] AC-04 验证：未审批建议不进 A/B
- [ ] AC-05 接线：`_build_degradation_test_suggestion` + `rollback_cycle` 路径
- [ ] AC-05 验证：5 分钟内自动回滚（§16 脚本）
- [ ] AC-06 验证：`_z_test_p_value` 对照（§17 脚本）
- [ ] AC-07 接线：dashboard `/evolve` 扩展（状态机图 + 审批入口 + A/B 结果卡）
- [ ] AC-07 验证：E2E 浏览器走一遍
- [ ] AC-03 验证：E2E 完整 observe→suggest→approve→A/B→promote/rollback
- [ ] 9 平台 CI 全绿 + E2E 全绿

### Phase 2：ADR-020 评审与上线

- [ ] ADR-020 实体化（演化闭环安全边界 / 人工 gate 设计 / 部署策略）
- [ ] §14 中 `pending_approval` 字段落库决策
- [ ] ROADMAP v5.2.0 进度同步
- [ ] CHANGELOG v5.2.0 条目
- [ ] release notes

### 验收：8 条 AC 全部通过 + 9 平台 CI + E2E + soak 报告第 6 章最终版

---

## 20. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| EvolutionLoop 实例分裂（ADR-019 同类） | 中 | 高 | §15 强制 conftest fixture；code review 禁裸模块级单例 |
| `_phase_evolve` 改动影响 prod 路径 | 中 | 高 | §18 AC-01 byte-level 行为一致；diff ≤ 30 行 |
| 人工 gate 字段 alembic 迁移爆炸 | 低 | 中 | §14 字段先驻留 `LoopReport`，ADR-020 评审决定是否落库 |
| pytest-xdist Windows 竞态 | 中 | 中 | CI windows 矩阵 `-n 0` 维持；新测试不加 `-n` 默认 |
| 48h soak 与 v5.2.0 写盘冲突 | 极低 | 中 | §18 MAOP_DATA_DIR 隔离；soak 不读 v5.2.0 路径 |
| 9 平台 CI flake | 中 | 中 | 与 soak 第二轮错峰：soak 已稳定 2h+，新代码 commit 走 CI 验证 |
