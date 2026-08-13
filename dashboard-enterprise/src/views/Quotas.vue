<template>
  <div class="quotas-page">
    <ListPageLayout
      :loading="loading"
      :error="error"
      :empty="!tenants.length"
      :error-title="t('view.quotas.loadError')"
      :empty-title="t('view.quotas.noTenants')"
      :empty-desc="t('view.quotas.noTenantsDesc')"
      :loading-lines="4"
    >
      <template #badges>
        <Badge tone="brand">{{ t('view.quotas.enterprise') }}</Badge>
      </template>
      <template #actions>
        <button class="btn" :disabled="loading" @click="loadAll">
          <AppIcon name="refresh" :size="15" /> {{ t('view.quotas.refresh') }}
        </button>
      </template>

      <template #stats>
        <StatCard
          :label="t('view.quotas.statTotalTenants')"
          :value="tenants.length"
          icon="building"
          tone="brand"
        />
        <StatCard
          :label="t('view.quotas.statOverQuota')"
          :value="overQuotaCount"
          icon="alert-triangle"
          tone="fail"
        />
        <StatCard
          :label="t('view.quotas.statAvgUsage')"
          :value="avgUsageRate + '%'"
          icon="gauge"
          tone="info"
        />
        <StatCard
          :label="t('view.quotas.statTotalAlerts')"
          :value="totalActiveAlerts"
          icon="activity"
          tone="warn"
        />
      </template>

      <template #content>
        <!-- 概览仪表板:热力图 + 资源分配饼图 -->
        <section class="overview-dashboard">
          <Card :title="t('view.quotas.heatmap')" :subtitle="t('view.quotas.heatmapDesc')" icon="gauge">
            <div class="heatmap">
              <div class="heatmap__header">
                <div class="heatmap__corner"></div>
                <div
                  v-for="r in RESOURCES"
                  :key="r.key"
                  class="heatmap__col-header"
                  :title="t('view.quotas.' + r.key)"
                >{{ t('view.quotas.' + r.key) }}</div>
              </div>
              <div
                v-for="tenant in tenants"
                :key="tenant.tenant_id"
                class="heatmap__row"
                :class="{ 'is-over': isTenantOverQuota(tenant) }"
              >
                <div class="heatmap__row-header" :title="tenant.name || tenant.tenant_id">
                  {{ tenant.name || tenant.tenant_id }}
                </div>
                <div
                  v-for="r in RESOURCES"
                  :key="r.key"
                  class="heatmap__cell"
                  :class="heatCellClass(tenant, r)"
                  :title="heatCellTitle(tenant, r)"
                >{{ heatCellRate(tenant, r) }}%</div>
              </div>
            </div>
            <div class="heatmap__legend">
              <span class="legend-item"><i class="dot dot--low"></i>{{ t('view.quotas.legendLow') }}</span>
              <span class="legend-item"><i class="dot dot--mid"></i>{{ t('view.quotas.legendHigh') }}</span>
              <span class="legend-item"><i class="dot dot--over"></i>{{ t('view.quotas.legendOver') }}</span>
            </div>
          </Card>

          <Card :title="t('view.quotas.allocation')" :subtitle="t('view.quotas.allocationDesc')" icon="box">
            <div class="allocation-chart">
              <Doughnut v-if="allocationChartData.labels.length" :data="allocationChartData" :options="allocationChartOptions" />
              <EmptyState v-else icon="box" :title="t('view.quotas.noTenants')" />
            </div>
          </Card>
        </section>

        <!-- 租户配额卡片网格 -->
        <div class="quota-grid">
          <div
            v-for="tenant in tenants"
            :key="tenant.tenant_id"
            class="quota-card"
            :class="{ 'is-over': isTenantOverQuota(tenant), 'is-suspended': tenant.status === 'suspended' }"
          >
            <div class="quota-card__head">
              <div class="quota-card__title">
                <h3>{{ tenant.name || tenant.tenant_id }}</h3>
                <span class="muted mono">{{ tenant.tenant_id }}</span>
              </div>
              <Badge :tone="statusTone(tenant.status)">{{ tenant.status || t('view.quotas.unknown') }}</Badge>
            </div>
            <div class="quota-card__meta">
              <span class="muted">{{ t('view.quotas.plan') }}</span>
              <b>{{ tenant.plan || '—' }}</b>
              <Badge v-if="isTenantOverQuota(tenant)" tone="fail" icon="alert-triangle">
                {{ t('view.quotas.overQuota') }}
              </Badge>
            </div>
            <div class="quota-card__body">
              <div v-for="r in RESOURCES" :key="r.key" class="quota-row">
                <div class="quota-row__head">
                  <AppIcon :name="r.icon" :size="13" />
                  <span class="quota-row__name">{{ t('view.quotas.' + r.key) }}</span>
                  <span class="quota-row__val">{{ formatUsage(tenant, r) }}</span>
                </div>
                <div class="quota-bar">
                  <div
                    class="quota-bar__fill"
                    :class="barToneClass(usageRateRaw(tenant, r))"
                    :style="{ width: barWidth(tenant, r) }"
                  ></div>
                </div>
              </div>
            </div>
            <div class="quota-card__actions">
              <button class="btn btn--sm" @click="openDetail(tenant)">
                <AppIcon name="info" :size="13" /> {{ t('view.quotas.viewDetail') }}
              </button>
              <button class="btn btn--sm btn--primary" @click="openAdjust(tenant)">
                <AppIcon name="wrench" :size="13" /> {{ t('view.quotas.adjust') }}
              </button>
            </div>
          </div>
        </div>
      </template>
    </ListPageLayout>

    <!-- 详情抽屉 -->
    <DetailDrawer
      :open="drawerOpen"
      :title="detailTenant ? (detailTenant.name || detailTenant.tenant_id) : t('view.quotas.detail')"
      icon="building"
      @close="closeDetail"
    >
      <!-- 实时使用量 -->
      <section class="drawer-section">
        <h4 class="drawer-section__title">{{ t('view.quotas.realtimeUsage') }}</h4>
        <div v-if="detailTenant" class="usage-grid">
          <div v-for="r in RESOURCES" :key="r.key" class="usage-item">
            <span class="usage-item__name">{{ t('view.quotas.' + r.key) }}</span>
            <div class="usage-item__bar">
              <div
                class="quota-bar__fill"
                :class="barToneClass(usageRateRaw(detailTenant, r))"
                :style="{ width: barWidth(detailTenant, r) }"
              ></div>
            </div>
            <span class="usage-item__val">{{ formatUsage(detailTenant, r) }}</span>
          </div>
        </div>
      </section>

      <!-- 使用量趋势折线图 -->
      <section class="drawer-section">
        <h4 class="drawer-section__title">{{ t('view.quotas.usageTrend') }}</h4>
        <div class="trend-chart">
          <Line v-if="trendChartData.labels.length" :data="trendChartData" :options="trendChartOptions" />
          <EmptyState v-else icon="activity" :title="t('view.quotas.noTrend')" />
        </div>
      </section>

      <!-- 配额变更历史 -->
      <section class="drawer-section">
        <h4 class="drawer-section__title">{{ t('view.quotas.quotaHistory') }}</h4>
        <ul v-if="quotaHistory.length" class="history-list">
          <li v-for="h in quotaHistory" :key="h.id" class="history-item">
            <div class="history-item__head">
              <Badge tone="info">{{ h.field }}</Badge>
              <span class="muted">{{ formatTime(h.changed_at) }}</span>
            </div>
            <div class="history-item__body">
              <span class="mono">{{ h.old_value }}</span>
              <AppIcon name="chevron-right" :size="12" />
              <span class="mono"><b>{{ h.new_value }}</b></span>
              <span class="muted">{{ t('view.quotas.changedBy') }} {{ h.changed_by || '—' }}</span>
            </div>
          </li>
        </ul>
        <EmptyState v-else icon="scroll" :title="t('view.quotas.noHistory')" />
      </section>

      <!-- 告警列表 -->
      <section class="drawer-section">
        <h4 class="drawer-section__title">{{ t('view.quotas.alerts') }}</h4>
        <ul v-if="detailAlerts.length" class="alert-list">
          <li
            v-for="a in detailAlerts"
            :key="a.id"
            class="alert-item"
            :class="{ 'is-resolved': a.status === 'resolved' }"
          >
            <div class="alert-item__head">
              <Badge :tone="alertTone(a.level)">{{ a.level }}</Badge>
              <span class="muted">{{ formatTime(a.time || a.timestamp) }}</span>
            </div>
            <div class="alert-item__body">{{ a.message }}</div>
            <div class="alert-item__foot">
              <Badge v-if="a.status === 'resolved'" tone="success">{{ t('view.quotas.resolved') }}</Badge>
              <Badge v-else tone="warn">{{ t('view.quotas.active') }}</Badge>
              <button
                v-if="a.status !== 'resolved'"
                class="btn btn--sm"
                @click="resolveAlert(a)"
              >{{ t('view.quotas.resolve') }}</button>
            </div>
          </li>
        </ul>
        <EmptyState v-else icon="check-circle" :title="t('view.quotas.noAlerts')" />
      </section>
    </DetailDrawer>

    <!-- 调整配额 Modal -->
    <div
      v-if="showAdjust"
      v-modal-a11y
      class="modal-overlay"
      @click.self="showAdjust = false"
      @modal:escape="showAdjust = false"
    >
      <div class="modal">
        <h3>{{ t('view.quotas.adjustTitle') }}</h3>
        <p class="muted">{{ t('view.quotas.adjustFor', { name: adjustTarget ? (adjustTarget.name || adjustTarget.tenant_id) : '' }) }}</p>
        <div class="adjust-form">
          <div v-for="r in RESOURCES" :key="r.key" class="adjust-row">
            <label class="adjust-row__label">
              <AppIcon :name="r.icon" :size="14" />
              {{ t('view.quotas.' + r.key) }}
            </label>
            <input
              v-model.number="adjustForm[r.quotaKey]"
              type="number"
              min="0"
              class="input adjust-row__input"
            />
            <span class="muted">{{ t('view.quotas.' + r.unit) }}</span>
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="showAdjust = false">{{ t('view.quotas.cancel') }}</button>
          <button class="btn btn--primary" :disabled="saving" @click="saveAdjust">
            {{ saving ? t('view.quotas.saving') : t('view.quotas.save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { Line, Doughnut } from 'vue-chartjs';
import {
  Chart as ChartJS, LineElement, PointElement, LinearScale, CategoryScale,
  Tooltip, Filler, ArcElement, Legend,
} from 'chart.js';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import Badge from '../components/Badge.vue';
import StatCard from '../components/StatCard.vue';
import Card from '../components/Card.vue';
import AppIcon from '../components/AppIcon.vue';
import EmptyState from '../components/EmptyState.vue';
import ListPageLayout from '../components/ListPageLayout.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import { baseLineOptions } from '../composables/chartOptions.js';

ChartJS.register(
  LineElement, PointElement, LinearScale, CategoryScale,
  Tooltip, Filler, ArcElement, Legend,
);

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

// ── 资源字段定义(统一 6 项配额维度) ───────────────────────────
const RESOURCES = [
  { key: 'agents', quotaKey: 'max_agents', usageKey: 'active_agents', unit: 'unitCount', icon: 'bot' },
  { key: 'users', quotaKey: 'max_users', usageKey: 'active_users', unit: 'unitCount', icon: 'user' },
  { key: 'cpu', quotaKey: 'max_cpu_cores', usageKey: 'cpu_cores', unit: 'unitCores', icon: 'cpu' },
  { key: 'memory', quotaKey: 'max_memory_mb', usageKey: 'memory_mb', unit: 'unitMB', icon: 'server' },
  { key: 'storage', quotaKey: 'max_storage_mb', usageKey: 'storage_mb', unit: 'unitMB', icon: 'database' },
  { key: 'apiCalls', quotaKey: 'max_api_calls_per_day', usageKey: 'api_calls_today', unit: 'unitCalls', icon: 'activity' },
];

// ── 状态 ────────────────────────────────────────────────────────
const tenants = ref([]);
const overview = ref({ heatmap: [], allocation: { labels: [], values: [] } });
const loading = ref(true);
const error = ref('');

// 详情抽屉
const drawerOpen = ref(false);
const detailTenant = ref(null);
const trendData = ref({ labels: [], series: [] });
const quotaHistory = ref([]);
const detailAlerts = ref([]);

// 调整配额
const showAdjust = ref(false);
const adjustTarget = ref(null);
const adjustForm = ref({});
const saving = ref(false);

// ── 工具函数 ────────────────────────────────────────────────────

function getQuota(tenant, r) {
  const q = tenant.quota || {};
  return q[r.quotaKey] ?? 0;
}
function getUsage(tenant, r) {
  const u = tenant.usage || {};
  return u[r.usageKey] ?? 0;
}
/** 使用率(原始,可超 100) */
function usageRateRaw(tenant, r) {
  const limit = getQuota(tenant, r);
  const used = getUsage(tenant, r);
  if (!limit || limit <= 0) return 0;
  return Math.round((used / limit) * 100);
}
/** 使用率(截断 100,用于进度条宽度) */
function usageRateCapped(tenant, r) {
  return Math.min(100, usageRateRaw(tenant, r));
}
/** 进度条宽度 */
function barWidth(tenant, r) {
  return usageRateCapped(tenant, r) + '%';
}
/** 进度条三色 class */
function barToneClass(rate) {
  if (rate >= 90) return 'is-danger';
  if (rate >= 70) return 'is-warn';
  return 'is-ok';
}
/** 格式化使用量显示:已用 / 上限 */
function formatUsage(tenant, r) {
  const used = getUsage(tenant, r);
  const limit = getQuota(tenant, r);
  const unit = t('view.quotas.' + r.unit);
  const usedStr = formatNum(used) + (unit ? ' ' + unit : '');
  const limitStr = limit > 0 ? formatNum(limit) : t('view.quotas.unlimited');
  return usedStr + ' / ' + limitStr;
}
function formatNum(n) {
  if (n === null || n === undefined) return '0';
  if (typeof n !== 'number') return String(n);
  if (n >= 1000) return n.toLocaleString();
  return String(n);
}
function statusTone(s) {
  if (s === 'active') return 'success';
  if (s === 'suspended') return 'fail';
  if (s === 'trial') return 'info';
  return 'neutral';
}
function alertTone(level) {
  if (level === 'critical') return 'fail';
  if (level === 'warning') return 'warn';
  return 'info';
}
/** 判断租户是否超额(任一资源 > 100%) */
function isTenantOverQuota(tenant) {
  return RESOURCES.some((r) => usageRateRaw(tenant, r) > 100);
}
/** 热力图单元格 class */
function heatCellClass(tenant, r) {
  const rate = usageRateRaw(tenant, r);
  if (rate > 100) return 'heatmap__cell--over';
  if (rate >= 90) return 'heatmap__cell--high';
  if (rate >= 50) return 'heatmap__cell--mid';
  return 'heatmap__cell--low';
}
/** 热力图单元格数值 */
function heatCellRate(tenant, r) {
  return usageRateRaw(tenant, r);
}
/** 热力图单元格 tooltip */
function heatCellTitle(tenant, r) {
  return t('view.quotas.' + r.key) + ': ' + formatUsage(tenant, r);
}
/** 相对时间格式化 */
function formatTime(ts) {
  if (!ts) return '—';
  const now = Date.now();
  const time = typeof ts === 'number' ? ts : new Date(ts).getTime();
  if (!time || Number.isNaN(time)) return '—';
  const diff = Math.max(0, now - time);
  const min = Math.floor(diff / 60000);
  if (min < 1) return t('view.quotas.justNow');
  if (min < 60) return t('view.quotas.minutesAgo', { n: min });
  const h = Math.floor(min / 60);
  if (h < 24) return t('view.quotas.hoursAgo', { n: h });
  const d = Math.floor(h / 24);
  return t('view.quotas.daysAgo', { n: d });
}

// ── 统计卡片计算属性 ────────────────────────────────────────────
const overQuotaCount = computed(() => tenants.value.filter(isTenantOverQuota).length);
const avgUsageRate = computed(() => {
  if (!tenants.value.length) return 0;
  let sum = 0;
  let count = 0;
  tenants.value.forEach((tenant) => {
    RESOURCES.forEach((r) => {
      const limit = getQuota(tenant, r);
      if (limit > 0) {
        sum += Math.min(100, usageRateRaw(tenant, r));
        count++;
      }
    });
  });
  return count ? Math.round(sum / count) : 0;
});
const totalActiveAlerts = computed(() =>
  detailAlerts.value.filter((a) => a.status !== 'resolved').length,
);

// ── 图表数据 ────────────────────────────────────────────────────

/** 资源分配饼图 */
const allocationChartData = computed(() => {
  const alloc = overview.value.allocation || { labels: [], values: [] };
  const palette = [
    '#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#3b82f6', '#a855f7', '#14b8a6', '#ec4899',
  ];
  return {
    labels: alloc.labels || [],
    datasets: [{
      data: alloc.values || [],
      backgroundColor: (alloc.labels || []).map((_, i) => palette[i % palette.length]),
      borderWidth: 2,
      borderColor: 'var(--surface, #fff)',
    }],
  };
});
const allocationChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
    tooltip: { enabled: true },
  },
};

/** 使用量趋势折线图 */
const trendChartData = computed(() => {
  const td = trendData.value || { labels: [], series: [] };
  const palette = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#3b82f6', '#a855f7'];
  return {
    labels: td.labels || [],
    datasets: (td.series || []).map((s, i) => ({
      label: s.label || '',
      data: s.data || [],
      borderColor: palette[i % palette.length],
      backgroundColor: palette[i % palette.length] + '20',
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 5,
    })),
  };
});
const trendChartOptions = baseLineOptions({ legendVisible: true });

// ── 数据加载 ────────────────────────────────────────────────────

async function loadTenants() {
  loading.value = true;
  error.value = '';
  try {
    const d = await api.get('/api/tenant/list');
    tenants.value = d.tenants || [];
  } catch (e) {
    error.value = e.message || t('view.quotas.loadError');
    tenants.value = [];
  } finally {
    loading.value = false;
  }
}

async function loadOverview() {
  try {
    const d = await api.get('/api/tenant/quotas/overview');
    overview.value = {
      heatmap: d.heatmap || [],
      allocation: d.allocation || { labels: [], values: [] },
    };
  } catch {
    // 概览失败不阻塞主列表,降级为空
    overview.value = { heatmap: [], allocation: { labels: [], values: [] } };
  }
}

async function loadAll() {
  await Promise.allSettled([loadTenants(), loadOverview()]);
}

// ── 详情抽屉 ────────────────────────────────────────────────────

async function openDetail(tenant) {
  detailTenant.value = tenant;
  drawerOpen.value = true;
  trendData.value = { labels: [], series: [] };
  quotaHistory.value = [];
  detailAlerts.value = [];

  const id = tenant.tenant_id;
  // 并行加载详情数据,各自容错
  const [trendRes, historyRes, alertsRes, usageRes] = await Promise.allSettled([
    api.get(`/api/tenant/${id}/usage/trend`),
    api.get(`/api/tenant/${id}/quota/history`),
    api.get(`/api/tenant/${id}/alerts`),
    api.get(`/api/tenant/${id}/usage`),
  ]);

  if (trendRes.status === 'fulfilled') {
    trendData.value = trendRes.value || { labels: [], series: [] };
  }
  if (historyRes.status === 'fulfilled') {
    quotaHistory.value = historyRes.value.history || historyRes.value.changes || [];
  }
  if (alertsRes.status === 'fulfilled') {
    detailAlerts.value = alertsRes.value.alerts || [];
  }
  // 实时使用量更新到 detailTenant
  if (usageRes.status === 'fulfilled' && usageRes.value) {
    detailTenant.value = {
      ...tenant,
      usage: { ...tenant.usage, ...(usageRes.value.usage || usageRes.value) },
    };
  }
}

function closeDetail() {
  drawerOpen.value = false;
  detailTenant.value = null;
}

// ── 调整配额 ────────────────────────────────────────────────────

function openAdjust(tenant) {
  adjustTarget.value = tenant;
  const q = tenant.quota || {};
  adjustForm.value = {};
  RESOURCES.forEach((r) => {
    adjustForm.value[r.quotaKey] = q[r.quotaKey] ?? 0;
  });
  showAdjust.value = true;
}

async function saveAdjust() {
  if (!adjustTarget.value) return;
  // 校验:非负数
  const invalid = Object.values(adjustForm.value).some(
    (v) => typeof v !== 'number' || v < 0 || Number.isNaN(v),
  );
  if (invalid) {
    toast.warn(t('view.quotas.invalidValue'));
    return;
  }
  saving.value = true;
  try {
    const id = adjustTarget.value.tenant_id;
    await api.post(`/api/tenant/${id}/quota`, { quota: { ...adjustForm.value } });
    toast.success(t('view.quotas.adjustSuccess', { name: adjustTarget.value.name || id }));
    showAdjust.value = false;
    await loadTenants();
  } catch (e) {
    toast.error(e.message || t('view.quotas.adjustFailed'));
  } finally {
    saving.value = false;
  }
}

// ── 告警解决 ────────────────────────────────────────────────────

async function resolveAlert(alert) {
  if (!detailTenant.value) return;
  if (typeof confirm === 'function' && !confirm(t('view.quotas.resolveConfirm'))) return;
  const id = detailTenant.value.tenant_id;
  try {
    await api.post(`/api/tenant/${id}/alerts/${alert.id}/resolve`, {});
    alert.status = 'resolved';
    toast.success(t('view.quotas.resolved'));
  } catch (e) {
    toast.error(e.message || t('view.quotas.adjustFailed'));
  }
}

onMounted(loadAll);
</script>

<style scoped>
.quotas-page { display: flex; flex-direction: column; gap: var(--sp-4); }

/* ── 概览仪表板 ── */
.overview-dashboard {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
  margin-bottom: var(--sp-4);
}
@media (max-width: 900px) {
  .overview-dashboard { grid-template-columns: 1fr; }
}

/* ── 热力图 ── */
.heatmap {
  display: grid;
  grid-auto-rows: minmax(28px, auto);
  font-size: var(--fs-xs);
  overflow-x: auto;
}
.heatmap__header,
.heatmap__row {
  display: grid;
  grid-template-columns: 120px repeat(6, 1fr);
  gap: 2px;
}
.heatmap__header {
  margin-bottom: 2px;
}
.heatmap__corner { }
.heatmap__col-header,
.heatmap__row-header {
  display: flex;
  align-items: center;
  padding: 0 var(--sp-2);
  font-weight: 600;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.heatmap__row-header { justify-content: flex-start; }
.heatmap__cell {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-sm);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  min-height: 28px;
}
.heatmap__cell--low  { background: var(--success-soft); color: var(--success); }
.heatmap__cell--mid  { background: color-mix(in srgb, var(--success) 45%, var(--surface)); color: var(--text); }
.heatmap__cell--high { background: var(--warn-soft); color: var(--warn); }
.heatmap__cell--over { background: var(--fail-soft); color: var(--fail); font-weight: 700; }
.heatmap__row.is-over .heatmap__row-header { color: var(--fail); font-weight: 700; }
.heatmap__legend {
  display: flex;
  gap: var(--sp-3);
  margin-top: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.legend-item { display: inline-flex; align-items: center; gap: 4px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: var(--r-full); }
.dot--low  { background: var(--success-soft); border: 1px solid var(--success); }
.dot--mid  { background: var(--warn-soft); border: 1px solid var(--warn); }
.dot--over { background: var(--fail-soft); border: 1px solid var(--fail); }

/* ── 资源分配饼图 ── */
.allocation-chart { height: 220px; }

/* ── 租户配额卡片网格 ── */
.quota-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--sp-4);
}
.quota-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  transition: border-color var(--motion) var(--ease);
}
.quota-card:hover { border-color: var(--border-strong); }
.quota-card.is-over { border-color: var(--fail); border-left: 3px solid var(--fail); }
.quota-card.is-suspended { opacity: 0.65; }
.quota-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-2);
}
.quota-card__title h3 { margin: 0; font-size: var(--fs-md); }
.quota-card__title .muted { font-size: var(--fs-xs); }
.quota-card__meta {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
}
.quota-card__body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.quota-row__head {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-sm);
  margin-bottom: 4px;
}
.quota-row__name { flex: 1; color: var(--text-muted); }
.quota-row__val { font-variant-numeric: tabular-nums; font-size: var(--fs-xs); color: var(--text); }

/* ── 进度条(三色) ── */
.quota-bar {
  height: 6px;
  background: var(--surface-2);
  border-radius: var(--r-full);
  overflow: hidden;
}
.quota-bar__fill {
  height: 100%;
  border-radius: var(--r-full);
  transition: width var(--motion) var(--ease);
}
.quota-bar__fill.is-ok     { background: var(--success); }
.quota-bar__fill.is-warn   { background: var(--warn); }
.quota-bar__fill.is-danger { background: var(--fail); }

.quota-card__actions {
  display: flex;
  gap: var(--sp-2);
  margin-top: var(--sp-1);
}

/* ── 详情抽屉内容 ── */
.drawer-section { margin-bottom: var(--sp-5); }
.drawer-section__title {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 var(--sp-2);
}
.usage-grid {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.usage-item {
  display: grid;
  grid-template-columns: 80px 1fr auto;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
}
.usage-item__name { color: var(--text-muted); }
.usage-item__bar {
  height: 6px;
  background: var(--surface-2);
  border-radius: var(--r-full);
  overflow: hidden;
}
.usage-item__val { font-variant-numeric: tabular-nums; font-size: var(--fs-xs); }

.trend-chart { height: 200px; }

/* ── 配额变更历史 ── */
.history-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
.history-item {
  padding: var(--sp-2);
  background: var(--surface-2);
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
}
.history-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.history-item__body {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

/* ── 告警列表 ── */
.alert-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: var(--sp-2); }
.alert-item {
  padding: var(--sp-3);
  background: var(--surface-2);
  border-radius: var(--r-md);
  border-left: 3px solid var(--warn);
}
.alert-item.is-resolved { border-left-color: var(--success); opacity: 0.7; }
.alert-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.alert-item__body { font-size: var(--fs-sm); color: var(--text); margin-bottom: 4px; }
.alert-item__foot {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

/* ── 调整配额 Modal ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: calc(var(--z-modal, 90) + 10);
  background: rgba(10, 12, 16, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal {
  background: var(--surface);
  border-radius: var(--r-lg);
  padding: var(--sp-5);
  width: min(520px, 92vw);
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal h3 { margin: 0 0 var(--sp-1); }
.adjust-form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  margin: var(--sp-3) 0;
}
.adjust-row {
  display: grid;
  grid-template-columns: 140px 1fr 60px;
  align-items: center;
  gap: var(--sp-2);
}
.adjust-row__label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-sm);
}
.adjust-row__input { width: 100%; }
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
</style>