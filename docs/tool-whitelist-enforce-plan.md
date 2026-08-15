# 工具白名单切 enforce — 阶段二/三实施方案（待审核）

> 版本：v1.0（2026-08-15）
> 流程：现状实测 → 方案设计 → 审核 → 风险评估 → 用户确认后执行
> 项目：MAOP（`F:\Nexus\MAOP`）
> 前置：T1 已落地（ToolPolicy + tool_whitelist.yaml + call/_call_sync_fallback 双路径拦截，audit 模式运行中）

---

## 一、现状（实测，2026-08-15）

| 项 | 实测结果 |
|---|---|
| `config/tool_whitelist.yaml` | `mode: audit` + `allow: []` + `deny: []`（注释含示例未启用） |
| `ToolPolicy.check()` | 决策链完整：deny 命中→拒；allow 命中→放行；均未命中→按 mode（audit 放行+warning / enforce 拒绝）；env `MAOP_TOOL_POLICY_MODE` 覆盖；配置缺失/损坏 fail-open 到 audit |
| `ToolManager._enforce_policy()` | call() 与 _call_sync_fallback() 双路径共用；拒绝返回 `ToolCallResult(ok=False, error="tool not allowed: ...")` 不抛异常 |
| `ToolManager.list()` | 返回 `[{category, tools: [{id, name, description, enabled}]}]`，**不含 command** |
| tools 表 schema | 含 `command TEXT NOT NULL` 字段（`tool_manager.py:172-185`） |
| 本机 tools 表 | `data/maop.db` 存在但 **无 tools 表**——工具在部署环境经 `register(id, command=...)` 入 DB，仓库内无种子 |
| audit 日志 | `[tool_policy] audit: tool %r not in whitelist ...` warning（本机未实际跑过调用，无收集数据） |
| 测试独立性 | `test_tool_manager.py` 全部用 `tmp_path` 临时目录构造，**不依赖仓库 yaml 默认值** ✓（改默认 audit 不影响测试） |

**关键事实**：本机无法导出"真实工具清单"（空表）。工具权威来源是部署环境 DB。阶段二的 allow 清单必须在**部署环境**生成，仓库内只能：① 预置高危 deny（安全默认）；② 提供导出脚本与切换检查清单。

---

## 二、方案选项（待拍板）

### 选项 A（推荐）：渐进加固 — 仓库补 deny + 导出脚本 + 部署方按清单切 enforce

- **立即生效**（本仓）：`deny` 填充高危命令清单（audit 模式下 deny 同样拦截，风险面立即收敛）
- **新增**：`py/scripts/export_tool_whitelist.py`（部署环境跑一次 → 从 DB 导出 id+command 生成 allow 段 yaml）
- **新增**：`config/tool_whitelist.enforce.example.yaml`（enforce 模板：mode: enforce + deny 高危 + allow 占位说明）
- **文档**：`docs/tool-whitelist-enforce-checklist.md`（切 enforce 五步检查清单）
- **仓库 yaml 保持 `mode: audit`**：切 enforce 是部署方的受控动作（改 yaml 或设 env），避免仓库默认 enforce 造成部署方未生成 allow 时全平台工具瘫痪
- 新增测试：导出脚本单测（临时 DB 生成正确 yaml）+ enforce 模式集成测试

### 选项 B（激进）：仓库直接切 enforce

- `config/tool_whitelist.yaml` 直接 `mode: enforce` + deny 高危 + allow 空
- 风险：**部署方未生成 allow 清单前，所有已入库工具全部被拒**（配置存在时 fail-closed，fail-open 不触发）→ 生产工具功能瘫痪
- 仅适合：确认部署环境已生成 allow 清单后再合并本选项

### 选项 C（折中，近 A）：A + 追加 enforce 门禁

- A 的全部内容 +
- CI 新增门禁：`enforce` 模式下跑 tool 相关测试（确保 allow 清单完整时测试全绿）
- 变更面大于 A（改 CI），收益有限（T1 测试已覆盖 enforce 语义）

**推荐 A**：安全默认立即生效（deny 高危）、工具清单由部署方按检查清单受控切换、不引入"配置存在即拒绝一切"的瘫痪风险。

---

## 三、方案 A 详细设计

### 3.1 仓库内 deny 高危清单（写入 tool_whitelist.yaml）

```yaml
mode: audit   # 保持 audit，切 enforce 由部署方执行

deny:
  - pattern: "rm*"         # 删除（含 rm -rf / rmdir）
  - pattern: "mkfs*"       # 格式化
  - pattern: "dd*"         # 块级覆写
  - pattern: "shutdown*"   # 关机
  - pattern: "reboot*"     # 重启
  - pattern: "halt*"       # 停机
  - pattern: "poweroff*"   # 断电
  - pattern: "sudo*"       # 提权
```

**取舍说明**：
- 不拦 `curl*`/`wget*`/`chmod*`/`chown*`——工具命令可能是合法的 HTTP 调用/权限操作（误伤面大于收益）
- pattern 匹配 id 与 command 双通道（`_match_rules` 实现），命令级拦截生效
- deny 在 audit 模式下**同样拦截**（`check()` 决策链 deny 优先）→ 立即收敛 rm/mkfs 类风险
- fnmatch `rm*` 会命中 `rmdir`——rmdir 属危险类，拦截符合预期

### 3.2 导出脚本 `py/scripts/export_tool_whitelist.py`

```
用法（部署环境）：
  python py/scripts/export_tool_whitelist.py [--out config/tool_whitelist.generated.yaml]
行为：
  1. 直连 tools 表（sqlite_connect + get_db_path("tool_manager")）
  2. 导出全部工具 id + command（含 enabled 状态；仅 enabled=1 进 allow）
  3. 生成 yaml：mode: audit + allow（id 精确匹配全量）+ deny（高危保留，从仓库模板继承）
  4. 输出前按 deny 模式集扫描命令，命中者标注 `# !! 高危: 命中 deny 模式, 建议人工评审` 注释
  5. --review 模式：仅输出高危命令清单供人工评审，不写 allow
产出：tool_whitelist.generated.yaml（部署方评审后替换/合并到 config/tool_whitelist.yaml）
```

**为什么 allow 用 id 精确而非 pattern 通配**：工具 id 稳定、可审计；命令变更（升级）不破坏 allow 匹配。

### 3.3 enforce 切换检查清单（docs/tool-whitelist-enforce-checklist.md）

```
切 enforce 五步：
1. 部署环境跑 export_tool_whitelist.py 生成 generated yaml
2. 人工评审：高危命令标注项（命中 deny 模式的）逐条决策（移除工具 or 放行）
3. 合并到 config/tool_whitelist.yaml（allow 填充 + mode: enforce）
4. 灰度：MAOP_TOOL_POLICY_MODE=enforce 启动一个实例，观察工具调用日志
   - 确认无"tool not allowed"误拦后全量切换
   - 有误拦 → 补充 allow 后重启（enforce 拒绝是 ok=False 不抛异常，agent 侧静默降级，必须日志盯防）
5. 全量切换后：新工具注册必须同步更新 allow（文档化流程）
```

### 3.4 新增测试

| 测试 | 断言 |
|---|---|
| `test_export_whitelist.py::test_generate_allow_from_db` | 临时 DB 注册 3 工具（含 1 个 enabled=0）→ 导出 yaml 仅含 2 个 enabled 工具 + deny 高危保留 |
| `test_export_whitelist.py::test_flag_high_risk_command` | 注册 command=`rm -rf /tmp/x` 的工具 → 导出标注高危 |
| `test_export_whitelist.py::test_review_mode_no_write` | --review 模式不写文件，仅输出高危清单 |
| enforce 集成（复用现有 TestToolPolicyUnit） | enforce + allow 完整 → call() 成功；enforce + allow 缺该工具 → ok=False 且 subprocess 零调用（现有测试已覆盖，补一个 allow 全量场景） |

---

## 四、风险评估

| # | 风险 | 等级 | 缓解 |
|---|------|------|------|
| 1 | **部署方未生成 allow 就切 enforce → 全部工具被拒（工具瘫痪）** | 🔴 高 | 选项 A 仓库保持 audit + 检查清单第 4 步灰度验证；enforce 拒绝为 ok=False 不抛异常（不崩进程），可日志快速定位 |
| 2 | **deny 误伤合法工具**（如工具命令恰为 `rm` 开头） | 🟡 中 | deny 只覆盖 rm/mkfs/dd/shutdown/reboot/halt/poweroff/sudo 8 类，均为破坏性命令；导出脚本会标注命中 deny 的工具，评审时发现误伤可移除规则 |
| 3 | **enforce 误拦后 agent 静默降级**（ok=False 被上层忽略） | 🟡 中 | `_enforce_policy` 拒绝时 `logger.warning`；检查清单第 4 步要求盯防日志；后续可加 dashboard 工具 blocked 标注（非本次范围） |
| 4 | **audit 日志噪音** | 🟢 低 | warning 级别，切 enforce 后自然消失 |
| 5 | **新工具注册后未同步 allow 被拦** | 🟢 低 | 检查清单第 5 步文档化注册流程；enforce 下新工具默认拒绝属最小权限预期 |
| 6 | **fail-open 掩盖 enforce 未生效**（配置损坏降级 audit） | 🟢 低 | `_load_yaml` 失败会 logger.warning 明确提示；检查清单第 4 步验证实际生效（构造一次拒绝场景） |

**不变量（行为零变化）**：
- audit 模式下未命中规则的工具：放行行为不变（选项 A 不引入 allow 的拒绝）
- 已 allow 的工具：enforce 下行为不变
- 测试：全部用临时配置，仓库 yaml 改动不影响现有 79 个 tool 测试

---

## 五、验收标准

1. `config/tool_whitelist.yaml` deny 高危 8 类填充，`mode: audit` 保持
2. `py/scripts/export_tool_whitelist.py` 存在，临时 DB 导出正确（allow 仅 enabled 工具 + 高危标注 + review 模式）
3. `docs/tool-whitelist-enforce-checklist.md` 存在（五步清单）
4. 新增测试全绿 + 现有 `test_tool_manager.py` / `test_tool_market.py` / `test_tool_mq_mcp_coverage.py` 零回归
5. `python -m py_compile` 新脚本通过

---

## 六、决策点（请拍板）

1. **选项选择**：A（推荐，渐进加固）/ B（激进，仓库直接 enforce）/ C（A + CI 门禁）
2. **deny 清单范围**：8 类高危（推荐）还是扩展（如加 curl/wget/chmod/chown——注意误伤面）？
3. **导出脚本落点**：`py/scripts/export_tool_whitelist.py`（推荐）还是并入现有脚本目录？
