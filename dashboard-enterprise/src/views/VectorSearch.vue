<template>
  <div class="vs-page">
    <PageHeader />

    <Card :title="t('view.vector.query')" icon="search" :marginBottom="16">
      <textarea
        v-model="query"
        class="query-input"
        rows="3"
        :placeholder="t('view.vector.queryPlaceholder')"
        @keydown.ctrl.enter="doSearch"
      ></textarea>
      <div class="query-controls">
        <div class="ctrl">
          <label>{{ t('view.vector.topK') }}</label>
          <input type="number" v-model.number="topK" min="1" max="100" class="num-input" />
        </div>
        <div class="ctrl ctrl--grow" v-if="hasScores">
          <label>{{ t('view.vector.minScore') }} <span class="muted">{{ minScore.toFixed(2) }}</span></label>
          <input type="range" v-model.number="minScore" min="0" max="1" step="0.05" class="range" />
        </div>
        <button class="btn btn--primary" @click="doSearch" :disabled="searching || !query.trim()">
          <AppIcon name="search" :size="15" /> {{ searching ? t('view.vector.searching') : t('common.search') }}
        </button>
      </div>
    </Card>

    <div v-if="searching" class="results-area">
      <div class="result-card" v-for="n in 3" :key="n">
        <Skeleton height="14px" />
        <Skeleton height="38px" />
      </div>
    </div>
    <template v-else>
      <div class="results-area" v-if="filteredResults.length">
        <div class="results-meta">
          <span>{{ filteredResults.length }} result{{ filteredResults.length === 1 ? '' : 's' }}</span>
          <span class="muted" v-if="searchTime">in {{ searchTime }} ms</span>
        </div>
        <div class="result-card" v-for="(r, i) in filteredResults" :key="i">
          <div class="result-head">
            <span class="rank">#{{ i + 1 }}</span>
            <Badge v-if="r.score != null" :tone="scoreTone(r.score)">{{ Math.round(r.score * 100) }}%</Badge>
            <span class="rid">{{ r.id || r.entry_id || r.vector_id || shortId(r) }}</span>
            <Badge v-if="r.agent" tone="info">{{ r.agent }}</Badge>
          </div>
          <div class="result-body">{{ r.content || r.text || r.chunk || r.payload || '—' }}</div>
          <div class="result-foot" v-if="r.tags || r.timestamp">
            <span class="tags" v-if="r.tags">
              <Badge v-for="t in normTags(r.tags)" :key="t" tone="neutral">{{ t }}</Badge>
            </span>
            <span class="muted" v-if="r.timestamp">{{ fmt(r.timestamp) }}</span>
          </div>
        </div>
      </div>
      <EmptyState
        v-else-if="searched"
        icon="search"
        :title="t('view.vector.noMatches')"
        :description="`No vectors matched “${lastQuery}”.`"
      />
    </template>

    <Card :title="t('view.vector.indexStats')" icon="database" :marginBottom="16">
      <div class="stat-grid" v-if="!statsLoading">
        <StatCard :label="t('view.vector.stat.totalEntries')" :value="stats.total_entries ?? 0" icon="database" tone="brand" />
        <StatCard :label="t('view.vector.stat.totalTraces')" :value="stats.total_traces ?? 0" icon="route" tone="info" />
        <StatCard :label="t('view.vector.stat.trajectorySteps')" :value="stats.total_trajectory_steps ?? 0" icon="activity" tone="warn" />
        <StatCard :label="t('view.vector.stat.indexedAgents')" :value="agentCount" icon="bot" tone="success" />
      </div>
      <div class="stat-grid" v-else>
        <Skeleton height="66px" v-for="n in 4" :key="n" />
      </div>
      <p class="inline-error" v-if="statsError">{{ statsError }}</p>
    </Card>

    <Card :title="t('view.vector.indexedVectors')" icon="box" :marginBottom="16">
      <DataTable
        v-if="vectors.length"
        :columns="vectorCols"
        :rows="vectors"
        :loading="vecLoading"
        row-key="id"
        :empty-text="t('view.vector.noIndexedVectors')"
      />
      <EmptyState v-else-if="!vecLoading" icon="database" :title="t('view.vector.noIndexedVectors')" :description="t('view.vector.vectorStoreEmpty')" />
      <Skeleton v-else height="120px" />
    </Card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import Card from '../components/Card.vue';
import PageHeader from '../components/PageHeader.vue';
import StatCard from '../components/StatCard.vue';
import Badge from '../components/Badge.vue';
import DataTable from '../components/DataTable.vue';
import Skeleton from '../components/Skeleton.vue';
import EmptyState from '../components/EmptyState.vue';
import AppIcon from '../components/AppIcon.vue';

const api = useApiStore();
const { t } = useI18n();

const query = ref('');
const topK = ref(10);
const minScore = ref(0);
const searching = ref(false);
const searched = ref(false);
const lastQuery = ref('');
const results = ref([]);
const searchTime = ref(0);

const stats = ref({});
const statsLoading = ref(true);
const statsError = ref('');

const vectors = ref([]);
const vecLoading = ref(true);

const hasScores = computed(() => results.value.some((r) => r.score != null));
const filteredResults = computed(() => {
  if (!hasScores.value) return results.value;
  return results.value.filter((r) => (r.score ?? 0) >= minScore.value);
});
const agentCount = computed(() => {
  const ba = stats.value.by_agent;
  return ba && typeof ba === 'object' ? Object.keys(ba).length : 0;
});

const vectorCols = [
  { key: 'id', label: 'ID' },
  { key: 'agent', label: t('view.vector.col.agent') },
  { key: 'score', label: t('view.vector.col.score'), type: 'num' },
  { key: 'timestamp', label: t('view.vector.col.timestamp') },
];

function normTags(t) {
  if (!t) return [];
  if (Array.isArray(t)) return t.map(String);
  return String(t).split(',').map((s) => s.trim()).filter(Boolean);
}
function shortId(r) {
  const c = r.content || r.text || '';
  return c ? c.slice(0, 8) + '…' : '#' + (results.value.indexOf(r) + 1);
}
function scoreTone(s) {
  if (s >= 0.8) return 'success';
  if (s >= 0.5) return 'info';
  return 'warn';
}
function fmt(ts) {
  if (ts === null) return '—';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

async function doSearch() {
  if (!query.value.trim()) return;
  searching.value = true;
  searched.value = true;
  lastQuery.value = query.value;
  const start = performance.now();
  try {
    const data = await api.get(`/api/vector/search?q=${encodeURIComponent(query.value)}&topk=${topK.value}`);
    results.value = data.results || [];
    searchTime.value = Math.round(performance.now() - start);
  } catch {
    results.value = [];
    searchTime.value = 0;
  }
  searching.value = false;
}

onMounted(async () => {
  try {
    stats.value = await api.get('/api/vector/stats');
  } catch {
    statsError.value = t('view.vector.statsError');
  } finally {
    statsLoading.value = false;
  }
  try {
    const v = await api.get('/api/vector/list');
    vectors.value = v.vectors || [];
  } catch {
    vectors.value = [];
  } finally {
    vecLoading.value = false;
  }
});
</script>

<style scoped>
</style>
