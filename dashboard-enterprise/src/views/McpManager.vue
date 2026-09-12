<template>
  <div class="mcp-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Servers ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'servers'">
          <Card :title="t('view.mcp.servers.title')" icon="server" margin-bottom="var(--sp-4)">
            <template #actions>
              <button class="btn-ghost" @click="openAddServer">
                <AppIcon name="plus" :size="15" /><span>{{ t('view.mcp.servers.add') }}</span>
              </button>
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.servers"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.mcp.servers.failedLoad')"
              :description="errors.servers"
            />
            <EmptyState
              v-else-if="!servers.length"
              icon="server"
              :title="t('view.mcp.servers.empty')"
              :description="t('view.mcp.servers.emptyHint')"
            />
            <div v-else class="srv-table" role="table" :aria-label="t('view.mcp.servers.title')">
              <div class="srv-table__head" role="row">
                <div class="srv-table__th" role="columnheader">{{ t('common.name') }}</div>
                <div class="srv-table__th" role="columnheader">{{ t('view.mcp.col.status') }}</div>
                <div class="srv-table__th srv-table__th--num" role="columnheader">{{ t('view.mcp.col.tools') }}</div>
                <div class="srv-table__th srv-table__th--num" role="columnheader">{{ t('view.mcp.col.latency') }}</div>
                <div class="srv-table__th srv-table__th--act" role="columnheader">{{ t('view.mcp.col.actions') }}</div>
              </div>
              <div v-for="s in servers" :key="s.id" class="srv-table__row" role="row">
                <div class="srv-table__td" role="cell">
                  <div class="srv-table__name">{{ s.name }}</div>
                  <div class="srv-table__url mono">{{ s.url || '—' }}</div>
                </div>
                <div class="srv-table__td" role="cell">
                  <Badge :tone="isConnected(s) ? 'success' : 'neutral'">
                    {{ isConnected(s) ? t('view.mcp.status.connected') : t('view.mcp.status.disconnected') }}
                  </Badge>
                </div>
                <div class="srv-table__td srv-table__td--num" role="cell">{{ s.tool_count ?? 0 }}</div>
                <div class="srv-table__td srv-table__td--num" role="cell">{{ formatLatency(s.latency_ms) }}</div>
                <div class="srv-table__td srv-table__td--act" role="cell">
                  <button class="icon-btn" type="button" :aria-label="t('common.edit')" @click="openEditServer(s)">
                    <AppIcon name="gear" :size="14" />
                  </button>
                  <button class="icon-btn icon-btn--danger" type="button" :aria-label="t('common.delete')" @click="confirmDeleteServer(s)">
                    <AppIcon name="trash" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Tools ──────────────────────────────────────────── -->
        <div v-show="activeTab === 'tools'">
          <Card :title="t('view.mcp.tools.title')" icon="wrench" margin-bottom="var(--sp-4)">
            <FilterBar
              :model-value="toolFilters"
              :schema="toolFilterSchema"
              :results-label="toolResultsLabel"
            />
            <div v-if="loading" class="blk">
              <Skeleton block height="40px" />
              <Skeleton block height="40px" />
            </div>
            <EmptyState
              v-else-if="errors.tools"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.mcp.tools.failedLoad')"
              :description="errors.tools"
            />
            <EmptyState
              v-else-if="!tools.length"
              icon="wrench"
              :title="t('view.mcp.tools.empty')"
              :description="t('view.mcp.tools.emptyHint')"
            />
            <div v-else class="tools-list">
              <details v-for="tool in filteredTools" :key="tool.id || tool.name" class="tool-item">
                <summary class="tool-item__head">
                  <div class="tool-item__main">
                    <AppIcon name="code" :size="15" class="tool-item__icon" />
                    <span class="tool-item__name">{{ tool.name }}</span>
                    <Badge v-if="tool.server_name" tone="info">{{ tool.server_name }}</Badge>
                  </div>
                  <div class="tool-item__meta">
                    <span class="tool-item__calls">{{ t('view.mcp.tools.callCount') }}: <b>{{ tool.call_count ?? 0 }}</b></span>
                    <AppIcon name="chevrondown" :size="14" class="tool-item__chevron" />
                  </div>
                </summary>
                <div class="tool-item__body">
                  <p v-if="tool.description" class="tool-item__desc">{{ tool.description }}</p>
                  <p v-else class="tool-item__desc muted">{{ t('view.tools.noDescription') }}</p>
                  <div class="tool-item__params">
                    <div class="tool-item__params-label">{{ t('view.mcp.tools.params') }}</div>
                    <pre v-if="hasParams(tool)" class="tool-item__schema mono">{{ formatSchema(tool.parameters) }}</pre>
                    <span v-else class="muted">{{ t('view.mcp.tools.noParams') }}</span>
                  </div>
                </div>
              </details>
            </div>
          </Card>
        </div>

        <!-- ── Statistics ─────────────────────────────────────── -->
        <div v-show="activeTab === 'stats'">
          <Card :title="t('view.mcp.stats.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="120px" />
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.stats"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.mcp.stats.failedLoad')"
              :description="errors.stats"
            />
            <EmptyState
              v-else-if="!hasStatsData"
              icon="activity"
              :title="t('view.mcp.stats.empty')"
              :description="t('view.mcp.stats.emptyHint')"
            />
            <div v-else class="stats-body">
              <!-- 概览卡片 -->
              <div class="stats-overview">
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.mcp.stats.totalCalls') }}</span>
                  <span class="stats-card__value">{{ statsOverview.totalCalls }}</span>
                </div>
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.mcp.stats.successRate') }}</span>
                  <span class="stats-card__value" :class="successRateTone">{{ statsOverview.successRate }}%</span>
                </div>
                <div class="stats-card">
                  <span class="stats-card__label">{{ t('view.mcp.stats.avgLatency') }}</span>
                  <span class="stats-card__value">{{ statsOverview.avgLatency }} {{ t('view.mcp.stats.latencyMs') }}</span>
                </div>
              </div>

              <!-- 时间序列图 -->
              <div class="stats-chart">
                <div class="stats-chart__head">
                  <span class="stats-chart__title">{{ t('view.mcp.stats.timeseries') }}</span>
                  <Segmented
                    :model-value="statsMetric"
                    :options="statsMetricOptions"
                    size="sm"
                    @update:model-value="statsMetric = $event"
                  />
                </div>
                <svg
                  v-if="statsSeries.length"
                  class="stats-chart__svg"
                  :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="t('view.mcp.stats.timeseries')"
                >
                  <line v-for="(g, i) in chartGridLines" :key="'g' + i" :x1="g.x1" :y1="g.y1" :x2="g.x2" :y2="g.y2" class="stats-chart__grid" />
                  <path :d="chartAreaPath" class="stats-chart__area" />
                  <path :d="chartLinePath" class="stats-chart__line" />
                  <circle v-for="(p, i) in chartPoints" :key="'p' + i" :cx="p.x" :cy="p.y" r="2.5" class="stats-chart__dot" />
                </svg>
                <div v-else class="stats-chart__empty muted">{{ t('view.mcp.stats.empty') }}</div>
              </div>

              <!-- 按工具明细 -->
              <div class="stats-per-tool">
                <div class="stats-per-tool__title">{{ t('view.mcp.stats.perTool') }}</div>
                <DataTable
                  :columns="perToolCols"
                  :rows="perToolRows"
                  :empty-text="t('view.mcp.stats.empty')"
                  compact
                />
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Concurrency ────────────────────────────────────── -->
        <div v-show="activeTab === 'concurrency'">
          <Card :title="t('view.mcp.concurrency.title')" icon="cpu" margin-bottom="var(--sp-4)">
            <p class="conc-hint">{{ t('view.mcp.concurrency.hint') }}</p>
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.tools"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.mcp.tools.failedLoad')"
              :description="errors.tools"
            />
            <EmptyState
              v-else-if="!tools.length"
              icon="cpu"
              :title="t('view.mcp.concurrency.empty')"
              :description="t('view.mcp.concurrency.emptyHint')"
            />
            <div v-else class="conc-table" role="table" :aria-label="t('view.mcp.concurrency.title')">
              <div class="conc-table__head" role="row">
                <div class="conc-table__th" role="columnheader">{{ t('common.name') }}</div>
                <div class="conc-table__th" role="columnheader">{{ t('view.mcp.col.server') }}</div>
                <div class="conc-table__th conc-table__th--num" role="columnheader">{{ t('view.mcp.concurrency.current') }}</div>
                <div class="conc-table__th conc-table__th--num" role="columnheader">{{ t('view.mcp.concurrency.newLimit') }}</div>
              </div>
              <div v-for="tool in tools" :key="tool.id || tool.name" class="conc-table__row" role="row">
                <div class="conc-table__td" role="cell">
                  <div class="conc-table__name">{{ tool.name }}</div>
                </div>
                <div class="conc-table__td" role="cell">
                  <span class="muted">{{ tool.server_name || '—' }}</span>
                </div>
                <div class="conc-table__td conc-table__td--num" role="cell">
                  <Badge :tone="(tool.concurrency_limit ?? 0) > 0 ? 'info' : 'neutral'">
                    {{ (tool.concurrency_limit ?? 0) > 0 ? tool.concurrency_limit : t('view.mcp.concurrency.unlimited') }}
                  </Badge>
                </div>
                <div class="conc-table__td conc-table__td--num" role="cell">
                  <div class="conc-edit">
                    <input
                      :value="concDrafts[tool.id || tool.name]"
                      type="number"
                      min="0"
                      class="conc-edit__input"
                      :placeholder="String(tool.concurrency_limit ?? 0)"
                      :aria-label="t('view.mcp.concurrency.newLimit')"
                      @input="setConcDraft(tool, $event.target.value)"
                    />
                    <button
                      class="btn-ghost btn-ghost--sm"
                      type="button"
                      :disabled="concSaving === (tool.id || tool.name)"
                      @click="saveConcurrency(tool)"
                    >
                      <AppIcon name="check" :size="13" />
                      <span>{{ concSaving === (tool.id || tool.name) ? t('common.loading') : t('view.mcp.concurrency.save') }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Add/Edit Server Modal ─────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showServerModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeServerModal"
        @modal:escape="closeServerModal"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ editingServer ? t('view.mcp.modal.editTitle') : t('view.mcp.modal.addTitle') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeServerModal">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">{{ t('view.mcp.modal.name') }}</span>
              <input v-model="serverForm.name" class="field__input" :placeholder="t('view.mcp.modal.name')" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.mcp.modal.url') }}</span>
              <input v-model="serverForm.url" class="field__input" :placeholder="t('view.mcp.modal.urlHint')" />
            </label>
            <div class="field-row">
              <label class="field">
                <span class="field__label">{{ t('view.mcp.modal.transport') }}</span>
                <select v-model="serverForm.transport" class="field__input field__select">
                  <option value="http">HTTP</option>
                  <option value="stdio">stdio</option>
                  <option value="sse">SSE</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.mcp.modal.authType') }}</span>
                <select v-model="serverForm.auth_type" class="field__input field__select">
                  <option value="none">{{ t('view.mcp.modal.auth.none') }}</option>
                  <option value="bearer">{{ t('view.mcp.modal.auth.bearer') }}</option>
                  <option value="basic">{{ t('view.mcp.modal.auth.basic') }}</option>
                  <option value="apikey">{{ t('view.mcp.modal.auth.apikey') }}</option>
                </select>
              </label>
            </div>
            <label v-if="serverForm.auth_type !== 'none'" class="field">
              <span class="field__label">{{ t('view.mcp.modal.token') }}</span>
              <input v-model="serverForm.token" class="field__input" type="password" :placeholder="t('view.mcp.modal.token')" />
            </label>
            <label class="field">
              <span class="field__label">{{ t('view.mcp.modal.timeout') }}</span>
              <input v-model.number="serverForm.timeout" class="field__input" type="number" min="1000" step="1000" placeholder="30000" />
            </label>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeServerModal">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="!serverForm.name.trim() || serverSaving"
              @click="submitServer"
            >
              {{ serverSaving ? t('view.mcp.modal.saving') : t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Confirm Delete Modal ──────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showDeleteConfirm"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeDeleteConfirm"
        @modal:escape="closeDeleteConfirm"
      >
        <div class="modal modal--sm" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('common.confirm') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeDeleteConfirm">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p>{{ t('view.mcp.servers.deleteConfirm') }}</p>
            <p v-if="deletingServer" class="modal__target mono">{{ deletingServer.name }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeDeleteConfirm">{{ t('common.cancel') }}</button>
            <button class="btn-danger" type="button" :disabled="serverDeleting" @click="doDeleteServer">
              {{ serverDeleting ? t('common.loading') : t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import DataTable from '../components/DataTable.vue';
import FilterBar from '../components/FilterBar.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('servers');
const tabOptions = [
  { value: 'servers', label: t('view.mcp.tab.servers'), icon: 'server' },
  { value: 'tools', label: t('view.mcp.tab.tools'), icon: 'wrench' },
  { value: 'stats', label: t('view.mcp.tab.stats'), icon: 'activity' },
  { value: 'concurrency', label: t('view.mcp.tab.concurrency'), icon: 'cpu' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ servers: null, tools: null, stats: null });

const servers = ref([]);
const tools = ref([]);
const stats = ref({ series: [], per_tool: [] });

// ── Servers: add / edit / delete ────────────────────────────────
const showServerModal = ref(false);
const editingServer = ref(null);
const serverSaving = ref(false);
const serverForm = reactive({
  name: '',
  url: '',
  transport: 'http',
  auth_type: 'none',
  token: '',
  timeout: 30000,
});

function resetServerForm() {
  serverForm.name = '';
  serverForm.url = '';
  serverForm.transport = 'http';
  serverForm.auth_type = 'none';
  serverForm.token = '';
  serverForm.timeout = 30000;
}

function openAddServer() {
  editingServer.value = null;
  resetServerForm();
  showServerModal.value = true;
}

function openEditServer(s) {
  editingServer.value = s;
  serverForm.name = s.name || '';
  serverForm.url = s.url || '';
  serverForm.transport = s.transport || 'http';
  serverForm.auth_type = s.auth_type || 'none';
  serverForm.token = '';
  serverForm.timeout = s.timeout ?? 30000;
  showServerModal.value = true;
}

function closeServerModal() {
  showServerModal.value = false;
}

async function submitServer() {
  const name = serverForm.name.trim();
  if (!name) return;
  serverSaving.value = true;
  try {
    const payload = {
      name,
      url: serverForm.url.trim(),
      transport: serverForm.transport,
      auth_type: serverForm.auth_type,
      timeout: Number(serverForm.timeout) || 30000,
    };
    if (serverForm.auth_type !== 'none' && serverForm.token) {
      payload.token = serverForm.token;
    }
    if (editingServer.value) {
      await api.put(`/api/mcp/servers/${editingServer.value.id}`, payload);
    } else {
      await api.post('/api/mcp/servers', payload);
    }
    showServerModal.value = false;
    await loadServers();
  } catch (err) {
    errors.value = { ...errors.value, servers: (err && err.message) || t('view.mcp.servers.saveFailed') };
  } finally {
    serverSaving.value = false;
  }
}

const showDeleteConfirm = ref(false);
const deletingServer = ref(null);
const serverDeleting = ref(false);

function confirmDeleteServer(s) {
  deletingServer.value = s;
  showDeleteConfirm.value = true;
}

function closeDeleteConfirm() {
  showDeleteConfirm.value = false;
  deletingServer.value = null;
}

async function doDeleteServer() {
  if (!deletingServer.value) return;
  serverDeleting.value = true;
  try {
    await api.del(`/api/mcp/servers/${deletingServer.value.id}`);
    showDeleteConfirm.value = false;
    deletingServer.value = null;
    await loadServers();
    await loadTools();
  } catch (err) {
    errors.value = { ...errors.value, servers: (err && err.message) || t('view.mcp.servers.deleteFailed') };
  } finally {
    serverDeleting.value = false;
  }
}

// ── Tools: filter ───────────────────────────────────────────────
const toolFilters = reactive({ server: '', query: '' });
const toolFilterSchema = computed(() => [
  {
    key: 'server',
    label: t('view.mcp.tools.filterServer'),
    options: serverFilterOptions.value,
  },
]);

const serverFilterOptions = computed(() =>
  servers.value
    .filter((s) => s.name)
    .map((s) => ({ value: s.name, label: s.name })),
);

const filteredTools = computed(() => {
  let list = tools.value;
  if (toolFilters.server) {
    list = list.filter((tool) => tool.server_name === toolFilters.server);
  }
  return list;
});

const toolResultsLabel = computed(() => {
  const n = filteredTools.value.length;
  return `${n} / ${tools.value.length}`;
});

function hasParams(tool) {
  const p = tool.parameters;
  if (!p) return false;
  if (typeof p === 'object') return Object.keys(p).length > 0;
  try { return Object.keys(JSON.parse(p)).length > 0; } catch { return false; }
}

function formatSchema(params) {
  if (typeof params === 'string') {
    try { return JSON.stringify(JSON.parse(params), null, 2); } catch { return params; }
  }
  try { return JSON.stringify(params, null, 2); } catch { return String(params); }
}

// ── Stats ───────────────────────────────────────────────────────
const statsMetric = ref('calls');
const statsMetricOptions = [
  { value: 'calls', label: t('view.mcp.stats.calls') },
  { value: 'success_rate', label: t('view.mcp.stats.successRate') },
  { value: 'avg_latency', label: t('view.mcp.stats.avgLatency') },
];

const statsSeries = computed(() => stats.value.series || []);
const hasStatsData = computed(() =>
  statsSeries.value.length > 0 || (stats.value.per_tool && stats.value.per_tool.length > 0),
);

const statsOverview = computed(() => {
  const series = statsSeries.value;
  if (!series.length) {
    return { totalCalls: 0, successRate: '—', avgLatency: '—' };
  }
  const totalCalls = series.reduce((sum, p) => sum + (p.calls ?? 0), 0);
  const last = series[series.length - 1];
  const avgLatency = last && last.avg_latency_ms != null ? Math.round(last.avg_latency_ms) : '—';
  const successRate = last && last.success_rate != null ? (last.success_rate * 100).toFixed(1) : '—';
  return { totalCalls, successRate, avgLatency };
});

const successRateTone = computed(() => {
  const v = parseFloat(statsOverview.value.successRate);
  if (isNaN(v)) return '';
  if (v >= 95) return 'is-ok';
  if (v >= 80) return 'is-warn';
  return 'is-fail';
});

// ── Chart (inline SVG) ──────────────────────────────────────────
// 固定 viewBox 尺寸, CSS 控制实际显示尺寸, 响应式自适应。
const CHART_W = 600;
const CHART_H = 180;
const CHART_PAD = 24;

const chartValues = computed(() => {
  const series = statsSeries.value;
  if (!series.length) return [];
  return series.map((p) => {
    if (statsMetric.value === 'calls') return p.calls ?? 0;
    if (statsMetric.value === 'success_rate') return (p.success_rate ?? 0) * 100;
    return p.avg_latency_ms ?? 0;
  });
});

const chartPoints = computed(() => {
  const vals = chartValues.value;
  if (vals.length < 2) return [];
  const max = Math.max(...vals, 1);
  const min = Math.min(...vals, 0);
  const range = max - min || 1;
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  return vals.map((v, i) => ({
    x: CHART_PAD + (i / (vals.length - 1)) * w,
    y: CHART_PAD + h - ((v - min) / range) * h,
  }));
});

const chartLinePath = computed(() => {
  const pts = chartPoints.value;
  if (!pts.length) return '';
  return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
});

const chartAreaPath = computed(() => {
  const pts = chartPoints.value;
  if (pts.length < 2) return '';
  const base = CHART_H - CHART_PAD;
  const head = `M${pts[0].x.toFixed(1)},${base}`;
  const top = pts.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const tail = `L${pts[pts.length - 1].x.toFixed(1)},${base} Z`;
  return `${head} ${top} ${tail}`;
});

const chartGridLines = computed(() => {
  const lines = [];
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  // 4 条水平网格线
  for (let i = 0; i <= 4; i++) {
    const y = CHART_PAD + (i / 4) * h;
    lines.push({ x1: CHART_PAD, y1: y, x2: CHART_PAD + w, y2: y });
  }
  return lines;
});

// ── Per-tool stats table ────────────────────────────────────────
const perToolCols = computed(() => [
  { key: 'name', label: t('common.name'), width: '40%' },
  { key: 'calls', label: t('view.mcp.col.calls'), type: 'num', align: 'right', width: '20%' },
  { key: 'success_rate', label: t('view.mcp.stats.successRate'), align: 'right', width: '20%' },
  { key: 'avg_latency', label: t('view.mcp.stats.avgLatency'), align: 'right', width: '20%' },
]);

const perToolRows = computed(() =>
  (stats.value.per_tool || []).map((r) => ({
    name: r.name || r.tool_name || '—',
    calls: r.calls ?? 0,
    success_rate: r.success_rate != null ? `${(r.success_rate * 100).toFixed(1)}%` : '—',
    avg_latency: r.avg_latency_ms != null ? `${Math.round(r.avg_latency_ms)} ${t('view.mcp.stats.latencyMs')}` : '—',
  })),
);

// ── Concurrency ────────────────────────────────────────────────
const concDrafts = reactive({});
const concSaving = ref('');

function setConcDraft(tool, val) {
  concDrafts[tool.id || tool.name] = val;
}

async function saveConcurrency(tool) {
  const key = tool.id || tool.name;
  const raw = concDrafts[key];
  if (raw === '' || raw === undefined || raw === null) return;
  const limit = Math.max(0, parseInt(raw, 10) || 0);
  concSaving.value = key;
  try {
    await api.put(`/api/mcp/tools/${tool.id}`, { concurrency_limit: limit });
    // 乐观更新本地状态
    const idx = tools.value.findIndex((tk) => (tk.id || tk.name) === key);
    if (idx >= 0) tools.value[idx] = { ...tools.value[idx], concurrency_limit: limit };
    delete concDrafts[key];
  } catch (err) {
    errors.value = { ...errors.value, tools: (err && err.message) || t('view.mcp.concurrency.saveFailed') };
  } finally {
    concSaving.value = '';
  }
}

// ── Helpers ─────────────────────────────────────────────────────
function isConnected(s) {
  const st = String(s.status || s.state || '').toLowerCase();
  if (st === 'connected' || st === 'online' || st === 'ok') return true;
  if (s.enabled === false) return false;
  return st !== 'disconnected' && st !== 'offline' && st !== 'error' && st !== '';
}

function formatLatency(ms) {
  if (ms == null) return '—';
  const n = Number(ms);
  if (isNaN(n)) return '—';
  if (n < 1000) return `${Math.round(n)} ${t('view.mcp.stats.latencyMs')}`;
  return `${(n / 1000).toFixed(2)}s`;
}

// ── Data loading ────────────────────────────────────────────────
async function loadServers() {
  try {
    const data = await api.get('/api/mcp/servers');
    servers.value = data.servers || [];
    errors.value = { ...errors.value, servers: null };
  } catch (err) {
    servers.value = [];
    errors.value = { ...errors.value, servers: (err && err.message) || t('view.mcp.servers.failedLoad') };
  }
}

async function loadTools() {
  try {
    const data = await api.get('/api/mcp/tools');
    tools.value = data.tools || [];
    errors.value = { ...errors.value, tools: null };
  } catch (err) {
    tools.value = [];
    errors.value = { ...errors.value, tools: (err && err.message) || t('view.mcp.tools.failedLoad') };
  }
}

async function loadStats() {
  try {
    const data = await api.get('/api/mcp/stats');
    stats.value = {
      series: data.series || [],
      per_tool: data.per_tool || [],
    };
    errors.value = { ...errors.value, stats: null };
  } catch (err) {
    stats.value = { series: [], per_tool: [] };
    errors.value = { ...errors.value, stats: (err && err.message) || t('view.mcp.stats.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadServers(), loadTools(), loadStats()]);
  loading.value = false;
}

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
/* ── Layout helpers ────────────────────────────────────────────── */
.blk { display: flex; flex-direction: column; gap: var(--sp-2); }
.muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }

/* ── Servers table ─────────────────────────────────────────────── */
.srv-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.srv-table__head,
.srv-table__row {
  display: grid;
  grid-template-columns: 1fr auto 80px 100px 96px;
  align-items: center;
  gap: var(--sp-2);
}
.srv-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.srv-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.srv-table__th--num { text-align: right; }
.srv-table__th--act { text-align: right; }
.srv-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.srv-table__row:hover { background: var(--surface-2); }
.srv-table__row:last-child { border-bottom: none; }
.srv-table__td { min-width: 0; }
.srv-table__td--num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.srv-table__td--act { display: flex; justify-content: flex-end; gap: var(--sp-1); }
.srv-table__name { font-weight: 600; color: var(--text); }
.srv-table__url { color: var(--text-faint); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Icon buttons ─────────────────────────────────────────────── */
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
  transition: background var(--motion) var(--ease), color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.icon-btn:hover { background: var(--surface-2); color: var(--text); border-color: var(--border-strong); }
.icon-btn--danger:hover { color: var(--fail); border-color: var(--fail); }

/* ── Ghost / primary buttons (match Tools.vue modal pattern) ──── */
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: background var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.btn-ghost:hover { background: var(--surface-2); border-color: var(--border-strong); }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost.is-busy { opacity: .6; }
.btn-ghost--sm { padding: 2px var(--sp-2); font-size: var(--fs-xs); }

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-4);
  border: none;
  border-radius: var(--r-md);
  background: var(--brand);
  color: var(--brand-contrast);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.btn-primary:hover { background: var(--brand-strong); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }

.btn-danger {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-4);
  border: none;
  border-radius: var(--r-md);
  background: var(--fail);
  color: var(--on-fail);
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion) var(--ease);
}
.btn-danger:hover { background: var(--fail-strong); }
.btn-danger:disabled { opacity: .5; cursor: not-allowed; }

/* ── Tools list ───────────────────────────────────────────────── */
.tools-list { display: flex; flex-direction: column; gap: var(--sp-2); margin-top: var(--sp-3); }
.tool-item {
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  background: var(--surface);
  transition: border-color var(--motion) var(--ease);
}
.tool-item:hover { border-color: var(--border-strong); }
.tool-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-3);
  cursor: pointer;
  list-style: none;
  user-select: none;
}
.tool-item__head::-webkit-details-marker { display: none; }
.tool-item__main { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.tool-item__icon { color: var(--brand-strong); flex-shrink: 0; }
.tool-item__name { font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-item__meta { display: flex; align-items: center; gap: var(--sp-3); flex-shrink: 0; }
.tool-item__calls { font-size: var(--fs-sm); color: var(--text-muted); }
.tool-item__calls b { color: var(--text); font-variant-numeric: tabular-nums; }
.tool-item__chevron { color: var(--text-faint); transition: transform var(--motion) var(--ease); }
.tool-item[open] .tool-item__chevron { transform: rotate(180deg); }
.tool-item__body { padding: 0 var(--sp-3) var(--sp-3); border-top: 1px solid var(--border-subtle); }
.tool-item__desc { font-size: var(--fs-sm); color: var(--text); margin: var(--sp-2) 0; line-height: 1.55; }
.tool-item__params { margin-top: var(--sp-2); }
.tool-item__params-label { font-size: var(--fs-xs); font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); margin-bottom: var(--sp-1); }
.tool-item__schema {
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--text);
  overflow-x: auto;
  white-space: pre-wrap;
  max-height: 240px;
  overflow-y: auto;
}

/* ── Stats ────────────────────────────────────────────────────── */
.stats-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.stats-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--sp-3); }
.stats-card {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.stats-card__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.stats-card__value { font-size: var(--fs-xl); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }
.stats-card__value.is-ok { color: var(--success); }
.stats-card__value.is-warn { color: var(--warn); }
.stats-card__value.is-fail { color: var(--fail); }

.stats-chart { border: 1px solid var(--border-subtle); border-radius: var(--r-md); padding: var(--sp-3); }
.stats-chart__head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); margin-bottom: var(--sp-3); flex-wrap: wrap; }
.stats-chart__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.stats-chart__svg { width: 100%; height: 180px; display: block; }
.stats-chart__grid { stroke: var(--border-subtle); stroke-width: 1; }
.stats-chart__area { fill: var(--brand-soft); }
.stats-chart__line { fill: none; stroke: var(--brand); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.stats-chart__dot { fill: var(--brand); }
.stats-chart__empty { text-align: center; padding: var(--sp-4); }

.stats-per-tool { margin-top: var(--sp-2); }
.stats-per-tool__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); margin-bottom: var(--sp-2); }

/* ── Concurrency ──────────────────────────────────────────────── */
.conc-hint { font-size: var(--fs-sm); color: var(--text-muted); margin-bottom: var(--sp-3); line-height: 1.55; }
.conc-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.conc-table__head,
.conc-table__row {
  display: grid;
  grid-template-columns: 1fr 1fr auto 200px;
  align-items: center;
  gap: var(--sp-2);
}
.conc-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.conc-table__th { font-size: var(--fs-xs); font-weight: 600; text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.conc-table__th--num { text-align: right; }
.conc-table__row { padding: var(--sp-3); border-bottom: 1px solid var(--border-subtle); transition: background var(--motion) var(--ease); }
.conc-table__row:hover { background: var(--surface-2); }
.conc-table__row:last-child { border-bottom: none; }
.conc-table__td { min-width: 0; }
.conc-table__td--num { text-align: right; }
.conc-table__name { font-weight: 600; color: var(--text); }
.conc-edit { display: flex; align-items: center; gap: var(--sp-2); justify-content: flex-end; }
.conc-edit__input {
  width: 80px;
  padding: var(--sp-1) var(--sp-2);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: var(--font-mono);
  text-align: right;
}
.conc-edit__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }

/* ── Modal ────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--overlay-scrim);
  padding: var(--sp-4);
}
.modal {
  width: 100%;
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-modal);
}
.modal--sm { max-width: 400px; }
.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border-subtle);
}
.modal__head h3 { font-size: var(--fs-lg); font-weight: 600; color: var(--text); margin: 0; }
.modal__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}
.modal__x:hover { background: var(--surface-2); color: var(--text); }
.modal__body { padding: var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-3); }
.modal__body p { color: var(--text); line-height: 1.55; }
.modal__target { margin-top: var(--sp-2); color: var(--text-muted); font-size: var(--fs-sm); }
.modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border-subtle);
}

/* ── Form fields ──────────────────────────────────────────────── */
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field__label { font-size: var(--fs-sm); font-weight: 600; color: var(--text); }
.field__input {
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.field__input::placeholder { color: var(--text-faint); }
.field__select { cursor: pointer; appearance: none; -webkit-appearance: none; background-image: var(--icon-chevron); background-repeat: no-repeat; background-position: right var(--sp-2) center; padding-right: var(--sp-7); }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .stats-overview { grid-template-columns: 1fr; }
  .field-row { grid-template-columns: 1fr; }
  .srv-table__head,
  .srv-table__row { grid-template-columns: 1fr auto 96px; }
  .srv-table__th--num:nth-child(3),
  .srv-table__td--num:nth-child(3) { display: none; }
  .conc-table__head,
  .conc-table__row { grid-template-columns: 1fr auto 180px; }
  .conc-table__th:nth-child(2),
  .conc-table__td:nth-child(2) { display: none; }
}

@media (max-width: 640px) {
  .srv-table__head,
  .srv-table__row { grid-template-columns: 1fr 96px; }
  .srv-table__th--num,
  .srv-table__td--num { display: none; }
  .conc-table__head,
  .conc-table__row { grid-template-columns: 1fr 160px; }
  .conc-table__th--num,
  .conc-table__td--num:nth-child(3) { display: none; }
  .tool-item__meta .tool-item__calls { display: none; }
  .modal__body { padding: var(--sp-3); }
  .modal__foot { flex-direction: column-reverse; }
  .modal__foot .btn-ghost,
  .modal__foot .btn-primary,
  .modal__foot .btn-danger { width: 100%; justify-content: center; }
}
</style>
