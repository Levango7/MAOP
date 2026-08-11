<template>
  <div class="evolve-page">
    <PageHeader>
      <Segmented v-model="tab" :options="tabOptions" size="sm" class="evolve-tabs" />
      <span class="status-badge" :class="evolving ? 'running' : 'idle'">
        <AppIcon :name="evolving ? 'refresh' : 'check-circle'" :size="13" :class="{ spinning: evolving }" />
        {{ evolving ? t('view.evolve.evolving') : t('view.evolve.idle') }}
      </span>
      <button class="btn-action" :disabled="evolving" @click="triggerEvolve">
        <AppIcon name="sparkles" :size="15" /> {{ t('view.evolve.trigger') }}
      </button>
    </PageHeader>

    <!-- Tab 容器: main=演化控制台 / history=演化历史(嵌入原 EvolutionHistory 页) -->
    <div v-show="tab === 'main'" class="evolve-main">
      <div class="stats-row">
        <StatCard :label="t('view.evolve.totalEvolutions')" :value="totalEvolutions" icon="activity" tone="brand" :loading="loading" />
        <StatCard :label="t('view.evolve.avgSuccessRate')" :value="successRate" unit="%" icon="check-circle" tone="success" :loading="loading" />
        <StatCard :label="t('view.evolve.agentsTracked')" :value="agentsTracked" icon="bot" tone="info" :loading="loading" />
        <StatCard :label="t('view.evolve.bestAgent')" :value="bestAgentLabel" icon="star" tone="warn" :loading="loading" />
      </div>

    <Card :title="t('view.evolve.statsByAgent')" icon="gauge" :margin-bottom="16">
      <DataTable
        v-if="byAgent.length"
        :columns="agentCols"
        :rows="byAgent"
        row-key="agent"
        :loading="loading"
        :empty-text="t('view.evolve.noData')"
      />
      <EmptyState
v-else-if="!loading" icon="gauge" :title="t('view.evolve.noData')"
                  :description="t('view.evolve.noDataDesc')" />
      <Skeleton v-else height="160px" />
    </Card>

    <!-- P2-12: Evolution metrics trend (Chart.js) -->
    <Card :title="t('view.evolve.timeseries.title')" icon="activity" :margin-bottom="16">
      <div class="evolve-chart-desc muted">{{ t('view.evolve.timeseries.desc') }}</div>
      <div class="evolve-chart-box">
        <Line v-if="timeseriesChartData.labels.length" :data="timeseriesChartData" :options="timeseriesChartOptions" />
        <EmptyState v-else icon="activity" :title="t('view.evolve.noData')" :description="t('view.evolve.historyNADesc')" />
      </div>
    </Card>

    <!-- P2-12: Strategy effectiveness heatmap -->
    <Card :title="t('view.evolve.heatmap.title')" icon="grid" :margin-bottom="16">
      <div class="evolve-chart-desc muted">{{ t('view.evolve.heatmap.desc') }}</div>
      <div v-if="heatmapCells.length" class="heatmap">
        <table class="heatmap__table">
          <thead>
            <tr>
              <th class="heatmap__corner">{{ t('view.evolve.colStrategy') }} \\ {{ t('view.evolve.colAgent') }}</th>
              <th v-for="ag in heatmapAgents" :key="ag" class="heatmap__col-head">{{ ag }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="st in heatmapStrategies" :key="st">
              <td class="heatmap__row-head">{{ st }}</td>
              <td
                v-for="ag in heatmapAgents"
                :key="ag"
                class="heatmap__cell"
                :class="heatmapCellClass(st, ag)"
                :title="heatmapCellTitle(st, ag)"
              >{{ heatmapCellLabel(st, ag) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else icon="grid" :title="t('view.evolve.noData')" :description="t('view.evolve.strategiesNADesc')" />
    </Card>

    <!-- P2-12: Agent configuration lineage -->
    <Card :title="t('view.evolve.lineage.title')" icon="git-branch" :margin-bottom="16">
      <div class="evolve-chart-desc muted">{{ t('view.evolve.lineage.desc') }}</div>
      <DataTable
        v-if="lineageRows.length"
        :columns="lineageCols"
        :rows="lineageRows"
        row-key="key"
        :empty-text="t('view.evolve.lineage.empty')"
      />
      <EmptyState v-else icon="git-branch" :title="t('view.evolve.lineage.empty')" />
    </Card>

    <div class="two-col">
      <Card :title="t('view.evolve.strategies')" icon="brain" :margin-bottom="16">
        <EmptyState
icon="brain" :title="t('view.evolve.notAvailable')"
                    :description="t('view.evolve.strategiesNADesc')" />
      </Card>
      <Card :title="t('view.evolve.history')" icon="scroll" :margin-bottom="16">
        <EmptyState
icon="scroll" :title="t('view.evolve.notAvailable')"
                    :description="t('view.evolve.historyNADesc')" />
      </Card>
    </div>

    <Card :title="t('view.evolve.promptHistory')" icon="clipboard" :margin-bottom="16">
      <EmptyState
icon="clipboard" :title="t('view.evolve.notAvailable')"
                  :description="t('view.evolve.promptNADesc')" />
    </Card>
    </div><!-- /.evolve-main -->

    <!-- 嵌入演化历史页: 仅当 tab=history 时渲染, 且自动隐藏其内部 PageHeader -->
    <EvolutionHistory v-if="tab === 'history'" embedded />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Line } from 'vue-chartjs';
import {
  Chart as ChartJS, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler, Legend,
} from 'chart.js';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Segmented from '../components/Segmented.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import EvolutionHistory from './EvolutionHistory.vue';
import { useI18n } from '../i18n';

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler, Legend);

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const route = useRoute();
const router = useRouter();
const loading = ref(false);
const evolving = ref(false);
const byAgent = ref([]);

// ── Tab 状态: main(演化控制台) / history(嵌入演化历史页) ──
// 惰性容错: 测试环境未挂 router 时, route.query 可能不存在 → 默认 'main'
const VALID_TABS = new Set(['main', 'history']);
const tab = ref(VALID_TABS.has(route?.query?.tab) ? route.query.tab : 'main');
const tabOptions = computed(() => [
  { value: 'main', label: t('view.evolve.tabMain'), icon: 'sparkles' },
  { value: 'history', label: t('view.evolve.tabHistory'), icon: 'scroll' },
]);
watch(tab, (v) => {
  if (!VALID_TABS.has(v)) return;
  if (route?.query && route.query.tab !== v) {
    router.replace({ query: { ...route.query, tab: v } }).catch(() => {});
  }
});
watch(() => route?.query?.tab, (v) => {
  if (VALID_TABS.has(v) && v !== tab.value) tab.value = v;
});

// P2-12: Evolution metrics state
const timeseries = ref([]);
const heatmap = ref([]);
const lineage = ref([]);

const totalEvolutions = computed(() => byAgent.value.reduce((s, a) => s + (a.total || 0), 0));
const successRate = computed(() => {
  const total = totalEvolutions.value;
  if (!total) return 0;
  const ok = byAgent.value.reduce((s, a) => s + (a.success || 0), 0);
  return Math.round((ok / total) * 1000) / 10;
});
const agentsTracked = computed(() => byAgent.value.length);
const bestAgent = computed(() => {
  if (!byAgent.value.length) return null;
  return byAgent.value.reduce((best, a) => (a.rate > (best?.rate ?? -1) ? a : best), byAgent.value[0]);
});
const bestAgentLabel = computed(() => (bestAgent.value ? `${bestAgent.value.agent} (${bestAgent.value.rate}%)` : '—'));

const agentColDefs = [
  { key: 'agent', label: 'view.evolve.colAgent' },
  { key: 'total', label: 'view.evolve.colTotal', type: 'num' },
  { key: 'success', label: 'view.evolve.colSuccess', type: 'num' },
  { key: 'fail', label: 'view.evolve.colFail', type: 'num' },
  { key: 'rate', label: 'view.evolve.colRate', type: 'num' },
  { key: 'avg_duration_ms', label: 'view.evolve.colAvgMs', type: 'num' },
];
const agentCols = computed(() => agentColDefs.map((c) => ({ ...c, label: t(c.label) })));

// ── P2-12: Timeseries chart data (success rate / duration / suggestions) ──
const timeseriesChartData = computed(() => {
  const items = timeseries.value;
  if (!items.length) return { labels: [], datasets: [] };
  const labels = items.map((it) => formatTs(it.timestamp));
  return {
    labels,
    datasets: [
      {
        label: t('view.evolve.colRate'),
        data: items.map((it) => it.success_rate),
        borderColor: '#1565C0',
        backgroundColor: 'rgba(21, 101, 192, 0.12)',
        tension: 0.3,
        fill: true,
        yAxisID: 'y',
      },
      {
        label: t('view.evolve.colAvgMs'),
        data: items.map((it) => it.duration_s),
        borderColor: '#E65100',
        backgroundColor: 'rgba(230, 81, 0, 0.08)',
        tension: 0.3,
        yAxisID: 'y1',
      },
      {
        label: t('view.evolve.colTotal'),
        data: items.map((it) => it.suggestions_applied),
        borderColor: '#43A047',
        backgroundColor: 'rgba(67, 160, 71, 0.08)',
        tension: 0.3,
        yAxisID: 'y1',
      },
    ],
  };
});

const timeseriesChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
    tooltip: { callbacks: {} },
  },
  scales: {
    x: { ticks: { font: { size: 10 }, maxRotation: 0 } },
    y: {
      type: 'linear',
      position: 'left',
      title: { display: true, text: '%', font: { size: 10 } },
      beginAtZero: true,
      max: 100,
    },
    y1: {
      type: 'linear',
      position: 'right',
      title: { display: true, text: 's / count', font: { size: 10 } },
      beginAtZero: true,
      grid: { drawOnChartArea: false },
    },
  },
};

function formatTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts * 1000);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch {
    return String(ts);
  }
}

// ── P2-12: Heatmap (strategy × agent) ──
// P2-12 fix: 模板引用 heatmapCells，补 computed 桥接 heatmap ref
const heatmapCells = computed(() => heatmap.value);
const heatmapStrategies = computed(() => {
  const set = new Set();
  for (const c of heatmap.value) set.add(c.strategy);
  return Array.from(set).sort();
});
const heatmapAgents = computed(() => {
  const set = new Set();
  for (const c of heatmap.value) set.add(c.agent);
  return Array.from(set).sort();
});
const heatmapLookup = computed(() => {
  const m = new Map();
  for (const c of heatmap.value) {
    m.set(`${c.strategy}::${c.agent}`, c);
  }
  return m;
});
function heatmapCell(strategy, agent) {
  return heatmapLookup.value.get(`${strategy}::${agent}`);
}
function heatmapCellLabel(strategy, agent) {
  const c = heatmapCell(strategy, agent);
  if (!c) return '';
  return c.gain.toFixed(2);
}
function heatmapCellClass(strategy, agent) {
  const c = heatmapCell(strategy, agent);
  if (!c) return 'heatmap__cell--empty';
  if (c.gain > 0.5) return 'heatmap__cell--hi';
  if (c.gain > 0) return 'heatmap__cell--mid';
  return 'heatmap__cell--lo';
}
function heatmapCellTitle(strategy, agent) {
  const c = heatmapCell(strategy, agent);
  if (!c) return '';
  const parts = [`strategy=${c.strategy}`, `agent=${c.agent}`, `gain=${c.gain}`];
  if (c.routing_key) parts.push(`routing_key=${c.routing_key}`);
  if (c.suggested_alternative) parts.push(`alt=${c.suggested_alternative}`);
  return parts.join('\n');
}

// ── P2-12: Lineage table ──
const lineageColDefs = [
  { key: 'agent', label: 'view.evolve.colAgent' },
  { key: 'version', label: 'view.evolve.colVersion', type: 'num' },
  { key: 'change', label: 'view.evolve.colChange' },
  { key: 'applied_at', label: 'view.evolve.colTimestamp' },
  { key: 'improved', label: 'view.evolve.colImproved', type: 'bool-icon' },
];
const lineageCols = computed(() => lineageColDefs.map((c) => ({ ...c, label: t(c.label) })));
const lineageRows = computed(() =>
  lineage.value.map((l, i) => ({
    key: `${l.agent}-${l.version}-${i}`,
    agent: l.agent,
    version: l.version,
    change: l.to_config || l.from_config || '—',
    applied_at: formatTs(l.applied_at),
    improved: l.improved,
  })),
);

function normalize(raw) {
  const data = raw && raw.data ? raw.data : raw;
  const stats = data && data.stats ? data.stats : {};
  const list = Array.isArray(stats.by_agent) ? stats.by_agent : [];
  return list.map((a) => ({
    agent: a.agent,
    total: a.total || 0,
    success: a.success || 0,
    fail: a.fail || 0,
    rate: a.rate !== null && a.rate !== undefined ? a.rate : 0,
    avg_duration_ms: a.avg_duration_ms || 0,
  }));
}

async function loadStatus() {
  loading.value = true;
  try {
    const raw = await api.get('/api/evolve/status');
    byAgent.value = normalize(raw);
  } catch {
    byAgent.value = [];
  } finally {
    loading.value = false;
  }
}

// P2-12: 加载演化指标聚合（时间序列 / 热力图 / 世系）
async function loadMetrics() {
  try {
    const res = await api.get('/api/evolve/metrics');
    timeseries.value = res.timeseries || [];
    heatmap.value = res.heatmap || [];
    lineage.value = res.lineage || [];
  } catch {
    // 指标加载失败不阻塞主页面
    timeseries.value = [];
    heatmap.value = [];
    lineage.value = [];
  }
}

async function triggerEvolve() {
  evolving.value = true;
  try {
    await api.post('/api/evolve/analyze', { strategies: 'all' });
    toast.success(t('view.evolve.triggered'));
    await loadStatus();
    await loadMetrics();
  } catch (e) {
    toast.error(e.message || t('view.evolve.failed'));
  } finally {
    evolving.value = false;
  }
}

onMounted(() => {
  loadStatus();
  loadMetrics();
});
</script>

<style scoped>
.evolve-chart-desc {
  font-size: 12px;
  margin-bottom: 8px;
}
.evolve-chart-box {
  height: 280px;
  position: relative;
}

/* ── Heatmap ── */
.heatmap {
  overflow-x: auto;
  padding: 4px 0;
}
.heatmap__table {
  border-collapse: collapse;
  font-size: 11px;
  width: 100%;
}
.heatmap__table th,
.heatmap__table td {
  border: 1px solid var(--border-light, #e2e8f0);
  padding: 4px 6px;
  text-align: center;
  white-space: nowrap;
}
.heatmap__corner,
.heatmap__col-head,
.heatmap__row-head {
  background: var(--surface-2, #f8fafc);
  color: var(--text-muted);
  font-weight: 600;
}
.heatmap__cell--empty { background: var(--surface); color: var(--text-faint); }
.heatmap__cell--hi { background: #C8E6C9; color: #1B5E20; font-weight: 600; }
.heatmap__cell--mid { background: #DCEDC8; color: #33691E; }
.heatmap__cell--lo { background: #FFCDD2; color: #B71C1C; }

.muted { color: var(--text-muted); }
</style>
