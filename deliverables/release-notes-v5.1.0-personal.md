# Release Notes: v5.1.0-personal

## MAOP v5.1.0 个人版交付

> 基于 v5.1.0 + 140 项安全审核修复，补齐 3 项 P0 门禁后交付。
> 双仓库方案：个人版（MAOP，MIT 开源）+ 企业版（MAOS，商业 License）。

## 新增

- **P1-1 成本兜底护栏**：新增 `PersonalCostGuard`，实现软/硬两档熔断。配置项 `MAOP_PERSONAL_COST_CAP`（全局累计花费阈值 USD）+ `MAOP_PERSONAL_COST_HARD`（硬熔断开关）。软熔断：达到阈值 → 告警 + 拒绝新 LLM 调用。硬熔断：达到阈值 → 中断运行中任务。
- **P1-2 edition 切换提示**：前端切换 enterprise 失败时显式提示"需 MAOS 商业包 + 有效 License"。后端无 license 切换 enterprise 返回 403。
- **P1-3 分布式边界声明**：`maop worker start` 在个人版下提示"分布式 worker 是企业版特性"并退出 1。
- **P1-4 长稳测试脚本**：新建 48h 多指标压测脚本（内存 RSS / 文件句柄数 / 连接池占用 / CPU 使用率）。1h 验证 PASS（116 样本，无内存泄漏）。

## 变更

- `settings.py` 新增 `personal_cost_cap` + `personal_cost_hard` 配置项
- `maop_execute.py` 集成 `PersonalCostGuard.check_new_call()` 前置检查
- `admin.py` edition 切换逻辑：无 license 切换 enterprise 从 200+degraded 改为 403+error
- **包名变更**：PyPI 包名从 `maop` 改为 `maop-orchestrator`（`maop` 被 PyPI 上他人占用）。`import maop` 不变，仅 `pip install maop-orchestrator`。
- `pyyaml` 版本约束从 `==6.0.2` 放宽为 `>=6.0.2,<7.0.0`

## 修复

- 修复 `soak_test.py` ruff 7 个代码风格问题
- 修复 `maop.__version__` 在双 wheel 场景下的兼容性（MAOP + MAOS `__init__.py` 双向兼容）

## 测试

- 后端：7924+ passed, 58 skipped, 0 failed（覆盖率 82%）
- 前端：357 passed
- 代码质量：ruff 0 error + mypy 0 error + ESLint 0 error
- 长稳测试：1h 验证通过（memory slope=-12.36 MB/h, handles slope=-1.09/h, 无泄漏）

## 安装

```bash
# 从 GitHub Release 安装
pip install https://github.com/Levango7/MAOP/releases/download/v5.1.0-personal/maop_orchestrator-5.1.0-py3-none-any.whl

# 或下载 wheel 文件后本地安装
pip install maop_orchestrator-5.1.0-py3-none-any.whl
```

## 产物

- `maop_orchestrator-5.1.0-py3-none-any.whl` (1.2 MB) — wheel 包
- `maop_orchestrator-5.1.0.tar.gz` (1.7 MB) — 源码包

## 企业版

企业版 MAOS（`maop-enterprise` 包）在私有仓库 `Levango7/MAOS`，需商业 License。
安装：`pip install maop-orchestrator` + `pip install maop-enterprise` + 配置 license key。