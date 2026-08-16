# 工具白名单切 enforce — 部署检查清单

> 适用：MAOP 部署环境从 `audit` 模式切换到 `enforce`（工具白名单三阶段之阶段三）
> 前置：T1（ToolPolicy 双路径拦截）已上线，阶段二 deny 高危已随仓库发布
> 关联：`py/scripts/export_tool_whitelist.py`、`config/tool_whitelist.yaml`

---

## 背景

- `audit` 模式：未命中 deny/allow 的工具放行 + warning 日志（阶段一）
- `enforce` 模式：未命中 allow 的工具**拒绝执行**，返回 `ToolCallResult(ok=False, error="tool not allowed: ...")`，**不抛异常**（agent 侧可能静默降级）
- deny 优先于 allow；配置缺失/损坏时 fail-open 到 audit（此时 enforce 未生效，需日志盯防）

## 切换五步

### 第 1 步：导出初始 allow 清单

在**部署环境**（tools 表已初始化）执行：

```bash
cd <MAOP 仓库根>
python py/scripts/export_tool_whitelist.py --review   # 先看高危清单
python py/scripts/export_tool_whitelist.py            # 生成 config/tool_whitelist.generated.yaml
```

### 第 2 步：人工评审

- 打开 `config/tool_whitelist.generated.yaml`：
  - `allow` 段：确认包含全部需要使用的工具 id
  - 被排除的高危工具（`# !! 高危` 注释）：逐条决策——确认工具确实不合法（保持排除）或移除对应 deny 规则后放行
- 检查是否有**合法但高危**的工具（如刻意用于清理的 `rm` 封装）：此类应走"移除 deny 规则 + 单独评审"流程，不应靠 allow 放行（deny 优先，allow 无效）

### 第 3 步：合并并切 enforce

```bash
# 备份原配置
cp config/tool_whitelist.yaml config/tool_whitelist.yaml.bak
# 合并 allow 段到 config/tool_whitelist.yaml，并把 mode 改为 enforce
```

`config/tool_whitelist.yaml` 最终形态：

```yaml
mode: enforce
allow:
  - id: "lint"
  - id: "build"
  ...
deny:
  - pattern: "rm*"
  ...
```

### 第 4 步：灰度验证（必须）

1. **单实例灰度**：仅一个实例设 `MAOP_TOOL_POLICY_MODE=enforce` 启动（其余保持 audit），观察其日志：
   ```bash
   MAOP_TOOL_POLICY_MODE=enforce python -m maop.dashboard.server
   ```
2. **盯防误拦**：grep 日志中的 `tool not allowed`——enforce 拒绝是 `ok=False` 不抛异常，**必须主动查日志**，agent 侧不会报错：
   ```bash
   grep "tool not allowed" <logfile>
   ```
3. **验证 enforce 真实生效**（防 fail-open 掩盖）：构造一次拒绝场景，确认返回 `ok=False`：
   ```python
   from maop.core.agent.tools.tool_manager import ToolManager
   mgr = ToolManager(root_dir=".")
   # 注册一个不在 allow 的临时工具
   mgr.register("__probe__", command="echo probe")
   r = mgr.call("__probe__")
   assert not r.ok and "tool not allowed" in r.error  # enforce 生效
   mgr.remove("__probe__")  # 清理
   ```
4. **有误拦** → 补充 allow 后重启实例，重复第 2-3 步；确认无误后全量切换。

### 第 5 步：全量切换 + 新工具注册流程

- 全量实例切 enforce（改 yaml 或各实例 env）
- **新工具注册必须同步更新 allow**（文档化流程）：
  ```
  注册新工具 → 运行 export_tool_whitelist.py 重新生成 → 评审 → 合并 allow → 重启
  ```
- 可选：保留一个 `MAOP_TOOL_POLICY_MODE=audit` 的"审计哨兵"实例，持续收集新调用，防止漏注册

## 回滚

- 单实例回滚：`unset MAOP_TOOL_POLICY_MODE`（或改回 `audit`）重启
- 全量回滚：`cp config/tool_whitelist.yaml.bak config/tool_whitelist.yaml` + 重启（备份保留在第 3 步）

## 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| enforce 下工具全部被拒 | 配置存在 + mode=enforce + allow 空（fail-open 不触发，是 fail-closed） | 回到第 1 步生成 allow |
| 日志显示 fail-open 警告 | 配置文件缺失/损坏 | 检查 yaml 语法，恢复备份 |
| 单个工具被误拦 | allow 缺该 id | 补 allow + 重启（第 4 步流程） |
| 高危工具被排除但确实需要 | deny 优先于 allow | 评审后移除对应 deny 规则（单独决策，不绕过） |
