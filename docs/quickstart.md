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
实测可用的第三方 CLI 包括 `codex`、`gemini`、`copilot`、`qwen`、`pi`、`opencode`、`cline` 等。

> 目标 CLI 必须**已在本机完成登录**、且能非交互运行。否则 `maop run` 会停在等待登录，
> 且当前版本不打印执行进度（见文末「已知行为」）。

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
自定义目录用 `maop init --home <dir>`，之后所有命令都要带同一个 `MAOP_HOME`。

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

`--agent` 必须是 `config/agents.yaml` 里在册、且本机已登录的 CLI。
不在册的名字（例如 `claude`）当前版本会回退到兜底 agent 并打印 WARN，
`run_events` 里 `agent=` 与实际执行者可能不一致。

> 当前版本 `maop run` 在任务执行期间不打印进度，只在结束时输出结果。
> 任务较大时请耐心等待，或用 `maop status` 观察。

## 6. 重启后历史仍在

```bash
maop stop
maop start
```

再打开首页，任务历史应仍可见（数据在 `~/.maop/data/maop.db`）。

## 常见问题

### Q: `maop health` 显示 `memory: degraded`，是不是装坏了？
**A:** 不是。`memory.db` 在首次使用记忆功能时才创建（`py/maop/deploy.py` 的
message 明确写 "will be created on first use"）。但当前版本 `maop health` 把任何
非 `healthy` 组件都判为失败（`py/maop/cli.py` 的 `all_healthy` 判定），所以全新安装
必然返回 FAIL。判断安装是否成功请看：`[+] database`、`[+] config` 均为 healthy，
以及 `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9079/healthz` 是否返回 200。

### Q: `ModuleNotFoundError: No module named 'maop'`
**A:** 确认用的是装包的那个 Python：`python -m pip install maop-orchestrator`，
或直接调用 `maop` 命令（入口在虚拟环境的 `Scripts/` 或 `bin/` 下）。

### Q: 前端页面空白或 API 请求失败
**A:** 确认 `maop start` 正在运行（默认 9079）；自定义端口时前端与探针需一致。

### Q: Agent 执行时报 LLM API 错误
**A:** 内置 LLM 的凭据在 Dashboard 的密钥/凭据管理页配置（Fernet 加密存储，见
`py/maop/core/api_key_vault.py`），**不通过 `.env` 的 `MAOP_LLM_API_KEY`**（该变量不存在）。
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

## 已知行为（2026-11-07 在 v5.6.1 干净环境实测）

| 现象 | 原因 | 是否影响可用性 |
|---|---|---|
| `maop health` 全新安装返回 FAIL | `memory.db` 未创建被判 degraded，CLI 又把 degraded 当失败 | 不影响：`healthz` 仍为 200 |
| `maop run` 执行期间无进度输出 | 未接流式进度 | 长任务难以判断是否卡住 |
| `--agent` 指定不在册 CLI 时静默回退 | failover 机制 | `run_events` 的 agent 标注可能与实际执行者不一致 |

## 下一步

- [用户指南](user-guide.md)
- [配置说明](configuration.md)
- [部署指南](deployment.md)
- [架构决策记录](adr/)
- [变更日志](../CHANGELOG.md)
