# MAOP v6 产品蓝图：从“功能仓库”到“Run-first 控制台”

> 状态：M0 已实施（2026-10-20），M1–M3 待排期。
> 本文是产品定位、首公里契约与架构收敛的单一说明；版本计划见
> [ROADMAP.md](../ROADMAP.md)，接口细节见 [api-reference.md](./api-reference.md)。

## 1. 一句话定位

**MAOP：本地优先、自带 CLI 的多 Agent 任务控制台。提交一次任务，自动选 agent、执行、验证、留痕、算钱。**

目标用户：已经同时使用 3 个以上编码 CLI 的个人开发者与小团队，尤其是国产 CLI 用户。
非目标：通用 LLM 网关、agent 运行时、工作流 SaaS、把 SSO/LDAP/多租户当作个人版主卖点。

## 2. 首公里契约（不可回退）

一个陌生用户在干净机器上必须能完成：

1. `pip install maop-orchestrator`
2. `maop init`（写入 `~/.maop`：默认 config + 预构建 Dashboard）
3. `maop start`（个人版 + 回环地址自动 owner 模式，无登录墙）
4. 打开首页，看到就绪状态与“新建任务”
5. 提交任务，看到 job id、运行状态、输出/错误与退出码
6. 重启后历史仍在

验收门禁：`py/tests/test_home_bootstrap.py`、`test_owner_mode.py`、
`test_router_control.py`，以及 CI `wheel-install` 作业里的 first-run smoke。

## 3. 信息架构（目标态）

| 组 | 页面 | 说明 |
|---|---|---|
| 工作台 | 概览、任务历史 | 就绪检查 + 新建任务 + 最近运行 |
| 运行 | 运行、工作流 | 任务 Composer 与运行详情 |
| 能力 | 智能体、模型、工具/MCP | 探测到的 CLI、健康、能力矩阵 |
| 记忆 | 记忆、搜索、知识图谱 | 带 provenance 的检索 |
| 观测 | 成本、日志、健康 | 单次运行成本与证据 |
| 设置 | 常规、高级、关于 | 可编辑配置与校验 |

企业能力只在企业版出现；个人版不再展示指向 `/licenses` 的死链。

## 4. 核心对象

```
Task -> Run -> Step(Plan/Execute/Verify) -> Attempt -> Artifact/Evidence
                                             -> CostRecord
```

- Run 状态机：queued → planning → running → verifying → succeeded / failed / cancelled。
- 一个 Run 必须有：任务文本、选中 agent、每步输出、验证结果、成本、产物、可重试入口。
- `run_events` 追加写是事实源；UI 与统计都读投影。

## 5. 架构收敛

- 控制面 / 执行面 / 状态面分离；模块化单体的前提下按域切竖片：
  `runs`、agent 适配器、`memory`、`cost`、`admin`。
- router 不直接 import core；service 层承担编排；用 import-linter 或 AST 门禁守住。
- Adapter 契约：id、版本、探测、健康、能力、调用、输出解析、超时、环境；
  每个一等公民适配器都要过一致性测试。31 个条目不再等于 31 个承诺。
- 降低 API 面：当前 65 个顶层 router 模块（含子目录共 82 个 .py），
  文档口径 482 个 Personal 端点 / 554 个企业端点（2026-09-29）；产品 API 目标约 80 个，
  `/api/v1` 只保留一份别名。
- 数据：当前 129 个去重 `CREATE TABLE` 表名、多个按模块拆分的 SQLite 文件；
  目标单库 + 迁移，核心表 25–30 张。SQLite 启动时迁移并先备份。
- 权限：read / run / admin 三档；个人版本机 owner 仅限回环；
  远程绑定必须认证。企业端点在企业模块缺失时显式不注册，而不是被 ImportError 静默吞掉。

## 6. 测试与部署

- 单元测试保留；新增产品级 E2E：
  1. wheel first-run（干净 venv → init → start → 首页/overview 200 → 任务 Composer 可见）
  2. 权限矩阵（owner/guest/admin）
  3. Adapter 一致性
- route-matrix 不允许把 403/404 当通过。
- 三种分发形态同一套验收：wheel、Docker、源码。
- Docker 已有正确构建路径（[py/Dockerfile](../py/Dockerfile)）；wheel 由
  `[tool.hatch.build.targets.wheel.force-include]` 打入 config 与 dist。

## 7. 里程碑

| 里程碑 | 范围 | 验收 |
|---|---|---|
| M0（已完成） | wheel 资产、`maop init`、根目录解析、owner 模式、任务 Composer、job 详情与落盘历史、去伪数据、标题/版本/死链 | 干净 venv 安装 wheel 后 `/`、`/api/overview` 均 200；Composer 可提交中文任务；重启后作业与输出仍可查 |
| M1 | 运行详情（时间线/产物/重试/取消）、导航压到 5 组、设置可编辑 | 单次任务可复盘；关键页无 4xx |
| M2 | 6 个一等公民适配器 + 一致性测试、记忆 provenance、成本归因 | 适配器矩阵通过；单次任务成本可查 |
| M3 | 团队共享、RBAC、审计、PG/Redis（MAOS） | 多用户下权限与审计验收 |

## 8. 非目标与停止条件

- M0 首跑指标（安装→首个任务 < 10 分钟、成功率 ≥ 90%）未达标前，不增加新功能。
- 不把“代码行数/测试数/API 端点数”当作首页 KPI。
- 不在个人版 UI 里保留“即将上线”占位页与重复页面。
