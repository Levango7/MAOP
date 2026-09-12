<template>
  <div class="agentbilling-view">
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
          <span>{{ t('view.agentBilling.refresh') }}</span>
        </button>
      </template>
      <template #content>
        <!-- ── Tab 1: Quota ─────────────────────────────────────── -->
        <div v-show="activeTab === 'quota'">
          <Card :title="t('view.agentBilling.quota.title')" icon="gauge" margin-bottom="var(--sp-4)">
            <div class="quota-layout">
              <!-- Agent selector -->
              <label class="field">
                <span class="field__label">{{ t('view.agentBilling.quota.selectAgent') }}</span>
                <select v-model="selectedAgent" class="field__input field__select" @change="onAgentChange">
                  <option value="">{{ t('view.agentBilling.quota.selectAgentHint') }}</option>
                  <option v-for="a in agents" :key="a" :value="a">{{ a }}</option>
                </select>
              </label>

              <!-- Loading -->
              <div v-if="quotaLoading" class="blk">
                <Skeleton block height="120px" />
                <Skeleton block height="120px" />
                <Skeleton block height="120px" />
              </div>

              <!-- Error -->
              <EmptyState
                v-else-if="quotaError"
                icon="alert-triangle"
                tone="fail"
                :title="t('view.agentBilling.failedLoad')"
                :description="quotaError"
              />

              <!-- No agent selected -->
              <EmptyState
                v-else-if="!selectedAgent"
                icon="gauge"
                :title="t('view.agentBilling.quota.noAgent')"
                :description="t('view.agentBilling.quota.noAgentHint')"
              />

              <!-- Quota buckets -->
              <div v-else class="quota-buckets">
                <div
                  v-for="bucket in quotaBuckets"
                  :key="bucket.key"
                  class="bucket-card"
                >
                  <div class="bucket-card__head">
                    <span class="bucket-card__name">{{ bucket.label }}</span>
                    <Badge :tone="bucket.tone">{{ bucket.statusText }}</Badge>
                  </div>
                  <div class="bucket-card__stats">
                    <div class="bucket-stat">
                      <span class="bucket-stat__label">{{ t('view.agentBilling.quota.total') }}</span>
                      <span class="bucket-stat__value mono">{{ formatTokens(bucket.total) }}</span>
                    </div>
                    <div class="bucket-stat">
                      <span class="bucket-stat__label">{{ t('view.agentBilling.quota.used') }}</span>
                      <span class="bucket-stat__value mono">{{ formatTokens(bucket.used) }}</span>
                    </div>
                    <div class="bucket-stat">
                      <span class="bucket-stat__label">{{ t('view.agentBilling.quota.remaining') }}</span>
                      <span class="bucket-stat__value mono">{{ formatTokens(bucket.remaining) }}</span>
                    </div>
                  </div>
                  <div class="bucket-progress">
                    <div class="bucket-progress__bar">
                      <div
                        class="bucket-progress__fill"
                        :class="'bucket-progress__fill--' + bucket.key"
                        :style="{ width: bucket.percent + '%' }"
                      />
                    </div>
                    <span class="bucket-progress__pct mono">{{ bucket.percentText }}</span>
                  </div>
                </div>
              </div>

              <!-- Quota actions -->
              <div v-if="selectedAgent && !quotaLoading && !quotaError" class="quota-actions">
                <div class="quota-actions__row">
                  <label class="field field--inline">
                    <span class="field__label">{{ t('view.agentBilling.quota.bucket') }}</span>
                    <select v-model="quotaForm.bucket" class="field__input field__select">
                      <option value="free">{{ t('view.agentBilling.quota.free') }}</option>
                      <option value="prepaid">{{ t('view.agentBilling.quota.prepaid') }}</option>
                      <option value="postpaid">{{ t('view.agentBilling.quota.postpaid') }}</option>
                    </select>
                  </label>
                  <label class="field field--inline">
                    <span class="field__label">{{ t('view.agentBilling.quota.amount') }}</span>
                    <input
                      v-model="quotaForm.amount"
                      type="number"
                      min="0"
                      class="field__input"
                      :placeholder="t('view.agentBilling.quota.amount')"
                    />
                  </label>
                </div>
                <div class="quota-actions__buttons">
                  <button
                    class="btn-primary"
                    type="button"
                    :disabled="quotaBusy"
                    @click="doConsume"
                  >
                    <AppIcon name="zap" :size="14" />
                    <span>{{ quotaBusy === 'consume' ? t('view.agentBilling.quota.consuming') : t('view.agentBilling.quota.consume') }}</span>
                  </button>
                  <button
                    class="btn-ghost"
                    type="button"
                    :disabled="quotaBusy"
                    @click="doRefund"
                  >
                    <AppIcon name="rotate-ccw" :size="14" />
                    <span>{{ quotaBusy === 'refund' ? t('view.agentBilling.quota.refunding') : t('view.agentBilling.quota.refund') }}</span>
                  </button>
                  <button
                    class="btn-ghost"
                    type="button"
                    :disabled="quotaBusy"
                    @click="doSetQuota"
                  >
                    <AppIcon name="gauge" :size="14" />
                    <span>{{ quotaBusy === 'set' ? t('view.agentBilling.quota.setting') : t('view.agentBilling.quota.setQuota') }}</span>
                  </button>
                  <button
                    class="btn-ghost btn-ghost--danger"
                    type="button"
                    :disabled="quotaBusy"
                    @click="doReset"
                  >
                    <AppIcon name="refresh" :size="14" />
                    <span>{{ quotaBusy === 'reset' ? t('view.agentBilling.quota.resetting') : t('view.agentBilling.quota.resetQuota') }}</span>
                  </button>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- ── Tab 2: Billing Records ───────────────────────────── -->
        <div v-show="activeTab === 'records'">
          <Card :title="t('view.agentBilling.records.title')" icon="clipboard" margin-bottom="var(--sp-4)">
            <!-- Filters -->
            <div class="records-filters">
              <label class="field field--inline">
                <span class="field__label">{{ t('view.agentBilling.records.filter.agent') }}</span>
                <select v-model="recordsFilter.agent" class="field__input field__select" @change="loadRecords">
                  <option value="">{{ t('view.agentBilling.records.filter.allAgents') }}</option>
                  <option v-for="a in agents" :key="a" :value="a">{{ a }}</option>
                </select>
              </label>
              <label class="field field--inline">
                <span class="field__label">{{ t('view.agentBilling.records.filter.timeRange') }}</span>
                <select v-model="recordsFilter.range" class="field__input field__select" @change="loadRecords">
                  <option value="all">{{ t('view.agentBilling.records.filter.allTime') }}</option>
                  <option value="1h">{{ t('view.agentBilling.records.filter.last1h') }}</option>
                  <option value="24h">{{ t('view.agentBilling.records.filter.last24h') }}</option>
                  <option value="7d">{{ t('view.agentBilling.records.filter.last7d') }}</option>
                  <option value="30d">{{ t('view.agentBilling.records.filter.last30d') }}</option>
                </select>
              </label>
            </div>

            <!-- Summary cards -->
            <div v-if="recordsSummary" class="records-summary">
              <div class="records-summary__card">
                <span class="records-summary__label">{{ t('view.agentBilling.records.totalTokens') }}</span>
                <span class="records-summary__value mono">{{ formatTokens(recordsSummary.total_tokens) }}</span>
              </div>
              <div class="records-summary__card">
                <span class="records-summary__label">{{ t('view.agentBilling.records.totalCost') }}</span>
                <span class="records-summary__value mono">{{ formatCost(recordsSummary.total_cost) }}</span>
              </div>
              <div class="records-summary__card">
                <span class="records-summary__label">{{ t('view.agentBilling.records.totalCalls') }}</span>
                <span class="records-summary__value mono">{{ formatNum(recordsSummary.total_calls) }}</span>
              </div>
            </div>

            <!-- Loading -->
            <div v-if="recordsLoading" class="blk">
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
              <Skeleton block height="44px" />
            </div>

            <!-- Error -->
            <EmptyState
              v-else-if="recordsError"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentBilling.failedLoad')"
              :description="recordsError"
            />

            <!-- Empty -->
            <EmptyState
              v-else-if="!records.length"
              icon="clipboard"
              :title="t('view.agentBilling.records.empty')"
              :description="t('view.agentBilling.records.emptyHint')"
            />

            <!-- Records table -->
            <div v-else class="records-table" role="table" :aria-label="t('view.agentBilling.records.title')">
              <div class="records-table__head" role="row">
                <div class="records-table__th" role="columnheader">{{ t('view.agentBilling.records.col.time') }}</div>
                <div class="records-table__th" role="columnheader">{{ t('view.agentBilling.records.col.agent') }}</div>
                <div class="records-table__th" role="columnheader">{{ t('view.agentBilling.records.col.model') }}</div>
                <div class="records-table__th" role="columnheader">{{ t('view.agentBilling.records.col.billingModel') }}</div>
                <div class="records-table__th records-table__th--num" role="columnheader">{{ t('view.agentBilling.records.col.tokens') }}</div>
                <div class="records-table__th records-table__th--num" role="columnheader">{{ t('view.agentBilling.records.col.calls') }}</div>
                <div class="records-table__th records-table__th--num" role="columnheader">{{ t('view.agentBilling.records.col.cost') }}</div>
                <div class="records-table__th" role="columnheader">{{ t('view.agentBilling.records.col.source') }}</div>
              </div>
              <div v-for="r in pagedRecords" :key="r.timestamp + '-' + r.agent_name + '-' + r.model" class="records-table__row" role="row">
                <div class="records-table__td" role="cell">
                  <span class="mono">{{ formatTime(r.timestamp || r.time) }}</span>
                </div>
                <div class="records-table__td" role="cell">{{ r.agent_name || r.agent || '—' }}</div>
                <div class="records-table__td" role="cell">{{ r.model || '—' }}</div>
                <div class="records-table__td" role="cell">
                  <Badge :tone="billingModelTone(r.billing_model)">{{ billingModelLabel(r.billing_model) }}</Badge>
                </div>
                <div class="records-table__td records-table__td--num" role="cell">
                  <span class="mono">{{ formatNum(r.tokens) }}</span>
                </div>
                <div class="records-table__td records-table__td--num" role="cell">
                  <span class="mono">{{ formatNum(r.calls) }}</span>
                </div>
                <div class="records-table__td records-table__td--num" role="cell">
                  <span class="mono">{{ formatCost(r.cost) }}</span>
                </div>
                <div class="records-table__td" role="cell">
                  <Badge :tone="sourceTone(r.source)">{{ sourceLabel(r.source) }}</Badge>
                </div>
              </div>
            </div>

            <!-- Pagination -->
            <div v-if="showRecordsPager" class="pager" role="navigation">
              <button
                class="pager__btn"
                type="button"
                :disabled="recordsPage === 1"
                :aria-label="t('view.agentBilling.records.prevPage')"
                @click="recordsPage--"
              >‹</button>
              <span class="pager__info">{{ recordsPage }} / {{ recordsTotalPages }}</span>
              <button
                class="pager__btn"
                type="button"
                :disabled="recordsPage === recordsTotalPages"
                :aria-label="t('view.agentBilling.records.nextPage')"
                @click="recordsPage++"
              >›</button>
            </div>
          </Card>
        </div>

        <!-- ── Tab 3: Summary ───────────────────────────────────── -->
        <div v-show="activeTab === 'summary'">
          <Card :title="t('view.agentBilling.summary.title')" icon="dollar" margin-bottom="var(--sp-4)">
            <!-- Loading -->
            <div v-if="summaryLoading" class="blk">
              <Skeleton block height="80px" />
              <Skeleton block height="160px" />
            </div>

            <!-- Error -->
            <EmptyState
              v-else-if="summaryError"
              icon="alert-triangle"
              tone="fail"
              :title="t('view.agentBilling.failedLoad')"
              :description="summaryError"
            />

            <!-- Empty -->
            <EmptyState
              v-else-if="!summaryList.length"
              icon="dollar"
              :title="t('view.agentBilling.summary.empty')"
              :description="t('view.agentBilling.summary.emptyHint')"
            />

            <!-- Summary content -->
            <div v-else class="summary-body">
              <!-- Overall totals -->
              <div class="summary-overview">
                <div class="summary-overview__card">
                  <span class="summary-overview__label">{{ t('view.agentBilling.summary.totalTokens') }}</span>
                  <span class="summary-overview__value mono">{{ formatTokens(summaryTotals.total_tokens) }}</span>
                </div>
                <div class="summary-overview__card">
                  <span class="summary-overview__label">{{ t('view.agentBilling.summary.totalCost') }}</span>
                  <span class="summary-overview__value mono">{{ formatCost(summaryTotals.total_cost) }}</span>
                </div>
                <div class="summary-overview__card">
                  <span class="summary-overview__label">{{ t('view.agentBilling.summary.totalCalls') }}</span>
                  <span class="summary-overview__value mono">{{ formatNum(summaryTotals.total_calls) }}</span>
                </div>
              </div>

              <!-- By agent -->
              <div class="summary-byagent">
                <h4 class="summary-byagent__title">{{ t('view.agentBilling.summary.byAgent') }}</h4>
                <div class="summary-agent-list">
                  <div
                    v-for="s in summaryList"
                    :key="s.agent_name || s.agent"
                    class="summary-agent-card"
                  >
                    <div class="summary-agent-card__head">
                      <span class="summary-agent-card__name">{{ s.agent_name || s.agent || '—' }}</span>
                    </div>
                    <div class="summary-agent-card__stats">
                      <div class="summary-agent-stat">
                        <span class="summary-agent-stat__label">{{ t('view.agentBilling.summary.consumed') }}</span>
                        <span class="summary-agent-stat__value mono">{{ formatTokens(s.total_tokens) }}</span>
                      </div>
                      <div class="summary-agent-stat">
                        <span class="summary-agent-stat__label">{{ t('view.agentBilling.summary.cost') }}</span>
                        <span class="summary-agent-stat__value mono">{{ formatCost(s.total_cost) }}</span>
                      </div>
                      <div class="summary-agent-stat">
                        <span class="summary-agent-stat__label">{{ t('view.agentBilling.summary.calls') }}</span>
                        <span class="summary-agent-stat__value mono">{{ formatNum(s.total_calls) }}</span>
                      </div>
                    </div>
                    <!-- Bucket usage bars -->
                    <div class="summary-agent-buckets">
                      <div
                        v-for="bucket in agentBucketBars(s)"
                        :key="bucket.key"
                        class="summary-bucket"
                      >
                        <div class="summary-bucket__head">
                          <span class="summary-bucket__label">{{ bucket.label }}</span>
                          <span class="summary-bucket__pct mono">{{ bucket.percentText }}</span>
                        </div>
                        <div class="summary-bucket__bar">
                          <div
                            class="summary-bucket__fill"
                            :class="'summary-bucket__fill--' + bucket.key"
                            :style="{ width: bucket.percent + '%' }"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
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
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import ListPageLayout from '../components/ListPageLayout.vue';
import AppIcon from '../components/AppIcon.vue';
import Card from '../components/Card.vue';
import Badge from '../components/Badge.vue';
import Segmented from '../components/Segmented.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const api = useApiStore();
const toast = useToast();
const { t } = useI18n();

// ── Tab state ───────────────────────────────────────────────────
const activeTab = ref('quota');
const tabOptions = [
  { value: 'quota', label: t('view.agentBilling.tab.quota'), icon: 'gauge' },
  { value: 'records', label: t('view.agentBilling.tab.records'), icon: 'clipboard' },
  { value: 'summary', label: t('view.agentBilling.tab.summary'), icon: 'dollar' },
];

// ── Data state ──────────────────────────────────────────────────
const loading = ref(false);
const agents = ref([]);

// Quota tab
const selectedAgent = ref('');
const quotaData = ref(null);
const quotaLoading = ref(false);
const quotaError = ref('');
const quotaBusy = ref(''); // '' | 'set' | 'consume' | 'refund' | 'reset'
const quotaForm = reactive({
  bucket: 'prepaid',
  amount: '',
});

// Records tab
const records = ref([]);
const recordsLoading = ref(false);
const recordsError = ref('');
const recordsSummary = ref(null);
const recordsFilter = reactive({
  agent: '',
  range: 'all',
});
const recordsPage = ref(1);
const RECORDS_PAGE_SIZE = 15;

// Summary tab
const summaryList = ref([]);
const summaryLoading = ref(false);
const summaryError = ref('');

// ── Helpers ─────────────────────────────────────────────────────
const UNLIMITED = -1;

function isUnlimited(v) {
  return v === UNLIMITED || v === null || v === undefined || v === 'unlimited';
}

function formatTokens(v) {
  if (isUnlimited(v)) return t('view.agentBilling.quota.unlimited');
  const n = Number(v);
  if (isNaN(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(Math.round(n));
}

function formatNum(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  return n.toLocaleString();
}

function formatCost(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  return '$' + n.toFixed(4);
}

function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString();
  } catch {
    return String(ts);
  }
}

function billingModelLabel(m) {
  const key = String(m || '').toLowerCase();
  const map = {
    per_token: 'view.agentBilling.billingModel.per_token',
    per_call: 'view.agentBilling.billingModel.per_call',
    subscription: 'view.agentBilling.billingModel.subscription',
    credit: 'view.agentBilling.billingModel.credit',
    blackbox: 'view.agentBilling.billingModel.blackbox',
    free: 'view.agentBilling.billingModel.free',
  };
  return map[key] ? t(map[key]) : (m || '—');
}

function billingModelTone(m) {
  const key = String(m || '').toLowerCase();
  if (key === 'free') return 'success';
  if (key === 'subscription') return 'brand';
  if (key === 'credit') return 'info';
  if (key === 'blackbox') return 'warn';
  return 'neutral';
}

function sourceLabel(s) {
  const key = String(s || '').toLowerCase();
  const map = {
    free: 'view.agentBilling.source.free',
    prepaid: 'view.agentBilling.source.prepaid',
    postpaid: 'view.agentBilling.source.postpaid',
  };
  return map[key] ? t(map[key]) : t('view.agentBilling.source.unknown');
}

function sourceTone(s) {
  const key = String(s || '').toLowerCase();
  if (key === 'free') return 'success';
  if (key === 'prepaid') return 'brand';
  if (key === 'postpaid') return 'info';
  return 'neutral';
}

function bucketPercent(total, used) {
  if (isUnlimited(total)) return null; // unlimited → no bar
  const tn = Number(total);
  const un = Number(used || 0);
  if (!isFinite(tn) || tn <= 0) return 0;
  return Math.min(100, Math.max(0, (un / tn) * 100));
}

function bucketPercentText(total, used) {
  const pct = bucketPercent(total, used);
  if (pct === null) return t('view.agentBilling.quota.unlimited');
  return pct.toFixed(1) + '%';
}

function bucketStatusText(total, used) {
  const pct = bucketPercent(total, used);
  if (pct === null) return t('view.agentBilling.quota.unlimited');
  if (pct >= 100) return '100%';
  if (pct >= 80) return '≥80%';
  return '<80%';
}

function bucketTone(total, used) {
  const pct = bucketPercent(total, used);
  if (pct === null) return 'info';
  if (pct >= 100) return 'fail';
  if (pct >= 80) return 'warn';
  return 'success';
}

// ── Quota computed ──────────────────────────────────────────────
const quotaBuckets = computed(() => {
  const q = quotaData.value;
  if (!q) return [];
  const defs = [
    { key: 'free', label: t('view.agentBilling.quota.free') },
    { key: 'prepaid', label: t('view.agentBilling.quota.prepaid') },
    { key: 'postpaid', label: t('view.agentBilling.quota.postpaid') },
  ];
  return defs.map((d) => {
    const b = (q[d.key] && typeof q[d.key] === 'object') ? q[d.key] : {};
    const total = b.total ?? b.limit ?? UNLIMITED;
    const used = b.used ?? b.consumed ?? 0;
    const remaining = b.remaining ?? (isUnlimited(total) ? UNLIMITED : Math.max(0, total - used));
    const pct = bucketPercent(total, used);
    return {
      key: d.key,
      label: d.label,
      total,
      used,
      remaining,
      percent: pct ?? 0,
      percentText: bucketPercentText(total, used),
      statusText: bucketStatusText(total, used),
      tone: bucketTone(total, used),
    };
  });
});

// ── Records computed ────────────────────────────────────────────
const recordsTotalPages = computed(() =>
  Math.max(1, Math.ceil(records.value.length / RECORDS_PAGE_SIZE)),
);

const showRecordsPager = computed(() => records.value.length > RECORDS_PAGE_SIZE);

const pagedRecords = computed(() => {
  const start = (recordsPage.value - 1) * RECORDS_PAGE_SIZE;
  return records.value.slice(start, start + RECORDS_PAGE_SIZE);
});

// ── Summary computed ────────────────────────────────────────────
const summaryTotals = computed(() => {
  return summaryList.value.reduce(
    (acc, s) => {
      acc.total_tokens += Number(s.total_tokens || 0);
      acc.total_cost += Number(s.total_cost || 0);
      acc.total_calls += Number(s.total_calls || 0);
      return acc;
    },
    { total_tokens: 0, total_cost: 0, total_calls: 0 },
  );
});

function agentBucketBars(s) {
  const defs = [
    { key: 'free', label: t('view.agentBilling.quota.free') },
    { key: 'prepaid', label: t('view.agentBilling.quota.prepaid') },
    { key: 'postpaid', label: t('view.agentBilling.quota.postpaid') },
  ];
  const buckets = (s && s.buckets) || (s && s.quota) || {};
  return defs.map((d) => {
    const b = (buckets[d.key] && typeof buckets[d.key] === 'object') ? buckets[d.key] : {};
    const total = b.total ?? b.limit ?? UNLIMITED;
    const used = b.used ?? b.consumed ?? 0;
    const pct = bucketPercent(total, used);
    return {
      key: d.key,
      label: d.label,
      percent: pct ?? 0,
      percentText: bucketPercentText(total, used),
    };
  });
}

// ── API: Agents list ────────────────────────────────────────────
async function loadAgents() {
  try {
    const data = await api.get('/api/agents');
    // Support { agents: [{name:...}, ...] } or { agents: ["name", ...] } or ["name", ...]
    const list = data.agents || data || [];
    agents.value = list.map((a) => (typeof a === 'string' ? a : (a.name || a.agent_name || ''))).filter(Boolean);
  } catch {
    // Non-fatal — agents list is best-effort; user can still type in other tabs
    agents.value = [];
  }
}

// ── API: Quota ──────────────────────────────────────────────────
async function loadQuota() {
  if (!selectedAgent.value) {
    quotaData.value = null;
    return;
  }
  quotaLoading.value = true;
  quotaError.value = '';
  try {
    const data = await api.get(`/api/quota/${encodeURIComponent(selectedAgent.value)}`);
    quotaData.value = data;
  } catch (err) {
    quotaData.value = null;
    quotaError.value = (err && err.message) || t('view.agentBilling.failedLoad');
  } finally {
    quotaLoading.value = false;
  }
}

function onAgentChange() {

  loadQuota();
}

async function doSetQuota() {
  if (!selectedAgent.value) {
    toast.warn(t('view.agentBilling.quota.validateAgent'));
    return;
  }
  const amount = Number(quotaForm.amount);
  if (isNaN(amount) || amount < 0) {
    toast.warn(t('view.agentBilling.quota.validateAmount'));
    return;
  }
  quotaBusy.value = 'set';
  try {
    await api.put(`/api/quota/${encodeURIComponent(selectedAgent.value)}`, {
      bucket: quotaForm.bucket,
      total: amount,
    });
    toast.success(t('view.agentBilling.quota.setSuccess'));
    await loadQuota();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentBilling.quota.failed'));
  } finally {
    quotaBusy.value = '';
  }
}

async function doConsume() {
  if (!selectedAgent.value) {
    toast.warn(t('view.agentBilling.quota.validateAgent'));
    return;
  }
  const amount = Number(quotaForm.amount);
  if (isNaN(amount) || amount < 0) {
    toast.warn(t('view.agentBilling.quota.validateAmount'));
    return;
  }
  quotaBusy.value = 'consume';
  try {
    await api.post(`/api/quota/${encodeURIComponent(selectedAgent.value)}/consume`, {
      bucket: quotaForm.bucket,
      amount,
    });
    toast.success(t('view.agentBilling.quota.consumeSuccess'));
    await loadQuota();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentBilling.quota.failed'));
  } finally {
    quotaBusy.value = '';
  }
}

async function doRefund() {
  if (!selectedAgent.value) {
    toast.warn(t('view.agentBilling.quota.validateAgent'));
    return;
  }
  const amount = Number(quotaForm.amount);
  if (isNaN(amount) || amount < 0) {
    toast.warn(t('view.agentBilling.quota.validateAmount'));
    return;
  }
  quotaBusy.value = 'refund';
  try {
    await api.post(`/api/quota/${encodeURIComponent(selectedAgent.value)}/refund`, {
      bucket: quotaForm.bucket,
      amount,
    });
    toast.success(t('view.agentBilling.quota.refundSuccess'));
    await loadQuota();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentBilling.quota.failed'));
  } finally {
    quotaBusy.value = '';
  }
}

async function doReset() {
  if (!selectedAgent.value) {
    toast.warn(t('view.agentBilling.quota.validateAgent'));
    return;
  }
  quotaBusy.value = 'reset';
  try {
    await api.post(`/api/quota/${encodeURIComponent(selectedAgent.value)}/reset`, {
      bucket: quotaForm.bucket,
    });
    toast.success(t('view.agentBilling.quota.resetSuccess'));
    await loadQuota();
  } catch (err) {
    toast.error((err && err.message) || t('view.agentBilling.quota.failed'));
  } finally {
    quotaBusy.value = '';
  }
}

// ── API: Records ────────────────────────────────────────────────
function recordsQueryParams() {
  const params = new URLSearchParams();
  if (recordsFilter.agent) params.set('agent', recordsFilter.agent);
  if (recordsFilter.range && recordsFilter.range !== 'all') params.set('range', recordsFilter.range);
  return params.toString();
}

async function loadRecords() {
  recordsLoading.value = true;
  recordsError.value = '';
  recordsPage.value = 1;
  try {
    const qs = recordsQueryParams();
    const url = '/api/billing/records' + (qs ? '?' + qs : '');
    const data = await api.get(url);
    records.value = data.records || data.items || [];
    recordsSummary.value = data.summary || null;
  } catch (err) {
    records.value = [];
    recordsSummary.value = null;
    recordsError.value = (err && err.message) || t('view.agentBilling.failedLoad');
  } finally {
    recordsLoading.value = false;
  }
}

// ── API: Summary ────────────────────────────────────────────────
async function loadSummary() {
  summaryLoading.value = true;
  summaryError.value = '';
  try {
    // If we have agents, fetch per-agent summaries in parallel; otherwise fetch a
    // global summary endpoint as a fallback.
    if (agents.value.length) {
      const results = await Promise.allSettled(
        agents.value.map((a) => api.get(`/api/billing/summary/${encodeURIComponent(a)}`)),
      );
      summaryList.value = results
        .filter((r) => r.status === 'fulfilled' && r.value)
        .map((r) => r.value);
    } else {
      const data = await api.get('/api/billing/summary');
      summaryList.value = data.summaries || data.items || [];
    }
  } catch (err) {
    summaryList.value = [];
    summaryError.value = (err && err.message) || t('view.agentBilling.failedLoad');
  } finally {
    summaryLoading.value = false;
  }
}

// ── Data loading ────────────────────────────────────────────────
async function loadAll() {
  loading.value = true;
  await Promise.allSettled([loadAgents()]);
  // After agents load, refresh tab-specific data
  await Promise.allSettled([
    loadQuota(),
    loadRecords(),
    loadSummary(),
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

/* ── Buttons ───────────────────────────────────────────────────── */
.btn-ghost {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--r-md);
  padding: 7px 12px; font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-ghost:hover { opacity: .9; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost--danger { color: var(--fail); }
.btn-primary {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  background: var(--brand); color: var(--brand-contrast);
  border: none; border-radius: var(--r-md);
  padding: 7px 16px; font-size: var(--fs-sm); font-weight: 600;
  cursor: pointer; transition: opacity var(--motion) var(--ease);
}
.btn-primary:hover { opacity: .9; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }

/* ── Form fields ───────────────────────────────────────────────── */
.field { display: flex; flex-direction: column; gap: var(--sp-1); }
.field--inline { flex-direction: row; align-items: center; gap: var(--sp-2); }
.field__label {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
  white-space: nowrap;
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
.field--inline .field__input { width: auto; min-width: 140px; }

/* ── Quota tab ─────────────────────────────────────────────────── */
.quota-layout { display: flex; flex-direction: column; gap: var(--sp-4); }
.quota-buckets {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
}
.bucket-card {
  display: flex; flex-direction: column; gap: var(--sp-3);
  padding: var(--sp-4); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.bucket-card__head {
  display: flex; align-items: center; justify-content: space-between;
}
.bucket-card__name {
  font-size: var(--fs-md); font-weight: 700; color: var(--text);
}
.bucket-card__stats {
  display: flex; flex-direction: column; gap: var(--sp-2);
}
.bucket-stat {
  display: flex; align-items: center; justify-content: space-between;
}
.bucket-stat__label {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.bucket-stat__value {
  font-size: var(--fs-base); font-weight: 600; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.bucket-progress {
  display: flex; align-items: center; gap: var(--sp-2);
}
.bucket-progress__bar {
  flex: 1; height: 8px; background: var(--surface-2);
  border-radius: var(--r-full); overflow: hidden;
  border: 1px solid var(--border-subtle);
}
.bucket-progress__fill {
  height: 100%; border-radius: var(--r-full);
  transition: width var(--motion) var(--ease);
}
.bucket-progress__fill--free { background: var(--success); }
.bucket-progress__fill--prepaid { background: var(--brand); }
.bucket-progress__fill--postpaid { background: var(--info); }
.bucket-progress__pct {
  font-size: var(--fs-xs); color: var(--text-muted);
  font-variant-numeric: tabular-nums; min-width: 48px; text-align: right;
}

/* ── Quota actions ─────────────────────────────────────────────── */
.quota-actions {
  display: flex; flex-direction: column; gap: var(--sp-3);
  padding-top: var(--sp-3); border-top: 1px solid var(--border-subtle);
}
.quota-actions__row {
  display: flex; gap: var(--sp-3); flex-wrap: wrap;
}
.quota-actions__buttons {
  display: flex; gap: var(--sp-2); flex-wrap: wrap;
}

/* ── Records tab ──────────────────────────────────────────────── */
.records-filters {
  display: flex; gap: var(--sp-3); flex-wrap: wrap;
  margin-bottom: var(--sp-3);
}
.records-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}
.records-summary__card {
  display: flex; flex-direction: column; gap: var(--sp-1);
  padding: var(--sp-3); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.records-summary__label {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.records-summary__value {
  font-size: var(--fs-xl); font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums;
}

/* ── Records table ────────────────────────────────────────────── */
.records-table {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.records-table__head,
.records-table__row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr 80px 70px 90px 1fr;
  align-items: center;
  gap: var(--sp-2);
}
.records-table__head {
  background: var(--surface-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-strong);
}
.records-table__th {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.records-table__th--num { text-align: right; }
.records-table__row {
  padding: var(--sp-3);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--motion) var(--ease);
}
.records-table__row:hover { background: var(--surface-2); }
.records-table__row:last-child { border-bottom: none; }
.records-table__td { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.records-table__td--num { text-align: right; }

/* ── Pagination ───────────────────────────────────────────────── */
.pager {
  display: flex; align-items: center; justify-content: center;
  gap: var(--sp-3); margin-top: var(--sp-3);
}
.pager__btn {
  display: grid; place-items: center;
  width: 32px; height: 32px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text); cursor: pointer;
  font-size: var(--fs-md); font-weight: 600;
  transition: opacity var(--motion) var(--ease);
}
.pager__btn:hover { opacity: .85; }
.pager__btn:disabled { opacity: .4; cursor: not-allowed; }
.pager__info {
  font-size: var(--fs-sm); color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

/* ── Summary tab ──────────────────────────────────────────────── */
.summary-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.summary-overview {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-3);
}
.summary-overview__card {
  display: flex; flex-direction: column; gap: var(--sp-1);
  padding: var(--sp-4); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.summary-overview__label {
  font-size: var(--fs-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
  color: var(--text-muted);
}
.summary-overview__value {
  font-size: var(--fs-2xl); font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums;
}

.summary-byagent__title {
  margin: 0 0 var(--sp-3); font-size: var(--fs-md);
  font-weight: 600; color: var(--text);
}
.summary-agent-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--sp-3);
}
.summary-agent-card {
  display: flex; flex-direction: column; gap: var(--sp-3);
  padding: var(--sp-4); border: 1px solid var(--border-subtle);
  border-radius: var(--r-md); background: var(--surface);
}
.summary-agent-card__head {
  display: flex; align-items: center; justify-content: space-between;
}
.summary-agent-card__name {
  font-size: var(--fs-md); font-weight: 700; color: var(--text);
}
.summary-agent-card__stats {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--sp-2);
}
.summary-agent-stat {
  display: flex; flex-direction: column; gap: 2px;
}
.summary-agent-stat__label {
  font-size: var(--fs-xs); color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .03em;
}
.summary-agent-stat__value {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.summary-agent-buckets {
  display: flex; flex-direction: column; gap: var(--sp-2);
  padding-top: var(--sp-2); border-top: 1px solid var(--border-subtle);
}
.summary-bucket__head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 4px;
}
.summary-bucket__label {
  font-size: var(--fs-xs); color: var(--text-muted); font-weight: 600;
}
.summary-bucket__pct {
  font-size: var(--fs-xs); color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.summary-bucket__bar {
  height: 6px; background: var(--surface-2);
  border-radius: var(--r-full); overflow: hidden;
  border: 1px solid var(--border-subtle);
}
.summary-bucket__fill {
  height: 100%; border-radius: var(--r-full);
  transition: width var(--motion) var(--ease);
}
.summary-bucket__fill--free { background: var(--success); }
.summary-bucket__fill--prepaid { background: var(--brand); }
.summary-bucket__fill--postpaid { background: var(--info); }

/* ── Responsive ────────────────────────────────────────────────── */
@media (max-width: 900px) {
  .quota-buckets { grid-template-columns: 1fr; }
  .records-summary { grid-template-columns: 1fr; }
  .summary-overview { grid-template-columns: 1fr; }
  .records-table__head,
  .records-table__row {
    grid-template-columns: 1fr 1fr 80px 90px;
  }
  .records-table__th:nth-child(3),
  .records-table__td:nth-child(3),
  .records-table__th:nth-child(4),
  .records-table__td:nth-child(4),
  .records-table__th:nth-child(6),
  .records-table__td:nth-child(6),
  .records-table__th:nth-child(8),
  .records-table__td:nth-child(8) { display: none; }
}
@media (max-width: 640px) {
  .records-table__head,
  .records-table__row {
    grid-template-columns: 1fr 80px 90px;
  }
  .records-table__th:nth-child(2),
  .records-table__td:nth-child(2) { display: none; }
  .summary-agent-card__stats { grid-template-columns: 1fr; }
}
</style>