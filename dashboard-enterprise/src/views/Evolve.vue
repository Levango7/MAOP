<template>
  <div class="evolve-page">
    <PageHeader>
      <span class="subtitle muted">{{ t('view.evolve.subtitle') }}</span>
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

      <!-- ── 企业版差异化叙事: 演化里程碑垂直时间线 ──
           个人版不渲染此区块 (edition.isEnterprise 控制)。
           纯静态叙事: 不调用任何 API, 不修改任何数据流, 只读 i18n + edition store。
           里程碑数据为内嵌常量, 用来说明 MAOP 自演化能力的典型历史轨迹。 -->
      <section
        v-if="edition.isEnterprise"
        class="evolve-milestones"
        aria-label="Evolution milestones timeline"
      >
        <header class="evolve-milestones__head">
          <div class="evolve-milestones__title-row">
            <AppIcon name="activity" :size="16" class="evolve-milestones__title-icon" />
            <h2 class="evolve-milestones__title">{{ t('view.evolve.milestones.title') }}</h2>
            <Badge tone="brand">{{ edition.edition }}</Badge>
          </div>
          <p class="evolve-milestones__subtitle muted">{{ t('view.evolve.milestones.subtitle') }}</p>
        </header>

        <ol class="evolve-milestones__timeline" role="list">
          <li
            v-for="(ms, idx) in evolutionMilestones"
            :key="ms.key"
            class="evolve-milestones__node"
            :class="['evolve-milestones__node--' + ms.type, { 'evolve-milestones__node--first': idx === 0, 'evolve-milestones__node--last': idx === evolutionMilestones.length - 1 }]"
            role="listitem"
          >
            <div class="evolve-milestones__rail" aria-hidden="true">
              <span class="evolve-milestones__dot"></span>
            </div>
            <div class="evolve-milestones__card">
              <div class="evolve-milestones__card-head">
                <div class="evolve-milestones__icon-wrap">
                  <AppIcon :name="ms.typeIcon" :size="14" class="evolve-milestones__type-icon" />
                </div>
                <span class="evolve-milestones__time">{{ ms.time }}</span>
                <Badge :tone="milestoneToneMap[ms.type]">{{ milestoneTypeLabel(ms.type) }}</Badge>
              </div>
              <h3 class="evolve-milestones__card-title">{{ ms.title }}</h3>
              <p class="evolve-milestones__card-desc muted">{{ ms.desc }}</p>
              <div class="evolve-milestones__impact">
                <span class="evolve-milestones__impact-label">{{ t('view.evolve.milestones.impact') }}</span>
                <span class="evolve-milestones__impact-value">{{ ms.impact }}</span>
              </div>
            </div>
          </li>
        </ol>
      </section>

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
      <!-- 迭代 B1: 世系时间线(故事化) + 明细表格(下钻)并存 -->
      <EvolutionTimeline v-if="lineage.length" :items="lineage" class="evo-tl" />
      <DataTable
        v-if="lineageRows.length"
        :columns="lineageCols"
        :rows="lineageRows"
        row-key="key"
        :empty-text="t('view.evolve.lineage.empty')"
      />
      <EmptyState v-else-if="!lineage.length" icon="git-branch" :title="t('view.evolve.lineage.empty')" />
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
import { useEditionStore } from '../stores/edition.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Segmented from '../components/Segmented.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import Badge from '../components/Badge.vue';
import EvolutionTimeline from '../components/EvolutionTimeline.vue';
import EvolutionHistory from './EvolutionHistory.vue';
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
import { useI18n } from '../i18n';

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler, Legend);

const { t } = useI18n();
const api = useApiStore();
const edition = useEditionStore();
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
        borderColor: cssVar('--chart-1'),
        backgroundColor: cssVarAlpha('--chart-1', 0.12),
        tension: 0.3,
        fill: true,
        yAxisID: 'y',
      },
      {
        label: t('view.evolve.colAvgMs'),
        data: items.map((it) => it.duration_s),
        borderColor: cssVar('--chart-4'),
        backgroundColor: cssVarAlpha('--chart-4', 0.08),
        tension: 0.3,
        yAxisID: 'y1',
      },
      {
        label: t('view.evolve.colTotal'),
        data: items.map((it) => it.suggestions_applied),
        borderColor: cssVar('--chart-3'),
        backgroundColor: cssVarAlpha('--chart-3', 0.08),
        tension: 0.3,
        yAxisID: 'y1',
      },
    ],
  };
});

// 双 y 轴时序图(特例, 不套用 baseLineOptions 单轴工厂);
// hover 交互相约一律遵循 composables/chartOptions.js 的规范(mode:index)
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

// ── 演化里程碑叙事 (企业版差异化) ──────────────────────────────────
// 纯静态叙事数据: 不调用任何 API, 不依赖 lineage/timeseries ref。
// 用来说明 MAOP 自演化能力的典型里程碑, 让企业版用户在进入 Evolve 页时
// 立即理解"自调优闭环"的价值, 而不是面对一张空表。
// type: perf(性能改进) / behavior(行为调整) / capability(新能力)
// impact: 简短的影响指标描述, 用于卡片右下角
const evolutionMilestones = [
  {
    key: 'ms-cap-1',
    time: '2026-03',
    type: 'capability',
    typeIcon: 'sparkles',
    title: 'Self-tuning loop bootstrapped',
    desc: 'MAOP introduced the closed-loop auto-tuning engine: observe → suggest → A/B → promote/rollback.',
    impact: '+1 closed loop',
  },
  {
    key: 'ms-perf-1',
    time: '2026-04',
    type: 'perf',
    typeIcon: 'gauge',
    title: 'Routing strategy optimized',
    desc: 'Cost-aware router reduced avg latency by delegating cheap tasks to local models.',
    impact: '−23% p95 latency',
  },
  {
    key: 'ms-behavior-1',
    time: '2026-05',
    type: 'behavior',
    typeIcon: 'brain',
    title: 'Prompt regression guard',
    desc: 'Per-agent prompt versions now gated by SPRT, blocking silent quality regressions before promotion.',
    impact: '+12% success rate',
  },
  {
    key: 'ms-cap-2',
    time: '2026-06',
    type: 'capability',
    typeIcon: 'network',
    title: 'Cross-agent lineage tracking',
    desc: 'Configuration lineage now records parent → child version chains across all agents for full auditability.',
    impact: '+full audit trail',
  },
  {
    key: 'ms-perf-2',
    time: '2026-07',
    type: 'perf',
    typeIcon: 'zap',
    title: 'Parallel A/B experiments',
    desc: 'Multiple experiments can now run concurrently per agent, cutting tuning cycle time from hours to minutes.',
    impact: '−68% cycle time',
  },
  {
    key: 'ms-behavior-2',
    time: '2026-08',
    type: 'behavior',
    typeIcon: 'shield',
    title: 'Human-in-the-loop gate',
    desc: 'High-risk config changes now require explicit approval, with pending queue and one-click approve/reject.',
    impact: '0 unapproved promotions',
  },
];

// 里程碑类型 → 徽章 tone 映射
const milestoneToneMap = {
  perf: 'success',
  behavior: 'info',
  capability: 'brand',
};
function milestoneTypeLabel(type) {
  const key = `view.evolve.milestones.type.${type}`;
  return t(key);
}

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
  edition.fetchEdition().catch(() => {});
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

/* ── 演化里程碑垂直时间线叙事 (企业版差异化) ───────────────────────────
 * 设计语言对齐 workbench: 1px 描边卡片, 无阴影/无渐变, 中性灰底。
 * 垂直时间线: 左侧 2px 轨道线 + 圆点, 右侧事件卡片。
 * 三种事件类型用不同颜色的圆点和徽章: perf=success / behavior=info / capability=brand。
 * 响应式: ≤700px 时卡片宽度自适应, 轨道线保持。 */
.evolve-milestones {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  margin-bottom: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.evolve-milestones__head {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.evolve-milestones__title-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.evolve-milestones__title-icon {
  color: var(--brand-strong);
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: var(--r-sm);
  background: var(--brand-soft);
  border: 1px solid var(--brand-faint);
  flex-shrink: 0;
}
.evolve-milestones__title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
  margin: 0;
}
.evolve-milestones__subtitle {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  margin: 0;
}
.evolve-milestones__timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  position: relative;
}
.evolve-milestones__node {
  position: relative;
  display: grid;
  grid-template-columns: 32px 1fr;
  gap: var(--sp-3);
  padding-bottom: var(--sp-4);
}
.evolve-milestones__node--last { padding-bottom: 0; }
.evolve-milestones__rail {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 14px;
}
/* 垂直轨道线: 除最后一个节点外, 从圆点向下延伸 */
.evolve-milestones__node:not(.evolve-milestones__node--last) .evolve-milestones__rail::after {
  content: "";
  position: absolute;
  top: 28px;
  bottom: -16px;
  left: 50%;
  transform: translateX(-50%);
  width: 2px;
  background: var(--border-subtle, var(--border));
  border-radius: 1px;
}
.evolve-milestones__dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--surface);
  box-shadow: 0 0 0 1px var(--border-strong);
  background: var(--text-faint);
  flex-shrink: 0;
  z-index: 1;
}
.evolve-milestones__node--perf       .evolve-milestones__dot { background: var(--success); box-shadow: 0 0 0 1px var(--success); }
.evolve-milestones__node--behavior   .evolve-milestones__dot { background: var(--info); box-shadow: 0 0 0 1px var(--info); }
.evolve-milestones__node--capability .evolve-milestones__dot { background: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.evolve-milestones__card {
  background: var(--surface-2);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-md);
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  transition: border-color var(--motion) var(--ease);
}
.evolve-milestones__card:hover { border-color: var(--border-strong); }
.evolve-milestones__node--perf       .evolve-milestones__card { border-left: 3px solid var(--success); }
.evolve-milestones__node--behavior   .evolve-milestones__card { border-left: 3px solid var(--info); }
.evolve-milestones__node--capability .evolve-milestones__card { border-left: 3px solid var(--brand); }
.evolve-milestones__card-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.evolve-milestones__icon-wrap {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: var(--r-sm);
  background: var(--surface);
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.evolve-milestones__node--perf       .evolve-milestones__type-icon { color: var(--success); }
.evolve-milestones__node--behavior   .evolve-milestones__type-icon { color: var(--info); }
.evolve-milestones__node--capability .evolve-milestones__type-icon { color: var(--brand-strong); }
.evolve-milestones__time {
  font-size: var(--fs-xs);
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  letter-spacing: .02em;
}
.evolve-milestones__card-title {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text);
  margin: 0;
  line-height: 1.35;
}
.evolve-milestones__card-desc {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  line-height: 1.55;
  margin: 0;
}
.evolve-milestones__impact {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding-top: var(--sp-1);
  border-top: 1px dashed var(--border-subtle, var(--border));
  margin-top: 2px;
}
.evolve-milestones__impact-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: var(--text-faint);
}
.evolve-milestones__impact-value {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
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
  border: 1px solid var(--border-light, rgba(148,163,184,.35));
  padding: 4px 6px;
  text-align: center;
  white-space: nowrap;
}
.heatmap__corner,
.heatmap__col-head,
.heatmap__row-head {
  background: var(--surface-2, rgba(148,163,184,.10));
  color: var(--text-muted);
  font-weight: 600;
}
.heatmap__cell--empty { background: var(--surface); color: var(--text-faint); }
.heatmap__cell--hi { background: var(--success-bg); color: var(--success-strong); font-weight: 600; }
.heatmap__cell--mid { background: var(--success-soft); color: var(--success); }
.heatmap__cell--lo { background: var(--fail-bg); color: var(--fail-strong); }

.muted { color: var(--text-muted); }

/* ── 演化里程碑响应式 ── */
@media (max-width: 700px) {
  .evolve-milestones__node {
    grid-template-columns: 24px 1fr;
    gap: var(--sp-2);
  }
  .evolve-milestones__card {
    padding: var(--sp-2);
  }
  .evolve-milestones__card-title {
    font-size: var(--fs-sm);
  }
}
@media (max-width: 480px) {
  .evolve-milestones__impact {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }
}
</style>
