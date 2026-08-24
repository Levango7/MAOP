# Archived Documentation

本目录存放项目历史文档，包括审查报告、计划文档、修复方案和合并计划。
这些文档仅作参考和追溯之用，**不应作为当前项目状态或架构决策的依据**。

## 当前权威文档

- `../adr/` — 架构决策记录（ADR 001-017）
- `../deployment.md` — 部署指南
- `../enterprise/license-issuance-guide.md` — 许可证签发指南
- `../../README.md` — 项目概述
- `../../CHANGELOG.md` — 变更日志
- `../../ROADMAP.md` — 路线图

## 目录结构

| 子目录 | 用途 |
|--------|------|
| `audits/` | 历史审查报告、综合分析、版本评估报告 |
| `plans/` | 历史计划文档、路线图、重构计划、执行清单 |
| `fixes/` | 修复方案、修复执行计划、修复计划 |
| `merge-plans/` | 合并计划、合并可行性报告、补丁测试合并方案 |

## audits/ — 历史审查报告

| 文件 | 日期 | 大小 | 说明 | 状态 |
|------|------|------|------|------|
| MAOP_全局审查报告_v5.1.0_20260814.md | 2026-08-14 | 15.2KB | v5.1.0 全局审查报告 | 历史快照 |
| comprehensive-review-maop-2026-07-20.md | 2026-07-20 | 15.5KB | 2026-07-20 MAOP 综合审查 | 已过时 |
| comprehensive-audit-report.md | 2026-07-xx | 12.6KB | 综合审计报告（已被 v2 取代） | 已过时 |
| MAOP_COMPREHENSIVE_ANALYSIS.md | 2026-07-xx | 41.6KB | MAOP 综合分析 | 已过时 |
| MAOP_v4_FINAL_REPORT.md | 2026-07-xx | 42.0KB | MAOP v4 最终报告 | 已过时 |
| MAOP_v4_EVALUATION.md | 2026-07-xx | 61.5KB | MAOP v4 评估报告 | 已过时 |
| MAOP_audit_report.md | 2026-07-xx | 14.7KB | MAOP 审计报告 | 已过时 |
| architecture-review-20260714.md | 2026-07-14 | 7.4KB | 2026-07-14 架构审查报告 | 已过时 |
| architecture-review-20260714-delta.md | 2026-07-14 | 6.0KB | 2026-07-14 架构审查增量报告 | 已过时 |
| frontend-migration-assessment.md | 2026-07-xx | 4.3KB | 前端迁移评估（Vue3 SPA 已实现） | 已过时 |
| arch-debt-three-plans-review.md | 2026-08-15 | 6.5KB | 架构债务三方案评审报告 | 历史快照 |
| design-system-legacy.md | 2026-08-17 | 3.5KB | 旧版 JS 仪表盘设计系统 v4.1（已被 Vue 3 取代） | 已过时 |
| project-structure-analysis.md | 2026-08-13 | 49.7KB | 项目结构分析报告 | 历史快照 |
| v5.0.1-review.md | 2026-08-13 | 3.5KB | v5.0.1 版本审查报告 | 历史快照 |

## plans/ — 历史计划文档

| 文件 | 日期 | 大小 | 说明 | 状态 |
|------|------|------|------|------|
| routing-refactor-plan.md | 2026-07-xx | 17.2KB | 路由重构计划 | 已完成 |
| followup-three-fixes-plan.md | 2026-08-14 | 7.4KB | 三项后续修复计划 | 已完成 |
| tool-whitelist-enforce-plan.md | 2026-08-15 | 9.2KB | 工具白名单强制执行计划 | 已完成 |
| tool-whitelist-enforce-checklist.md | 2026-08-15 | 4.0KB | 工具白名单强制检查清单 | 已完成 |
| tool-whitelist-enforce-drill.md | 2026-08-15 | 3.4KB | 工具白名单强制演练记录 | 已完成 |

> **注**：`prd-three-phase-roadmap.md` 与 `hld-three-phase-roadmap.md` 现位于 `docs/` 根目录作为权威路线图文档，不再属于归档范围。

## fixes/ — 修复方案文档

| 文件 | 日期 | 大小 | 说明 | 状态 |
|------|------|------|------|------|
| p2p3-fix-execution-plan.md | 2026-08-07 | 46.8KB | P2-P3 修复执行计划（M3/M4/M5/M8） | 已完成 |
| architecture-debt-remediation.md | 2026-08-14 | 6.3KB | 架构债务修复方案 | 已完成 |
| REMEDIATION_PLAN.md | 2026-07-xx | 2.6KB | 修复计划（已迁移到 issue tracker） | 已过时 |

## merge-plans/ — 合并计划文档

| 文件 | 日期 | 大小 | 说明 | 状态 |
|------|------|------|------|------|
| h2-merge-execution-plan.md | 2026-08-07 | 33.9KB | H2 合并执行计划（4 Phase 顺序） | 已完成 |
| h2-merge-feasibility-check-report.md | 2026-08-07 | 31.6KB | H2 合并可行性核对报告（26 项） | 已完成 |
| h2-patch-test-merge-plan.md | 2026-08-07 | 19.0KB | H2 补丁测试合并计划 | 已完成 |

## 维护说明

- 本目录文档**仅供历史参考**，不反映当前项目状态
- 如需了解当前架构决策，请查阅 `../adr/`
- 如需了解当前路线图，请查阅 `../../ROADMAP.md`
- 如需了解变更历史，请查阅 `../../CHANGELOG.md`
- 新增归档文档时，请按类别放入对应子目录并更新本索引
