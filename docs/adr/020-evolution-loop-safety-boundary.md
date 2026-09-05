# ADR-020: 自演化闭环的安全边界（EvolutionLoop Safety Boundary）

**Status**: Proposed (Stub — 待 Phase 2 评审填充)
**Date**: 2026-09-06
**Decision Owner**: MAOP Architecture Team
**Related**: ADR-019 (Module Singleton Testability), [spec-v5.2.0-evolution-loop.md](../spec-v5.2.0-evolution-loop.md), PRD §4.2 (F2-01), HLD §3.1

## Context

`py/maop/core/evolution/` 已落地的 16 模块底座（`evolution_loop.py` 等）当前只被 `_phase_evolve` 调用 `EvolveEngine.analyze()` 产出建议，**建议不进入验证与部署**——"自演化"实质是断链的一半。

v5.2.0 F2-01 / M2.1 目标：把"评估 → 建议 → 审批 → A/B → 提升/回滚"完整闭环接入主循环，并通过 `MAOP_EVOLUTION_LOOP_ENABLED` 开关（默认 `false`）控制。

**本 ADR 待决问题**（Phase 2 必填）：

1. **人工 gate 的安全边界**：
   - 谁有权审批？仅 admin 还是分级 RBAC？
   - 审批窗口多长？过期后建议回滚还是重提？
   - 审批变更的审计日志写到哪？是否复用 `py/maop/core/audit/`？

2. **A/B 流量承载的隔离**：
   - 单实例部署时，A/B 流量如何在 prompt_version 切换期间不污染 baseline？
   - 是否需要 read-only 镜像挂载（`data/prompt_versions/`）？
   - 与 `py/maop/core/evolution/ab_test.py` 现有 `create_experiment / assign / record / evaluate` 接口契约是否变更？

3. **自动回滚的安全边界**：
   - 何时自动触发？仅 `validation_improved == False`？还是更激进（latency p99 退化 ≥ 20%）？
   - 回滚到上一版本还是回到指定 baseline？
   - 是否需要"回滚后冻结"机制，避免回滚的版本再次被自动提升？

4. **§14 字段持久化决策**：
   - `pending_approval / approval_state / approved_by / approved_at` 字段是否落库？
   - 若落库：alembic 迁移 vs in-memory（影响 spec §6"不新增表结构"约束）
   - 跨进程共享审批状态的需求（如果未来多实例 HA 部署）

5. **部署策略**：
   - spec §3 已锁定"不触碰生产部署"——MVP 阶段"提升为 current 版本"≠"部署到生产"
   - 但 `prompt_version.rollback()` 会修改生产 DB 中的 prompt 元数据——这条边界在哪？

## Decision（待 Phase 2 评审）

**Phase 1 临时决策**（本 stub 立）：

- `MAOP_EVOLUTION_LOOP_ENABLED` 默认 `false`（spec AC-01）
- `_phase_evolve` 接线点改动粒度 ≤ 30 行（spec §18）
- 人工 gate 字段**先驻留 `LoopReport` 内存**，不立即走 alembic（spec §14）
- A/B 沿用 `ab_test.py` 现有接口，不新增
- 自动回滚条件：`validation_improved == False` AND `snapshot_id != ""`
- 部署边界：`prompt_version.rollback()` 调用 = 提升为 current，不部署到生产；自动部署到生产 = 明确 out-of-scope（spec §3）

**Phase 2 必填内容**（TODO）：

- [ ] 5 个待决问题逐项落地决策
- [ ] §14 字段落库决策与 alembic 迁移计划
- [ ] 与 ADR-015（Distributed HA Redis Lease）的关系：MVP 阶段明确单实例，不引入分布式 lease
- [ ] 风险评估矩阵：每项决策的「失效模式 → 用户感知 → 缓解」
- [ ] 可观测指标定义：闭环运行期间的 SLO（建议产出间隔 / A/B 收敛时长 / 人工 gate 等待时间）
- [ ] §18 soak 兼容验证脚本：开关关闭时 `_phase_evolve` 行为与现状 byte-level 一致

## Consequences

### 正面（Phase 1 即可获益）

- 强制 `MAOP_EVOLUTION_LOOP_ENABLED` 默认 false → 生产路径零回归（spec AC-01）
- §14 字段驻留内存 → 不阻塞 MVP 推进，alembic 迁移留待 Phase 2
- 改动粒度 ≤ 30 行 → review 友好，回归风险低

### 负面（已知）

- Phase 1 阶段人工 gate 无持久化 → 进程重启后审批状态丢失（已知 trade-off，Phase 2 解决）
- 单实例部署 → 跨进程审批/回滚不一致风险（与 ADR-015 协调）

### 不变的

- 业务代码（`evolution_loop.py` / `ab_test.py` / `prompt_version.py`）**不重写**
- 仅在 `_phase_evolve` 接线点和 `LoopReport` 字段层扩展
- spec §2 MVP 范围锁定

## Follow-up Backlog

- [ ] Phase 2 评审：5 个待决问题逐项填充
- [ ] §14 字段落库决策 + alembic 迁移
- [ ] 与 ADR-015 协调：HA 部署下的人工 gate 跨实例一致性
- [ ] 可观测 SLO 定义
- [ ] §18 soak 兼容验证脚本

## References

- [spec-v5.2.0-evolution-loop.md](../spec-v5.2.0-evolution-loop.md) §5 接线点、§14 状态机、§19 实施 checklist
- [ADR-019: 模块级单例可测性](019-module-singleton-testability.md) — EvolutionLoop 单例治理基础
- [PRD §4.2 F2-01](../prd-three-phase-roadmap.md)
- [HLD §3.1](../hld-three-phase-roadmap.md)
