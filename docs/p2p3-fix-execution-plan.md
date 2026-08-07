# P2-P3 暂缓项修复执行计划

> **文档版本**: v1.0
> **生成日期**: 2026-08-07
> **适用范围**: MAOP 项目 P2-P3 暂缓项 M3 / M4 / M5 / M8
> **文档性质**: 修复方案设计文档（不执行实际修复）

## 第1章 概述

### 1.1 背景

P2-P3 问题核对后，4 项暂缓问题需要设计修复方案。本文档对每项问题进行代码分析、方案设计、风险评估和可行性确认，作为后续执行修复的依据。

### 1.2 修复项清单

| 编号 | 问题 | 文件 | 当前状态 | 风险 |
|------|------|------|----------|------|
| M3 | 向量搜索全量加载到内存 | `py/maop/core/vector.py`、`py/maop/core/memory/vector.py` | 部分修复 | 中 |
| M4 | data.py `vs.list_all()` 全量加载 | `py/maop/dashboard/routers/data.py` | 部分修复 | 低 |
| M5 | 同步 sqlite3 阻塞事件循环 | `py/maop/core/cost_tracker.py`、`py/maop/core/tenant.py` | 部分修复 | 中 |
| M8 | deploy.py 仅本地拉起，不校验就绪 | `py/maop/deploy.py` | 部分修复 | 低 |

### 1.3 建议执行顺序

1. **M8**（风险最低，独立性最强，不涉及核心数据路径）
2. **M4**（风险低，前后端兼容性好，已有分页基础设施）
3. **M5**（风险中，涉及 async 路径改造，需测试事件循环）
4. **M3**（风险中，涉及核心搜索路径，需充分回归测试）

## 第2章 M3 — 向量搜索 `_load_cache` 分页

### 2.1 当前代码分析

#### 2.1.1 涉及文件

- `py/maop/core/vector.py` 第 485-503 行（基础版 VectorStore）
- `py/maop/core/memory/vector.py` 第 801-819 行（分层版 VectorStore，含 HNSW/sqlite-vec/NumPy 三级回退）

两个文件的 `_load_cache()` 实现完全相同。

#### 2.1.2 问题代码

```python
# py/maop/core/memory/vector.py 第 801-819 行
def _load_cache(self) -> None:
    """Load all vectors, text, and metadata from SQLite into memory cache.

    P2 fix: Added cache size limit to prevent unbounded memory growth.
    For datasets > 50K vectors, consider using sqlite-vec or faiss for ANN indexing.

    Batch-loads all columns in a single query to avoid N+1 per-entry lookups
    during search_vector(). Also populates _text_cache and _meta_cache.
    """
    try:
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT id, vector, text, metadata FROM vector_entries"
            ).fetchall():
                self._cache[row["id"]] = json.loads(row["vector"])
                self._text_cache[row["id"]] = row["text"] or ""
                self._meta_cache[row["id"]] = json.loads(row["metadata"] or "{}")
    except Exception as exc:
        logger.warning("[vector] Cache load failed: %s", exc)
```

#### 2.1.3 当前状态评估

| 已有措施 | 实际生效 | 说明 |
|----------|----------|------|
| `_cache_max_size = 50000` 限制 | ❌ 未生效 | 仅定义了属性，`_load_cache()` 中从未检查或使用此限制 |
| sqlite-vec ANN 索引优先路径 | ✅ 生效 | `search_vector()` 优先尝试 `_search_vector_sqlite_vec()`，避免遍历缓存 |
| NumPy 加速 | ✅ 生效 | `_search_vector_numpy()` 批量矩阵运算 |
| HNSW 分层（memory 版） | ✅ 生效 | 100K 以上向量走 HNSW |

**核心问题**: 虽然 sqlite-vec/HNSW 路径不依赖 `_cache`，但 NumPy 和纯 Python 回退路径需要 `_cache`。且 `_search_vector_hnsw()` 在第 703-704 行也会调用 `_load_cache()` 作为重建索引的数据源。当数据集超过可用内存时，`_load_cache()` 仍然会 OOM。

### 2.2 修复方案

#### 2.2.1 方案选择

采用 **方案 A：分页加载 + `_cache_max_size` 实际生效**。

理由：
- sqlite-vec/HNSW 路径不需要全量缓存，分页加载不影响其性能
- NumPy/Python 回退路径在超过 50K 时本就不推荐使用（注释已说明）
- 改动最小，保持现有 API 兼容

#### 2.2.2 具体代码修改

**文件 1**: `py/maop/core/memory/vector.py` 第 801-819 行

```python
def _load_cache(self) -> None:
    """Load vectors, text, and metadata from SQLite into memory cache.

    P2-P3 fix: 分页加载，遵守 _cache_max_size 限制，防止大数据集 OOM。
    - 当总条数 <= _cache_max_size 时，全量加载（保持原行为）
    - 当总条数 > _cache_max_size 时，仅加载最近的 _cache_max_size 条
      （按 created_at DESC 排序，优先保留新数据）
    - sqlite-vec/HNSW 路径不依赖 _cache，不受此限制影响
    - NumPy/Python 回退路径仅搜索缓存中的向量（已知限制）
    """
    try:
        with self._connect() as conn:
            # 先查总数
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM vector_entries"
            ).fetchone()["cnt"]

            if total <= self._cache_max_size:
                # 小数据集：全量加载（保持原行为）
                rows = conn.execute(
                    "SELECT id, vector, text, metadata FROM vector_entries"
                ).fetchall()
            else:
                # 大数据集：仅加载最近的 _cache_max_size 条
                logger.warning(
                    "[vector] Dataset %d > cache_max_size %d, "
                    "loading only recent %d entries (use sqlite-vec/HNSW for full search)",
                    total, self._cache_max_size, self._cache_max_size,
                )
                rows = conn.execute(
                    "SELECT id, vector, text, metadata FROM vector_entries "
                    "ORDER BY created_at DESC LIMIT ?",
                    (self._cache_max_size,),
                ).fetchall()

            for row in rows:
                self._cache[row["id"]] = json.loads(row["vector"])
                self._text_cache[row["id"]] = row["text"] or ""
                self._meta_cache[row["id"]] = json.loads(row["metadata"] or "{}")
    except Exception as exc:
        logger.warning("[vector] Cache load failed: %s", exc)
```

**文件 2**: `py/maop/core/vector.py` 第 485-503 行 — 同样修改。

#### 2.2.3 可选增强（不在本次范围）

- 添加 `_load_cache_page(page_size, page_num)` 方法，支持按需分页加载
- 在 `search_vector()` 中根据 tier 选择性加载（sqlite-vec 路径跳过 `_load_cache`）

### 2.3 风险评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 风险等级 | **中** | 涉及核心搜索路径 |
| 功能影响 | NumPy/Python 回退路径在 >50K 时仅搜索最近 50K 条 | sqlite-vec/HNSW 路径不受影响 |
| 兼容性 | 完全兼容 | 小数据集行为不变 |
| 性能影响 | 正向 | 大数据集避免 OOM，启动更快 |
| 回滚难度 | 易 | 还原 SQL 即可 |

### 2.4 验证步骤

```bash
# 1. 单元测试（如有）
cd F:\Nexus\MAOP\py
python -m pytest tests/ -k vector -v

# 2. 手动验证小数据集行为不变
python -c "
from maop.core.memory.vector import VectorStore
vs = VectorStore()
for i in range(100):
    vs.index(f'doc{i}', f'text {i}')
vs._load_cache()
assert len(vs._cache) == 100, f'Expected 100, got {len(vs._cache)}'
print('OK: small dataset full load')
"

# 3. 手动验证大数据集分页（模拟 >50K，可调小 _cache_max_size 测试）
python -c "
from maop.core.memory.vector import VectorStore
vs = VectorStore()
vs._cache_max_size = 10  # 测试用小值
for i in range(100):
    vs.index(f'doc{i}', f'text {i}')
vs._cache.clear()
vs._load_cache()
assert len(vs._cache) == 10, f'Expected 10, got {len(vs._cache)}'
print('OK: large dataset paginated load')
"

# 4. 验证搜索仍可用
python -c "
from maop.core.memory.vector import VectorStore
vs = VectorStore()
results = vs.search('test', top=5)
print(f'OK: search returned {len(results)} results')
"
```

### 2.5 可行性结论

**✅ 可行**。方案改动小，保持 API 兼容，sqlite-vec/HNSW 主路径不受影响，仅 NumPy/Python 回退路径在大数据集时降级为最近 N 条搜索（已有注释说明此场景应使用 sqlite-vec/HNSW）。

---

## 第3章 M4 — data.py `list_all` 分页

### 3.1 当前代码分析

#### 3.1.1 涉及文件

- `py/maop/dashboard/routers/data.py` 第 229-238 行（API 端点）
- `py/maop/core/memory/vector.py` 第 871-917 行（`list_all()` 实现，**已支持分页**）
- `dashboard-enterprise/src/views/VectorSearch.vue` 第 187 行（前端调用）

#### 3.1.2 问题代码

```python
# py/maop/dashboard/routers/data.py 第 229-238 行
@router.get("/api/vector/list")
async def api_vector_list() -> dict[str, Any]:
    try:
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(get_db_path("vectors")))
        items = vs.list_all() if hasattr(vs, "list_all") else []  # ← 未传递分页参数
        return {"vectors": items, "count": len(items)}
    except Exception as exc:
        logger.error('Vector list failed: %s', exc)
        return {"vectors": [], "count": 0, "status": "error", "error": "Vector list unavailable"}
```

#### 3.1.3 已有基础设施

`memory/vector.py` 的 `list_all()` **已经支持分页**：

```python
# py/maop/core/memory/vector.py 第 871-917 行
def list_all(
    self,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 10_000))  # Clamp 1..10000
    offset = max(0, int(offset))
    # ... SQL with LIMIT ? OFFSET ?
```

#### 3.1.4 前端调用

```javascript
// dashboard-enterprise/src/views/VectorSearch.vue 第 187 行
const v = await api.get('/api/vector/list');  // ← 未传递分页参数
vectors.value = v.vectors || [];
```

前端 `VectorSearch.vue` 使用 `DataTable` 组件展示向量列表，当前无分页控件，仅展示首屏数据。

#### 3.1.5 当前状态评估

| 维度 | 状态 | 说明 |
|------|------|------|
| `list_all()` 分页支持 | ✅ 已实现 | 默认 limit=1000，最大 10000 |
| API 端点暴露分页参数 | ❌ 未暴露 | `api_vector_list()` 无 limit/offset 参数 |
| 前端传递分页参数 | ❌ 未传递 | Vue 组件无分页控件 |
| OOM 风险 | ⚠️ 已缓解 | 默认 limit=1000 已避免全量加载 |

**核心问题**: 虽然 `list_all()` 默认 limit=1000 已避免 OOM，但 API 层未暴露分页参数，前端无法翻页查看完整数据。

### 3.2 修复方案

#### 3.2.1 方案选择

采用 **方案 A：API 层暴露分页参数 + 前端添加分页控件（可选）**。

理由：
- `list_all()` 已有分页基础设施，仅需 API 层透传
- 前端 `DataTable` 组件可能已支持分页（需确认），即使不支持，后端默认 limit=1000 也安全
- 改动最小，向后兼容

#### 3.2.2 具体代码修改

**文件 1**: `py/maop/dashboard/routers/data.py` 第 229-238 行

```python
@router.get("/api/vector/list")
async def api_vector_list(
    limit: int = Query(1000, ge=1, le=10000, description="最大返回条数 (1..10000)"),
    offset: int = Query(0, ge=0, description="跳过条数 (>=0)"),
) -> dict[str, Any]:
    """列出已索引向量（分页）。

    P2-P3 fix: 暴露 limit/offset 分页参数，避免全量加载。
    - limit: 1..10000，默认 1000
    - offset: >=0，默认 0
    - 返回 total 字段，便于前端分页控件计算总页数
    """
    try:
        from maop.core.memory.vector import VectorStore
        vs = VectorStore(db_path=str(get_db_path("vectors")))
        if hasattr(vs, "list_all"):
            items = vs.list_all(limit=limit, offset=offset)
            total = vs.count() if hasattr(vs, "count") else len(items)
        else:
            items = []
            total = 0
        return {
            "vectors": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:
        logger.error('Vector list failed: %s', exc)
        return {"vectors": [], "count": 0, "total": 0, "status": "error", "error": "Vector list unavailable"}
```

**文件 2**（可选，前端）: `dashboard-enterprise/src/views/VectorSearch.vue` 第 187 行

```javascript
// 修改前
const v = await api.get('/api/vector/list');

// 修改后（添加分页状态）
const vecPage = ref(1);
const vecPageSize = ref(1000);
const vecTotal = ref(0);

async function loadVectors() {
  vecLoading.value = true;
  try {
    const offset = (vecPage.value - 1) * vecPageSize.value;
    const v = await api.get(`/api/vector/list?limit=${vecPageSize.value}&offset=${offset}`);
    vectors.value = v.vectors || [];
    vecTotal.value = v.total || 0;
  } catch {
    vectors.value = [];
    vecTotal.value = 0;
  } finally {
    vecLoading.value = false;
  }
}

// onMounted 中调用 loadVectors()
onMounted(async () => {
  // ... stats loading ...
  await loadVectors();
});
```

> **注**: 前端修改为可选项。后端默认 limit=1000 已保证安全。前端是否添加分页控件取决于产品需求，本次修复可仅做后端，前端保持首屏 1000 条展示。

#### 3.2.3 兼容性分析

| 调用方 | 兼容性 | 说明 |
|--------|--------|------|
| 前端 `VectorSearch.vue` | ✅ 完全兼容 | 不传 limit/offset 时使用默认值 1000/0 |
| `VectorSearch.test.js` | ✅ 完全兼容 | mock 返回 `{ vectors: [] }`，新增字段不影响 |
| 其他 API 调用者 | ✅ 完全兼容 | 新增查询参数有默认值 |

### 3.3 风险评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 风险等级 | **低** | 仅 API 层透传，`list_all()` 已有分页 |
| 功能影响 | 正向 | 暴露分页能力，前端可翻页 |
| 兼容性 | 完全兼容 | 新增参数有默认值 |
| 性能影响 | 正向 | 明确分页，避免歧义 |
| 回滚难度 | 易 | 还原参数即可 |

### 3.4 验证步骤

```bash
# 1. 启动 dashboard
cd F:\Nexus\MAOP\py
python -m uvicorn maop.dashboard.server:app --host 127.0.0.1 --port 9079

# 2. 验证默认分页
curl "http://127.0.0.1:9079/api/vector/list"
# 预期: { "vectors": [...], "count": N, "total": M, "limit": 1000, "offset": 0 }

# 3. 验证自定义分页
curl "http://127.0.0.1:9079/api/vector/list?limit=10&offset=20"
# 预期: { "vectors": [...10 items...], "count": 10, "total": M, "limit": 10, "offset": 20 }

# 4. 验证边界
curl "http://127.0.0.1:9079/api/vector/list?limit=0"
# 预期: 422 Validation Error (ge=1)

curl "http://127.0.0.1:9079/api/vector/list?limit=10001"
# 预期: 422 Validation Error (le=10000)

# 5. 前端验证
# 打开 VectorSearch 页面，确认列表正常加载
```

### 3.5 可行性结论

**✅ 可行**。`list_all()` 已有分页基础设施，API 层仅需透传参数，完全向后兼容，前端可选择性添加分页控件。

---

## 第4章 M5 — cost_tracker/tenant `asyncio.to_thread` 包装

### 4.1 当前代码分析

#### 4.1.1 涉及文件

- `py/maop/core/cost_tracker.py` 第 140-185 行（`record()` 方法）
- `py/maop/core/tenant.py` 第 148-180 行（`check_quota()` 方法）

#### 4.1.2 问题代码 — cost_tracker

```python
# py/maop/core/cost_tracker.py 第 140-185 行
def record(
    self,
    *,
    session_id: str = "",
    agent: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    metadata: dict[str, Any] | None = None,
) -> CostEntry:
    """Record a single LLM call's token usage and calculate cost."""
    # ... 计算 cost_usd, 构造 entry ...
    import json
    with sqlite_connect(self._db_path) as conn:  # ← 第 173 行：同步 sqlite3
        conn.execute(
            """INSERT INTO cost_entries ... VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (...),
        )
    self._check_budget()  # ← 内部也用同步 sqlite3
    return entry
```

#### 4.1.3 问题代码 — tenant

```python
# py/maop/core/tenant.py 第 148-180 行
def check_quota(self, tenant_id: str, *, tokens_used: int = 0, requests_used: int = 0) -> bool:
    from datetime import datetime, timezone
    config = self.get_tenant(tenant_id)  # ← 内部同步 sqlite3
    if not config or not config.enabled:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    validate_identifier("tenant_usage", "table")
    with sqlite_connect(self._db_path) as conn:  # ← 第 156 行：同步 sqlite3
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tenant_usage WHERE tenant_id = ? AND date = ?",
            (tenant_id, today),
        ).fetchone()
        # ... 配额检查 ...
        conn.execute(
            """INSERT INTO tenant_usage ... ON CONFLICT ... DO UPDATE SET ...""",
            (tenant_id, today, tokens_used, requests_used),
        )
    return True
```

#### 4.1.4 调用路径分析

**cost_tracker.record() 调用路径**:

| 调用点 | 文件 | 行号 | 上下文 | 是否 async | 是否阻塞 |
|--------|------|------|--------|------------|----------|
| `_record_cost()` | `py/maop/core/llm_provider.py` | 852 | 被 `chat_with_fallback` (async) 调用 | ✅ async 上下文 | ⚠️ 阻塞 |
| `record_cost()` 路由 | `py/maop/dashboard/routers/cost.py` | 96 | FastAPI async 路由 | ✅ async 上下文 | ⚠️ 阻塞 |

**`_record_cost` 调用链**:
```
chat_with_fallback() [async]           # llm_provider.py 第 782 行
  └─ _record_cost(resp, kwargs) [sync] # llm_provider.py 第 794/828 行
       └─ get_cost_tracker().record()  # llm_provider.py 第 852 行 [sync sqlite3] ⚠️
```

**tenant.check_quota() 调用路径**:

| 调用点 | 文件 | 行号 | 上下文 | 是否 async | 是否阻塞 |
|--------|------|------|--------|------------|----------|
| 无实际调用 | — | — | 仅文档示例 | — | — |

> **注**: `check_quota` 当前无实际调用点（搜索仅找到文档示例），但作为公开 API，未来可能在 async 中间件中被调用，仍需修复。

#### 4.1.5 已有 `asyncio.to_thread` 模式

项目中已广泛使用 `asyncio.to_thread` 包装同步操作（59 处匹配），包括：
- `circuit_breaker.py`: 所有 async 方法通过 `asyncio.to_thread` 委托给同步方法
- `sandbox.py`: `await asyncio.to_thread(self.get, sandbox_id)`
- `mcp_hub_transport.py`: HTTP 调用通过 `asyncio.to_thread` 包装
- `data_proxy.py`: 文件读取通过 `asyncio.to_thread` 包装

**模式参考**（来自 `circuit_breaker.py`）:
```python
# 同步方法
def record_success(self, agent_name: str) -> bool: ...

# async 包装
async def record_success_async(self, agent_name: str) -> bool:
    return await asyncio.to_thread(self.record_success, agent_name)
```

### 4.2 修复方案

#### 4.2.1 方案选择

采用 **方案 A：添加 async 版本方法 + 调用点改为 await async 版本**。

理由：
- 保持同步方法不变，向后兼容（非 async 调用者仍可用）
- 与项目现有模式一致（circuit_breaker 已用此模式）
- 改动集中在调用点，风险可控

#### 4.2.2 具体代码修改

**文件 1**: `py/maop/core/cost_tracker.py` — 添加 async 版本

在 `record()` 方法后添加：

```python
async def record_async(
    self,
    *,
    session_id: str = "",
    agent: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    metadata: dict[str, Any] | None = None,
) -> CostEntry:
    """Async version of record() — wraps sync sqlite3 via asyncio.to_thread.

    P2-P3 fix: 避免在 async 路径中阻塞事件循环。
    在 async 上下文中调用此方法而非 record()。
    """
    import asyncio
    return await asyncio.to_thread(
        self.record,
        session_id=session_id,
        agent=agent,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        metadata=metadata,
    )
```

同样为 `budget_status()` 添加 async 版本（被 `_check_budget` 内部调用，且 `cost.py` 路由可能调用）:

```python
async def budget_status_async(self) -> BudgetStatus:
    """Async version of budget_status()."""
    import asyncio
    return await asyncio.to_thread(self.budget_status)
```

**文件 2**: `py/maop/core/tenant.py` — 添加 async 版本

在 `check_quota()` 方法后添加：

```python
async def check_quota_async(
    self,
    tenant_id: str,
    *,
    tokens_used: int = 0,
    requests_used: int = 0,
) -> bool:
    """Async version of check_quota() — wraps sync sqlite3 via asyncio.to_thread.

    P2-P3 fix: 避免在 async 路径中阻塞事件循环。
    在 async 上下文中调用此方法而非 check_quota()。
    """
    import asyncio
    return await asyncio.to_thread(
        self.check_quota,
        tenant_id,
        tokens_used=tokens_used,
        requests_used=requests_used,
    )
```

**文件 3**: `py/maop/core/llm_provider.py` — 修改 `_record_cost` 为 async

```python
# 修改前 (第 844-863 行)
def _record_cost(resp: LLMResponse, kwargs: dict[str, Any]) -> None:
    try:
        from maop.core.monitoring.cost_tracker import get_cost_tracker
        get_cost_tracker().record(...)
    except Exception as exc:
        logger.warning("[llm_provider] CostTracker record failed: %s", exc)

# 修改后
async def _record_cost(resp: LLMResponse, kwargs: dict[str, Any]) -> None:
    """Auto-record LLM call metrics to CostTracker (best-effort, async)."""
    try:
        from maop.core.monitoring.cost_tracker import get_cost_tracker
        tracker = get_cost_tracker()
        if hasattr(tracker, "record_async"):
            await tracker.record_async(
                model=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                session_id=str(kwargs.get("session_id", "")),
                agent=str(kwargs.get("agent", "")),
                metadata={"provider": resp.provider} if resp.provider else None,
            )
        else:
            # Fallback: 同步调用（非 async 上下文兼容）
            tracker.record(
                model=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                session_id=str(kwargs.get("session_id", "")),
                agent=str(kwargs.get("agent", "")),
                metadata={"provider": resp.provider} if resp.provider else None,
            )
    except Exception as exc:
        logger.warning("[llm_provider] CostTracker record failed: %s", exc)
```

**修改调用点**（`llm_provider.py` 第 794, 828 行）:

```python
# 修改前
_record_cost(resp, kwargs)

# 修改后
await _record_cost(resp, kwargs)
```

**文件 4**: `py/maop/dashboard/routers/cost.py` — 修改 `record_cost` 路由

```python
# 修改前 (第 91-106 行)
@router.post("/record")
@handle_api_errors
async def record_cost(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    entry = tracker.record(...)
    return {"entry": entry.model_dump()}

# 修改后
@router.post("/record")
@handle_api_errors
async def record_cost(body: dict, request: Request) -> dict[str, Any]:
    require_admin(request)
    tracker = _get_cost_tracker()
    if hasattr(tracker, "record_async"):
        entry = await tracker.record_async(
            session_id=body.get("session_id", ""),
            agent=body.get("agent", ""),
            model=body.get("model", ""),
            prompt_tokens=body.get("prompt_tokens", 0),
            completion_tokens=body.get("completion_tokens", 0),
            total_tokens=body.get("total_tokens", 0),
            latency_ms=body.get("latency_ms", 0),
            metadata=body.get("metadata"),
        )
    else:
        entry = tracker.record(
            session_id=body.get("session_id", ""),
            agent=body.get("agent", ""),
            model=body.get("model", ""),
            prompt_tokens=body.get("prompt_tokens", 0),
            completion_tokens=body.get("completion_tokens", 0),
            total_tokens=body.get("total_tokens", 0),
            latency_ms=body.get("latency_ms", 0),
            metadata=body.get("metadata"),
        )
    return {"entry": entry.model_dump()}
```

> **注**: `cost.py` 中其他路由（如 `summary`、`entries`、`budget`）也调用同步方法，但本次仅修复 `record`（写入路径，高频调用）。读取路径（`summary` 等）可后续优化。

#### 4.2.3 同步版本 `monitoring/cost_tracker.py` 处理

`llm_provider.py` 第 851 行导入的是 `maop.core.monitoring.cost_tracker`，需确认该文件是否与 `core/cost_tracker.py` 同步。

**建议**: `monitoring/cost_tracker.py` 也添加 `record_async` 方法（与 `core/cost_tracker.py` 保持一致）。

### 4.3 风险评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 风险等级 | **中** | 涉及 async 路径改造，`_record_cost` 改为 async 影响调用链 |
| 功能影响 | 无 | 行为不变，仅执行方式从同步改为异步委托 |
| 兼容性 | 完全兼容 | 同步方法保留，async 版本为新增 |
| 性能影响 | 正向 | 避免阻塞事件循环，提升并发能力 |
| 回滚难度 | 中 | 需还原 `_record_cost` 签名和调用点 |
| 测试复杂度 | 中 | 需验证 async 上下文和同步上下文两种调用方式 |

**关键风险点**:
- `_record_cost` 从同步改为 async，所有调用点必须加 `await`（第 794、828 行）
- 需确认 `chat_with_fallback` 中所有 `_record_cost` 调用点都已修改
- `agent/llm_chat/llm_provider.py` 也有相同代码（第 794、828、844、852 行），需同步修改

### 4.4 验证步骤

```bash
# 1. 单元测试
cd F:\Nexus\MAOP\py
python -m pytest tests/ -k cost -v
python -m pytest tests/ -k tenant -v

# 2. 验证 async record 不阻塞
python -c "
import asyncio
from maop.core.cost_tracker import CostTracker

async def main():
    tracker = CostTracker()
    entry = await tracker.record_async(
        session_id='test',
        agent='test',
        model='gpt-4o',
        prompt_tokens=100,
        completion_tokens=50,
    )
    print(f'OK: async record returned {entry.id}')

asyncio.run(main())
"

# 3. 验证同步版本仍可用
python -c "
from maop.core.cost_tracker import CostTracker
tracker = CostTracker()
entry = tracker.record(
    session_id='test',
    model='gpt-4o',
    prompt_tokens=100,
    completion_tokens=50,
)
print(f'OK: sync record returned {entry.id}')
"

# 4. 验证 LLM 调用链不阻塞
python -c "
import asyncio
from maop.core.llm_provider import LLMProvider

async def main():
    provider = LLMProvider()
    # 模拟一次 LLM 调用（会触发 _record_cost）
    # 需配置实际 provider 或 mock
    print('OK: LLM call chain test')

asyncio.run(main())
"

# 5. 验证 dashboard /api/cost/record 端点
curl -X POST http://127.0.0.1:9079/api/cost/record \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","model":"gpt-4o","prompt_tokens":100,"completion_tokens":50}'
```

### 4.5 可行性结论

**✅ 可行**。与项目现有 `asyncio.to_thread` 模式一致（circuit_breaker 已用此模式），保持同步方法向后兼容，async 版本为新增。关键注意点：`_record_cost` 改为 async 后，所有调用点必须加 `await`，且 `agent/llm_chat/llm_provider.py` 需同步修改。

---

## 第5章 M8 — deploy.py `start()` 就绪检查

### 5.1 当前代码分析

#### 5.1.1 涉及文件

- `py/maop/deploy.py` 第 288-370 行（`start()` 方法）
- `py/maop/deploy.py` 第 152-255 行（`health_check()` 方法）

#### 5.1.2 问题代码

```python
# py/maop/deploy.py 第 288-370 行
def start(
    root_dir: str | Path = ".",
    *,
    port: int = 9079,
    host: str = "127.0.0.1",
    log_level: str = "INFO",
    dashboard: bool = True,
) -> SystemStatus:
    """Start the MAOP system (dashboard server)."""
    root = Path(root_dir).resolve()

    # Validate first
    validation = validate_config(root)
    if not validation.valid:
        return SystemStatus(status=ServiceStatus.ERROR, ...)

    # Check if already running
    existing_pid = _read_pid(root)
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)
            return SystemStatus(status=ServiceStatus.RUNNING, ...)
        except (OSError, ProcessLookupError):
            _remove_pid(root)

    # Ensure data directory
    (root / "data").mkdir(parents=True, exist_ok=True)

    # Start dashboard as subprocess
    if dashboard:
        cmd = [sys.executable, "-m", "uvicorn", "maop.dashboard.server:app", ...]
        proc = subprocess.Popen(cmd, ...)  # ← 第 340 行：启动子进程
        _write_pid(root, proc.pid)
        logger.info("MAOP started: pid=%d, dashboard=%s:%d", proc.pid, host, port)

        return SystemStatus(  # ← 第 350 行：直接返回 STARTING，不校验就绪
            status=ServiceStatus.STARTING,
            pid=proc.pid,
            started_at=datetime.now(timezone.utc).isoformat(),
            config=DeployConfig(...),
        )

    # No dashboard - just mark as running
    _write_pid(root, os.getpid())
    return SystemStatus(status=ServiceStatus.RUNNING, ...)
```

#### 5.1.3 已有 `health_check()` 方法

`health_check()` 方法（第 152-255 行）已存在，检查 4 个组件：
1. Database（`data/maop.db`）
2. Memory store（`data/memory.db`）
3. Config（`config/agents.yaml`）
4. Dashboard（HTTP `http://127.0.0.1:9079/api/health`）

返回 `list[ComponentHealth]`，每个组件有 `status`（HEALTHY/DEGRADED/UNHEALTHY）。

#### 5.1.4 当前状态评估

| 维度 | 状态 | 说明 |
|------|------|------|
| `validate_config()` 启动前校验 | ✅ 已有 | 检查目录、配置文件、Python 包 |
| `health_check()` 方法 | ✅ 已有 | 检查 4 个组件健康状态 |
| `start()` 后就绪检查 | ❌ 缺失 | 启动后直接返回 STARTING，不轮询 health_check |
| PID 管理 | ✅ 已有 | 写入/读取/清理 PID 文件 |

**核心问题**: `start()` 启动子进程后立即返回 `STARTING` 状态，调用方无法知道服务是否真正就绪。如果 uvicorn 启动失败（端口占用、导入错误等），调用方仍会收到 `STARTING` 而非 `ERROR`。

### 5.2 修复方案

#### 5.2.1 方案选择

采用 **方案 A：start() 后轮询 health_check，超时返回失败**。

理由：
- `health_check()` 已存在，仅需调用
- 轮询模式简单可靠，与 Kubernetes readiness probe 模式一致
- 添加 `wait_ready` 参数控制是否等待（保持灵活性）

#### 5.2.2 具体代码修改

**文件**: `py/maop/deploy.py` 第 288-370 行

```python
def start(
    root_dir: str | Path = ".",
    *,
    port: int = 9079,
    host: str = "127.0.0.1",
    log_level: str = "INFO",
    dashboard: bool = True,
    wait_ready: bool = True,
    ready_timeout_s: float = 30.0,
    ready_interval_s: float = 0.5,
) -> SystemStatus:
    """Start the MAOP system (dashboard server).

    P2-P3 fix: 启动后轮询 health_check 直到就绪或超时。

    Parameters
    ----------
    wait_ready : bool
        是否等待服务就绪。默认 True。
        False 时立即返回 STARTING（保持原行为，向后兼容）。
    ready_timeout_s : float
        就绪检查超时秒数。默认 30.0。
    ready_interval_s : float
        就绪检查轮询间隔秒数。默认 0.5。
    """
    root = Path(root_dir).resolve()

    # Validate first
    validation = validate_config(root)
    if not validation.valid:
        return SystemStatus(
            status=ServiceStatus.ERROR,
            config=DeployConfig(root_dir=str(root)),
            components=[ComponentHealth(
                name="validation",
                status=HealthStatus.UNHEALTHY,
                message="; ".join(validation.errors),
            )],
        )

    # Check if already running
    existing_pid = _read_pid(root)
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)
            return SystemStatus(
                status=ServiceStatus.RUNNING,
                pid=existing_pid,
                config=DeployConfig(root_dir=str(root), dashboard_port=port),
            )
        except (OSError, ProcessLookupError):
            _remove_pid(root)

    # Ensure data directory
    (root / "data").mkdir(parents=True, exist_ok=True)

    # Start dashboard as subprocess
    if dashboard:
        cmd = [
            sys.executable, "-m", "uvicorn",
            "maop.dashboard.server:app",
            "--host", host,
            "--port", str(port),
            "--log-level", log_level.lower(),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        _write_pid(root, proc.pid)
        logger.info("MAOP started: pid=%d, dashboard=%s:%d", proc.pid, host, port)

        # P2-P3 fix: 就绪检查 — 轮询 health_check 直到成功或超时
        if wait_ready:
            components = _wait_for_ready(
                root,
                timeout_s=ready_timeout_s,
                interval_s=ready_interval_s,
            )

            # 检查子进程是否已退出（启动失败）
            if proc.poll() is not None:
                # 子进程已退出，读取 stderr 获取错误信息
                stderr_output = ""
                try:
                    stderr_output = proc.stderr.read().decode("utf-8", errors="replace")[-500:]
                except Exception:
                    pass
                _remove_pid(root)
                logger.error("MAOP subprocess exited prematurely: %s", stderr_output)
                return SystemStatus(
                    status=ServiceStatus.ERROR,
                    pid=proc.pid,
                    config=DeployConfig(root_dir=str(root), dashboard_port=port),
                    components=[ComponentHealth(
                        name="subprocess",
                        status=HealthStatus.UNHEALTHY,
                        message=f"Process exited: {stderr_output}",
                    )],
                )

            # 评估就绪状态
            dashboard_healthy = any(
                c.name == "dashboard" and c.status == HealthStatus.HEALTHY
                for c in components
            )

            if dashboard_healthy:
                final_status = ServiceStatus.RUNNING
                logger.info("MAOP ready: pid=%d", proc.pid)
            else:
                # Dashboard 未就绪但进程仍在运行 — 返回 STARTING + 健康检查结果
                final_status = ServiceStatus.STARTING
                logger.warning(
                    "MAOP not ready after %.1fs: pid=%d, components=%s",
                    ready_timeout_s, proc.pid,
                    [(c.name, c.status.value) for c in components],
                )

            return SystemStatus(
                status=final_status,
                pid=proc.pid,
                started_at=datetime.now(timezone.utc).isoformat(),
                components=components,
                config=DeployConfig(
                    root_dir=str(root),
                    dashboard_port=port,
                    dashboard_host=host,
                    log_level=log_level,
                ),
            )

        # 不等待就绪 — 保持原行为
        return SystemStatus(
            status=ServiceStatus.STARTING,
            pid=proc.pid,
            started_at=datetime.now(timezone.utc).isoformat(),
            config=DeployConfig(
                root_dir=str(root),
                dashboard_port=port,
                dashboard_host=host,
                log_level=log_level,
            ),
        )

    # No dashboard - just mark as running
    _write_pid(root, os.getpid())
    return SystemStatus(
        status=ServiceStatus.RUNNING,
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(),
        config=DeployConfig(root_dir=str(root)),
    )


def _wait_for_ready(
    root_dir: str | Path,
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> list[ComponentHealth]:
    """轮询 health_check 直到 dashboard 就绪或超时。

    P2-P3 fix: start() 后的就绪检查辅助函数。
    返回最后一次 health_check 的结果（无论是否就绪）。
    """
    deadline = time.monotonic() + timeout_s
    last_components: list[ComponentHealth] = []

    while time.monotonic() < deadline:
        last_components = health_check(root_dir, timeout_s=interval_s)

        # 检查 dashboard 组件是否 HEALTHY
        dashboard_healthy = any(
            c.name == "dashboard" and c.status == HealthStatus.HEALTHY
            for c in last_components
        )
        if dashboard_healthy:
            return last_components

        time.sleep(interval_s)

    return last_components
```

#### 5.2.3 向后兼容性

| 调用方 | 兼容性 | 说明 |
|--------|--------|------|
| 现有调用 `start(root_dir)` | ✅ 完全兼容 | `wait_ready=True` 默认开启，但行为改进（返回 RUNNING 而非 STARTING） |
| 需要原行为 | ✅ 可选 | 传 `wait_ready=False` 即可 |
| CLI 调用 | ✅ 兼容 | CLI 可加 `--no-wait` 参数映射到 `wait_ready=False` |

### 5.3 风险评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 风险等级 | **低** | 仅添加就绪检查逻辑，不改变启动本身 |
| 功能影响 | 正向 | 调用方可获知真实就绪状态，启动失败可检测 |
| 兼容性 | 完全兼容 | `wait_ready=False` 保持原行为 |
| 性能影响 | 启动耗时增加 | 默认最多等 30s，但实际就绪通常 2-5s |
| 回滚难度 | 易 | 设 `wait_ready=False` 或还原代码 |
| 跨平台 | 需验证 | `proc.poll()` 和 `proc.stderr.read()` 在 Windows/Linux 行为一致 |

**关键注意点**:
- `proc.stderr.read()` 是阻塞操作，仅在 `proc.poll() is not None`（进程已退出）时调用，安全
- `health_check()` 中 dashboard 检查使用 `urllib.request.urlopen`，超时由 `timeout_s` 控制
- 轮询间隔 0.5s 平衡响应速度和 CPU 开销

### 5.4 验证步骤

```bash
# 1. 正常启动（应返回 RUNNING）
python -c "
from maop.deploy import start
status = start('F:/Nexus/MAOP', port=9079, wait_ready=True, ready_timeout_s=30)
print(f'Status: {status.status.value}')
print(f'PID: {status.pid}')
for c in status.components:
    print(f'  {c.name}: {c.status.value}')
assert status.status.value == 'running', f'Expected running, got {status.status.value}'
print('OK: start with ready check')
"

# 2. 不等待就绪（应返回 STARTING，原行为）
python -c "
from maop.deploy import start
status = start('F:/Nexus/MAOP', port=9079, wait_ready=False)
assert status.status.value == 'starting', f'Expected starting, got {status.status.value}'
print('OK: start without wait')
"

# 3. 端口占用（应返回 ERROR）
python -c "
import subprocess
# 先占用端口
proc1 = subprocess.Popen(['python', '-m', 'uvicorn', 'maop.dashboard.server:app', '--port', '9080'])
from maop.deploy import start
status = start('F:/Nexus/MAOP', port=9080, wait_ready=True, ready_timeout_s=10)
print(f'Status: {status.status.value}')
# 清理
proc1.terminate()
"

# 4. 超时测试（短超时）
python -c "
from maop.deploy import start
status = start('F:/Nexus/MAOP', port=9081, wait_ready=True, ready_timeout_s=0.1)
print(f'Status: {status.status.value}')
# 预期: STARTING（超时未就绪）
"

# 5. stop 后重新 start
python -c "
from maop.deploy import stop, start
stop('F:/Nexus/MAOP')
status = start('F:/Nexus/MAOP', port=9079, wait_ready=True)
print(f'Status: {status.status.value}')
"
```

### 5.5 可行性结论

**✅ 可行**。`health_check()` 已存在，仅需添加轮询逻辑。`wait_ready` 参数保持向后兼容，默认开启就绪检查提升健壮性。跨平台需验证 `proc.poll()` 和 `stderr.read()` 行为。

---

## 第6章 风险评估汇总

### 6.1 风险矩阵

| 修复项 | 风险等级 | 影响范围 | 兼容性 | 回滚难度 | 测试复杂度 |
|--------|----------|----------|--------|----------|------------|
| M3 | 中 | 核心搜索路径（NumPy/Python 回退） | 完全兼容 | 易 | 中 |
| M4 | 低 | API 端点 + 前端（可选） | 完全兼容 | 易 | 低 |
| M5 | 中 | LLM 调用链 + cost 路由 | 完全兼容 | 中 | 中 |
| M8 | 低 | deploy 启动流程 | 完全兼容 | 易 | 低 |

### 6.2 关键风险点

1. **M3**: 大数据集（>50K）时 NumPy/Python 回退路径仅搜索最近 50K 条。**缓解**: sqlite-vec/HNSW 主路径不受影响，且已有注释说明此场景应使用 ANN 索引。

2. **M5**: `_record_cost` 从同步改为 async，所有调用点必须加 `await`。**缓解**: 搜索确认调用点有限（`llm_provider.py` 第 794、828 行），且 `agent/llm_chat/llm_provider.py` 需同步修改。

3. **M8**: `proc.stderr.read()` 阻塞操作。**缓解**: 仅在 `proc.poll() is not None`（进程已退出）时调用，安全。

### 6.3 依赖关系

```
M8 (独立) ──┐
M4 (独立) ──┼─→ 可并行执行
M3 (独立) ──┘
M5 (独立) ──→ 需修改 llm_provider.py + agent/llm_chat/llm_provider.py 两处
```

四项修复相互独立，可并行执行。M5 需注意同一修改需应用到两个 `llm_provider.py` 文件。

## 第7章 验证步骤汇总

### 7.1 单元测试

```bash
cd F:\Nexus\MAOP\py

# M3
python -m pytest tests/ -k vector -v

# M4
python -m pytest tests/ -k "data or vector" -v

# M5
python -m pytest tests/ -k "cost or tenant" -v

# M8
python -m pytest tests/ -k deploy -v
```

### 7.2 集成测试

```bash
# 启动 dashboard（验证 M8 就绪检查）
python -c "from maop.deploy import start; s = start('F:/Nexus/MAOP'); print(s.status.value)"

# 验证 API 端点（M4）
curl "http://127.0.0.1:9079/api/vector/list?limit=10&offset=0"

# 验证 cost record（M5）
curl -X POST http://127.0.0.1:9079/api/cost/record \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","model":"gpt-4o","prompt_tokens":100,"completion_tokens":50}'

# 验证向量搜索（M3）
curl "http://127.0.0.1:9079/api/vector/search?q=test&topk=5"
```

### 7.3 前端验证

```bash
cd F:\Nexus\MAOP\dashboard-enterprise
npm run dev
# 打开 VectorSearch 页面，确认：
# - 向量列表正常加载（M4）
# - 搜索功能正常（M3）
```

## 第8章 可行性结论

### 8.1 总体结论

| 修复项 | 可行性 | 建议 |
|--------|--------|------|
| M3 | ✅ 可行 | 采用分页加载方案，保持 sqlite-vec/HNSW 主路径不受影响 |
| M4 | ✅ 可行 | API 层透传分页参数，`list_all()` 已有基础设施 |
| M5 | ✅ 可行 | 与项目现有 `asyncio.to_thread` 模式一致，保持同步方法向后兼容 |
| M8 | ✅ 可行 | `health_check()` 已存在，添加轮询逻辑，`wait_ready` 参数保持兼容 |

### 8.2 建议执行顺序

1. **M8**（风险最低，独立性最强，不涉及核心数据路径）
2. **M4**（风险低，前后端兼容性好，已有分页基础设施）
3. **M5**（风险中，涉及 async 路径改造，需测试事件循环）
4. **M3**（风险中，涉及核心搜索路径，需充分回归测试）

### 8.3 替代方案备选

#### M3 替代方案

- **方案 B: 按需加载** — search 时才加载目标向量，不预加载全量。优点：内存最优。缺点：每次 search 都查 DB，延迟增加。
- **方案 C: LRU 缓存** — 用 `functools.lru_cache` 替代手写缓存。优点：标准库支持。缺点：需重构 `_get_entry_info`。

**推荐**: 方案 A（分页加载），改动最小，保持兼容。

#### M4 替代方案

- **方案 B: 游标分页** — 用 `created_at` 作为游标替代 OFFSET。优点：大数据集性能更好。缺点：前端需改造游标逻辑。

**推荐**: 方案 A（LIMIT/OFFSET），`list_all()` 已实现，无需改造。

#### M5 替代方案

- **方案 B: aiosqlite** — 用 `aiosqlite` 替代 `sqlite3`。优点：原生 async。缺点：引入新依赖，需重写所有 SQL 调用。
- **方案 C: 同步方法 + 调用点 `to_thread`** — 不添加 async 版本，调用点直接 `await asyncio.to_thread(tracker.record, ...)`。优点：改动更少。缺点：调用点代码冗长。

**推荐**: 方案 A（async 版本方法），与 circuit_breaker 模式一致，调用点简洁。

#### M8 替代方案

- **方案 B: 信号量就绪** — 子进程启动后发送 SIGUSR1 信号表示就绪。优点：无轮询延迟。缺点：跨平台兼容性差（Windows 信号支持有限）。
- **方案 C: 文件就绪标记** — 子进程启动后创建 `data/.ready` 文件。优点：简单。缺点：需修改 dashboard server 代码。

**推荐**: 方案 A（轮询 health_check），`health_check()` 已存在，无需修改 server 代码。

## 附录 A: 文件修改清单

| 文件 | 修改类型 | 行号 | 说明 |
|------|----------|------|------|
| `py/maop/core/memory/vector.py` | 修改 | 801-819 | M3: `_load_cache()` 分页加载 |
| `py/maop/core/vector.py` | 修改 | 485-503 | M3: `_load_cache()` 分页加载（基础版） |
| `py/maop/dashboard/routers/data.py` | 修改 | 229-238 | M4: `api_vector_list` 加分页参数 |
| `dashboard-enterprise/src/views/VectorSearch.vue` | 可选修改 | 187 | M4: 前端分页控件（可选） |
| `py/maop/core/cost_tracker.py` | 新增方法 | — | M5: `record_async()`、`budget_status_async()` |
| `py/maop/core/monitoring/cost_tracker.py` | 新增方法 | — | M5: `record_async()`（与 core 版本同步） |
| `py/maop/core/tenant.py` | 新增方法 | — | M5: `check_quota_async()` |
| `py/maop/core/llm_provider.py` | 修改 | 794, 828, 844-863 | M5: `_record_cost` 改 async + 调用点加 await |
| `py/maop/core/agent/llm_chat/llm_provider.py` | 修改 | 794, 828, 844-863 | M5: 同上（agent 版本同步修改） |
| `py/maop/dashboard/routers/cost.py` | 修改 | 91-106 | M5: `record_cost` 路由用 `record_async` |
| `py/maop/deploy.py` | 修改 | 288-370 | M8: `start()` 加就绪检查 + `_wait_for_ready()` |

## 附录 B: 代码量估算

| 修复项 | 新增行数 | 修改行数 | 总计 |
|--------|----------|----------|------|
| M3 | 15 | 10 | 25 |
| M4 | 15 | 5 | 20 |
| M5 | 40 | 20 | 60 |
| M8 | 60 | 15 | 75 |
| **合计** | **130** | **50** | **180** |