<template>
  <div class="cost-view">
    <PageHeader>
      <Segmented
        :model-value="period"
        :options="periodOptions"
        size="sm"
        @update:model-value="onPeriodChange"
      />
      <button class="btn-ghost" :class="{ 'is-busy': loading }" @click="load" :disabled="loading">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </PageHeader>

    <div class="stat-row">
      <StatCard
        :label="t('view.cost.stat.totalCost')"
        :value="money(summary.total_cost_usd)"
        icon="dollar"
        tone="brand"
        :loading="loading"
      />
      <StatCard
        :label="t('view.cost.stat.totalTokens')"
        :value="formatNum(summary.total_tokens)"
        icon="box"
        tone="info"
        :loading="loading"
      />
      <StatCard
        :label="t('view.cost.stat.totalCalls')"
        :value="formatNum(summary.total_calls)"
        icon="activity"
        tone="success"
        :loading="loading"
      />
      <StatCard
        :label="t('view.cost.stat.avgLatency')"
        :value="(summary.avg_latency_ms || 0).toFixed(0)"
        unit="ms"
        icon="gauge"
        tone="warn"
        :loading="loading"
      />
    </div>

    <div class="grid-2">
      <Card :title="t('view.cost.budgetStatus')" icon="shield" :marginBottom="0">
        <template #actions>
          <Badge v-if="!loading" :tone="budget.daily_over_budget || budget.monthly_over_budget ? 'fail' : 'success'">
            {{ budget.daily_over_budget || budget.monthly_over_budget ? t('view.cost.overBudget') : t('view.cost.withinBudget') }}
          </Badge>
        </template>
        <div v-if="loading" class="blk">
          <Skeleton block height="14px" />
          <Skeleton block height="14px" />
        </div>
        <div v-else class="budget">
          <div class="budget__row">
            <span class="budget__label">{{ t('view.cost.daily') }}</span>
            <div class="budget__track"><div class="budget__fill" :class="{ 'is-over': budget.daily_over_budget }" :style="{ width: dailyPct + '%' }" /></div>
            <span class="budget__val">{{ money(budget.daily_spent_usd) }} <span class="muted">/ {{ budget.daily_limit_usd ? money(budget.daily_limit_usd) : t('view.cost.noLimit') }}</span></span>
          </div>
          <div class="budget__row">
            <span class="budget__label">{{ t('view.cost.monthly') }}</span>
            <div class="budget__track"><div class="budget__fill" :class="{ 'is-over': budget.monthly_over_budget }" :style="{ width: monthlyPct + '%' }" /></div>
            <span class="budget__val">{{ money(budget.monthly_spent_usd) }} <span class="muted">/ {{ budget.monthly_limit_usd ? money(budget.monthly_limit_usd) : t('view.cost.noLimit') }}</span></span>
          </div>
        </div>
      </Card>

      <Card :title="t('view.cost.costByModel')" icon="cpu" :marginBottom="0">
        <div v-if="loading" class="blk"><Skeleton block height="14px" /><Skeleton block height="14px" /><Skeleton block height="14px" /></div>
        <EmptyState v-else-if="!modelKeys.length" icon="cpu" :title="t('view.cost.noModelSpend')" :description="t('view.cost.noModelSpendDesc')" />
        <ul v-else class="breakdown">
          <li v-for="m in modelKeys" :key="m" class="breakdown__item">
            <span class="breakdown__name">{{ m }}</span>
            <div class="breakdown__track"><div class="breakdown__fill" :style="{ width: modelPct(m) + '%' }" /></div>
            <span class="breakdown__val">{{ money(summary.by_model[m].cost) }}</span>
          </li>
        </ul>
      </Card>
    </div>

    <Card :title="t('view.cost.costByAgent')" icon="bot" marginBottom="16px" class="margin-top">
      <div v-if="loading" class="blk"><Skeleton block height="14px" /><Skeleton block height="14px" /></div>
      <EmptyState v-else-if="!agentKeys.length" icon="bot" :title="t('view.cost.noAgentSpend')" :description="t('view.cost.noAgentSpendDesc')" />
      <div v-else class="agent-grid">
        <div v-for="a in agentKeys" :key="a" class="agent-card">
          <span class="agent-card__name">{{ a || t('view.cost.unknown') }}</span>
          <span class="agent-card__cost">{{ money(summary.by_agent[a].cost) }}</span>
          <span class="agent-card__meta">{{ formatNum(summary.by_agent[a].tokens) }} {{ t('view.cost.tokens') }} · {{ summary.by_agent[a].calls }} {{ t('view.cost.calls') }}</span>
        </div>
      </div>
    </Card>

    <Card :title="t('view.cost.recentEntries')" icon="clipboard" marginBottom="0">
      <template #actions>
        <Badge v-if="!loading" tone="neutral">{{ entries.length }} {{ t('view.cost.shown') }}</Badge>
      </template>
      <DataTable
        v-if="!loading"
        :columns="entryCols"
        :rows="entryRows"
        :empty-text="t('view.cost.noEntries')"
        compact
      />
      <div v-else class="blk"><Skeleton block height="14px" /><Skeleton block height="14px" /><Skeleton block height="14px" /></div>
    </Card>

    <p v-if="error" class="view-error">
      <AppIcon name="alert-triangle" :size="14" /> {{ error }}
    </p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { AppIcon, Card, StatCard, Badge, DataTable, Segmented, Skeleton, EmptyState, PageHeader } from '../components/index.js';

const api = useApiStore();
const { t } = useI18n();

const period = ref('7d');
const loading = ref(false);
const error = ref(null);
const summary = ref({
  total_cost_usd: 0, total_tokens: 0, total_calls: 0, avg_latency_ms: 0,
  by_model: {}, by_agent: {},
});
const budget = ref({});
const entries = ref([]);

const periodOptions = [
  { value: '7d', label: t('view.cost.period7d') },
  { value: '30d', label: t('view.cost.period30d') },
  { value: '90d', label: t('view.cost.period90d') },
];

const modelKeys = computed(() => Object.keys(summary.value.by_model || {}));
const agentKeys = computed(() => Object.keys(summary.value.by_agent || {}));

const dailyPct = computed(() => {
  const lim = budget.value.daily_limit_usd;
  if (!lim) return 0;
  return Math.min(100, ((Number(budget.value.daily_spent_usd) || 0) / lim) * 100);
});
const monthlyPct = computed(() => {
  const lim = budget.value.monthly_limit_usd;
  if (!lim) return 0;
  return Math.min(100, (budget.value.monthly_spent_usd / lim) * 100);
});
function modelPct(m) {
  const info = summary.value.by_model?.[m];
  const total = summary.value.total_cost_usd;
  if (!info || !total) return 0;
  return Math.min(100, (info.cost / total) * 100);
}

const entryCols = [
  { key: 'time', label: t('view.cost.col.time'), type: 'time', width: '180px' },
  { key: 'agent', label: t('view.cost.col.agent'), width: '160px' },
  { key: 'model', label: t('common.model'), width: '160px' },
  { key: 'tokens', label: t('view.cost.col.tokens'), type: 'num', align: 'right' },
  { key: 'cost', label: t('view.cost.col.cost'), type: 'num', align: 'right' },
  { key: 'latency', label: t('common.latency'), align: 'right', width: '100px' },
];
const entryRows = computed(() =>
  entries.value.map((e) => ({
    id: e.id,
    time: e.created_at,
    agent: e.agent || '—',
    model: e.model || '—',
    tokens: formatNum(e.total_tokens),
    cost: '$' + (e.cost_usd || 0).toFixed(6),
    latency: (e.latency_ms ?? 0) + 'ms',
  }))
);

function money(v) {
  const n = Number(v) || 0;
  return '$' + n.toFixed(4);
}
function formatNum(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'K';
  return String(v);
}

function onPeriodChange(v) {
  period.value = v;
  load();
}

async function load() {
  loading.value = true;
  error.value = null;
  const days = { '7d': 7, '30d': 30, '90d': 90 }[period.value] || 7;
  const start = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
  const [s, b, e] = await Promise.allSettled([
    api.get(`/api/cost/summary?start_date=${start}`),
    api.get('/api/cost/budget'),
    api.get(`/api/cost/entries?start_date=${start}&limit=50`),
  ]);
  if (s.status === 'fulfilled') summary.value = s.value.summary || summary.value;
  else error.value = (error.value || '') + 'summary failed. ';
  if (b.status === 'fulfilled') budget.value = b.value.budget || {};
  else error.value = (error.value || '') + 'budget failed. ';
  if (e.status === 'fulfilled') entries.value = e.value.entries || [];
  else error.value = (error.value || '') + 'entries failed. ';
  if (error.value) error.value = error.value.trim();
  loading.value = false;
}

onMounted(load);
</script>

<style scoped>
</style>
