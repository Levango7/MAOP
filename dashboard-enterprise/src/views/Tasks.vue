<template>
  <div class="tasks-view">
    <PageHeader>
      <template #badges>
        <Badge tone="brand" icon="scroll">{{ t('view.tasks.count', { n: total }) }}</Badge>
      </template>
      <span v-if="lastUpdated" class="last-updated">{{ t('view.tasks.updated') }} {{ lastUpdated }}</span>
      <button class="act-btn" :disabled="loading" :title="t('common.refresh')" @click="loadTasks">
        <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
      </button>
    </PageHeader>

    <!-- 搜索栏 + 状态过滤 + 排序选择 -->
    <FilterBar
      :model-value="filters"
      :schema="filterSchema"
      search-key="search"
      :search-placeholder="t('view.tasks.searchPlaceholder')"
      :results-label="`${total} / ${total}`"
      class="tasks-filterbar"
    >
      <template #extra>
        <select v-model="filters.sort" class="filterbar__select" :aria-label="t('view.tasks.sortLabel')" @change="onFilterChange">
          <option value="created_at">{{ t('view.tasks.sortCreated') }}</option>
          <option value="updated_at">{{ t('view.tasks.sortUpdated') }}</option>
          <option value="name">{{ t('view.tasks.sortName') }}</option>
          <option value="status">{{ t('view.tasks.sortStatus') }}</option>
        </select>
        <select v-model="filters.order" class="filterbar__select" :aria-label="t('view.tasks.orderLabel')" @change="onFilterChange">
          <option value="desc">{{ t('view.tasks.orderDesc') }}</option>
          <option value="asc">{{ t('view.tasks.orderAsc') }}</option>
        </select>
      </template>
    </FilterBar>

    <!-- 三态主体: 错误 → 加载 → 内容 -->
    <Card :title="t('view.tasks.title')" icon="scroll" :margin-bottom="16">
      <template #actions>
        <span class="muted">{{ t('view.tasks.pageInfo', { page, total: totalPages || 1 }) }}</span>
      </template>
      <div v-if="error" class="tasks-error">
        <EmptyState icon="alert-triangle" tone="fail" :title="t('view.tasks.loadError')" :description="error" />
      </div>
      <div v-else-if="loading" class="tasks-loading">
        <Skeleton :lines="8" block />
      </div>
      <div v-else-if="!tasks.length" class="tasks-empty">
        <EmptyState icon="scroll" :title="t('view.tasks.empty')" :description="t('view.tasks.emptyDesc')" />
      </div>
      <div v-else class="tasks-table-wrap">
        <table class="tasks-table">
          <thead>
            <tr>
              <th>{{ t('view.tasks.colName') }}</th>
              <th>{{ t('view.tasks.colStatus') }}</th>
              <th>{{ t('view.tasks.colCreated') }}</th>
              <th>{{ t('view.tasks.colDuration') }}</th>
              <th class="tasks-table__actions-head">{{ t('view.tasks.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td class="tasks-table__name">
                <div class="task-name">{{ taskDisplayName(task) }}</div>
                <div class="task-id">{{ task.id }}</div>
              </td>
              <td>
                <Badge :tone="statusTone(task.status)">{{ statusLabel(task.status) }}</Badge>
              </td>
              <td class="tasks-table__time">{{ formatTime(task.created_at) }}</td>
              <td class="tasks-table__duration">{{ formatDuration(task) }}</td>
              <td class="tasks-table__actions">
                <button
                  class="act-btn small"
                  :title="t('view.tasks.viewDetail')"
                  :aria-label="t('view.tasks.viewDetail')"
                  @click="viewDetail(task)"
                >
                  <AppIcon name="file-text" :size="13" />
                </button>
                <button
                  class="act-btn small"
                  :title="t('view.tasks.rerun')"
                  :aria-label="t('view.tasks.rerun')"
                  @click="confirmRerun(task)"
                >
                  <AppIcon name="refresh" :size="13" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>

    <!-- 分页控件 -->
    <div v-if="totalPages > 1" class="tasks-pagination">
      <button class="act-btn small" :disabled="page <= 1 || loading" @click="goPage(page - 1)">
        {{ t('view.tasks.prevPage') }}
      </button>
      <span class="page-info">{{ page }} / {{ totalPages }}</span>
      <button class="act-btn small" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">
        {{ t('view.tasks.nextPage') }}
      </button>
    </div>

    <!-- 重跑确认对话框 -->
    <DetailDrawer
      :open="rerunConfirm.open"
      :title="t('view.tasks.rerunConfirmTitle')"
      icon="refresh"
      @close="rerunConfirm.open = false"
    >
      <p class="rerun-message">{{ t('view.tasks.rerunConfirmText', { name: rerunConfirm.taskName }) }}</p>
      <template #footer>
        <button class="act-btn" :disabled="rerunning" @click="executeRerun">
          <AppIcon v-if="rerunning" name="refresh" :size="14" class="spinning" />
          {{ rerunning ? t('view.tasks.rerunning') : t('view.tasks.rerun') }}
        </button>
        <button class="act-btn ghost" :disabled="rerunning" @click="rerunConfirm.open = false">
          {{ t('common.cancel') }}
        </button>
      </template>
    </DetailDrawer>
  </div>
</template>

<script setup>
// P1-3: 任务历史页 — 列表 + 搜索 + 状态过滤 + 排序 + 分页 + 重跑
//
// 后端: GET /api/sessions (分页+搜索+过滤+排序), POST /api/sessions/{id}/rerun
// 数据源: SessionManager (SQLite sessions 表)
//
// 设计: 复用项目现有组件 (PageHeader / FilterBar / Card / Badge / Skeleton /
// EmptyState / DetailDrawer / AppIcon), 与 Audit.vue / Overview.vue 风格一致。
// 未引入 Element Plus — 项目组件库为自研, 保持 UI 一致性 (见 README)。

import { ref, reactive, computed, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import FilterBar from '../components/FilterBar.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const router = useRouter();

// ── 状态 ──
const loading = ref(true);
const error = ref('');
const tasks = ref([]);
const total = ref(0);
const page = ref(1);
const limit = ref(20);
const totalPages = ref(0);
const lastUpdated = ref('');

// ── 过滤/搜索/排序 ──
const filters = reactive({
  search: '',
  status: '',
  sort: 'created_at',
  order: 'desc',
});

// 状态过滤选项 (在 FilterBar schema 中渲染为 <select>)
const filterSchema = computed(() => [
  {
    key: 'status',
    label: t('view.tasks.filterStatus'),
    options: [
      { value: 'all', label: t('view.tasks.statusAll') },
      { value: 'running', label: t('view.tasks.statusRunning') },
      { value: 'active', label: t('view.tasks.statusActive') },
      { value: 'paused', label: t('view.tasks.statusPaused') },
      { value: 'completed', label: t('view.tasks.statusCompleted') },
      { value: 'failed', label: t('view.tasks.statusFailed') },
      { value: 'archived', label: t('view.tasks.statusArchived') },
    ],
  },
]);

// ── 数据加载 ──
async function loadTasks() {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams();
    params.set('status', filters.status || 'all');
    if (filters.search) params.set('search', filters.search);
    params.set('page', String(page.value));
    params.set('limit', String(limit.value));
    params.set('sort', filters.sort);
    params.set('order', filters.order);

    const data = await api.get(`/api/sessions?${params.toString()}`);
    tasks.value = data.items || [];
    total.value = data.total || 0;
    totalPages.value = data.total_pages || 0;
    lastUpdated.value = new Date().toLocaleTimeString();
  } catch (err) {
    error.value = String(err.message || err);
    tasks.value = [];
  } finally {
    loading.value = false;
  }
}

// ── 过滤变更处理 ──
// FilterBar 通过 mutate filters 对象触发响应, watch 监听 search/status 重载
let searchDebounce = null;
watch(() => filters.search, () => {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    page.value = 1;
    loadTasks();
  }, 300);
});
watch(() => filters.status, () => {
  page.value = 1;
  loadTasks();
});

function onFilterChange() {
  page.value = 1;
  loadTasks();
}

function goPage(p) {
  if (p < 1 || p > totalPages.value || loading.value) return;
  page.value = p;
  loadTasks();
}

// ── 任务展示辅助 ──
function taskDisplayName(task) {
  // 优先用 metadata.task / metadata.description / metadata.prompt, 兜底 agent
  const meta = task.metadata || {};
  return meta.task || meta.description || meta.prompt || task.agent || task.id;
}

function statusTone(status) {
  // running/active=info(蓝) completed=success(绿) failed=fail(红) paused=warn(黄)
  const s = String(status || '').toLowerCase();
  if (s === 'completed') return 'success';
  if (s === 'failed') return 'fail';
  if (s === 'paused') return 'warn';
  if (s === 'archived') return 'neutral';
  return 'info'; // active / running / 未知
}

function statusLabel(status) {
  const s = String(status || '').toLowerCase();
  const map = {
    active: t('view.tasks.statusActive'),
    running: t('view.tasks.statusRunning'),
    paused: t('view.tasks.statusPaused'),
    completed: t('view.tasks.statusCompleted'),
    failed: t('view.tasks.statusFailed'),
    archived: t('view.tasks.statusArchived'),
  };
  return map[s] || status || '—';
}

function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString();
  } catch {
    return String(ts);
  }
}

function formatDuration(task) {
  // 耗时 = updated_at - created_at (秒级精度)
  if (!task.created_at || !task.updated_at) return '—';
  try {
    const start = new Date(task.created_at).getTime();
    const end = new Date(task.updated_at).getTime();
    if (isNaN(start) || isNaN(end)) return '—';
    const diff = Math.max(0, Math.floor((end - start) / 1000));
    if (diff < 60) return diff + 's';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ' + (diff % 60) + 's';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ' + Math.floor((diff % 3600) / 60) + 'm';
    return Math.floor(diff / 86400) + 'd ' + Math.floor((diff % 86400) / 3600) + 'h';
  } catch {
    return '—';
  }
}

// ── 查看详情 ──
function viewDetail(task) {
  // 跳转到 Run 页面并带上 session id, Run 页可据此加载会话上下文
  router.push({ path: '/run', query: { session: task.id } });
}

// ── 重跑确认 + 执行 ──
const rerunConfirm = reactive({ open: false, taskId: '', taskName: '' });
const rerunning = ref(false);

function confirmRerun(task) {
  rerunConfirm.taskId = task.id;
  rerunConfirm.taskName = taskDisplayName(task);
  rerunConfirm.open = true;
}

async function executeRerun() {
  if (!rerunConfirm.taskId || rerunning.value) return;
  rerunning.value = true;
  try {
    const data = await api.post(`/api/sessions/${rerunConfirm.taskId}/rerun`, {});
    if (data.error) {
      toast.error(t('view.tasks.rerunFailed') + ': ' + data.error);
    } else {
      toast.success(t('view.tasks.rerunSuccess'));
      rerunConfirm.open = false;
      // 刷新列表以展示新会话
      await loadTasks();
    }
  } catch (err) {
    toast.error(t('view.tasks.rerunFailed') + ': ' + (err.message || err));
  } finally {
    rerunning.value = false;
  }
}

// ── 初始化 ──
onMounted(() => {
  loadTasks();
});
</script>

<style scoped>
.tasks-view {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.last-updated {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  white-space: nowrap;
}

.tasks-filterbar {
  margin-bottom: var(--sp-3);
}

/* ── 任务表格 ── */
.tasks-table-wrap {
  width: 100%;
  overflow-x: auto;
}
.tasks-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}
.tasks-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  font-size: var(--fs-xs);
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-strong, var(--border));
  white-space: nowrap;
  text-align: left;
}
.tasks-table tbody td {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle, var(--border));
  color: var(--text);
  vertical-align: middle;
}
.tasks-table tbody tr {
  transition: background var(--motion) var(--ease);
}
.tasks-table tbody tr:hover {
  background: var(--surface-2);
}
.tasks-table tbody tr:last-child td {
  border-bottom: none;
}
.tasks-table__name {
  min-width: 180px;
}
.task-name {
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.task-id {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  font-family: var(--font-mono, monospace);
  margin-top: 2px;
}
.tasks-table__time,
.tasks-table__duration {
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.tasks-table__actions-head,
.tasks-table__actions {
  text-align: right;
  white-space: nowrap;
}
.tasks-table__actions {
  display: flex;
  gap: var(--sp-1);
  justify-content: flex-end;
}

/* ── 分页 ── */
.tasks-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
  padding: var(--sp-3);
}
.tasks-pagination .page-info {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  min-width: 80px;
  text-align: center;
}

/* ── 重跑确认 ── */
.rerun-message {
  margin: 0;
  padding: var(--sp-2) 0;
  font-size: var(--fs-sm);
  color: var(--text);
  line-height: 1.6;
}

/* ── 复用 Audit.vue 的 act-btn 风格 ── */
.act-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--sp-1) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.act-btn:hover:not(:disabled) {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.act-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.act-btn.small {
  padding: var(--sp-1) var(--sp-2);
  font-size: var(--fs-xs);
}
.act-btn.ghost {
  background: transparent;
  border-color: var(--border);
}
.act-btn.danger:hover:not(:disabled) {
  border-color: var(--fail);
  background: var(--fail-soft);
  color: var(--fail);
}

.spinning {
  animation: maop-spin 1s linear infinite;
}
@keyframes maop-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.muted {
  color: var(--text-muted);
  font-size: var(--fs-xs);
}
</style>