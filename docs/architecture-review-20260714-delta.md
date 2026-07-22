# MAOP 架构再审阅（增量）— 2026-07-14 晚

> 触发：用户于 2026-07-14 当天又改动了 `dashboard / data / docs / py` 多个目录（时间戳 00:14–01:59）。
> 本文是对 `architecture-review-20260714.md` 的**增量更正与状态更新**，结论以本文为准。

## 一、相比上午评审，项目发生了什么

| 变化 | 上午评审时 | 现在 | 性质 |
|------|-----------|------|------|
| 测试套件 | 几乎无 | **34 个 test_*.py**（含 phase4–7、new_modules、missing_modules、enhancements、migration） | ✅ 重大质量投入 |
| migration.py | 空壳 | **已实装**（Migration / MigrationManager / CLI，含 checksum 校验） | ✅ 修复 |
| 容器化 | 无 | 新增 `docker-compose.yml` + `Dockerfile`（dashboard 容器化） | ✅ 新增 |
| 安全红线 | 待确认 | `REMEDIATION_PLAN.md` 标记 Phase 0（命令注入/路径穿越/JSON 加固）**全部 ✅** | ✅ 修复 |
| 备份清理 | 散落 | 29 个备份文件归入 `dashboard/.backup/` | ✅ 清理 |
| 项目描述 | "Python 主引擎" | pyproject 改为 **"PS engine + Python service layer"** | ✅ 诚实重定位 |
| README | 反映 PS 架构 | **未更新**，仍称 `server-v2.ps1` 为 canonical（8080） | ⚠️ 文档漂移（新隐患） |

## 二、上午 4 个 P0 的重新核验（逐条，附证据）

| # | 问题 | 上午结论 | 晚 review 结论 | 证据 |
|---|------|---------|---------------|------|
| P0-1 | `pydantic-settings` 未进依赖 | 未修 | **仍未修** | `settings.py:19` import 它；`pyproject.toml` 依赖仅 pyyaml/pydantic/httpx/uvicorn；`requirements.txt` 仅 fastapi/uvicorn；托管 Python 3.13.12 `import pydantic_settings` → ModuleNotFoundError；`from maop.config.settings import PEVSettings` → 失败 |
| P0-2 | data_bridge PS 回退 | 残留死代码 | **更严重：默认激活** | `data_bridge.py:59` `fallback_to_ps: bool = True`（默认开）；`:578` `_invoke_ps_fallback` 仍 `powershell -NoProfile -File`；`:490` 状态接口暴露 `ps_bridge_active`。即 Python 层**无法脱离 PS 独立运行** |
| P0-3 | DB 双写 / 状态分裂 | 熔断 vs 队列不一致 | **确认双状态库并存** | Python 读 `maop.db/memory.db/queue.db`（sqlite3，`:71-91`）；PS 写 `memory.json/prompts.json/tools.json/vectors.json/human-queue.json`。`queue.db` 在 data/ 中**不存在**（会被自动建空），与 `human-queue.json` 分裂 → 队列状态分裂脑 |
| P0-4 | 无锁文件 | 未修 | **仍未修** | `py/` 下无 `requirements.lock` / `poetry.lock` / `Pipfile.lock`；依赖全浮动版本 |

> **更正说明**：上午我把 P0-2 判断为"残留死代码、可删"。重新读 `data_bridge.py:59` 后确认它是**默认开启的运行时分支**，不是死代码。这反而印证了核心论断——当前系统本质是"套 Python 壳、靠 PS 回退缝活的双轨系统"，比上午判断更依赖 PS。

## 三、新增隐患（上午未出现 / 未强调）

1. **⚠️ README 与实现严重脱节（最高优先级文档问题）**
   - README 结构块**完全没提 `py/` 包**；仍写 `server-v2.ps1` 为 canonical HTTP server（port 8080）。
   - 现实：`py/maop/dashboard/server.py`（FastAPI）才是 9078 端口真服务，`server.py:2` 自述 "FastAPI replacement for dashboard/server-v2.ps1"。
   - `REMEDIATION_PLAN.md 1.1.2` 称已"删除 dashboard/server.ps1"，README 仍列其为 DEPRECATED——两文档互相矛盾。
   - 风险：任何新人/后续 agent 按 README 理解架构会完全误判。

2. **⚠️ `config/routing.yaml` 仍不存在**
   - `config/` 仅 `agents.yaml`(Jul 8) + `rules.yaml`(Jul 12)。`hot_reload` 监听的路由文件是幽灵文件（上午已提，至今未建）。

3. **⚠️ 队列层分裂脑（P0-3 的具体化）**
   - Python `queue.db`（自动建空）vs PS `human-queue.json`：同一条队列状态两个真相源，无同步机制。

4. **⚠️ YAML 解析 PS 依赖未消（REMEDIATION_PLAN 2.1 标 TODO）**
   - `dag-engine.ps1` / `validate-config.ps1` → Python bridge 仍"待后续"，PS 仍掌握部分配置解析路径。

## 四、整体判断（更新）

**架构设计：8/10（不变）** —— 配置驱动 + 闭环 + 弹性基础设施层（熔断/事件总线/缓存三防护/鉴权/TLS/沙箱）依然扎实，且补了 migration、测试、容器化三块短板。

**工程完成度：6/10（较上午 5/10 提升 1 分）**
- 加分项：安全红线全清、测试套件成型、migration 实装、Docker 化、诚实重定位。
- 扣分项：4 个 P0 一个没动；且发现 PS 回退是**默认开**，双轨依赖比上午判断更深；README 文档漂移成为新的 onboarding 风险。

**一句话**：你做了大量"补质量地基"的工作（测试/迁移/容器/安全），方向全对；但**迁移收口那 4 步（补依赖→拆 PS 默认回退→统一 SQLite 状态源→锁版本）一个没落地**，而 README 还停留在旧世界——对外承诺与代码现实之间的裂缝反而扩大了。

## 五、建议的下一步（按性价比排序）

| 优先级 | 动作 | 代价 | 收益 |
|--------|------|------|------|
| P0 | `pyproject.toml` 加 `pydantic-settings`；`requirements.txt` 同步；生成锁文件 | 5 分钟 | 干净环境可装、可跑 |
| P0 | `data_bridge` 默认 `fallback_to_ps=False`，并列出仍依赖 PS 的端点清单逐一 Python 化 | 中 | Python 层可独立运行，剥离 PS 依赖 |
| P0 | 选 SQLite 或 JSON 单一状态源；若选 SQLite，删 `human-queue.json` 等 JSON 镜像并加迁移 | 中 | 消除分裂脑 |
| P1 | **重写 README**：反映 `py/` 包、FastAPI 9078、PS 作为兼容层；与 REMEDIATION_PLAN 对齐 | 30 分钟 | 消除文档漂移，避免后续误判 |
| P1 | 建 `config/routing.yaml` 或改 `hot_reload` 监听真实文件 | 10 分钟 | 消除幽灵监听 |
| P2 | 在 CI 跑 `pytest`（34 文件）；确认绿灯 | 低 | 锁住质量成果 |

> 注：测试套件是否已全绿未经本环境验证（需完整 venv + 依赖，用户自行迁移中，未越界安装）。建议接入 CI 固化。
