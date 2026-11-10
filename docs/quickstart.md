# MAOP 5 分钟快速入门

> 目标：在干净机器上装好、起服务、跑出第一个任务。
> 权威契约见 [v6 产品蓝图](product-v6-blueprint.md) 第 2 节「首公里契约（不可回退）」。

## 前置要求

| 组件 | 最低版本 | 检查命令 |
|------|---------|----------|
| Python | >= 3.10 | `python --version` |
| 至少一个外部 CLI agent | — | 见下 |

**MAOP 是编排层，不自带 agent 运行时。** `maop run` 驱动的是 `config/agents.yaml`
里已注册的 CLI；一个都没装时，派发返回 `exit_code=-2 (Command not found)`，任务不会执行。

### 开工前自检三步

MAOP **不校验**目标 CLI 的登录状态、配额与非交互参数。CLI 未就绪时不会快速失败，
而是长时间无输出（见文末「已知行为」）。所以先自己确认三件事：

```bash
copilot --version                                  # 1. 装了吗（换成你要用的 CLI）
grep -n "^  copilot:" ~/.maop/config/agents.yaml   # 2. 在册吗
copilot -p "Reply with exactly: OK"                # 3. 能非交互跑通吗
```

三步都通过，`maop run` 才可能成功。第 3 步是关键：各 CLI 的非交互参数不同
（`copilot` 用 `-p`、`codex` 用 `exec`、`pi` 用 `--print`），`agents.yaml` 的
`cli_args` 已按各 CLI 写好；若 CLI 升级后改了参数，派发会退化成交互模式并挂起。

> 自定义 `--home` 时，`agents.yaml` 在 `<home>/config/agents.yaml`。

## 1. 安装（约 1 分钟）

```bash
pip install maop-orchestrator
```

验证：`maop --version` 应输出 `5.6.1`（或更高）。

## 2. 初始化（约 1 分钟）

```bash
maop init
```

写入 `~/.maop`：默认配置 6 个文件 + 预构建 Dashboard（273 个静态资产）。实测约 72 秒。
自定义目录用 `maop init --home <dir>`。wheel 安装后续命令带 `MAOP_HOME=<dir>` 即可；
但从源码 / `pip install -e` 运行时 `MAOP_HOME` 会被源码树覆盖（数据仍写进 `<仓库>/data/`），
必须改用 `MAOP_ROOT_DIR=<dir>`。

## 3. 启动（约 10 秒）

```bash
maop start
```

- 默认监听 `http://127.0.0.1:9079`，自定义端口用 `maop start --port N`
- 个人版 + 回环地址自动进入 **owner 模式**（仅本机免登录）；远程监听仍要求认证
- 数据、日志、配置都在 `~/.maop`，不写入 site-packages

## 4. 打开首页

浏览器访问 `http://127.0.0.1:9079`。首页应显示就绪状态与「新建任务」。

## 5. 提交第一个任务

```bash
maop run --task "把 README 里的错别字列出来" --agent codex
```

成功时末行输出形如：

```
[OK] run 完成 (23556.5ms)
```

`--agent` 必须是 `config/agents.yaml` 里在册、且本机已登录的 CLI。
不在册的名字（例如 `claude`）当前版本会回退到兜底 agent 并打印 WARN。

> 当前版本 `maop run` 在任务执行期间不打印进度，只在结束时输出结果。
> `maop status` 只报告服务是否运行，**不显示任务进度**。

## 6. 重启后历史仍在

```bash
maop stop
maop start
```

再打开首页，任务历史应仍可见（数据在 `~/.maop/data/maop.db`）。

## 常见问题

### Q: `maop health` 列出多项 `degraded`，是不是装坏了？
**A:** 不是。全新安装后 `data/maop.db` 与 `data/memory.db` 尚未创建，
`health_check()` 会把 `database`、`memory` 标为 `degraded`（`py/maop/deploy.py`
对 `memory.db` 的提示是 "will be created on first use"）。`maop health` 只把
`unhealthy` 判为失败，`degraded` 仅提示、不改变退出码（见 `py/maop/cli.py` 的
`cmd_health`），所以输出 `[OK] health 完成 — N 项降级` 属正常。别用 `health`
的退出码判断服务是否可用：`maop start` 起来后，用
`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9079/api/health`
确认返回 200，再按第 5 步跑一次 `maop run`。

### Q: `ModuleNotFoundError: No module named 'maop'`
**A:** 确认用的是装包的那个 Python：`python -m pip install maop-orchestrator`，
或直接调用 `maop` 命令（入口在虚拟环境的 `Scripts/` 或 `bin/` 下）。

### Q: 前端页面空白或 API 请求失败
**A:** 确认 `maop start` 正在运行（默认 9079）；自定义端口时前端与探针需一致。

### Q: Agent 执行时报 LLM API 错误
**A:** 内置 LLM 的凭据在 Dashboard 的密钥/凭据管理页配置（Fernet 加密存储，见
`py/maop/core/security/api_key_vault.py`），**不通过 `.env` 的 `MAOP_LLM_API_KEY`**（该变量不存在）。
只用外部 CLI agent 执行任务时，不需要配置内置 LLM 凭据。

### Q: 企业版功能（SSO/RBAC/审计）显示 404
**A:** 个人版默认 `personal` edition。企业版需设置 `MAOP_EDITION=enterprise` 并配置有效
License，详见 [ADR-016](adr/016-dual-edition-architecture.md)。

## 开发者：从源码运行

> 这不是首跑路径。首次使用请走上面 1–6 步。

```bash
git clone <MAOP_REPO_URL>
cd MAOP
pip install -e py/
cd dashboard-enterprise && npm install && npm run build && cd ..
maop start --port 9079
```

前端热更新开发：后端 `cd py && python -m maop.dashboard.server`；前端
`cd dashboard-enterprise && npm run dev`（Vite 默认 5173，需代理到 9079）。

## 已知行为（2026-11-08 在 v5.6.1 干净环境实测，Windows；`health` 相关结论已按当前代码复核）

| 现象 | 原因 | 影响 |
|---|---|---|
| `maop health` 全新安装列出多项 `degraded` | `maop.db` / `memory.db` 尚未创建 | 仅提示，退出码仍为 0；用 `/api/health` 是否为 200 判断服务可用 |
| `maop run` 执行期间无进度输出 | 未接流式进度 | 长任务难以判断是否卡住 |
| 目标 CLI 未登录或配额耗尽时**长时间无输出、不报错** | 无登录态预检，也不区分「等待中」与「已失败」 | 首跑最易踩：看起来像卡死 |
| `--agent` 指定不在册 CLI 时静默回退 | failover 机制 | `run_events` 的 agent 标注可能与实际执行者不一致 |
| 日志打印 `agent=codex` 但实际执行的是 `--agent` 指定的 CLI | 路由默认值先被记录，`--agent` 覆盖发生在其后 | 读日志会误判实际执行者 |

## 下一步

- [用户指南](user-guide.md)
- [配置说明](configuration.md)
- [部署指南](deployment.md)
- [架构决策记录](adr/)
- [变更日志](../CHANGELOG.md)
