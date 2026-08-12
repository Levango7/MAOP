<template>
  <div class="overview">
    <PageHeader>
      <template #badges>
        <Badge v-if="edition.edition" :tone="edition.edition === 'enterprise' ? 'brand' : 'neutral'">{{ edition.edition }}</Badge>
      </template>
      <span v-if="lastUpdated" class="freshness" :class="{ stale: isStale }">
        {{ t('view.overview.updated') }} {{ freshnessText }}
      </span>
      <button class="refresh-btn" :class="{ 'pulse-once': pulsing }" :disabled="loading" :title="t('common.refresh')" @click="refresh">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </PageHeader>

    <!-- Error state -->
    <Card v-if="error" icon="alert-triangle" :title="t('view.overview.loadError')">
      <p class="muted">{{ error }}</p>
      <template #actions><button class="link-btn" @click="refresh">{{ t('common.retry') }}</button></template>
    </Card>

    <!-- ── 层 1: Hero strip — 健康结论 + 关键运行态,一屏之内给答案 ── -->
    <div v-if="!error" class="ov-hero" :class="heroTone">
      <span class="ov-hero__dot" aria-hidden="true"></span>
      <span class="ov-hero__status">{{ heroLabel }}</span>
      <span class="ov-hero__sep" aria-hidden="true"></span>
      <span class="ov-hero__kpi">
        {{ data?.agents_total ?? '—' }} {{ t('view.overview.statActiveAgents').toLowerCase() }}
        · {{ data?.delegations_total ?? '—' }} {{ t('view.overview.heroTasksRunning') }}
      </span>
      <span class="ov-hero__fresh muted">{{ t('view.overview.updated') }} {{ freshnessText }}</span>
    </div>

    <!-- ── 层 2: Action 磁贴 — 引导用户"下一步做什么" ── -->
    <nav class="ov-actions" aria-label="Quick actions">
      <router-link v-for="a in quickActions" :key="a.to" :to="a.to" class="ov-action">
        <AppIcon :name="a.icon" :size="16" class="ov-action__icon" />
        <span class="ov-action__label">{{ t(a.label) }}</span>
      </router-link>
    </nav>

    <!-- KPI grid -->
    <div class="stats-grid">
      <StatCard
        v-for="s in stats" :key="s.label"
        :label="s.label" :value="s.value" :unit="s.unit" :icon="s.icon" :tone="s.tone" :accent="s.accent" :loading="loading"
        :yoy="s.yoy" :mom="s.mom" :yoy-label="s.yoyLabel" :mom-label="s.momLabel"
      />
    </div>

    <!-- Main rows -->
    <div v-if="!error" class="row">
      <!-- System health -->
      <Card icon="cpu" :title="t('view.overview.systemHealth')" :badge="healthScore + '%'" :badge-tone="healthTone">
        <div v-if="loading" class="health-skel">
          <Skeleton v-for="n in 4" :key="n" height="14px" />
        </div>
        <template v-else>
          <div v-for="m in healthMetrics" :key="m.label" class="metric">
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

    <!-- ── 层 3: 图表 (2/3) + 活动流 (1/3) 并排 — 上下文不再独占整行 ── -->
    <div v-if="!error" class="ov-split">
      <Card icon="activity" :title="t('view.overview.throughput')" class="chart-card">
        <div class="chart-box">
          <Line v-if="chartData.labels.length" :data="chartData" :options="chartOptions" />
          <EmptyState v-else icon="activity" :title="t('view.overview.noTimeseries')" :description="t('view.overview.throughputUnavailable')" />
        </div>
      </Card>

      <div class="activity-feed">
        <div v-for="(ev, i) in recentEvents" :key="i" class="activity-item">
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
    </div>

    <!-- System info -->
    <div v-if="!error" class="row">
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
    <div v-if="!error && (data?.fail_ranking || []).length" class="row">
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
      <div v-for="d in edition.degradations" :key="d.backend" class="degrade">
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
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';

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

// ── Hero strip: 健康结论 + 关键运行态 ──
// 结论来自 degradation 日志 + success_rate,与 edition.degradations 联动
const heroTone = computed(() => {
  if (error.value) return 'ov-hero--down';
  if (edition.hasDegradations && edition.degradations.length > 0) return 'ov-hero--degraded';
  const sr = data.value?.success_rate;
  if (sr !== null && sr !== undefined && sr < 80) return 'ov-hero--degraded';
  return 'ov-hero--healthy';
});
const heroLabel = computed(() => {
  switch (heroTone.value) {
    case 'ov-hero--down': return t('view.overview.heroDown');
    case 'ov-hero--degraded': return t('view.overview.heroDegraded');
    default: return t('view.overview.heroHealthy');
  }
});

// ── Quick actions: 4 个最高频入口,与 topbar 动作一一对应 ──
const quickActions = [
  { to: '/run?tab=structured', label: 'view.overview.actionRun', icon: 'play' },
  { to: '/run?tab=chat', label: 'view.overview.actionChat', icon: 'chat' },
  { to: '/agents', label: 'view.overview.actionAgents', icon: 'bot' },
  { to: '/logs', label: 'view.overview.actionLogs', icon: 'scroll' },
];

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
    { label: t('view.overview.statSuccessRate'), value: d.success_rate !== null && d.success_rate !== undefined ? Number(d.success_rate).toFixed(1) : '—', unit: '%', icon: 'check-circle', tone: 'success', accent: ACCENTS[2], yoy: d.success_rate_yoy ?? null, mom: d.success_rate_mom ?? null, yoyLabel: yl, momLabel: ml },
    { label: t('view.overview.statAvgLatency'), value: d.avg_latency_ms !== null && d.avg_latency_ms !== undefined ? Math.round(d.avg_latency_ms) : '—', unit: 'ms', icon: 'gauge', tone: 'warn', accent: ACCENTS[3] },
    { label: t('view.overview.statTests'), value: d.tests_total ?? '—', icon: 'clipboard', tone: 'neutral', accent: ACCENTS[4] },
    { label: t('view.overview.statModules'), value: d.modules_total ?? '—', icon: 'box', tone: 'brand', accent: ACCENTS[5] },
    { label: t('view.overview.statCodeLines'), value: d.code_lines !== null && d.code_lines !== undefined ? formatNum(d.code_lines) : '—', icon: 'code', tone: 'neutral', accent: ACCENTS[6] },
    { label: t('view.overview.statApiEndpoints'), value: d.api_endpoints ?? '—', icon: 'server', tone: 'info', accent: ACCENTS[7] },
    { label: t('view.overview.statSourceFiles'), value: d.source_files !== null && d.source_files !== undefined ? formatNum(d.source_files) : '—', icon: 'file', tone: 'neutral', accent: ACCENTS[8] },
    { label: t('view.overview.statTestFiles'), value: d.test_files !== null && d.test_files !== undefined ? formatNum(d.test_files) : '—', icon: 'beaker', tone: 'neutral', accent: ACCENTS[9] },
  ];
});

const healthScore = computed(() => {
  const r = data.value?.success_rate;
  return r !== null && r !== undefined ? Math.round(r) : 0;
});
const healthTone = computed(() => (healthScore.value >= 95 ? 'success' : healthScore.value >= 80 ? 'warn' : 'fail'));
const healthMetrics = computed(() => {
  const d = data.value || {};
  const sr = d.success_rate !== null && d.success_rate !== undefined ? Math.round(d.success_rate) : 0;
  const lat = d.avg_latency_ms !== null && d.avg_latency_ms !== undefined ? Math.round(d.avg_latency_ms) : 0;
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
    const tKey = ['ts', 't', 'time', 'timestamp'].find((k) => first[k] !== null && first[k] !== undefined);
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

// Theme-aware chart colors: shared composable reads CSS vars at compute time,
// so charts follow dark/light theme switches without a remount.
function chartBrand()     { return cssVar('--chart-1', '#3574f0'); }
function chartMuted()     { return cssVar('--text-muted', '#9aa3b2'); }
function chartGridColor() { return cssVar('--border-subtle', 'rgba(163,173,190,.15)'); }
function chartBrandFill() { return cssVarAlpha('--chart-1', .14); }

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
/* ── Hero strip — 全宽状态条: 描边卡片风, 与全站 workbench 语言一致 ── */
.ov-hero {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
}
.ov-hero__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.ov-hero--healthy .ov-hero__dot { background: var(--success); }
.ov-hero--degraded .ov-hero__dot { background: var(--warn); }
.ov-hero--down .ov-hero__dot { background: var(--fail); }
.ov-hero__status { font-size: var(--fs-md); font-weight: 600; color: var(--text); }
.ov-hero__sep { width: 1px; height: 14px; background: var(--border); flex-shrink: 0; }
.ov-hero__kpi { font-size: var(--fs-sm); color: var(--text-muted); font-variant-numeric: tabular-nums; }
.ov-hero__fresh { margin-left: auto; font-size: var(--fs-xs); }

/* ── Quick actions — 4 磁贴, 视觉上是明确的"按钮卡" ── */
.ov-actions {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-3);
}
.ov-action {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  color: var(--text);
  font-size: var(--fs-sm);
  font-weight: 500;
  text-decoration: none;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease), color var(--motion) var(--ease);
  min-height: 44px;
}
.ov-action:hover {
  border-color: var(--brand);
  color: var(--brand-strong);
}
.ov-action:hover .ov-action__icon { color: var(--brand-strong); }
.ov-action:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.ov-action__icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: var(--r-md);
  background: var(--brand-soft);
  color: var(--brand-strong);
  transition: inherit;
}
.ov-action__label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Split tier — 图表 2/3 + 活动流 1/3 ── */
.ov-split {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--sp-4);
  align-items: stretch;
}
.ov-split > * { min-width: 0; margin-bottom: 0; }
.ov-split .activity-feed { margin-bottom: 0; }

@media (max-width: 1200px) {
  .ov-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .ov-split { grid-template-columns: 1fr; }
  .ov-hero__kpi { display: none; }
}
@media (max-width: 520px) {
  .ov-actions { grid-template-columns: 1fr; }
}
</style>
