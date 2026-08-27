# ADR-018: 双 Wheel 授权模型（D2 决策）

- **状态**: Accepted
- **日期**: 2026-08-27
- **决策者**: 用户拍板
- **关联**: ADR-016（双版架构）, 交付方案 v2.1

## 背景

MAOP 个人版（开源 MIT）和 MAOS 企业版（商业 license）需要清晰的授权边界。

此前的 `maop/enterprise/__init__.py` docstring 声称"随主包单 wheel 发布"，但 `pyproject.toml` 定义的是独立包 `maop-enterprise`——两者矛盾。

## 决策

采用**双 wheel 模式**：
- `maop`（个人版 wheel）：PyPI 公开发布，MIT 许可
- `maop-enterprise`（企业版 wheel）：私有仓库/商业渠道发布，Commercial 许可

主包通过延迟导入 `maop.enterprise.*` 使用企业功能。未安装 `maop-enterprise` 时，企业路由优雅降级为 404。

## 理由

1. **商业 license 独立**：企业版代码不混入开源 wheel，避免 license 污染
2. **私有仓库可控**：`maop-enterprise` 不上 PyPI，通过私有渠道分发
3. **发版解耦**：个人版和企业版可独立发版，不互相阻塞
4. **安全边界清晰**：`pip install maop-orchestrator` 只装个人版，企业功能需额外安装 `maop-enterprise` + 有效 license

## 影响

- MAOS 仓库需补齐 `maop/__init__.py`（namespace 包兼容 hatchling）
- MAOP 仓库需加契约测试（`test_enterprise_contract.py`），防止双仓 API 漂移
- 客户安装流程：`pip install maop-orchestrator` + `pip install maop-enterprise` + 配置 license key