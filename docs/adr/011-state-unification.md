# ADR-011: P0-3 状态源真统一（队列/人工队列单一真源）

## Status
Accepted (2026-08-05). queue.db is the single source for message queue; human_queue.db for human approval queue. PS-era human-queue.json retired.

## Context
项目从 PS 引擎迁移到 Python。当前在「队列/人工队列」状态上存在多真源隐患，经核查实际比任务书描述更复杂（已通过 Grep/Read 实测）：

1. **机器消息队列分裂脑（真实缺陷）**：`py/maop/maop_loop.py:246` 向 `data/message_queue.db` 写入消息，而 dashboard 的 `py/maop/dashboard/data_bridge.py:345 queue_stats()` 读取 `data/queue.db`。二者是不同文件，dashboard 统计恒为 0，与运行时实际队列状态不一致。
2. **人工队列真源标注错误**：Python 的人工审批队列真源是 `data/human_queue.db`（`py/maop/core/human_proxy.py:61`），但 `data_bridge._connect_queue`（:96-104）注释误称 queue.db 是「human task queue 真源」，造成命名混淆。
3. **human-queue.json 是纯 PS 遗留**：Python 无任何读写点（仅 `data_bridge.py:99-102` 注释提及；PS 唯一写点为 `src/human-proxy.ps1:15`），PS 正被淘汰，它将在 PS 下线后自然消亡。若 PS 与 Python 并行运行，二者各自维护人工队列，存在潜在逻辑分裂。
4. **circuit-breaker.json 实为 SQLite**：`py/maop/core/circuit_breaker.py:90-119` 含 SQLite DDL；`CircuitBreaker` 用 `sqlite3.connect(str(self._path))` 打开调用方传入的 `.json` 路径，故 `circuit-breaker.json` 是被错命名为 `.json` 的 SQLite 文件，功能单真源；但 `CircuitBreaker.__init__` 默认 `maop.db` 分支（:151）为死代码，命名也具误导性。

目标：让每种状态有**唯一、明确、被所有读写方共享的 SQLite 真源**，消除文件级分裂；对 PS 遗留 JSON 明确处置（删除或改为只读镜像），不给 Python 引入第二个真源。

## Decision
**采用「按领域分库、每库单真源」方案（推荐），不合并到单一大库：**

1. **机器消息队列**：统一到 `data/queue.db`。
   - `maop_loop.py:246` 的 `message_queue.db` → 改为 `queue.db`，与 `MessageQueue` 默认值（`message_queue.py:155`）及 `data_bridge` 读取端一致。
   - 删除/不再生成 `data/message_queue.db`（消除分裂脑）。
2. **人工审批队列**：Python 真源确认为 `data/human_queue.db`（`human_proxy.py`），保持不变。
   - `human-queue.json`（PS）**直接移除**（不是「只读镜像」）：理由——Python 侧已无任何读写点，镜像既无消费方也无一致性收益，保留只增加维护与混淆成本；PS 正淘汰，无「可用性」保留价值。
   - 修正 `data_bridge._connect_queue` 注释，明确 `queue.db`=机器消息队列、`human_queue.db`=人工审批队列，二者是不同领域，避免后续误读。
3. **熔断状态**：保持单文件，但**重命名为 `data/circuit-breaker.db`**（去掉误导性 `.json` 扩展名），同步更新 `maop_loop.py:175`、`provider.py:93,165` 的构造路径；清理 `CircuitBreaker.__init__` 中默认 `maop.db` 死分支（统一以 `circuit-breaker.db` 为默认路径）。
4. **备份**：`db_backup.py:68 DEFAULT_DATABASES` 增加 `"human_queue.db"`（当前漏备人工队列），移除对 `message_queue.db` 的任何隐含引用。

**取舍说明（可用性 vs 一致性 vs 迁移成本）**：
- 选「删除 human-queue.json」而非「只读镜像」：一致性最高、迁移成本最低（镜像需要双向或单向同步逻辑，且无人消费）。可用性的损失可忽略，因为 PS 已无长期保留意义。
- 选「按领域分库」而非「全部塞进 queue.db」：人工队列与机器队列 schema/生命周期不同（审批需过期、人工 resolve），合并会污染消息队列表结构、增加耦合；分库各自单真源最清晰。
- 不引入一次性数据迁移：human_queue.db 由原 HumanProxy 自举冷启，PS 遗留 pending 审批非关键生产态，直接删除 JSON 即可；如确有需要可由 code-reviewer 加一次性 import（见变更清单可选项）。

## Consequences
- **正面**：消除 dashboard 队列统计为 0 的真实缺陷；每类状态唯一真源，杜绝分裂脑；命名与代码一致，降低后续维护误解。
- **代价**：需改动 maop_loop（1 处）、data_bridge（注释）、db_backup（1 处增项）、PS 两处删除、`CircuitBreaker` 默认路径调整 + 两处调用方路径；并清理磁盘上残留的 `message_queue.db` 与 `human-queue.json`。
- **风险/影响面**：`maop_loop` 改用 `queue.db` 后，若旧 `message_queue.db` 有在途消息需迁移（建议启动脚本一次性搬移或丢弃，因消息多为瞬态，丢弃可接受）；`circuit-breaker.db` 重命名会使旧 `circuit-breaker.json` 中的熔断历史丢失（可接受，属运行时状态非业务数据）。
- **依赖**：纯 Python 改动 + PS 删除，不涉及路由/maop_plan。

## Change List（函数级，供 code-reviewer 执行；本 ADR 不含代码实现）

| # | 文件:行 | 函数/位置 | 当前 | 改为 |
|---|---|---|---|---|
| 1 | `py/maop/maop_loop.py:246` | `MaopLoop.__init__` 中 `self._message_queue = MessageQueue(...)` | `db_path=self._root/"data"/"message_queue.db"` | `db_path=self._root/"data"/"queue.db"` |
| 2 | `py/maop/core/db_backup.py:68` | `DEFAULT_DATABASES` | `["maop.db","memory.db","queue.db"]` | 增加 `"human_queue.db"` → `["maop.db","memory.db","queue.db","human_queue.db"]`；确认对不存在的 `message_queue.db` 不报错 |
| 3 | `py/maop/dashboard/data_bridge.py:96-104` | `_connect_queue` docstring | 称 queue.db 是「human task queue 真源」并提 human-queue.json 分裂 | 重写为：queue.db=机器消息队列（`MessageQueue`）；人工审批队列真源是 `human_queue.db`（`HumanProxy`）；human-queue.json 已移除，无 Python 读写 |
| 4 | `py/maop/core/circuit_breaker.py:149-152` | `CircuitBreaker.__init__` | 默认 `maop.db`，调用方传 `.json` | 默认改为 `data/circuit-breaker.db`；删除/修正默认 `maop.db` 死分支 |
| 5 | `py/maop/maop_loop.py:175` | `CircuitBreaker(...)` 构造 | `self._root/"data"/"circuit-breaker.json"` | `self._root/"data"/"circuit-breaker.db"` |
| 6 | `py/maop/dashboard/provider.py:93,165` | `CircuitBreaker(...)` 构造（两处） | `.../"circuit-breaker.json"` | `.../"circuit-breaker.db"` |
| 7 | `src/human-proxy.ps1:15` | PS 写 `human-queue.json` | 写入 `data/human-queue.json` | 随 PS 淘汰删除该写点/文件（PS 侧，code-reviewer 负责） |
| 8 | `src/circuit-breaker.ps1:9`、`circuit-breaker.psm1:31`、`database.ps1:438`、`delegate-plugin.ps1:112`、`doctor.ps1:23` | PS 引用 `circuit-breaker.json` | 写/读 `.json` | 随 PS 淘汰统一删除或改 `.db`（PS 侧） |
| 9 | 运维/迁移脚本 | 磁盘残留清理 | 存在 `data/message_queue.db`、`data/human-queue.json`、`data/circuit-breaker.json` | 删除三者（可选：迁移 message_queue.db 在途消息到 queue.db；human_queue.db 冷启无需迁移） |
| 10 | `py/tests/test_data_bridge.py:79-80` | 测试构造 queue.db | 已正确 | 不变；补充断言 human_pending 走 human_queue.db |
| 11 | `py/tests/test_message_queue.py:21` | `db_path = tmp/"queue.db"` | 已正确 | 不变（确认不再有 message_queue.db 用例） |
| 12 | `py/tests/test_db_backup.py:23,40` | 断言 3 个库 | 断言 maop/memory/queue | 增加断言含 human_queue.db（共 4 个） |

**可选（非必须）**：若生产确有 PS 遗留 pending 审批需保留，在 `human_proxy.py` 增加一次性 `migrate_from_json(path)` 导入 `data/human-queue.json` 到 `human_queue.db`，迁移后删除 JSON。默认不实现，直接删除 JSON。
