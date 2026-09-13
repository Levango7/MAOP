<template>
  <div class="an-view">
    <ListPageLayout>
      <template #actions>
        <Segmented
          :model-value="dateRange"
          :options="dateRangeOptions"
          size="sm"
          @update:model-value="setDateRange"
        />
        <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <Segmented
          :model-value="activeTab"
          :options="tabOptions"
          size="sm"
          @update:model-value="activeTab = $event"
        />

        <!-- ── Summary ─────────────────────────────────────────── -->
        <div v-show="activeTab === 'summary'" class="an-section">
          <Card :title="t('view.analysis.summary.title')" icon="activity" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="120px" />
            </div>
            <EmptyState
              v-else-if="errors.summary"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.summary.failedLoad')"
              :description="errors.summary"
            />
            <EmptyState
              v-else-if="!hasSummaryData"
              icon="activity"
              :title="t('view.analysis.summary.empty')"
              :description="t('view.analysis.summary.emptyHint')"
            />
            <div v-else class="an-kpi-grid">
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.totalTasks') }}</span>
                <span class="an-kpi__value">{{ summaryData.total_tasks ?? 0 }}</span>
              </div>
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.successRate') }}</span>
                <span class="an-kpi__value" :class="successRateTone">{{ formatPct(summaryData.success_rate) }}</span>
              </div>
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.totalCost') }}</span>
                <span class="an-kpi__value">${{ formatNum(summaryData.total_cost_usd) }}</span>
              </div>
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.avgLatency') }}</span>
                <span class="an-kpi__value">{{ formatNum(summaryData.avg_latency_ms) }} ms</span>
              </div>
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.activeAgents') }}</span>
                <span class="an-kpi__value">{{ summaryData.active_agents ?? 0 }}</span>
              </div>
              <div class="an-kpi">
                <span class="an-kpi__label">{{ t('view.analysis.summary.tokens') }}</span>
                <span class="an-kpi__value">{{ formatNum(summaryData.total_tokens) }}</span>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Agent efficiency ───────────────────────────────── -->
        <div v-show="activeTab === 'agents'" class="an-section">
          <Card :title="t('view.analysis.agents.title')" icon="bot" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.agents"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.agents.failedLoad')"
              :description="errors.agents"
            />
            <EmptyState
              v-else-if="!agentRows.length"
              icon="bot"
              :title="t('view.analysis.agents.empty')"
              :description="t('view.analysis.agents.emptyHint')"
            />
            <DataTable
              v-else
              :columns="agentCols"
              :rows="agentRows"
              :empty-text="t('view.analysis.agents.empty')"
              compact
            />
          </Card>
        </div>

        <!-- ── Task trends ─────────────────────────────────────── -->
        <div v-show="activeTab === 'trends'" class="an-section">
          <Card :title="t('view.analysis.trends.title')" icon="activity" margin-bottom="var(--sp-4)">
            <template #actions>
              <Segmented
                :model-value="trendsGranularity"
                :options="granularityOptions"
                size="sm"
                @update:model-value="setGranularity"
              />
            </template>
            <div v-if="loading" class="blk">
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.trends"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.trends.failedLoad')"
              :description="errors.trends"
            />
            <EmptyState
              v-else-if="!trendsSeries.length"
              icon="activity"
              :title="t('view.analysis.trends.empty')"
              :description="t('view.analysis.trends.emptyHint')"
            />
            <div v-else class="an-trends">
              <div class="an-chart">
                <svg
                  class="an-chart__svg"
                  :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="t('view.analysis.trends.chart')"
                >
                  <line v-for="(g, i) in trendGridLines" :key="'g' + i" :x1="g.x1" :y1="g.y1" :x2="g.x2" :y2="g.y2" class="an-chart__grid" />
                  <path :d="trendSuccessArea" class="an-chart__area an-chart__area--success" />
                  <path :d="trendSuccessLine" class="an-chart__line an-chart__line--success" />
                  <path :d="trendFailureLine" class="an-chart__line an-chart__line--fail" />
                  <path :d="trendTimeoutLine" class="an-chart__line an-chart__line--warn" />
                </svg>
                <div class="an-chart__legend">
                  <span class="an-chart__legend-item"><i class="an-chart__dot an-chart__dot--success"></i>{{ t('view.analysis.trends.col.success') }}</span>
                  <span class="an-chart__legend-item"><i class="an-chart__dot an-chart__dot--fail"></i>{{ t('view.analysis.trends.col.failure') }}</span>
                  <span class="an-chart__legend-item"><i class="an-chart__dot an-chart__dot--warn"></i>{{ t('view.analysis.trends.col.timeout') }}</span>
                </div>
              </div>
              <DataTable
                :columns="trendsCols"
                :rows="trendsTableRows"
                :empty-text="t('view.analysis.trends.empty')"
                compact
              />
            </div>
          </Card>
        </div>

        <!-- ── Resources ───────────────────────────────────────── -->
        <div v-show="activeTab === 'resources'" class="an-section">
          <Card :title="t('view.analysis.resources.title')" icon="cpu" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.resources"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.resources.failedLoad')"
              :description="errors.resources"
            />
            <EmptyState
              v-else-if="!hasResourceData"
              icon="cpu"
              :title="t('view.analysis.resources.empty')"
              :description="t('view.analysis.resources.emptyHint')"
            />
            <div v-else class="an-resources">
              <div class="an-resource-grid">
                <div class="an-resource-card">
                  <span class="an-resource-card__label">{{ t('view.analysis.resources.cpu') }}</span>
                  <span class="an-resource-card__value">{{ formatNum(resourceData.cpu_pct) }}%</span>
                </div>
                <div class="an-resource-card">
                  <span class="an-resource-card__label">{{ t('view.analysis.resources.memory') }}</span>
                  <span class="an-resource-card__value">{{ formatNum(resourceData.memory_mb) }}</span>
                </div>
                <div class="an-resource-card">
                  <span class="an-resource-card__label">{{ t('view.analysis.resources.dbPool') }}</span>
                  <span class="an-resource-card__value">{{ resourceData.db_in_use ?? 0 }} / {{ resourceData.db_max_size ?? 0 }}</span>
                </div>
                <div class="an-resource-card">
                  <span class="an-resource-card__label">{{ t('view.analysis.resources.cacheHit') }}</span>
                  <span class="an-resource-card__value">{{ formatPct(resourceData.cache_hit_rate) }}</span>
                </div>
              </div>
              <div v-if="resourceSeries.length" class="an-chart">
                <svg
                  class="an-chart__svg"
                  :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="t('view.analysis.resources.title')"
                >
                  <line v-for="(g, i) in resourceGridLines" :key="'rg' + i" :x1="g.x1" :y1="g.y1" :x2="g.x2" :y2="g.y2" class="an-chart__grid" />
                  <path :d="resourceAreaPath" class="an-chart__area" />
                  <path :d="resourceLinePath" class="an-chart__line" />
                </svg>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Cost breakdown ──────────────────────────────────── -->
        <div v-show="activeTab === 'cost'" class="an-section">
          <Card :title="t('view.analysis.cost.title')" icon="dollar" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="200px" />
            </div>
            <EmptyState
              v-else-if="errors.cost"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.cost.failedLoad')"
              :description="errors.cost"
            />
            <EmptyState
              v-else-if="!costRows.length"
              icon="dollar"
              :title="t('view.analysis.cost.empty')"
              :description="t('view.analysis.cost.emptyHint')"
            />
            <div v-else class="an-cost">
              <div class="an-cost-grid">
                <div class="an-cost-chart">
                  <svg
                    class="an-cost-chart__svg"
                    :viewBox="`0 0 ${PIE_R * 2} ${PIE_R * 2}`"
                    role="img"
                    :aria-label="t('view.analysis.cost.title')"
                  >
                    <path v-for="(s, i) in costPieSlices" :key="s.label + '-' + i" :d="s.d" :class="'an-cost-chart__slice an-cost-chart__slice--' + (i % 5)" />
                  </svg>
                </div>
                <div class="an-cost-legend">
                  <div
                    v-for="(s, i) in costPieSlices"
                    :key="s.label + '-' + i"
                    class="an-cost-legend__item"
                  >
                    <i class="an-cost-legend__dot" :class="'an-cost-legend__dot--' + (i % 5)"></i>
                    <span class="an-cost-legend__label">{{ s.label }}</span>
                    <span class="an-cost-legend__value">${{ formatNum(s.value) }}</span>
                  </div>
                </div>
              </div>
              <div class="an-cost-total">
                {{ t('view.analysis.cost.total') }}: <b>${{ formatNum(costTotal) }}</b>
              </div>
              <DataTable
                :columns="costCols"
                :rows="costRows"
                :empty-text="t('view.analysis.cost.empty')"
                compact
              />
            </div>
          </Card>
        </div>

        <!-- ── Bottlenecks ─────────────────────────────────────── -->
        <div v-show="activeTab === 'bottlenecks'" class="an-section">
          <Card :title="t('view.analysis.bottlenecks.title')" icon="alert-triangle" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="120px" />
              <Skeleton block height="120px" />
              <Skeleton block height="120px" />
            </div>
            <EmptyState
              v-else-if="errors.bottlenecks"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.analysis.bottlenecks.failedLoad')"
              :description="errors.bottlenecks"
            />
            <EmptyState
              v-else-if="!hasBottleneckData"
              icon="alert-triangle"
              :title="t('view.analysis.bottlenecks.empty')"
              :description="t('view.analysis.bottlenecks.emptyHint')"
            />
            <div v-else class="an-bottlenecks">
              <div class="an-bottleneck-section">
                <h4 class="an-bottleneck-section__title">{{ t('view.analysis.bottlenecks.slowEndpoints') }}</h4>
                <DataTable
                  :columns="slowEndpointCols"
                  :rows="slowEndpointRows"
                  :empty-text="t('common.noData')"
                  compact
                />
              </div>
              <div class="an-bottleneck-section">
                <h4 class="an-bottleneck-section__title">{{ t('view.analysis.bottlenecks.expensiveAgents') }}</h4>
                <DataTable
                  :columns="expensiveAgentCols"
                  :rows="expensiveAgentRows"
                  :empty-text="t('common.noData')"
                  compact
                />
              </div>
              <div class="an-bottleneck-section">
                <h4 class="an-bottleneck-section__title">{{ t('view.analysis.bottlenecks.memoryConsumers') }}</h4>
                <DataTable
                  :columns="memoryConsumerCols"
                  :rows="memoryConsumerRows"
                  :empty-text="t('common.noData')"
                  compact
                />
              </div>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>
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
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const { t } = useI18n();

// ── Tab + date range state ──────────────────────────────────────
const activeTab = ref('summary');
const tabOptions = [
  { value: 'summary', label: t('view.analysis.tab.summary'), icon: 'activity' },
  { value: 'agents', label: t('view.analysis.tab.agents'), icon: 'bot' },
  { value: 'trends', label: t('view.analysis.tab.trends'), icon: 'activity' },
  { value: 'resources', label: t('view.analysis.tab.resources'), icon: 'cpu' },
  { value: 'cost', label: t('view.analysis.tab.cost'), icon: 'dollar' },
  { value: 'bottlenecks', label: t('view.analysis.tab.bottlenecks'), icon: 'alert-triangle' },
];

const dateRange = ref('7d');
const dateRangeOptions = [
  { value: '7d', label: t('view.analysis.range.7d') },
  { value: '30d', label: t('view.analysis.range.30d') },
  { value: '90d', label: t('view.analysis.range.90d') },
];

function setDateRange(v) {
  dateRange.value = v;
  loadAll();
}

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({
  summary: null,
  agents: null,
  trends: null,
  resources: null,
  cost: null,
  bottlenecks: null,
});

const summaryData = ref({});
const agentData = ref({ agents: [], total_cost_usd: 0, total_calls: 0 });
const trendsData = ref({ series: [], granularity: 'day' });
const resourceData = ref({});
const resourceSeries = ref([]);
const costData = ref({ items: [], total: 0 });
const bottleneckData = ref({ slow_endpoints: [], expensive_agents: [], memory_consumers: [] });

const trendsGranularity = ref('day');
const granularityOptions = [
  { value: 'hour', label: t('view.analysis.trends.granularity.hour') },
  { value: 'day', label: t('view.analysis.trends.granularity.day') },
  { value: 'week', label: t('view.analysis.trends.granularity.week') },
];

function setGranularity(v) {
  trendsGranularity.value = v;
  loadTrends();
}

// ── Date range helper ───────────────────────────────────────────
function dateRangeParams() {
  const days = dateRange.value === '90d' ? 90 : dateRange.value === '30d' ? 30 : 7;
  const end = new Date();
  const start = new Date(end.getTime() - days * 86400 * 1000);
  return {
    date_from: start.toISOString(),
    date_to: end.toISOString(),
  };
}

// ── Summary ─────────────────────────────────────────────────────
const hasSummaryData = computed(() => Object.keys(summaryData.value).length > 0);

const successRateTone = computed(() => {
  const v = Number(summaryData.value.success_rate);
  if (isNaN(v)) return '';
  if (v >= 0.95) return 'is-ok';
  if (v >= 0.8) return 'is-warn';
  return 'is-fail';
});

// ── Agent efficiency ────────────────────────────────────────────
const agentCols = computed(() => [
  { key: 'agent', label: t('view.analysis.agents.col.agent'), width: '20%' },
  { key: 'total_tasks', label: t('view.analysis.agents.col.tasks'), type: 'num', align: 'right', width: '10%' },
  { key: 'success_rate', label: t('view.analysis.agents.col.successRate'), align: 'right', width: '12%' },
  { key: 'tokens', label: t('view.analysis.agents.col.tokens'), type: 'num', align: 'right', width: '12%' },
  { key: 'cost_usd', label: t('view.analysis.agents.col.cost'), type: 'num', align: 'right', width: '12%' },
  { key: 'avg_latency_ms', label: t('view.analysis.agents.col.latency'), type: 'num', align: 'right', width: '12%' },
  { key: 'tasks_per_usd', label: t('view.analysis.agents.col.tasksPerUsd'), type: 'num', align: 'right', width: '11%' },
  { key: 'circuit_breaker', label: t('view.analysis.agents.col.circuit'), type: 'badge', align: 'right', width: '11%' },
]);

const agentRows = computed(() =>
  (agentData.value.agents || []).map((a) => ({
    ...a,
    success_rate: a.success_rate_pct != null ? `${(Number(a.success_rate_pct)).toFixed(1)}%` : '—',
    cost_usd: a.cost_usd != null ? `$${formatNum(a.cost_usd)}` : '—',
    avg_latency_ms: a.avg_latency_ms != null ? `${formatNum(a.avg_latency_ms)} ms` : '—',
    circuit_breaker: a.circuit_breaker || '—',
  })),
);

// ── Task trends ─────────────────────────────────────────────────
const trendsSeries = computed(() => trendsData.value.series || []);

const trendsCols = computed(() => [
  { key: 'bucket', label: t('view.analysis.trends.col.bucket'), width: '25%' },
  { key: 'success', label: t('view.analysis.trends.col.success'), type: 'num', align: 'right', width: '15%' },
  { key: 'failure', label: t('view.analysis.trends.col.failure'), type: 'num', align: 'right', width: '15%' },
  { key: 'timeout', label: t('view.analysis.trends.col.timeout'), type: 'num', align: 'right', width: '15%' },
  { key: 'total', label: t('view.analysis.trends.col.total'), type: 'num', align: 'right', width: '15%' },
  { key: 'success_rate', label: t('view.analysis.trends.col.successRate'), align: 'right', width: '15%' },
]);

const trendsTableRows = computed(() =>
  trendsSeries.value.map((s) => ({
    ...s,
    success_rate: s.success_rate != null ? `${(s.success_rate * 100).toFixed(1)}%` : '—',
  })),
);

// ── Chart (inline SVG) ──────────────────────────────────────────
const CHART_W = 600;
const CHART_H = 180;
const CHART_PAD = 24;

function buildLinePath(values) {
  if (values.length < 2) return '';
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  const pts = values.map((v, i) => ({
    x: CHART_PAD + (i / (values.length - 1)) * w,
    y: CHART_PAD + h - ((v - min) / range) * h,
  }));
  return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
}

function buildAreaPath(values) {
  if (values.length < 2) return '';
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  const pts = values.map((v, i) => ({
    x: CHART_PAD + (i / (values.length - 1)) * w,
    y: CHART_PAD + h - ((v - min) / range) * h,
  }));
  const base = CHART_H - CHART_PAD;
  const head = `M${pts[0].x.toFixed(1)},${base}`;
  const top = pts.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const tail = `L${pts[pts.length - 1].x.toFixed(1)},${base} Z`;
  return `${head} ${top} ${tail}`;
}

const trendSuccessValues = computed(() => trendsSeries.value.map((s) => s.success ?? 0));
const trendFailureValues = computed(() => trendsSeries.value.map((s) => s.failure ?? 0));
const trendTimeoutValues = computed(() => trendsSeries.value.map((s) => s.timeout ?? 0));

const trendSuccessLine = computed(() => buildLinePath(trendSuccessValues.value));
const trendFailureLine = computed(() => buildLinePath(trendFailureValues.value));
const trendTimeoutLine = computed(() => buildLinePath(trendTimeoutValues.value));
const trendSuccessArea = computed(() => buildAreaPath(trendSuccessValues.value));

const trendGridLines = computed(() => {
  const lines = [];
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  for (let i = 0; i <= 4; i++) {
    const y = CHART_PAD + (i / 4) * h;
    lines.push({ x1: CHART_PAD, y1: y, x2: CHART_PAD + w, y2: y });
  }
  return lines;
});

// ── Resources ───────────────────────────────────────────────────
const hasResourceData = computed(() => Object.keys(resourceData.value).length > 0 || resourceSeries.value.length > 0);

const resourceLineValues = computed(() => resourceSeries.value.map((r) => r.cpu_pct ?? 0));
const resourceLinePath = computed(() => buildLinePath(resourceLineValues.value));
const resourceAreaPath = computed(() => buildAreaPath(resourceLineValues.value));
const resourceGridLines = computed(() => {
  const lines = [];
  const w = CHART_W - CHART_PAD * 2;
  const h = CHART_H - CHART_PAD * 2;
  for (let i = 0; i <= 4; i++) {
    const y = CHART_PAD + (i / 4) * h;
    lines.push({ x1: CHART_PAD, y1: y, x2: CHART_PAD + w, y2: y });
  }
  return lines;
});

// ── Cost breakdown ──────────────────────────────────────────────
const costRows = computed(() => {
  const items = costData.value.items || [];
  return items.map((it) => ({
    dimension: it.dimension || it.name || '—',
    value: `$${formatNum(it.value || it.cost || 0)}`,
    tokens: formatNum(it.tokens || 0),
    calls: it.calls || 0,
  }));
});

const costTotal = computed(() => costData.value.total || 0);

const PIE_R = 80;
const costPieSlices = computed(() => {
  const items = (costData.value.items || []).slice(0, 8);
  const total = items.reduce((sum, it) => sum + (it.value || it.cost || 0), 0);
  if (total <= 0) return [];
  let acc = 0;
  return items.map((it) => {
    const value = it.value || it.cost || 0;
    const start = (acc / total) * 2 * Math.PI;
    acc += value;
    const end = (acc / total) * 2 * Math.PI;
    const x1 = PIE_R + PIE_R * Math.sin(start);
    const y1 = PIE_R - PIE_R * Math.cos(start);
    const x2 = PIE_R + PIE_R * Math.sin(end);
    const y2 = PIE_R - PIE_R * Math.cos(end);
    const largeArc = end - start > Math.PI ? 1 : 0;
    return {
      label: it.dimension || it.name || '—',
      value,
      d: `M${PIE_R},${PIE_R} L${x1.toFixed(1)},${y1.toFixed(1)} A${PIE_R},${PIE_R} 0 ${largeArc} 1 ${x2.toFixed(1)},${y2.toFixed(1)} Z`,
    };
  });
});

const costCols = computed(() => [
  { key: 'dimension', label: t('view.analysis.cost.col.dimension'), width: '40%' },
  { key: 'value', label: t('view.analysis.cost.col.value'), align: 'right', width: '20%' },
  { key: 'tokens', label: t('view.analysis.cost.col.tokens'), type: 'num', align: 'right', width: '20%' },
  { key: 'calls', label: t('view.analysis.cost.col.calls'), type: 'num', align: 'right', width: '20%' },
]);

// ── Bottlenecks ─────────────────────────────────────────────────
const hasBottleneckData = computed(() => {
  const b = bottleneckData.value;
  return (b.slow_endpoints && b.slow_endpoints.length > 0) ||
    (b.expensive_agents && b.expensive_agents.length > 0) ||
    (b.memory_consumers && b.memory_consumers.length > 0);
});

const slowEndpointCols = computed(() => [
  { key: 'name', label: t('view.analysis.bottlenecks.col.name'), width: '60%' },
  { key: 'latency_ms', label: t('view.analysis.bottlenecks.col.latency'), type: 'num', align: 'right', width: '40%' },
]);
const slowEndpointRows = computed(() =>
  (bottleneckData.value.slow_endpoints || []).map((e) => ({
    name: e.name || e.endpoint || '—',
    latency_ms: e.latency_ms || e.latency || 0,
  })),
);

const expensiveAgentCols = computed(() => [
  { key: 'name', label: t('view.analysis.bottlenecks.col.name'), width: '60%' },
  { key: 'cost_usd', label: t('view.analysis.bottlenecks.col.cost'), type: 'num', align: 'right', width: '40%' },
]);
const expensiveAgentRows = computed(() =>
  (bottleneckData.value.expensive_agents || []).map((e) => ({
    name: e.name || e.agent || '—',
    cost_usd: `$${formatNum(e.cost_usd || e.cost || 0)}`,
  })),
);

const memoryConsumerCols = computed(() => [
  { key: 'name', label: t('view.analysis.bottlenecks.col.name'), width: '60%' },
  { key: 'memory_mb', label: t('view.analysis.bottlenecks.col.memory'), type: 'num', align: 'right', width: '40%' },
]);
const memoryConsumerRows = computed(() =>
  (bottleneckData.value.memory_consumers || []).map((e) => ({
    name: e.name || e.process || '—',
    memory_mb: e.memory_mb || e.memory || 0,
  })),
);

// ── Helpers ─────────────────────────────────────────────────────
function formatNum(v) {
  if (v == null) return '0';
  const n = Number(v);
  if (isNaN(n)) return '0';
  if (Math.abs(n) >= 1000000) return (n / 1000000).toFixed(2) + 'M';
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(2) + 'k';
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2);
}
function formatPct(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  // If value looks like 0-1 fraction, multiply by 100
  const pct = n <= 1 ? n * 100 : n;
  return pct.toFixed(1) + '%';
}

// ── Data loading ────────────────────────────────────────────────
async function loadSummary() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    const data = await api.get(`/api/analysis/summary?${params.toString()}`);
    summaryData.value = data.data || data.summary || {};
    errors.value = { ...errors.value, summary: null };
  } catch (err) {
    summaryData.value = {};
    errors.value = { ...errors.value, summary: (err && err.message) || t('view.analysis.summary.failedLoad') };
  }
}

async function loadAgents() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    const data = await api.get(`/api/analysis/agent-efficiency?${params.toString()}`);
    agentData.value = data.data || { agents: [], total_cost_usd: 0, total_calls: 0 };
    errors.value = { ...errors.value, agents: null };
  } catch (err) {
    agentData.value = { agents: [], total_cost_usd: 0, total_calls: 0 };
    errors.value = { ...errors.value, agents: (err && err.message) || t('view.analysis.agents.failedLoad') };
  }
}

async function loadTrends() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    params.set('granularity', trendsGranularity.value);
    const data = await api.get(`/api/analysis/task-trends?${params.toString()}`);
    trendsData.value = data.data || { series: [], granularity: trendsGranularity.value };
    errors.value = { ...errors.value, trends: null };
  } catch (err) {
    trendsData.value = { series: [], granularity: trendsGranularity.value };
    errors.value = { ...errors.value, trends: (err && err.message) || t('view.analysis.trends.failedLoad') };
  }
}

async function loadResources() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    const data = await api.get(`/api/analysis/resource-utilization?${params.toString()}`);
    const d = data.data || {};
    resourceData.value = d;
    resourceSeries.value = d.series || d.timeseries || [];
    errors.value = { ...errors.value, resources: null };
  } catch (err) {
    resourceData.value = {};
    resourceSeries.value = [];
    errors.value = { ...errors.value, resources: (err && err.message) || t('view.analysis.resources.failedLoad') };
  }
}

async function loadCost() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    const data = await api.get(`/api/analysis/cost-breakdown?${params.toString()}`);
    const d = data.data || {};
    costData.value = {
      items: d.items || d.by_agent || d.breakdown || [],
      total: d.total || d.total_cost_usd || 0,
    };
    errors.value = { ...errors.value, cost: null };
  } catch (err) {
    costData.value = { items: [], total: 0 };
    errors.value = { ...errors.value, cost: (err && err.message) || t('view.analysis.cost.failedLoad') };
  }
}

async function loadBottlenecks() {
  try {
    const params = new URLSearchParams(dateRangeParams());
    const data = await api.get(`/api/analysis/performance-bottlenecks?${params.toString()}`);
    const d = data.data || {};
    bottleneckData.value = {
      slow_endpoints: d.slow_endpoints || d.slowest_endpoints || [],
      expensive_agents: d.expensive_agents || d.most_expensive_agents || [],
      memory_consumers: d.memory_consumers || d.top_memory_consumers || [],
    };
    errors.value = { ...errors.value, bottlenecks: null };
  } catch (err) {
    bottleneckData.value = { slow_endpoints: [], expensive_agents: [], memory_consumers: [] };
    errors.value = { ...errors.value, bottlenecks: (err && err.message) || t('view.analysis.bottlenecks.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([
    loadSummary(),
    loadAgents(),
    loadTrends(),
    loadResources(),
    loadCost(),
    loadBottlenecks(),
  ]);
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

.an-section { margin-top: var(--sp-4); }

/* ── Buttons ───────────────────────────────────────────────────── */
/* NOTE: .btn-ghost/.btn-primary/.modal 等为多 view 共享样式，重复定义见
   AgentBilling/AgentGateway/AgentProxy/AgentRegistry/Blackboard/Debate/Feedback。
   全局基础样式见 src/styles/pages.css（BEM 命名 .btn--ghost），此处 scoped 隔离不冲突。 */
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-3);
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

/* ── KPI grid ──────────────────────────────────────────────────── */
.an-kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
}
.an-kpi {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.an-kpi__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.an-kpi__value { font-size: var(--fs-xl); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }
.an-kpi__value.is-ok { color: var(--success); }
.an-kpi__value.is-warn { color: var(--warn); }
.an-kpi__value.is-fail { color: var(--fail); }

/* ── Trends ────────────────────────────────────────────────────── */
.an-trends { display: flex; flex-direction: column; gap: var(--sp-4); }

/* ── Chart ────────────────────────────────────────────────────── */
.an-chart { border: 1px solid var(--border-subtle); border-radius: var(--r-md); padding: var(--sp-3); }
.an-chart__svg { width: 100%; height: 180px; display: block; }
.an-chart__grid { stroke: var(--border-subtle); stroke-width: 1; }
.an-chart__area { fill: var(--brand-soft); }
.an-chart__area--success { fill: var(--success-soft); }
.an-chart__line { fill: none; stroke: var(--brand); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.an-chart__line--success { stroke: var(--success); }
.an-chart__line--fail { stroke: var(--fail); }
.an-chart__line--warn { stroke: var(--warn); }
.an-chart__legend { display: flex; gap: var(--sp-3); margin-top: var(--sp-2); flex-wrap: wrap; }
.an-chart__legend-item { display: inline-flex; align-items: center; gap: var(--sp-1); font-size: var(--fs-xs); color: var(--text-muted); }
.an-chart__dot { display: inline-block; width: 8px; height: 8px; border-radius: var(--r-full); }
.an-chart__dot--success { background: var(--success); }
.an-chart__dot--fail { background: var(--fail); }
.an-chart__dot--warn { background: var(--warn); }

/* ── Resources ────────────────────────────────────────────────── */
.an-resources { display: flex; flex-direction: column; gap: var(--sp-4); }
.an-resource-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
}
.an-resource-card {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
}
.an-resource-card__label { font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: .03em; color: var(--text-muted); }
.an-resource-card__value { font-size: var(--fs-lg); font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }

/* ── Cost ─────────────────────────────────────────────────────── */
.an-cost { display: flex; flex-direction: column; gap: var(--sp-3); }
.an-cost-grid {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: var(--sp-4);
  align-items: center;
}
.an-cost-chart { display: flex; justify-content: center; }
.an-cost-chart__svg { width: 160px; height: 160px; display: block; }
.an-cost-chart__slice { stroke: var(--surface); stroke-width: 1; }
.an-cost-chart__slice--0 { fill: var(--brand); }
.an-cost-chart__slice--1 { fill: var(--info); }
.an-cost-chart__slice--2 { fill: var(--success); }
.an-cost-chart__slice--3 { fill: var(--warn); }
.an-cost-chart__slice--4 { fill: var(--fail); }
.an-cost-legend { display: flex; flex-direction: column; gap: var(--sp-1); }
.an-cost-legend__item { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); }
.an-cost-legend__dot { display: inline-block; width: 10px; height: 10px; border-radius: var(--r-sm); flex-shrink: 0; }
.an-cost-legend__dot--0 { background: var(--brand); }
.an-cost-legend__dot--1 { background: var(--info); }
.an-cost-legend__dot--2 { background: var(--success); }
.an-cost-legend__dot--3 { background: var(--warn); }
.an-cost-legend__dot--4 { background: var(--fail); }
.an-cost-legend__label { color: var(--text); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.an-cost-legend__value { color: var(--text-muted); font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
.an-cost-total { font-size: var(--fs-sm); color: var(--text-muted); padding: var(--sp-2) 0; }
.an-cost-total b { color: var(--text); font-weight: 600; }

/* ── Bottlenecks ──────────────────────────────────────────────── */
.an-bottlenecks { display: flex; flex-direction: column; gap: var(--sp-4); }
.an-bottleneck-section { display: flex; flex-direction: column; gap: var(--sp-2); }
.an-bottleneck-section__title { font-size: var(--fs-sm); font-weight: 600; color: var(--text); margin: 0; }

/* ── Responsive ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .an-kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .an-resource-grid { grid-template-columns: repeat(2, 1fr); }
  .an-cost-grid { grid-template-columns: 1fr; }
  .an-cost-chart { justify-content: center; }
}

@media (max-width: 640px) {
  .an-kpi-grid { grid-template-columns: 1fr; }
  .an-resource-grid { grid-template-columns: 1fr; }
  .an-cost-grid { grid-template-columns: 1fr; }
}
</style>
