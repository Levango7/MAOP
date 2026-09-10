<template>
  <div class="observability-page view-enter">
    <PageHeader>
      <div class="header-actions">
        <span class="edition-badge" :class="edition">
          <AppIcon :name="edition === 'enterprise' ? 'shield' : 'user'" :size="14" />
          <!-- 修复: 硬编码英文替换为 i18n 翻译键 -->
          {{ edition === 'enterprise' ? t('view.observability.enterprise') : t('view.observability.personal') }}
        </span>
        <span class="tracing-badge" :class="tracingEnabled ? 'on' : 'off'">
          <span class="dot"></span>
          <!-- 修复: 硬编码英文替换为 i18n 翻译键 -->
          {{ tracingEnabled ? t('view.observability.tracingOn') : t('view.observability.tracingOff') }}
        </span>
      </div>
    </PageHeader>

    <!-- ── Status overview ────────────────────────────────────── -->
    <div class="metrics-grid">
      <StatCard
        v-for="m in summaryCards"
        :key="m.label"
        :label="m.label"
        :value="m.value"
        :unit="m.unit"
        :icon="m.icon"
        :tone="m.tone"
        :loading="loading"
      />
    </div>

    <div class="two-col">
      <!-- ── Pipeline status ─────────────────────────────────── -->
      <Card :title="t('view.observability.pipelineTitle')" icon="activity" :margin-bottom="0">
        <div class="pipeline-list">
          <div v-for="p in pipelineRows" :key="p.name" class="pipeline-row">
            <span class="pipeline-dot" :class="p.ok ? 'ok' : 'bad'"></span>
            <span class="pipeline-name">{{ p.name }}</span>
            <span class="pipeline-detail">{{ p.detail }}</span>
            <!-- 修复: 硬编码 OK/OFF 替换为 i18n 翻译键 -->
            <span class="pipeline-status" :class="p.ok ? 'ok' : 'bad'">{{ p.ok ? t('view.observability.statusOk') : t('view.observability.statusOff') }}</span>
          </div>
        </div>
      </Card>

      <!-- ── Configuration ──────────────────────────────────── -->
      <Card :title="t('view.observability.configTitle')" icon="gear" :margin-bottom="0">
        <div v-if="configLoading" class="config-skel">
          <Skeleton v-for="n in 5" :key="n" height="18px" />
        </div>
        <div v-else class="config-list">
          <div v-for="c in configRows" :key="c.label" class="config-row">
            <span class="config-label">{{ c.label }}</span>
            <span class="config-value" :class="c.mono ? 'mono' : ''">{{ c.value }}</span>
          </div>
        </div>
      </Card>
    </div>

    <!-- ── Canonical metrics ──────────────────────────────────── -->
    <Card :title="t('view.observability.canonicalMetricsTitle')" icon="gauge" class="mt">
      <div class="metric-table">
        <div class="metric-header">
          <span class="col-name">{{ t('view.observability.metric') }}</span>
          <span class="col-type">{{ t('view.observability.type') }}</span>
          <span class="col-value">{{ t('view.observability.value') }}</span>
          <span class="col-extra">{{ t('view.observability.detail') }}</span>
        </div>
        <div v-for="m in canonicalMetrics" :key="m.name" class="metric-row">
          <span class="col-name mono">{{ m.name }}</span>
          <span class="col-type"><span class="type-tag" :class="m.type">{{ m.type }}</span></span>
          <span class="col-value">{{ m.value }}</span>
          <span class="col-extra">{{ m.detail }}</span>
        </div>
      </div>
    </Card>

    <!-- ── Health checks ──────────────────────────────────────── -->
    <Card :title="t('view.observability.healthChecksTitle')" icon="shield" class="mt">
      <template #actions>
        <button class="refresh-btn" :aria-label="t('common.refresh')" :disabled="healthLoading" @click="loadHealth">
          <AppIcon name="refresh" :size="14" />
        </button>
      </template>
      <div v-if="healthLoading" class="health-skel">
        <Skeleton v-for="n in 4" :key="n" height="20px" />
      </div>
      <div v-else-if="healthChecks.length" class="health-list">
        <div v-for="h in healthChecks" :key="h.name" class="health-row">
          <span class="health-dot" :class="h.ok ? 'ok' : 'bad'"></span>
          <span class="health-name">{{ h.name }}</span>
          <span class="health-detail">{{ h.detail }}</span>
        </div>
      </div>
      <EmptyState v-else icon="shield" :title="t('view.observability.noHealthData')" :hint="t('view.observability.noHealthHint')" />
    </Card>

    <!-- ── Trace info ─────────────────────────────────────────── -->
    <Card :title="t('view.observability.distributedTracingTitle')" icon="share2" class="mt">
      <div v-if="traceInfo.enabled" class="trace-info">
        <div class="trace-row">
          <AppIcon name="check-circle" :size="16" />
          <!-- 修复: 硬编码英文替换为 i18n 翻译键 -->
          <span>{{ t('view.observability.tracingActive') }}</span>
        </div>
        <div class="trace-hint">{{ traceInfo.hint }}</div>
      </div>
      <div v-else class="trace-info disabled">
        <div class="trace-row">
          <AppIcon name="alert-triangle" :size="16" />
          <!-- 修复: 硬编码英文替换为 i18n 翻译键 (带插值) -->
          <span>{{ t('view.observability.tracingDisabled', { hint: traceInfo.hint }) }}</span>
        </div>
        <div class="trace-enable">
          <code>MAOP_OTEL_ENABLED=1</code> + <code>pip install opentelemetry-sdk</code>
        </div>
      </div>
    </Card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { StatCard, Card, Skeleton, EmptyState, AppIcon, PageHeader } from '../components/index.js';
import { useI18n } from '../i18n';

const { t } = useI18n();

const api = useApiStore();

const loading = ref(true);
const configLoading = ref(true);
const healthLoading = ref(false);
const status = ref(null);
const config = ref(null);
const health = ref(null);
const traceInfo = ref({ enabled: false, hint: '' });

const edition = computed(() => status.value?.edition || 'personal');
const tracingEnabled = computed(() => !!status.value?.tracing_enabled);
const enterpriseMode = computed(() => !!status.value?.enterprise_mode);

// ── Summary cards (top row) ───────────────────────────────────────
const summaryCards = computed(() => [
  {
    // 修复: 硬编码英文替换为 i18n 翻译键
    label: t('view.observability.requestsTotal'),
    value: formatNum(status.value?.metrics?.metrics?.maop_requests_total || 0),
    unit: '',
    icon: 'activity',
    tone: 'brand',
  },
  {
    label: t('view.observability.errorsTotal'),
    value: formatNum(status.value?.metrics?.metrics?.maop_errors_total || 0),
    unit: '',
    icon: 'alert-triangle',
    tone: status.value?.metrics?.metrics?.maop_errors_total > 0 ? 'fail' : 'success',
  },
  {
    label: t('view.observability.activeSpans'),
    value: formatNum(status.value?.metrics?.metrics?.maop_active_spans || 0),
    unit: '',
    icon: 'share2',
    tone: 'brand',
  },
  {
    label: t('view.observability.traceExports'),
    value: formatNum(status.value?.metrics?.metrics?.maop_trace_export_total || 0),
    unit: '',
    icon: 'refresh',
    tone: 'brand',
  },
]);

// ── Pipeline rows ─────────────────────────────────────────────────
const pipelineRows = computed(() => [
  {
    // 修复: 硬编码英文替换为 i18n 翻译键
    name: t('view.observability.structuredLogging'),
    ok: true,
    detail: status.value?.logging?.level || 'INFO',
  },
  {
    name: t('view.observability.traceCorrelation'),
    ok: !!status.value?.logging?.trace_correlation,
    detail: status.value?.logging?.trace_correlation ? t('view.observability.otelLinked') : t('view.observability.noOtel'),
  },
  {
    name: t('view.observability.otelTracing'),
    ok: tracingEnabled.value,
    detail: status.value?.tracing?.tracer_type || 'NoopTracer',
  },
  {
    name: t('view.observability.prometheusMetrics'),
    ok: true,
    detail: '/api/prometheus',
  },
  {
    name: t('view.observability.enterpriseMode'),
    ok: enterpriseMode.value,
    detail: edition.value,
  },
]);

// ── Config rows ───────────────────────────────────────────────────
const configRows = computed(() => {
  if (!config.value) return [];
  // 修复: 硬编码英文 label 替换为 i18n 翻译键; yes/no 也走 i18n
  return [
    { label: t('view.observability.configEdition'), value: config.value.edition },
    { label: t('view.observability.configOtelEnabled'), value: config.value.otel_enabled ? t('view.observability.yes') : t('view.observability.no') },
    { label: t('view.observability.configOtelExporter'), value: config.value.otel_exporter },
    { label: t('view.observability.configOtelEndpoint'), value: config.value.otel_endpoint, mono: true },
    { label: t('view.observability.configServiceName'), value: config.value.otel_service_name, mono: true },
    { label: t('view.observability.configScrapePath'), value: config.value.prometheus_scrape_path, mono: true },
    { label: t('view.observability.configGrafanaUid'), value: config.value.grafana_dashboard_uid, mono: true },
  ];
});

// ── Canonical metrics table ───────────────────────────────────────
const canonicalMetrics = computed(() => {
  const m = status.value?.metrics?.metrics || {};
  const h = status.value?.metrics?.histograms || {};
  return [
    {
      name: 'maop_requests_total',
      type: 'counter',
      value: formatNum(m.maop_requests_total || 0),
      detail: t('view.observability.metricLabelsHttp'),
    },
    {
      name: 'maop_request_duration_seconds',
      type: 'histogram',
      // 'obs' / 's' 为技术单位后缀，不参与 i18n（遵循 OpenMetrics/Prometheus 单位约定）
      value: `${h.maop_request_duration_seconds?.count || 0} obs`,
      detail: `sum=${h.maop_request_duration_seconds?.sum || 0}s`,
    },
    {
      name: 'maop_agent_execution_seconds',
      type: 'histogram',
      // 'obs' / 's' 为技术单位后缀，不参与 i18n（遵循 OpenMetrics/Prometheus 单位约定）
      value: `${h.maop_agent_execution_seconds?.count || 0} obs`,
      detail: `sum=${h.maop_agent_execution_seconds?.sum || 0}s`,
    },
    {
      name: 'maop_errors_total',
      type: 'counter',
      value: formatNum(m.maop_errors_total || 0),
      detail: t('view.observability.metricLabelsModule'),
    },
  ];
});

// ── Health checks ─────────────────────────────────────────────────
const healthChecks = computed(() => {
  if (!health.value?.checks) return [];
  const out = [];
  for (const [name, info] of Object.entries(health.value.checks)) {
    out.push({
      name,
      ok: !!info.ok,
      // 修复: 硬编码 available/missing 替换为 i18n 翻译键
      detail: info.error || info.type || info.version || (info.ok ? t('view.observability.available') : t('view.observability.missing')),
    });
  }
  return out;
});

// ── Helpers ───────────────────────────────────────────────────────
function formatNum(n) {
  if (typeof n !== 'number') return '0';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n * 100) / 100);
}

// ── Data loading ──────────────────────────────────────────────────
async function loadStatus() {
  loading.value = true;
  try {
    status.value = await api.get('/api/observability/status');
  } catch (e) {
    console.warn('[observability] status failed:', e?.message);
  }
  loading.value = false;
}

async function loadConfig() {
  configLoading.value = true;
  try {
    config.value = await api.get('/api/observability/config');
  } catch (e) {
    console.warn('[observability] config failed:', e?.message);
  }
  configLoading.value = false;
}

async function loadHealth() {
  healthLoading.value = true;
  try {
    health.value = await api.get('/api/observability/health');
  } catch (e) {
    console.warn('[observability] health failed:', e?.message);
  }
  healthLoading.value = false;
}

async function loadTraces() {
  try {
    traceInfo.value = await api.get('/api/observability/traces?limit=5');
  } catch {
    // 修复: 硬编码英文替换为 i18n 翻译键
    traceInfo.value = { enabled: false, hint: t('view.observability.endpointUnavailable') };
  }
}

let pollTimer = null;
onMounted(() => {
  loadStatus();
  loadConfig();
  loadHealth();
  loadTraces();
  pollTimer = setInterval(loadStatus, 15000);
});

onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<style scoped>
/* ── Header badges ─────────────────────────────────────────────── */
.header-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}
.edition-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: var(--fs-sm);
  font-weight: 600;
  background: var(--bg-tag, rgba(148,163,184,.16));
  color: var(--text-muted);
}
.edition-badge.enterprise {
  background: var(--brand-soft);
  color: var(--brand);
}
.tracing-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: var(--fs-sm);
  font-weight: 600;
}
.tracing-badge .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.tracing-badge.on {
  background: var(--success-soft);
  color: var(--success);
}
.tracing-badge.on .dot { background: var(--success); }
.tracing-badge.off {
  background: var(--bg-tag, rgba(148,163,184,.16));
  color: var(--text-faint);
}
.tracing-badge.off .dot { background: var(--text-faint); }

/* ── Layout ─────────────────────────────────────────────────────── */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.mt { margin-top: 16px; }

@media (max-width: 900px) {
  .metrics-grid { grid-template-columns: repeat(2, 1fr); }
  .two-col { grid-template-columns: 1fr; }
}

/* 修复: metrics-grid 缺少小屏幕 1 列断点 → 640px 以下降为单列 */
@media (max-width: 640px) {
  .metrics-grid { grid-template-columns: 1fr; }
}

/* ── Pipeline list ──────────────────────────────────────────────── */
.pipeline-list, .config-list, .health-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pipeline-row, .config-row, .health-row {
  display: grid;
  grid-template-columns: 14px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border-soft, rgba(148,163,184,.16));
}
.pipeline-row:last-child, .config-row:last-child, .health-row:last-child {
  border-bottom: none;
}
.pipeline-dot, .health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  justify-self: center;
}
.pipeline-dot.ok, .health-dot.ok { background: var(--success); }
.pipeline-dot.bad, .health-dot.bad { background: var(--fail); }
.pipeline-name, .health-name {
  font-size: var(--fs-base);
  color: var(--text);
  font-weight: 500;
}
.pipeline-detail, .health-detail {
  grid-column: 3;
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-family: var(--font-mono, monospace);
}
.pipeline-status {
  grid-column: 3;
  font-size: var(--fs-xs);
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
}
.pipeline-status.ok {
  background: var(--success-soft);
  color: var(--success);
}
.pipeline-status.bad {
  background: var(--fail-soft);
  color: var(--fail);
}

/* ── Config rows ────────────────────────────────────────────────── */
.config-row {
  grid-template-columns: 1fr auto;
}
.config-label {
  font-size: var(--fs-base);
  color: var(--text-muted);
}
.config-value {
  font-size: var(--fs-base);
  color: var(--text);
  font-weight: 500;
}
.config-value.mono {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-sm);
}

/* ── Metric table ───────────────────────────────────────────────── */
.metric-table {
  display: flex;
  flex-direction: column;
}
.metric-header, .metric-row {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 2fr;
  gap: 12px;
  padding: 8px 4px;
  align-items: center;
}
.metric-header {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border, rgba(148,163,184,.35));
}
.metric-row {
  border-bottom: 1px solid var(--border-soft, rgba(148,163,184,.16));
}
.metric-row:last-child { border-bottom: none; }
.mono { font-family: var(--font-mono, monospace); font-size: var(--fs-sm); }
.col-name { color: var(--text); }
.col-type { text-align: center; }
.col-value {
  text-align: center;
  font-weight: 600;
  color: var(--text);
}
.col-extra {
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-family: var(--font-mono, monospace);
}
.type-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 8px;
  font-size: var(--fs-xs);
  font-weight: 600;
}
.type-tag.counter {
  background: var(--brand-soft);
  color: var(--brand);
}
.type-tag.histogram {
  background: var(--chart-5-soft);
  color: var(--chart-5);
}
.type-tag.gauge {
  background: var(--warn-soft);
  color: var(--warn);
}

/* ── Health & trace ─────────────────────────────────────────────── */
.refresh-btn {
  background: none;
  border: 1px solid var(--border, rgba(148,163,184,.35));
  border-radius: 4px;
  padding: 4px 8px;
  cursor: pointer;
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
}
.refresh-btn:hover { color: var(--brand); border-color: var(--brand); }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.health-skel, .config-skel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.trace-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.trace-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-base);
  color: var(--text);
}
.trace-info.disabled .trace-row { color: var(--text-muted); }
.trace-hint {
  font-size: var(--fs-sm);
  color: var(--text-faint);
  padding-left: 24px;
}
.trace-enable {
  font-size: var(--fs-sm);
  padding-left: 24px;
  color: var(--text-muted);
}
.trace-enable code {
  font-family: var(--font-mono, monospace);
  background: var(--bg-tag, rgba(148,163,184,.16));
  padding: 1px 6px;
  border-radius: 3px;
  font-size: var(--fs-xs);
}
</style>