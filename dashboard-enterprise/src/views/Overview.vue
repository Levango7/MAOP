<template>
  <div class="overview">
    <OnboardingWizard />
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

    <!-- ── 企业版差异化叙事: Plan-Execute-Verify 三阶段工作流 ──
         个人版不渲染此区块 (edition.isEnterprise 控制)。
         纯展示: 不调用任何 API, 不修改任何数据流, 只读 edition store。 -->
    <section
      v-if="!error && edition.isEnterprise"
      class="ov-pev"
      aria-label="Plan-Execute-Verify workflow"
    >
      <header class="ov-pev__head">
        <div class="ov-pev__title-row">
          <AppIcon name="route" :size="16" class="ov-pev__title-icon" />
          <h2 class="ov-pev__title">{{ t('view.overview.pev.title') }}</h2>
          <Badge tone="brand">{{ edition.edition }}</Badge>
        </div>
        <p class="ov-pev__subtitle muted">{{ t('view.overview.pev.subtitle') }}</p>
      </header>

      <ol class="ov-pev__phases" role="list">
        <li
          v-for="(phase, idx) in pevPhases"
          :key="phase.key"
          class="ov-pev__phase"
          :class="['ov-pev__phase--' + phase.key, { 'ov-pev__phase--first': idx === 0, 'ov-pev__phase--last': idx === pevPhases.length - 1 }]"
          role="listitem"
        >
          <div class="ov-pev__icon-wrap">
            <AppIcon :name="phase.icon" :size="20" class="ov-pev__icon" />
          </div>
          <div class="ov-pev__phase-body">
            <div class="ov-pev__phase-head">
              <span class="ov-pev__phase-title">{{ t(phase.titleKey) }}</span>
              <Badge :tone="phase.stateTone">{{ t(phase.stateKey) }}</Badge>
            </div>
            <p class="ov-pev__phase-desc muted">{{ t(phase.descKey) }}</p>
          </div>
          <span
            v-if="idx < pevPhases.length - 1"
            class="ov-pev__connector"
            aria-hidden="true"
          >
            <AppIcon name="chevron-right" :size="14" />
            <span class="ov-pev__connector-label">{{ t('view.overview.pev.connector') }}</span>
          </span>
        </li>
      </ol>
    </section>

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
import OnboardingWizard from '../components/OnboardingWizard.vue';
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
import { baseLineOptions } from '../composables/chartOptions.js';

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

// ── Plan-Execute-Verify 三阶段工作流叙事 (企业版差异化) ──
// 纯静态描述: 不调用 API, 不依赖 data ref, 只读 i18n + edition store。
// 三个阶段分别用 route/play/check-circle 图标, 状态徽章用 brand/info/success。
const pevPhases = [
  { key: 'plan',    icon: 'route',        titleKey: 'view.overview.pev.plan.title',    descKey: 'view.overview.pev.plan.desc',    stateKey: 'view.overview.pev.plan.state',    stateTone: 'brand' },
  { key: 'execute', icon: 'play',         titleKey: 'view.overview.pev.execute.title', descKey: 'view.overview.pev.execute.desc', stateKey: 'view.overview.pev.execute.state', stateTone: 'info' },
  { key: 'verify',  icon: 'check-circle', titleKey: 'view.overview.pev.verify.title',  descKey: 'view.overview.pev.verify.desc',  stateKey: 'view.overview.pev.verify.state',  stateTone: 'success' },
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
const chartOptions = computed(() => baseLineOptions({
  muted: chartMuted(),
  grid: chartGridColor(),
  legendVisible: false,
}));

async function load() {
  error.value = '';
  if (!data.value) loading.value = true; // skeleton only on first load
  try {
    const d = await api.get('/api/overview');
    data.value = d;
    lastUpdated.value = new Date();
  } catch (e) {
    if (!data.value) error.value = e.message || t('view.overview.loadFailed');
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

/* ── Plan-Execute-Verify 叙事 (企业版差异化) ───────────────────────────
 * 设计语言对齐 workbench: 1px 描边卡片, 无阴影/无渐变, 中性灰底。
 * 三阶段用 grid 横排, 每阶段一张卡片; 阶段间用 chevron + "then" 连接。
 * 响应式: ≤1200px 切两列, ≤700px 切单列, 连接符在窄屏下隐藏。 */
.ov-pev {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.ov-pev__head {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.ov-pev__title-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.ov-pev__title-icon {
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
.ov-pev__title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
  margin: 0;
}
.ov-pev__subtitle {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  margin: 0;
}
.ov-pev__phases {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--sp-3);
  position: relative;
}
.ov-pev__phase {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border-subtle, var(--border));
  border-radius: var(--r-md);
  min-height: 88px;
  transition: border-color var(--motion) var(--ease);
}
.ov-pev__phase:hover { border-color: var(--border-strong); }
.ov-pev__phase--plan    { border-left: 3px solid var(--brand); }
.ov-pev__phase--execute { border-left: 3px solid var(--info); }
.ov-pev__phase--verify  { border-left: 3px solid var(--success); }
.ov-pev__icon-wrap {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: var(--r-md);
  background: var(--surface);
  border: 1px solid var(--border);
}
.ov-pev__phase--plan    .ov-pev__icon { color: var(--brand-strong); }
.ov-pev__phase--execute .ov-pev__icon { color: var(--info); }
.ov-pev__phase--verify  .ov-pev__icon { color: var(--success); }
.ov-pev__phase-body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  min-width: 0;
  flex: 1;
}
.ov-pev__phase-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.ov-pev__phase-title {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text);
}
.ov-pev__phase-desc {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  line-height: 1.5;
  margin: 0;
}
.ov-pev__connector {
  position: absolute;
  right: -28px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  color: var(--text-faint);
  pointer-events: none;
  z-index: 1;
}
.ov-pev__connector-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: var(--text-faint);
}
.ov-pev__phase--last .ov-pev__connector { display: none; }

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
  .ov-pev__phases { grid-template-columns: 1fr 1fr; }
  .ov-pev__phase--last { grid-column: 1 / -1; }
  .ov-pev__connector { display: none; }
}
@media (max-width: 900px) {
  .ov-split { grid-template-columns: 1fr; }
  .ov-hero__kpi { display: none; }
}
@media (max-width: 700px) {
  .ov-pev__phases { grid-template-columns: 1fr; }
  .ov-pev__phase--last { grid-column: auto; }
}
@media (max-width: 520px) {
  .ov-actions { grid-template-columns: 1fr; }
}
</style>
