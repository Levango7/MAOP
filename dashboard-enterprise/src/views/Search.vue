<template>
  <div class="search-view">
    <PageHeader />

    <section v-if="!statsError" class="stat-row">
      <StatCard :label="t('view.search.stat.memoryEntries')" :value="memStats.total_entries" icon="brain" tone="brand" :loading="statsLoading" />
      <StatCard :label="t('view.search.stat.vectors')" :value="vecStats.total_entries" icon="database" tone="info" :loading="statsLoading" />
      <StatCard :label="t('view.search.stat.graphNodes')" :value="graphStats.nodes" icon="network" tone="warn" :loading="statsLoading" />
      <StatCard :label="t('view.search.stat.graphEdges')" :value="graphStats.edges" icon="link" tone="success" :loading="statsLoading" />
    </section>

    <Card icon="search" :margin-bottom="16">
      <Segmented v-model="activeTab" :options="tabOptions" />
      <div class="search-bar">
        <div class="input-wrap">
          <input
v-model="query" class="search-input" :placeholder="placeholder"
            :disabled="searching" @keydown.enter="doSearch" />
          <AppIcon name="search" :size="16" class="input-icon-right" />
        </div>
        <button class="btn-primary" :disabled="searching || (activeTab !== 'graph' && !query.trim())" @click="doSearch">
          <AppIcon v-if="searching" name="refresh" :size="14" /> {{ searching ? t('view.search.searching') : t('common.search') }}
        </button>
      </div>

      <div class="opts">
        <template v-if="activeTab === 'memory'">
          <select v-model="memoryType" class="opt">
            <option value="all">{{ t('view.search.opt.allTypes') }}</option><option value="episode">{{ t('view.search.opt.episode') }}</option>
            <option value="skill">{{ t('view.search.opt.skill') }}</option><option value="config">{{ t('view.search.opt.config') }}</option>
            <option value="error">{{ t('view.search.opt.error') }}</option>
          </select>
          <select v-model.number="memoryTopK" class="opt">
            <option :value="10">{{ t('view.search.opt.top10') }}</option><option :value="20">{{ t('view.search.opt.top20') }}</option><option :value="50">{{ t('view.search.opt.top50') }}</option>
          </select>
        </template>
        <template v-if="activeTab === 'vector'">
          <select v-model.number="vectorTopK" class="opt">
            <option :value="5">{{ t('view.search.opt.vTop5') }}</option><option :value="10">{{ t('view.search.opt.vTop10') }}</option><option :value="20">{{ t('view.search.opt.vTop20') }}</option>
          </select>
        </template>
        <template v-if="activeTab === 'graph'">
          <select v-model="graphMode" class="opt">
            <option value="neighbors">{{ t('view.search.opt.neighbors') }}</option><option value="nodes">{{ t('view.search.opt.nodes') }}</option><option value="edges">{{ t('view.search.opt.edges') }}</option>
          </select>
        </template>
        <template v-if="activeTab === 'log'">
          <select v-model="logType" class="opt">
            <option value="all">{{ t('common.all') }}</option><option value="dashboard">{{ t('view.search.opt.dashboard') }}</option>
            <option value="delegations">{{ t('view.search.opt.delegations') }}</option><option value="checker">{{ t('view.search.opt.checker') }}</option>
          </select>
        </template>
        <template v-if="activeTab === 'agent'">
          <select v-model="agentStatus" class="opt">
            <option value="all">{{ t('view.search.opt.allStatus') }}</option><option value="available">{{ t('view.search.opt.cliAvailable') }}</option>
            <option value="unavailable">{{ t('view.search.opt.cliMissing') }}</option>
          </select>
        </template>
      </div>
    </Card>

    <Card
icon="clipboard" :title="resultTitle" :margin-bottom="0"
      :subtitle="searched && !searching ? `${resultRows.length} result(s)${searchTime ? ' in ' + searchTime + 'ms' : ''}` : ''">
      <div v-if="searchError" class="err"><EmptyState icon="alert-triangle" :title="t('view.search.searchFailed')" :description="searchError" /></div>
      <Skeleton v-else-if="searching" :lines="6" block />
      <DataTable v-else-if="resultRows.length" :columns="resultColumns" :rows="resultRows" :loading="false" :empty-text="t('view.search.noResults')" />
      <EmptyState v-else-if="searched" icon="search" :title="t('view.search.noResults')" :description="`No matches for “${lastQuery}”.`" />
      <EmptyState v-else icon="search" :title="t('view.search.runSearch')" :description="t('view.search.runSearchHint')" />
    </Card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useApiStore } from '../stores/api.js';
import { useI18n } from '../i18n';
import { Card, StatCard, DataTable, Segmented, Skeleton, EmptyState, AppIcon, PageHeader } from '../components/index.js';

const api = useApiStore();
const { t } = useI18n();
const enc = (s) => encodeURIComponent(s);

const activeTab = ref('memory');
const query = ref('');
const searching = ref(false);
const searched = ref(false);
const lastQuery = ref('');
const searchTime = ref(0);
const searchError = ref('');
const results = ref([]);

const memoryType = ref('all');
const memoryTopK = ref(10);
const vectorTopK = ref(10);
const graphMode = ref('neighbors');
const logType = ref('all');
const agentStatus = ref('all');

const tabOptions = [
  { value: 'memory', label: t('view.search.tab.memory'), icon: 'brain' },
  { value: 'vector', label: t('view.search.tab.vector'), icon: 'database' },
  { value: 'graph', label: t('view.search.tab.graph'), icon: 'network' },
  { value: 'log', label: t('view.search.tab.log'), icon: 'scroll' },
  { value: 'agent', label: t('view.search.tab.agent'), icon: 'bot' },
];

const placeholder = computed(() => ({
  memory: t('view.search.placeholder.memory'),
  vector: t('view.search.placeholder.vector'),
  graph: t('view.search.placeholder.graph'),
  log: t('view.search.placeholder.log'),
  agent: t('view.search.placeholder.agent'),
}[activeTab.value] || t('view.search.placeholder.default')));

const resultTitle = computed(() => ({
  memory: t('view.search.resultTitle.memory'), vector: t('view.search.resultTitle.vector'), graph: t('view.search.resultTitle.graph'),
  log: t('view.search.resultTitle.log'), agent: t('view.search.resultTitle.agent'),
}[activeTab.value] || t('view.search.resultTitle.default')));

/* ---- index stats ---- */
const statsLoading = ref(true);
const statsError = ref('');
const memStats = reactive({ total_entries: 0 });
const vecStats = reactive({ total_entries: 0 });
const graphStats = reactive({ nodes: 0, edges: 0 });

async function loadStats() {
  statsLoading.value = true;
  const get = async (url, sink, keys) => {
    try { const d = await api.get(url); keys.forEach(k => { if (d[k] !== null && d[k] !== undefined) sink[k] = d[k]; }); }
    catch (e) { console.warn('[search] stats load failed for', url, e && e.message); }
  };
  await Promise.all([
    get('/api/memory/stats', memStats, ['total_entries']),
    get('/api/vector/stats', vecStats, ['total_entries']),
    get('/api/graph/stats', graphStats, ['nodes', 'edges']),
  ]);
  statsLoading.value = false;
}

/* ---- columns / rows per tab ---- */
const resultColumns = computed(() => {
  switch (activeTab.value) {
    case 'memory': return [
      { key: 'id', label: 'ID' }, { key: 'agent', label: t('view.search.col.agent') },
      { key: 'task', label: t('view.search.col.task') }, { key: 'tags', label: t('view.search.col.tags') },
      { key: 'score', label: t('view.search.col.score'), align: 'right', type: 'num' },
    ];
    case 'vector': return [
      { key: 'id', label: 'ID' }, { key: 'score', label: t('view.search.col.score'), align: 'right', type: 'num' },
    ];
    case 'graph':
      if (graphMode.value === 'nodes') return [{ key: 'id', label: 'ID' }, { key: 'label', label: t('view.search.col.label') }, { key: 'weight', label: t('view.search.col.weight'), align: 'right', type: 'num' }];
      if (graphMode.value === 'edges') return [{ key: 'source', label: t('view.search.col.source') }, { key: 'target', label: t('view.search.col.target') }];
      return [{ key: 'name', label: t('view.search.col.node') }, { key: 'detail', label: t('common.details') }];
    case 'log': return [
      { key: 'time', label: t('view.search.col.time'), type: 'time' }, { key: 'level', label: t('view.search.col.level'), type: 'badge' },
      { key: 'agent', label: t('view.search.col.agent') }, { key: 'msg', label: t('view.search.col.message') },
    ];
    case 'agent': return [
      { key: 'name', label: t('common.name') }, { key: 'model', label: t('common.model') }, { key: 'driver', label: t('common.driver') },
      { key: 'capabilities', label: t('common.capabilities') }, { key: 'cli_available', label: 'CLI', type: 'badge' },
    ];
  }
  return [];
});

const resultRows = computed(() => {
  const tab = activeTab.value;
  const r = results.value || [];
  if (tab === 'graph' && graphMode.value === 'neighbors') {
    return r.map(n => typeof n === 'string'
      ? { name: n, detail: '' }
      : { name: n.id || n.name || n.label || '—', detail: n.weight !== null && n.weight !== undefined ? ('weight ' + n.weight) : (n.detail || '') });
  }
  if (tab === 'graph' && graphMode.value === 'edges') {
    return r.map(e => Array.isArray(e)
      ? { source: e[0], target: e[1] }
      : { source: e.source || e.from || '—', target: e.target || e.to || '—' });
  }
  if (tab === 'agent') {
    return r.map(a => ({
      name: a.name, model: a.model || '—', driver: a.driver || '—',
      capabilities: (a.capabilities || []).join(', ') || '—',
      cli_available: a.cli_available ? 'ok' : 'fail',
    }));
  }
  if (tab === 'log') {
    return r.map(l => ({ time: l.ts, level: l.level || 'info', agent: l.agent || '—', msg: l.msg || '' }));
  }
  if (tab === 'memory') {
    return r.map(x => ({
      id: x.id, agent: x.agent || '—', task: x.task || '—', tags: x.tags || '—',
      score: x.score !== null && x.score !== undefined ? Number(x.score).toFixed(3) : '—',
    }));
  }
  return r;
});

async function doSearch() {
  if (searching.value) return;
  if (activeTab.value !== 'graph' && !query.value.trim()) return;
  searching.value = true;
  searched.value = true;
  lastQuery.value = query.value;
  searchError.value = '';
  results.value = [];
  const start = performance.now();
  try {
    switch (activeTab.value) {
      case 'memory': {
        const d = await api.get(`/api/memory/search?q=${enc(query.value)}&topk=${memoryTopK.value}`);
        let res = d.results || [];
        if (memoryType.value !== 'all') res = res.filter(r => (r.layer || r.type) === memoryType.value);
        results.value = res; break;
      }
      case 'vector': {
        const d = await api.get(`/api/vector/search?q=${enc(query.value)}&topk=${vectorTopK.value}`);
        results.value = d.results || []; break;
      }
      case 'graph': {
        if (graphMode.value === 'neighbors') {
          const d = await api.get(`/api/graph/neighbors?node=${enc(query.value)}`);
          results.value = d.neighbors || [];
        } else if (graphMode.value === 'nodes') {
          const d = await api.get('/api/graph/nodes');
          results.value = d.nodes || d || [];
        } else {
          const d = await api.get('/api/graph/edges');
          results.value = d.edges || d || [];
        }
        break;
      }
      case 'log': {
        const d = await api.get(`/api/logs?type=${logType.value}`);
        results.value = d.logs || []; break;
      }
      case 'agent': {
        const d = await api.get('/api/model/agents');
        let arr = d.agents || [];
        const q = query.value.toLowerCase();
        arr = arr.filter(a =>
          (a.name || '').toLowerCase().includes(q) ||
          (a.model || '').toLowerCase().includes(q) ||
          (a.capabilities || []).some(c => c.toLowerCase().includes(q)));
        if (agentStatus.value !== 'all') {
          const want = agentStatus.value === 'available';
          arr = arr.filter(a => !!a.cli_available === want);
        }
        results.value = arr; break;
      }
    }
    searchTime.value = Math.round(performance.now() - start);
  } catch (e) {
    searchError.value = e.message || 'Search failed';
    results.value = [];
  }
  searching.value = false;
}

onMounted(loadStats);
</script>

<style scoped>
</style>
