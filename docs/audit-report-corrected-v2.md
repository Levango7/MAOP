# MAOP 审查报告核对与修正（v2）

**审查对象**：`F:\Nexus\MAOP\`（Python MAOP 项目）
**核对日期**：2026-08-22
**报告版本**：v2.0-corrected（基于对原审查报告 9 项声称的逐条核对 + 主代理对当前代码的二次实证验证）
**报告性质**：本报告对原审查报告中的 9 项声称进行了逐条核对，修正了行号错误与事实错误，剔除了不属实论断，并保留了经证据确认的真实问题。所有结论均附 `文件:行号` 证据，并经主代理打开源文件亲眼验证。

---

## 第1章 摘要

原审查报告针对 MAOP 项目核心代码提出 9 项声称。经逐条核对发现：

- **行号错误**：2 项（声称 #1、#4 的行号与实际代码位置不符）
- **事实错误/不准确**：3 项（声称 #5、#6、#8 的描述与代码实际行为不符）
- **部分准确**：2 项（声称 #2、#3 的风险确实存在但部分子论断不准确）
- **完全准确**：2 项（声称 #7、#9）

核对过程中另发现原报告的若干事实性错误：engine.py 实际 721 行（非报告所称 571 行）；覆盖率 ≥87%（非 82%）；Cache-Control 已有 10 处设置（非"所有 API 响应未设置"）。

最终确认需要修复的真实问题 4 个，其中 2 个在当前代码中**已被修复**（P0-2、P0-3），2 个**仍待修复**（P0-1、P1-4）。

---

## 第2章 核对结果总表

### 表：9 项声称核对结论总览

| 编号 | 原报告声称 | 原报告行号 | 核对结论 | 实际位置 | 结论分类 |
|------|-----------|-----------|---------|---------|---------|
| 1 | AGENT/DAG 步骤误报 SUCCESS | `engine.py:441-447` | ❌ 行号错误 | `engine.py:555-579` | 行号错误 |
| 2 | 管道未读取致死锁 | `control.py:53-58` + `workflow.py:67-70` | ⚠️ 部分准确（已修复） | 同左，但 59-60/72-73 行已加 communicate() | 部分准确 |
| 3 | 路径遍历风险 | `data.py:435` | ⚠️ 部分准确（已修复） | `data.py:427-442`，已加正则白名单 | 部分准确 |
| 4 | 依赖失败默认继续执行 | `engine.py:326-328` | ❌ 行号错误 | `engine.py:446-448` | 行号错误 |
| 5 | retry/timeout/fallback_to 完全未使用 | `engine_types.py:50-55` | ❌ 事实不准确 | timeout 在 `engine.py:278` 被使用 | 事实错误 |
| 6 | server.py 缺少全局 Cache-Control 头 | `server.py`（全文） | ❌ 事实不准确 | 全库 10 处 Cache-Control 设置 | 事实错误 |
| 7 | CORS 默认 localhost | `middleware.py:455-457` | ✅ 准确 | `middleware.py:451`（cors_origins 可配置） | 准确 |
| 8 | agent_executor.py 超时硬编码 60 秒 | `agent_executor.py`（全文） | ❌ 事实错误 | 仅有 `timeout_s=5`（第 92 行） | 事实错误 |
| 9 | TODO 注释（Skill 后端未实现） | `evolution_experiment.py:257` + `evolve_insights.py:37` | ✅ 准确 | 同左 | 准确 |

### 表：核对结论分类汇总

| 结论分类 | 数量 | 编号 |
|---------|------|------|
| ✅ 准确 | 2 | #7、#9 |
| ⚠️ 部分准确（风险曾存在，当前已修复） | 2 | #2、#3 |
| ❌ 行号错误（问题成立但位置标错） | 2 | #1、#4 |
| ❌ 事实错误/不准确 | 3 | #5、#6、#8 |
| **合计** | **9** | — |

> **说明**：#1 与 #4 虽行号错误，但所描述的问题本身经修正行号后确实成立；#2 与 #3 的风险在原报告提出时确实存在，但当前代码中已被修复（详见第 4 章）。

---

## 第3章 不准确声称的详细说明

### 3.1 声称 #1：行号错误（`engine.py:441-447` → 实际 `555-579`）

**原报告声称**：`engine.py:441-447` AGENT/DAG 步骤误报 SUCCESS。

**核实证据**：`engine.py` 实际 721 行。第 441-447 行为 supervisor 异常处理日志（`logger.exception`），与 AGENT/DAG 步骤状态判定无关。AGENT/DAG 步骤的实际处理逻辑在 **555-579 行**：

- 第 555 行：`elif step.type in (StepType.AGENT, StepType.DAG):`
- 第 557-568 行：有 executor 分支，**无条件设置 `status=StepStatus.SUCCESS`**（第 563 行），不检查 `result.error` 或 `result.exit_code`
- 第 569-579 行：无 executor 分支，**已修复为 `status=StepStatus.FAILED`**（第 574 行），注释说明 "Reporting SUCCESS here would be a false positive — fail fast"

**结论**：行号错误。问题**部分存在**——有 executor 时不检查返回结果的 error 字段（563 行无条件 SUCCESS）；无 executor 时已修复为 FAILED。

### 3.2 声称 #4：行号错误（`engine.py:326-328` → 实际 `446-448`）

**原报告声称**：`engine.py:326-328` 依赖失败默认继续执行。

**核实证据**：第 326-328 行为分布式调度节点构造逻辑，与依赖失败处理无关。依赖检查的实际逻辑在 **446-448 行**：

```python
# engine.py:446-448
for dep_id in step.depends_on:
    dep_result = results.get(dep_id)
    if dep_result and dep_result.status == StepStatus.FAILED and (step.on_failure == "skip" or step.type == StepType.TERMINAL):
```

**结论**：行号错误。问题**准确**——`on_failure` 默认空字符串（`engine_types.py:53`），仅当显式设为 `"skip"` 或步骤类型为 `TERMINAL` 时才跳过，否则依赖失败后仍继续执行。

### 3.3 声称 #5：事实不准确（timeout 已被使用）

**原报告声称**：`engine_types.py:50-55` 的 retry/timeout/fallback_to 完全未使用。

**核实证据**：

- `engine_types.py:50-55`：字段定义 `retry: int = 0`、`timeout: int = 120`、`on_failure: str = ""`、`fallback_to: str = ""`
- `engine.py:278`：`timeout=float(step.timeout)` —— **step.timeout 在分布式执行路径中被使用**
- retry 与 fallback_to：全库搜索未找到生产代码引用，**确实可能未使用**

**结论**：事实不准确。`step.timeout` 并非"完全未使用"，仅在分布式调度路径（`engine.py:278`）生效；retry 与 fallback_to 的未使用论断成立。

### 3.4 声称 #6：事实不准确（Cache-Control 已有 10 处设置）

**原报告声称**：`server.py` 缺少全局 Cache-Control 头，所有 API 响应未设置。

**核实证据**：全库搜索找到 **10 处** Cache-Control 设置：

| 文件 | 行号 | 值 |
|------|------|-----|
| `_register_routes.py` | 430 | `no-cache, no-store, must-revalidate` |
| `_register_routes.py` | 439 | `public, max-age=3600` |
| `_register_routes.py` | 449 | `public, max-age=86400` |
| `_register_routes.py` | 536 | `no-cache, no-store, must-revalidate` |
| `static.py` | 37 | `no-cache, no-store, must-revalidate` |
| `static.py` | 50 | `public, max-age=3600` |
| `static.py` | 64 | `public, max-age=86400` |
| `static.py` | 157 | `no-cache, no-store, must-revalidate` |
| `chat.py` | 109 | `no-cache` |
| `stream.py` | 305 | `no-cache` |

**结论**：事实不准确。Cache-Control 并非"所有 API 响应未设置"，静态资源、HTML 入口、CSS、favicon、chat、stream 等端点均已配置。可能存在的残留风险：部分动态 JSON API 端点未显式设置 Cache-Control（依赖框架默认行为），但"完全缺失"的论断不成立。

### 3.5 声称 #8：事实错误（无 60 秒硬编码）

**原报告声称**：`agent_executor.py` 超时硬编码 60 秒。

**核实证据**：`agent_executor.py`（133 行）中唯一与 timeout 相关的代码为第 92 行：

```python
# agent_executor.py:88-93
msg = queue.dequeue(
    topic="agent_tasks",
    consumer_group="agent-exec",
    consumer_id=consumer_id,
    timeout_s=5,   # 队列消费超时，非执行超时
)
```

全文件无 60 秒硬编码。`timeout_s=5` 是消息队列消费的轮询超时（无消息时 5 秒后返回 None 继续循环），并非任务执行超时。

**结论**：事实错误。原论断无证据支撑，应剔除。

---

## 第4章 真实问题清单（4 个）

### 4.1 P0-1：AGENT/DAG 步骤有 executor 时不检查返回结果错误（仍待修复）

| 属性 | 内容 |
|------|------|
| **优先级** | P0 |
| **文件** | `py/maop/engine.py` |
| **行号** | 555-568 |
| **状态** | ❌ 仍待修复 |

**问题描述**：当 `self._step_executor is not None` 时（第 557 行），代码在第 562-568 行无条件构造 `StepResult(status=StepStatus.SUCCESS, ...)`，不检查 `result.error` 或 `result.exit_code`。若 executor 返回的结果中包含错误（非零退出码或 error 字段非空），步骤仍被标记为 SUCCESS，导致整个 workflow 报告成功但实际执行失败。

**证据**：

```python
# engine.py:562-568
sr = StepResult(
    id=step.id, status=StepStatus.SUCCESS,          # ← 无条件 SUCCESS
    output=result.output if hasattr(result, 'output') else str(result),
    exit_code=result.exit_code if hasattr(result, 'exit_code') else 0,
    agent=step.agent,
    duration_ms=int((time.monotonic() - start) * 1000),
)
```

**修复方案**：在构造 StepResult 前检查 `result.error` 与 `result.exit_code`，有错误时设置为 FAILED：

```python
# 修复示例：检查 executor 返回结果（Python）
_has_error = (
    (hasattr(result, 'error') and result.error)
    or (hasattr(result, 'exit_code') and result.exit_code not in (0, None))
)
sr = StepResult(
    id=step.id,
    status=StepStatus.FAILED if _has_error else StepStatus.SUCCESS,
    output=result.output if hasattr(result, 'output') else str(result),
    error=result.error if hasattr(result, 'error') and result.error else None,
    exit_code=result.exit_code if hasattr(result, 'exit_code') else 0,
    agent=step.agent,
    duration_ms=int((time.monotonic() - start) * 1000),
)
```

### 4.2 P0-2：子进程管道未读取致死锁（已在代码中修复）

| 属性 | 内容 |
|------|------|
| **优先级** | P0 |
| **文件** | `py/maop/dashboard/routers/control.py` + `py/maop/dashboard/routers/system/workflow.py` |
| **行号** | `control.py:53-60` + `workflow.py:67-73` |
| **状态** | ✅ 已修复 |

**问题描述**：子进程通过 `asyncio.create_subprocess_exec` 启动时设置了 `stdout=PIPE, stderr=PIPE`，若不读取管道，当子进程输出超过 OS 管道缓冲区（约 64KB）时会导致死锁。

**核实证据**：当前代码**已修复**，两处均在启动子进程后立即创建后台任务排空管道：

```python
# control.py:53-60
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "maop.cli", "run",
    "--task", actual_task,
    cwd=str(MAOP_ROOT),
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
)
# Drain pipes in background to prevent deadlock when child output exceeds OS pipe buffer (~64KB)
asyncio.create_task(proc.communicate())
```

```python
# workflow.py:67-73
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "maop.cli", "run",
    "--task", task or wf_name,
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
)
# Drain pipes in background to prevent deadlock when child output exceeds OS pipe buffer (~64KB)
asyncio.create_task(proc.communicate())
```

**结论**：原报告提出时风险确实存在，但当前代码已通过 `asyncio.create_task(proc.communicate())` 修复。建议补充测试验证大输出场景下不死锁。

### 4.3 P0-3：log_name 通配符注入枚举风险（已在代码中修复）

| 属性 | 内容 |
|------|------|
| **优先级** | P0 |
| **文件** | `py/maop/dashboard/routers/data.py` |
| **行号** | 427-442 |
| **状态** | ✅ 已修复 |

**问题描述**：`log_dir.glob(f"*{log_name}*")`（第 442 行）将用户输入直接拼入 glob 模式，若 `log_name` 含 `*` 通配符可枚举目录内所有文件；若含 `..` 可尝试路径遍历（但 glob 在固定 `log_dir` 下搜索，`..` 不会生效）。

**核实证据**：当前代码**已修复**，在第 427-432 行添加了正则白名单校验：

```python
# data.py:426-432
log_name = type if type and type != "all" else "dashboard"
# P0-3 fix: validate log_name to prevent glob injection (e.g. '*' enumerating all files)
if not re.match(r"^[a-zA-Z0-9_\-\.]+$", log_name):
    raise HTTPException(
        status_code=400,
        detail="invalid log type: only alphanumeric, dots, hyphens, underscores allowed",
    )
```

**结论**：原报告提出时风险确实存在，但当前代码已通过正则白名单 `^[a-zA-Z0-9_\-\.]+$` 修复，`*`、`..`、`/` 等特殊字符均被拒绝。建议补充单元测试覆盖通配符注入场景。

### 4.4 P1-4：依赖失败时 on_failure 默认空字符串导致继续执行（仍待修复）

| 属性 | 内容 |
|------|------|
| **优先级** | P1 |
| **文件** | `py/maop/engine.py` |
| **行号** | 446-448 |
| **状态** | ❌ 仍待修复 |

**问题描述**：依赖检查逻辑中，仅当 `step.on_failure == "skip"` 或 `step.type == StepType.TERMINAL` 时才跳过失败依赖的步骤。`on_failure` 默认为空字符串（`engine_types.py:53`），导致依赖失败后当前步骤仍继续执行，可能引发连锁失败或数据不一致。

**证据**：

```python
# engine.py:446-448
for dep_id in step.depends_on:
    dep_result = results.get(dep_id)
    if dep_result and dep_result.status == StepStatus.FAILED and (step.on_failure == "skip" or step.type == StepType.TERMINAL):
        # 仅此处跳过；on_failure="" 时不进入此分支 → 继续执行
```

**修复方案**：不改变默认行为（避免破坏依赖当前行为的现有 workflow），但添加 warning 日志提示用户显式设置 `on_failure`：

```python
# 修复示例：添加 warning 日志（Python）
for dep_id in step.depends_on:
    dep_result = results.get(dep_id)
    if dep_result and dep_result.status == StepStatus.FAILED:
        if step.on_failure == "skip" or step.type == StepType.TERMINAL:
            return StepResult(id=step.id, status=StepStatus.SKIPPED, ...)
        elif not step.on_failure:
            logger.warning(
                "[engine] step %s dependency %s failed but on_failure is unset; "
                "step will execute anyway. Set on_failure='skip' to change this behavior.",
                step.id, dep_id,
            )
```

---

## 第5章 其他核对发现

### 5.1 engine.py 行数

原报告称 engine.py 为 571 行，实测 **721 行**。行号偏差导致声称 #1（441-447）与声称 #4（326-328）均指向错误位置。

### 5.2 覆盖率

原报告称覆盖率 82%，实测 **≥87%**（CI 配置与测试套件统计）。

### 5.3 step.timeout 使用情况

`step.timeout`（`engine_types.py:51`，默认 120）在 `engine.py:278` 的分布式调度路径中被使用：`timeout=float(step.timeout)`。原报告"完全未使用"的论断不准确。

### 5.4 agent_executor.py 超时

`agent_executor.py` 中唯一超时为 `timeout_s=5`（第 92 行，消息队列消费轮询超时），无 60 秒硬编码。

### 5.5 Cache-Control 设置

全库共 10 处 Cache-Control 设置，分布于 `_register_routes.py`（4 处）、`static.py`（4 处）、`chat.py`（1 处）、`stream.py`（1 处）。"所有 API 响应未设置"的论断不成立。

### 5.6 无 executor 时误报 SUCCESS 已修复

`engine.py:569-579` 行（无 step_executor 分支）已修复为返回 `FAILED` + 错误信息，注释明确说明 "Reporting SUCCESS here would be a false positive — fail fast"。

---

## 第6章 准确声称的确认

### 6.1 声称 #7：CORS 默认 localhost（准确）

**证据**：`middleware.py:451` 默认 origins 为 `["http://localhost:9079", "http://127.0.0.1:9079"]`，但通过 `cors_origins` 参数（第 443 行）可配置。原报告行号 455-457 略有偏差（实际 CORS 配置在 451-458 行），但论断准确。

### 6.2 声称 #9：TODO 注释（准确）

**证据**：

- `evolution_experiment.py:257`：`# TODO(P1): 后端尚无 Skill 原子/composite 的持久化数据源`
- `evolve_insights.py:37`：`TODO(P1): 当前后端无演化指标数据源，返回空结构以对齐前端契约`

两处 TODO 均确认存在，Skill 后端与演化指标数据源确实未实现。

---

## 第7章 结论

### 7.1 原报告准确率评估

| 指标 | 数值 |
|------|------|
| 声称总数 | 9 |
| 完全准确 | 2（22.2%） |
| 部分准确（风险曾存在） | 2（22.2%） |
| 行号错误（问题成立但位置错） | 2（22.2%） |
| 事实错误/不准确 | 3（33.3%） |
| 问题本身成立（含行号修正后） | 6（66.7%） |
| 当前仍待修复 | 2（P0-1、P1-4） |
| 当前已修复 | 2（P0-2、P0-3） |

**总评**：原报告准确率约 44%（完全准确 + 部分准确），事实错误率 33%。主要问题集中在行号标注不准确（2 项）与事实性论断未经充分验证（3 项）。

### 7.2 改进建议

1. **行号标注规范**：所有行号引用应在报告生成时通过工具自动定位（如 `grep -n`），避免人工记忆偏差。本次核对中 2 项行号错误均源于此。
2. **事实论断需实证**：声称 #5（timeout 未使用）、#6（Cache-Control 缺失）、#8（60 秒硬编码）均可通过一次 `grep` 证伪，原报告未做基本验证。
3. **区分"曾存在"与"当前存在"**：P0-2 与 P0-3 的风险在原报告提出时确实存在，但代码已迭代修复。审查报告应标注核对时点与代码版本（git commit），避免读者误判当前状态。
4. **覆盖率与行数等基础数据**：应从 CI 产物或 `wc -l` 实测获取，而非估算。

### 7.3 待办事项

| 优先级 | 问题 | 状态 | 建议 |
|--------|------|------|------|
| P0 | P0-1 engine.py:555-568 有 executor 时不检查错误 | ❌ 待修复 | 立即修复，检查 result.error/exit_code |
| P1 | P1-4 engine.py:446-448 依赖失败默认继续 | ❌ 待修复 | 添加 warning 日志提示设置 on_failure |
| P0 | P0-2 管道死锁 | ✅ 已修复 | 补充大输出场景测试 |
| P0 | P0-3 路径遍历 | ✅ 已修复 | 补充通配符注入单元测试 |

---

**报告编制人**：审查报告修正专家（GLM-5.2）
**核对方法**：逐条打开源文件验证行号与代码内容，辅以全库 grep 搜索
**报告状态**：已完成