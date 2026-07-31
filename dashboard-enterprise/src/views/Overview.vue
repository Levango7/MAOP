<template>
  <div class="overview">
    <PageHeader>
      <template #badges>
        <Badge v-if="edition.edition" :tone="edition.edition === 'enterprise' ? 'brand' : 'neutral'">{{ edition.edition }}</Badge>
      </template>
      <span v-if="lastUpdated" class="freshness" :class="{ stale: isStale }">
        {{ t('view.overview.updated') }} {{ freshnessText }}
      </span>
      <button class="refresh-btn" :class="{ 'pulse-once': pulsing }" @click="refresh" :disabled="loading" :title="t('common.refresh')">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </PageHeader>

    <!-- Error state -->
    <Card v-if="error" icon="alert-triangle" :title="t('view.overview.loadError')">
      <p class="muted">{{ error }}</p>
      <template #actions><button class="link-btn" @click="refresh">{{ t('common.retry') }}</button></template>
    </Card>

    <!-- KPI grid -->
    <div class="stats-grid">
      <StatCard
        v-for="s in stats" :key="s.label"
        :label="s.label" :value="s.value" :unit="s.unit" :icon="s.icon" :tone="s.tone" :accent="s.accent" :loading="loading"
        :yoy="s.yoy" :mom="s.mom" :yoy-label="s.yoyLabel" :mom-label="s.momLabel"
      />
    </div>

    <!-- Activity timeline (replaces redundant status-strip) -->
    <div class="activity-feed" v-if="!error">
      <div class="activity-item" v-for="(ev, i) in recentEvents" :key="i">
        <div class="activity-dot"></div>
        <div class="activity-body">
          <div class="activity-header">
            <span class="activity-title">{{ ev.title }}</span>
            <span class="activity-time">{{ ev.time }}</span>
          </div>
          <p class="activity-desc">{{ ev.desc }}</p>
        </div>
      </div>
    </div>

    <!-- Main rows -->
    <div class="row" v-if="!error">
      <!-- System health -->
      <Card icon="cpu" :title="t('view.overview.systemHealth')" :badge="healthScore + '%'" :badge-tone="healthTone">
        <div v-if="loading" class="health-skel">
          <Skeleton v-for="n in 4" :key="n" height="14px" />
        </div>
        <template v-else>
          <div class="metric" v-for="m in healthMetrics" :key="m.label">
            <div class="metric__head">
              <span class="metric__label">{{ m.label }}</span>
              <span class="metric__val">{{ m.display }}</span>
            </div>
            <div class="metric__bar"><div class="metric__fill" :style="{ width: m.pct + '%', background: m.color }"></div></div>
          </div>
        </template>
      </Card>

      <!-- Recent delegations -->
      <Card icon="activity" :title="t('view.overview.recentDelegations')" :badge="(data?.recent_delegations || []).length + ''" badge-tone="info">
        <DataTable
          v-if="!loading"
          :rows="data?.recent_delegations || []"
          :loading="loading"
          :sortable="true"
          :empty-text="t('view.overview.noRecentDelegations')"
          :max-height="320"
        />
        <div v-else class="tbl-skel"><Skeleton v-for="n in 5" :key="n" height="14px" /></div>
      </Card>
    </div>

    <!-- Chart + System info -->
    <div class="row" v-if="!error">
      <Card icon="activity" :title="t('view.overview.throughput')" class="chart-card">
        <div class="chart-box">
          <Line v-if="chartData.labels.length" :data="chartData" :options="chartOptions" />
          <EmptyState v-else icon="activity" :title="t('view.overview.noTimeseries')" :description="t('view.overview.throughputUnavailable')" />
        </div>
      </Card>

      <Card icon="server" :title="t('view.overview.runtime')">
        <div v-if="loading" class="info-skel"><Skeleton v-for="n in 5" :key="n" height="14px" /></div>
        <dl v-else class="info-list">
          <div class="info-row"><dt>{{ t('common.version') }}</dt><dd>{{ data.version }}</dd></div>
          <div class="info-row"><dt>{{ t('common.uptime') }}</dt><dd>{{ data.uptime }}</dd></div>
          <div class="info-row"><dt>{{ t('view.overview.dtPython') }}</dt><dd>{{ data.python_ver }}</dd></div>
          <div class="info-row"><dt>{{ t('common.platform') }}</dt><dd>{{ data.platform }}</dd></div>
          <div class="info-row"><dt>{{ t('view.overview.dtApiEndpoints') }}</dt><dd>{{ data.api_endpoints }}</dd></div>
          <div class="info-row"><dt>{{ t('view.overview.dtSourceFiles') }}</dt><dd>{{ data.source_files }}</dd></div>
          <div class="info-row"><dt>{{ t('view.overview.dtCodeLines') }}</dt><dd>{{ formatNum(data.code_lines) }}</dd></div>
          <div class="info-row"><dt>{{ t('view.overview.dtTestFiles') }}</dt><dd>{{ data.test_files }}</dd></div>
        </dl>
      </Card>
    </div>

    <!-- Failure ranking -->
    <div class="row" v-if="!error && (data?.fail_ranking || []).length">
      <Card icon="alert-triangle" :title="t('view.overview.failureRanking')" badge-tone="fail" :badge="(data.fail_ranking || []).length + ''">
        <DataTable
          :rows="data.fail_ranking"
          :columns="failColumns"
          :sortable="true"
          :empty-text="t('view.overview.noFailures')"
          :max-height="320"
        />
      </Card>
    </div>

    <!-- Degradations (real, from edition store) -->
    <Card v-if="edition.hasDegradations" icon="alert-triangle" :title="t('view.overview.activeDegradations')" badge-tone="warn">
      <div class="degrade" v-for="d in edition.degradations" :key="d.backend">
        <Badge tone="warn">{{ d.backend }}</Badge>
        <span class="muted">{{ d.requested }} → {{ d.fallback }}</span>
        <span class="faint">· {{ d.reason }}</span>
      </div>
    </Card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { Line } from 'vue-chartjs';
import {
  Chart as ChartJS, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler,
} from 'chart.js';
import { useApiStore } from '../stores/api.js';
import { useEditionStore } from '../stores/edition.js';
import { useI18n } from '../i18n';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import { Card, StatCard, Badge, DataTable, Skeleton, EmptyState } from '../components/index.js';

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Filler);

const api = useApiStore();
const edition = useEditionStore();
const { t } = useI18n();

const data = ref(null);
const loading = ref(true);
const error = ref('');
const lastUpdated = ref(null);
const pulsing = ref(false);
let refreshTimer = null;

// Activity timeline data — loaded from /api/info/activity
const recentEvents = ref([]);
const activityError = ref('');

async function loadActivity() {
  try {
    const res = await api.get('/api/info/activity?limit=8');
    if (res && res.status === 'ok' && Array.isArray(res.events)) {
      recentEvents.value = res.events;
    }
  } catch (e) {
    // Non-critical: activity feed degrades gracefully, no error banner
    console.warn('[overview] activity feed unavailable:', e.message);
  }
}

const isStale = computed(() => {
  if (!lastUpdated.value) return false;
  return Date.now() - lastUpdated.value.getTime() > 90000;
});
const freshnessText = computed(() => {
  if (!lastUpdated.value) return '—';
  const s = Math.floor((Date.now() - lastUpdated.value.getTime()) / 1000);
  if (s < 5) return 'just now';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  return Math.floor(s / 3600) + 'h ago';
});

// Dedup palette: 10 visually distinct accent colors, one per KPI, so the
// colored left borders never repeat within the grid. Values reference theme
// tokens (var(--chart-N)) so charts follow the active dark/light theme.
const ACCENTS = [
  'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)',
  'var(--chart-6)', 'var(--chart-7)', 'var(--chart-8)', 'var(--chart-9)', 'var(--chart-10)',
];

const stats = computed(() => {
  const d = data.value || {};
  const yl = t('view.overview.yoy');
  const ml = t('view.overview.mom');
  return [
    { label: t('view.overview.statActiveAgents'), value: d.agents_total ?? '—', icon: 'bot', tone: 'brand', accent: ACCENTS[0] },
    { label: t('view.overview.statDelegations'), value: d.delegations_total ?? '—', icon: 'activity', tone: 'info', accent: ACCENTS[1], yoy: d.delegations_yoy ?? null, mom: d.delegations_mom ?? null, yoyLabel: yl, momLabel: ml },
    { label: t('view.overview.statSuccessRate'), value: d.success_rate != null ? Number(d.success_rate).toFixed(1) : '—', unit: '%', icon: 'check-circle', tone: 'success', accent: ACCENTS[2], yoy: d.success_rate_yoy ?? null, mom: d.success_rate_mom ?? null, yoyLabel: yl, momLabel: ml },
    { label: t('view.overview.statAvgLatency'), value: d.avg_latency_ms != null ? Math.round(d.avg_latency_ms) : '—', unit: 'ms', icon: 'gauge', tone: 'warn', accent: ACCENTS[3] },
    { label: t('view.overview.statTests'), value: d.tests_total ?? '—', icon: 'clipboard', tone: 'neutral', accent: ACCENTS[4] },
    { label: t('view.overview.statModules'), value: d.modules_total ?? '—', icon: 'box', tone: 'brand', accent: ACCENTS[5] },
    { label: t('view.overview.statCodeLines'), value: d.code_lines != null ? formatNum(d.code_lines) : '—', icon: 'code', tone: 'neutral', accent: ACCENTS[6] },
    { label: t('view.overview.statApiEndpoints'), value: d.api_endpoints ?? '—', icon: 'server', tone: 'info', accent: ACCENTS[7] },
    { label: t('view.overview.statSourceFiles'), value: d.source_files != null ? formatNum(d.source_files) : '—', icon: 'file', tone: 'neutral', accent: ACCENTS[8] },
    { label: t('view.overview.statTestFiles'), value: d.test_files != null ? formatNum(d.test_files) : '—', icon: 'beaker', tone: 'neutral', accent: ACCENTS[9] },
  ];
});

const healthScore = computed(() => {
  const r = data.value?.success_rate;
  return r != null ? Math.round(r) : 0;
});
const healthTone = computed(() => (healthScore.value >= 95 ? 'success' : healthScore.value >= 80 ? 'warn' : 'fail'));
const healthMetrics = computed(() => {
  const d = data.value || {};
  const sr = d.success_rate != null ? Math.round(d.success_rate) : 0;
  const lat = d.avg_latency_ms != null ? Math.round(d.avg_latency_ms) : 0;
  return [
    { label: t('view.overview.statSuccessRate'), display: sr + '%', pct: sr, color: 'var(--success)' },
    { label: t('view.overview.statAvgLatency'), display: lat + ' ms', pct: Math.min(100, Math.round(lat / 10)), color: 'var(--warn)' },
    { label: t('view.overview.metricAgentsOnline'), display: String(d.agents_total ?? 0), pct: Math.min(100, (d.agents_total || 0) * 10), color: 'var(--brand)' },
    { label: t('view.overview.metricDelegations'), display: String(d.delegations_total ?? 0), pct: Math.min(100, (d.delegations_total || 0)), color: 'var(--info)' },
  ];
});

const failColumns = computed(() => [
  { key: 'agent', label: t('view.overview.colAgent'), type: 'text' },
  { key: 'error', label: t('view.overview.colError'), type: 'text' },
  { key: 'count', label: t('view.overview.colCount'), type: 'num', align: 'right' },
  { key: 'rate', label: t('view.overview.colRate'), type: 'badge', align: 'right' },
]);

// ── Time-series normalization (shape unknown → defensive) ───────────────
function normalizeTimeseries(ts) {
  if (!Array.isArray(ts) || !ts.length) return { labels: [], values: [] };
  const first = ts[0];
  if (typeof first === 'number') return { labels: ts.map((_, i) => '#' + i), values: ts };
  if (first && typeof first === 'object') {
    const tKey = ['ts', 't', 'time', 'timestamp'].find((k) => first[k] != null);
    const vKey = ['count', 'value', 'throughput', 'n', 'total', 'delegations'].find((k) => typeof first[k] === 'number');
    if (tKey && vKey) {
      return { labels: ts.map((r) => fmtTick(r[tKey])), values: ts.map((r) => r[vKey]) };
    }
    const numKey = Object.keys(first).find((k) => typeof first[k] === 'number');
    if (numKey) return { labels: ts.map((_, i) => '#' + i), values: ts.map((r) => r[numKey]) };
  }
  return { labels: [], values: [] };
}
function fmtTick(t) {
  const d = new Date(typeof t === 'number' ? t : String(t));
  if (isNaN(d.getTime())) return String(t);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
function formatNum(n) {
  if (n === null) return '—';
  return Number(n).toLocaleString();
}

const _ts = computed(() => normalizeTimeseries(data.value?.timeseries));

// Theme-aware chart colors: read CSS token values at compute time so the
// line chart follows dark/light theme switches.  Falls back to dark defaults.
function token(name, fallback) {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; }
  catch { return fallback; }
}
function chartBrand()   { return token('--chart-1', '#6366f1'); }
function chartMuted()   { return token('--text-muted', '#94a3b8'); }
function chartGridColor() { return token('--border-subtle', 'rgba(148,163,184,.15)'); }
function chartBrandFill() {
  const c = token('--chart-1', '#6366f1');
  // Convert hex to rgba with 0.14 opacity
  if (c.startsWith('#')) {
    const r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
    return `rgba(${r},${g},${b},.14)`;
  }
  return 'rgba(99,102,241,.14)';
}

const chartData = computed(() => ({
  labels: _ts.value.labels,
  datasets: [{
    data: _ts.value.values,
    borderColor: chartBrand(),
    backgroundColor: chartBrandFill(),
    borderWidth: 2,
    fill: true,
    tension: 0.35,
    pointRadius: 0,
    pointHoverRadius: 4,
  }],
}));
const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { intersect: false, mode: 'index' },
  plugins: { legend: { display: false }, tooltip: { enabled: true } },
  scales: {
    x: { grid: { display: false }, ticks: { color: chartMuted(), maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
    y: { grid: { color: chartGridColor() }, ticks: { color: chartMuted() }, beginAtZero: true },
  },
}));

async function load() {
  error.value = '';
  if (!data.value) loading.value = true; // skeleton only on first load
  try {
    const d = await api.get('/api/overview');
    data.value = d;
    lastUpdated.value = new Date();
  } catch (e) {
    if (!data.value) error.value = e.message || 'Failed to load overview';
  } finally {
    loading.value = false;
  }
  // L5: Load activity feed in parallel (non-critical, no error banner)
  loadActivity();
}
function refresh() {
  pulsing.value = true;
  setTimeout(() => (pulsing.value = false), 320);
  load();
}

onMounted(async () => {
  await edition.fetchEdition().catch(() => {});
  await load();
  refreshTimer = setInterval(load, 30000);
});
onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer); });
</script>

<style scoped>
</style>
