<template>
  <div class="evo-history-page">
    <PageHeader>
      <span class="subtitle muted">{{ t('view.evolutionHistory.subtitle') }}</span>
      <button class="btn-ghost" :class="{ 'is-busy': loading }" @click="loadAll" :disabled="loading">
        <AppIcon name="refresh" :size="15" />
        <span>{{ t('view.evolutionHistory.refresh') }}</span>
      </button>
    </PageHeader>

    <div class="stat-row">
      <StatCard
        :label="t('view.evolutionHistory.stat.totalCycles')"
        :value="cycles.length"
        icon="activity"
        tone="brand"
        :loading="loading"
      />
      <StatCard
        :label="t('view.evolutionHistory.stat.promotions')"
        :value="promotionCount"
        icon="arrow-up"
        tone="success"
        :loading="loading"
      />
      <StatCard
        :label="t('view.evolutionHistory.stat.rollbacks')"
        :value="rollbackCount"
        icon="arrow-down"
        tone="fail"
        :loading="loading"
      />
      <StatCard
        :label="t('view.evolutionHistory.stat.pending')"
        :value="pending.length"
        icon="clock"
        tone="warn"
        :loading="loading"
      />
    </div>

    <!-- 演化循环历史 -->
    <Card :title="t('view.evolutionHistory.cycles.title')" icon="activity" :margin-bottom="16">
      <div class="card-desc muted">{{ t('view.evolutionHistory.cycles.desc') }}</div>
      <DataTable
        v-if="cycles.length"
        :columns="cycleCols"
        :rows="cycleRows"
        row-key="cycle_id"
        :loading="loading"
        :empty-text="t('view.evolutionHistory.noData')"
      />
      <EmptyState v-else-if="!loading" icon="activity"
        :title="t('view.evolutionHistory.noData')"
        :description="t('view.evolutionHistory.noDataDesc')" />
      <Skeleton v-else height="160px" />
    </Card>

    <!-- A/B 实验 -->
    <Card :title="t('view.evolutionHistory.ab.title')" icon="beaker" :margin-bottom="16">
      <div class="card-desc muted">{{ t('view.evolutionHistory.ab.desc') }}</div>
      <DataTable
        v-if="abRows.length"
        :columns="abCols"
        :rows="abRows"
        row-key="name"
        :empty-text="t('view.evolutionHistory.noData')"
      />
      <EmptyState v-else icon="beaker"
        :title="t('view.evolutionHistory.noData')"
        :description="t('view.evolutionHistory.noDataDesc')" />
    </Card>

    <!-- 部署历史 -->
    <Card :title="t('view.evolutionHistory.deploy.title')" icon="rotate-ccw" :margin-bottom="16">
      <div class="card-desc muted">{{ t('view.evolutionHistory.deploy.desc') }}</div>
      <DataTable
        v-if="deployRows.length"
        :columns="deployCols"
        :rows="deployRows"
        row-key="id"
        :empty-text="t('view.evolutionHistory.noData')"
      />
      <EmptyState v-else icon="rotate-ccw"
        :title="t('view.evolutionHistory.noData')"
        :description="t('view.evolutionHistory.noDataDesc')" />
    </Card>

    <!-- 待批准（人工 gate） -->
    <Card :title="t('view.evolutionHistory.pending.title')" icon="clock" :margin-bottom="16">
      <div class="card-desc muted">{{ t('view.evolutionHistory.pending.desc') }}</div>
      <div v-if="pending.length" class="pending-list">
        <div v-for="item in pending" :key="item.cycle_id" class="pending-item">
          <div class="pending-item__main">
            <span class="pending-item__exp">{{ item.experiment }}</span>
            <span class="pending-item__cycle muted">{{ item.cycle_id }}</span>
          </div>
          <div class="pending-item__detail muted">{{ item.detail }}</div>
          <button class="btn-action" @click="approve(item)" :disabled="approving === item.cycle_id">
            <AppIcon name="check-circle" :size="14" />
            {{ t('view.evolutionHistory.approve') }}
          </button>
        </div>
      </div>
      <EmptyState v-else icon="clock"
        :title="t('view.evolutionHistory.noData')"
        :description="t('view.evolutionHistory.pending.desc')" />
    </Card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useToast } from '../composables/useToast.js';
import { useI18n } from '../i18n';
import AppIcon from '../components/AppIcon.vue';
import PageHeader from '../components/PageHeader.vue';
import Card from '../components/Card.vue';
import StatCard from '../components/StatCard.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';

const { t } = useI18n();
const api = useApiStore();
const toast = useToast();

const loading = ref(false);
const cycles = ref([]);
const abExperiments = ref([]);
const deployments = ref([]);
const pending = ref([]);
const approving = ref('');

const promotionCount = computed(() => cycles.value.filter((c) => c.promoted).length);
const rollbackCount = computed(() => cycles.value.filter((c) => c.rolled_back).length);

const cycleCols = computed(() => [
  { key: 'cycle_id', label: t('view.evolutionHistory.colCycle') },
  { key: 'experiment', label: t('view.evolutionHistory.colExperiment') },
  { key: 'decision_label', label: t('view.evolutionHistory.colDecision'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'promoted', label: t('view.evolutionHistory.colPromoted'), type: 'num' },
  { key: 'rolled_back', label: t('view.evolutionHistory.colRolledBack'), type: 'num' },
  { key: 'suggestions_count', label: t('view.evolutionHistory.colSuggestions'), type: 'num' },
  { key: 'duration_s', label: t('view.evolutionHistory.colDuration'), type: 'num' },
  { key: 'time_label', label: t('view.evolutionHistory.colTime') },
]);

const cycleRows = computed(() =>
  cycles.value.map((c) => ({
    ...c,
    promoted: c.promoted ? 1 : 0,
    rolled_back: c.rolled_back ? 1 : 0,
    decision_label: t(`view.evolutionHistory.decision.${c.sprt_decision || 'continue'}`),
    time_label: formatTs(c.started_at),
    duration_s: (c.duration_s || 0).toFixed(2) + 's',
  })),
);

const abCols = computed(() => [
  { key: 'name', label: t('view.evolutionHistory.colExperiment') },
  { key: 'decision_label', label: t('view.evolutionHistory.colDecision'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'samples', label: t('view.evolutionHistory.colSuggestions'), type: 'num' },
  { key: 'success_rate', label: 'Rate %', type: 'num' },
]);

const abRows = computed(() =>
  abExperiments.value.map((e) => ({
    name: e.name,
    decision_label: t(`view.evolutionHistory.decision.${e.decision || 'continue'}`),
    winner: e.winner || '—',
    samples: e.samples || 0,
    success_rate: e.success_rate != null ? (e.success_rate * 100).toFixed(1) : '—',
  })),
);

const deployCols = computed(() => [
  { key: 'experiment', label: t('view.evolutionHistory.colExperiment') },
  { key: 'action', label: t('view.evolutionHistory.colAction'), type: 'badge' },
  { key: 'winner', label: t('view.evolutionHistory.colWinner') },
  { key: 'snapshot_id', label: t('view.evolutionHistory.colSnapshot') },
  { key: 'success_label', label: t('view.evolutionHistory.colSuccess'), type: 'bool-icon' },
  { key: 'time_label', label: t('view.evolutionHistory.colTime') },
]);

const deployRows = computed(() =>
  deployments.value.map((d) => ({
    ...d,
    success_label: d.success,
    time_label: formatTs(d.created_at),
    snapshot_id: d.snapshot_id ? d.snapshot_id.slice(0, 12) : '—',
  })),
);

function formatTs(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts * 1000);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
  } catch {
    return String(ts);
  }
}

async function loadCycles() {
  try {
    const res = await api.get('/api/evolution/cycles?limit=50');
    cycles.value = res.cycles || [];
  } catch {
    cycles.value = [];
  }
}

async function loadAb() {
  try {
    const res = await api.get('/api/evolution/ab/list');
    const names = res.experiments || [];
    abExperiments.value = await Promise.all(
      names.map(async (name) => {
        try {
          const r = await api.get(`/api/evolution/ab/evaluate/${encodeURIComponent(name)}`);
          const sprt = r.result?.sprt || {};
          return {
            name,
            decision: r.result?.decision || 'continue',
            winner: r.result?.winner || '',
            samples: sprt.samples || 0,
            success_rate: sprt.success_rate,
          };
        } catch {
          return { name, decision: 'continue', winner: '', samples: 0, success_rate: null };
        }
      }),
    );
  } catch {
    abExperiments.value = [];
  }
}

async function loadDeployments() {
  try {
    const res = await api.get('/api/evolution/deploy/history');
    deployments.value = res.history || [];
  } catch {
    deployments.value = [];
  }
}

async function loadPending() {
  try {
    const res = await api.get('/api/evolution/pending');
    pending.value = res.pending || [];
  } catch {
    pending.value = [];
  }
}

async function loadAll() {
  loading.value = true;
  await Promise.all([loadCycles(), loadAb(), loadDeployments(), loadPending()]);
  loading.value = false;
}

async function approve(item) {
  approving.value = item.cycle_id;
  try {
    await api.post('/api/evolution/approve', { experiment: item.experiment });
    toast.success(t('view.evolutionHistory.approved'));
    await loadAll();
  } catch (e) {
    toast.error(e.message || 'approve failed');
  } finally {
    approving.value = '';
  }
}

onMounted(() => {
  loadAll();
});
</script>

<style scoped>
.subtitle {
  font-size: 13px;
  margin-right: auto;
  padding-right: 12px;
}
.card-desc {
  font-size: 12px;
  margin-bottom: 10px;
}
.muted { color: var(--text-muted); }

.pending-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pending-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--border-light, #e2e8f0);
  border-radius: 6px;
}
.pending-item__main {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-weight: 600;
}
.pending-item__detail {
  grid-column: 1 / 2;
  font-size: 12px;
}
.btn-action {
  grid-row: 1 / 3;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border, #cbd5e1);
  background: var(--surface, #fff);
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
}
.btn-action:hover:not(:disabled) {
  background: var(--surface-2, #f1f5f9);
}
.btn-action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--border, #cbd5e1);
  background: transparent;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
}
.btn-ghost:hover:not(:disabled) {
  background: var(--surface-2, #f1f5f9);
}
.btn-ghost.is-busy {
  opacity: 0.6;
  cursor: progress;
}
</style>