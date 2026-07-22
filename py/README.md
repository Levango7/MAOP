# MAOP (Python service layer)

MAOP 的 Python 服务层包（`maop`）。项目整体说明见仓库根目录 `README.md`。

## 状态与持久化

- 消息队列唯一真源：`data/queue.db`（SQLite）
- 人工审批队列唯一真源：`data/human_queue.db`（SQLite）
- 熔断状态唯一真源：`data/maop.db` 的 `circuit_breaker_state` 表
- `human-queue.json` 为 Python 写入的只读镜像，PowerShell 引擎为遗留层

## 开发 / 测试

```bash
cd py
pip install -e .
pip install mmh3 pytest pytest-cov pydantic-settings
pytest
```

CI 定义见 `.github/workflows/ci.yml`（GitHub Actions：3 平台 × Python 3.12/3.13 矩阵，含 ruff + mypy lint、Docker 构建、pip-audit 安全扫描）。
