<template>
  <div class="audit-view">
    <PageHeader>
      <template #badges>
        <Badge tone="brand" icon="shield">{{ t('view.audit.enterprise') }}</Badge>
      </template>
      <span v-if="lastUpdated" class="last-updated">{{ t('view.audit.updated') }} {{ lastUpdated }}</span>
      <button class="act-btn" :disabled="loading" :title="t('view.audit.refresh')" @click="loadAll">
        <AppIcon name="refresh" :size="14" :class="{ spinning: loading }" aria-hidden="true" />
      </button>
    </PageHeader>

    <!-- Tab 切换栏 (独立于 PageHeader, 便于测试与布局) -->
    <Segmented v-model="tab" :options="tabOptions" size="sm" class="audit-tabs" />

    <!-- ════════════════════════════════════════════════════════════════════
         Tab 1: Events  (默认, 包含统计/图表/过滤/表格/导出/实时提示)
    ════════════════════════════════════════════════════════════════════ -->
    <div v-if="tab === 'events'" class="audit-events">
      <!-- 顶部统计卡片: 今日操作 / 高风险 / 活跃用户 / 异常事件 -->
      <div class="stat-row">
        <StatCard :label="t('view.audit.statTodayOps')" :value="statTodayOps" icon="activity" tone="brand" :loading="loading" />
        <StatCard :label="t('view.audit.statHighRisk')" :value="statHighRisk" icon="alert-triangle" tone="fail" :loading="loading" />
        <StatCard :label="t('view.audit.statActiveUsers')" :value="statActiveUsers" icon="user" tone="info" :loading="loading" />
        <StatCard :label="t('view.audit.statAnomalies')" :value="statAnomalies" icon="zap" tone="warn" :loading="loading" />
      </div>

      <!-- 高级过滤栏: 时间范围 / 用户 / 操作类型 / 资源 / 风险等级 / 关键词 -->
      <FilterBar
        :model-value="filters"
        :schema="filterSchema"
        search-key="keyword"
        :search-placeholder="t('view.audit.filterKeyword')"
        :results-label="`${visibleRows.length} / ${events.value.length}`"
        class="audit-filterbar"
      >
        <template #extra>
          <select v-model="filters.range" class="filterbar__select" :aria-label="t('view.audit.filterTimeRange')">
            <option value="24h">{{ t('view.audit.range24h') }}</option>
            <option value="7d">{{ t('view.audit.range7d') }}</option>
            <option value="30d">{{ t('view.audit.range30d') }}</option>
          </select>
          <div class="export-group">
            <button class="act-btn small" :disabled="!visibleRows.length" @click="exportCsv">
              <AppIcon name="download" :size="13" /> CSV
            </button>
            <button class="act-btn small" :disabled="!visibleRows.length" @click="exportJson">
              <AppIcon name="download" :size="13" /> JSON
            </button>
          </div>
        </template>
      </FilterBar>

      <!-- 三态主体: 错误 → 加载 → 内容 -->
      <div v-if="events.error" class="audit-error">
        <EmptyState icon="alert-triangle" tone="fail" :title="t('view.audit.eventsError')" :description="events.error" />
      </div>
      <div v-else-if="loading" class="audit-loading">
        <Skeleton :lines="8" block />
      </div>
      <div v-else class="audit-body">
        <!-- 操作趋势图 (折线/柱状切换) -->
        <Card :title="t('view.audit.trendTitle')" icon="activity" :margin-bottom="16">
          <div class="chart-head">
            <span class="muted">{{ t('view.audit.trendDesc') }}</span>
            <Segmented v-model="trendKind" :options="trendKindOptions" size="sm" />
          </div>
          <div class="chart-box">
            <Line v-if="trendKind === 'line' && trendData.labels.length" :data="trendData" :options="trendOptions" />
            <Bar v-else-if="trendKind === 'bar' && trendData.labels.length" :data="trendData" :options="trendOptions" />
            <EmptyState v-else icon="activity" :title="t('view.audit.noMatch')" />
          </div>
        </Card>

        <!-- 两列: 热力图 + 饼图 -->
        <div class="two-col">
          <Card :title="t('view.audit.heatmapTitle')" icon="grid" :margin-bottom="16">
            <div class="muted">{{ t('view.audit.heatmapDesc') }}</div>
            <div v-if="heatmapUsers.length" class="heatmap">
              <table class="heatmap__table">
                <thead>
                  <tr>
                    <th class="heatmap__corner">{{ t('view.audit.actor') }} \\ {{ t('view.audit.columnAction') }}</th>
                    <th v-for="act in heatmapActions" :key="act" class="heatmap__col-head">{{ act }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="u in heatmapUsers" :key="u">
                    <td class="heatmap__row-head">{{ u }}</td>
                    <td
                      v-for="act in heatmapActions"
                      :key="act"
                      class="heatmap__cell"
                      :class="heatmapCellClass(u, act)"
                      :title="`${u} · ${act} = ${heatmapCount(u, act)}`"
                    >{{ heatmapCount(u, act) || '' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState v-else icon="grid" :title="t('view.audit.heatmapEmpty')" />
          </Card>

          <Card :title="t('view.audit.pieTitle')" icon="clipboard" :margin-bottom="16">
            <div class="muted">{{ t('view.audit.pieDesc') }}</div>
            <div class="chart-box pie-box">
              <Pie v-if="pieData.labels.length" :data="pieData" :options="pieOptions" />
              <EmptyState v-else icon="clipboard" :title="t('view.audit.pieEmpty')" />
            </div>
          </Card>
        </div>

        <!-- 事件表格 + 实时监控提示 -->
        <Card :title="t('view.audit.title')" icon="scroll" :margin-bottom="16">
          <template #actions>
            <span class="live-hint" :class="{ 'is-live': liveConnected }">
              <AppIcon :name="liveConnected ? 'radio' : 'clock'" :size="12" />
              {{ liveConnected ? t('view.audit.liveConnected') : t('view.audit.liveDisconnected') }}
            </span>
            <button class="act-btn small" @click="toggleLive">
              {{ liveConnected ? t('view.audit.liveDisconnect') : t('view.audit.liveConnect') }}
            </button>
          </template>
          <div v-if="liveConnected" class="live-tail">
            <div class="live-tail__head">{{ t('view.audit.liveTail') }} · {{ t('view.audit.liveHint') }}</div>
            <div v-if="liveEvents.length" class="live-tail__list">
              <div v-for="(ev, i) in liveEvents" :key="i" class="live-tail__item">
                <span class="live-tail__time">{{ ev.time }}</span>
                <Badge :tone="levelTone(ev.level)">{{ ev.level }}</Badge>
                <span class="live-tail__action">{{ ev.action }}</span>
                <span class="live-tail__actor">{{ ev.actor }}</span>
              </div>
            </div>
            <div v-else class="live-tail__empty">{{ t('view.audit.liveEmpty') }}</div>
          </div>
          <DataTable
            :columns="cols" :rows="visibleRows" :loading="false"
            :empty-text="t('view.audit.noMatch')" :sortable="true" row-key="time"
          />
        </Card>
      </div>
    </div>

    <!-- ════════════════════════════════════════════════════════════════════
         Tab 2: Alert Rules  (规则列表 + 启用/禁用 + 创建/编辑)
    ════════════════════════════════════════════════════════════════════ -->
    <div v-if="tab === 'rules'" class="audit-rules">
      <Card :title="t('view.audit.rulesTitle')" icon="shield" :subtitle="t('view.audit.rulesDesc')">
        <template #actions>
          <button class="act-btn" @click="openRuleEditor()">
            <AppIcon name="plus" :size="14" /> {{ t('view.audit.ruleCreate') }}
          </button>
        </template>
        <div v-if="rulesError" class="audit-error">
          <EmptyState icon="alert-triangle" tone="fail" :title="t('view.audit.rulesError')" :description="rulesError" />
        </div>
        <div v-else-if="rulesLoading" class="audit-loading">
          <Skeleton :lines="5" block />
        </div>
        <div v-else-if="!rules.length" class="audit-error">
          <EmptyState icon="shield" :title="t('view.audit.rulesEmpty')" />
        </div>
        <div v-else class="rule-list">
          <div v-for="r in rules" :key="r.id" class="rule-row">
            <div class="rule-row__main">
              <span class="rule-row__name">{{ r.name }}</span>
              <span class="rule-row__cond">{{ r.condition }}</span>
            </div>
            <Badge :tone="levelTone(r.severity)">{{ r.severity }}</Badge>
            <span class="rule-row__state" :class="r.enabled ? 'is-on' : 'is-off'">
              <AppIcon :name="r.enabled ? 'check-circle' : 'x-circle'" :size="13" />
              {{ r.enabled ? t('common.on') : t('common.off') }}
            </span>
            <div class="rule-row__actions">
              <button class="act-btn small" :title="t('view.audit.ruleEdit')" @click="openRuleEditor(r)">
                <AppIcon name="wrench" :size="13" />
              </button>
              <button class="act-btn small" :title="t('view.audit.ruleToggle')" @click="toggleRule(r)">
                <AppIcon :name="r.enabled ? 'x-circle' : 'check-circle'" :size="13" />
              </button>
              <button class="act-btn small danger" :title="t('view.audit.ruleDelete')" @click="deleteRule(r)">
                <AppIcon name="trash" :size="13" />
              </button>
            </div>
          </div>
        </div>
      </Card>

      <!-- 规则编辑抽屉 -->
      <DetailDrawer
        :open="ruleEditor.open"
        :title="ruleEditor.rule.id ? t('view.audit.ruleEdit') : t('view.audit.ruleCreate')"
        icon="shield"
        @close="ruleEditor.open = false"
      >
        <div class="rule-form">
          <label class="form-label">{{ t('view.audit.ruleName') }}</label>
          <input v-model="ruleEditor.rule.name" class="form-input" :placeholder="t('view.audit.ruleNamePlaceholder')" />
          <label class="form-label">{{ t('view.audit.ruleCondition') }}</label>
          <input v-model="ruleEditor.rule.condition" class="form-input" :placeholder="t('view.audit.ruleConditionPlaceholder')" />
          <label class="form-label">{{ t('view.audit.ruleSeverity') }}</label>
          <select v-model="ruleEditor.rule.severity" class="form-input">
            <option value="info">{{ t('view.audit.info') }}</option>
            <option value="warning">{{ t('view.audit.warning') }}</option>
            <option value="critical">{{ t('view.audit.critical') }}</option>
          </select>
          <label class="form-check">
            <input v-model="ruleEditor.rule.enabled" type="checkbox" />
            {{ t('view.audit.ruleEnabled') }}
          </label>
        </div>
        <template #footer>
          <button class="act-btn" :disabled="!ruleEditor.rule.name?.trim()" @click="saveRule">{{ t('view.audit.ruleSave') }}</button>
          <button class="act-btn ghost" @click="ruleEditor.open = false">{{ t('view.audit.ruleCancel') }}</button>
        </template>
      </DetailDrawer>
    </div>

    <!-- ════════════════════════════════════════════════════════════════════
         Tab 3: Alert History  (近期触发的告警)
    ════════════════════════════════════════════════════════════════════ -->
    <div v-if="tab === 'history'" class="audit-history">
      <Card :title="t('view.audit.historyTitle')" icon="clock" :subtitle="t('view.audit.historyDesc')">
        <div v-if="historyError" class="audit-error">
          <EmptyState icon="alert-triangle" tone="fail" :title="t('view.audit.historyError')" :description="historyError" />
        </div>
        <DataTable
          v-else
          :columns="historyCols" :rows="history" :loading="historyLoading"
          :empty-text="t('view.audit.historyEmpty')" row-key="id" :sortable="true"
        />
      </Card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue';
import { Line, Bar, Pie } from 'vue-chartjs';
import {
  Chart as ChartJS, LineElement, PointElement, LinearScale, CategoryScale,
  Tooltip, Filler, Legend, BarElement, ArcElement,
} from 'chart.js';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Segmented from '../components/Segmented.vue';
import FilterBar from '../components/FilterBar.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import Badge from '../components/Badge.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import DetailDrawer from '../components/DetailDrawer.vue';
import { cssVar, cssVarAlpha } from '../composables/chartTokens.js';
import { baseLineOptions } from '../composables/chartOptions.js';

ChartJS.register(
  LineElement, PointElement, LinearScale, CategoryScale,
  Tooltip, Filler, Legend, BarElement, ArcElement,
);

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

const loading = ref(true);
const lastUpdated = ref('');
const events = reactive({ value: [], error: '' });
const summary = reactive({ data: { total: 0, by_action: {}, by_actor: {} }, error: '' });

// ── Tab 状态 ──
const tab = ref('events');
const tabOptions = computed(() => [
  { value: 'events', label: t('view.audit.tabEvents'), icon: 'scroll' },
  { value: 'rules', label: t('view.audit.tabRules'), icon: 'shield' },
  { value: 'history', label: t('view.audit.tabHistory'), icon: 'clock' },
]);

// ── 高级过滤 ──
const filters = reactive({
  action: '', level: '', actor: '', keyword: '', resource: '', risk: '', range: '24h',
});

const filterSchema = computed(() => [
  {
    key: 'action',
    label: t('view.audit.filterActionType'),
    options: actionOptions.value.map((a) => ({ value: a, label: a })),
  },
  {
    key: 'level',
    label: t('view.audit.filterRisk'),
    options: [
      { value: 'info', label: t('view.audit.info') },
      { value: 'warning', label: t('view.audit.warning') },
      { value: 'critical', label: t('view.audit.critical') },
    ],
  },
  {
    key: 'actor',
    label: t('view.audit.filterUser'),
    options: actorOptions.value.map((a) => ({ value: a, label: a })),
  },
  {
    key: 'resource',
    label: t('view.audit.filterResource'),
    options: resourceOptions.value.map((r) => ({ value: r, label: r })),
  },
]);

const actionOptions = computed(() => {
  const set = new Set();
  events.value.forEach((e) => e.action && set.add(e.action));
  return [...set].sort();
});
const actorOptions = computed(() => {
  const set = new Set();
  events.value.forEach((e) => e.actor && set.add(e.actor));
  return [...set].sort();
});
const resourceOptions = computed(() => {
  const set = new Set();
  events.value.forEach((e) => e.target && set.add(e.target));
  return [...set].sort();
});

const cols = computed(() => [
  { key: 'time', label: t('view.audit.time'), type: 'time', sortable: true },
  { key: 'action', label: t('view.audit.columnAction'), sortable: true },
  { key: 'actor', label: t('view.audit.actor'), sortable: true },
  { key: 'target', label: t('view.audit.target') },
  { key: 'level', label: t('view.audit.level'), type: 'badge', sortable: true },
  { key: 'result', label: t('view.audit.columnResult') },
]);

const visibleRows = computed(() => {
  const fa = filters.action, fl = filters.level, fo = filters.actor;
  const fr = filters.resource, fk = (filters.keyword || '').trim().toLowerCase();
  return events.value.filter((e) => {
    if (fa && e.action !== fa) return false;
    if (fl && (e.level || 'info') !== fl) return false;
    if (fo && e.actor !== fo) return false;
    if (fr && (e.target || '') !== fr) return false;
    if (fk) {
      const hay = `${e.action || ''} ${e.actor || ''} ${e.target || ''} ${e.detail || ''}`.toLowerCase();
      if (!hay.includes(fk)) return false;
    }
    return true;
  });
});

// ── 统计卡片 ──
const statTodayOps = computed(() => {
  const now = Date.now();
  const start = now - 24 * 3600 * 1000;
  return events.value.filter((e) => toMs(e.time) >= start).length;
});
const statHighRisk = computed(() =>
  events.value.filter((e) => (e.level || 'info') === 'critical').length,
);
const statActiveUsers = computed(() => {
  const now = Date.now();
  const start = now - 24 * 3600 * 1000;
  const set = new Set();
  events.value.forEach((e) => {
    if (toMs(e.time) >= start && e.actor) set.add(e.actor);
  });
  return set.size;
});
const statAnomalies = computed(() =>
  events.value.filter((e) => {
    const lv = (e.level || 'info');
    return lv === 'warning' || lv === 'critical';
  }).length,
);

function toMs(ts) {
  if (!ts) return 0;
  if (typeof ts === 'number') return ts > 1e12 ? ts : ts * 1000;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? 0 : d.getTime();
}

// ── 趋势图 ──
const trendKind = ref('line');
const trendKindOptions = computed(() => [
  { value: 'line', label: t('view.audit.trendLine') },
  { value: 'bar', label: t('view.audit.trendBar') },
]);

const trendBuckets = computed(() => {
  // 按小时分桶(最多 24 个桶), 分别统计 ops / highRisk
  const rangeMs = rangeToMs(filters.range);
  const now = Date.now();
  const start = now - rangeMs;
  const bucketMs = rangeMs / 24;
  const buckets = Array.from({ length: 24 }, (_, i) => ({
    t: start + i * bucketMs, ops: 0, highRisk: 0,
  }));
  events.value.forEach((e) => {
    const ms = toMs(e.time);
    if (ms < start || ms > now) return;
    const idx = Math.min(23, Math.floor((ms - start) / bucketMs));
    buckets[idx].ops += 1;
    if ((e.level || 'info') === 'critical') buckets[idx].highRisk += 1;
  });
  return buckets;
});

function rangeToMs(r) {
  if (r === '7d') return 7 * 24 * 3600 * 1000;
  if (r === '30d') return 30 * 24 * 3600 * 1000;
  return 24 * 3600 * 1000; // 24h
}

const trendData = computed(() => {
  const bs = trendBuckets.value;
  return {
    labels: bs.map((b) => fmtHour(b.t)),
    datasets: [
      {
        label: t('view.audit.trendDatasetOps'),
        data: bs.map((b) => b.ops),
        borderColor: cssVar('--chart-1', '#3574f0'),
        backgroundColor: trendKind.value === 'bar'
          ? cssVarAlpha('--chart-1', 0.6)
          : cssVarAlpha('--chart-1', 0.12),
        tension: 0.3,
        fill: trendKind.value === 'line',
        borderWidth: 2,
      },
      {
        label: t('view.audit.trendDatasetHighRisk'),
        data: bs.map((b) => b.highRisk),
        borderColor: cssVar('--chart-5', '#9e8cfc'),
        backgroundColor: trendKind.value === 'bar'
          ? cssVarAlpha('--chart-5', 0.6)
          : cssVarAlpha('--chart-5', 0.08),
        tension: 0.3,
        fill: trendKind.value === 'line',
        borderWidth: 2,
      },
    ],
  };
});

const trendOptions = computed(() => baseLineOptions({}));

function fmtHour(ms) {
  const d = new Date(ms);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

// ── 热力图 (用户 × 操作) ──
const heatmapUsers = computed(() => {
  const set = new Set();
  visibleRows.value.forEach((e) => e.actor && set.add(e.actor));
  return [...set].slice(0, 12); // 限制行数避免过长
});
const heatmapActions = computed(() => {
  const set = new Set();
  visibleRows.value.forEach((e) => e.action && set.add(e.action));
  return [...set].slice(0, 10); // 限制列数
});
function heatmapCount(u, act) {
  return visibleRows.value.filter((e) => e.actor === u && e.action === act).length;
}
function heatmapCellClass(u, act) {
  const n = heatmapCount(u, act);
  if (!n) return 'hm-0';
  if (n >= 5) return 'hm-3';
  if (n >= 2) return 'hm-2';
  return 'hm-1';
}

// ── 饼图 (操作分布) ──
const pieData = computed(() => {
  const counts = {};
  visibleRows.value.forEach((e) => {
    const k = e.action || 'unknown';
    counts[k] = (counts[k] || 0) + 1;
  });
  const labels = Object.keys(counts);
  const palette = [
    '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5',
    '--chart-6', '--chart-7', '--chart-8', '--chart-9', '--chart-10',
  ];
  return {
    labels,
    datasets: [{
      data: labels.map((l) => counts[l]),
      backgroundColor: labels.map((_, i) => cssVar(palette[i % palette.length], '#3574f0')),
      borderWidth: 2,
      borderColor: cssVar('--surface', '#fff'),
    }],
  };
});
const pieOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
    tooltip: { enabled: true },
  },
};

// ── 实时监控 (可选 WebSocket) ──
const liveConnected = ref(false);
const liveEvents = ref([]);
let liveWs = null;
function toggleLive() {
  if (liveConnected.value) {
    disconnectLive();
  } else {
    connectLive();
  }
}
function connectLive() {
  // 软连接: 尝试打开 /ws, 失败则仅切换 UI 状态(便于无后端环境演示)
  try {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    liveWs = new WebSocket(`${proto}//${window.location.host}/ws`);
    liveWs.onopen = () => { liveConnected.value = true; };
    liveWs.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        const ev = data.event || data;
        liveEvents.value.unshift({
          time: ev.time || ev.timestamp || new Date().toISOString(),
          level: ev.level || ev.severity || 'info',
          action: ev.action || '',
          actor: ev.actor || '',
        });
        if (liveEvents.value.length > 50) liveEvents.value.pop();
      } catch { /* ignore non-JSON */ }
    };
    liveWs.onclose = () => { liveConnected.value = false; liveWs = null; };
    liveWs.onerror = () => { liveConnected.value = false; liveWs = null; };
  } catch {
    // 测试/无 WebSocket 环境下, 仅切换状态便于 UI 预览
    liveConnected.value = true;
  }
}
function disconnectLive() {
  if (liveWs) {
    try { liveWs.close(); } catch { /* ignore */ }
    liveWs = null;
  }
  liveConnected.value = false;
}

function levelTone(lv) {
  if (lv === 'critical') return 'fail';
  if (lv === 'warning') return 'warn';
  return 'info';
}

// ── 导出 CSV / JSON ──
function exportCsv() {
  const rows = visibleRows.value;
  if (!rows.length) { toast.warn(t('view.audit.exportEmpty')); return; }
  const headers = ['time', 'action', 'actor', 'target', 'level', 'result', 'ip', 'detail'];
  const lines = [headers.join(',')];
  rows.forEach((r) => {
    lines.push(headers.map((h) => csvEscape(r[h])).join(','));
  });
  downloadBlob(lines.join('\n'), 'audit-events.csv', 'text/csv');
  toast.success(t('view.audit.exportDone', { n: rows.length }));
}
function exportJson() {
  const rows = visibleRows.value;
  if (!rows.length) { toast.warn(t('view.audit.exportEmpty')); return; }
  downloadBlob(JSON.stringify(rows, null, 2), 'audit-events.json', 'application/json');
  toast.success(t('view.audit.exportDone', { n: rows.length }));
}
function csvEscape(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}
function downloadBlob(content, filename, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── 告警规则 ──
const rules = ref([]);
const rulesLoading = ref(false);
const rulesError = ref('');
const ruleEditor = reactive({ open: false, rule: { id: '', name: '', condition: '', severity: 'warning', enabled: true } });

async function loadRules() {
  rulesLoading.value = true;
  rulesError.value = '';
  try {
    const d = await api.get('/api/audit/rules');
    rules.value = d.rules || [];
  } catch (e) {
    rulesError.value = e.message || t('view.audit.rulesUnavailable');
  } finally {
    rulesLoading.value = false;
  }
}
function openRuleEditor(rule) {
  if (rule) {
    ruleEditor.rule = { ...rule };
  } else {
    ruleEditor.rule = { id: '', name: '', condition: '', severity: 'warning', enabled: true };
  }
  ruleEditor.open = true;
}
async function saveRule() {
  try {
    const r = ruleEditor.rule;
    if (r.id) {
      await api.put(`/api/audit/rules/${r.id}`, r);
      toast.success(t('view.audit.ruleUpdated'));
    } else {
      await api.post('/api/audit/rules', r);
      toast.success(t('view.audit.ruleCreated'));
    }
    ruleEditor.open = false;
    await loadRules();
  } catch (e) {
    toast.error(e.message || t('view.audit.saveFailed'));
  }
}
async function toggleRule(rule) {
  try {
    await api.put(`/api/audit/rules/${rule.id}`, { ...rule, enabled: !rule.enabled });
    toast.success(t('view.audit.ruleToggled', { state: !rule.enabled ? t('common.on') : t('common.off') }));
    await loadRules();
  } catch (e) {
    toast.error(e.message || t('view.audit.toggleFailed'));
  }
}
async function deleteRule(rule) {
  if (!window.confirm(t('view.audit.ruleConfirmDelete'))) return;
  try {
    await api.delete(`/api/audit/rules/${rule.id}`);
    toast.success(t('view.audit.ruleDeleted'));
    await loadRules();
  } catch (e) {
    toast.error(e.message || t('view.audit.deleteFailed'));
  }
}

// ── 告警历史 ──
const history = ref([]);
const historyLoading = ref(false);
const historyError = ref('');
const historyCols = computed(() => [
  { key: 'time', label: t('view.audit.historyTime'), type: 'time', sortable: true },
  { key: 'rule_name', label: t('view.audit.historyRule'), sortable: true },
  { key: 'event', label: t('view.audit.historyEvent') },
  { key: 'actor', label: t('view.audit.historyActor') },
  { key: 'severity', label: t('view.audit.ruleSeverity'), type: 'badge' },
]);

async function loadHistory() {
  historyLoading.value = true;
  historyError.value = '';
  try {
    const d = await api.get('/api/audit/alerts');
    history.value = d.alerts || d.history || [];
  } catch (e) {
    historyError.value = e.message || t('view.audit.historyUnavailable');
  } finally {
    historyLoading.value = false;
  }
}

// ── 数据加载 ──
async function loadSummary() {
  try {
    const d = await api.get('/api/audit/summary');
    const s = d.summary || d;
    summary.data = {
      total: s.total || s.total_events || 0,
      by_action: s.by_action || {},
      by_actor: s.by_actor || {},
    };
    summary.error = '';
  } catch (e) {
    summary.error = e.message || t('view.audit.summaryUnavailable');
  }
}
async function loadEvents() {
  try {
    const d = await api.get('/api/audit/events');
    events.value = (d.events || []).map((e) => ({
      ...e,
      time: e.time || e.timestamp,
      level: e.level || e.severity || 'info',
      target: e.target || e.resource || '',
    }));
    events.error = '';
  } catch (e) {
    events.error = e.message || t('view.audit.eventsUnavailable');
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadEvents(), loadSummary()]);
  loading.value = false;
  lastUpdated.value = new Date().toLocaleTimeString();
}

// 切换 Tab 时按需加载
function onTabChange(v) {
  if (v === 'rules' && !rules.value.length && !rulesError.value) loadRules();
  if (v === 'history' && !history.value.length && !historyError.value) loadHistory();
}
watch(tab, onTabChange);

onMounted(loadAll);
onUnmounted(() => {
  disconnectLive();
});
</script>

<style scoped>
.audit-view { display: flex; flex-direction: column; gap: var(--sp-4); }
.audit-tabs { margin-right: var(--sp-2); }
.last-updated { font-size: var(--fs-xs); color: var(--text-faint); }

/* ── 统计卡片行 ── */
.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
}
@media (max-width: 900px) { .stat-row { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .stat-row { grid-template-columns: 1fr; } }

/* ── 过滤栏 ── */
.audit-filterbar { margin-bottom: 0; }
.export-group { display: inline-flex; gap: var(--sp-1); margin-left: var(--sp-2); }

/* ── 主体 ── */
.audit-body { display: flex; flex-direction: column; gap: var(--sp-3); }
.audit-error, .audit-loading { padding: var(--sp-4); }

/* ── 图表 ── */
.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-2);
}
.chart-box { height: 280px; position: relative; }
.pie-box { height: 260px; }
.muted { font-size: var(--fs-sm); color: var(--text-muted); }

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
}
@media (max-width: 1100px) { .two-col { grid-template-columns: 1fr; } }

/* ── 热力图 ── */
.heatmap { overflow-x: auto; margin-top: var(--sp-2); }
.heatmap__table { width: 100%; border-collapse: collapse; font-size: var(--fs-xs); }
.heatmap__table th,
.heatmap__table td {
  border: 1px solid var(--border-subtle, var(--border));
  padding: 4px 6px;
  text-align: center;
}
.heatmap__corner,
.heatmap__row-head,
.heatmap__col-head {
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 600;
  white-space: nowrap;
}
.heatmap__row-head { text-align: left; }
.heatmap__cell { font-variant-numeric: tabular-nums; }
.heatmap__cell.hm-0 { background: var(--surface); color: var(--text-faint); }
.heatmap__cell.hm-1 { background: var(--brand-soft); color: var(--text); }
.heatmap__cell.hm-2 { background: color-mix(in srgb, var(--brand) 45%, var(--surface)); color: var(--text); font-weight: 600; }
.heatmap__cell.hm-3 { background: var(--brand); color: var(--brand-contrast); font-weight: 700; }

/* ── 实时监控 ── */
.live-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: var(--r-full);
  background: var(--surface-2);
}
.live-hint.is-live { color: var(--success); background: var(--success-soft); }
.live-tail {
  margin-bottom: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  max-height: 160px;
  overflow-y: auto;
}
.live-tail__head { font-size: var(--fs-xs); color: var(--text-muted); margin-bottom: var(--sp-1); }
.live-tail__list { display: flex; flex-direction: column; gap: 2px; }
.live-tail__item {
  display: grid;
  grid-template-columns: auto auto 1fr auto;
  gap: var(--sp-2);
  align-items: center;
  font-size: var(--fs-xs);
  padding: 2px 0;
}
.live-tail__time { color: var(--text-faint); font-variant-numeric: tabular-nums; }
.live-tail__action { color: var(--text); font-weight: 600; }
.live-tail__actor { color: var(--text-muted); }
.live-tail__empty { font-size: var(--fs-sm); color: var(--text-faint); padding: var(--sp-2); text-align: center; }

/* ── 规则列表 ── */
.rule-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.rule-row {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  gap: var(--sp-3);
  align-items: center;
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  transition: border-color var(--motion) var(--ease);
}
.rule-row:hover { border-color: var(--border-strong); }
.rule-row__main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rule-row__name { font-weight: 600; color: var(--text); }
.rule-row__cond { font-size: var(--fs-xs); color: var(--text-muted); font-family: var(--font-mono, monospace); word-break: break-all; }
.rule-row__state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  font-weight: 600;
}
.rule-row__state.is-on { color: var(--success); }
.rule-row__state.is-off { color: var(--text-faint); }
.rule-row__actions { display: inline-flex; gap: var(--sp-1); }

/* ── 规则表单 ── */
.rule-form { display: flex; flex-direction: column; gap: var(--sp-2); }
.form-label { font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted); }
.form-input {
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
}
.form-input:focus { outline: none; border-color: var(--brand); }
.form-check { display: inline-flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); }

/* ── 通用按钮 ── */
.act-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--sp-1) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  color: var(--text);
  font-size: var(--fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: border-color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.act-btn:hover { border-color: var(--border-strong); background: var(--surface-2); }
.act-btn:disabled { opacity: .5; cursor: not-allowed; }
.act-btn.small { padding: 3px 8px; font-size: var(--fs-xs); }
.act-btn.ghost { background: transparent; }
.act-btn.danger { color: var(--fail); }
.act-btn.danger:hover { background: var(--fail-soft); border-color: var(--fail); }

@keyframes spin { to { transform: rotate(360deg); } }
.spinning { animation: spin 1s linear infinite; }
</style>
