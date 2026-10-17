# ADR-030: Workflow Visual Builder（F3-02）骨架

## Status

Proposed

## Context

MAOP 的工作流编排依赖 YAML/代码定义（`agents.yaml`、`config/` 下的各种 YAML），非技术用户无法自助构建。F3-02 引入可视化 DAG 编辑器。

### 现状

| 已有能力 | 位置 | F3-02 复用方式 |
|----------|------|----------------|
| `DAGraph.vue`（SVG 渲染） | `dashboard-enterprise/src/components/DagGraph.vue` | DAG 只读视图 → 扩展为可视化编辑画布 |
| `DagProgressEmitter` | `py/maop/core/agent/dag/dag_progress_emitter.py` | DAG 节点状态 → 编辑器节点类型契约 |
| `execution_service` | `py/maop/dashboard/services/execution_service.py` | DAG YAML 提交执行 |
| Workflow YAML 结构 | `agents.yaml` + `config/` 下 YAML | 双向编译目标 |

### 缺失能力

- 无可视化拖拽编辑器
- 无 Visual DAG → YAML 编译器
- 无 YAML → Visual DAG 反向渲染器
- 无节点配置面板（数据 A3.2 R02）

## Decision

引入 Visual Builder MVP 骨架（三层分离：前端画布 + 后端编译器 + 校验层）：

### D1. 编译器（Backend, Python）

**模块**: `py/maop/core/visual_builder/compiler.py`

```python
class VisualDAGCompiler:
    """Visual DAG JSON → MAOP YAML compiler."""
    def compile(self, visual_dag: VisualDAG) -> str: ...
    def decompile(self, yaml_str: str) -> VisualDAG: ...
    def validate(self, visual_dag: VisualDAG) -> list[ValidationError]: ...

class VisualDAGValidator:
    """DAG 合法性校验：环检测、类型匹配、必填项."""
    def detect_cycles(self, nodes, edges) -> bool: ...
```

**数据契约**:

```python
class VisualNode(BaseModel):
    id: str
    node_type: str  # "agent" | "tool" | "gateway" | "condition"
    label: str
    config: dict[str, Any] = Field(default_factory=dict)
    position: tuple[float, float] = (0, 0)

class VisualEdge(BaseModel):
    source: str
    target: str
    label: str = ""
    condition: str = ""

class VisualDAG(BaseModel):
    name: str = "untitled"
    nodes: list[VisualNode] = Field(default_factory=list)
    edges: list[VisualEdge] = Field(default_factory=list)
    version: int = 1
```

### D2. 前端画布（Frontend, Vue 3）

**组件**: `dashboard-enterprise/src/views/VisualBuilder.vue` + `components/VisualBuilderCanvas.vue`

- MVP 实现最小拖拽流（HTML5 drag API，不引入 VueFlow 依赖以控制复杂度）
- 节点面板 / 画布 / 配置面板三段式布局
- 实时校验（debounce 300ms 调后端 validate）

### D3. API 端点

```
POST /api/visual-builder/compile     → YAML
POST /api/visual-builder/decompile   → Visual JSON
POST /api/visual-builder/validate    → {valid: bool, errors: list}
GET  /api/visual-builder/templates   → 模板库
```

### D4. 版本化

编译产出的 YAML 纳入 `config_history`（`core/config/config_history.py` 已有），保留 diff/revert。

## Consequences

### Positive

- 非技术用户 30 分钟内构建首个 DAG
- YAML 作为底层输出（Git 友好），可 diff/revert
- 往返一致性确保双向编辑不丢信息

### Negative

- MVP 阶段仅支持 Agent/Tool/Gateway/Condition 四种节点类型
- 100 节点 DAG 拖拽 60fps 需后续引入虚拟化（MVP 支持 ≤ 50 节点）

## Related

- PRD: F3-02 Workflow Visual Builder（PRD 5.3）
- HLD: §4.2 Visual Builder Architecture（HLD 4.2）
- 复用: `DagGraph.vue`、`config_history.py`
