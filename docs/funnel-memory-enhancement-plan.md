# 漏斗式记忆机制完善方案

> 状态：待审核  
> 日期：2026-08-24  
> 作者：Team Leader  
> 关联模块：`py/maop/memory/{evidence,atoms,symbolic,llm_dedup}.py`

## 一、背景

MAOP 已实现对齐 TencentDB Agent Memory 的漏斗式记忆增强（L0 证据 → L1 原子事实 → L3 向量晋升 + 符号化短期记忆）。核心机制设计精良，但在 **API 暴露、前端 UI、agent 模式支持、文档、性能、代码细节** 6 个方面需要完善。

## 二、完善范围（按优先级）

### P1-A：后端 API 端点暴露

**现状**：`routers/memory.py`（368行）只有 L2/L3 记忆端点，无漏斗记忆端点。

**方案**：在 `routers/memory.py` 中新增漏斗记忆端点（复用现有 router，避免新增文件碎片化）。

| 端点 | 方法 | 功能 | 权限 |
|------|------|------|------|
| `/api/memory/funnel/stats` | GET | L0+L1+符号化三层统计 | require_admin |
| `/api/memory/funnel/evidence` | GET | L0 证据列表（分页+搜索+kind过滤） | require_admin |
| `/api/memory/funnel/evidence/{ref_id}` | GET | L0 证据原文回查 | require_admin |
| `/api/memory/funnel/evidence/{ref_id}` | DELETE | 删除单条证据 | require_admin |
| `/api/memory/funnel/evidence/prune` | POST | 批量清理过期证据 | require_admin |
| `/api/memory/funnel/facts` | GET | L1 原子事实列表（分页+搜索+topic过滤） | require_admin |
| `/api/memory/funnel/facts/{fact_id}` | GET | 单条事实详情 | require_admin |
| `/api/memory/funnel/facts/promote` | POST | 晋升高频事实到 L3 | require_admin |
| `/api/memory/funnel/task-map/{session_id}` | GET | Mermaid 任务状态图 | require_admin |
| `/api/memory/funnel/task-map/{session_id}/nodes` | GET | 任务图节点明细 | require_admin |
| `/api/memory/funnel/task-map/{session_id}` | DELETE | 清空会话任务图 | require_admin |

**实现要点**：
- 通过 `MemoryFacade` 的透传 API（`evidence_store`/`atom_facts`/`symbolic`）访问
- 使用 `handle_api_errors` 装饰器统一错误处理
- 分页参数：`?page=1&page_size=20`，返回 `{items, total, page, page_size}`
- agent 模式下 facade 返回 None/空，API 返回空列表而非 404

### P1-B：前端 UI 面板

**现状**：`views/ThreeLayerMemory.vue` 只展示 L1/L2/L3 三层记忆，无漏斗增强视图。

**方案**：新建 `views/FunnelMemory.vue`，路由 `/funnel-memory`。

**UI 结构**：
```
┌─────────────────────────────────────────────┐
│  PageHeader [刷新]                          │
├─────────────────────────────────────────────┤
│  StatCard × 4：L0总数 | L1总数 | 任务图会话数 | 外置文件数 │
├─────────────────────────────────────────────┤
│  Tab: L0 证据 | L1 原子事实 | 任务状态图      │
├─────────────────────────────────────────────┤
│  [L0 证据 Tab]                              │
│   搜索框 + kind 过滤 + 分页表格              │
│   列：ref_id | kind | summary | source | 时间 │
│   操作：回查原文(modal) | 删除               │
│   [清理过期] 按钮 (>90天)                    │
├─────────────────────────────────────────────┤
│  [L1 原子事实 Tab]                          │
│   搜索框 + topic 过滤 + 分页表格              │
│   列：subject | predicate | object | confidence | access_count │
│   Top 5 高频事实卡片                         │
│   [晋升到 L3] 按钮 (min_access=3)           │
├─────────────────────────────────────────────┤
│  [任务状态图 Tab]                           │
│   会话选择器                                 │
│   Mermaid 渲染区域 (mermaid.js)              │
│   节点明细表格                               │
└─────────────────────────────────────────────┘
```

**实现要点**：
- 复用现有组件：`PageHeader`、`StatCard`、`Card`、`EmptyState`、`AppIcon`
- i18n：新增 `view.funnel.*` key（中英文）
- Mermaid 渲染：动态 import mermaid.js（已有依赖）
- 路由守卫：enterprise only（与 ThreeLayerMemory 一致）

### P2-A：agent 模式支持

**现状**：`facade.py` 中 agent 模式（`ThreeLayerMemory`）返回 None/False，漏斗增强不可用。

**方案**：在 `MemoryFacade` 中，agent 模式也懒加载独立的漏斗组件实例。

```python
# facade.py 修改
def evidence_store(self):
    """L0 证据层实例（chat + agent 模式均可用）。"""
    existing = getattr(self._impl, "evidence_store", None)
    if existing is not None:
        return existing
    # agent 模式：创建独立实例
    if self._mode == "agent":
        if self._funnel_evidence is None:
            try:
                from maop.memory.evidence import EvidenceStore
                self._funnel_evidence = EvidenceStore(root_dir=self._root)
            except Exception:
                pass
        return self._funnel_evidence
    return None
```

同理修改 `atom_facts`、`symbolic` 属性。

### P2-B：独立架构文档

**方案**：新建 `docs/funnel-memory-design.md`，内容包括：
- 设计哲学（对齐 TencentDB Agent Memory 漏斗哲学）
- 三层架构图（L0→L1→L3 + 符号化短期记忆）
- 各层职责、Schema、API
- 回查链路说明
- LLM 语义去重方案A 说明
- 配置开关（`MAOP_LLM_DEDUP`、`MAOP_DB_PER_MODULE`）
- 性能考量与限制

### P3-A：L1 检索性能优化

**现状**：`search_facts` 用 `LIKE %query%` 全表扫描，无法利用索引。

**方案**：为 `atom_facts` 表添加 FTS5 虚拟表。

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS atom_facts_fts USING fts5(
    subject, predicate, object_value, topic,
    content='atom_facts', content_rowid='rowid'
);
-- 通过触发器同步（或应用层写入时同步）
```

`search_facts` 改用：
```sql
SELECT a.* FROM atom_facts a
JOIN atom_facts_fts f ON a.rowid = f.rowid
WHERE atom_facts_fts MATCH ?
ORDER BY rank LIMIT ?
```

**注意**：FTS5 在 SQLite 中需编译启用，需确认项目的 SQLite 支持 FTS5（大多数发行版默认支持）。

### P3-B：refs 文件自动清理

**现状**：`evidence.py` 有 `prune()` 方法但非自动触发。

**方案**：在 `MemoryManager.consolidate()` 末尾触发 L0 清理。

```python
def consolidate(self, ...):
    # ... 现有 L2→L3 合并逻辑 ...
    
    # 顺带清理 L0 过期证据（与 consolidation 同周期）
    if self.evidence_store is not None:
        try:
            pruned = self.evidence_store.prune(older_than_days=90)
            if pruned:
                logger.info("[memory_manager] L0 prune: %d 条过期证据已清理", pruned)
        except Exception as exc:
            logger.warning("[memory_manager] L0 prune failed: %s", exc)
```

### P3-C：3 个代码细节修复

#### 1. `search_facts` 命中计数范围限制
**问题**：`atoms.py:421-426` 的 `UPDATE ... SET access_count = access_count + 1` 没有 LIMIT，单字符 query 会命中大量行。
**修复**：改为只对返回的 top N 条目计数：
```python
# 先查询得到结果，再对结果的 ID 批量更新 access_count
if query and results:
    ids = [r["id"] for r in results]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE atom_facts SET access_count = access_count + 1 WHERE id IN ({placeholders})", ids)
```

#### 2. `symbolic.py` Mermaid 注入风险
**问题**：`description` 只做了 `replace('"', "'")`，未防 Mermaid 语法注入。
**修复**：增加 Mermaid 关键字过滤：
```python
def _safe_label(desc: str) -> str:
    """清洗 Mermaid 标签：去引号 + 截断 + 过滤语法字符。"""
    s = desc.replace('"', "'").replace("\n", " ")
    # 过滤 Mermaid 语法字符
    for kw in ("-->", "---", "-0", "class", "end", "subgraph", "graph"):
        s = s.replace(kw, kw.replace("-", "‐"))  # 用连字符替代
    return s[:80]  # 截断防长标签
```

#### 3. `promote_facts` 签名一致性确认
**问题**：`atoms.py:478` 调用 `vector_index_fn(row["id"], text, {...})` 是3参数，需确认 `manager.py` 传递的函数签名匹配。
**修复**：在 `manager.py` 的 `promote_atom_facts` 方法中确认 `long_term_index` 的签名，如不匹配则包装适配。

## 三、实施计划

| 阶段 | 任务 | 文件 | 预计工时 |
|------|------|------|----------|
| 1 | P3-C 代码细节修复 | `atoms.py`、`symbolic.py`、`manager.py` | 0.5h |
| 2 | P1-A API 端点 | `routers/memory.py` | 1.5h |
| 3 | P1-B 前端 UI | `FunnelMemory.vue`、`router/index.js`、i18n | 2h |
| 4 | P2-A agent 模式 | `facade.py` | 0.5h |
| 5 | P3-B 自动清理 | `manager.py` | 0.5h |
| 6 | P2-B 架构文档 | `docs/funnel-memory-design.md` | 1h |
| 7 | P3-A FTS5 优化 | `atoms.py` | 1h（可选，需确认 FTS5 可用） |
| 8 | 测试 + 验证 | `test_funnel_memory.py` + 全量测试 | 1h |

**总计**：约 8h（P3-A FTS5 可选，不做则 7h）

## 四、风险与限制

1. **FTS5 可用性**：需确认项目的 SQLite 编译了 FTS5 扩展。如不可用，P3-A 跳过，保留 LIKE 查询。
2. **Mermaid.js 依赖**：前端 Mermaid 渲染需确认 `package.json` 已有 mermaid 依赖。如无则需安装。
3. **agent 模式 DB 隔离**：agent 模式创建独立漏斗组件时，需确认 DB 路径一致（共用 `maop.db`）。
4. **API 权限**：所有端点使用 `require_admin`，与现有 `memory.py` 端点一致。

## 五、验收标准

- [ ] 全量后端测试 0 failed（7266+ passed）
- [ ] 前端测试 351+ passed
- [ ] `ruff check` 0 error
- [ ] `mypy` 无新增 error
- [ ] API 端点可用（curl 验证）
- [ ] 前端面板可访问（`/funnel-memory` 路由）
- [ ] agent 模式漏斗增强可用
- [ ] 架构文档完整