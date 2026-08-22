# 黑板架构（Blackboard Architecture）设计文档

> 灵感来源：经典黑板系统（HEARSAY-II 语音识别系统）+ MAOP 现有 EventBus / 三层记忆基础设施。
>
> 本文档仅做设计，不包含实施代码修改。实施阶段将另行输出 patch。

## 第1章　需求分析

### 1.1　现状分析

#### 1.1.1　现有 EventBus 能力盘点

MAOP 已在 `py/maop/core/reliability/event_bus.py` 实现了一套成熟的发布订阅事件总线，核心能力如下：

- **异步分发**：支持同步与异步订阅者（`SyncHandler` / `AsyncHandler`），通过 `inspect.iscoroutinefunction` 自动识别。
- **通配符主题**：支持 `execution.*` 形式的通配符订阅，`_find_matching_subs` 做前缀匹配与去重。
- **ACK 与重试**：`ack_required=True` 时按 `max_retries` 重试，退避间隔 `retry_delay_s`。
- **死信跟踪**：重试耗尽后写入 `DeadLetterEntry`，可通过 `get_dead_letters()` 审计。
- **优先级排序**：`EventPriority`（LOW/NORMAL/HIGH/CRITICAL），同主题内高优先级先执行。
- **线程安全**：`threading.RLock` 保护订阅表、历史、死信、计数器的并发变更。
- **历史留存**：`_history` 环形缓冲（默认 200 条），`get_history()` 支持主题前缀过滤。

#### 1.1.2　EventBus 与黑板架构的本质区别

EventBus 是**消息传递机制**，黑板架构是**协作问题求解范式**。二者在抽象层次、数据语义、控制流上存在根本差异，对照如下。

表：EventBus 与黑板架构对照表

| 维度 | EventBus（发布订阅） | Blackboard（黑板架构） |
|------|----------------------|------------------------|
| 核心抽象 | 事件（`topic` + `data` 扁平 dict） | 结构化知识条目（`schema` + `state` + `version` + `provenance`） |
| 数据存储 | 无状态中转，事件即发即弃；`_history` 仅用于事后查询 | 持久化共享知识库，条目可反复读写、查询、演进 |
| 触发机制 | 显式 `publish(event)` 触发订阅者，一次触发一次响应 | 知识源注册触发规则，黑板状态变化自动评估规则并触发，可链式扩散 |
| 协作模式 | 订阅者之间松耦合、互不感知，无共享上下文 | 知识源通过共享黑板间接协作，可读取其他知识源的产出作为输入 |
| 控制流 | 数据流驱动（事件沿 topic 流转） | 状态驱动（黑板状态变迁 → 规则匹配 → 知识源执行 → 写回黑板 → 再变迁） |
| 收敛性 | 无收敛概念，事件流可无限延续 | 可定义终止条件（黑板达到目标状态或无规则可触发） |
| 优先级 | 订阅者静态优先级 | 知识源优先级 + 触发规则优先级 + 动态调度（抢占/让步） |
| 适用场景 | 解耦模块间一次性通知（如执行结果回传） | 多知识源协同求解非结构化/复杂问题（如故障诊断、图谱构建） |

#### 1.1.3　现有三层记忆能力盘点

`py/maop/core/memory/three_layer_memory.py` 与 `py/maop/memory/facade.py` 提供了分层记忆：

- **L1 Working Memory**：进程内 `LRUCache`，TTL + 容量驱逐，驱逐时溢出到 L2。
- **L2 Episodic Memory**：SQLite `episodic_memory` 表，任务经验按时间衰减检索。
- **L3 Semantic Memory**：`VectorStore` 向量索引，`consolidate()` 将 L2 提升到 L3。
- **Facade 统一入口**：`MemoryFacade` 按 `mode` 路由到 `MemoryManager`（chat）或 `ThreeLayerMemory`（agent），共享同一 `maop.db`。

三层记忆解决了**单 Agent 的记忆持久化与检索**问题，但未解决**多知识源协作求解**问题：它没有知识源注册、没有状态变迁触发规则、没有多源并发读写同一结构化问题的协调机制。

#### 1.1.4　现状结论

- EventBus 提供**可靠的事件分发底座**，可作为黑板状态变迁通知的传输层复用。
- 三层记忆提供**持久化存储与向量检索**，可作为黑板条目存储的后端之一。
- 二者均**不构成黑板架构**：缺少结构化共享黑板、知识源抽象、触发规则引擎、收敛控制四个核心组件。
- 结论：在 EventBus 之上构建黑板层，复用其分发能力；黑板存储基于 SQLite（与现有 `maop.db` 共享）或 Redis（分布式场景），不重复造轮子。

### 1.2　目标能力

本次设计需交付以下四项核心能力：

1. **结构化共享知识库（Shared Blackboard）**：提供带 schema 的结构化知识条目存储，支持按领域（domain）、键（key）、状态（state）查询；条目带版本号与来源溯源（provenance），支持乐观并发控制。
2. **多知识源异步读写（Knowledge Source）**：知识源是独立处理单元，声明其读取依赖与写入产出；多个知识源可并发读写黑板，控制器负责调度与冲突仲裁。
3. **状态变化事件驱动（State-driven Trigger）**：黑板条目状态变迁自动产生事件，触发规则引擎评估；触发规则为条件谓词（predicate），匹配后按优先级调度知识源执行。
4. **知识源注册与触发规则（Registration & Trigger Rule）**：支持运行时动态注册知识源与触发规则；规则可定义前置条件、优先级、是否可重入；支持终止条件判定以收敛求解。

### 1.3　用户场景

#### 1.3.1　场景一：知识图谱构建

多个知识源协同从非结构化文本构建知识图谱：

- **知识源 A（实体抽取器）**：读取黑板上的原始文本条目，抽取实体写回黑板。
- **知识源 B（关系抽取器）**：读取黑板上的实体条目，抽取关系写回黑板。
- **知识源 C（图谱合并器）**：读取实体与关系条目，合并去重后写入图谱条目。
- **触发规则**：当原始文本条目状态变为 `ingested`，触发 A；当实体条目状态变为 `extracted`，触发 B；当实体与关系均 `extracted`，触发 C。
- **收敛条件**：所有原始文本处理完毕且图谱条目不再增长。

#### 1.3.2　场景二：故障诊断

多知识源协同定位生产故障根因：

- **知识源 A（指标采集器）**：读取黑板上的故障告警条目，采集相关指标写回黑板。
- **知识源 B（日志分析器）**：读取告警与指标条目，分析日志写回黑板。
- **知识源 C（拓扑分析器）**：读取指标条目，分析调用拓扑定位异常节点写回黑板。
- **知识源 D（根因裁决器）**：读取所有分析产出，综合裁决根因写回黑板并标记 `diagnosed`。
- **触发规则**：告警 `received` → 触发 A；指标 `collected` → 触发 B、C（并发）；B、C 均 `done` → 触发 D。
- **收敛条件**：黑板出现 `diagnosed` 状态的根因条目。

#### 1.3.3　场景三：长任务协作

多个 Agent 协作完成长周期任务（如大型代码重构）：

- **知识源 A（任务拆分器）**：读取黑板上的总任务条目，拆分为子任务写回黑板。
- **知识源 B（子任务执行器）**：读取黑板上的待执行子任务，执行后写回结果条目。
- **知识源 C（结果聚合器）**：读取已完成子任务结果，聚合写回黑板。
- **知识源 D（质量检查器）**：读取聚合结果，校验不通过则回写新子任务触发再执行。
- **触发规则**：总任务 `pending` → 触发 A；子任务 `pending` → 触发 B；子任务 `done` → 触发 C；聚合 `aggregated` → 触发 D；D 判定不通过 → 回写子任务（链式扩散）。
- **收敛条件**：质量检查通过或达到最大迭代次数。

## 第2章　设计方案

### 2.1　架构结构图

黑板架构由四个核心组件构成：共享黑板、知识源、触发规则、黑板控制器。控制器监听黑板状态变迁，评估触发规则，调度知识源执行，知识源读写黑板后又产生新的状态变迁，形成闭环直至收敛。

图：黑板架构总体结构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        KnowledgeSource 集合                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ KS_A     │  │ KS_B     │  │ KS_C     │  │ KS_X     │         │
│  │ (抽取)   │  │ (分析)   │  │ (合并)   │  │ (裁决)   │         │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────▲─────┘         │
│       │ read/write   │ read/write   │ read/write   │ write        │
└───────┼──────────────┼──────────────┼──────────────┼─────────────┘
        ▼              ▼              ▼              │
┌─────────────────────────────────────────────────────┐
│                   共享黑板（Blackboard）              │
│  ┌───────────────────────────────────────────────┐  │
│  │  BlackboardEntry[]  (结构化知识条目)           │  │
│  │  ├─ domain / key / schema / payload           │  │
│  │  ├─ state / version / provenance              │  │
│  │  └─ created_at / updated_at                   │  │
│  └───────────────────────────────────────────────┘  │
│         │ state 变迁 (write → state change)          │
│         ▼                                            │
│  ┌───────────────────────────────────────────────┐  │
│  │  bus.publish(Event(topic="bb.changed", ...))  │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────┘
                               │ 事件
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BlackboardController（黑板控制器）               │
│  ┌─────────────────┐   ┌─────────────────┐                      │
│  │ TriggerRule 引擎 │   │  调度器          │                      │
│  │ 规则匹配/优先级  │──▶│  KS 执行/并发    │                      │
│  └─────────────────┘   └─────────────────┘                      │
│         │                                                        │
│         ▼  触发 KS_X (按优先级)                                   │
└─────────┼────────────────────────────────────────────────────────┘
          │
          └──────▶  KnowledgeSource_X.execute(blackboard)  ──── 闭环
```

数据流说明：

1. 知识源 A/B/C 读写共享黑板，产生 `BlackboardEntry` 状态变迁。
2. 黑板将状态变迁通过现有 EventBus 发出事件：`await event_bus.publish(Event(topic="blackboard.changed", data={"entry_id": ..., "domain": ..., "key": ..., "old_state": ..., "new_state": ..., "version": ...}, source="blackboard"))`。
3. `BlackboardController` 订阅该事件，取出变迁条目，送入 `TriggerRule` 引擎评估。
4. 规则引擎匹配命中的规则，按优先级排序后交由调度器执行对应知识源。
5. 知识源执行完毕写回黑板，回到步骤 1，形成状态驱动闭环。
6. 控制器每轮评估终止条件，满足则停止调度，求解收敛。

### 2.2　核心类设计

#### 2.2.1　BlackboardEntry（黑板条目）

黑板条目是结构化知识的最小单元，带领域、键、schema、状态、版本与来源溯源。

```python
# 代码示例：BlackboardEntry 数据结构（Python）
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EntryState(str, Enum):
    """黑板条目生命周期状态。"""
    DRAFT = "draft"           # 草稿，知识源写入中
    READY = "ready"           # 就绪，可被其他知识源读取
    LOCKED = "locked"         # 锁定，某知识源独占处理中
    DONE = "done"             # 完成，终态
    FAILED = "failed"         # 失败，终态
    SUPERSEDED = "superseded" # 被新版本取代


@dataclass
class BlackboardEntry:
    """黑板条目：结构化知识单元。

    Parameters
    ----------
    domain : str
        领域命名空间，如 "kg.build"、"diagnosis"、"refactor"。
    key : str
        领域内唯一键，如 "entity:UserService"、"alert:alert-123"。
    schema : str
        payload 的 schema 标识（如 "entity.v1"、"metric.v1"），用于校验。
    payload : dict[str, Any]
        实际知识内容，需符合 schema 约束。
    state : EntryState
        条目状态，驱动触发规则匹配。
    version : int
        乐观并发控制版本号，每次写回自增。
    provenance : dict[str, Any]
        来源溯源，记录产生该条目的知识源、输入条目 ID、时间戳。
    """
    id: str = ""                          # 全局唯一 ID（UUID）
    domain: str = ""
    key: str = ""
    schema: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    state: EntryState = EntryState.DRAFT
    version: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
```

表：BlackboardEntry 字段说明表

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| `id` | `str` | 全局唯一 ID（UUID4） | 非空，主键 |
| `domain` | `str` | 领域命名空间 | 非空，索引列 |
| `key` | `str` | 领域内唯一键 | 非空，`(domain, key)` 联合唯一 |
| `schema` | `str` | payload schema 标识 | 非空，用于校验 |
| `payload` | `dict` | 知识内容 | 需通过 schema 校验 |
| `state` | `EntryState` | 生命周期状态 | 枚举值 |
| `version` | `int` | 乐观锁版本号 | ≥0，每次写回自增 |
| `provenance` | `dict` | 来源溯源 | 含 `source_ks`、`input_ids`、`timestamp` |
| `created_at` | `str` | 创建时间（ISO-8601） | 自动填充 |
| `updated_at` | `str` | 更新时间（ISO-8601） | 每次写回更新 |

#### 2.2.2　Blackboard（共享黑板）

共享黑板提供条目 CRUD、状态变迁、查询、订阅能力。存储后端为 SQLite（单机，复用 `maop.db`）或 Redis（分布式）。

```python
# 代码示例：Blackboard 接口（Python）
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable


class Blackboard(ABC):
    """共享黑板抽象接口。

    存储后端由实现类决定（SQLiteBlackboard / RedisBlackboard）。
    所有写操作产生状态变迁事件，通过 EventBus 广播。
    """

    @abstractmethod
    async def write(
        self,
        domain: str,
        key: str,
        schema: str,
        payload: dict[str, Any],
        *,
        state: EntryState = EntryState.READY,
        source_ks: str = "",
        input_ids: list[str] | None = None,
    ) -> BlackboardEntry:
        """写入/更新条目。

        - 若 (domain, key) 不存在则创建，version=0。
        - 若存在则更新，version 自增；旧版本标记 SUPERSEDED。
        - 写入后通过 EventBus 异步发布事件：
          await event_bus.publish(Event(
              topic="blackboard.changed",
              data={"entry_id": ..., "domain": ..., "key": ...,
                    "old_state": ..., "new_state": ..., "version": ...},
              source="blackboard",
          ))
        """

    @abstractmethod
    async def read(self, domain: str, key: str) -> BlackboardEntry | None:
        """按 (domain, key) 读取最新版本条目。"""

    @abstractmethod
    async def query(
        self,
        *,
        domain: str = "",
        state: EntryState | None = None,
        schema: str = "",
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        """按条件查询条目列表。"""

    @abstractmethod
    async def transition(
        self,
        entry_id: str,
        new_state: EntryState,
        *,
        expected_version: int | None = None,
    ) -> BlackboardEntry:
        """状态变迁（乐观锁）。

        - expected_version 不匹配时抛 VersionConflictError。
        - 变迁后通过 EventBus 异步发布事件：
          await event_bus.publish(Event(
              topic="blackboard.changed",
              data={"entry_id": ..., "domain": ..., "key": ...,
                    "old_state": ..., "new_state": ..., "version": ...},
              source="blackboard",
          ))
        """

    @abstractmethod
    async def lock(self, entry_id: str, ks_name: str, ttl_s: float = 30.0) -> bool:
        """独占锁（知识源处理前锁定条目，防止并发冲突）。"""

    @abstractmethod
    async def unlock(self, entry_id: str, ks_name: str) -> None:
        """释放独占锁。"""

    @abstractmethod
    def subscribe_changes(
        self,
        handler: Callable[[BlackboardEntry], Awaitable[None]],
    ) -> None:
        """订阅条目状态变迁（内部委托 EventBus.subscribe）。"""
```

#### 2.2.3　KnowledgeSource（知识源）

知识源是独立处理单元，声明其读取依赖与写入领域，执行时从黑板读取输入、计算、写回产出。

```python
# 代码示例：KnowledgeSource 接口（Python）
from abc import ABC, abstractmethod


class KnowledgeSource(ABC):
    """知识源抽象基类。

    子类需实现 ``execute``，并声明 ``read_domains`` / ``write_domains``
    供控制器做依赖分析与并发调度。
    """

    name: str = ""               # 知识源唯一名称
    priority: int = 0            # 调度优先级（越大越先）
    reentrant: bool = False      # 是否允许重入（同一触发可并发多实例）

    @property
    @abstractmethod
    def read_domains(self) -> list[str]:
        """声明读取的领域列表（用于依赖图构建）。"""

    @property
    @abstractmethod
    def write_domains(self) -> list[str]:
        """声明写入的领域列表。"""

    @abstractmethod
    async def execute(self, bb: Blackboard, trigger_entry: BlackboardEntry) -> None:
        """执行知识源逻辑。

        Parameters
        ----------
        bb : Blackboard
            共享黑板实例，用于读写。
        trigger_entry : BlackboardEntry
            触发本次执行的黑板条目（状态变迁源）。
        """
```

#### 2.2.4　TriggerRule（触发规则）

触发规则是条件谓词，定义何时触发哪个知识源。规则引擎在黑板状态变迁时评估所有规则，命中的按优先级排序后调度。

```python
# 代码示例：TriggerRule 接口（Python）
from abc import ABC, abstractmethod


class TriggerRule(ABC):
    """触发规则抽象基类。

    一个规则绑定一个知识源，定义其触发条件（谓词）。
    """

    name: str = ""               # 规则唯一名称
    target_ks: str = ""          # 目标知识源名称
    priority: int = 0            # 规则优先级（同轮多规则命中时排序）

    @abstractmethod
    def matches(self, entry: BlackboardEntry, bb: Blackboard) -> bool:
        """评估规则是否命中。

        可读取黑板做更复杂的条件判断（如"存在至少 2 个 extracted 实体"）。
        纯状态匹配的简单规则可直接比对 entry.state。
        """

    @abstractmethod
    def describe(self) -> str:
        """人类可读的规则描述（用于 dashboard 展示与调试）。"""
```

内置规则工厂提供常用模式，避免每个场景手写谓词：

表：内置触发规则工厂说明表

| 工厂方法 | 触发条件 | 典型用途 |
|----------|----------|----------|
| `on_state(domain, state, ks)` | 指定领域出现指定状态的条目 | 状态机驱动（如 `ingested` → 抽取） |
| `on_state_count(domain, state, n, ks)` | 指定领域指定状态条目数 ≥ n | 等待多源就绪后聚合 |
| `on_schema_and_state(schema, state, ks)` | 指定 schema 且指定状态 | 细粒度类型+状态匹配 |
| `custom(predicate, ks)` | 自定义谓词函数 | 复杂跨条目条件 |

#### 2.2.5　BlackboardController（黑板控制器）

控制器是黑板架构的"大脑"，监听变迁、评估规则、调度知识源、判定收敛。

```python
# 代码示例：BlackboardController 接口（Python）
from abc import ABC, abstractmethod


class BlackboardController(ABC):
    """黑板控制器：规则引擎 + 调度器 + 收敛判定。

    生命周期：
        1. register_ks / register_rule  →  注册知识源与规则
        2. start                        →  订阅黑板变迁，进入主循环
        3. 每次变迁                      →  评估规则 → 调度命中 KS
        4. is_converged                 →  停止调度
        5. stop                         →  清理资源
    """

    @abstractmethod
    def register_ks(self, ks: KnowledgeSource) -> None:
        """注册知识源。"""

    @abstractmethod
    def register_rule(self, rule: TriggerRule) -> None:
        """注册触发规则。"""

    @abstractmethod
    def set_convergence(self, predicate: Callable[[Blackboard], bool]) -> None:
        """设置收敛判定谓词。满足时停止调度。"""

    @abstractmethod
    async def start(self) -> None:
        """启动控制器（订阅黑板变迁事件）。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止控制器（取消订阅，等待在途任务完成）。"""

    @abstractmethod
    async def is_converged(self) -> bool:
        """判定是否已收敛。"""
```

控制器内部调度策略：

- **优先级调度**：同轮命中多个规则时，按 `rule.priority` 降序排序，同优先级按 `ks.priority` 二次排序。
- **并发控制**：`read_domains` 无交集的知识源可并发执行；有交集的串行执行以避免读写冲突。并发分组的校验规则见下方"read_domains 声明与并发校验"说明。
- **可重入控制**：`ks.reentrant=False` 时，同一知识源未完成则跳过新触发（或入队等待）。
- **死锁防护**：知识源执行设超时（`execution_timeout_s`），超时强制释放条目锁并标记 `FAILED`。
- **迭代上限**：`max_iterations` 防止非收敛场景无限循环，达到上限强制停止并告警。

**read_domains 声明与并发校验**（对应审核项 R-7）：

知识源并发分组依赖 `read_domains` 与 `write_domains` 声明的正确性。若知识源未正确声明，可能导致本应串行的知识源被并发执行，引发读写冲突。控制器采用以下校验机制：

1. **注册时默认值**：知识源注册时（`register_ks`），若未声明 `read_domains`（返回空列表），默认为**只读全部域**（保守策略，等价于 `read_domains = ["*"]`）。此默认值与任何知识源的 `write_domains` 均有交集，因此未声明 `read_domains` 的知识源**不与其他知识源并发执行**，仅可独占运行。
2. **调度前并发校验**：控制器在调度前对同轮命中的知识源两两校验：
   - 若知识源 A 的 `write_domains` 与知识源 B 的 `read_domains` 有交集，**或**知识源 B 的 `write_domains` 与知识源 A 的 `read_domains` 有交集，则 A 与 B **不可并发执行**。
   - 上述两条均无交集时，A 与 B 可并发执行。
3. **校验失败处理**：若运行时检测到并发冲突（如知识源声明的 `read_domains` 不完整导致实际读写冲突），控制器记录警告日志（含冲突知识源名称、涉及域），并**降级为串行执行**该批次知识源，确保数据一致性。
4. **声明完整性要求**：知识源子类**必须**如实声明 `read_domains` 与 `write_domains`。控制器在注册时记录声明信息到 dashboard，便于运维核查。建议在知识源单元测试中加入声明完整性断言。

### 2.3　与现有系统集成方案

#### 2.3.1　分层复用关系

黑板架构不重写基础设施，而是复用现有 EventBus 与三层记忆，在其之上增加黑板语义层。

图：黑板架构与现有系统集成示意图

```
┌─────────────────────────────────────────────────────────┐
│              新增层：Blackboard 语义层                    │
│  blackboard.py                                          │
│  ├─ BlackboardEntry / EntryState                        │
│  ├─ Blackboard (SQLiteBlackboard / RedisBlackboard)     │
│  ├─ KnowledgeSource / TriggerRule                       │
│  └─ BlackboardController                                │
└──────────────┬──────────────────────┬───────────────────┘
               │ 复用事件分发          │ 复用持久化/检索
               ▼                      ▼
┌──────────────────────────┐  ┌──────────────────────────┐
│  现有层：EventBus         │  │  现有层：三层记忆          │
│  event_bus.py            │  │  three_layer_memory.py    │
│  (publish/subscribe)     │  │  facade.py               │
│  → blackboard.changed    │  │  → 条目存储后端           │
└──────────────────────────┘  └──────────────────────────┘
```

#### 2.3.2　EventBus 复用方案

黑板状态变迁通过现有 `EventBus` 广播，不引入新的事件机制。

##### 2.3.2.1　统一 EventBus 实例与 API（对应审核项 C-3）

当前代码库存在两个 EventBus 实现，API 不统一：

- `maop.enterprise.notification.event_bus.EventBus`——提供 `emit(topic, payload)` 方法（`failure_detector.py` 使用）。
- `maop.core.reliability.event_bus.EventBus`——提供 `publish(Event)` / `publish_sync(Event)` 方法（`engine.py`、黑板架构使用）。

若两个实例独立，则黑板发布的 `blackboard.changed` 事件与监督者发布的 `supervisor.alert` 事件**无法互通**——监督者无法订阅黑板变迁事件来监控知识源执行，黑板也无法订阅监督者预警来触发知识源。

**统一方案**：

1. **全局唯一实例**：统一使用 `maop.core.reliability.event_bus.EventBus`，通过 `get_event_bus()` 获取单例。黑板控制器、监督者、执行引擎均注入同一实例。
2. **统一 API**：所有事件发布统一使用 `publish(Event)` 异步 API 或 `publish_sync(Event)` 同步 API，接收 `Event` 对象而非 `(topic, payload)` 元组。
3. **监督者侧修正（由监督者型设计文档负责）**：`failure_detector.py` 的 `_publish_event` 方法需将 `self._event_bus.emit(event_type, full_payload, tenant_id=...)` 改为 `await self._event_bus.publish(Event(topic=event_type, data=full_payload, source="failure_detector"))`（或同步包装 `publish_sync`）；其 TYPE_CHECKING 导入需改为 `from maop.core.reliability.event_bus import EventBus`。
4. **跨架构协作**：统一后，黑板控制器与监督者共享同一 EventBus 实例，可互相订阅对方事件：
   - 监督者订阅 `blackboard.changed` 事件，监控知识源执行健康度（如某知识源频繁写 `FAILED` 条目则触发降级）。
   - 黑板控制器订阅 `supervisor.alert` / `agent_replaced` 事件，在 agent 替换后重新调度依赖该 agent 的知识源。

##### 2.3.2.2　事件定义与发布 API（对应审核项 F-6）

黑板状态变迁事件使用 `Event` 对象封装，通过 `publish(Event)` 异步发布（或 `publish_sync(Event)` 同步发布）：

- **事件主题**：`blackboard.changed`，`data` 含 `entry_id`、`domain`、`key`、`old_state`、`new_state`、`version`。
- **事件来源**：`source="blackboard"`，用于跨架构事件溯源与审计。
- **异步发布**（推荐，在异步上下文中使用）：

  ```python
  # 代码示例：黑板变迁事件异步发布（Python）
  from maop.core.reliability.event_bus import get_event_bus, Event

  event_bus = get_event_bus()
  await event_bus.publish(Event(
      topic="blackboard.changed",
      data={
          "entry_id": entry.id,
          "domain": entry.domain,
          "key": entry.key,
          "old_state": old_state.value,
          "new_state": entry.state.value,
          "version": entry.version,
      },
      source="blackboard",
  ))
  ```

- **同步发布**（在同步上下文或回调中使用）：

  ```python
  # 代码示例：黑板变迁事件同步发布（Python）
  from maop.core.reliability.event_bus import get_event_bus, Event

  event_bus = get_event_bus()
  event_bus.publish_sync(Event(
      topic="blackboard.changed",
      data={"entry_id": entry.id, "domain": entry.domain, ...},
      source="blackboard",
  ))
  ```

- **订阅方**：`BlackboardController` 在 `start()` 时调用 `get_event_bus().subscribe("blackboard.changed", self._on_change)`。
- **ACK 与重试**：黑板变迁事件设 `ack_required=True`、`max_retries=3`，复用 EventBus 死信机制保证不丢触发。
- **通配符**：知识源可订阅 `blackboard.changed.<domain>` 细粒度主题（实现时在 publish 时按 domain 拼接子主题）。

#### 2.3.3　存储后端方案

黑板条目存储采用双后端策略，按部署模式选择：

表：黑板存储后端选型对照表

| 后端 | 适用场景 | 实现要点 | 与现有系统集成 |
|------|----------|----------|----------------|
| `SQLiteBlackboard` | 单机/个人版 | 新建 `blackboard_entries` 表于共享 `maop.db`，复用 `get_memory_db_path()` | 与三层记忆同库，事务隔离 |
| `RedisBlackboard` | 分布式/企业版 | Hash 存条目、Sorted Set 存索引、Lua 脚本保证原子变迁 | 复用现有 Redis 连接池（若有） |

`SQLiteBlackboard` 表结构（DDL）：

```sql
-- SQL：黑板条目表 DDL
CREATE TABLE IF NOT EXISTS blackboard_entries (
    id          TEXT PRIMARY KEY,           -- UUID
    domain      TEXT NOT NULL,              -- 领域命名空间
    key         TEXT NOT NULL,              -- 领域内键
    schema      TEXT NOT NULL,              -- payload schema 标识
    payload     TEXT NOT NULL,              -- JSON 序列化的知识内容
    state       TEXT NOT NULL DEFAULT 'draft',  -- EntryState 枚举值
    version     INTEGER NOT NULL DEFAULT 0, -- 乐观锁版本
    provenance  TEXT NOT NULL DEFAULT '{}', -- JSON 序列化的来源溯源
    created_at  TEXT NOT NULL,              -- ISO-8601
    updated_at  TEXT NOT NULL,              -- ISO-8601
    UNIQUE(domain, key, version)            -- 同领域同键版本唯一
);

CREATE INDEX IF NOT EXISTS idx_bb_domain_state
    ON blackboard_entries(domain, state);

CREATE INDEX IF NOT EXISTS idx_bb_schema_state
    ON blackboard_entries(schema, state);

-- SQL：黑板条目锁表 DDL
CREATE TABLE IF NOT EXISTS blackboard_locks (
    entry_id    TEXT PRIMARY KEY,
    ks_name     TEXT NOT NULL,
    locked_at   TEXT NOT NULL,
    ttl_s       REAL NOT NULL
);
```

#### 2.3.4　与三层记忆的边界划分

黑板与三层记忆职责互补，不重叠：

- **三层记忆**：解决"单个 Agent 记住什么"——对话上下文、任务经验、长期知识，面向**单 Agent 纵向记忆**。
- **黑板**：解决"多个知识源协作求解什么"——共享问题状态、中间产出、触发协调，面向**多源横向协作**。
- **交互点**：知识源在 `execute` 中可调用 `MemoryFacade` 读取长期知识作为计算输入（如根因裁决器读取历史故障经验），黑板条目本身不存于三层记忆中。

### 2.4　触发机制设计

#### 2.4.1　知识源注册

知识源与触发规则均支持运行时动态注册，无需重启：

```python
# 代码示例：知识源与规则注册（Python）
controller = get_blackboard_controller()

# 注册知识源
controller.register_ks(EntityExtractor(name="ks.entity", priority=10))
controller.register_ks(RelationExtractor(name="ks.relation", priority=5))

# 注册触发规则（使用内置工厂）
controller.register_rule(
    on_state(domain="kg.build", state=EntryState.READY, ks="ks.entity")
)
controller.register_rule(
    on_state_count(
        domain="kg.build", state=EntryState.DONE, n=2, ks="ks.merge",
    )
)

# 设置收敛条件：所有原始文本处理完毕且图谱条目不再增长
controller.set_convergence(my_convergence_predicate)

await controller.start()
```

**知识源白名单机制**（对应审核项 R-8）：

动态注册知识源存在安全风险（恶意类可能借动态注册注入危险逻辑）。除 `require_admin` 鉴权外，控制器内置白名单机制对知识源类名进行二次校验：

1. **白名单配置**：白名单配置在 `config/blackboard.yaml` 中，格式如下：

   ```yaml
   # config/blackboard.yaml
   allowed_knowledge_sources:
     - "EntityExtractor"
     - "RelationExtractor"
     - "GraphMerger"
     - "RootCauseArbiter"
   ```

2. **注册时校验**：`register_ks` 在注册知识源时校验知识源类名（`type(ks).__name__`）是否在白名单中：
   - 类名在白名单中 → 允许注册。
   - 类名不在白名单中 → 拒绝注册，抛 `KnowledgeSourceNotAllowedError`，并记录安全事件（含类名、调用方、时间戳）。
3. **白名单为空策略**：白名单为空（`allowed_knowledge_sources: []` 或未配置）时表示**允许全部**（开发模式，便于本地调试与测试）。生产环境**必须**配置非空白名单，启动时若检测到生产环境（`env == "prod"`）且白名单为空，控制器记录警告并拒绝启动。
4. **白名单变更审批**：白名单变更需经过审批流程。变更后通过 EventBus 发布 `blackboard.whitelist_changed` 事件通知审计模块：

   ```python
   # 代码示例：白名单变更事件发布（Python）
   await event_bus.publish(Event(
       topic="blackboard.whitelist_changed",
       data={
           "action": "add",
           "ks_class": "EntityExtractor",
           "operator": admin_user,
           "timestamp": "...",
       },
       source="blackboard",
   ))
   ```

   审计模块订阅该事件，记录白名单变更轨迹，供安全审查追溯。

#### 2.4.2　触发规则评估流程

图：触发规则评估与调度流程图

```
黑板状态变迁
     │
     ▼
await bus.publish(Event(topic="blackboard.changed", ...))
      │
      ▼
Controller._on_change(entry)
     │
     ├─ 1. 取出所有已注册 TriggerRule
     │
     ├─ 2. 并行评估 rule.matches(entry, bb)
     │     └─ 命中的规则集合 R = {r | r.matches == True}
     │
     ├─ 3. 按 rule.priority 降序排序 R
     │     └─ 同优先级按 ks.priority 二次排序
     │
     ├─ 4. 依赖分析：按 read_domains 分组
     │     ├─ 无交集组 → 可并发
     │     └─ 有交集组 → 串行
     │
     ├─ 5. 调度执行
     │     ├─ 检查 ks.reentrant / 是否在途
     │     ├─ lock 触发条目（独占）
     │     ├─ asyncio.create_task(ks.execute(bb, entry))
     │     └─ 超时守护（execution_timeout_s）
     │
     ├─ 6. 执行完成回调
     │     ├─ unlock 条目
     │     ├─ ks 写回黑板 → 新变迁 → 回到步骤 1（闭环）
     │     └─ 记录执行 trace（供 dashboard 审计）
     │
     └─ 7. 收敛判定
           ├─ is_converged() == True → stop
           └─ iteration >= max_iterations → 强制 stop + 告警
```

#### 2.4.3　优先级调度策略

表：优先级调度策略说明表

| 调度维度 | 排序键 | 说明 |
|----------|--------|------|
| 规则间优先级 | `rule.priority` 降序 | 同轮多规则命中时，高优先级规则的目标知识源先调度 |
| 知识源间优先级 | `ks.priority` 降序 | 同优先级规则下，高优先级知识源先执行 |
| 并发分组 | `read_domains` 交集 | 无交集可并发，有交集串行避免读写冲突 |
| 重入控制 | `ks.reentrant` | `False` 时在途则跳过/入队；`True` 可并发多实例 |
| 公平性 | 触发时间 FIFO | 同优先级按触发先后排序，避免饥饿 |

### 2.5　API 设计

黑板架构通过 dashboard 路由对外暴露管理与观测 API，遵循现有 `knowledge.py` 路由的风格（`APIRouter(prefix="/api/blackboard")` + `handle_api_errors` 装饰器 + `require_admin` 写操作鉴权）。

#### 2.5.1　路由清单

表：黑板 API 路由清单

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| `GET` | `/api/blackboard/entries` | 查询黑板条目（支持 domain/state/schema/limit 过滤） | 否 |
| `GET` | `/api/blackboard/entries/{entry_id}` | 获取单个条目详情 | 否 |
| `POST` | `/api/blackboard/entries` | 手动写入条目（注入初始知识） | 是 |
| `PATCH` | `/api/blackboard/entries/{entry_id}/state` | 手动状态变迁 | 是 |
| `GET` | `/api/blackboard/sources` | 列出已注册知识源 | 否 |
| `POST` | `/api/blackboard/sources` | 动态注册知识源（按类名+配置） | 是 |
| `GET` | `/api/blackboard/rules` | 列出已注册触发规则 | 否 |
| `POST` | `/api/blackboard/rules` | 动态注册触发规则 | 是 |
| `GET` | `/api/blackboard/controller/status` | 控制器运行状态（运行中/已收敛/迭代次数） | 否 |
| `POST` | `/api/blackboard/controller/start` | 启动控制器 | 是 |
| `POST` | `/api/blackboard/controller/stop` | 停止控制器 | 是 |
| `GET` | `/api/blackboard/trace` | 执行轨迹审计（知识源执行历史） | 否 |
| `GET` | `/api/blackboard/graph` | 知识源依赖图（用于可视化） | 否 |

#### 2.5.2　路由实现骨架

```python
# 代码示例：黑板路由骨架（Python）
from fastapi import APIRouter, Request
from pydantic import BaseModel

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

router = APIRouter(prefix="/api/blackboard", tags=["blackboard"])


class WriteEntryRequest(BaseModel):
    domain: str
    key: str
    schema: str
    payload: dict[str, Any]
    state: str = "ready"
    source_ks: str = "manual"


@router.get("/entries")
@handle_api_errors("blackboard entries")
async def list_entries(
    domain: str = "",
    state: str = "",
    schema: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    from maop.core.reliability.blackboard import get_blackboard
    bb = get_blackboard()
    entries = await bb.query(domain=domain, state=state or None,
                             schema=schema, limit=limit)
    return {"status": "ok", "data": [e.__dict__ for e in entries]}


@router.post("/entries")
@handle_api_errors("blackboard write")
async def write_entry(body: WriteEntryRequest, request: Request) -> dict[str, Any]:
    require_admin(request)
    from maop.core.reliability.blackboard import get_blackboard
    bb = get_blackboard()
    entry = await bb.write(body.domain, body.key, body.schema, body.payload,
                           source_ks=body.source_ks)
    return {"status": "ok", "data": entry.__dict__}


@router.get("/controller/status")
@handle_api_errors("blackboard controller status")
async def controller_status() -> dict[str, Any]:
    from maop.core.reliability.blackboard import get_blackboard_controller
    ctrl = get_blackboard_controller()
    return {
        "status": "ok",
        "data": {
            "running": ctrl.is_running,
            "converged": await ctrl.is_converged(),
            "iterations": ctrl.iteration_count,
            "ks_count": len(ctrl.registered_ks),
            "rule_count": len(ctrl.registered_rules),
        },
    }
```

#### 2.5.3　路由注册集成

在 `py/maop/dashboard/_register_routes.py` 的 `register_routers(app)` 函数中，参照 `knowledge` 路由的注册方式新增：

```python
# 代码示例：路由注册集成（Python）
from maop.dashboard.routers import blackboard as blackboard_router
app.include_router(blackboard_router.router)
logger.info("[server] Router: blackboard enabled")
```

## 第3章　文件清单

### 3.1　新增文件

表：新增文件清单

| 文件路径 | 职责 | 优先级 |
|----------|------|--------|
| `py/maop/core/reliability/blackboard.py` | 黑板核心：`BlackboardEntry`、`EntryState`、`Blackboard`、`SQLiteBlackboard`、`KnowledgeSource`、`TriggerRule`、`BlackboardController`、内置规则工厂、全局单例获取函数 | 高 |
| `py/maop/core/reliability/blackboard_redis.py` | Redis 后端实现：`RedisBlackboard`（企业版分布式场景，可延后实施） | 中 |
| `py/maop/dashboard/routers/blackboard.py` | 黑板 dashboard 路由：条目 CRUD、知识源/规则管理、控制器状态、执行轨迹、依赖图 | 高 |
| `docs/design-blackboard.md` | 本设计文档 | — |

### 3.2　修改文件

表：修改文件清单

| 文件路径 | 修改内容 | 影响范围 |
|----------|----------|----------|
| `py/maop/dashboard/_register_routes.py` | 在 `register_routers(app)` 中新增 `blackboard` 路由注册（4 行，参照 `knowledge` 路由模式） | 仅新增 include_router，无破坏性变更 |
| `py/maop/core/reliability/__init__.py` | 导出黑板核心类（`Blackboard`、`KnowledgeSource` 等），便于上层 `from maop.core.reliability import Blackboard` | 仅新增导出，向后兼容 |

### 3.3　数据库变更

表：数据库变更清单

| 变更 | 对象 | 说明 |
|------|------|------|
| 新增表 | `blackboard_entries` | 黑板条目存储，建于共享 `maop.db`（通过 `get_memory_db_path()`） |
| 新增表 | `blackboard_locks` | 条目独占锁表 |
| 新增索引 | `idx_bb_domain_state` | 按 domain + state 加速查询 |
| 新增索引 | `idx_bb_schema_state` | 按 schema + state 加速细粒度查询 |

> 注：所有变更均为**新增**表与索引，不修改现有 `episodic_memory`、`memory_entries` 等表结构，无破坏性 schema 迁移风险。

### 3.4　实施依赖关系

图：实施依赖关系图

```
blackboard.py (核心)
    │
    ├─ 依赖 event_bus.py (已有，复用 EventBus 分发)
    ├─ 依赖 shared_db.py (已有，复用 get_memory_db_path)
    │
    ▼
routers/blackboard.py (路由)
    │
    ├─ 依赖 blackboard.py (核心)
    ├─ 依赖 error_handler.py (已有，handle_api_errors)
    └─ 依赖 security/middleware.py (已有，require_admin)
    │
    ▼
_register_routes.py (注册)
    └─ 新增 include_router(blackboard_router.router)
```

实施顺序建议：

1. 先实施 `blackboard.py` 核心层（含 `SQLiteBlackboard` 与单元测试）。
2. 再实施 `routers/blackboard.py` 路由层并注册。
3. 最后按需实施 `blackboard_redis.py`（企业版分布式场景）。

## 第4章　设计约束与风险

### 4.1　设计约束

- **只设计不实施**：本文档不修改任何代码文件，实施阶段另行输出 patch。
- **复用优先**：不重写 EventBus 与三层记忆，黑板层在其之上构建。
- **向后兼容**：新增表与路由不影响现有功能；`__init__.py` 仅新增导出。
- **单机优先**：首版以 `SQLiteBlackboard` 为主，`RedisBlackboard` 延后。

### 4.2　风险与对策

表：风险与对策说明表

| 风险 | 影响 | 对策 |
|------|------|------|
| 知识源并发写同一条目导致丢失更新 | 数据不一致 | 乐观锁（`version` 字段）+ `transition` 的 `expected_version` 校验 |
| 触发规则链式扩散导致无限循环 | 资源耗尽 | `max_iterations` 上限 + 收敛谓词 + 每轮迭代计数告警 |
| 知识源执行超时占用条目锁 | 死锁 | `execution_timeout_s` 超时强制释放锁 + 标记 `FAILED` |
| 大量条目查询性能退化 | dashboard 响应慢 | `domain+state`、`schema+state` 复合索引 + 分页 |
| 动态注册恶意知识源 | 安全风险 | `require_admin` 鉴权 + 知识源类名白名单校验（白名单配置于 `config/blackboard.yaml`，注册时校验类名，生产环境必须配置非空白名单，详见 2.4.1 节） |

## 第5章　验收标准

1. **结构化共享知识库**：可写入带 schema 的条目，按 domain/key/state 查询，版本自增，乐观锁冲突可检测。
2. **多知识源异步读写**：≥2 个知识源可并发读写黑板，无交集可并发、有交集串行，无丢失更新。
3. **状态变化事件驱动**：条目状态变迁自动产生 `blackboard.changed` 事件，控制器收到并评估规则。
4. **知识源注册与触发规则**：运行时可注册知识源与规则，规则命中后按优先级调度，收敛条件满足后停止。
5. **API 可用**：`/api/blackboard/*` 路由全部可用，写操作需 admin 鉴权，读操作开放。
6. **与现有系统共存**：EventBus、三层记忆、现有路由不受影响，`maop.db` 新增表不破坏现有表。