<template>
  <div class="agentproxy-view">
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
          <span>{{ t('view.agentProxy.adapters.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Adapters ────────────────────────────────────────── -->
        <div v-show="activeTab === 'adapters'">
          <Card :title="t('view.agentProxy.adapters.title')" icon="route" margin-bottom="var(--sp-4)">
            <div v-if="loading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.adapters"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentProxy.adapters.failedLoad')"
              :description="errors.adapters"
            />
            <EmptyState
              v-else-if="!adapters.length"
              icon="route"
              :title="t('view.agentProxy.adapters.empty')"
              :description="t('view.agentProxy.adapters.emptyHint')"
            />
            <div v-else class="ap-table" role="table" :aria-label="t('view.agentProxy.adapters.title')">
              <div class="ap-table__head" role="row">
                <div class="ap-table__th" role="columnheader">{{ t('view.agentProxy.col.name') }}</div>
                <div class="ap-table__th" role="columnheader">{{ t('view.agentProxy.col.status') }}</div>
                <div class="ap-table__th ap-table__th--num" role="columnheader">{{ t('view.agentProxy.col.latency') }}</div>
                <div class="ap-table__th ap-table__th--num" role="columnheader">{{ t('view.agentProxy.col.calls') }}</div>
                <div class="ap-table__th ap-table__th--act" role="columnheader">{{ t('view.agentProxy.col.actions') }}</div>
              </div>
              <div v-for="a in adapters" :key="a.name" class="ap-table__row" role="row">
                <div class="ap-table__td" role="cell">
                  <div class="ap-table__name">{{ a.name }}</div>
                  <div v-if="a.description" class="ap-table__desc muted">{{ a.description }}</div>
                </div>
                <div class="ap-table__td" role="cell">
                  <Badge :tone="adapterStatusTone(a)">
                    {{ adapterStatusLabel(a) }}
                  </Badge>
                </div>
                <div class="ap-table__td ap-table__td--num" role="cell">
                  <span class="mono">{{ formatLatency(a.latency_ms) }}</span>
                </div>
                <div class="ap-table__td ap-table__td--num" role="cell">
                  <span class="mono">{{ a.call_count ?? a.calls ?? 0 }}</span>
                </div>
                <div class="ap-table__td ap-table__td--act" role="cell">
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.agentProxy.action.call')"
                    @click="openCallTab(a)"
                  >
                    <AppIcon name="send" :size="14" />
                  </button>
                  <button
                    class="icon-btn"
                    type="button"
                    :aria-label="t('view.agentProxy.action.sync')"
                    @click="openSync(a)"
                  >
                    <AppIcon name="refresh" :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Health ──────────────────────────────────────────── -->
        <div v-show="activeTab === 'health'">
          <Card :title="t('view.agentProxy.health.title')" icon="activity" margin-bottom="var(--sp-4)">
            <template #actions>
              <button
                class="btn-ghost btn-ghost--sm"
                :disabled="healthRunning"
                @click="runHealth"
              >
                <AppIcon name="activity" :size="14" :class="{ spinning: healthRunning }" />
                <span>{{ healthRunning ? t('view.agentProxy.health.running') : t('view.agentProxy.health.runBtn') }}</span>
              </button>
            </template>
            <div v-if="healthRunning" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>
            <EmptyState
              v-else-if="errors.health"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentProxy.health.failedLoad')"
              :description="errors.health"
            />
            <EmptyState
              v-else-if="!hasHealthData"
              icon="activity"
              :title="t('view.agentProxy.health.empty')"
              :description="t('view.agentProxy.health.emptyHint')"
            />
            <div v-else class="health-body">
              <!-- 概览卡片 -->
              <div class="health-overview">
                <div class="health-card">
                  <span class="health-card__label">{{ t('view.agentProxy.health.healthyCount') }}</span>
                  <span class="health-card__value is-ok">{{ healthSummary.healthy }}</span>
                </div>
                <div class="health-card">
                  <span class="health-card__label">{{ t('view.agentProxy.health.unhealthyCount') }}</span>
                  <span class="health-card__value is-fail">{{ healthSummary.unhealthy }}</span>
                </div>
                <div class="health-card">
                  <span class="health-card__label">{{ t('view.agentProxy.health.totalCount') }}</span>
                  <span class="health-card__value">{{ healthSummary.total }}</span>
                </div>
              </div>

              <!-- 健康状态条形图（内联SVG） -->
              <div class="health-chart">
                <svg
                  class="health-chart__svg"
                  :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="t('view.agentProxy.health.title')"
                >
                  <rect
                    v-for="(bar, i) in healthBars"
                    :key="'bar' + i"
                    :x="bar.x"
                    :y="bar.y"
                    :width="bar.w"
                    :height="bar.h"
                    :class="'health-chart__bar health-chart__bar--' + bar.tone"
                    rx="4"
                  />
                  <text
                    v-for="(bar, i) in healthBars"
                    :key="'lbl' + i"
                    :x="bar.x + bar.w / 2"
                    :y="CHART_H - 4"
                    class="health-chart__label"
                    text-anchor="middle"
                  >{{ bar.label }}</text>
                </svg>
              </div>

              <!-- 按适配器明细 -->
              <div class="health-detail">
                <DataTable
                  :columns="healthCols"
                  :rows="healthRows"
                  :empty-text="t('view.agentProxy.health.empty')"
                  compact
                />
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Call ────────────────────────────────────────────── -->
        <div v-show="activeTab === 'call'">
          <Card :title="t('view.agentProxy.call.title')" icon="send" margin-bottom="var(--sp-4)">
            <p class="call-hint">{{ t('view.agentProxy.call.hint') }}</p>
            <div class="call-form">
              <label class="field">
                <span class="field__label">{{ t('view.agentProxy.call.fieldAdapter') }}</span>
                <select v-model="callForm.adapter" class="field__input field__select">
                  <option value="">{{ t('view.agentProxy.call.fieldAdapterHint') }}</option>
                  <option v-for="a in adapters" :key="a.name" :value="a.name">{{ a.name }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentProxy.call.fieldTask') }}</span>
                <textarea
                  v-model="callForm.task"
                  class="field__textarea"
                  rows="3"
                  :placeholder="t('view.agentProxy.call.fieldTaskHint')"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('view.agentProxy.call.fieldKwargs') }}</span>
                <textarea
                  v-model="callForm.kwargs"
                  class="field__textarea mono"
                  rows="4"
                  :placeholder="t('view.agentProxy.call.fieldKwargsHint')"
                />
              </label>
              <div class="call-form__actions">
                <button
                  class="btn-primary"
                  type="button"
                  :disabled="calling"
                  @click="doCall"
                >
                  <AppIcon name="send" :size="14" />
                  <span>{{ calling ? t('view.agentProxy.call.sending') : t('view.agentProxy.call.submit') }}</span>
                </button>
              </div>
              <div v-if="callResult" class="call-result">
                <span class="field__label">{{ t('view.agentProxy.call.result') }}</span>
                <pre class="call-result__body mono">{{ formatResult(callResult) }}</pre>
              </div>
              <p v-if="callError" class="call-err">{{ callError }}</p>
            </div>
          </Card>
        </div>
      </template>
    </ListPageLayout>

    <!-- ── Sync Config Modal ────────────────────────────────── -->
    <Teleport to="body">
      <div
        v-if="showSyncModal"
        v-modal-a11y
        class="modal-overlay"
        @click.self="closeSync"
        @modal:escape="closeSync"
      >
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal__head">
            <h3>{{ t('view.agentProxy.sync.title') }}</h3>
            <button class="modal__x" type="button" :aria-label="t('common.close')" @click="closeSync">
              <AppIcon name="x" :size="16" />
            </button>
          </div>
          <div class="modal__body">
            <p v-if="syncTarget" class="modal__target mono">{{ syncTarget.name }}</p>
            <p class="modal__hint">{{ t('view.agentProxy.sync.hint') }}</p>
            <textarea
              v-model="syncConfigText"
              class="field__textarea mono"
              rows="8"
              spellcheck="false"
              :placeholder="t('view.agentProxy.sync.fieldConfigHint')"
            />
            <p v-if="syncError" class="modal__err">{{ syncError }}</p>
          </div>
          <div class="modal__foot">
            <button class="btn-ghost" type="button" @click="closeSync">{{ t('common.cancel') }}</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="syncing"
              @click="submitSync"
            >
              {{ syncing ? t('view.agentProxy.sync.syncing') : t('view.agentProxy.sync.submit') }}
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
import { useToast } from '../composables/useToast.js';
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
const toast = useToast();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('adapters');
const tabOptions = [
  { value: 'adapters', label: t('view.agentProxy.tab.adapters'), icon: 'route' },
  { value: 'health', label: t('view.agentProxy.tab.health'), icon: 'activity' },
  { value: 'call', label: t('view.agentProxy.tab.call'), icon: 'send' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const errors = ref({ adapters: null, health: null });
const adapters = ref([]);
const healthData = ref({});
const healthRunning = ref(false);

// ── Helpers ─────────────────────────────────────────────────────
function normalizeStatus(s) {
  return String(s || '').toLowerCase();
}

function adapterStatusLabel(a) {
  const n = normalizeStatus(a.status || a.state);
  if (n === 'healthy' || n === 'ok' || n === 'online' || n === 'up') return t('view.agentProxy.status.healthy');
  if (n === 'unhealthy' || n === 'fail' || n === 'error' || n === 'down' || n === 'offline') return t('view.agentProxy.status.unhealthy');
  return t('view.agentProxy.status.unknown');
}

function adapterStatusTone(a) {
  const n = normalizeStatus(a.status || a.state);
  if (n === 'healthy' || n === 'ok' || n === 'online' || n === 'up') return 'success';
  if (n === 'unhealthy' || n === 'fail' || n === 'error' || n === 'down' || n === 'offline') return 'fail';
  return 'neutral';
}

function formatLatency(ms) {
  if (ms == null) return '—';
  const n = Number(ms);
  if (isNaN(n)) return '—';
  if (n < 1000) return `${Math.round(n)}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

function formatResult(r) {
  if (r == null) return '';
  if (typeof r === 'string') return r;
  try { return JSON.stringify(r, null, 2); } catch { return String(r); }
}

// ── Health summary ──────────────────────────────────────────────
const healthEntries = computed(() => {
  const h = healthData.value;
  if (!h || typeof h !== 'object') return [];
  // health 可能是 { adapter_name: { healthy: true, ... } } 或数组
  if (Array.isArray(h)) {
    return h.map((item) => ({
      name: item.name || item.adapter || '—',
      healthy: item.healthy !== false && normalizeStatus(item.status) !== 'unhealthy',
      latency_ms: item.latency_ms ?? item.latency ?? null,
      last_check: item.last_check ?? item.checked_at ?? null,
    }));
  }
  return Object.entries(h).map(([name, val]) => ({
    name,
    healthy: val && (val.healthy !== false) && normalizeStatus(val.status) !== 'unhealthy',
    latency_ms: (val && (val.latency_ms ?? val.latency)) ?? null,
    last_check: (val && (val.last_check ?? val.checked_at)) ?? null,
  }));
});

const hasHealthData = computed(() => healthEntries.value.length > 0);

const healthSummary = computed(() => {
  const entries = healthEntries.value;
  const healthy = entries.filter((e) => e.healthy).length;
  return {
    healthy,
    unhealthy: entries.length - healthy,
    total: entries.length,
  };
});

// ── Health bar chart (inline SVG) ───────────────────────────────
const CHART_W = 600;
const CHART_H = 160;
const CHART_PAD = 16;

const healthBars = computed(() => {
  const entries = healthEntries.value;
  if (!entries.length) return [];
  const n = entries.length;
  const gap = 8;
  const totalGap = gap * (n + 1);
  const barW = Math.max(8, (CHART_W - totalGap) / n);
  const maxH = CHART_H - CHART_PAD * 2 - 20; // 留出标签空间
  return entries.map((e, i) => ({
    x: gap + i * (barW + gap),
    y: CHART_PAD + maxH - (e.healthy ? maxH : maxH * 0.3),
    w: barW,
    h: e.healthy ? maxH : maxH * 0.3,
    tone: e.healthy ? 'ok' : 'fail',
    label: e.name.length > 8 ? e.name.slice(0, 7) + '…' : e.name,
  }));
});

// ── Health table ────────────────────────────────────────────────
const healthCols = computed(() => [
  { key: 'name', label: t('view.agentProxy.col.name'), width: '40%' },
  { key: 'status', label: t('view.agentProxy.col.status'), type: 'badge', width: '20%' },
  { key: 'latency', label: t('view.agentProxy.col.latency'), align: 'right', width: '20%' },
  { key: 'last_check', label: t('view.agentProxy.health.lastCheck'), type: 'time', align: 'right', width: '20%' },
]);

const healthRows = computed(() =>
  healthEntries.value.map((e) => ({
    name: e.name,
    status: e.healthy ? t('view.agentProxy.status.healthy') : t('view.agentProxy.status.unhealthy'),
    latency: e.latency_ms != null ? formatLatency(e.latency_ms) : '—',
    last_check: e.last_check || null,
  })),
);

// ── Call form ───────────────────────────────────────────────────
const calling = ref(false);
const callResult = ref(null);
const callError = ref('');
const callForm = reactive({
  adapter: '',
  task: '',
  kwargs: '{}',
});

function openCallTab(a) {
  callForm.adapter = a.name;
  activeTab.value = 'call';
}

async function doCall() {
  callError.value = '';
  const adapter = callForm.adapter.trim();
  const task = callForm.task.trim();
  if (!adapter) {
    toast.warn(t('view.agentProxy.call.validateAdapter'));
    return;
  }
  if (!task) {
    toast.warn(t('view.agentProxy.call.validateTask'));
    return;
  }
  let kwargs = {};
  if (callForm.kwargs.trim()) {
    try {
      kwargs = JSON.parse(callForm.kwargs);
    } catch {
      toast.warn(t('view.agentProxy.call.validateKwargs'));
      return;
    }
  }
  calling.value = true;
  callResult.value = null;
  try {
    const data = await api.post('/api/bridge/call', { adapter, task, kwargs });
    callResult.value = data.result ?? data;
    toast.success(t('view.agentProxy.call.success'));
  } catch (err) {
    const msg = (err && err.message) || t('view.agentProxy.call.failed');
    callError.value = msg;
    toast.error(msg);
  } finally {
    calling.value = false;
  }
}

// ── Sync config modal ───────────────────────────────────────────
const showSyncModal = ref(false);
const syncTarget = ref(null);
const syncConfigText = ref('{}');
const syncError = ref('');
const syncing = ref(false);

function openSync(a) {
  syncTarget.value = a;
  try {
    syncConfigText.value = JSON.stringify(a.config || {}, null, 2);
  } catch {
    syncConfigText.value = '{}';
  }
  syncError.value = '';
  showSyncModal.value = true;
}

function closeSync() {
  showSyncModal.value = false;
  syncTarget.value = null;
  syncError.value = '';
}

async function submitSync() {
  if (!syncTarget.value) return;
  let parsed;
  try {
    parsed = JSON.parse(syncConfigText.value || '{}');
    syncError.value = '';
  } catch {
    syncError.value = t('view.agentProxy.sync.validateConfig');
    return;
  }
  syncing.value = true;
  try {
    await api.post('/api/bridge/sync-config', {
      adapter: syncTarget.value.name,
      config: parsed,
    });
    toast.success(t('view.agentProxy.sync.success'));
    showSyncModal.value = false;
    syncTarget.value = null;
  } catch (err) {
    toast.error((err && err.message) || t('view.agentProxy.sync.failed'));
  } finally {
    syncing.value = false;
  }
}

// ── Health check ────────────────────────────────────────────────
async function runHealth() {
  healthRunning.value = true;
  errors.value = { ...errors.value, health: null };
  try {
    const data = await api.get('/api/bridge/health');
    healthData.value = data.health || {};
  } catch (err) {
    healthData.value = {};
    errors.value = { ...errors.value, health: (err && err.message) || t('view.agentProxy.health.failedLoad') };
    toast.error((err && err.message) || t('view.agentProxy.health.failedLoad'));
  } finally {
    healthRunning.value = false;
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadAdapters() {
  try {
    const data = await api.get('/api/bridge/adapters');
    adapters.value = data.adapters || [];
    errors.value = { ...errors.value, adapters: null };
  } catch (err) {
    adapters.value = [];
    errors.value = { ...errors.value, adapters: (err && err.message) || t('view.agentProxy.adapters.failedLoad') };
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadAdapters()]);
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

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px var(--sp-3); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost--sm { padding: var(--sp-1) var(--sp-2); font-size: var(--fs-xs); }
.btn-primary {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: 7px var(--sp-4); font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.icon-btn {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), border-color var(--motion) var(--ease);
}
.icon-btn:hover { color: var(--text); border-color: var(--border-strong); }
.icon-btn:disabled { opacity: .5; cursor: not-allowed; }
.spinning { animation: maop-spin 1s linear infinite; }
@keyframes maop-spin { to { transform: rotate(360deg); } }

/* ── Adapter table ─────────────────────────────────────────────── */
.ap-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.ap-table__head,
.ap-table__row {
  display: grid;
  grid-template-columns: 1fr 120px 100px 100px 100px;
  align-items: center;
  gap: var(--sp-2);
}
.ap-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.ap-table__th {
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .03em;
  color: var(--text-muted);
}
.ap-table__th--num { text-align: right; }
.ap-table__th--act { text-align: right; }
.ap-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.ap-table__row:hover { background: var(--surface-2); }
.ap-table__row:last-child { border-bottom: none; }
.ap-table__td { min-width: 0; }
.ap-table__name { font-weight: 600; color: var(--text); font-size: var(--fs-sm); }
.ap-table__desc { font-size: var(--fs-xs); margin-top: 2px; }
.ap-table__td--num { text-align: right; }
.ap-table__td--act {
  display: flex; gap: var(--sp-1); justify-content: flex-end; flex-wrap: wrap;
}

/* ── Health body ───────────────────────────────────────────────── */
.health-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.health-overview {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
}
.health-card {
  display: flex; flex-direction: column; gap: var(--sp-1);
  padding: var(--sp-3); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.health-card__label {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.health-card__value {
  font-size: var(--fs-2xl); font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.health-card__value.is-ok { color: var(--success); }
.health-card__value.is-fail { color: var(--fail); }

/* ── Health chart ──────────────────────────────────────────────── */
.health-chart {
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  padding: var(--sp-2);
  background: var(--surface);
}
.health-chart__svg {
  width: 100%; height: 160px; display: block;
}
.health-chart__bar { transition: opacity var(--motion) var(--ease); }
.health-chart__bar--ok { fill: var(--success); }
.health-chart__bar--fail { fill: var(--fail); }
.health-chart__label {
  font-size: var(--fs-2xs); fill: var(--text-muted);
  font-family: var(--font-mono);
}

/* ── Health detail ─────────────────────────────────────────────── */
.health-detail { margin-top: var(--sp-2); }

/* ── Call form ─────────────────────────────────────────────────── */
.call-hint {
  font-size: var(--fs-sm); color: var(--text-muted);
  margin: 0 0 var(--sp-3); line-height: 1.5;
}
.call-form { display: flex; flex-direction: column; gap: var(--sp-3); }
.call-form__actions { display: flex; justify-content: flex-end; }
.call-result { display: flex; flex-direction: column; gap: var(--sp-1); }
.call-result__body {
  background: var(--surface-2); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3);
  max-height: 280px; overflow: auto; white-space: pre-wrap;
  font-size: var(--fs-xs); color: var(--text);
}
.call-err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }

/* ── Form fields ───────────────────────────────────────────────── */
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field__label {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
}
.field__input {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  transition: border-color var(--motion) var(--ease);
}
.field__input:focus { outline: none; border-color: var(--brand); }
.field__select {
  appearance: none; -webkit-appearance: none;
  background-image: var(--icon-chevron);
  background-repeat: no-repeat;
  background-position: right var(--sp-2) center;
  padding-right: var(--sp-7);
  cursor: pointer;
}
.field__textarea {
  font-family: inherit; font-size: var(--fs-base);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 10px; width: 100%;
  resize: vertical; min-height: 80px;
  transition: border-color var(--motion) var(--ease);
}
.field__textarea:focus { outline: none; border-color: var(--brand); }

/* ── Modal ─────────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed; inset: 0; background: var(--overlay-scrim);
  display: flex; align-items: center; justify-content: center;
  z-index: var(--z-modal);
}
.modal {
  position: relative;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); padding: var(--sp-6);
  width: calc(100% - 32px); max-width: 560px; max-height: 88vh; overflow-y: auto;
  box-shadow: var(--shadow-lg);
}
.modal__head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.modal__head h3 { margin: 0; font-size: var(--fs-lg); color: var(--text); }
.modal__x {
  display: grid; place-items: center;
  width: 28px; height: 28px;
  background: transparent; border: none; border-radius: var(--r-sm);
  color: var(--text-muted); cursor: pointer;
  transition: color var(--motion) var(--ease), background var(--motion) var(--ease);
}
.modal__x:hover { color: var(--text); background: var(--surface-2); }
.modal__body { display: flex; flex-direction: column; gap: var(--sp-2); }
.modal__target { color: var(--text-muted); font-size: var(--fs-sm); }
.modal__hint { color: var(--text-muted); font-size: var(--fs-sm); margin: 0; }
.modal__err { color: var(--fail); font-size: var(--fs-sm); margin: 0; }
.modal__foot {
  display: flex; justify-content: flex-end; gap: var(--sp-2);
  margin-top: var(--sp-4);
}

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .ap-table__head,
  .ap-table__row {
    grid-template-columns: 1fr 120px 100px;
  }
  .ap-table__th--num:nth-child(3),
  .ap-table__td--num:nth-child(3) { display: none; }
  .health-overview { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .ap-table__head,
  .ap-table__row {
    grid-template-columns: 1fr 100px;
  }
  .ap-table__th--num,
  .ap-table__td--num { display: none; }
  .ap-table__th--act,
  .ap-table__td--act { display: none; }
}
</style>
