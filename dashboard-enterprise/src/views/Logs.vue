<template>
  <div class="logs-view">
    <PageHeader>
      <Segmented
        :model-value="logType"
        :options="typeOptions"
        size="sm"
        @update:model-value="onTypeChange"
      />
      <button class="btn-ghost" :class="{ 'is-busy': loading }" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('common.refresh') }}</span>
      </button>
    </PageHeader>

    <div class="filter-bar">
      <div class="filter-input-wrap">
        <AppIcon name="search" :size="15" class="filter-input-icon" />
        <input v-model="filter" class="filter-input" :placeholder="t('view.logs.filterPlaceholder')" />
      </div>
      <span v-if="!loading" class="filter-meta">{{ displayLogs.length }} / {{ logs.length }} {{ t('view.logs.lines') }}</span>
    </div>

    <div class="grid-2">
      <Card :title="t('view.logs.logOutput')" icon="scroll" :margin-bottom="0" class="log-card">
        <template #actions>
          <Badge v-if="!loading && logs.length" :tone="errorLogs > 0 ? 'fail' : 'success'">
            {{ errorLogs }} {{ t('view.logs.errors') }}
          </Badge>
        </template>
        <div v-if="loading" class="blk"><Skeleton block height="12px" /><Skeleton block height="12px" /><Skeleton block height="12px" /><Skeleton block height="12px" /></div>
        <EmptyState v-else-if="error" icon="alert-triangle" tone="fail" :title="t('view.logs.failedLoad')" :description="error" />
        <EmptyState v-else-if="!logs.length" icon="scroll" :title="t('view.logs.noLogOutput')" :description="t('view.logs.noLogOutputDesc')" />
        <EmptyState v-else-if="!displayLogs.length" icon="search" :title="t('view.logs.noMatches')" :description="t('view.logs.noMatchesDesc')" />
        <div v-else ref="logContainer" class="log-list">
          <div v-for="(e, i) in displayLogs" :key="i" class="log-line" :class="'lvl-' + e.level">
            <span class="log-line__ts">{{ e.ts || '—' }}</span>
            <Badge :tone="levelTone(e.level)" class="log-line__lvl">{{ e.level }}</Badge>
            <span class="log-line__agent">{{ e.agent }}</span>
            <span class="log-line__msg">{{ e.msg }}</span>
          </div>
        </div>
      </Card>

      <Card :title="t('view.logs.logAnalysis')" icon="activity" :margin-bottom="0" class="analysis-card">
        <div v-if="loading" class="blk">
          <div class="stat-row"><Skeleton height="56px" /><Skeleton height="56px" /><Skeleton height="56px" /><Skeleton height="56px" /></div>
          <Skeleton block height="14px" /><Skeleton block height="14px" />
        </div>
        <EmptyState v-else-if="error" icon="alert-triangle" tone="fail" :title="t('view.logs.analysisUnavailable')" :description="error" />
        <div v-else class="analysis">
          <div class="stat-row">
            <StatCard :label="t('view.logs.stat.total')" :value="formatNum(analysis.total)" icon="clipboard" tone="brand" />
            <StatCard :label="t('view.logs.stat.success')" :value="formatNum(analysis.by_status.success)" icon="check-circle" tone="success" />
            <StatCard :label="t('view.logs.stat.failure')" :value="formatNum(analysis.by_status.failure)" icon="x-circle" tone="fail" />
            <StatCard :label="t('view.logs.stat.timeout')" :value="formatNum(analysis.by_status.timeout)" icon="alert-triangle" tone="warn" />
          </div>

          <div class="section">
            <h4 class="section__title">{{ t('view.logs.byAgent') }}</h4>
            <EmptyState v-if="!agentKeys.length" icon="bot" :title="t('common.noData')" :description="t('view.logs.noDataDesc')" />
            <ul v-else class="dist">
              <li v-for="a in agentKeys" :key="a" class="dist__item">
                <span class="dist__name">{{ a }}</span>
                <div class="dist__track"><div class="dist__fill" :style="{ width: agentPct(a) + '%' }" /></div>
                <span class="dist__count">{{ analysis.by_agent[a] }}</span>
              </li>
            </ul>
          </div>

          <div class="section">
            <h4 class="section__title">{{ t('view.logs.errorPatterns') }}</h4>
            <EmptyState v-if="!analysis.error_patterns.length" icon="check-circle" :title="t('view.logs.noErrors')" :description="t('view.logs.noErrorsDesc')" />
            <ul v-else class="errs">
              <li v-for="(ep, i) in analysis.error_patterns" :key="i" class="errs__item">
                <span class="errs__rank">{{ i + 1 }}</span>
                <span class="errs__msg">{{ ep[0] }}</span>
                <span class="errs__count">{{ ep[1] }}</span>
              </li>
            </ul>
          </div>
        </div>
      </Card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { AppIcon, Card, StatCard, Badge, Segmented, Skeleton, EmptyState, PageHeader } from '../components/index.js';

const api = useApiStore();
const { t } = useI18n();

const logType = ref('all');
const filter = ref('');
const loading = ref(false);
const error = ref(null);
const logs = ref([]);
const logContainer = ref(null);
const analysis = ref({
  total: 0,
  by_status: { success: 0, failure: 0, timeout: 0, other: 0 },
  by_agent: {},
  error_patterns: [],
});

const typeOptions = [
  { value: 'all', label: t('common.all'), icon: 'scroll' },
  { value: 'dashboard', label: t('view.logs.type.dashboard'), icon: 'server' },
  { value: 'delegations', label: t('view.logs.type.delegations'), icon: 'route' },
  { value: 'checker', label: t('view.logs.type.checker'), icon: 'shield' },
];

const displayLogs = computed(() => {
  const q = filter.value.trim().toLowerCase();
  const list = q
    ? logs.value.filter((l) => (l.msg || '').toLowerCase().includes(q) || (l.agent || '').toLowerCase().includes(q))
    : logs.value;
  return list.slice(-500);
});
const errorLogs = computed(() => logs.value.filter((l) => l.level === 'error' || /fail|exception/i.test(l.msg || '')).length);
const agentKeys = computed(() => Object.keys(analysis.value.by_agent || {}));

function levelTone(level) {
  if (level === 'error') return 'fail';
  if (level === 'warn') return 'warn';
  if (level === 'debug') return 'neutral';
  return 'info';
}
function agentPct(a) {
  const max = Math.max(1, ...Object.values(analysis.value.by_agent));
  return Math.round((analysis.value.by_agent[a] / max) * 100);
}
function formatNum(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'K';
  return String(v);
}

function onTypeChange(v) {
  logType.value = v;
  load();
}

async function load() {
  loading.value = true;
  error.value = null;
  const [l, a] = await Promise.allSettled([
    api.get(`/api/logs?type=${logType.value}`),
    api.get('/api/logs/analysis'),
  ]);
  if (l.status === 'fulfilled') logs.value = l.value.logs || [];
  else error.value = (l.reason && l.reason.message) || 'Log stream failed';
  if (a.status === 'fulfilled') {
    const d = a.value || {};
    analysis.value = {
      total: d.total || 0,
      by_status: d.by_status || { success: 0, failure: 0, timeout: 0, other: 0 },
      by_agent: d.by_agent || {},
      error_patterns: d.error_patterns || [],
    };
  } else {
    error.value = error.value || ((a.reason && a.reason.message) || 'Analysis failed');
  }
  loading.value = false;
  await nextTick();
  if (logContainer.value) logContainer.value.scrollTop = logContainer.value.scrollHeight;
}

onMounted(load);
</script>

<style scoped>
</style>
