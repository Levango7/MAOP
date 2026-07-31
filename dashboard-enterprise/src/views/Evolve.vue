<template>
  <div class="evolve-page">
    <PageHeader>
      <span class="status-badge" :class="evolving ? 'running' : 'idle'">
        <AppIcon :name="evolving ? 'refresh' : 'check-circle'" :size="13" :class="{ spinning: evolving }" />
        {{ evolving ? t('view.evolve.evolving') : t('view.evolve.idle') }}
      </span>
      <button class="btn-action" @click="triggerEvolve" :disabled="evolving">
        <AppIcon name="sparkles" :size="15" /> {{ t('view.evolve.trigger') }}
      </button>
    </PageHeader>

    <div class="stats-row">
      <StatCard :label="t('view.evolve.totalEvolutions')" :value="totalEvolutions" icon="activity" tone="brand" :loading="loading" />
      <StatCard :label="t('view.evolve.avgSuccessRate')" :value="successRate" unit="%" icon="check-circle" tone="success" :loading="loading" />
      <StatCard :label="t('view.evolve.agentsTracked')" :value="agentsTracked" icon="bot" tone="info" :loading="loading" />
      <StatCard :label="t('view.evolve.bestAgent')" :value="bestAgentLabel" icon="star" tone="warn" :loading="loading" />
    </div>

    <Card :title="t('view.evolve.statsByAgent')" icon="gauge" :marginBottom="16">
      <DataTable
        v-if="byAgent.length"
        :columns="agentCols"
        :rows="byAgent"
        row-key="agent"
        :loading="loading"
        :empty-text="t('view.evolve.noData')"
      />
      <EmptyState v-else-if="!loading" icon="gauge" :title="t('view.evolve.noData')"
                  :description="t('view.evolve.noDataDesc')" />
      <Skeleton v-else height="160px" />
    </Card>

    <div class="two-col">
      <Card :title="t('view.evolve.strategies')" icon="brain" :marginBottom="16">
        <EmptyState icon="brain" :title="t('view.evolve.notAvailable')"
                    :description="t('view.evolve.strategiesNADesc')" />
      </Card>
      <Card :title="t('view.evolve.history')" icon="scroll" :marginBottom="16">
        <EmptyState icon="scroll" :title="t('view.evolve.notAvailable')"
                    :description="t('view.evolve.historyNADesc')" />
      </Card>
    </div>

    <Card :title="t('view.evolve.promptHistory')" icon="clipboard" :marginBottom="16">
      <EmptyState icon="clipboard" :title="t('view.evolve.notAvailable')"
                  :description="t('view.evolve.promptNADesc')" />
    </Card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import { useI18n } from '../i18n';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();
const loading = ref(false);
const evolving = ref(false);
const byAgent = ref([]);

const totalEvolutions = computed(() => byAgent.value.reduce((s, a) => s + (a.total || 0), 0));
const successRate = computed(() => {
  const total = totalEvolutions.value;
  if (!total) return 0;
  const ok = byAgent.value.reduce((s, a) => s + (a.success || 0), 0);
  return Math.round((ok / total) * 1000) / 10;
});
const agentsTracked = computed(() => byAgent.value.length);
const bestAgent = computed(() => {
  if (!byAgent.value.length) return null;
  return byAgent.value.reduce((best, a) => (a.rate > (best?.rate ?? -1) ? a : best), byAgent.value[0]);
});
const bestAgentLabel = computed(() => (bestAgent.value ? `${bestAgent.value.agent} (${bestAgent.value.rate}%)` : '—'));

const agentColDefs = [
  { key: 'agent', label: 'view.evolve.colAgent' },
  { key: 'total', label: 'view.evolve.colTotal', type: 'num' },
  { key: 'success', label: 'view.evolve.colSuccess', type: 'num' },
  { key: 'fail', label: 'view.evolve.colFail', type: 'num' },
  { key: 'rate', label: 'view.evolve.colRate', type: 'num' },
  { key: 'avg_duration_ms', label: 'view.evolve.colAvgMs', type: 'num' },
];
const agentCols = computed(() => agentColDefs.map((c) => ({ ...c, label: t(c.label) })));

function normalize(raw) {
  const data = raw && raw.data ? raw.data : raw;
  const stats = data && data.stats ? data.stats : {};
  const list = Array.isArray(stats.by_agent) ? stats.by_agent : [];
  return list.map((a) => ({
    agent: a.agent,
    total: a.total || 0,
    success: a.success || 0,
    fail: a.fail || 0,
    rate: a.rate != null ? a.rate : 0,
    avg_duration_ms: a.avg_duration_ms || 0,
  }));
}

async function loadStatus() {
  loading.value = true;
  try {
    const raw = await api.get('/api/evolve/status');
    byAgent.value = normalize(raw);
  } catch {
    byAgent.value = [];
  } finally {
    loading.value = false;
  }
}

async function triggerEvolve() {
  evolving.value = true;
  try {
    await api.post('/api/evolve/analyze', { strategies: 'all' });
    toast.success(t('view.evolve.triggered'));
    await loadStatus();
  } catch (e) {
    toast.error(e.message || t('view.evolve.failed'));
  } finally {
    evolving.value = false;
  }
}

onMounted(loadStatus);
</script>

<style scoped>
</style>
