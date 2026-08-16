# 工具白名单切 enforce — 本地演练报告（已验证）

> 日期：2026-08-15
> 目的：在隔离环境跑通「导出 allow → 人工评审 → 切 enforce → 三类拦截行为」全链路，为生产操作提供已验证依据
> 环境：隔离临时目录（MAOP_DATA_DIR），未污染仓库 `data/maop.db` 与 `config/tool_whitelist.yaml`

---

## 演练环境与工具

| 工具 id | command | 角色 |
|---------|---------|------|
| `lint` | `echo lint-ok` | 合法工具 → 应放行 |
| `cleanup` | `rm -rf /tmp/maop_drill_cleanup` | 高危命令 → 应被 deny 拦截 |
| `probe` | `echo probe-ok` | 评审时被裁掉 → 应被 enforce 拒绝 |

## 全链路结果（逐环验证）

### 1. 导出脚本生成 allow（阶段二）

`export_tool_whitelist.py` 从 DB 导出，命中 deny 的 `cleanup` **自动排除并标注**：

```yaml
allow:
  # !! 高危: 命中 deny 模式 'rm*' (command='rm -rf /tmp/maop_drill_cleanup')
  # - id: "cleanup"   # 已排除，需先移除 deny 规则
  - id: "probe"
  - id: "lint"
deny:
  - pattern: "rm*"
  ...
```

### 2. 人工评审（模拟）

allow 裁掉 `probe`，仅放行 `lint`；`mode` 改为 `enforce`。

### 3. ToolPolicy 决策实测

| 场景 | allowed | matched | reason |
|------|---------|---------|--------|
| `lint`（allow 命中） | True | `allow` | — |
| `cleanup`（deny 命中） | **False** | `deny` | `denied by deny rule` |
| `probe`（未放行 + enforce） | **False** | — | `not in allow list (mode=enforce)` |

### 4. ToolManager 端到端（真实执行路径）

| call | ok | 结果 | 日志 |
|------|-----|------|------|
| `lint` | True | `output='lint-ok\n'`，真实执行 | — |
| `probe` | **False** | `error='tool not allowed: probe: not in allow list (mode=enforce)'`，**未执行** | `[tool_manager] tool 'probe' blocked by policy` |
| `cleanup` | **False** | `error='tool not allowed: cleanup: denied by deny rule'`，**未执行** | `[tool_manager] tool 'cleanup' blocked by policy` |

**验证结论**：enforce 拒绝是 `ok=False` + error 明确 + warning 日志三通道可观测，且**不抛异常**（进程不崩，agent 侧静默降级——生产必须盯防日志）。

## 对生产操作的要求映射

| 本地演练动作 | 生产对应操作 |
|---|---|
| 注册模拟工具 | 部署环境真实 tools 表（已有数据） |
| 跑导出脚本 | `python py/scripts/export_tool_whitelist.py`（docker 环境进容器内联执行） |
| 评审裁掉 probe | 人工评审高危标注项 + 核对 allow 完整性 |
| enforce.yaml | 合并到 `config/tool_whitelist.yaml`，`mode: enforce` |
| 三类 call 实测 | 第 4 步灰度：单实例 `MAOP_TOOL_POLICY_MODE=enforce` + 盯防 `tool not allowed` 日志 |
| 断言 lint 放行 | 生产验证"合法工具不误拦" |

## 关键结论

1. **机制已验证**：导出 → 评审 → enforce 三类行为（放行 / deny 拦截 / 未放行拒绝）全部正确
2. **观测通道**：拒绝时 `ToolCallResult.error` + `[tool_manager] tool ... blocked by policy` 日志双通道
3. **安全特性**：deny 优先于 allow（cleanup 即使进 allow 也无效）；拒绝不抛异常（进程稳定）
4. **生产注意**：enforce 拒绝静默降级，**必须**按检查清单第 4 步盯防日志；当前本地 tools 表为空，切 enforce 无破坏风险但无实际拦截对象——生产在部署环境按 `docs/archive/plans/tool-whitelist-enforce-checklist.md` 五步执行即可
